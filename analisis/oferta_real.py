"""Bloque 1 / Modelo C -- oferta real, oferta fantasma y rebaja silenciosa.

    python -m analisis.oferta_real                 # todos los dias, regla principal
    python -m analisis.oferta_real --lookback 30   # ventana mas corta (NO es la regla)

C es un DIAGNOSTICO, no un clasificador entrenado: una regla determinista
sobre la historia del mismo SKU. Es el unico sitio del repo donde vive la
regla; el optimizador, rigidez.py y el Modelo B la importan de aqui.

El problema
-----------
`on_sale` copia el cartel de la tienda: regular_price > price. Un producto
que lleva el tachado el 90% de los dias no esta en oferta: ese es su precio
normal y el "regular" es un ancla de marketing. El cartel no es falsable;
la historia del propio producto si.

La regla principal (decidida el 22-09-2026, BITACORA)
-----------------------------------------------------
Por (cadena, SKU) y dia t, con la ventana HACIA ATRAS [t - LOOKBACK, t - 1]
(el dia de hoy NO entra: el habitual es el de antes de hoy):

    precio_habitual(t) = mediana de price del SKU en la ventana
    frac_on_sale(t)    = proporcion de dias observados de la ventana con cartel
    descuento_real(t)  = 1 - price(t) / precio_habitual(t)

    oferta_real        = descuento_real >= 10 %  y  frac_on_sale <= 0,5
    fantasma           = on_sale  y  NO oferta_real
    rebaja_silenciosa  = oferta_real  y  NO on_sale      (se nombra aparte;
                                                          no es fantasma)

    fiable = la ventana tiene >= MIN_OBS_VENTANA (21) dias observados.
    Si fiable=False los tres flags salen NA y el optimizador no usa el
    cartel: usa solo el precio de venta.

Ademas, sin tocar la regla:

    descuento_anunciado = 1 - price / regular_price  (cuando ambos existen):
                          lo que el cartel dice que se ahorra
    ref_moda            = moda de price en la misma ventana (Eichenbaum,
                          Jaimovich y Rebelo 2011), para la sensibilidad

Literatura
----------
Nakamura y Steinsson (2008): la intensidad promocional depende de que las
ofertas se identifiquen contra un precio de referencia propio del producto
y no contra lo que la tienda declara. Bueno (2024) lo operacionaliza para
el Peru con un filtro de precio regular sobre una ventana movil. La
mediana es inmune a la propia oferta: una caida de 5 dias en una ventana
de 45 no mueve la referencia. La moda (EJR 2011) es la prueba de robustez.

Umbrales
--------
- LOOKBACK_DIAS = 45: una promocion mensual (Cencosud publica "del 01al30")
  no alcanza a ocupar la mayoria de la ventana.
- MIN_OBS_VENTANA = 21: con menos, un punado de precios fija la mediana.
- UMBRAL_DESCUENTO = 0.10 y TECHO_FRAC_ON_SALE = 0.5: la regla decidida.

Sensibilidad (se calcula APARTE, en `sensibilidad()`; no pisa las columnas
principales ni se elige la variante que "queda mejor"): umbral 0 % (toda
caida estricta), 5 % y 15 %, y la moda en lugar de la mediana.

Limite conocido: un cambio permanente de precio (baja y no vuelve) se marca
como oferta_real hasta que la nueva serie domina la ventana (~LOOKBACK/2
dias). Es inherente a cualquier filtro con ventana hacia atras.

La tarjeta (card_price) no entra en C.
"""

import argparse

import numpy as np
import pandas as pd

from analisis import panel as P

# --- Regla principal. No dispersar estos numeros por el codigo. ------------
LOOKBACK_DIAS = 45          # ventana (dias calendario) hacia atras, sin hoy
MIN_OBS_VENTANA = 21        # dias observados minimos en la ventana: fiable
UMBRAL_DESCUENTO = 0.10     # caida minima bajo el habitual para ser oferta
TECHO_FRAC_ON_SALE = 0.5    # cartel en mas de la mitad de la ventana: no hay oferta
# ---------------------------------------------------------------------------

