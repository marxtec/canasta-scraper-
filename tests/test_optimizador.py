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


# ---------------------------------------------------------------------------
# Paquetes enteros y niveles (docs: Metodologia de estandarizacion, secc. 4 y 9)
# ---------------------------------------------------------------------------

def test_costo_entero_combina_tamanos():
    """Arroz, hogar de 4 (17,688 kg) en Metro el 22-09: 3 x 5 kg + 3 x 1 kg
    (S/ 78,60) cuesta menos que 4 x 5 kg (S/ 85,20)."""
    costo, compra = OPT.costo_entero(147.4 * 30 / 1000 * 4, [(5.0, 21.3), (1.0, 4.9)])
    assert abs(costo - 78.6) < 1e-9 and compra == {5.0: 3, 1.0: 3}


def test_costo_entero_elige_el_tamano_por_costo_total_no_por_precio_por_kg():
    """Margarina, 445 g: el pote de 220 g sale mas barato por kg (S/ 33,6
    contra 33,8), pero cubrir 445 g pide tres (S/ 22,20) y uno de 450 g
    alcanza (S/ 15,20)."""
    costo, compra = OPT.costo_entero(0.445, [(0.22, 7.4), (0.45, 15.2)])
    assert abs(costo - 15.2) < 1e-9 and compra == {0.45: 1}
    costo, compra = OPT.costo_entero(0.288, [(0.22, 7.9), (0.45, 15.2)])   # Metro, hogar de 4
    assert abs(costo - 15.2) < 1e-9 and compra == {0.45: 1}
    assert np.isnan(OPT.costo_entero(1.0, [])[0])


def _canasta_con_alternativa():
    filas = []
    for c in ["metro", "wong"]:
        for w, base in ((5.0, True), (1.0, False)):
            filas.append({"id": 5, "descripcion": "arroz", "grupo": "g", "cadena": c,
                          "item_id": f"{c}-{w:g}", "ean": "", "cantidad": 4.422,
                          "paquetes": 4.422 / w, "unidad": "kg", "net_quantity": w,
                          "identidad": "ean", "nivel": "nucleo", "presentacion_base": base})
    filas.append({"id": 62, "descripcion": "mandarina", "grupo": "g", "cadena": "metro",
                  "item_id": "metro-m", "ean": "", "cantidad": 1.0, "paquetes": 1.0,
                  "unidad": "kg", "net_quantity": 1.0, "identidad": "por_kilo",
                  "nivel": "ampliado_1", "presentacion_base": True})
    return pd.DataFrame(filas)


def test_celda_es_la_presentacion_base_y_el_entero_usa_todas():
    precios = pd.DataFrame([
        {"retailer": "metro", "item_id": "metro-5", "price": 21.3},
        {"retailer": "metro", "item_id": "metro-1", "price": 4.9},
        {"retailer": "wong", "item_id": "wong-5", "price": 21.5},
        {"retailer": "wong", "item_id": "wong-1", "price": 5.1},
        {"retailer": "metro", "item_id": "metro-m", "price": 5.0},
    ]).assign(regular_price=lambda x: x["price"], on_sale=False, available=True)
    cel = OPT.celdas(_canasta_con_alternativa(), precios, personas=4)
    assert len(cel) == 3                                   # una celda por item y cadena
    m = cel[(cel["id"] == 5) & (cel["cadena"] == "metro")].iloc[0]
    assert abs(m["costo"] - 4.422 * 21.3 / 5) < 1e-9       # continuo: solo la base
    assert abs(m["costo_entero"] - 78.6) < 1e-9 and m["compra_entera"] == "3x5 + 3x1"
    plan, res, _ = OPT.resolver(_canasta_con_alternativa(), precios, personas=4)
    r = res[res["escenario"] == "con_tottus"].iloc[0]
    assert r["items_nucleo"] == 1 and r["items_ampliado_1"] == 2
    assert abs(r["costo_ampliado_1"] - r["costo_nucleo"] - 5.0) < 1e-9
    assert r["personas"] == 4


def test_lo_que_se_vende_pesado_se_compra_exacto():
    can = pd.DataFrame([{"id": 21, "descripcion": "pollo", "grupo": "g", "cadena": "metro",
                         "item_id": "m-1", "ean": "", "cantidad": 1.626, "paquetes": 1.626,
                         "unidad": "kg", "net_quantity": 1.0, "identidad": "granel",
                         "nivel": "nucleo", "presentacion_base": True, "a_granel": True}])
    p = pd.DataFrame([{"retailer": "metro", "item_id": "m-1", "price": 9.4,
                       "regular_price": 9.4, "on_sale": False, "available": True}])
    cel = OPT.celdas(can, p, personas=4)
    assert abs(cel.iloc[0]["costo_entero"] - 1.626 * 4 * 9.4) < 1e-9
    assert cel.iloc[0]["compra_entera"].endswith("a granel")


def test_canasta_vieja_sin_columnas_nuevas_sigue_funcionando():
    can = _canasta({1: ["metro"]})
    p = _precios({("metro", 1): {"price": 7.0}})
    plan, res, _ = OPT.resolver(can, p)
    assert plan.iloc[0]["costo"] == 7.0 and plan.iloc[0]["nivel"] == "nucleo"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
