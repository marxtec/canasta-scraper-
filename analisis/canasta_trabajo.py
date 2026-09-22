"""Canasta de trabajo: cruce de la canasta oficial del INEI con el panel.

    python -m analisis.canasta_trabajo              # escribe data/canasta_trabajo.csv
    python -m analisis.canasta_trabajo --recongelar # SOLO si se decide rehacerla

Tres conjuntos que no se mezclan:

    canasta oficial   data/canasta_oficial.csv. CBA del INEI, gramos per
                      capita dia de Lima Metropolitana (fuente en
                      data/canasta_oficial.fuente.txt). Entra como lista y
                      cantidades; no como serie del IPC.
    canasta de trabajo  ESTE modulo: los items oficiales vinculados a
                      productos del panel presentes en >= 4 de las 5 cadenas.
    universo scrapeado  todo data/daily/. Los EAN comunes de un dia son
                      universo, no canasta.

Reglas de emparejamiento (data/canasta_reglas.csv, fijadas ANTES de mirar
precios; este modulo solo usa si hay un precio valido, nunca su nivel):

1. Cada item oficial se vincula a productos del panel por descripcion
   normalizada (regex `incluye` / `excluye` sobre el nombre sin tildes),
   unidad (`parse_quantity` ya la dejo en net_quantity/unit) y marca cuando
   la fuente la trae (solo SIBARITA). No se sustituye marca.
2. Envasado: entre los candidatos con EAN de fabricante (12+ digitos, sin
   prefijo 2) se elige UN EAN -- el presente en mas cadenas; empate: el
   gramaje mas cercano a `gramaje_ref`, luego el mas visto, luego el menor
   EAN -- y las demas cadenas se unen por ese EAN aunque lo nombren
   distinto. Esa es la identidad entre supermercados. El gramaje del EAN
   es el que reporta la mayoria de cadenas (README §4c).
3. Granel (sin EAN, prefijo 2): un representante por kilo dentro de cada
   cadena -- el mas visto en la ventana; empate: el nombre mas corto, luego
   el menor item_id --. No se inventa un EAN.
4. Se excluyen packs y combos, y las filas de un vendedor que no es la
   cadena (marketplace).
5. Una cadena "tiene" el item si el SKU elegido tuvo precio valido y
   available != False en al menos MIN_FRAC_DIAS de los dias de la ventana.
   Entra a la canasta de trabajo si lo tienen >= MIN_CADENAS cadenas.

Ventana de inclusion: los primeros VENTANA_INCLUSION_DIAS dias del panel.
Con menos dias la salida sale estado=provisional; con la ventana completa
sale estado=congelada y ya no se reescribe (--recongelar para forzarlo, y
eso es una decision que se anota en la BITACORA).

Cantidad: gramos per capita dia x DIAS_PERIODO / g_por_unidad_base, en kg o
litros per capita al mes. Los liquidos usan 1000 g/l salvo el aceite
(920 g/l): es un supuesto declarado en canasta_reglas.csv.
"""

import argparse
import math
import re
import warnings

import numpy as np
import pandas as pd

from analisis import panel as P

DATA_DIR = P.ROOT / "data"
OFICIAL = DATA_DIR / "canasta_oficial.csv"
REGLAS = DATA_DIR / "canasta_reglas.csv"
SALIDA = DATA_DIR / "canasta_trabajo.csv"
EXCLUSIONES = DATA_DIR / "canasta_trabajo_exclusiones.csv"

VENTANA_INCLUSION_DIAS = 14     # primeros dias del panel que deciden la inclusion
MIN_CADENAS = 4                 # de 5
MIN_FRAC_DIAS = 0.8             # presencia minima del SKU en la ventana
DIAS_PERIODO = 30               # cantidades per capita al mes

# El vendedor que ES la cadena, tal como lo publica cada una. Cualquier otro
# seller_name es un tercero del marketplace. Vacio = sin dato: se acepta.
VENDEDOR_PROPIO = {
    "metro": "CENCOSUD RETAIL PERU S.A.",
    "wong": "WongIO",
    "plaza_vea": "Plaza Vea",
    "vivanda": "Vivanda",
    "tottus": "Tottus",
}

