"""Bloque 5 -- huecos del panel y regla de precios faltantes para la canasta.

    python -m analisis.precios_faltantes                    # mide huecos y compara las 3 reglas
    python -m analisis.precios_faltantes --regla arrastre   # solo una regla
    python -m analisis.precios_faltantes --tope-arrastre 14 --min-cadenas 4

La canasta = sum_i q_i * P_{i,t} exige un precio por item y dia. Sin regla
explicita, la canasta sube y baja por agotamientos y no por precios (§2 del
README, la decision metodologica mas importante y hasta ahora abierta).

PRIMERO SE MIDE. Por (cadena, SKU, dia del panel) desde la primera vez que
el SKU aparece, un hueco es un dia sin precio valido. Tipos:

    agotado_listado        la cadena lo lista pero available=False o precio 0
    desaparecido_catalogo  no esta en el CSV y su hoja de categoria SI se
                           recorrio ese dia (data/trees/): la cadena lo deslisto
    desaparecido_arbol     no esta en el CSV y su hoja NO se recorrio: cambio
                           el arbol, no el surtido. Solo distinguible con arbol
    desaparecido_sin_arbol no esta en el CSV y no hay arbol guardado para
                           ese dia (los arboles se guardan desde el 21-09-2026)

Se reporta cuantos huecos hay, cuanto duran (rachas, censuradas si llegan
al ultimo dia) y como se reparten por cadena y categoria. Para Tottus
`available` no es observable (la API de listado no publica stock): un
agotado en Tottus solo se ve como desaparicion.

DESPUES SE COMPARAN TRES REGLAS sobre la canasta de referencia (EAN con
precio en las `min_cadenas` cadenas el primer dia del panel, q_i = 1):

    arrastre    ultimo precio observado, hasta TOPE_ARRASTRE_DIAS dias
                (practica del INEI para faltantes temporales). Pasado el
                tope el item se excluye ese dia.
    imputacion  ultimo precio x variacion mediana de su categoria en esa
                cadena entre la ultima observacion y hoy (indice encadenado
                de la categoria; si la categoria no tiene pares, el de la
                cadena entera).
    exclusion   el item sale del indice ese dia. Se reporta el nivel de
                composicion variable (el que sube y baja por agotamientos)
                y el indice encadenado de composicion emparejada (solo
                items con precio en ambos dias consecutivos), que es la
                version defendible de esta regla.

El resultado ES el contraste: cuanto difiere la canasta segun la regla. Si
las tres coinciden a menos de UMBRAL_INDIFERENCIA la eleccion es
irrelevante y se adopta la mas simple; si divergen, la divergencia es un
hallazgo y hay que justificar la eleccion con ella.

q_i = 1 para todos los items porque las cantidades de la bolsa todavia no
estan definidas: lo que se compara aqui es el efecto de la regla, no el
nivel en soles.
"""

import argparse

import numpy as np
import pandas as pd

from analisis import panel as P
from canasta import storage

TOPE_ARRASTRE_DIAS = 7          # dias calendario que se arrastra un precio
MIN_CADENAS_CANASTA = 5         # EAN presente en todas las cadenas el dia base
UMBRAL_INDIFERENCIA = 0.005     # 0,5 puntos de indice: por debajo, da igual la regla
MIN_DIAS_SOLIDO = 14            # dias de panel para dar por concluyente el contraste
REGLAS = ("arrastre", "imputacion", "exclusion")


# ---------------------------------------------------------------------------
# Medicion de huecos
# ---------------------------------------------------------------------------

def _hojas_por_dia(retailer, fechas):
    """{fecha: set(rutas de hoja recorridas)} o fecha ausente si no hay arbol."""
    out = {}
    for f in fechas:
        arbol = storage.load_tree(retailer, str(pd.Timestamp(f).date()))
        if arbol and arbol.get("leaves"):
            out[pd.Timestamp(f)] = {ruta for _, ruta in arbol["leaves"]}
    return out