# Variantes de sensibilidad: (nombre, referencia, umbral). Umbral 0 se lee
# como "cualquier caida estricta" (descuento > 0), no como descuento >= 0,
# que marcaria oferta todo dia en que el precio iguala al habitual.
VARIANTES = (
    ("principal", "mediana", UMBRAL_DESCUENTO),
    ("umbral_0", "mediana", 0.0),
    ("umbral_5", "mediana", 0.05),
    ("umbral_15", "mediana", 0.15),
    ("moda", "moda", UMBRAL_DESCUENTO),
)

_EPS = 1e-9                 # 1 - 9/10 = 0.0999999...: no perder el borde

COLUMNAS_ENTRADA = ["retailer", "item_id", "fecha", "price", "on_sale"]
COLUMNAS_C = [
    "retailer", "item_id", "fecha", "price", "on_sale", "regular_price",
    "precio_habitual", "ref_moda", "n_obs_ventana", "frac_on_sale",
    "descuento_real", "descuento_anunciado", "fiable",
    "oferta_real", "fantasma", "rebaja_silenciosa",
]
_FLAGS = ("oferta_real", "fantasma", "rebaja_silenciosa")


def _matrices(df):
    """Pivota (producto x dia calendario) precio, cartel y precio regular.

    Se usa el calendario completo entre el primer y el ultimo dia, no solo
    los dias con CSV: asi un dia sin recoleccion cuenta como hueco y la
    ventana sigue midiendose en dias, que es lo que dice el parametro.
    """
    cols = COLUMNAS_ENTRADA + (["regular_price"] if "regular_price" in df else [])
    d = df.loc[df["price"].notna(), cols].copy()
    d["fecha"] = pd.to_datetime(d["fecha"]).dt.normalize()
    d = d.drop_duplicates(["retailer", "item_id", "fecha"], keep="last")
    dias = pd.date_range(d["fecha"].min(), d["fecha"].max(), freq="D")
    idx = pd.MultiIndex.from_frame(d[["retailer", "item_id"]].drop_duplicates())

    def pivote(col, valores):
        return (d.assign(**{col: valores})
                .pivot_table(index=["retailer", "item_id"], columns="fecha",
                             values=col, aggfunc="last", dropna=False)
                .reindex(index=idx, columns=dias))

    precio = pivote("price", d["price"])
    cartel = pivote("on_sale", d["on_sale"].astype(float))
    regular = (pivote("regular_price", pd.to_numeric(d["regular_price"], errors="coerce"))
               if "regular_price" in d else None)
    return precio, cartel, regular


def _moda_filas(bloque):
    """Moda por fila de una matriz con NaN, al centimo. Empate: el precio
    mas alto (el regular es el alto; la oferta es la desviacion). Fila sin
    datos: NaN. Vectorizado: ordena y mide rachas de valores iguales."""
    n, w = bloque.shape
    if w == 0:
        return np.full(n, np.nan)
    a = np.sort(np.round(bloque * 100), axis=1)          # NaN al final
    racha = np.zeros_like(a)
    racha[:, 0] = np.where(np.isnan(a[:, 0]), 0, 1)
    for k in range(1, w):
        igual = a[:, k] == a[:, k - 1]
        racha[:, k] = np.where(np.isnan(a[:, k]), 0, np.where(igual, racha[:, k - 1] + 1, 1))
    # la ultima posicion con la racha maxima = el valor mas alto entre empates
    inv = racha[:, ::-1]
    pos = w - 1 - np.argmax(inv == inv.max(axis=1, keepdims=True), axis=1)
    moda = a[np.arange(n), pos] / 100
    moda[racha.max(axis=1) == 0] = np.nan
    return moda


