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

Metodologia completa: documento "Metodologia de estandarizacion de la
canasta" (2026-09-22) y BITACORA. Resumen:

Cada fila dice por que via entro (`identidad`), y cada item cae en un nivel
segun la identidad mas debil de sus filas:

    ean              mismo codigo de barras de fabricante            nucleo
    ean_equivalente  mismo paquete con otro codigo: la tabla
                     data/ean_equivalencias.csv, o un EAN vacio con misma
                     marca, mismo gramaje (<= 2%) y nombre parecido
                     (Jaccard >= 0,6)                                nucleo
    granel           "x kg" (o peso variable de VTEX) de la variedad
                     fijada en `variedad_pref`                       nucleo
    por_kilo         mismo alimento, otra marca, tamano o variedad;
                     precio por kg dentro de la tolerancia r_max     ampliado_1
    por_unidad       vendido por unidad; peso por unidad del CENAN
                     (`g_unidad`, con su fila en `fuente_g_unidad`) ampliado_2

Reglas de emparejamiento (data/canasta_reglas.csv, fijadas ANTES de mirar
precios; este modulo solo usa si hay un precio valido, nunca su nivel):

1. Candidatos: descripcion normalizada (regex `incluye` / `excluye`),
   unidad y marca cuando la fuente la trae. Fuera: combos ("+", "combo"),
   vendedores del marketplace y nombres con dos gramajes que se contradicen.
   Un paquete de porciones con un solo EAN ("Paquete 6un") SI entra: es la
   presentacion normal.
2. El gramaje es del EAN: si una cadena lo publica (nombre o contenido neto
   declarado) vale para todas; si difieren, el de la mayoria.
3. Envasado: se elige UN EAN (el presente en mas cadenas; empate: la
   preferencia de la regla, el gramaje mas cercano a `gramaje_ref`, el mas
   visto, el menor). Si no llega a MIN_CADENAS, las cadenas que faltan
   entran por_kilo: la marca presente en mas cadenas, el tamano dentro de
   r_max mas cercano al del EAN (o a la cantidad de un hogar de referencia).
4. Granel: la variedad fijada. Si no llega a MIN_CADENAS, las cadenas que
   faltan entran por_kilo con otra variedad (`variedad_distinta`) o con un
   envase de peso declarado dentro de r_max de 1 kg. Sin variedad fijada, el
   item entero es por_kilo.
5. Por unidad: unidades del nombre ("Bandeja 30un", "x unid") x g_unidad.
6. Una cadena "tiene" el item si el SKU elegido tuvo precio valido y
   available != False en al menos MIN_FRAC_DIAS de los dias de la ventana.
   Entra a la canasta si lo tienen >= MIN_CADENAS cadenas. Solo se completa
   con una identidad mas debil cuando la mas fuerte no llega a la barra.
7. Para el costo con paquetes enteros se listan tambien los otros tamanos
   de la misma linea y marca en la cadena (`presentacion_base` = False).
   Lo que se vende pesado (`a_granel`) se compra en la cantidad exacta.

Tolerancia de tamano r_max: sale del efecto del tamano medido en la ventana
(analisis/tamano.py), se escribe en data/canasta_trabajo_beta.csv y se
congela con la canasta. `cruzar` la recibe hecha: no mira precios.

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
from analisis import presentacion as PR
from analisis import tamano as TAM

DATA_DIR = P.ROOT / "data"
OFICIAL = DATA_DIR / "canasta_oficial.csv"
REGLAS = DATA_DIR / "canasta_reglas.csv"
EQUIVALENCIAS = DATA_DIR / "ean_equivalencias.csv"
SALIDA = DATA_DIR / "canasta_trabajo.csv"
EXCLUSIONES = DATA_DIR / "canasta_trabajo_exclusiones.csv"
BETAS = DATA_DIR / "canasta_trabajo_beta.csv"

