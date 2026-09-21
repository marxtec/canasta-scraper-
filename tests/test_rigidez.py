#!/usr/bin/env python3
"""Tests del bloque 2 (rigidez) con paneles sinteticos. Sin red, sin data/.

    python tests/test_rigidez.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from analisis import panel as P
from analisis import rigidez as R


def _panel(precios_por_sku, fechas, cadena="metro", on_sale=None):
    """precios_por_sku = {sku: [p0, p1, ...]} alineado con fechas (None = ausente)."""
    filas = []
    for sku, ps in precios_por_sku.items():
        for i, (f, p) in enumerate(zip(fechas, ps)):
            if p is None:
                continue
            filas.append({"retailer": cadena, "item_id": sku, "fecha": pd.Timestamp(f),
                          "price": float(p),
                          "on_sale": bool(on_sale[sku][i]) if on_sale else False,
                          "oferta_real": pd.NA, "fiable": False})
    return pd.DataFrame(filas)


def test_calendario_peru():
    assert P.es_dia_habil("2026-09-21")          # lunes
    assert not P.es_dia_habil("2026-09-19")      # sabado
    assert not P.es_dia_habil("2026-09-20")      # domingo
    assert not P.es_dia_habil("2026-10-08")      # Combate de Angamos (jueves)
    assert not P.es_dia_habil("2026-04-03")      # Viernes Santo 2026
    assert P.es_dia_habil("2026-04-01")          # miercoles santo: habil


def test_tipo_transicion():
    assert R.tipo_transicion("2026-09-21", "2026-09-22") == "habil"      # lun -> mar
    assert R.tipo_transicion("2026-09-19", "2026-09-20") == "cruza_finde"
    assert R.tipo_transicion("2026-09-20", "2026-09-21") == "cruza_finde"  # dom -> lun
    assert R.tipo_transicion("2026-09-25", "2026-09-28") == "cruza_finde"  # vie -> lun
    assert R.tipo_transicion("2026-09-22", "2026-09-24") == "habil"      # salto mar -> jue


def test_transiciones_cuenta_subidas_bajadas_altas_bajas():
    fechas = ["2026-09-21", "2026-09-22", "2026-09-23"]   # lun, mar, mie
    precios = {
        "A": [10.0, 12.0, 12.0],      # sube, luego igual
        "B": [10.0, 8.0, 8.0],        # baja
        "C": [10.0, 10.0, None],      # baja de catalogo el 23
        "D": [None, 5.0, 5.0],        # alta el 22
        "E": [10.0, 10.004, 10.0],    # menos de medio centimo: NO es cambio
    }
    tr, cb = R.transiciones(_panel(precios, fechas))
    assert len(tr) == 2 and (tr["tipo"] == "habil").all()
    t0 = tr.iloc[0]
    assert t0["sku_seguidos"] == 4 and t0["sube"] == 1 and t0["baja"] == 1
    assert t0["altas_catalogo"] == 1 and t0["bajas_catalogo"] == 0
    t1 = tr.iloc[1]
    assert t1["bajas_catalogo"] == 1 and t1["sube"] == 0 and t1["baja"] == 0
    assert len(cb) == 2
    assert abs(cb["log_cambio"].max() - np.log(1.2)) < 1e-9
    assert abs(cb["log_cambio"].min() - np.log(0.8)) < 1e-9


def test_entradas_y_salidas_de_oferta_declarada():
    fechas = ["2026-09-21", "2026-09-22"]
    precios = {"A": [10, 8], "B": [8, 10], "C": [10, 10]}
    on_sale = {"A": [False, True], "B": [True, False], "C": [False, False]}
    tr, _ = R.transiciones(_panel(precios, fechas, on_sale=on_sale))
    assert tr.iloc[0]["entra_oferta_decl"] == 1
    assert tr.iloc[0]["sale_oferta_decl"] == 1


def test_oferta_real_solo_cuenta_si_fiable():
    fechas = ["2026-09-21", "2026-09-22"]
    df = _panel({"A": [10, 8], "B": [10, 8]}, fechas)
    df["oferta_real"] = pd.array([False, True, False, True], dtype="boolean")
    df["fiable"] = [True, True, False, False]        # B no es fiable
    tr, _ = R.transiciones(df)
    assert tr.iloc[0]["sku_evaluables_real"] == 1
    assert tr.iloc[0]["entra_oferta_real"] == 1


def test_agregar_separa_habil_y_finde():
    fechas = ["2026-09-18", "2026-09-19", "2026-09-21", "2026-09-22"]  # vie sab | lun mar
    precios = {"A": [10, 10, 10, 11], "B": [10, 10, 10, 10], "C": [10, 9, 10, 10]}
    tr, cb = R.transiciones(_panel(precios, fechas))
    assert list(tr["tipo"]) == ["cruza_finde", "cruza_finde", "habil"]
    agg = R.agregar(tr, cb).set_index(["retailer", "tipo"])
    assert agg.loc[("metro", "habil"), "n_transiciones"] == 1
    assert abs(agg.loc[("metro", "habil"), "tasa_cambio"] - 1 / 3) < 1e-9
    assert agg.loc[("metro", "cruza_finde"), "n_transiciones"] == 2
    assert abs(agg.loc[("metro", "cruza_finde"), "tasa_cambio"] - 2 / 6) < 1e-9
    assert agg.loc[("metro", "total"), "sku_seguidos"] == 9
    assert ("TOTAL", "total") in agg.index


def test_bimodalidad():
    rng = np.random.default_rng(0)
    bimodal = np.concatenate([rng.normal(-0.2, 0.02, 500), rng.normal(0.2, 0.02, 500)])
    unimodal = rng.normal(0, 0.05, 1000)
    assert R.coef_bimodalidad(bimodal) > R.UMBRAL_BIMODAL
    assert R.coef_bimodalidad(unimodal) < R.UMBRAL_BIMODAL
    assert np.isnan(R.coef_bimodalidad([0.1, 0.2]))


def test_horizontes_mide_solo_origenes_completos():
    fechas = pd.date_range("2026-09-21", periods=10, freq="D")
    # A entra en oferta el dia 6 (indice 5); B nunca
    on_sale = {"A": [False] * 5 + [True] * 5, "B": [False] * 10}
    precios = {"A": [10] * 10, "B": [10] * 10}
    df = _panel(precios, fechas, on_sale=on_sale)
    hz = R.horizontes(df, hs=(3, 30)).set_index(["horizonte", "version"])
    h3 = hz.loc[(3, "decl")]
    # origenes: dias con t + 3 <= ultimo dia (indices 0..6) y sin oferta en t:
    #   A: indices 0..4 (5), B: 0..6 (7) -> 12 origenes
    assert h3["origenes"] == 12
    # positivos: A en t=2,3,4 (la entrada del indice 5 cae dentro de (t, t+3])
    assert h3["positivos"] == 3
    assert abs(h3["tasa_positivos"] - 3 / 12) < 1e-9
    h30 = hz.loc[(30, "decl")]
    assert h30["origenes"] == 0 and np.isnan(h30["tasa_positivos"])
    assert h30["dias_faltan_para_medir"] == 30 - 9
    assert not np.isnan(h30["extrapolado_geometrico"])


def test_panel_de_un_dia_no_revienta():
    df = _panel({"A": [10]}, ["2026-09-21"])
    tr, cb = R.transiciones(df)
    assert tr.empty and cb.empty
    assert R.agregar(tr, cb).empty


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    fallos = 0
    for nombre, fn in fns:
        try:
            fn()
            print(f"  ok    {nombre}")
        except AssertionError as e:
            fallos += 1
            print(f"  FALLA {nombre}  {e}")
        except Exception as e:
            fallos += 1
            print(f"  ERROR {nombre}  {type(e).__name__}: {e}")
    print(f"\n{len(fns) - fallos}/{len(fns)} tests pasan")
    sys.exit(1 if fallos else 0)
