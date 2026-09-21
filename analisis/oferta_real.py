"""Bloque 1 -- oferta_real: separar la oferta del cartel.

    python -m analisis.oferta_real                 # todos los dias, umbrales por defecto
    python -m analisis.oferta_real --lookback 30   # ventana mas corta
    python -m analisis.oferta_real --panel-completo  # ademas, flags dia a dia

El problema
-----------
`on_sale` copia el cartel de la tienda: regular_price > price. Un producto
que lleva el tachado el 90% de los dias no esta en oferta: ese es su precio
normal y el "regular" es un ancla de marketing. Con el cartel, el 24% de
filas en oferta que reporta la bitacora no es falsable.

El metodo
---------
Es la distincion de Nakamura y Steinsson (2008) entre precio regular y
precio de oferta: la frecuencia de cambio de precios --y con ella la
intensidad promocional-- depende criticamente de si las ofertas se
identifican contra un precio de referencia propio del producto y no contra
lo que la tienda declara. Bueno (2024) lo operacionaliza para el Peru con
un filtro de precio regular sobre una ventana movil de 30 dias: el precio
regular es el que domina la ventana, y una oferta es una desviacion corta
por debajo de el.

Aqui, por (cadena, SKU) y dia t:

    precio_ref(t)   = mediana de los precios observados en (t - LOOKBACK, t]
    frac_on_sale(t) = proporcion de dias de esa ventana con on_sale = True
    descuento(t)    = 1 - price(t) / precio_ref(t)

    oferta_real(t)        = descuento >= UMBRAL_DESCUENTO
                            y frac_on_sale < TECHO_FRAC_ON_SALE
    tachado_permanente(t) = on_sale y NO oferta_real

La mediana (y no la media de Bueno) porque es inmune a la propia oferta: una
caida de 5 dias en una ventana de 45 no mueve la referencia. El techo sobre
frac_on_sale es la salvaguarda de N&S: si el cartel esta casi siempre, el
"regular" no es regular y no hay oferta que medir.

Por que estos umbrales
----------------------
- LOOKBACK_DIAS = 45. Bueno usa 30; se alarga a 45 para que una promocion
  mensual (Cencosud publica ventanas "del 01al30") no ocupe la mayoria de
  la ventana y se convierta en referencia. Con 30 dias, una oferta que dura
  el mes entero engañaria a la mediana.
- MIN_OBS_VENTANA = 21. Con menos de 21 dias observados la mediana la fija
  un punado de precios y cualquier dip la arrastra. Por debajo, fiable=False
  y los flags salen NA: no se produce una cifra sesgada en silencio.
- UMBRAL_DESCUENTO = 0.10. El descuento mediano del cartel en el panel es
  13-17% (bitacora 20-09); 10% deja pasar las ofertas reales y descarta el
  ruido de redondeo y los "descuentos" de 2-3% que son ajuste de precio.
- TECHO_FRAC_ON_SALE = 0.5. Si el cartel esta mas de la mitad de los dias,
  el precio con cartel es el precio normal del producto.

Limite conocido: un cambio permanente de precio (baja y no vuelve) se marca
como oferta_real hasta que la nueva serie domina la ventana (~LOOKBACK/2
dias). Es inherente a cualquier filtro con ventana hacia atras, incluido el
de Bueno; el test sintetico lo documenta.
"""

import argparse

import numpy as np
import pandas as pd

from analisis import panel as P

# --- Umbrales, documentados arriba. No dispersarlos por el codigo. ---------
LOOKBACK_DIAS = 45          # ventana (dias calendario) del precio de referencia
MIN_OBS_VENTANA = 21        # dias observados minimos para fiable=True
UMBRAL_DESCUENTO = 0.10     # caida minima bajo la referencia para ser oferta
TECHO_FRAC_ON_SALE = 0.5    # por encima, el cartel es el precio normal
# ---------------------------------------------------------------------------

COLUMNAS_ENTRADA = ["retailer", "item_id", "fecha", "price", "on_sale"]


def _matrices(df):
    """Pivota (producto x dia calendario) precio y on_sale.

    Se usa el calendario completo entre el primer y el ultimo dia, no solo
    los dias con CSV: asi un dia sin recoleccion cuenta como hueco y la
    ventana sigue midiendose en dias, que es lo que dice el parametro.
    """
    d = df.loc[df["price"].notna(), COLUMNAS_ENTRADA].copy()
    d["fecha"] = pd.to_datetime(d["fecha"]).dt.normalize()
    d = d.drop_duplicates(["retailer", "item_id", "fecha"], keep="last")
    dias = pd.date_range(d["fecha"].min(), d["fecha"].max(), freq="D")
    idx = pd.MultiIndex.from_frame(d[["retailer", "item_id"]].drop_duplicates())
    precio = (d.pivot_table(index=["retailer", "item_id"], columns="fecha",
                            values="price", aggfunc="last")
              .reindex(index=idx, columns=dias))
    cartel = (d.assign(on_sale=d["on_sale"].astype(float))
              .pivot_table(index=["retailer", "item_id"], columns="fecha",
                           values="on_sale", aggfunc="last")
              .reindex(index=idx, columns=dias))
    return precio, cartel