VENTANA_INCLUSION_DIAS = 14     # primeros dias del panel que deciden la inclusion
MIN_CADENAS = 4                 # de 5
MIN_FRAC_DIAS = 0.8             # presencia minima del SKU en la ventana
DIAS_PERIODO = 30               # cantidades per capita al mes
PERSONAS_REF = 4                # hogar de referencia para elegir el tamano base
TOL_GRAMAJE_EQ = 0.02           # |ln(w_a / w_b)| maximo para ean_equivalente
MIN_JACCARD_EQ = 0.6            # parecido minimo de nombres para ean_equivalente

NUCLEO = {"ean", "ean_equivalente", "granel"}
NIVEL_DE = {"ean": 0, "ean_equivalente": 0, "granel": 0, "por_kilo": 1, "por_unidad": 2}
NIVELES = ["nucleo", "ampliado_1", "ampliado_2"]

# El vendedor que ES la cadena, tal como lo publica cada una. Cualquier otro
# seller_name es un tercero del marketplace. Vacio = sin dato: se acepta.
VENDEDOR_PROPIO = {
    "metro": "CENCOSUD RETAIL PERU S.A.",
    "wong": "WongIO",
    "plaza_vea": "Plaza Vea",
    "vivanda": "Vivanda",
    "tottus": "Tottus",
}

# Combos y promociones de contenido: no son una presentacion del producto.
# Los multipacks de un solo EAN ("Paquete 6un", "Pack x6") si lo son.
_PACK = re.compile(r"\+|\bcombo\b")
_GRANEL = re.compile(r"\b(?:x|por)\s*(?:kg|kgs|kilo|kilos)\b")
# "Bandeja 15un x 2un": parse_quantity lee 15, no 30. No se usa como
# alternativa de tamano.
_PACK_DE_PACKS = re.compile(r"\d\s*(?:un|und|unid|unidades)\b.*\bx\s*\d+\s*(?:un|und|unid|unidades)\b")

COLUMNAS_SALIDA = [
    "id", "descripcion", "grupo", "cadena", "item_id", "ean", "product_name",
    "net_quantity", "unidad", "cantidad", "paquetes", "regla", "identidad", "nivel",
    "presentacion_base", "a_granel", "marcas", "r_max", "n_cadenas",
    "ventana_inicio", "ventana_fin", "estado",
]
COLUMNAS_EXCLUSIONES = ["id", "descripcion", "grupo", "motivo", "n_cadenas", "cadenas"]
COLUMNAS_PANEL = ["retailer", "item_id", "product_name", "brand", "ean", "net_quantity",
                  "unit", "unit_multiplier", "contenido_neto_declarado", "price",
                  "available", "seller_name"]


def cargar_oficial(path=OFICIAL):
    o = pd.read_csv(path, dtype={"id": int, "descripcion": str, "grupo": str, "unidad": str})
    o["cantidad"] = pd.to_numeric(o["cantidad"], errors="coerce")
    return o


def cargar_reglas(path=REGLAS):
    r = pd.read_csv(path, dtype=str, keep_default_na=False)
    r["id"] = r["id"].astype(int)
    for col in ("variedad_pref", "g_unidad", "fuente_g_unidad"):
        if col not in r:
            r[col] = ""
    for col in ("gramaje_ref", "g_por_unidad_base", "g_unidad"):
        r[col] = pd.to_numeric(r[col], errors="coerce")
    return r


def cargar_equivalencias(path=EQUIVALENCIAS):
    if not path.exists():
        return pd.DataFrame(columns=["ean_a", "ean_b", "motivo"])
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _grupos_ean(equivalencias):
    """{ean: ean canonico del grupo} por union de pares (el menor del grupo)."""
    padre = {}

    def raiz(x):
        while padre.get(x, x) != x:
            x = padre[x]
        return x

    for a, b in equivalencias[["ean_a", "ean_b"]].itertuples(index=False):
        ra, rb = raiz(a.strip()), raiz(b.strip())
        if ra != rb:
            padre[max(ra, rb)] = min(ra, rb)
    return {e: raiz(e) for e in set(padre) | set(padre.values())}