def huecos(panel, hojas=None):
    """Rachas de huecos por (retailer, item_id). `hojas` = {retailer: {fecha:
    set(rutas)}}; si es None se leen de data/trees/.

    Devuelve (rachas, celdas) donde celdas tiene una fila por (retailer,
    item_id, fecha) con hueco y su tipo."""
    filas = []
    for cad, g in panel.groupby("retailer"):
        fechas = sorted(g["fecha"].unique())
        idx_fecha = {f: i for i, f in enumerate(fechas)}
        h = (hojas or {}).get(cad)
        if h is None and hojas is None:
            h = _hojas_por_dia(cad, fechas)
        h = h or {}
        g = g.sort_values("fecha")
        primera = g.groupby("item_id")["fecha"].min()
        ruta = g.groupby("item_id")["category_path"].last()
        cat = g.groupby("item_id")["categoria_comun"].last()
        presente = g.set_index(["item_id", "fecha"])
        valido = presente["price"].notna() & presente["available"].ne(False)
        celdas_ok = set(valido[valido].index)
        celdas_listado = set(presente.index)
        for sku, f0 in primera.items():
            for f in fechas[idx_fecha[f0]:]:
                if (sku, f) in celdas_ok:
                    continue
                if (sku, f) in celdas_listado:
                    tipo = "agotado_listado"
                elif f not in h:
                    tipo = "desaparecido_sin_arbol"
                elif ruta.get(sku) in h[f]:
                    tipo = "desaparecido_catalogo"
                else:
                    tipo = "desaparecido_arbol"
                filas.append({"retailer": cad, "item_id": sku, "fecha": f, "tipo": tipo,
                              "categoria_comun": cat.get(sku)})
    celdas = pd.DataFrame(filas, columns=["retailer", "item_id", "fecha", "tipo", "categoria_comun"])
    rachas = _rachas(celdas, panel)
    return rachas, celdas


def _rachas(celdas, panel):
    if celdas.empty:
        return pd.DataFrame(columns=["retailer", "item_id", "inicio", "fin", "dias", "tipo",
                                     "censurada", "categoria_comun"])
    ultimo = panel.groupby("retailer")["fecha"].max()
    fechas_cad = {c: sorted(g["fecha"].unique()) for c, g in panel.groupby("retailer")}
    out = []
    for (cad, sku), g in celdas.sort_values("fecha").groupby(["retailer", "item_id"]):
        pos = {f: i for i, f in enumerate(fechas_cad[cad])}
        ini, prev, tipos = None, None, []
        for f, tipo in zip(g["fecha"], g["tipo"]):
            if ini is None:
                ini, prev, tipos = f, f, [tipo]
            elif pos[f] == pos[prev] + 1:
                prev, tipos = f, tipos + [tipo]
            else:
                out.append(_racha(cad, sku, ini, prev, tipos, pos, ultimo[cad], g))
                ini, prev, tipos = f, f, [tipo]
        out.append(_racha(cad, sku, ini, prev, tipos, pos, ultimo[cad], g))
    return pd.DataFrame(out)


def _racha(cad, sku, ini, fin, tipos, pos, ultimo, g):
    return {"retailer": cad, "item_id": sku, "inicio": ini, "fin": fin,
            "dias": pos[fin] - pos[ini] + 1,
            "tipo": pd.Series(tipos).mode().iloc[0],
            "censurada": fin == ultimo,
            "categoria_comun": g["categoria_comun"].iloc[0]}


def resumen_huecos(celdas, panel):
    """Por cadena: celdas SKU-dia posibles, huecos y reparto por tipo."""
    filas = []
    for cad, g in panel.groupby("retailer"):
        fechas = sorted(g["fecha"].unique())
        primera = g.groupby("item_id")["fecha"].min()
        posibles = sum(len([f for f in fechas if f >= f0]) for f0 in primera)
        c = celdas[celdas["retailer"] == cad]
        fila = {"cadena": cad, "sku": len(primera), "dias": len(fechas),
                "celdas_sku_dia": posibles, "huecos": len(c),
                "tasa_hueco": len(c) / posibles if posibles else np.nan}
        for tipo in ("agotado_listado", "desaparecido_catalogo", "desaparecido_arbol",
                     "desaparecido_sin_arbol"):
            fila[tipo] = int((c["tipo"] == tipo).sum())
        filas.append(fila)
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Canasta bajo tres reglas
# ---------------------------------------------------------------------------