_PACK = re.compile(
    r"\b(?:pack|packs|twopack|two pack|sixpack|six pack|tripack|combo)\b|\+"
    r"|\b(?:paquete|bolsa|caja|display)\s*(?:x\s*)?\d+\s*(?:un|und|unid|unidades)\b"
    r"|\bpaquete\s*(?:x\s*)?\d+\b"
    r"|\bx\s*\d+\s*(?:un|und|unid|unidades)\b"
)
_GRANEL = re.compile(r"\b(?:x|por)\s*(?:kg|kgs|kilo|kilos)\b")

COLUMNAS_SALIDA = [
    "id", "descripcion", "grupo", "cadena", "item_id", "ean", "product_name",
    "net_quantity", "unidad", "cantidad", "paquetes", "regla", "n_cadenas",
    "ventana_inicio", "ventana_fin", "estado",
]
COLUMNAS_PANEL = ["retailer", "item_id", "product_name", "brand", "ean", "net_quantity",
                  "unit", "price", "available", "seller_name"]


def cargar_oficial(path=OFICIAL):
    o = pd.read_csv(path, dtype={"id": int, "descripcion": str, "grupo": str, "unidad": str})
    o["cantidad"] = pd.to_numeric(o["cantidad"], errors="coerce")
    return o


def cargar_reglas(path=REGLAS):
    r = pd.read_csv(path, dtype=str, keep_default_na=False)
    r["id"] = r["id"].astype(int)
    for col in ("gramaje_ref", "g_por_unidad_base"):
        r[col] = pd.to_numeric(r[col], errors="coerce")
    return r


def preparar_panel(panel):
    """Filas utilizables para emparejar. El precio se reduce a un booleano
    (hay precio valido y se puede comprar) y se descarta: las reglas no
    pueden ver su nivel."""
    d = panel.copy()
    d["plano"] = d["product_name"].fillna("").astype(str).map(P._plano)
    d["marca_plana"] = d["brand"].fillna("").astype(str).map(P._plano) if "brand" in d else ""
    propio = d["retailer"].map(VENDEDOR_PROPIO)
    vendedor = d["seller_name"].astype("string")
    d["vendedor_ok"] = vendedor.isna() | (vendedor == propio)
    d["es_pack"] = _contiene(d["plano"], _PACK)
    d["comprable"] = d["price"].notna() & (d["price"] > 0) & d["available"].ne(False)
    d["ean_fab"] = P.ean_comparable(d["ean"])
    d = d[d["vendedor_ok"] & ~d["es_pack"]]
    return d.drop(columns=["price"])


def _presencia(d, n_dias):
    """Por (retailer, item_id): fraccion de dias de la ventana comprable."""
    c = d[d["comprable"]].groupby(["retailer", "item_id"])["fecha"].nunique()
    return c / n_dias