def _por_valor(serie, fn):
    """serie.map(fn) calculado una vez por valor distinto: el panel repite
    el mismo nombre en cada dia y cada cadena."""
    valores = serie.dropna().unique()
    tabla = {v: fn(v) for v in valores}
    return serie.map(lambda v: tabla[v] if v in tabla else fn(v))


def _gramaje_por_ean(d):
    """Completa net_quantity/unit de cada EAN de fabricante con el de la
    mayoria de cadenas que lo publican (nombre o contenido declarado), y
    marca las filas cuyo gramaje propio difiere mas de TOL_GRAMAJE_EQ."""
    peso = d["unit"].isin(["kg", "l"]) & (d["net_quantity"] > 0)
    decl = _por_valor(d["contenido_neto_declarado"], PR.contenido_declarado)
    d["_dq"] = pd.to_numeric(decl.map(lambda t: t[0] if isinstance(t, tuple) else None),
                             errors="coerce")
    d["_du"] = decl.map(lambda t: t[1] if isinstance(t, tuple) else None)
    usar_decl = ~peso & d["_dq"].notna()
    if usar_decl.any():
        d.loc[usar_decl, "net_quantity"] = d.loc[usar_decl, "_dq"].astype(float)
        d.loc[usar_decl, "unit"] = d.loc[usar_decl, "_du"].astype(str)
    peso = d["unit"].isin(["kg", "l"]) & (d["net_quantity"] > 0)

    d["gramaje_discrepante"] = False
    con = d[d["ean_fab"] & peso]
    if con.empty:
        return d.drop(columns=["_dq", "_du"])
    por_cad = (con.assign(q=con["net_quantity"].round(6))
               .groupby(["ean", "retailer", "unit"])["q"].median().reset_index())
    mayoria, discrepantes = {}, set()
    for ean, g in por_cad.groupby("ean"):
        cnt = g.groupby(["unit", "q"]).size()
        top = cnt[cnt == cnt.max()].index.max()          # empate: el mayor, como _gramaje_mayoria
        mayoria[ean] = top
        if (np.abs(np.log(g["q"] / top[1])) > TOL_GRAMAJE_EQ).any() or g["unit"].nunique() > 1:
            discrepantes.add(ean)
    d["gramaje_discrepante"] = d["ean"].isin(discrepantes)
    sin = d["ean_fab"] & ~peso & d["ean"].isin(mayoria)
    if sin.any():
        eans = d.loc[sin, "ean"].astype(str)
        d.loc[sin, "unit"] = [mayoria[e][0] for e in eans]
        d.loc[sin, "net_quantity"] = [float(mayoria[e][1]) for e in eans]
    d["net_quantity"] = pd.to_numeric(d["net_quantity"], errors="coerce")
    return d.drop(columns=["_dq", "_du"])


