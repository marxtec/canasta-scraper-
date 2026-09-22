"""Modelo A -- transversal: la prima relativa de cada cadena para un producto.

    NO SE ENTRENA TODAVIA. `fit` se niega a correr con menos de
    MIN_DIAS_PANEL dias de panel. No llamar sobre data/daily/ antes.

Que predice
-----------
Para un producto i (EAN de fabricante) presente en 2+ cadenas y la cadena c:

    r_ic = log(precio_habitual_ic) - media_c' log(precio_habitual_ic')

donde precio_habitual_ic es la mediana de `price` del producto en esa
cadena sobre la ventana de entrenamiento (la misma definicion de habitual
que C, aplicada a la ventana). La cadena mas barata predicha es
argmin_c r_hat_ic entre las cadenas que tienen el producto.

Que NO usa
----------
Ningun precio como feature: ni el de otras cadenas, ni el del dia, ni un
"nivel de precio" del producto (r_ic contiene p_ic: seria el target).
`verificar_features` lo hace cumplir. Los atributos del producto son del
EAN (iguales en todas las cadenas); lo unico que distingue dos filas del
mismo producto es la cadena, el grupo y la interaccion cadena x categoria.

Features: categoria comun, marca, marca propia vs nacional, log del
gramaje, unidad, cadena, grupo, cadena x categoria. Opcional: intensidad
promocional de (cadena, categoria), calculada SOLO con filas del fold de
entrenamiento.

Lineas base: la cadena ganadora mas frecuente del entrenamiento, y la
ganadora por categoria. Validacion: grupos por EAN (un EAN nunca esta en
entrenamiento y prueba a la vez) mas un holdout temporal con EAN no vistos.
Metrica que importa al optimizador: regret en soles = precio habitual en
la cadena predicha menos el minimo entre cadenas.

Que le entrega al optimizador
-----------------------------
`rellenar_celdas`: un precio predicho para celdas SIN precio observado
(nunca para un agotado), componiendo la prima predicha con los precios
observados del mismo producto en otras cadenas:
    p_hat_ic = media geometrica_c' [ p_ic' * exp(r_hat_ic - r_hat_ic') ]
Los precios ajenos entran en la composicion al predecir, no como feature
del modelo. Elegir el menor precio ya visto NO es A: eso es el optimizador.

El algoritmo (gradient boosting) se importa recien en `fit`; sin
scikit-learn el modulo carga igual y los tests corren con un estimador
de juguete.
"""

import zlib

import numpy as np
import pandas as pd

from analisis import panel as P

MIN_DIAS_PANEL = 60             # del orden de dos meses de panel
DIAS_HOLDOUT = 14               # holdout temporal: ultimas dos semanas
N_FOLDS = 5
MIN_CADENAS = 2

FEATURES_CAT = ["categoria_comun", "marca", "unidad", "cadena", "grupo", "cadena_x_categoria"]
FEATURES_NUM = ["log_gramaje", "marca_propia"]
FEATURE_INTENSIDAD = "intensidad_promocional"
# Ninguna feature puede llamarse asi: son precios o se derivan de precios.
PROHIBIDAS = ("price", "precio", "regular", "card", "descuento", "brecha", "nivel", "r_ic")

# Marcas propias de las cadenas, tal como aparecen en `brand` normalizado.
# Declarada a mano; revisar contra el panel antes de entrenar.
MARCAS_PROPIAS = {
    "metro", "wong", "cuisine & co", "bell's", "bells", "tottus", "precio uno",
    "la florencia", "artisan", "vivanda", "plaza vea", "tottus basics", "home care",
}

COLUMNAS_PANEL = ["retailer", "item_id", "ean", "brand", "category", "net_quantity",
                  "unit", "price", "on_sale", "available"]


class PanelInsuficiente(RuntimeError):
    """El panel no cubre el minimo de dias para entrenar."""


def verificar_features(nombres):
    malas = [n for n in nombres if any(p in n.lower() for p in PROHIBIDAS)]
    if malas:
        raise ValueError(f"features construidas con precios, prohibidas en A: {malas}")


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def _moda(s):
    s = s.dropna()
    return s.mode().iloc[0] if len(s) else None