def matriz_canasta(panel, min_cadenas=MIN_CADENAS_CANASTA, incluir_internos=False):
    """{cadena: DataFrame EAN x fecha} de la canasta de referencia, con NaN
    donde falta precio. Tambien devuelve la categoria de cada EAN.

    Un precio publicado con available=False NO cuenta: un precio al que no
    se puede comprar no es el precio que paga el hogar, y es justo el hueco
    que la regla tiene que resolver. (Tottus no publica stock: ahi todo
    precio cuenta.)"""
    d = panel[panel["price"].notna() & panel["available"].ne(False)
              & P.ean_comparable(panel["ean"], incluir_internos)]
    obs = d.groupby(["fecha", "ean", "retailer"])["price"].median().reset_index()
    fechas = sorted(obs["fecha"].unique())
    if not fechas:
        return {}, pd.Series(dtype=str), []
    base = obs[obs["fecha"] == fechas[0]].groupby("ean")["retailer"].nunique()
    eans = sorted(base[base >= min_cadenas].index)
    cat = (d[d["ean"].isin(eans)].groupby("ean")["categoria_comun"]
           .agg(lambda s: s.mode().iloc[0]))
    mats = {}
    for cad, g in obs[obs["ean"].isin(eans)].groupby("retailer"):
        m = g.pivot(index="ean", columns="fecha", values="price").reindex(index=eans, columns=fechas)
        mats[cad] = m
    return mats, cat, fechas


def regla_arrastre(m, tope=TOPE_ARRASTRE_DIAS):
    """Forward-fill acotado en dias calendario."""
    out = m.copy()
    fechas = list(m.columns)
    for i, f in enumerate(fechas):
        col = out[f]
        falta = col.isna()
        if not falta.any() or i == 0:
            continue
        for j in range(i - 1, -1, -1):
            if (f - fechas[j]).days > tope:
                break
            prev = m[fechas[j]]
            rellenar = falta & prev.notna()
            out.loc[rellenar, f] = prev[rellenar]
            falta = out[f].isna()
            if not falta.any():
                break
    return out


def indice_categoria(m, cat):
    """log-indice encadenado por categoria (y global), sobre pares observados
    en dias consecutivos. DataFrame categoria x fecha, con fila '__todas__'."""
    fechas = list(m.columns)
    cats = sorted(set(cat.reindex(m.index).fillna("otros")))
    L = pd.DataFrame(0.0, index=cats + ["__todas__"], columns=fechas)
    logm = np.log(m)
    for f0, f1 in zip(fechas[:-1], fechas[1:]):
        d = (logm[f1] - logm[f0]).dropna()
        glob = d.median() if len(d) else 0.0
        L.loc["__todas__", f1] = L.loc["__todas__", f0] + glob
        for c in cats:
            dc = d[cat.reindex(d.index).fillna("otros") == c]
            L.loc[c, f1] = L.loc[c, f0] + (dc.median() if len(dc) else glob)
    return L


def regla_imputacion(m, cat):
    L = indice_categoria(m, cat)
    out = m.copy()
    fechas = list(m.columns)
    catm = cat.reindex(m.index).fillna("otros")
    ultimo_p = pd.Series(np.nan, index=m.index)
    ultimo_f = pd.Series(pd.NaT, index=m.index)
    for f in fechas:
        obs = m[f].notna()
        ultimo_p[obs] = m.loc[obs, f]
        ultimo_f[obs] = f
        falta = (~obs) & ultimo_p.notna()
        for sku in m.index[falta]:
            c = catm[sku]
            out.loc[sku, f] = ultimo_p[sku] * np.exp(L.loc[c, f] - L.loc[c, ultimo_f[sku]])
    return out