def calcular(df, lookback=LOOKBACK_DIAS, min_obs=MIN_OBS_VENTANA,
             umbral=UMBRAL_DESCUENTO, techo=TECHO_FRAC_ON_SALE):
    """La tabla de C: una fila por (retailer, item_id, fecha) observada.

    `df` necesita retailer, item_id, fecha, price, on_sale; si trae
    regular_price se calcula ademas descuento_anunciado.

    Columnas: COLUMNAS_C mas oferta_real_prov / fantasma_prov /
    rebaja_silenciosa_prov (siempre calculadas, tambien sin historia
    suficiente: sirven para probar la maquinaria, no para publicar).
    """
    extra = [f"{c}_prov" for c in _FLAGS]
    if df.empty or df["price"].notna().sum() == 0:
        return pd.DataFrame(columns=COLUMNAS_C + extra)

    precio, cartel, regular = _matrices(df)
    Pm, Sm = precio.to_numpy(float), cartel.to_numpy(float)
    n_dias = Pm.shape[1]
    ref = np.full_like(Pm, np.nan)
    moda = np.full_like(Pm, np.nan)
    n_obs = np.zeros_like(Pm, dtype=int)
    frac = np.full_like(Pm, np.nan)
    for t in range(1, n_dias):
        ini = max(0, t - lookback)
        bloque = Pm[:, ini:t]                       # [t - lookback, t - 1]: hoy no entra
        n_obs[:, t] = (~np.isnan(bloque)).sum(axis=1)
        filas = n_obs[:, t] > 0
        if filas.any():
            ref[filas, t] = np.nanmedian(bloque[filas], axis=1)
            moda[filas, t] = _moda_filas(bloque[filas])
            frac[filas, t] = np.nanmean(Sm[filas, ini:t], axis=1)

    obs = ~np.isnan(Pm)
    ii, tt = np.where(obs)
    out = pd.DataFrame({
        "retailer": precio.index.get_level_values(0)[ii],
        "item_id": precio.index.get_level_values(1)[ii],
        "fecha": precio.columns[tt],
        "price": Pm[ii, tt],
        "on_sale": Sm[ii, tt] == 1.0,
        "regular_price": regular.to_numpy(float)[ii, tt] if regular is not None else np.nan,
        "precio_habitual": ref[ii, tt],
        "ref_moda": moda[ii, tt],
        "n_obs_ventana": n_obs[ii, tt],
        "frac_on_sale": frac[ii, tt],
    })
    out["descuento_real"] = 1 - out["price"] / out["precio_habitual"]
    reg = out["regular_price"].where(out["regular_price"] > 0)
    out["descuento_anunciado"] = 1 - out["price"] / reg
    out["fiable"] = out["n_obs_ventana"] >= min_obs

    prov = _flags(out["descuento_real"], out["frac_on_sale"], out["on_sale"], umbral, techo)
    for col in _FLAGS:
        out[f"{col}_prov"] = prov[col]
        out[col] = prov[col].astype("boolean").where(out["fiable"], pd.NA)
    return out[COLUMNAS_C + extra]


def _flags(descuento, frac, on_sale, umbral, techo):
    """La regla, en un solo sitio. Devuelve {flag: Serie bool}."""
    if umbral <= 0:
        cae = descuento > _EPS
    else:
        cae = descuento >= umbral - _EPS
    oferta = cae & (frac <= techo + _EPS)
    oferta = oferta.fillna(False).astype(bool)
    on_sale = on_sale.astype(bool)
    return {"oferta_real": oferta,
            "fantasma": on_sale & ~oferta,
            "rebaja_silenciosa": oferta & ~on_sale}


def sensibilidad(flags, variantes=VARIANTES, techo=TECHO_FRAC_ON_SALE):
    """Tabla aparte, por (variante, cadena, fecha), sobre filas fiables:
    share en oferta real, share del cartel que es fantasma y share en
    rebaja silenciosa. No modifica `flags`.

    Todas las variantes se calculan con la misma ventana y el mismo
    requisito de fiabilidad que la principal; solo cambian la referencia
    (mediana o moda) y el umbral."""
    ev = flags[flags["fiable"].astype(bool)]
    filas = []
    if ev.empty:
        return pd.DataFrame(columns=["variante", "referencia", "umbral", "retailer", "fecha",
                                     "fiables", "on_sale", "oferta_real", "fantasma_sobre_cartel",
                                     "rebaja_silenciosa"])
    for nombre, referencia, umbral in variantes:
        base = ev["precio_habitual"] if referencia == "mediana" else ev["ref_moda"]
        desc = 1 - ev["price"] / base
        f = _flags(desc, ev["frac_on_sale"], ev["on_sale"], umbral, techo)
        d = pd.DataFrame({"retailer": ev["retailer"], "fecha": ev["fecha"],
                          "on_sale": ev["on_sale"].astype(bool), **f})
        for (cad, fecha), g in d.groupby(["retailer", "fecha"]):
            n_cartel = int(g["on_sale"].sum())
            filas.append({
                "variante": nombre, "referencia": referencia, "umbral": umbral,
                "retailer": cad, "fecha": fecha, "fiables": len(g),
                "on_sale": g["on_sale"].mean(),
                "oferta_real": g["oferta_real"].mean(),
                "fantasma_sobre_cartel": g["fantasma"].sum() / n_cartel if n_cartel else np.nan,
                "rebaja_silenciosa": g["rebaja_silenciosa"].mean(),
            })
    return pd.DataFrame(filas)