def construir_dataset(panel, desde=None, hasta=None):
    """Una fila por (ean, cadena) con features y target r_ic.

    `precio_habitual` y `r_ic` quedan en el DataFrame para evaluar, nunca
    como features."""
    d = panel[P.ean_comparable(panel["ean"]) & panel["price"].notna()]
    if desde is not None:
        d = d[d["fecha"] >= pd.Timestamp(desde)]
    if hasta is not None:
        d = d[d["fecha"] <= pd.Timestamp(hasta)]
    if d.empty:
        return pd.DataFrame()
    d = d.assign(categoria_comun=d["category"].map(P.categoria_comun),
                 marca=d["brand"].fillna("").astype(str).map(P._plano))
    hab = (d.groupby(["ean", "retailer"])
           .agg(precio_habitual=("price", "median"), dias=("fecha", "nunique"))
           .reset_index())
    n = hab.groupby("ean")["retailer"].transform("nunique")
    hab = hab[n >= MIN_CADENAS]
    # atributos del PRODUCTO: uno por EAN, iguales en todas sus cadenas
    attr = d.groupby("ean").agg(categoria_comun=("categoria_comun", _moda),
                                marca=("marca", _moda),
                                gramaje=("net_quantity", "median"),
                                unidad=("unit", _moda)).reset_index()
    ds = hab.merge(attr, on="ean", how="left").rename(columns={"retailer": "cadena"})
    ds["grupo"] = ds["cadena"].map(P.GRUPOS)
    ds["cadena_x_categoria"] = ds["cadena"] + "|" + ds["categoria_comun"].fillna("otros")
    ds["marca_propia"] = ds["marca"].isin(MARCAS_PROPIAS).astype(int)
    ds["log_gramaje"] = np.log(ds["gramaje"].where(ds["gramaje"] > 0))
    lp = np.log(ds["precio_habitual"])
    ds["r_ic"] = lp - lp.groupby(ds["ean"]).transform("mean")
    return ds.drop(columns=["gramaje"]).reset_index(drop=True)


def intensidad_promocional(panel, eans, desde=None, hasta=None):
    """Share de on_sale por (cadena, categoria) usando SOLO los EAN dados
    (los del fold de entrenamiento) y las fechas dadas."""
    d = panel[panel["ean"].isin(set(eans))]
    if desde is not None:
        d = d[d["fecha"] >= pd.Timestamp(desde)]
    if hasta is not None:
        d = d[d["fecha"] <= pd.Timestamp(hasta)]
    d = d.assign(categoria_comun=d["category"].map(P.categoria_comun))
    return (d.groupby(["retailer", "categoria_comun"])["on_sale"].mean()
            .rename(FEATURE_INTENSIDAD).reset_index().rename(columns={"retailer": "cadena"}))


def _con_intensidad(ds, tabla):
    out = ds.drop(columns=[FEATURE_INTENSIDAD], errors="ignore").merge(
        tabla, on=["cadena", "categoria_comun"], how="left")
    return out


# ---------------------------------------------------------------------------
# Particiones
# ---------------------------------------------------------------------------

def fold_por_ean(eans, k=N_FOLDS):
    """Fold determinista por EAN (crc32): todas las filas de un EAN caen
    en el mismo fold."""
    return pd.Series([zlib.crc32(str(e).encode()) % k for e in eans], index=getattr(eans, "index", None))


def holdout_temporal(panel, corte):
    """(train, test): train con dias < corte; test con dias >= corte y solo
    EAN que no estan en train."""
    corte = pd.Timestamp(corte)
    tr = construir_dataset(panel, hasta=corte - pd.Timedelta(days=1))
    te = construir_dataset(panel, desde=corte)
    if not te.empty and not tr.empty:
        te = te[~te["ean"].isin(set(tr["ean"]))]
    return tr, te


# ---------------------------------------------------------------------------
# Lineas base y metricas
# ---------------------------------------------------------------------------

def _ganadora_real(ds):
    return ds.loc[ds.groupby("ean")["r_ic"].idxmin(), ["ean", "cadena", "categoria_comun"]]


def baseline_global(train):
    """Orden de cadenas por cuantas veces ganan en train (la primera es 'la
    cadena mas barata es siempre la misma')."""
    return list(_ganadora_real(train)["cadena"].value_counts().index)