def canasta_por_regla(mats, cat, tope=TOPE_ARRASTRE_DIAS, reglas=REGLAS):
    """Filas (regla, cadena, fecha, n_items, soles, indice)."""
    filas = []
    for cad, m in mats.items():
        fechas = list(m.columns)
        versiones = {}
        if "arrastre" in reglas:
            versiones["arrastre"] = regla_arrastre(m, tope)
        if "imputacion" in reglas:
            versiones["imputacion"] = regla_imputacion(m, cat)
        if "exclusion" in reglas:
            versiones["exclusion"] = m
        for regla, v in versiones.items():
            base = v[fechas[0]].sum()
            enc = 100.0
            for i, f in enumerate(fechas):
                soles = v[f].sum()
                n = int(v[f].notna().sum())
                if regla == "exclusion":
                    if i > 0:
                        ambos = v[f].notna() & v[fechas[i - 1]].notna()
                        s0, s1 = v.loc[ambos, fechas[i - 1]].sum(), v.loc[ambos, f].sum()
                        enc = enc * (s1 / s0) if s0 else np.nan
                    indice = enc
                else:
                    # composicion fija: si un item queda NaN (tope superado)
                    # se compara sobre los items con precio ese dia y el base
                    con = v[f].notna()
                    indice = 100 * v.loc[con, f].sum() / v.loc[con, fechas[0]].sum() if con.any() else np.nan
                filas.append({"regla": regla, "retailer": cad, "fecha": f, "n_items": n,
                              "soles": soles, "indice": indice,
                              "items_base": int(m[fechas[0]].notna().sum())})
    return pd.DataFrame(filas)


def contraste(canasta):
    """Por cadena: divergencia maxima y media entre reglas (puntos de indice)."""
    if canasta.empty:
        return pd.DataFrame()
    w = canasta.pivot_table(index=["retailer", "fecha"], columns="regla", values="indice")
    filas = []
    for cad, g in w.groupby(level="retailer"):
        rango = g.max(axis=1) - g.min(axis=1)
        fila = {"cadena": cad, "dias": len(g), "divergencia_max": rango.max(),
                "divergencia_media": rango.mean(),
                "indiferente": bool(rango.max() <= 100 * UMBRAL_INDIFERENCIA)}
        for a in g.columns:
            for b in g.columns:
                if a < b:
                    fila[f"|{a}-{b}|_max"] = (g[a] - g[b]).abs().max()
        filas.append(fila)
    return pd.DataFrame(filas)


def incidencia_en_canasta(mats):
    filas = []
    for cad, m in mats.items():
        celdas = m.size
        faltan = int(m.isna().sum().sum())
        filas.append({"cadena": cad, "items": len(m), "dias": m.shape[1],
                      "celdas": celdas, "faltantes": faltan,
                      "tasa_faltante": faltan / celdas if celdas else np.nan,
                      "items_con_algun_hueco": int(m.isna().any(axis=1).sum())})
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------

