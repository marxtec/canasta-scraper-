"""Modelo B -- longitudinal: ¿entra en oferta real en los proximos h dias?

    NO SE ENTRENA TODAVIA. `fit` se niega a correr si el panel no tiene
    origenes completos suficientes para h = 7 o 14 despues de los 21 dias
    que necesita C. No llamar sobre data/daily/ antes.

Unidad
------
(cadena, SKU, dia t) con C fiable en t y SIN oferta real en t. Si el
producto ya esta en oferta real hoy se compra hoy por regla (optimizador),
no por B: por construccion B no puede reducirse a mirar el precio de hoy.

Target
------
    y_h(t) = 1 si hay oferta_real (de C) en algun dia fiable de (t, t+h]
    h in {7, 14}. El horizonte de 30 dias NO se entrena: con un panel de
    ~60 dias y 21 de arranque de C no deja origenes para validar.
Segunda salida: profundidad = el mayor descuento_real de C en los dias de
oferta de la ventana.
Solo entran origenes con la ventana completa (t + h <= ultimo dia de la
cadena).

Features (todas con informacion hasta t; `features` se prueba contra
alterar dias futuros)
    dias_desde_oferta_real   dias desde la ultima oferta real observada
    frac_oferta_real_sku     share de dias fiables del SKU con oferta real, hasta t
    frac_oferta_real_cat     lo mismo para (cadena, categoria), hasta t
    fantasma_hoy             el cartel de hoy es fantasma (C)
    frac_on_sale             share de cartel en la ventana de C (hasta t-1)
    descuento_real_hoy       precio de hoy frente al habitual de C
    dias_hasta_promo_end     vigencia publicada de la promocion (Metro, Wong)
    hermana_en_oferta        la otra cadena del grupo tiene el mismo EAN en
                             oferta real hoy
    dia_semana, quincena, cadena, categoria_comun
Prohibido: agregados del panel completo (resumen_productos de C, tasas del
panel entero): miran el futuro de cualquier t que no sea el ultimo.

Validacion: temporal, con EMBARGO de h dias: todo origen de entrenamiento
cumple t + h < primer origen de prueba (su etiqueta no mira la prueba).
Linea base predictiva: la tasa historica del SKU (frac_oferta_real_sku) y
la de la categoria. Linea base de decision: comprar hoy (ahorro 0).
Metricas: PR-AUC, Brier, calibracion por deciles y el ahorro de la
politica "esperar si p * profundidad * habitual > umbral" en el backtest,
con los precios realizados.
"""

import numpy as np
import pandas as pd

from analisis import oferta_real as OR
from analisis import panel as P
from analisis.modelo_a import Codificador, PanelInsuficiente

HORIZONTES = (7, 14)
MIN_DIAS_ORIGEN_TRAIN = 14      # dias distintos de origen para entrenar
MIN_DIAS_ORIGEN_TEST = 7        # dias distintos de origen para probar
UMBRAL_ESPERA = 0.0             # soles de ahorro esperado para esperar

FEATURES_CAT = ["cadena", "categoria_comun"]
FEATURES_NUM = ["dias_desde_oferta_real", "frac_oferta_real_sku", "frac_oferta_real_cat",
                "fantasma_hoy", "frac_on_sale", "descuento_real_hoy", "dias_hasta_promo_end",
                "hermana_en_oferta", "dia_semana", "quincena"]
FEATURES = FEATURES_CAT + FEATURES_NUM

COLUMNAS_PANEL = ["retailer", "item_id", "ean", "category", "price", "regular_price",
                  "on_sale", "promo_end"]


# ---------------------------------------------------------------------------
# Tabla base: C mas atributos
# ---------------------------------------------------------------------------

