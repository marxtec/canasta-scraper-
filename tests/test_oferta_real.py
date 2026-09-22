#!/usr/bin/env python3
"""Tests de C (oferta_real) con series sinteticas.

    python tests/test_oferta_real.py
    pytest tests/test_oferta_real.py

Sin red y sin leer data/: cada test fabrica su propia serie con la longitud
que necesita, asi que el resultado no depende de cuantos dias haya en el
panel real.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from analisis import oferta_real as OR

LOOK, MINOBS = OR.LOOKBACK_DIAS, OR.MIN_OBS_VENTANA


def _serie(precios, on_sale=None, cadena="metro", sku="1", inicio="2026-01-01"):
    fechas = pd.date_range(inicio, periods=len(precios), freq="D")
    on_sale = [False] * len(precios) if on_sale is None else on_sale
    return pd.DataFrame({
        "retailer": cadena, "item_id": sku, "fecha": fechas,
        "price": [float(p) if p is not None else np.nan for p in precios],
        "on_sale": on_sale,
    })


def _flags(df, **kw):
    out = OR.calcular(df, **kw)
    return out.sort_values("fecha").reset_index(drop=True)


def test_oferta_real_caida_corta_y_retorno():
    """Precio estable alto, caida de 5 dias del 20%, retorno. Los dias de la
    caida son oferta_real; los demas no."""
    n_pre, n_dip, n_post = LOOK, 5, 10
    precios = [10.0] * n_pre + [8.0] * n_dip + [10.0] * n_post
    cartel = [False] * n_pre + [True] * n_dip + [False] * n_post
    f = _flags(_serie(precios, cartel))
    dip = f.iloc[n_pre:n_pre + n_dip]
    assert dip["fiable"].all()
    assert dip["oferta_real"].astype(bool).all()
    assert not dip["fantasma"].astype(bool).any()
    assert not dip["rebaja_silenciosa"].astype(bool).any()
    assert np.allclose(dip["precio_habitual"], 10.0)
    resto = pd.concat([f.iloc[MINOBS:n_pre], f.iloc[n_pre + n_dip:]])
    assert not resto["oferta_real"].astype(bool).any()
    # sin cartel tambien se detecta: la oferta la define el precio, y es
    # rebaja silenciosa, no fantasma
    f2 = _flags(_serie(precios))
    caida = f2.iloc[n_pre:n_pre + n_dip]
    assert caida["oferta_real"].astype(bool).all()
    assert caida["rebaja_silenciosa"].astype(bool).all()
    assert not caida["fantasma"].astype(bool).any()


def test_fantasma_siempre_en_oferta():
    """Cartel todos los dias, precio plano: no hay oferta, hay ancla."""
    n = LOOK + 10
    f = _flags(_serie([7.5] * n, [True] * n))
    ev = f[f["fiable"]]
    assert len(ev) == n - MINOBS                 # hoy no cuenta en la ventana
    assert not ev["oferta_real"].astype(bool).any()
    assert ev["fantasma"].astype(bool).all()
    assert np.allclose(ev["frac_on_sale"], 1.0)


def test_fantasma_aunque_el_precio_baje():
    """Cartel casi siempre y una caida del 20%: el techo de frac_on_sale
    manda. Es la salvaguarda de Nakamura-Steinsson."""
    n = LOOK
    precios = [10.0] * n + [8.0] * 3
    cartel = [True] * (n + 3)
    f = _flags(_serie(precios, cartel))
    dip = f.iloc[n:]
    assert not dip["oferta_real"].astype(bool).any()
    assert dip["fantasma"].astype(bool).all()


def test_cambio_permanente_de_precio():
    """Baja del 20% que no vuelve. Al principio es indistinguible de una
    oferta (limite documentado); cuando la nueva serie domina la ventana el
    habitual se mueve y deja de marcarse."""
    precios = [10.0] * LOOK + [8.0] * (LOOK + 5)
    f = _flags(_serie(precios))
    primeros = f.iloc[LOOK:LOOK + 3]
    assert primeros["oferta_real"].astype(bool).all()      # todavia parece oferta
    ultimos = f.iloc[-5:]
    assert np.allclose(ultimos["precio_habitual"], 8.0)
    assert not ultimos["oferta_real"].astype(bool).any()   # ya es el precio normal
    assert not ultimos["fantasma"].astype(bool).any()


def test_lookback_insuficiente_sale_no_fiable():
    """Con menos de MIN_OBS_VENTANA dias ANTERIORES observados: fiable=False
    y los flags publicables son NA. Los *_prov si se calculan."""
    n = MINOBS - 1
    precios = [10.0] * (n - 1) + [5.0]
    f = _flags(_serie(precios, [False] * (n - 1) + [True]))
    assert not f["fiable"].any()
    assert f["oferta_real"].isna().all()
    assert f["fantasma"].isna().all()
    assert bool(f.iloc[-1]["oferta_real_prov"])
    # con MINOBS dias anteriores el ultimo pasa a fiable
    f2 = _flags(_serie([10.0] * MINOBS + [5.0]))
    assert bool(f2.iloc[-1]["fiable"]) and bool(f2.iloc[-1]["oferta_real"])


def test_hoy_no_entra_en_el_habitual():
    """El habitual es el de ANTES de hoy: una caida fuerte hoy no mueve su
    propia referencia."""
    f = _flags(_serie([10.0] * MINOBS + [5.0]))
    assert f.iloc[-1]["precio_habitual"] == 10.0
    assert abs(f.iloc[-1]["descuento_real"] - 0.5) < 1e-12
    assert int(f.iloc[-1]["n_obs_ventana"]) == MINOBS
    assert np.isnan(f.iloc[0]["precio_habitual"])          # el primer dia no tiene pasado


def test_huecos_no_cuentan_como_observaciones():
    """Un hueco de 10 dias en medio no aporta observaciones a la ventana:
    la ventana se mide en dias calendario, la fiabilidad en dias observados."""
    precios = [10.0] * 15 + [None] * 10 + [10.0] * 10
    df = _serie(precios)
    f = _flags(df)
    assert len(f) == 25                                     # solo dias con precio
    assert int(f.iloc[-1]["n_obs_ventana"]) == 24           # 25 observados menos hoy
    assert bool(f.iloc[-1]["fiable"])
    # con lookback corto los dias huecos si descuentan: la ventana
    # [t-20, t-1] toma 1 dia de la cabeza y 9 de la cola
    f2 = _flags(df, lookback=20, min_obs=10)
    assert int(f2.iloc[-1]["n_obs_ventana"]) == 10


def test_umbral_configurable():
    precios = [10.0] * LOOK + [9.3]                          # -7%
    assert not bool(_flags(_serie(precios)).iloc[-1]["oferta_real"])
    assert bool(_flags(_serie(precios), umbral=0.05).iloc[-1]["oferta_real"])


def test_borde_del_diez_por_ciento():
    """1 - 9/10 no es exactamente 0,10 en coma flotante: igual es oferta."""
    f = _flags(_serie([10.0] * MINOBS + [9.0]))
    assert bool(f.iloc[-1]["oferta_real"])


def test_ejemplo_de_la_bitacora_es_fantasma():
    """Precio de siempre S/ 10-11, hoy cartel 'antes 14, ahora 10': fantasma."""
    precios = [10.0, 11.0] * 15 + [10.0]
    df = _serie(precios, [False] * 30 + [True])
    df["regular_price"] = precios[:-1] + [14.0]
    hoy = _flags(df).iloc[-1]
    assert bool(hoy["fantasma"]) and not bool(hoy["oferta_real"])
    assert abs(hoy["descuento_anunciado"] - (1 - 10 / 14)) < 1e-12
    assert hoy["descuento_real"] < 0.10


def test_techo_es_mas_de_la_mitad():
    """Cartel exactamente la mitad de la ventana: aun puede haber oferta."""
    n = 2 * MINOBS
    cartel = [True, False] * MINOBS + [True]
    f = _flags(_serie([10.0] * n + [8.0], cartel))
    assert abs(f.iloc[-1]["frac_on_sale"] - 0.5) < 1e-12
    assert bool(f.iloc[-1]["oferta_real"])


def test_moda_y_empate_al_mas_alto():
    f = _flags(_serie([10.0] * 12 + [8.0] * 12 + [7.0]))
    assert f.iloc[-1]["ref_moda"] == 10.0                  # empate 12-12: gana el alto
    f2 = _flags(_serie([10.0] * 10 + [8.0] * 14 + [7.0]))
    assert f2.iloc[-1]["ref_moda"] == 8.0


def test_sensibilidad_no_pisa_la_regla():
    """Caida del 7%: no es oferta con la principal (10%) pero si con 5% y
    con 0%. La sensibilidad se reporta aparte y la tabla de C no cambia."""
    df = _serie([10.0] * MINOBS + [9.3], [False] * MINOBS + [True])
    f = OR.calcular(df)
    antes = f.copy()
    s = OR.sensibilidad(f).set_index("variante")
    assert s.loc["principal", "oferta_real"] == 0
    assert s.loc["umbral_5", "oferta_real"] == 1 and s.loc["umbral_0", "oferta_real"] == 1
    assert s.loc["umbral_15", "fantasma_sobre_cartel"] == 1
    assert set(s.index) == {v[0] for v in OR.VARIANTES}
    pd.testing.assert_frame_equal(f, antes)


def test_tabla_c_tiene_las_columnas_del_optimizador():
    t = OR.tabla_c(OR.calcular(_serie([10.0] * 3)))
    assert list(t.columns) == OR.COLUMNAS_C


def test_precio_cero_no_es_observacion():
    df = _serie([10.0] * 5 + [0.0] + [10.0] * 5)
    df.loc[df["price"] <= 0, "price"] = np.nan
    f = _flags(df)
    assert len(f) == 10


def test_varios_productos_y_cadenas_no_se_mezclan():
    a = _serie([10.0] * LOOK + [8.0], cadena="metro", sku="A")
    b = _serie([20.0] * LOOK + [20.0], cadena="wong", sku="A")
    f = OR.calcular(pd.concat([a, b]))
    ult = f.sort_values("fecha").groupby(["retailer", "item_id"]).tail(1)
    ult = ult.set_index("retailer")
    assert bool(ult.loc["metro", "oferta_real"])
    assert not bool(ult.loc["wong", "oferta_real"])
    assert ult.loc["wong", "precio_habitual"] == 20.0


def test_resumen_productos_cuenta_huecos():
    df = _serie([10.0, 10.0, None, 10.0])
    flags = OR.calcular(df)
    dias = {"metro": list(df["fecha"])}
    res = OR.resumen_productos(flags, dias)
    assert int(res.iloc[0]["dias_observados"]) == 3
    assert int(res.iloc[0]["dias_con_hueco"]) == 1
    assert not bool(res.iloc[0]["fiable"])


def test_resumen_cadenas_reporta_no_evaluable():
    b = _serie([10.0] * (LOOK + 1), sku="B")
    a = _serie([10.0] * 3, sku="A", inicio=b["fecha"].max() - pd.Timedelta(days=2))
    df = pd.concat([a, b])
    flags = OR.calcular(df)
    fecha = df["fecha"].max()
    res = OR.resumen_cadenas(flags, fecha).set_index("cadena")
    assert res.loc["metro", "filas"] == 2
    assert res.loc["metro", "evaluables"] == 1
    assert abs(res.loc["metro", "no_evaluable"] - 0.5) < 1e-9


def test_panel_vacio():
    assert OR.calcular(pd.DataFrame(columns=OR.COLUMNAS_ENTRADA)).empty


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