def resumen_productos(flags, dias_cadena):
    """Una fila por (retailer, item_id): historia, huecos y estado final.

    DESCRIPTIVO: usa todo el panel. No es un feature de ningun modelo
    (frac_dias_oferta_real y dias_con_hueco miran el futuro de cualquier
    dia que no sea el ultimo).

    `dias_cadena` = {cadena: [fechas con CSV]}; los huecos se cuentan solo
    sobre dias en que la cadena SI se recolecto, entre la primera y la
    ultima observacion del producto.
    """
    if flags.empty:
        return pd.DataFrame()
    g = flags.sort_values("fecha").groupby(["retailer", "item_id"], sort=False)
    res = g.agg(
        primera_fecha=("fecha", "min"),
        ultima_fecha=("fecha", "max"),
        dias_observados=("fecha", "size"),
        frac_on_sale_total=("on_sale", "mean"),
    ).reset_index()
    ultimo = g.tail(1).set_index(["retailer", "item_id"])
    res = res.set_index(["retailer", "item_id"])
    for col in ("price", "precio_habitual", "n_obs_ventana", "frac_on_sale"):
        res[f"{col}_ultimo"] = ultimo[col]
    for col in ("fiable",) + _FLAGS + tuple(f"{c}_prov" for c in _FLAGS):
        res[col] = ultimo[col]
    fiables = flags[flags["fiable"].astype(bool)]
    if not fiables.empty:
        share = (fiables.groupby(["retailer", "item_id"])["oferta_real"]
                 .apply(lambda s: s.astype(float).mean()))
        res["frac_dias_oferta_real"] = share
    else:
        res["frac_dias_oferta_real"] = np.nan
    res = res.reset_index()

    huecos = []
    for cad, ini, fin, n in zip(res["retailer"], res["primera_fecha"],
                                res["ultima_fecha"], res["dias_observados"]):
        fechas = dias_cadena.get(cad, [])
        en_rango = sum(1 for f in fechas if ini <= pd.Timestamp(f) <= fin)
        huecos.append(max(en_rango - n, 0))
    res["dias_con_hueco"] = huecos
    return res


def _fila_cadena(nombre, g):
    ev = g[g["fiable"].astype(bool)]
    cartel_ev = ev[ev["on_sale"]]

    def media(s):
        return s.astype(float).mean() if len(s) else np.nan

    return {
        "cadena": nombre,
        "filas": len(g),
        "on_sale_declarado": g["on_sale"].mean() if len(g) else np.nan,
        "descuento_anunciado_mediano": g.loc[g["on_sale"], "descuento_anunciado"].median(),
        "evaluables": len(ev),
        "no_evaluable": 1 - len(ev) / len(g) if len(g) else np.nan,
        "oferta_real": media(ev["oferta_real"]),
        "fantasma_sobre_cartel": media(cartel_ev["fantasma"]),
        "rebaja_silenciosa": media(ev["rebaja_silenciosa"]),
        "oferta_real_prov": g["oferta_real_prov"].mean() if len(g) else np.nan,
        "fantasma_prov": g["fantasma_prov"].mean() if len(g) else np.nan,
    }