def preparar(panel):
    """La tabla de C (oferta_real.calcular) con ean, categoria, grupo y
    promo_end del mismo dia."""
    c = OR.calcular(panel[["retailer", "item_id", "fecha", "price", "on_sale"]
                          + (["regular_price"] if "regular_price" in panel else [])])
    attr_cols = [x for x in ("ean", "category", "promo_end") if x in panel]
    attr = (panel[["retailer", "item_id", "fecha"] + attr_cols]
            .drop_duplicates(["retailer", "item_id", "fecha"], keep="last"))
    t = c.merge(attr, on=["retailer", "item_id", "fecha"], how="left")
    t["categoria_comun"] = t["category"].map(P.categoria_comun) if "category" in t else "otros"
    t["grupo"] = t["retailer"].map(P.GRUPOS)
    return t.sort_values(["retailer", "item_id", "fecha"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Features, solo con informacion hasta t
# ---------------------------------------------------------------------------

def features(tabla):
    """Una fila por fila de `tabla` con FEATURES calculadas hasta t."""
    t = tabla.sort_values(["retailer", "item_id", "fecha"]).reset_index(drop=True)
    fiable = t["fiable"].astype(bool)
    of = t["oferta_real"].astype("boolean").fillna(False).astype(bool) & fiable

    ultima = t["fecha"].where(of).groupby([t["retailer"], t["item_id"]]).ffill()
    # la ultima oferta ANTES de hoy (en un origen hoy no hay oferta)
    ultima_prev = ultima.groupby([t["retailer"], t["item_id"]]).shift(1)
    dias_desde = (t["fecha"] - ultima.where(~of, ultima_prev)).dt.days

    cum_of = of.astype(int).groupby([t["retailer"], t["item_id"]]).cumsum()
    cum_fi = fiable.astype(int).groupby([t["retailer"], t["item_id"]]).cumsum()

    diario = (pd.DataFrame({"retailer": t["retailer"], "categoria_comun": t["categoria_comun"],
                            "fecha": t["fecha"], "of": of.astype(int), "fi": fiable.astype(int)})
              .groupby(["retailer", "categoria_comun", "fecha"])[["of", "fi"]].sum()
              .groupby(level=[0, 1]).cumsum().reset_index())
    diario["frac_oferta_real_cat"] = diario["of"] / diario["fi"].where(diario["fi"] > 0)
    cat = t[["retailer", "categoria_comun", "fecha"]].merge(
        diario[["retailer", "categoria_comun", "fecha", "frac_oferta_real_cat"]],
        on=["retailer", "categoria_comun", "fecha"], how="left")["frac_oferta_real_cat"]

    # hermana: otra cadena del mismo grupo, mismo EAN, oferta real hoy
    if "ean" in t:
        e = pd.DataFrame({"ean": t["ean"], "grupo": t["grupo"], "fecha": t["fecha"],
                          "of": of.astype(int)})
        tot = e.groupby(["ean", "grupo", "fecha"])["of"].transform("sum")
        hermana = ((tot - e["of"]) > 0).astype(int).where(t["ean"].notna(), 0)
    else:
        hermana = pd.Series(0, index=t.index)

    if "promo_end" in t:
        fin = pd.to_datetime(t["promo_end"], errors="coerce")
        hasta_fin = (fin - t["fecha"]).dt.days
        hasta_fin = hasta_fin.where(hasta_fin >= 0)
    else:
        hasta_fin = pd.Series(np.nan, index=t.index)

    out = t[["retailer", "item_id", "fecha", "ean", "price", "precio_habitual", "fiable",
             "oferta_real", "categoria_comun"]].copy() if "ean" in t else \
        t[["retailer", "item_id", "fecha", "price", "precio_habitual", "fiable",
           "oferta_real", "categoria_comun"]].copy()
    out["cadena"] = t["retailer"]
    out["dias_desde_oferta_real"] = dias_desde
    out["frac_oferta_real_sku"] = cum_of / cum_fi.where(cum_fi > 0)
    out["frac_oferta_real_cat"] = cat.to_numpy()
    out["fantasma_hoy"] = t["fantasma"].astype("boolean").fillna(False).astype(int)
    out["frac_on_sale"] = t["frac_on_sale"]
    out["descuento_real_hoy"] = t["descuento_real"]
    out["dias_hasta_promo_end"] = hasta_fin
    out["hermana_en_oferta"] = hermana.to_numpy()
    out["dia_semana"] = t["fecha"].dt.dayofweek
    out["quincena"] = np.where(t["fecha"].dt.day <= 15, 1, 2)
    return out


# ---------------------------------------------------------------------------
# Etiquetas (miran el futuro a proposito: solo como target)
# ---------------------------------------------------------------------------

def etiquetas(tabla, h):
    """y, profundidad, precio_espera y completo por fila, sobre el calendario
    de cada cadena. precio_espera = el precio del primer dia de oferta real
    en (t, t+h], o el ultimo observado en la ventana si no hay oferta."""
    t = tabla[["retailer", "item_id", "fecha", "price", "fiable", "oferta_real",
               "descuento_real"]].copy()
    t["of"] = t["oferta_real"].astype("boolean").fillna(False).astype(bool) & t["fiable"].astype(bool)
    dias = pd.date_range(t["fecha"].min(), t["fecha"].max(), freq="D")
    idx = pd.MultiIndex.from_frame(t[["retailer", "item_id"]].drop_duplicates())

    def mat(col, fill):
        return (t.pivot_table(index=["retailer", "item_id"], columns="fecha", values=col,
                              aggfunc="last", dropna=False)
                .reindex(index=idx, columns=dias).to_numpy(float))

    OF = np.nan_to_num(mat("of", 0), nan=0.0) > 0
    D = mat("descuento_real", np.nan)
    PR = mat("price", np.nan)
    n, m = OF.shape
    y = np.zeros((n, m), bool)
    prof = np.full((n, m), np.nan)
    pesp = np.full((n, m), np.nan)
    ultimo_obs = np.full((n, m), np.nan)
    for k in range(1, h + 1):
        of_k = np.zeros_like(OF)
        of_k[:, :m - k] = OF[:, k:]
        d_k = np.full_like(D, np.nan)
        d_k[:, :m - k] = D[:, k:]
        p_k = np.full_like(PR, np.nan)
        p_k[:, :m - k] = PR[:, k:]
        nuevo = of_k & ~y
        pesp[nuevo] = p_k[nuevo]
        y |= of_k
        prof = np.where(of_k, np.fmax(prof, d_k), prof)
        ultimo_obs = np.where(~np.isnan(p_k), p_k, ultimo_obs)
    pesp = np.where(y, pesp, ultimo_obs)

    ultimo_cadena = t.groupby("retailer")["fecha"].max()
    cad = idx.get_level_values(0)
    fin = np.array([ultimo_cadena[c] for c in cad], dtype="datetime64[ns]")
    completo = (dias.to_numpy()[None, :] + np.timedelta64(h, "D")) <= fin[:, None]

    ii, tt = np.nonzero(~np.isnan(PR))
    return pd.DataFrame({
        "retailer": cad[ii], "item_id": idx.get_level_values(1)[ii], "fecha": dias[tt],
        "y": y[ii, tt], "profundidad": prof[ii, tt], "precio_espera": pesp[ii, tt],
        "completo": completo[ii, tt],
    })


def dataset(tabla, h):
    """Origenes (fiable, sin oferta real hoy, ventana completa) con
    features y etiquetas."""
    if h not in HORIZONTES:
        raise ValueError(f"Modelo B entrena solo h en {HORIZONTES}; h={h} no se entrena.")
    f = features(tabla)
    e = etiquetas(tabla, h)
    d = f.merge(e, on=["retailer", "item_id", "fecha"], how="inner")
    origen = (d["fiable"].astype(bool)
              & ~d["oferta_real"].astype("boolean").fillna(False).astype(bool)
              & d["completo"])
    return d[origen].reset_index(drop=True)


def split_temporal(ds, h, dias_test=MIN_DIAS_ORIGEN_TEST):
    """(train, test) con embargo: test = los ultimos `dias_test` dias de
    origen; train = origenes con t + h < primer dia de test."""
    fechas = np.sort(ds["fecha"].unique())
    if len(fechas) < dias_test + 1:
        return ds.iloc[:0], ds.iloc[:0]
    inicio_test = pd.Timestamp(fechas[-dias_test])
    test = ds[ds["fecha"] >= inicio_test]
    train = ds[ds["fecha"] + pd.Timedelta(days=h) < inicio_test]
    return train, test


# ---------------------------------------------------------------------------
# Metricas
# ---------------------------------------------------------------------------

def average_precision(y, score):
    """PR-AUC como precision promedio (sin scikit-learn)."""
    y = np.asarray(y, bool)
    s = np.asarray(score, float)
    ok = ~np.isnan(s)
    y, s = y[ok], s[ok]
    if y.sum() == 0:
        return np.nan
    orden = np.argsort(-s, kind="mergesort")
    y = y[orden]
    tp = np.cumsum(y)
    precision = tp / np.arange(1, len(y) + 1)
    return float((precision * y).sum() / y.sum())


def brier(y, p):
    y, p = np.asarray(y, float), np.asarray(p, float)
    ok = ~np.isnan(p)
    return float(np.mean((p[ok] - y[ok]) ** 2))


def calibracion(y, p, bins=10):
    d = pd.DataFrame({"y": np.asarray(y, float), "p": np.asarray(p, float)}).dropna()
    d["bin"] = pd.qcut(d["p"].rank(method="first"), min(bins, len(d)), labels=False)
    return d.groupby("bin").agg(p_media=("p", "mean"), y_media=("y", "mean"),
                                n=("y", "size")).reset_index()


def ahorro_politica(test, p, profundidad, umbral=UMBRAL_ESPERA):
    """Backtest de la decision, con precios realizados: espera si
    p * profundidad * habitual > umbral; si espera paga precio_espera, si no
    paga price de hoy. Frente a comprar hoy (ahorro 0)."""
    ref = test["precio_habitual"].fillna(test["price"])
    esperar = (np.asarray(p) * np.nan_to_num(np.asarray(profundidad)) * ref) > umbral
    ahorro = np.where(esperar, test["price"] - test["precio_espera"], 0.0)
    return {"espera_share": float(esperar.mean()), "ahorro_total": float(np.nansum(ahorro)),
            "ahorro_medio_por_origen": float(np.nanmean(ahorro))}


# ---------------------------------------------------------------------------
# Entrenamiento
# ---------------------------------------------------------------------------

def _gbm(tipo, n_cats):
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    except ImportError as exc:                       # pragma: no cover
        raise ImportError("El Modelo B usa gradient boosting de scikit-learn: "
                          "pip install scikit-learn.") from exc
    cls = HistGradientBoostingClassifier if tipo == "clf" else HistGradientBoostingRegressor
    return cls(max_iter=300, learning_rate=0.05, categorical_features=list(range(n_cats)),
               random_state=0)


class ModeloB:
    def __init__(self, h, cod, clf, reg, prof_media, metricas):
        self.h, self.cod, self.clf, self.reg = h, cod, clf, reg
        self.prof_media, self.metricas = prof_media, metricas

    def predecir(self, ds):
        """(cadena, item_id, fecha, p_oferta, profundidad) para el optimizador."""
        X = self.cod.transform(ds)
        p = self.clf.predict_proba(X)[:, 1]
        prof = self.reg.predict(X) if self.reg is not None else np.full(len(ds), self.prof_media)
        return pd.DataFrame({"cadena": ds["cadena"].to_numpy(), "item_id": ds["item_id"].to_numpy(),
                             "fecha": ds["fecha"].to_numpy(), "p_oferta": p,
                             "profundidad": np.clip(prof, 0, None)})


def verificar_panel(tabla, h):
    ds = dataset(tabla, h)
    n = ds["fecha"].nunique()
    necesarios = MIN_DIAS_ORIGEN_TRAIN + h + MIN_DIAS_ORIGEN_TEST
    if n < necesarios:
        raise PanelInsuficiente(
            f"Modelo B h={h}: {n} dias de origen completos (fiables, despues de los "
            f"{OR.MIN_OBS_VENTANA} dias de C); hacen falta {necesarios} "
            f"({MIN_DIAS_ORIGEN_TRAIN} de entrenamiento + {h} de embargo + "
            f"{MIN_DIAS_ORIGEN_TEST} de prueba). No se entrena con los dias que prueban el scraper.")
    return ds


def fit(panel, h, clasificador=None, regresor=None, tabla=None):
    """Entrena y valida B para el horizonte h. Se niega si el panel no
    alcanza. `clasificador` / `regresor`: fabricas sin argumentos con
    fit/predict(_proba); None = gradient boosting de scikit-learn."""
    if h not in HORIZONTES:
        raise ValueError(f"Modelo B entrena solo h en {HORIZONTES}; h={h} no se entrena.")
    tabla = tabla if tabla is not None else preparar(panel)
    ds = verificar_panel(tabla, h)
    train, test = split_temporal(ds, h)
    if train["fecha"].nunique() < MIN_DIAS_ORIGEN_TRAIN or train["y"].nunique() < 2:
        raise PanelInsuficiente(f"Modelo B h={h}: el entrenamiento no tiene dias o clases suficientes.")

    cod = Codificador(FEATURES_CAT, FEATURES_NUM).fit(train)
    clf = clasificador() if clasificador else _gbm("clf", len(FEATURES_CAT))
    clf.fit(cod.transform(train), train["y"].astype(int).to_numpy())
    pos = train[train["y"]]
    prof_media = float(pos["profundidad"].mean()) if len(pos) else 0.0
    reg = None
    if len(pos) >= 20:
        reg = regresor() if regresor else _gbm("reg", len(FEATURES_CAT))
        reg.fit(cod.transform(pos), pos["profundidad"].to_numpy())
    modelo = ModeloB(h, cod, clf, reg, prof_media, {})

    pred = modelo.predecir(test)
    y = test["y"].to_numpy()
    base_sku = test["frac_oferta_real_sku"].to_numpy()
    base_cat = test["frac_oferta_real_cat"].to_numpy()
    modelo.metricas = {
        "h": h, "origenes_train": len(train), "origenes_test": len(test),
        "tasa_positivos_test": float(y.mean()),
        "pr_auc": {"modelo": average_precision(y, pred["p_oferta"]),
                   "base_tasa_sku": average_precision(y, base_sku),
                   "base_tasa_categoria": average_precision(y, base_cat)},
        "brier": {"modelo": brier(y, pred["p_oferta"]), "base_tasa_sku": brier(y, base_sku)},
        "calibracion": calibracion(y, pred["p_oferta"]),
        "ahorro_politica": ahorro_politica(test, pred["p_oferta"], pred["profundidad"]),
        "ahorro_comprar_hoy": 0.0,
        "embargo_ok": bool(train["fecha"].max() + pd.Timedelta(days=h) < test["fecha"].min()),
    }
    return modelo
