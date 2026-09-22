#!/usr/bin/env python3
"""Tests del optimizador v0 con canastas y precios sinteticos.

    pytest tests/test_optimizador.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from analisis import optimizador as OPT

CADENAS = ["metro", "wong", "plaza_vea", "vivanda", "tottus"]


def _canasta(items):
    """items: {id: [cadenas]}; un envase por item, paquetes = 1."""
    filas = []
    for i, cads in items.items():
        for c in cads:
            filas.append({"id": i, "descripcion": f"item {i}", "grupo": "g", "cadena": c,
                          "item_id": f"{c}-{i}", "ean": "", "cantidad": 1.0, "paquetes": 1.0,
                          "unidad": "kg"})
    return pd.DataFrame(filas)


def _precios(tabla):
    """tabla: {(cadena, id): dict(price=, regular_price=, on_sale=, available=)}"""
    filas = []
    for (c, i), v in tabla.items():
        price = v.get("price", 10.0)
        reg = v.get("regular_price", price)
        filas.append({"retailer": c, "item_id": f"{c}-{i}", "price": price,
                      "regular_price": reg, "on_sale": v.get("on_sale", reg > price),
                      "available": v.get("available", True), "card_price": 1.0})
    return pd.DataFrame(filas)


def _c(tabla):
    """C del dia: {(cadena, id): (fiable, oferta_real, fantasma)}"""
    return pd.DataFrame([{"retailer": c, "item_id": f"{c}-{i}", "fiable": f,
                          "oferta_real": o, "fantasma": fa, "rebaja_silenciosa": False,
                          "precio_habitual": 10.0, "descuento_real": 0.0}
                         for (c, i), (f, o, fa) in tabla.items()])


def test_agotado_no_se_elige():
    can = _canasta({1: ["metro", "wong"]})
    p = _precios({("metro", 1): {"price": 5.0, "available": False},
                  ("wong", 1): {"price": 9.0}})
    plan, res, _ = OPT.resolver(can, p)
    fila = plan[plan["escenario"] == "con_tottus"].iloc[0]
    assert fila["cadena"] == "wong" and fila["precio_pagado"] == 9.0


def test_sin_precio_es_infactible_y_no_se_arrastra():
    can = _canasta({1: ["metro"], 2: ["metro", "wong"]})
    p = _precios({("metro", 2): {"price": 4.0}, ("wong", 2): {"price": 5.0}})
    plan, res, _ = OPT.resolver(can, p)
    r = res.set_index("escenario").loc["con_tottus"]
    assert r["items_sin_cadena_factible"] == 1 and r["items_con_plan"] == 1


def test_precio_pagado_es_price():
    """Ni el tachado ni la tarjeta: se paga `price`."""
    can = _canasta({1: ["metro", "wong"]})
    p = _precios({("metro", 1): {"price": 8.0, "regular_price": 12.0},
                  ("wong", 1): {"price": 9.0}})
    plan, res, _ = OPT.resolver(can, p)
    fila = plan[plan["escenario"] == "con_tottus"].iloc[0]
    assert fila["precio_pagado"] == 8.0 and fila["costo"] == 8.0
    assert res.set_index("escenario").loc["con_tottus", "costo_sistema"] == 8.0


def test_fantasma_no_cuenta_como_ahorro_de_la_linea_base():
    """La linea base elige la cadena por el cartel (metro promete 4 soles),
    paga price, y ese ahorro prometido queda como fantasma, no como real."""
    can = _canasta({1: ["metro", "wong"], 2: ["metro", "wong"]})
    p = _precios({("metro", 1): {"price": 10.0, "regular_price": 14.0},
                  ("metro", 2): {"price": 5.0},
                  ("wong", 1): {"price": 9.0}, ("wong", 2): {"price": 5.0}})
    c = _c({("metro", 1): (True, False, True), ("metro", 2): (True, False, False),
            ("wong", 1): (True, False, False), ("wong", 2): (True, False, False)})
    _, res, base = OPT.resolver(can, p, c)
    r = res.set_index("escenario").loc["con_tottus"]
    assert r["linea_base_cadena"] == "metro"
    assert r["linea_base_costo"] == 15.0
    assert r["linea_base_ahorro_anunciado"] == 4.0
    assert r["linea_base_ahorro_fantasma"] == 4.0
    assert r["linea_base_ahorro_en_oferta_real"] == 0.0
    assert r["sistema_costo_mismos_items"] == 14.0


def test_c_no_fiable_deja_el_ahorro_como_no_verificable():
    can = _canasta({1: ["metro"]})
    p = _precios({("metro", 1): {"price": 10.0, "regular_price": 14.0}})
    _, res, _ = OPT.resolver(can, p, c_dia=None)
    r = res.set_index("escenario").loc["con_tottus"]
    assert r["linea_base_ahorro_no_verificable"] == 4.0 and r["linea_base_ahorro_fantasma"] == 0.0


def test_linea_base_prioriza_cobertura():
    """Una cadena que no puede vender toda la canasta no es la linea base
    aunque su cartel prometa mas."""
    can = _canasta({1: ["metro", "wong"], 2: ["wong"]})
    p = _precios({("metro", 1): {"price": 5.0, "regular_price": 50.0},
                  ("wong", 1): {"price": 9.0}, ("wong", 2): {"price": 3.0}})
    _, res, _ = OPT.resolver(can, p)
    assert res.set_index("escenario").loc["con_tottus", "linea_base_cadena"] == "wong"


def test_dos_escenarios_de_tottus():
    can = _canasta({1: ["metro", "tottus"]})
    p = _precios({("metro", 1): {"price": 9.0},
                  ("tottus", 1): {"price": 7.0, "available": None}})
    plan, res, _ = OPT.resolver(can, p)
    por = plan.set_index("escenario")
    assert por.loc["con_tottus", "cadena"] == "tottus"
    assert por.loc["sin_tottus", "cadena"] == "metro"
    assert set(res["escenario"]) == {"con_tottus", "sin_tottus"}


def test_escenario_de_visita_apagado_por_defecto_y_exacto():
    can = _canasta({1: CADENAS[:3], 2: CADENAS[:3]})
    p = _precios({("metro", 1): {"price": 5.0}, ("metro", 2): {"price": 9.0},
                  ("wong", 1): {"price": 9.0}, ("wong", 2): {"price": 5.0},
                  ("plaza_vea", 1): {"price": 6.0}, ("plaza_vea", 2): {"price": 6.0}})
    plan, res, _ = OPT.resolver(can, p)
    assert not res["visita_activa"].any()
    assert not plan["escenario"].str.contains("visita").any()
    # sin visita: metro + wong = 10. Con 3 soles por visita: plaza_vea sola = 12+3 = 15 < 16
    plan, res, _ = OPT.resolver(can, p, costo_visita=3.0)
    r = res.set_index("escenario").loc["con_tottus"]
    assert r["costo_sistema"] == 10.0
    assert r["visita_costo_total"] == 15.0 and r["visita_cadenas"] == "plaza_vea"
    # tope de una cadena sin costo de visita
    _, res, _ = OPT.resolver(can, p, costo_visita=0.0, max_cadenas=1)
    assert res.set_index("escenario").loc["con_tottus", "visita_costo_total"] == 12.0


def test_modelo_a_solo_rellena_celdas_sin_precio():
    can = _canasta({1: ["metro", "wong", "plaza_vea"]})
    p = _precios({("metro", 1): {"price": 9.0},
                  ("wong", 1): {"price": 8.0, "available": False}})
    a = pd.DataFrame({"cadena": ["wong", "plaza_vea"], "item_id": ["wong-1", "plaza_vea-1"],
                      "precio_predicho": [1.0, 7.0]})
    plan, res, _ = OPT.resolver(can, p, a_pred=a)
    fila = plan[plan["escenario"] == "con_tottus"].iloc[0]
    assert fila["cadena"] == "plaza_vea" and fila["fuente_precio"] == "modelo_a"
    # la linea base no usa precios predichos
    assert res.set_index("escenario").loc["con_tottus", "linea_base_cadena"] == "metro"


def test_accion_temporal_oferta_real_se_compra_hoy_y_fantasma_no_decide():
    plan = pd.DataFrame({"cadena": ["metro", "wong", "vivanda"], "item_id": ["a", "b", "c"],
                         "oferta_real": [True, False, False], "fantasma": [False, True, False],
                         "precio_habitual": [10.0, 10.0, 10.0], "precio_pagado": [8.0, 10.0, 10.0],
                         "paquetes": [1.0, 1.0, 1.0]})
    sin_b = OPT.accion_temporal(plan)
    assert (sin_b["accion"] == "comprar_hoy").all()
    b = pd.DataFrame({"cadena": ["metro", "wong", "vivanda"], "item_id": ["a", "b", "c"],
                      "p_oferta": [0.9, 0.5, 0.5], "profundidad": [0.2, 0.2, 0.2]})
    con_b = OPT.accion_temporal(plan, b).set_index("item_id")
    assert con_b.loc["a", "accion"] == "comprar_hoy" and con_b.loc["a", "motivo_accion"] == "oferta_real_hoy"
    # b (fantasma) y c (sin cartel) tienen la misma prediccion: misma accion
    assert con_b.loc["b", "accion"] == con_b.loc["c", "accion"] == "esperar"


def test_comparador_sin_canasta_usa_universo_de_4_cadenas():
    dia = pd.DataFrame({
        "retailer": CADENAS[:4] + CADENAS[:3],
        "item_id": [f"x{i}" for i in range(7)],
        "product_name": ["A"] * 4 + ["B"] * 3,
        "ean": ["7750000000001"] * 4 + ["7750000000002"] * 3,
    })
    u = OPT.canasta_desde_universo(dia)
    assert set(u["id"]) == {"7750000000001"} and len(u) == 4


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
