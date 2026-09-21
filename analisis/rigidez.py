"""Bloque 2 -- rigidez de precios e intensidad promocional en dias habiles.

    python -m analisis.rigidez
    python -m analisis.rigidez --horizontes 7 14 30 60

La bitacora midio 0,48% de cambio de precio diario entre el sabado 19 y el
domingo 20 de septiembre y la declaro NO PUBLICABLE: los supermercados
hacen repricing en dias habiles. Este modulo rehace la medida sobre TODOS
los pares de dias consecutivos disponibles y la separa en tres grupos:

    habil        todos los dias de (d0, d1] son habiles (lun-vie no feriado)
    cruza_finde  algun dia de [d0, d1] es sabado, domingo o feriado
    total        todo junto

Por cadena y transicion se cuenta, sobre los SKU con precio valido ambos
dias: subidas, bajadas, entradas y salidas de oferta (con el cartel
`on_sale` y con `oferta_real` del bloque 1, para contrastarlas), y sobre el
catalogo: altas y bajas de SKU. Cuando hay cambio, se reporta su tamaño
mediano y si la distribucion de tamaños es bimodal, que es lo que Bueno
(2024) encontro (picos en +-20% y poca densidad cerca de cero).

Tambien se calcula la tasa de positivos que tendria el Modelo B a
distintos horizontes: P(entra en oferta en (t, t+h]). Solo se mide sobre
origenes t con t+h dentro del panel; si no hay ninguno, lo dice. La
extrapolacion geometrica desde la tasa diaria se imprime aparte y
etiquetada como tal: NO es una medida.

Umbrales: un cambio de precio es cualquier diferencia mayor a TOL_CAMBIO
(un centimo es cambio). Un "cambio grande" es |Δ| en la banda de Bueno,
BANDA_20 = 15%..25%.
"""

import argparse

import numpy as np
import pandas as pd

from analisis import oferta_real as OR
from analisis import panel as P

TOL_CAMBIO = 0.005          # soles: menos de medio centimo es redondeo, no cambio
BANDA_20 = (0.15, 0.25)     # |cambio| en esta banda = pico de Bueno (2024)
CERCA_DE_CERO = 0.02        # |cambio| < 2%: "cambio pequeño"
UMBRAL_BIMODAL = 5 / 9      # coeficiente de bimodalidad de Sarle > 0.555
HORIZONTES = (7, 14, 30)    # dias, para el target del Modelo B
MIN_TRANSICIONES_HABILES = 5   # por debajo, la tasa en dias habiles es provisional
BINS_TAMANO = [-1, -0.5, -0.3, -0.25, -0.15, -0.10, -0.05, -0.02, 0,
               0.02, 0.05, 0.10, 0.15, 0.25, 0.3, 0.5, 1, 10]


def tipo_transicion(d0, d1):
    dias = pd.date_range(d0, d1, freq="D")
    return "habil" if all(P.es_dia_habil(d) for d in dias) else "cruza_finde"


def coef_bimodalidad(x):
    """Coeficiente de Sarle: (g^2 + 1) / (k + 3(n-1)^2 / ((n-2)(n-3))).
    > 5/9 sugiere bimodalidad. NaN con menos de 4 datos."""
    x = pd.Series(x).dropna()
    n = len(x)
    if n < 4:
        return np.nan
    g, k = x.skew(), x.kurt()
    return (g ** 2 + 1) / (k + 3 * (n - 1) ** 2 / ((n - 2) * (n - 3)))


def _flags_oferta(panel):
    """Añade oferta_real (nullable) al panel, via bloque 1."""
    f = OR.calcular(panel[["retailer", "item_id", "fecha", "price", "on_sale"]])
    f = f[["retailer", "item_id", "fecha", "oferta_real", "fiable"]]
    return panel.merge(f, on=["retailer", "item_id", "fecha"], how="left")