def _contiene(serie, patron):
    """str.contains sin el aviso de grupos: las reglas son regex de
    usuario y pueden llevar parentesis."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return serie.str.contains(patron, regex=True)


def _cumple(d, regla):
    """Mascara: la fila cumple incluye, no toca excluye y (si hay) la marca."""
    m = pd.Series(True, index=d.index)
    if regla["incluye"]:
        m &= _contiene(d["plano"], regla["incluye"])
    if regla["excluye"]:
        m &= ~_contiene(d["plano"], regla["excluye"])
    if regla["marca"]:
        m &= _contiene(d["marca_plana"], regla["marca"]) | _contiene(d["plano"], regla["marca"])
    return m


def _candidatos(d, regla):
    return d[d["unit"].eq(regla["unidad"]) & _cumple(d, regla)]


def _ean_coherente(filas, regla):
    """Un EAN sirve si su descripcion cumple la regla en la mayoria de las
    cadenas que lo publican y no toca `excluye` en ninguna. Protege de dos
    fallas vistas en el panel: el EAN elegido por el nombre de UNA cadena
    (galletas saladas nombradas solo "Galletas" en otra) y el EAN que la
    cadena asigna a dos productos distintos (errores de catalogacion)."""
    nombres = filas.drop_duplicates(["retailer", "plano"])
    if regla["excluye"] and _contiene(nombres["plano"], regla["excluye"]).any():
        return False
    ok = _cumple(nombres, regla).groupby(nombres["retailer"]).any()
    return 2 * int(ok.sum()) > len(ok)


def _gramaje_mayoria(serie):
    s = serie.dropna().round(6)
    if s.empty:
        return np.nan
    cnt = s.value_counts()
    return float(cnt[cnt == cnt.max()].index.max())


def _envasado(d, regla, pres, por_ean):
    cand = _candidatos(d, regla)
    cand = cand[cand["ean_fab"]]
    if cand.empty:
        return None, "sin candidato con EAN de fabricante"
    opciones = []
    for ean in sorted(cand["ean"].unique()):
        # identidad por EAN: todas las filas con ese EAN, se llamen como se llamen
        filas = por_ean[ean]
        filas = filas[filas["unit"].eq(regla["unidad"])]
        if not _ean_coherente(filas, regla):
            continue
        preferido = bool(regla["preferencia"]) and bool(
            _contiene(filas["plano"], regla["preferencia"]).any())
        por_cadena = {}
        for cad, g in filas.groupby("retailer"):
            p = pres.reindex(pd.MultiIndex.from_arrays(
                [g["retailer"], g["item_id"]])).fillna(0.0).to_numpy()
            g = g.assign(pres=p)
            g = g[g["pres"] >= MIN_FRAC_DIAS]
            if g.empty:
                continue
            best = (g.groupby("item_id").agg(pres=("pres", "max"), nombre=("product_name", "last"))
                    .reset_index().sort_values(["pres", "item_id"], ascending=[False, True]).iloc[0])
            por_cadena[cad] = (best["item_id"], best["nombre"])
        gram = _gramaje_mayoria(filas.groupby("retailer")["net_quantity"].median())
        ref = regla["gramaje_ref"]
        dist = abs(math.log(gram / ref)) if (ref and not np.isnan(ref) and gram and not np.isnan(gram)) else 0.0
        vistos = int(filas["comprable"].sum())
        opciones.append((-len(por_cadena), not preferido, dist, -vistos, ean, por_cadena, gram))
    if not opciones:
        return None, "ningun EAN candidato describe el item de forma coherente entre cadenas"
    opciones.sort(key=lambda o: o[:5])
    mejor = opciones[0]
    por_cadena, gram = mejor[5], mejor[6]
    if len(por_cadena) < MIN_CADENAS:
        return None, (f"sin producto identico (mismo EAN) en {MIN_CADENAS} de 5 cadenas "
                      f"(el mejor EAN, {mejor[4]}, esta en {len(por_cadena)})")
    if np.isnan(gram):
        return None, f"el EAN elegido ({mejor[4]}) no tiene gramaje parseado en ninguna cadena"
    return [{"cadena": c, "item_id": i, "ean": mejor[4], "product_name": n,
             "net_quantity": gram, "regla": "ean"} for c, (i, n) in sorted(por_cadena.items())], None


def _granel(d, regla, pres, por_ean=None):
    cand = _candidatos(d, regla)
    cand = cand[cand["plano"].str.contains(_GRANEL)]
    filas = []
    for cad, g in cand.groupby("retailer"):
        p = pres.reindex(pd.MultiIndex.from_arrays([g["retailer"], g["item_id"]])).fillna(0.0).to_numpy()
        g = g.assign(pres=p, largo=g["product_name"].str.len())
        g = g[g["pres"] >= MIN_FRAC_DIAS]
        if g.empty:
            continue
        best = (g.groupby("item_id").agg(pres=("pres", "max"), largo=("largo", "min"),
                                         nombre=("product_name", "last"), ean=("ean", "last"),
                                         ean_fab=("ean_fab", "last"))
                .reset_index().sort_values(["pres", "largo", "item_id"],
                                           ascending=[False, True, True]).iloc[0])
        filas.append({"cadena": cad, "item_id": best["item_id"],
                      "ean": best["ean"] if bool(best["ean_fab"]) else "",
                      "product_name": best["nombre"], "net_quantity": 1.0, "regla": "granel"})
    if len(filas) < MIN_CADENAS:
        return None, (f"sin representante por {regla['unidad']} en {MIN_CADENAS} de 5 cadenas "
                      f"(hay en {len(filas)})")
    return filas, None


def cruzar(oficial, reglas, panel, ventana=VENTANA_INCLUSION_DIAS):
    """(canasta_trabajo, exclusiones). `panel` con COLUMNAS_PANEL + fecha."""
    lima = oficial[oficial["cantidad"].notna() & (oficial["cantidad"] > 0)]
    faltan = sorted(set(lima["id"]) - set(reglas["id"]))
    if faltan:
        raise ValueError(f"items oficiales sin regla de emparejamiento: {faltan}")
    fechas = sorted(panel["fecha"].unique())
    usadas = fechas[:ventana]
    estado = "congelada" if len(fechas) >= ventana else "provisional"
    d = preparar_panel(panel[panel["fecha"].isin(usadas)])
    pres = _presencia(d, len(usadas))
    por_ean = {e: g for e, g in d[d["ean_fab"]].groupby("ean")}
    reglas = reglas.set_index("id")

    filas, excl = [], []
    for _, item in lima.iterrows():
        regla = reglas.loc[item["id"]]
        base = {"id": item["id"], "descripcion": item["descripcion"], "grupo": item["grupo"]}
        if regla["tipo"] == "excluido":
            excl.append({**base, "motivo": regla["motivo"], "n_cadenas": 0, "cadenas": ""})
            continue
        fn = _envasado if regla["tipo"] == "envasado" else _granel
        res, motivo = fn(d, regla, pres, por_ean)
        if res is None:
            excl.append({**base, "motivo": motivo, "n_cadenas": 0, "cadenas": ""})
            continue
        cantidad = item["cantidad"] * DIAS_PERIODO / regla["g_por_unidad_base"]
        for r in res:
            filas.append({**base, **r, "unidad": regla["unidad"], "cantidad": round(cantidad, 6),
                          "paquetes": round(cantidad / r["net_quantity"], 6),
                          "n_cadenas": len(res),
                          "ventana_inicio": pd.Timestamp(usadas[0]).date(),
                          "ventana_fin": pd.Timestamp(usadas[-1]).date(), "estado": estado})
    trabajo = pd.DataFrame(filas, columns=COLUMNAS_SALIDA)
    exclusiones = pd.DataFrame(excl, columns=["id", "descripcion", "grupo", "motivo",
                                              "n_cadenas", "cadenas"])
    return trabajo, exclusiones


def congelada(path=SALIDA):
    if not path.exists():
        return False
    e = pd.read_csv(path, usecols=["estado"], dtype=str)
    return bool((e["estado"] == "congelada").any())


def correr(recongelar=False, panel_df=None, salida=SALIDA, exclusiones=EXCLUSIONES,
           silencioso=False):
    if congelada(salida) and not recongelar:
        raise SystemExit(f"{salida.name} ya esta congelada. No se rearma segun el resultado; "
                         "--recongelar solo con una decision anotada en la BITACORA.")
    panel = panel_df if panel_df is not None else P.cargar_panel(columnas=COLUMNAS_PANEL)
    if panel.empty:
        raise SystemExit("Panel vacio.")
    trabajo, excl = cruzar(cargar_oficial(), cargar_reglas(), panel)
    trabajo.to_csv(salida, index=False, encoding="utf-8", lineterminator="\n")
    excl.to_csv(exclusiones, index=False, encoding="utf-8", lineterminator="\n")
    if not silencioso:
        estado = trabajo["estado"].iloc[0] if len(trabajo) else "vacia"
        print(f"=== canasta de trabajo | estado {estado} | "
              f"{trabajo['id'].nunique()} items oficiales dentro, {len(excl)} fuera ===")
        if estado != "congelada":
            print(f"PROVISIONAL: la ventana de inclusion pide {VENTANA_INCLUSION_DIAS} dias; "
                  "prueba la maquinaria, no es la canasta.")
        print(f"-> {salida.name}, {exclusiones.name}")
    return trabajo, excl


def main(argv=None):
    P.configurar_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--recongelar", action="store_true",
                    help="reescribe una canasta ya congelada (decision a anotar en la BITACORA)")
    args = ap.parse_args(argv)
    correr(recongelar=args.recongelar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