def preparar_panel(panel, equivalencias=None, conservar_precio=False):
    """Filas utilizables para emparejar. El precio se reduce a un booleano
    (hay precio valido y se puede comprar) y se descarta: las reglas no
    pueden ver su nivel. `conservar_precio` solo para analisis/tamano.py."""
    d = panel.copy()
    for col in ("unit_multiplier", "contenido_neto_declarado", "brand", "seller_name"):
        if col not in d:
            d[col] = np.nan
    d["ean"] = d["ean"].astype("string").fillna("").str.strip()
    d["net_quantity"] = pd.to_numeric(d["net_quantity"], errors="coerce")
    d["unit"] = d["unit"].astype("string")
    d["plano"] = d["product_name"].fillna("").astype(str).map(P._plano)
    d["marca_plana"] = d["brand"].fillna("").astype(str).map(P._plano)
    propio = d["retailer"].map(VENDEDOR_PROPIO)
    vendedor = d["seller_name"].astype("string")
    d["vendedor_ok"] = vendedor.isna() | (vendedor == propio)
    d["es_pack"] = _contiene(d["plano"], _PACK)
    nombre = d["product_name"].fillna("").astype(str)
    d["ambiguo"] = _por_valor(nombre, PR.gramaje_ambiguo)
    d["aprox"] = _por_valor(nombre, PR.peso_aprox)
    d["suelta"] = _por_valor(nombre, PR.unidad_suelta)
    d["comprable"] = d["price"].notna() & (d["price"] > 0) & d["available"].ne(False)
    d["ean_fab"] = P.ean_comparable(d["ean"])

    # Granel: "x kg" en el nombre, o peso variable de VTEX (unitMultiplier
    # distinto de 1 y sin cantidad en el nombre): el precio publicado es por kg.
    mult = pd.to_numeric(d["unit_multiplier"], errors="coerce")
    variable = mult.notna() & (mult != 1) & d["net_quantity"].isna() & d["suelta"].isna()
    d["es_granel"] = _contiene(d["plano"], _GRANEL) | variable
    d.loc[variable, "net_quantity"] = 1.0
    d.loc[variable, "unit"] = "kg"

    d = _gramaje_por_ean(d)
    grupos = _grupos_ean(equivalencias if equivalencias is not None
                         else pd.DataFrame(columns=["ean_a", "ean_b"]))
    d["ean_grupo"] = d["ean"].map(lambda e: grupos.get(e, e))
    d["linea"] = _por_valor(nombre, PR.linea)
    d = d[d["vendedor_ok"] & ~d["es_pack"] & ~d["ambiguo"]]
    return d if conservar_precio else d.drop(columns=["price"])


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


def _moda(serie):
    s = serie.dropna()
    s = s[s != ""]
    if s.empty:
        return ""
    cnt = s.value_counts()
    return sorted(cnt[cnt == cnt.max()].index)[0]


def _fila(g, identidad, nq, marcas=(), ean=None):
    """Una fila de salida desde la fila del panel `g` (Series)."""
    if ean is None:
        ean = g["ean"] if bool(g["ean_fab"]) else ""
    return {"cadena": g["retailer"], "item_id": g["item_id"], "ean": ean,
            "product_name": g["product_name"], "net_quantity": float(nq),
            "identidad": identidad, "marcas": ";".join(m for m in marcas if m),
            "a_granel": bool(g["es_granel"])}


def _mejor_por_cadena(c, orden, asc):
    """Primera fila de cada cadena segun `orden`. `c` ya viene con pres >= MIN."""
    return c.sort_values(orden, ascending=asc).drop_duplicates("retailer", keep="first")


def _ean_vacio_equivalente(cand, cadena, gram, marca, nombres_ref):
    """Fila de `cadena` sin EAN de fabricante (vacio o codigo interno de la
    cadena) que es el mismo paquete que el EAN: misma marca, gramaje a <= 2%
    y nombre parecido. None si no hay."""
    c = cand[cand["retailer"].eq(cadena) & ~cand["ean_fab"] & cand["net_quantity"].gt(0)
             & cand["marca_plana"].eq(marca)]
    if c.empty or not marca:
        return None
    c = c[np.abs(np.log(c["net_quantity"] / gram)) <= TOL_GRAMAJE_EQ]
    if c.empty:
        return None
    jac = c.apply(lambda r: max(PR.jaccard(PR.palabras(r["product_name"], r["brand"]), n)
                                for n in nombres_ref), axis=1)
    c = c.assign(jac=jac)
    c = c[c["jac"] >= MIN_JACCARD_EQ]
    if c.empty:
        return None
    return c.sort_values(["jac", "pres", "item_id"], ascending=[False, False, True]).iloc[0]


