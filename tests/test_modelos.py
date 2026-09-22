#!/usr/bin/env python3
"""Tests de los Modelos A y B (esqueleto, sin entrenar sobre data/daily/).

    pytest tests/test_modelos.py

Paneles sinteticos minimos. Lo que se prueba: que `fit` se niega sin panel
suficiente, que ninguna feature cambia al alterar dias futuros, que A no
usa precios como feature, que la validacion respeta grupos por EAN y el
embargo de B, y que las etiquetas son las que dicen los docstrings.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest

from analisis import modelo_a as A
from analisis import modelo_b as B
from analisis import oferta_real as OR


class Media:
    """Regresor de juguete: predice la media del train."""

    def fit(self, X, y):
        self.m = float(np.mean(y))
        return self

    def predict(self, X):
        return np.full(len(X), self.m)


class Tasa:
    """Clasificador de juguete: la tasa de positivos del train."""

    def fit(self, X, y):
        self.p = float(np.mean(y))
        return self

    def predict_proba(self, X):
        return np.column_stack([np.full(len(X), 1 - self.p), np.full(len(X), self.p)])


# ---------------------------------------------------------------------------
# Paneles sinteticos
# ---------------------------------------------------------------------------

def panel_a(n_dias=60, n_ean=30, seed=0):
    rng = np.random.default_rng(seed)
    efecto = {"metro": 0.0, "wong": 0.0, "plaza_vea": -0.05, "tottus": 0.03}
    filas = []
    for d in pd.date_range("2026-10-01", periods=n_dias, freq="D"):
        for k in range(n_ean):
            base = 5 + k
            for cad, ef in efecto.items():
                if (k + len(cad)) % 5 == 0 and cad == "tottus":
                    continue                        # no todos en todas
                filas.append({"retailer": cad, "item_id": f"{cad}-{k}", "fecha": d,
                              "ean": f"77500000{k:05d}", "brand": f"Marca{k % 4}",
                              "category": "Abarrotes" if k % 2 else "Lacteos",
                              "net_quantity": 0.5 + k % 3, "unit": "kg",
                              "price": round(base * np.exp(ef + rng.normal(0, 0.01)), 2),
                              "on_sale": bool(rng.random() < 0.2), "available": True})
    return pd.DataFrame(filas)


def panel_b(n_dias=75, n_sku=20):
    """Ofertas periodicas de 3 dias cada 10, desfasadas por SKU; Metro y Wong
    con el mismo EAN (hermanas)."""
    filas = []
    for i, d in enumerate(pd.date_range("2026-10-01", periods=n_dias, freq="D")):
        for k in range(n_sku):
            en_oferta = (i + k) % 10 < 3 and i >= 25
            for cad in ("metro", "wong"):
                precio = 10.0 + k
                filas.append({"retailer": cad, "item_id": f"{cad}-{k}", "fecha": d,
                              "ean": f"77500000{k:05d}", "category": "Abarrotes",
                              "price": round(precio * (0.8 if en_oferta else 1.0), 2),
                              "regular_price": precio, "on_sale": en_oferta,
                              "promo_end": None})
    return pd.DataFrame(filas)


# ---------------------------------------------------------------------------
# Modelo A
# ---------------------------------------------------------------------------

def test_a_se_niega_con_panel_corto():
    with pytest.raises(A.PanelInsuficiente):
        A.fit(panel_a(n_dias=10), estimador=Media)


def test_a_no_acepta_features_de_precio():
    A.verificar_features(A.FEATURES_CAT + A.FEATURES_NUM + [A.FEATURE_INTENSIDAD])
    with pytest.raises(ValueError):
        A.verificar_features(["categoria_comun", "precio_otras_cadenas"])
    with pytest.raises(ValueError):
        A.verificar_features(["nivel_de_precio"])


def test_a_features_no_dependen_de_precios_ni_del_futuro():
    p = panel_a(n_dias=20)
    corte = p["fecha"].min() + pd.Timedelta(days=9)
    p2 = p.copy()
    fut = p2["fecha"] > corte
    p2.loc[fut, "price"] *= 3.0                      # se altera el futuro
    d1 = A.construir_dataset(p, hasta=corte)
    d2 = A.construir_dataset(p2, hasta=corte)
    pd.testing.assert_frame_equal(d1, d2)
    # y sobre todo el panel: los precios cambian el target, nunca las features
    p3 = p.copy()
    p3["price"] *= np.random.default_rng(1).uniform(0.5, 2.0, len(p3))
    f = A.FEATURES_CAT + A.FEATURES_NUM
    a, b = A.construir_dataset(p), A.construir_dataset(p3)
    pd.testing.assert_frame_equal(a[["ean", "cadena"] + f], b[["ean", "cadena"] + f])
    assert not np.allclose(a["r_ic"], b["r_ic"])


def test_a_target_es_prima_relativa():
    ds = A.construir_dataset(panel_a(n_dias=5))
    assert np.allclose(ds.groupby("ean")["r_ic"].sum(), 0.0, atol=1e-9)


def test_a_folds_por_ean_no_se_mezclan():
    ds = A.construir_dataset(panel_a(n_dias=5))
    f = A.fold_por_ean(ds["ean"])
    assert (ds.assign(f=f).groupby("ean")["f"].nunique() == 1).all()


def test_a_holdout_temporal_con_ean_no_vistos():
    p = panel_a(n_dias=30)
    p = p[~((p["fecha"] < "2026-10-20") & p["ean"].str.endswith(("1", "2")))]
    tr, te = A.holdout_temporal(p, "2026-10-20")
    assert not te.empty and not set(tr["ean"]) & set(te["ean"])


def test_a_baselines_y_regret():
    test = pd.DataFrame({"ean": ["e1"] * 3 + ["e2"] * 2,
                         "cadena": ["metro", "wong", "tottus", "metro", "tottus"],
                         "categoria_comun": ["x"] * 5,
                         "precio_habitual": [10.0, 9.0, 11.0, 5.0, 4.0]})
    lp = np.log(test["precio_habitual"])
    test["r_ic"] = lp - lp.groupby(test["ean"]).transform("mean")
    assert A.baseline_global(test)[0] in {"wong", "tottus"}
    m = A.evaluar(test, pd.Series({"e1": "metro", "e2": "tottus"}))
    assert m["acierto_top1"] == 0.5 and m["regret_soles_medio"] == 0.5


def test_a_fit_con_estimador_de_juguete():
    mod = A.fit(panel_a(n_dias=60), estimador=Media, usar_intensidad=True)
    assert len(mod.metricas["cv_por_ean"]) > 0
    for k in ("modelo", "base_global", "base_categoria"):
        assert "regret_soles_medio" in mod.metricas["cv_por_ean"][0][k]


def test_a_fit_con_gradient_boosting():
    pytest.importorskip("sklearn")
    mod = A.fit(panel_a(n_dias=60))
    m = mod.metricas["cv_por_ean"][0]["modelo"]
    assert np.isfinite(m["mae_r"])


def test_a_rellena_solo_celdas_sin_precio():
    class Fijo:
        def predecir(self, ds):
            return ds["cadena"].map({"metro": 0.0, "plaza_vea": np.log(0.9)})
    attr = pd.DataFrame({"ean": ["e", "e"], "cadena": ["metro", "plaza_vea"]})
    obs = pd.DataFrame({"ean": ["e"], "cadena": ["metro"], "price": [10.0]})
    r = A.rellenar_celdas(Fijo(), attr, obs)
    assert list(r["cadena"]) == ["plaza_vea"]
    assert abs(r["precio_predicho"].iloc[0] - 9.0) < 1e-9


# ---------------------------------------------------------------------------
# Modelo B
# ---------------------------------------------------------------------------

def test_b_horizonte_30_no_se_entrena():
    with pytest.raises(ValueError):
        B.fit(panel_b(), 30, clasificador=Tasa)
    with pytest.raises(ValueError):
        B.dataset(B.preparar(panel_b(n_dias=10)), 30)


def test_b_se_niega_con_panel_corto():
    with pytest.raises(A.PanelInsuficiente):
        B.fit(panel_b(n_dias=40), 7, clasificador=Tasa)


def test_b_features_no_cambian_al_alterar_el_futuro():
    p = panel_b(n_dias=50)
    corte = p["fecha"].min() + pd.Timedelta(days=35)
    f_trunc = B.features(B.preparar(p[p["fecha"] <= corte]))
    p2 = p.copy()
    fut = p2["fecha"] > corte
    p2.loc[fut, "price"] = p2.loc[fut, "price"] * 0.5
    p2.loc[fut, "on_sale"] = ~p2.loc[fut, "on_sale"]
    f_full = B.features(B.preparar(p2))
    f_full = f_full[f_full["fecha"] <= corte]
    cols = ["retailer", "item_id", "fecha"] + B.FEATURES
    pd.testing.assert_frame_equal(f_trunc[cols].reset_index(drop=True),
                                  f_full[cols].reset_index(drop=True))


def _una_serie(n=40, oferta=(30, 31, 32)):
    fechas = pd.date_range("2026-10-01", periods=n, freq="D")
    precio = [8.0 if i in oferta else 10.0 for i in range(n)]
    return pd.DataFrame({"retailer": "metro", "item_id": "a", "fecha": fechas,
                         "ean": "7750000000001", "category": "Abarrotes", "price": precio,
                         "regular_price": 10.0, "on_sale": [i in oferta for i in range(n)],
                         "promo_end": None})


def test_b_etiquetas_y_precio_de_espera():
    t = B.preparar(_una_serie())
    e = B.etiquetas(t, 7).set_index(t["fecha"].sort_values().to_numpy())
    dia = lambda i: pd.Timestamp("2026-10-01") + pd.Timedelta(days=i)
    assert bool(e.loc[dia(25), "y"])                                # (25, 32] ve la oferta
    assert e.loc[dia(25), "precio_espera"] == 8.0
    assert abs(e.loc[dia(25), "profundidad"] - 0.2) < 1e-9
    assert not bool(e.loc[dia(22), "y"])                            # (22, 29] no
    assert e.loc[dia(22), "precio_espera"] == 10.0
    assert bool(e.loc[dia(32), "completo"]) and not bool(e.loc[dia(33), "completo"])


def test_b_origenes_excluyen_oferta_de_hoy_y_no_fiables():
    ds = B.dataset(B.preparar(_una_serie()), 7)
    dias = set((ds["fecha"] - pd.Timestamp("2026-10-01")).dt.days)
    assert min(dias) >= OR.MIN_OBS_VENTANA and not dias & {30, 31, 32}


def test_b_dias_desde_la_ultima_oferta():
    f = B.features(B.preparar(_una_serie(n=40, oferta=(22, 23))))
    f = f.set_index((f["fecha"] - pd.Timestamp("2026-10-01")).dt.days)
    assert f.loc[30, "dias_desde_oferta_real"] == 7
    assert np.isnan(f.loc[21, "dias_desde_oferta_real"])


def test_b_embargo():
    ds = B.dataset(B.preparar(panel_b()), 7)
    tr, te = B.split_temporal(ds, 7)
    assert tr["fecha"].max() + pd.Timedelta(days=7) < te["fecha"].min()


def test_b_average_precision():
    assert abs(B.average_precision([1, 0, 1], [0.9, 0.8, 0.1]) - (1 + 2 / 3) / 2) < 1e-12


def test_b_fit_con_clasificador_de_juguete():
    mod = B.fit(panel_b(n_dias=80), 7, clasificador=Tasa, regresor=Media)
    m = mod.metricas
    assert m["embargo_ok"] and m["ahorro_comprar_hoy"] == 0.0
    assert set(m["pr_auc"]) == {"modelo", "base_tasa_sku", "base_tasa_categoria"}
    pred = mod.predecir(B.dataset(B.preparar(panel_b(n_dias=80)), 7).head(3))
    assert list(pred.columns) == ["cadena", "item_id", "fecha", "p_oferta", "profundidad"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