def transiciones(panel):
    """Una fila por (cadena, d0, d1) consecutivos en la lista de dias de esa
    cadena. Devuelve (resumen_transiciones, cambios_individuales)."""
    filas, cambios = [], []
    cols = ["item_id", "price", "on_sale", "oferta_real", "fiable"]
    for cad, g in panel.groupby("retailer"):
        dias = sorted(g["fecha"].unique())
        por_dia = {d: gg[cols].drop_duplicates("item_id").set_index("item_id")
                   for d, gg in g.groupby("fecha")}
        for d0, d1 in zip(dias[:-1], dias[1:]):
            a, b = por_dia[d0], por_dia[d1]
            altas = len(b.index.difference(a.index))
            bajas = len(a.index.difference(b.index))
            m = a.join(b, lsuffix="_0", rsuffix="_1", how="inner")
            m = m[m["price_0"].notna() & m["price_1"].notna()]
            n = len(m)
            delta = m["price_1"] - m["price_0"]
            sube, baja = delta > TOL_CAMBIO, delta < -TOL_CAMBIO
            cambio = np.log(m["price_1"] / m["price_0"])[sube | baja]
            ev = m[m["fiable_0"].eq(True) & m["fiable_1"].eq(True)]
            o0 = ev["oferta_real_0"].astype("boolean").fillna(False)
            o1 = ev["oferta_real_1"].astype("boolean").fillna(False)
            filas.append({
                "retailer": cad, "d0": d0, "d1": d1,
                "dias_entre": (pd.Timestamp(d1) - pd.Timestamp(d0)).days,
                "tipo": tipo_transicion(d0, d1),
                "sku_seguidos": n,
                "sube": int(sube.sum()), "baja": int(baja.sum()),
                "entra_oferta_decl": int((~m["on_sale_0"] & m["on_sale_1"]).sum()),
                "sale_oferta_decl": int((m["on_sale_0"] & ~m["on_sale_1"]).sum()),
                "sku_evaluables_real": len(ev),
                "entra_oferta_real": int((~o0 & o1).sum()),
                "sale_oferta_real": int((o0 & ~o1).sum()),
                "altas_catalogo": altas, "bajas_catalogo": bajas,
                "mediana_abs_cambio": float(np.expm1(cambio.abs()).median()) if len(cambio) else np.nan,
                "n_cambios": int(len(cambio)),
            })
            if len(cambio):
                cambios.append(pd.DataFrame({
                    "retailer": cad, "d0": d0, "d1": d1,
                    "tipo": filas[-1]["tipo"], "item_id": cambio.index,
                    "log_cambio": cambio.values,
                }))
    tr = pd.DataFrame(filas)
    cb = pd.concat(cambios, ignore_index=True) if cambios else pd.DataFrame(
        columns=["retailer", "d0", "d1", "tipo", "item_id", "log_cambio"])
    return tr, cb


def agregar(tr, cb):
    """Tasas por (cadena, tipo) y totales. Las tasas son eventos / SKU
    seguidos sumados sobre las transiciones, es decir por transicion (dia a
    dia) y no promedio de tasas."""
    if tr.empty:
        return pd.DataFrame()
    filas = []
    grupos = [(cad, tipo, g) for (cad, tipo), g in tr.groupby(["retailer", "tipo"])]
    grupos += [(cad, "total", g) for cad, g in tr.groupby("retailer")]
    grupos += [("TOTAL", tipo, g) for tipo, g in tr.groupby("tipo")]
    grupos += [("TOTAL", "total", tr)]
    for cad, tipo, g in grupos:
        n = g["sku_seguidos"].sum()
        ev = g["sku_evaluables_real"].sum()
        sel = cb if cad == "TOTAL" else cb[cb["retailer"] == cad]
        if tipo != "total":
            sel = sel[sel["tipo"] == tipo]
        pct = np.expm1(sel["log_cambio"]) if len(sel) else pd.Series(dtype=float)
        absp = pct.abs()
        filas.append({
            "retailer": cad, "tipo": tipo,
            "n_transiciones": len(g),
            "sku_seguidos": int(n),
            "tasa_cambio": (g["sube"].sum() + g["baja"].sum()) / n if n else np.nan,
            "tasa_sube": g["sube"].sum() / n if n else np.nan,
            "tasa_baja": g["baja"].sum() / n if n else np.nan,
            "entra_oferta_decl": g["entra_oferta_decl"].sum() / n if n else np.nan,
            "sale_oferta_decl": g["sale_oferta_decl"].sum() / n if n else np.nan,
            "sku_evaluables_real": int(ev),
            "entra_oferta_real": g["entra_oferta_real"].sum() / ev if ev else np.nan,
            "sale_oferta_real": g["sale_oferta_real"].sum() / ev if ev else np.nan,
            "altas_catalogo": int(g["altas_catalogo"].sum()),
            "bajas_catalogo": int(g["bajas_catalogo"].sum()),
            "n_cambios": int(len(sel)),
            "mediana_abs_cambio": float(absp.median()) if len(absp) else np.nan,
            "share_banda_20": float(((absp >= BANDA_20[0]) & (absp <= BANDA_20[1])).mean()) if len(absp) else np.nan,
            "share_cerca_cero": float((absp < CERCA_DE_CERO).mean()) if len(absp) else np.nan,
            "coef_bimodalidad": coef_bimodalidad(sel["log_cambio"]) if len(sel) else np.nan,
        })
    out = pd.DataFrame(filas)
    out["bimodal"] = pd.array(out["coef_bimodalidad"] > UMBRAL_BIMODAL, dtype="boolean")
    out.loc[out["coef_bimodalidad"].isna(), "bimodal"] = pd.NA
    return out