def _por_kilo(c, ctx, excluir, w0=None, marca_top=None, marcas_extra=()):
    """Filas por_kilo para las cadenas que no estan en `excluir`.

    Precio-ciego: la marca presente en mas cadenas (empate: orden
    alfabetico), el tamano base w0 (el de la identidad fuerte, o el mas
    cercano a la cantidad de un hogar de referencia), y en cada cadena el
    candidato dentro de r_max: primero la marca, luego el tamano mas
    cercano, luego el mas visto y el menor item_id."""
    c = c[c["w"].gt(0)]
    if c.empty:
        return []
    if marca_top is None:
        cnt = c[c["marca_plana"] != ""].groupby("marca_plana")["retailer"].nunique()
        marca_top = sorted(cnt[cnt == cnt.max()].index)[0] if len(cnt) else ""
    if w0 is None or not np.isfinite(w0):
        ref = c[c["marca_plana"].eq(marca_top)] if marca_top else c
        ref = ref.assign(dist=np.abs(np.log(ref["w"] / ctx["q_hogar"])))
        w0 = float(ref.sort_values(["dist", "w"]).iloc[0]["w"])
    c = c.assign(dist=np.abs(np.log(c["w"] / w0)), otra_marca=~c["marca_plana"].eq(marca_top))
    c = c[(c["dist"] <= math.log(ctx["r_max"]) + 1e-9) & ~c["retailer"].isin(excluir)]
    filas = []
    for _, g in _mejor_por_cadena(c, ["otra_marca", "dist", "pres", "item_id"],
                                  [True, True, False, True]).iterrows():
        marcas = list(marcas_extra)
        if g["aprox"]:
            marcas.append("peso_aprox")
        if g["otra_marca"] and marca_top:
            marcas.append("marca_distinta")
        if g["dist"] > TOL_GRAMAJE_EQ:
            marcas.append("tamano_distinto")
        filas.append(_fila(g, ctx["identidad_debil"], g["w"], marcas))
    return filas


def _envasado(d, regla, ctx):
    cand = _candidatos(d, regla)
    cand = cand[cand["pres"] >= MIN_FRAC_DIAS]
    if cand.empty:
        return None, "sin candidato presente en la ventana"
    opciones = []
    for eg in sorted(cand.loc[cand["ean_fab"], "ean_grupo"].unique()):
        filas = ctx["por_ean"].get(eg)
        if filas is None:
            continue
        filas = filas[filas["unit"].eq(regla["unidad"])]
        if filas.empty or not _ean_coherente(filas, regla):
            continue
        preferido = bool(regla["preferencia"]) and bool(
            _contiene(filas["plano"], regla["preferencia"]).any())
        ok = filas[filas["pres"] >= MIN_FRAC_DIAS]
        cad_por_ean = ok.groupby("ean")["retailer"].nunique()
        principal = sorted(cad_por_ean[cad_por_ean == cad_por_ean.max()].index)[0] \
            if len(cad_por_ean) else eg
        gram = _gramaje_mayoria(filas.groupby("retailer")["net_quantity"].median())
        por_cadena = {}
        for _, g in _mejor_por_cadena(ok, ["pres", "item_id"], [False, True]).iterrows():
            ident = "ean" if g["ean"] == principal else "ean_equivalente"
            marcas = ["gramaje_discrepante"] if g["gramaje_discrepante"] else []
            por_cadena[g["retailer"]] = _fila(g, ident, gram, marcas, ean=g["ean"])
        if por_cadena and np.isfinite(gram):
            marca = _moda(filas["marca_plana"])
            nombres = [PR.palabras(n, b) for n, b in
                       filas[["product_name", "brand"]].drop_duplicates().itertuples(index=False)]
            for cad in sorted(set(cand["retailer"]) - set(por_cadena)):
                g = _ean_vacio_equivalente(cand, cad, gram, marca, nombres)
                if g is not None:
                    por_cadena[cad] = _fila(g, "ean_equivalente", gram, ["sin_ean_fabricante"],
                                            ean="")
        ref = regla["gramaje_ref"]
        dist = abs(math.log(gram / ref)) if (ref and not np.isnan(ref) and gram
                                             and not np.isnan(gram)) else 0.0
        vistos = float(filas["pres"].sum())
        opciones.append((-len(por_cadena), not preferido, dist, -vistos, eg, por_cadena, gram,
                         _moda(filas["marca_plana"])))
    if opciones:
        opciones.sort(key=lambda o: o[:5])
        mejor = opciones[0]
        por_cadena, gram, marca, n_ean, ean_mejor = mejor[5], mejor[6], mejor[7], -mejor[0], mejor[4]
    else:
        por_cadena, gram, marca, n_ean, ean_mejor = {}, np.nan, None, 0, None
    if por_cadena and np.isnan(gram):
        return None, f"el EAN elegido ({ean_mejor}) no tiene gramaje parseado en ninguna cadena"
    if len(por_cadena) >= MIN_CADENAS:
        return list(por_cadena.values()), None

    # Las cadenas que faltan: el mismo alimento por kilo, dentro de r_max.
    c = cand.assign(w=cand["net_quantity"])
    extra = _por_kilo(c, {**ctx, "identidad_debil": "por_kilo"}, set(por_cadena),
                      w0=gram if por_cadena else None,
                      marca_top=marca if por_cadena else None)
    total = list(por_cadena.values()) + extra
    if len(total) >= MIN_CADENAS:
        return total, None
    return None, (f"sin {MIN_CADENAS} de 5 cadenas: mismo EAN en {n_ean}"
                  + (f" (mejor EAN {ean_mejor})" if ean_mejor else "")
                  + f", por kilo en {len(total)}")