def resumen_cadenas(flags, fecha):
    """Tabla por cadena sobre el dia `fecha`."""
    d = flags[flags["fecha"] == pd.Timestamp(fecha)]
    filas = [_fila_cadena(cad, g) for cad, g in d.groupby("retailer")]
    filas.append(_fila_cadena("TOTAL", d))
    return pd.DataFrame(filas)


def tabla_c(flags):
    """La tabla diaria que consumen el optimizador y el Modelo B."""
    return flags[COLUMNAS_C].copy()


def correr(lookback=LOOKBACK_DIAS, min_obs=MIN_OBS_VENTANA, umbral=UMBRAL_DESCUENTO,
           techo=TECHO_FRAC_ON_SALE, panel_df=None, silencioso=False):
    """Corre C sobre data/daily/ y escribe:

    oferta_real_panel__<fecha>.csv.gz   la tabla diaria de C (se regenera; en .gitignore)
    oferta_real__<fecha>.csv            resumen por producto (descriptivo)
    oferta_real_cadenas__<fecha>.csv    resumen por cadena del ultimo dia
    oferta_real_sensibilidad__<fecha>.csv  variantes, aparte de la regla

    Devuelve (flags, resumen_productos, resumen_cadenas)."""
    df = panel_df if panel_df is not None else P.cargar_panel(
        columnas=["retailer", "item_id", "price", "regular_price", "on_sale"])
    if df.empty:
        raise SystemExit("Panel vacio.")
    fecha = str(df["fecha"].max().date())
    dias = P.dias_por_cadena(df)
    n_dias = df["fecha"].nunique()

    flags = calcular(df, lookback, min_obs, umbral, techo)
    prod = resumen_productos(flags, dias)
    cad = resumen_cadenas(flags, fecha)
    sens = sensibilidad(flags, techo=techo)

    path = P.ruta_derivado("oferta_real_panel", fecha, "csv.gz")
    tabla_c(flags).to_csv(path, index=False, compression="gzip", lineterminator="\n")
    P.escribir_csv(prod, "oferta_real", fecha)
    P.escribir_csv(cad, "oferta_real_cadenas", fecha)
    P.escribir_csv(sens, "oferta_real_sensibilidad", fecha)

    if not silencioso:
        print(f"=== C: oferta_real | ultimo dia {fecha} | {n_dias} dias en el panel ===")
        print(f"ventana [t-{lookback}, t-1]  min_obs={min_obs}  umbral={umbral:.0%}  "
              f"techo_frac_on_sale={techo:.0%}")
        print()
        vista = cad.copy()
        for col in ("on_sale_declarado", "descuento_anunciado_mediano", "no_evaluable",
                    "oferta_real", "fantasma_sobre_cartel", "rebaja_silenciosa",
                    "oferta_real_prov", "fantasma_prov"):
            vista[col] = vista[col].map(P.pct)
        print(P.tabla(vista))
        print()
        print(P.aviso_fiabilidad(n_dias, min_obs + 1, "oferta_real / fantasma"))
        if n_dias <= min_obs:
            print("Las columnas *_prov son la version calculada con la historia que hay:\n"
                  "sirven para probar la maquinaria, NO para publicar.")
        print(f"-> {path.name} (tabla diaria de C, {len(flags)} filas)")
    return flags, prod, cad


def main(argv=None):
    P.configurar_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DIAS,
                    help=f"ventana en dias hacia atras (def. {LOOKBACK_DIAS})")
    ap.add_argument("--min-obs", type=int, default=MIN_OBS_VENTANA,
                    help=f"dias observados minimos para fiable=True (def. {MIN_OBS_VENTANA})")
    ap.add_argument("--umbral", type=float, default=UMBRAL_DESCUENTO,
                    help=f"descuento minimo bajo el habitual (def. {UMBRAL_DESCUENTO})")
    ap.add_argument("--techo", type=float, default=TECHO_FRAC_ON_SALE,
                    help=f"techo de frac_on_sale para ser oferta (def. {TECHO_FRAC_ON_SALE})")
    args = ap.parse_args(argv)
    if args.lookback < args.min_obs:
        ap.error("--lookback no puede ser menor que --min-obs")
    correr(args.lookback, args.min_obs, args.umbral, args.techo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