def calcular(df, lookback=LOOKBACK_DIAS, min_obs=MIN_OBS_VENTANA,
             umbral=UMBRAL_DESCUENTO, techo=TECHO_FRAC_ON_SALE):
    """Flags dia a dia. Una fila por (retailer, item_id, fecha) observada.

    Columnas: price, on_sale, precio_ref, n_obs_ventana, frac_on_sale,
    descuento, fiable, oferta_real, tachado_permanente (NA si no fiable),
    oferta_real_prov, tachado_permanente_prov (siempre calculados).
    """
    if df.empty or df["price"].notna().sum() == 0:
        return pd.DataFrame(columns=COLUMNAS_ENTRADA + [
            "precio_ref", "n_obs_ventana", "frac_on_sale", "descuento", "fiable",
            "oferta_real", "tachado_permanente", "oferta_real_prov",
            "tachado_permanente_prov"])

    precio, cartel = _matrices(df)
    Pm, Sm = precio.to_numpy(float), cartel.to_numpy(float)
    n_dias = Pm.shape[1]
    ref = np.full_like(Pm, np.nan)
    n_obs = np.zeros_like(Pm, dtype=int)
    frac = np.full_like(Pm, np.nan)
    for t in range(n_dias):
        ini = max(0, t - lookback + 1)
        bloque = Pm[:, ini:t + 1]
        con_dato = ~np.isnan(bloque)
        n_obs[:, t] = con_dato.sum(axis=1)
        filas = n_obs[:, t] > 0
        if filas.any():
            ref[filas, t] = np.nanmedian(bloque[filas], axis=1)
            frac[filas, t] = np.nanmean(Sm[filas, ini:t + 1], axis=1)

    obs = ~np.isnan(Pm)
    ii, tt = np.where(obs)
    out = pd.DataFrame({
        "retailer": precio.index.get_level_values(0)[ii],
        "item_id": precio.index.get_level_values(1)[ii],
        "fecha": precio.columns[tt],
        "price": Pm[ii, tt],
        "on_sale": Sm[ii, tt] == 1.0,
        "precio_ref": ref[ii, tt],
        "n_obs_ventana": n_obs[ii, tt],
        "frac_on_sale": frac[ii, tt],
    })
    out["descuento"] = 1 - out["price"] / out["precio_ref"]
    out["fiable"] = out["n_obs_ventana"] >= min_obs
    out["oferta_real_prov"] = (out["descuento"] >= umbral) & (out["frac_on_sale"] < techo)
    out["tachado_permanente_prov"] = out["on_sale"] & ~out["oferta_real_prov"]
    for col in ("oferta_real", "tachado_permanente"):
        out[col] = out[f"{col}_prov"].astype("boolean").where(out["fiable"], pd.NA)
    return out