def _granel(d, regla, ctx):
    cand = _candidatos(d, regla)
    cand = cand[cand["pres"] >= MIN_FRAC_DIAS]
    gr = cand[cand["es_granel"]].assign(largo=lambda x: x["product_name"].str.len())
    pref = regla["variedad_pref"]
    filas = []
    if pref:
        pm = gr if pref == "." else gr[_contiene(gr["plano"], pref)]
        for _, g in _mejor_por_cadena(pm, ["pres", "largo", "item_id"],
                                      [False, True, True]).iterrows():
            filas.append(_fila(g, "granel", 1.0))
        if len(filas) >= MIN_CADENAS:
            return filas, None
    marca_var = "variedad_distinta" if pref else "variedad_no_fijada"
    tienen = {f["cadena"] for f in filas}
    otras = gr[~gr["retailer"].isin(tienen)]
    for _, g in _mejor_por_cadena(otras, ["pres", "largo", "item_id"],
                                  [False, True, True]).iterrows():
        filas.append(_fila(g, "por_kilo", 1.0, [marca_var]))
    if len(filas) < MIN_CADENAS:
        # Un envase de peso declarado cerca de 1 kg ("Mondongo Bolsa 1 kg").
        env = cand[~cand["es_granel"]].assign(w=lambda x: x["net_quantity"])
        filas += _por_kilo(env, {**ctx, "identidad_debil": "por_kilo"},
                           {f["cadena"] for f in filas}, w0=1.0, marca_top="",
                           marcas_extra=["peso_declarado"])
    if len(filas) >= MIN_CADENAS:
        return filas, None
    return None, (f"sin representante por {regla['unidad']} en {MIN_CADENAS} de 5 cadenas "
                  f"(hay en {len(filas)})")


def _por_unidad(d, regla, ctx):
    g_u = regla["g_unidad"]
    if not (g_u and np.isfinite(g_u) and g_u > 0):
        return None, "por unidad sin peso de referencia con fuente (g_unidad)"
    cand = d[_cumple(d, regla)]
    cand = cand[cand["pres"] >= MIN_FRAC_DIAS]
    n = np.where(cand["unit"].eq("un") & cand["net_quantity"].gt(0), cand["net_quantity"],
                 np.where(cand["suelta"].eq("unidad"), 1.0, np.nan))
    cand = cand.assign(n=n).dropna(subset=["n"])
    cand = cand.assign(w=cand["n"] * g_u / 1000.0)
    filas = _por_kilo(cand, {**ctx, "identidad_debil": "por_unidad"}, set())
    if len(filas) >= MIN_CADENAS:
        return filas, None
    return None, (f"sin {MIN_CADENAS} de 5 cadenas con venta por unidad "
                  f"convertible (hay en {len(filas)})")