def baseline_categoria(train):
    g = _ganadora_real(train)
    orden_global = baseline_global(train)
    por_cat = {cat: list(s.value_counts().index) + orden_global
               for cat, s in g.groupby("categoria_comun")["cadena"]}
    return por_cat, orden_global


def _elegir_por_orden(test, orden):
    """Para cada EAN, la primera cadena del orden que lo tiene."""
    out = {}
    for ean, g in test.groupby("ean"):
        cads = set(g["cadena"])
        cat = g["categoria_comun"].iloc[0]
        lista = orden.get(cat, orden.get(None, [])) if isinstance(orden, dict) else orden
        out[ean] = next((c for c in lista if c in cads), sorted(cads)[0])
    return pd.Series(out, name="cadena_pred")


def evaluar(test, cadena_pred):
    """Acierto top-1 y regret (soles y %) de una eleccion por EAN."""
    real = _ganadora_real(test).set_index("ean")["cadena"]
    p = test.set_index(["ean", "cadena"])["precio_habitual"]
    minimo = test.groupby("ean")["precio_habitual"].min()
    elegido = pd.Series({e: p.get((e, c), np.nan) for e, c in cadena_pred.items()})
    regret = (elegido - minimo.reindex(elegido.index))
    return {
        "n_productos": int(len(cadena_pred)),
        "acierto_top1": float((cadena_pred == real.reindex(cadena_pred.index)).mean()),
        "regret_soles_medio": float(regret.mean()),
        "regret_soles_mediano": float(regret.median()),
        "regret_pct_medio": float((regret / minimo.reindex(elegido.index)).mean()),
    }


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

def _gbm_por_defecto(n_cats):
    try:
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError as exc:                       # pragma: no cover
        raise ImportError("El Modelo A usa gradient boosting de scikit-learn: "
                          "pip install scikit-learn (no esta en requirements-analisis.txt "
                          "porque aun no se entrena).") from exc
    # las n_cats primeras columnas son categoricas (codigo -1 = desconocido,
    # que scikit-learn trata como faltante)
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05,
                                         categorical_features=list(range(n_cats)),
                                         random_state=0)