def tamanos(cb):
    """Histograma de tamaños de cambio (signed, en %) por cadena y tipo."""
    if cb.empty:
        return pd.DataFrame(columns=["retailer", "tipo", "bin_inf", "bin_sup", "n"])
    d = cb.assign(pct=np.expm1(cb["log_cambio"]))
    d["bin"] = pd.cut(d["pct"], BINS_TAMANO, right=False)
    filas = []
    for (cad, tipo), g in d.groupby(["retailer", "tipo"]):
        cnt = g["bin"].value_counts(sort=False)
        for b, n in cnt.items():
            filas.append({"retailer": cad, "tipo": tipo, "bin_inf": b.left,
                          "bin_sup": b.right, "n": int(n)})
    return pd.DataFrame(filas)


def horizontes(panel, hs=HORIZONTES):
    """Tasa de positivos del Modelo B: P(entra en oferta en (t, t+h]).

    Origen = (cadena, SKU, dia t observado, sin oferta en t) con t+h <= ultimo
    dia de la cadena. Positivo = alguna observacion en (t, t+h] con oferta.
    Se hace con el cartel (decl) y con oferta_real (solo origenes y
    ventanas fiables)."""
    filas = []
    for cad, g in panel.groupby("retailer"):
        g = g[g["price"].notna()]
        ultimo = g["fecha"].max()
        primero = g["fecha"].min()
        p_decl = _tasa_diaria_entrada(g, "on_sale")
        for h in hs:
            tope = ultimo - pd.Timedelta(days=h)
            for version, col in (("decl", "on_sale"), ("real", "oferta_real")):
                s = g[["item_id", "fecha", col]].copy()
                if version == "real":
                    s = s[g["fiable"].eq(True)]
                s[col] = s[col].astype("boolean").fillna(False).astype(bool)
                s = s.sort_values(["item_id", "fecha"])
                origenes = s[(~s[col]) & (s["fecha"] <= tope)]
                n_or = len(origenes)
                pos = 0
                if n_or:
                    # dias con oferta por SKU -> para cada origen, ¿hay uno en (t, t+h]?
                    con = s[s[col]].groupby("item_id")["fecha"].apply(np.array)
                    for sku, grp in origenes.groupby("item_id"):
                        fechas_of = con.get(sku)
                        if fechas_of is None or len(fechas_of) == 0:
                            continue
                        t = grp["fecha"].to_numpy()
                        f = np.sort(fechas_of.astype("datetime64[ns]"))
                        j = np.searchsorted(f, t, side="right")
                        siguiente = np.where(j < len(f), f[np.minimum(j, len(f) - 1)],
                                             np.datetime64("NaT"))
                        dentro = (siguiente - t) <= np.timedelta64(h, "D")
                        pos += int(np.nansum(dentro & ~np.isnat(siguiente)))
                filas.append({
                    "retailer": cad, "horizonte": h, "version": version,
                    "origenes": n_or, "positivos": pos,
                    "tasa_positivos": pos / n_or if n_or else np.nan,
                    "dias_panel": (ultimo - primero).days + 1,
                    "dias_faltan_para_medir": max(0, h - ((ultimo - primero).days)),
                    "extrapolado_geometrico": 1 - (1 - p_decl) ** h if version == "decl" and not np.isnan(p_decl) else np.nan,
                })
    return pd.DataFrame(filas)