def _alternativas(d, regla, filas):
    """Otros tamanos de la misma linea y marca en la misma cadena, para el
    costo con paquetes enteros. Solo envasados con gramaje propio."""
    alt = []
    for f in filas:
        if f["identidad"] in ("granel",) or "variedad" in f["marcas"]:
            continue
        g = d[d["retailer"].eq(f["cadena"]) & d["item_id"].eq(f["item_id"])]
        if g.empty:
            continue
        g = g.iloc[0]
        if g["es_granel"] or not g["linea"]:
            continue
        c = d[d["retailer"].eq(f["cadena"]) & d["linea"].eq(g["linea"])
              & d["marca_plana"].eq(g["marca_plana"]) & (d["pres"] >= MIN_FRAC_DIAS)
              & ~d["item_id"].eq(f["item_id"]) & ~d["es_granel"]
              & ~_contiene(d["plano"], _PACK_DE_PACKS)]
        if f["identidad"] == "por_unidad":
            n = np.where(c["unit"].eq("un") & c["net_quantity"].gt(0), c["net_quantity"], np.nan)
            c = c.assign(w=n * regla["g_unidad"] / 1000.0)
        else:
            c = c[c["unit"].eq(g["unit"])].assign(w=lambda x: x["net_quantity"])
        c = c[c["w"].gt(0) & (np.abs(np.log(c["w"] / f["net_quantity"])) > TOL_GRAMAJE_EQ)]
        for _, a in c.drop_duplicates("w").sort_values(["w", "item_id"]).iterrows():
            fila = _fila(a, f["identidad"], a["w"], ["alternativa"])
            fila["presentacion_base"] = False
            alt.append(fila)
    return alt


def cruzar(oficial, reglas, panel, ventana=VENTANA_INCLUSION_DIAS, equivalencias=None,
           betas=None):
    """(canasta_trabajo, exclusiones). `panel` con COLUMNAS_PANEL + fecha.
    `betas`: tabla de analisis/tamano.py (id, r_max); sin ella, el r_max del
    beta de referencia."""
    lima = oficial[oficial["cantidad"].notna() & (oficial["cantidad"] > 0)]
    reglas = reglas.copy()
    for col, v in (("variedad_pref", ""), ("g_unidad", np.nan), ("fuente_g_unidad", "")):
        if col not in reglas:
            reglas[col] = v
    faltan = sorted(set(lima["id"]) - set(reglas["id"]))
    if faltan:
        raise ValueError(f"items oficiales sin regla de emparejamiento: {faltan}")
    fechas = sorted(panel["fecha"].unique())
    usadas = fechas[:ventana]
    estado = "congelada" if len(fechas) >= ventana else "provisional"
    # Presencia con todos los dias; el cruce, con la ultima fila de cada SKU.
    w = panel[panel["fecha"].isin(usadas)]
    comprable = w["price"].notna() & (w["price"] > 0) & w["available"].ne(False)
    pres = _presencia(w.assign(comprable=comprable), len(usadas))
    ult = w.sort_values("fecha").drop_duplicates(["retailer", "item_id"], keep="last")
    d = preparar_panel(ult, equivalencias)
    d = d.assign(pres=pres.reindex(pd.MultiIndex.from_arrays(
        [d["retailer"], d["item_id"]])).fillna(0.0).to_numpy())
    por_ean = {e: g for e, g in d[d["ean_fab"]].groupby("ean_grupo")}
    reglas = reglas.set_index("id")
    rmax_def = TAM.r_max(TAM.BETA_DEFECTO)
    rmax = ({} if betas is None or betas.empty
            else dict(zip(betas["id"].astype(int), betas["r_max"].astype(float))))

    filas, excl = [], []
    for _, item in lima.iterrows():
        regla = reglas.loc[item["id"]]
        base = {"id": item["id"], "descripcion": item["descripcion"], "grupo": item["grupo"]}
        if regla["tipo"] == "excluido":
            excl.append({**base, "motivo": regla["motivo"], "n_cadenas": 0, "cadenas": ""})
            continue
        cantidad = item["cantidad"] * DIAS_PERIODO / regla["g_por_unidad_base"]
        ctx = {"por_ean": por_ean, "q_hogar": cantidad * PERSONAS_REF,
               "r_max": rmax.get(item["id"], rmax_def)}
        fn = {"envasado": _envasado, "granel": _granel, "por_unidad": _por_unidad}[regla["tipo"]]
        res, motivo = fn(d, regla, ctx)
        if res is None:
            excl.append({**base, "motivo": motivo, "n_cadenas": 0, "cadenas": ""})
            continue
        for r in res:
            r["presentacion_base"] = True
        res = res + _alternativas(d, regla, res)
        n_cad = len({r["cadena"] for r in res})
        nivel = NIVELES[max(NIVEL_DE[r["identidad"]] for r in res)]
        for r in res:
            filas.append({**base, **r, "regla": r["identidad"], "nivel": nivel,
                          "unidad": regla["unidad"], "cantidad": round(cantidad, 6),
                          "paquetes": round(cantidad / r["net_quantity"], 6),
                          "r_max": round(ctx["r_max"], 4), "n_cadenas": n_cad,
                          "ventana_inicio": pd.Timestamp(usadas[0]).date(),
                          "ventana_fin": pd.Timestamp(usadas[-1]).date(), "estado": estado})
    trabajo = pd.DataFrame(filas, columns=COLUMNAS_SALIDA)
    if len(trabajo):
        trabajo = trabajo.sort_values(["id", "cadena", "presentacion_base", "net_quantity"],
                                      ascending=[True, True, False, True]).reset_index(drop=True)
    exclusiones = pd.DataFrame(excl, columns=COLUMNAS_EXCLUSIONES)
    return trabajo, exclusiones