def correr(regla="todas", tope=TOPE_ARRASTRE_DIAS, min_cadenas=MIN_CADENAS_CANASTA,
           incluir_internos=False, panel_df=None, silencioso=False):
    df = panel_df if panel_df is not None else P.cargar_panel(
        columnas=["retailer", "item_id", "ean", "price", "available", "category",
                  "category_path"])
    if df.empty:
        raise SystemExit("Panel vacio.")
    fecha = str(df["fecha"].max().date())
    n_dias = df["fecha"].nunique()
    reglas = REGLAS if regla == "todas" else (regla,)

    rachas, celdas = huecos(df)
    res_h = resumen_huecos(celdas, df)
    por_cat = (celdas.groupby(["retailer", "categoria_comun", "tipo"]).size()
               .reset_index(name="huecos")) if not celdas.empty else pd.DataFrame()
    mats, cat, fechas = matriz_canasta(df, min_cadenas, incluir_internos)
    inc = incidencia_en_canasta(mats)
    can = canasta_por_regla(mats, cat, tope, reglas)
    con = contraste(can) if len(reglas) > 1 else pd.DataFrame()

    P.escribir_csv(rachas, "huecos", fecha)
    P.escribir_csv(res_h, "huecos_resumen", fecha)
    P.escribir_csv(por_cat, "huecos_categoria", fecha)
    P.escribir_csv(can, "canasta_reglas", fecha)
    P.escribir_csv(pd.concat([inc.assign(bloque="incidencia"),
                              con.assign(bloque="contraste")], ignore_index=True)
                   if not con.empty else inc.assign(bloque="incidencia"),
                   "canasta_reglas_resumen", fecha)

    if not silencioso:
        print(f"=== precios faltantes | ultimo dia {fecha} | {n_dias} dias ===\n")
        print("--- huecos por cadena (SKU-dia desde la primera aparicion) ---")
        v = res_h.copy()
        v["tasa_hueco"] = v["tasa_hueco"].map(lambda x: P.pct(x, 2))
        print(P.tabla(v))
        if not rachas.empty:
            print("\n--- duracion de las rachas de hueco (dias de panel) ---")
            dur = rachas.groupby(["retailer", "tipo"]).agg(
                rachas=("dias", "size"), mediana=("dias", "median"), p90=("dias", lambda s: s.quantile(0.9)),
                censuradas=("censurada", "mean")).reset_index()
            dur["censuradas"] = dur["censuradas"].map(P.pct)
            print(P.tabla(dur, "{:.1f}"))
        sin_arbol = int((celdas["tipo"] == "desaparecido_sin_arbol").sum()) if not celdas.empty else 0
        if sin_arbol:
            print(f"\n{sin_arbol} desapariciones sin arbol guardado ese dia: no se puede decir si\n"
                  "fue la cadena o el arbol. Los arboles se guardan desde el 21-09-2026.")
        print(f"\n--- canasta de referencia: EAN en {min_cadenas} cadenas el {fechas[0].date() if fechas else '?'} ---")
        v = inc.copy()
        v["tasa_faltante"] = v["tasa_faltante"].map(lambda x: P.pct(x, 2))
        print(P.tabla(v))
        print(f"\n--- indice de la canasta por regla (base {fechas[0].date() if fechas else '?'} = 100) ---")
        w = can.pivot_table(index=["retailer", "fecha"], columns="regla", values="indice")
        print(w.round(3).to_string())
        if not con.empty:
            print("\n--- contraste entre reglas (puntos de indice) ---")
            print(P.tabla(con, "{:.3f}"))
            if con["indiferente"].all():
                print(f"\nCon {n_dias} dias las tres reglas difieren menos de "
                      f"{100 * UMBRAL_INDIFERENCIA:.1f} puntos en todas las cadenas.")
            else:
                peor = con.loc[con["divergencia_max"].idxmax()]
                print(f"\nDivergencia maxima: {peor['divergencia_max']:.2f} puntos en {peor['cadena']}.")
        print()
        print(P.aviso_fiabilidad(n_dias, MIN_DIAS_SOLIDO, "contraste entre reglas de faltantes"))
    return {"rachas": rachas, "celdas": celdas, "resumen": res_h, "canasta": can,
            "contraste": con, "incidencia": inc}


def main(argv=None):
    P.configurar_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--regla", choices=REGLAS + ("todas",), default="todas")
    ap.add_argument("--tope-arrastre", type=int, default=TOPE_ARRASTRE_DIAS,
                    help=f"dias maximos de arrastre (def. {TOPE_ARRASTRE_DIAS})")
    ap.add_argument("--min-cadenas", type=int, default=MIN_CADENAS_CANASTA,
                    help=f"cadenas en que debe estar el EAN el dia base (def. {MIN_CADENAS_CANASTA})")
    ap.add_argument("--incluir-internos", action="store_true")
    args = ap.parse_args(argv)
    correr(args.regla, args.tope_arrastre, args.min_cadenas, args.incluir_internos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