def resumen_productos(flags, dias_cadena):
    """Una fila por (retailer, item_id): historia, huecos y estado final.

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
    for col in ("price", "precio_ref", "n_obs_ventana", "frac_on_sale", "fiable",
                "oferta_real", "tachado_permanente", "oferta_real_prov",
                "tachado_permanente_prov"):
        res[col if col in ("fiable", "oferta_real", "tachado_permanente",
                           "oferta_real_prov", "tachado_permanente_prov")
            else f"{col}_ultimo"] = ultimo[col]
    fiables = flags[flags["fiable"]]
    if not fiables.empty:
        share = (fiables.groupby(["retailer", "item_id"])["oferta_real"]
                 .apply(lambda s: s.astype(float).mean()))
        res["frac_dias_oferta_real"] = share
    else:
        res["frac_dias_oferta_real"] = np.nan
    res = res.reset_index()

    # dias con hueco: dias recolectados de la cadena entre la primera y la
    # ultima observacion en los que el producto no tuvo precio valido.
    huecos = []
    for cad, ini, fin, n in zip(res["retailer"], res["primera_fecha"],
                                res["ultima_fecha"], res["dias_observados"]):
        fechas = dias_cadena.get(cad, [])
        en_rango = sum(1 for f in fechas if ini <= pd.Timestamp(f) <= fin)
        huecos.append(max(en_rango - n, 0))
    res["dias_con_hueco"] = huecos
    return res


def resumen_cadenas(flags, fecha):
    """Tabla por cadena sobre el dia `fecha`."""
    d = flags[flags["fecha"] == pd.Timestamp(fecha)]
    filas = []
    for cad, g in d.groupby("retailer"):
        ev = g[g["fiable"]]
        filas.append({
            "cadena": cad,
            "filas": len(g),
            "on_sale_declarado": g["on_sale"].mean(),
            "evaluables": len(ev),
            "no_evaluable": 1 - len(ev) / len(g) if len(g) else np.nan,
            "oferta_real": ev["oferta_real"].astype(float).mean() if len(ev) else np.nan,
            "tachado_permanente": ev["tachado_permanente"].astype(float).mean() if len(ev) else np.nan,
            "oferta_real_prov": g["oferta_real_prov"].mean(),
            "tachado_perm_prov": g["tachado_permanente_prov"].mean(),
        })
    tot = d
    ev = tot[tot["fiable"]]
    filas.append({
        "cadena": "TOTAL", "filas": len(tot),
        "on_sale_declarado": tot["on_sale"].mean() if len(tot) else np.nan,
        "evaluables": len(ev),
        "no_evaluable": 1 - len(ev) / len(tot) if len(tot) else np.nan,
        "oferta_real": ev["oferta_real"].astype(float).mean() if len(ev) else np.nan,
        "tachado_permanente": ev["tachado_permanente"].astype(float).mean() if len(ev) else np.nan,
        "oferta_real_prov": tot["oferta_real_prov"].mean() if len(tot) else np.nan,
        "tachado_perm_prov": tot["tachado_permanente_prov"].mean() if len(tot) else np.nan,
    })
    return pd.DataFrame(filas)


def correr(lookback=LOOKBACK_DIAS, min_obs=MIN_OBS_VENTANA, umbral=UMBRAL_DESCUENTO,
           techo=TECHO_FRAC_ON_SALE, panel_completo=False, panel_df=None, silencioso=False):
    """Corre el bloque sobre data/daily/ y escribe los derivados. Devuelve
    (flags, resumen_productos, resumen_cadenas)."""
    df = panel_df if panel_df is not None else P.cargar_panel(
        columnas=["retailer", "item_id", "price", "on_sale"])
    if df.empty:
        raise SystemExit("Panel vacio.")
    fecha = str(df["fecha"].max().date())
    dias = P.dias_por_cadena(df)
    n_dias = len(P.dias_disponibles()) if panel_df is None else df["fecha"].nunique()

    flags = calcular(df, lookback, min_obs, umbral, techo)
    prod = resumen_productos(flags, dias)
    cad = resumen_cadenas(flags, fecha)

    P.escribir_csv(prod, "oferta_real", fecha)
    P.escribir_csv(cad, "oferta_real_cadenas", fecha)
    if panel_completo:
        path = P.ruta_derivado("oferta_real_panel", fecha, "csv.gz")
        flags.to_csv(path, index=False, compression="gzip", lineterminator="\n")

    if not silencioso:
        print(f"=== oferta_real | ultimo dia {fecha} | {n_dias} dias en el panel ===")
        print(f"lookback={lookback}d  min_obs={min_obs}  umbral={umbral:.0%}  "
              f"techo_frac_on_sale={techo:.0%}")
        print()
        vista = cad.copy()
        for col in ("on_sale_declarado", "no_evaluable", "oferta_real",
                    "tachado_permanente", "oferta_real_prov", "tachado_perm_prov"):
            vista[col] = vista[col].map(P.pct)
        print(P.tabla(vista))
        print()
        print(P.aviso_fiabilidad(n_dias, min_obs, "oferta_real / tachado_permanente"))
        if n_dias < min_obs:
            print("Las columnas *_prov son la version calculada con la historia que hay:\n"
                  "sirven para probar la maquinaria, NO para publicar.")
        print(f"-> data/derived/oferta_real__{fecha}.csv ({len(prod)} productos)")
    return flags, prod, cad


def main(argv=None):
    P.configurar_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DIAS,
                    help=f"ventana en dias del precio de referencia (def. {LOOKBACK_DIAS})")
    ap.add_argument("--min-obs", type=int, default=MIN_OBS_VENTANA,
                    help=f"dias observados minimos para fiable=True (def. {MIN_OBS_VENTANA})")
    ap.add_argument("--umbral", type=float, default=UMBRAL_DESCUENTO,
                    help=f"descuento minimo bajo la referencia (def. {UMBRAL_DESCUENTO})")
    ap.add_argument("--techo", type=float, default=TECHO_FRAC_ON_SALE,
                    help=f"techo de frac_on_sale para ser oferta (def. {TECHO_FRAC_ON_SALE})")
    ap.add_argument("--panel-completo", action="store_true",
                    help="escribe ademas los flags dia a dia (csv.gz)")
    args = ap.parse_args(argv)
    if args.lookback < args.min_obs:
        ap.error("--lookback no puede ser menor que --min-obs")
    correr(args.lookback, args.min_obs, args.umbral, args.techo, args.panel_completo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