def congelada(path=SALIDA):
    if not path.exists():
        return False
    e = pd.read_csv(path, usecols=["estado"], dtype=str)
    return bool((e["estado"] == "congelada").any())


def estimar_betas(panel, reglas, ventana=VENTANA_INCLUSION_DIAS):
    """beta y r_max por item con la ventana de inclusion (analisis/tamano.py)."""
    usadas = sorted(panel["fecha"].unique())[:ventana]
    d = preparar_panel(panel[panel["fecha"].isin(usadas)], conservar_precio=True)
    t, _ = TAM.estimar(d, reglas.set_index("id"), _cumple)
    return t


def correr(recongelar=False, panel_df=None, salida=SALIDA, exclusiones=EXCLUSIONES,
           betas_path=None, silencioso=False):
    if congelada(salida) and not recongelar:
        raise SystemExit(f"{salida.name} ya esta congelada. No se rearma segun el resultado; "
                         "--recongelar solo con una decision anotada en la BITACORA.")
    panel = panel_df if panel_df is not None else P.cargar_panel(columnas=COLUMNAS_PANEL)
    if panel.empty:
        raise SystemExit("Panel vacio.")
    reglas = cargar_reglas()
    betas = estimar_betas(panel, reglas)
    trabajo, excl = cruzar(cargar_oficial(), reglas, panel,
                           equivalencias=cargar_equivalencias(), betas=betas)
    betas_path = betas_path or salida.with_name(salida.stem + "_beta.csv")
    trabajo.to_csv(salida, index=False, encoding="utf-8", lineterminator="\n")
    excl.to_csv(exclusiones, index=False, encoding="utf-8", lineterminator="\n")
    betas.round(4).to_csv(betas_path, index=False, encoding="utf-8", lineterminator="\n")
    if not silencioso:
        estado = trabajo["estado"].iloc[0] if len(trabajo) else "vacia"
        items = trabajo.drop_duplicates("id")
        print(f"=== canasta de trabajo | estado {estado} | "
              f"{trabajo['id'].nunique()} items oficiales dentro, {len(excl)} fuera ===")
        print(items["nivel"].value_counts().reindex(NIVELES, fill_value=0).to_string())
        if estado != "congelada":
            print(f"PROVISIONAL: la ventana de inclusion pide {VENTANA_INCLUSION_DIAS} dias; "
                  "prueba la maquinaria, no es la canasta.")
        print(f"-> {salida.name}, {exclusiones.name}, {betas_path.name}")
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