def _tasa_diaria_entrada(g, col):
    """Entradas / SKU seguidos por dia (cartel), para la extrapolacion."""
    tot_n, tot_e = 0, 0
    dias = sorted(g["fecha"].unique())
    por_dia = {d: gg.drop_duplicates("item_id").set_index("item_id")[col]
               for d, gg in g.groupby("fecha")}
    for d0, d1 in zip(dias[:-1], dias[1:]):
        m = pd.concat([por_dia[d0].rename("a"), por_dia[d1].rename("b")], axis=1, join="inner")
        tot_n += len(m)
        tot_e += int((~m["a"].astype(bool) & m["b"].astype(bool)).sum())
    return tot_e / tot_n if tot_n else np.nan


def correr(hs=HORIZONTES, panel_df=None, silencioso=False):
    df = panel_df if panel_df is not None else P.cargar_panel(
        columnas=["retailer", "item_id", "price", "on_sale"])
    if df.empty:
        raise SystemExit("Panel vacio.")
    fecha = str(df["fecha"].max().date())
    df = _flags_oferta(df)
    tr, cb = transiciones(df)
    agg = agregar(tr, cb)
    tam = tamanos(cb)
    hz = horizontes(df, hs)

    P.escribir_csv(agg, "rigidez", fecha)
    P.escribir_csv(tr, "rigidez_transiciones", fecha)
    P.escribir_csv(tam, "rigidez_tamanos", fecha)
    P.escribir_csv(hz, "rigidez_horizontes", fecha)

    if not silencioso:
        _imprimir(fecha, df, tr, agg, hz)
    return agg, tr, hz, tam


def _imprimir(fecha, df, tr, agg, hz):
    n_dias = df["fecha"].nunique()
    print(f"=== rigidez | ultimo dia {fecha} | {n_dias} dias | "
          f"{len(tr)} transiciones ({(tr['tipo'] == 'habil').sum()} habiles, "
          f"{(tr['tipo'] == 'cruza_finde').sum()} cruzan fin de semana) ===\n")
    if tr.empty:
        print("Hace falta al menos 2 dias de una misma cadena para medir una transicion.")
        return
    vista = agg[["retailer", "tipo", "n_transiciones", "sku_seguidos", "tasa_cambio",
                 "tasa_sube", "tasa_baja", "entra_oferta_decl", "sale_oferta_decl",
                 "entra_oferta_real", "sale_oferta_real", "altas_catalogo",
                 "bajas_catalogo", "mediana_abs_cambio", "share_banda_20",
                 "share_cerca_cero", "coef_bimodalidad", "bimodal"]].copy()
    for c in ("tasa_cambio", "tasa_sube", "tasa_baja", "entra_oferta_decl",
              "sale_oferta_decl", "entra_oferta_real", "sale_oferta_real",
              "mediana_abs_cambio", "share_banda_20", "share_cerca_cero"):
        vista[c] = vista[c].map(lambda v: P.pct(v, 2))
    print(P.tabla(vista))
    print()
    n_hab = int((tr["tipo"] == "habil").sum())
    if n_hab == 0:
        print("NO HAY TRANSICIONES ENTRE DIAS HABILES TODAVIA. La tasa de cambio\n"
              "diaria de arriba mide fines de semana y NO ES PUBLICABLE como rigidez.")
    else:
        print(P.aviso_fiabilidad(n_hab, MIN_TRANSICIONES_HABILES,
                                 "tasa de cambio en dias habiles"))
    if agg["sku_evaluables_real"].sum() == 0:
        print("oferta_real aun no es fiable en ningun SKU (bloque 1 pide "
              f"{OR.MIN_OBS_VENTANA} dias): las columnas *_real salen vacias.")
    print("\n--- Modelo B: tasa de positivos por horizonte ---")
    v = hz.copy()
    for c in ("tasa_positivos", "extrapolado_geometrico"):
        v[c] = v[c].map(lambda x: P.pct(x, 2))
    print(P.tabla(v))
    sin = hz[(hz["version"] == "decl") & (hz["origenes"] == 0)]
    if not sin.empty:
        hs_sin = sorted(sin["horizonte"].unique())
        print(f"\nHorizontes {hs_sin}: 0 origenes evaluables (el panel tiene menos dias\n"
              "que el horizonte). La columna extrapolado_geometrico es 1-(1-p)^h con la\n"
              "tasa diaria de entrada al cartel: es una EXTRAPOLACION, no una medida.")


def main(argv=None):
    P.configurar_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--horizontes", type=int, nargs="+", default=list(HORIZONTES),
                    help=f"horizontes en dias para el Modelo B (def. {HORIZONTES})")
    args = ap.parse_args(argv)
    correr(tuple(args.horizontes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