class Codificador:
    """Categoricas -> codigos enteros aprendidos en train. Se guardan las
    MAX_CATEGORIAS - 1 mas frecuentes; el resto va a "__otras__" y lo no
    visto en train a -1. El tope es el de scikit-learn (255 bins)."""

    MAX_CATEGORIAS = 255

    def __init__(self, cats, nums):
        self.cats, self.nums, self.mapas = cats, nums, {}

    def fit(self, df):
        for c in self.cats:
            frec = df[c].dropna().astype(str).value_counts()
            top = sorted(frec.index[:self.MAX_CATEGORIAS - 1])
            self.mapas[c] = {v: i for i, v in enumerate(top)}
            self.mapas[c]["__otras__"] = len(top)
        return self

    def transform(self, df):
        X = pd.DataFrame(index=df.index)
        for c in self.cats:
            m = self.mapas[c]
            s = df[c].astype(str)
            vistos = s.isin(m)
            X[c] = s.map(m).where(vistos, m["__otras__"]).where(df[c].notna(), -1).astype(int)
        for c in self.nums:
            X[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
        return X


def _features(usar_intensidad):
    return FEATURES_CAT, FEATURES_NUM + ([FEATURE_INTENSIDAD] if usar_intensidad else [])


def _entrenar(train, estimador, usar_intensidad):
    cats, nums = _features(usar_intensidad)
    verificar_features(cats + nums)
    cod = Codificador(cats, nums).fit(train)
    est = estimador() if callable(estimador) else _gbm_por_defecto(len(cats))
    est.fit(cod.transform(train), train["r_ic"].to_numpy())
    return cod, est


def _predecir(cod, est, ds):
    return pd.Series(est.predict(cod.transform(ds)), index=ds.index)


class ModeloA:
    def __init__(self, cod, est, metricas, usar_intensidad, intensidad):
        self.cod, self.est, self.metricas = cod, est, metricas
        self.usar_intensidad, self.intensidad = usar_intensidad, intensidad

    def predecir(self, ds):
        if self.usar_intensidad:
            ds = _con_intensidad(ds, self.intensidad)
        return _predecir(self.cod, self.est, ds)


def _metricas_fold(train, test, cod, est):
    test = test.assign(r_hat=_predecir(cod, est, test))
    pred = test.loc[test.groupby("ean")["r_hat"].idxmin()].set_index("ean")["cadena"]
    por_cat, orden = baseline_categoria(train)
    return {
        "modelo": {**evaluar(test, pred),
                   "mae_r": float((test["r_hat"] - test["r_ic"]).abs().mean())},
        "base_global": evaluar(test, _elegir_por_orden(test, orden)),
        "base_categoria": evaluar(test, _elegir_por_orden(test, por_cat)),
    }


def fit(panel, estimador=None, min_dias=MIN_DIAS_PANEL, usar_intensidad=False,
        n_folds=N_FOLDS, dias_holdout=DIAS_HOLDOUT):
    """Entrena y valida A. Se niega con menos de `min_dias` dias de panel.

    `estimador`: fabrica sin argumentos de un regresor con fit/predict
    (None = gradient boosting de scikit-learn). Devuelve ModeloA con las
    metricas de la validacion por grupos de EAN y del holdout temporal."""
    n_dias = panel["fecha"].nunique() if len(panel) else 0
    if n_dias < min_dias:
        raise PanelInsuficiente(f"Modelo A: el panel tiene {n_dias} dias; hacen falta {min_dias}. "
                                "No se entrena con los dias que prueban el scraper.")
    ds = construir_dataset(panel)
    if ds.empty:
        raise PanelInsuficiente("Modelo A: no hay EAN en 2+ cadenas.")

    def preparar(tr, te):
        if not usar_intensidad:
            return tr, te, None
        tab = intensidad_promocional(panel, tr["ean"].unique())
        return _con_intensidad(tr, tab), _con_intensidad(te, tab), tab

    folds = fold_por_ean(ds["ean"], n_folds)
    cv = []
    for k in range(n_folds):
        tr, te = ds[folds != k], ds[folds == k]
        if tr.empty or te.empty:
            continue
        tr, te, _ = preparar(tr, te)
        cod, est = _entrenar(tr, estimador, usar_intensidad)
        cv.append(_metricas_fold(tr, te, cod, est))

    corte = panel["fecha"].max() - pd.Timedelta(days=dias_holdout - 1)
    tr_t, te_t = holdout_temporal(panel, corte)
    holdout = None
    if not tr_t.empty and not te_t.empty:
        tr_t, te_t, _ = preparar(tr_t, te_t)
        cod, est = _entrenar(tr_t, estimador, usar_intensidad)
        holdout = _metricas_fold(tr_t, te_t, cod, est)

    ds_f, _, tab = preparar(ds, ds.iloc[:0])
    cod, est = _entrenar(ds_f, estimador, usar_intensidad)
    return ModeloA(cod, est, {"cv_por_ean": cv, "holdout_temporal": holdout,
                              "n_dias": n_dias, "n_productos": ds["ean"].nunique()},
                   usar_intensidad, tab)


def rellenar_celdas(modelo, atributos, observados):
    """Precio predicho para celdas sin precio observado.

    `atributos`: filas (ean, cadena) con las features de construir_dataset,
    para TODAS las cadenas del producto (observadas y faltantes).
    `observados`: (ean, cadena, price) con precio observado y comprable.
    Devuelve (ean, cadena, precio_predicho) solo para las celdas que no
    estan en `observados`."""
    a = atributos.assign(r_hat=modelo.predecir(atributos).to_numpy())
    obs = observados.merge(a[["ean", "cadena", "r_hat"]], on=["ean", "cadena"])
    faltan = a.merge(observados[["ean", "cadena"]], on=["ean", "cadena"], how="left", indicator=True)
    faltan = faltan[faltan["_merge"] == "left_only"]
    filas = []
    for _, f in faltan.iterrows():
        o = obs[obs["ean"] == f["ean"]]
        if o.empty:
            continue
        logp = np.log(o["price"]) + f["r_hat"] - o["r_hat"]
        filas.append({"ean": f["ean"], "cadena": f["cadena"],
                      "precio_predicho": float(np.exp(logp.mean()))})
    return pd.DataFrame(filas, columns=["ean", "cadena", "precio_predicho"])
