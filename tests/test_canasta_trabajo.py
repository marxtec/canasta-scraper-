#!/usr/bin/env python3
"""Tests del cruce canasta oficial x panel (analisis/canasta_trabajo.py).

    pytest tests/test_canasta_trabajo.py

Filas sinteticas, sin red y sin leer data/daily/.
"""

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest

from analisis import canasta_trabajo as CT

CADENAS = ["metro", "wong", "plaza_vea", "vivanda", "tottus"]
FECHAS = pd.date_range("2026-10-01", periods=5, freq="D")


def _fila(cad, item, nombre, ean, nq=5.0, unit="kg", price=10.0, available=True,
          seller=None, brand="", **extra):
    return {"retailer": cad, "item_id": item, "product_name": nombre, "brand": brand,
            "ean": ean, "net_quantity": nq, "unit": unit, "price": price,
            "available": available, "seller_name": seller or CT.VENDEDOR_PROPIO[cad], **extra}


def _panel(filas, fechas=FECHAS):
    return pd.concat([pd.DataFrame(filas).assign(fecha=f) for f in fechas], ignore_index=True)


def _oficial(*items):
    return pd.DataFrame([{"id": i, "descripcion": d, "grupo": "g", "unidad": "g_per_capita_dia",
                          "cantidad": q} for i, d, q in items])


def _reglas(*reglas):
    base = {"tipo": "envasado", "unidad": "kg", "incluye": "", "excluye": "", "marca": "",
            "preferencia": "", "gramaje_ref": np.nan, "g_por_unidad_base": 1000.0, "motivo": ""}
    return pd.DataFrame([{**base, **r} for r in reglas])


ARROZ = _oficial((5, "ARROZ CORRIENTE", 147.4))
R_ARROZ = _reglas({"id": 5, "incluye": r"^arroz extra\b", "excluye": "integral"})


def test_envasado_une_por_ean_aunque_el_nombre_difiera():
    filas = [_fila(c, f"{c}-1", "Arroz Extra Costeno Bolsa 5kg", "7755139246890") for c in CADENAS[:4]]
    filas.append(_fila("tottus", "t-1", "Costeno Bolsa 5 Kg", "7755139246890"))
    t, e = CT.cruzar(ARROZ, R_ARROZ, _panel(filas))
    assert sorted(t["cadena"]) == sorted(CADENAS)
    assert set(t["ean"]) == {"7755139246890"} and set(t["regla"]) == {"ean"}
    assert abs(t["cantidad"].iloc[0] - 147.4 * 30 / 1000) < 1e-9
    assert abs(t["paquetes"].iloc[0] - 147.4 * 30 / 1000 / 5.0) < 1e-9
    assert e.empty


def test_ean_interno_no_es_identidad_entra_por_kilo():
    """Un codigo interno de la cadena no une cadenas por EAN: el item entra
    como por_kilo (nivel ampliado_1), sin inventar un EAN."""
    filas = [_fila(c, f"{c}-1", "Arroz Extra Casa 5kg", "2200201985535") for c in CADENAS]
    t, e = CT.cruzar(ARROZ, R_ARROZ, _panel(filas))
    assert e.empty
    assert set(t["identidad"]) == {"por_kilo"} and set(t["nivel"]) == {"ampliado_1"}
    assert (t["ean"] == "").all()


def test_combos_y_terceros_no_cuentan():
    ean = "7755139246890"
    filas = [_fila(c, f"{c}-1", "Arroz Extra Costeno 5kg", ean) for c in CADENAS[:3]]
    filas.append(_fila("vivanda", "v-1", "Arroz Extra Costeno 5kg + Aceite Primor 1L", ean))
    filas.append(_fila("tottus", "t-1", "Arroz Extra Costeno 5kg", ean, seller="Otro SAC"))
    t, e = CT.cruzar(ARROZ, R_ARROZ, _panel(filas))
    assert t.empty
    assert "mismo EAN en 3" in e.iloc[0]["motivo"]


def test_multipack_de_un_solo_ean_si_cuenta():
    """"Paquete 6un" con un solo EAN es la presentacion normal, no un combo."""
    of = _oficial((2, "GALLETA DE SODA", 2.3))
    r = _reglas({"id": 2, "incluye": "soda", "excluye": ""})
    ean = "7622201714819"
    filas = [_fila(c, f"{c}-1", "Galletas Soda Field Paquete 6un 32g", ean, nq=0.192)
             for c in CADENAS]
    t, e = CT.cruzar(of, r, _panel(filas))
    assert e.empty and set(t["identidad"]) == {"ean"} and len(t) == 5


def test_item_en_tres_cadenas_queda_fuera_con_motivo():
    filas = [_fila(c, f"{c}-1", "Arroz Extra Costeno 5kg", "7755139246890") for c in CADENAS[:3]]
    t, e = CT.cruzar(ARROZ, R_ARROZ, _panel(filas))
    assert t.empty and e.iloc[0]["id"] == 5
    assert "4 de 5" in e.iloc[0]["motivo"]


def test_agotado_resta_presencia():
    """Un SKU que la cadena lista con available=False casi toda la ventana no
    cuenta como presente en esa cadena."""
    ean = "7755139246890"
    filas = [_fila(c, f"{c}-1", "Arroz Extra Costeno 5kg", ean) for c in CADENAS[:3]]
    filas.append(_fila("vivanda", "v-1", "Arroz Extra Costeno 5kg", ean, available=False))
    t, _ = CT.cruzar(ARROZ, R_ARROZ, _panel(filas))
    assert t.empty


def test_los_precios_no_deciden_el_emparejamiento():
    """Mismo panel con precios permutados: misma canasta de trabajo."""
    filas = []
    for c in CADENAS:
        filas.append(_fila(c, f"{c}-a", "Arroz Extra Costeno 5kg", "7755139246890", price=20.0))
        filas.append(_fila(c, f"{c}-b", "Arroz Extra Faraon 5kg", "7754294000095", price=15.0))
    p1 = _panel(filas)
    p2 = p1.copy()
    p2["price"] = np.random.default_rng(0).permutation(p2["price"].to_numpy()) * 3
    t1, _ = CT.cruzar(ARROZ, R_ARROZ, p1)
    t2, _ = CT.cruzar(ARROZ, R_ARROZ, p2)
    pd.testing.assert_frame_equal(t1, t2)
    assert set(t1["ean"]) == {"7754294000095"}      # empate: el menor EAN, no el mas barato


def test_ean_con_descripcion_incoherente_se_descarta():
    """El EAN que una cadena nombra con una palabra excluida no se usa,
    aunque sea el que mas cadenas tienen."""
    malo, bueno = "7750000000001", "7750000000002"
    filas = [_fila(c, f"{c}-m", "Arroz Extra Costeno 5kg", malo) for c in CADENAS]
    filas[0]["product_name"] = "Arroz Extra Integral Costeno 5kg"
    filas += [_fila(c, f"{c}-b", "Arroz Extra Paisana 5kg", bueno) for c in CADENAS[:4]]
    t, _ = CT.cruzar(ARROZ, R_ARROZ, _panel(filas))
    assert set(t["ean"]) == {bueno}


def test_granel_un_representante_por_cadena_sin_inventar_ean():
    of = _oficial((79, "PAPA BLANCA", 103.1))
    r = _reglas({"id": 79, "tipo": "granel", "incluye": r"^papa blanca\b", "excluye": "pure",
                 "variedad_pref": "."})
    filas = []
    for i, c in enumerate(CADENAS):
        filas.append(_fila(c, f"{c}-1", "Papa Blanca Yungay Seleccionada x kg", f"25{i}0000000001", nq=1.0))
        filas.append(_fila(c, f"{c}-2", "Papa Blanca x kg", str(1000 + i), nq=1.0))
    t, _ = CT.cruzar(of, r, _panel(filas))
    assert len(t) == 5 and set(t["regla"]) == {"granel"}
    assert set(t["item_id"]) == {f"{c}-2" for c in CADENAS}     # el nombre mas corto
    assert (t["ean"] == "").all()                                # ni el interno ni el corto
    assert abs(t["paquetes"].iloc[0] - 103.1 * 30 / 1000) < 1e-9


def test_excluido_por_regla_documenta_el_motivo():
    of = _oficial((107, "ALMUERZO RESTAURANTE", 48.4))
    r = _reglas({"id": 107, "tipo": "excluido", "motivo": "consumo fuera del hogar"})
    t, e = CT.cruzar(of, r, _panel([_fila("metro", "m", "x", "1")]))
    assert t.empty and e.iloc[0]["motivo"] == "consumo fuera del hogar"


def test_item_oficial_sin_regla_es_error():
    with pytest.raises(ValueError):
        CT.cruzar(_oficial((5, "ARROZ", 1.0), (6, "AVENA", 1.0)), R_ARROZ,
                  _panel([_fila("metro", "m", "x", "1")]))


def test_items_fuera_del_dominio_lima_no_entran():
    of = _oficial((5, "ARROZ CORRIENTE", 147.4), (9, "MAIZ MORADO", np.nan))
    filas = [_fila(c, f"{c}-1", "Arroz Extra Costeno 5kg", "7755139246890") for c in CADENAS]
    t, e = CT.cruzar(of, R_ARROZ, _panel(filas))       # 9 no tiene regla y no hace falta
    assert set(t["id"]) == {5} and e.empty


def test_ventana_y_congelamiento():
    filas = [_fila(c, f"{c}-1", "Arroz Extra Costeno 5kg", "7755139246890") for c in CADENAS]
    corto = _panel(filas, FECHAS[:3])
    t, _ = CT.cruzar(ARROZ, R_ARROZ, corto, ventana=4)
    assert set(t["estado"]) == {"provisional"}
    largo = _panel(filas, pd.date_range("2026-10-01", periods=6, freq="D"))
    t, _ = CT.cruzar(ARROZ, R_ARROZ, largo, ventana=4)
    assert set(t["estado"]) == {"congelada"}
    assert str(t["ventana_fin"].iloc[0]) == "2026-10-04"          # solo la ventana decide
    tmp = pathlib.Path(tempfile.mkdtemp()) / "ct.csv"
    t.to_csv(tmp, index=False)
    assert CT.congelada(tmp)
    with pytest.raises(SystemExit):
        CT.correr(panel_df=largo, salida=tmp, exclusiones=tmp.with_name("ex.csv"))


def test_archivos_reales_son_coherentes():
    """La canasta oficial transcrita y las reglas: cada item de Lima tiene
    regla y la suma cuadra con el total impreso por el INEI (1370,2 g)."""
    of = CT.cargar_oficial()
    r = CT.cargar_reglas()
    lima = of[of["cantidad"].notna()]
    assert len(of) == 110 and len(lima) == 68
    assert abs(lima["cantidad"].sum() - 1370.2) < 0.5
    assert set(lima["id"]) <= set(r["id"])
    assert set(r["tipo"]) <= {"envasado", "granel", "excluido", "por_unidad"}
    pu = r[r["tipo"] == "por_unidad"]
    assert (pu["g_unidad"] > 0).all() and pu["fuente_g_unidad"].str.contains("CENAN").all()
    fuera = r[r["id"].isin([104, 106, 107, 108, 109, 110])]
    assert set(fuera["tipo"]) == {"excluido"}
    eq = CT.cargar_equivalencias()
    assert set(eq.columns) >= {"ean_a", "ean_b", "motivo"} and (eq["motivo"] != "").all()


# ---------------------------------------------------------------------------
# Identidades (docs: Metodologia de estandarizacion, secciones 6 a 8)
# ---------------------------------------------------------------------------

def _eq(*pares):
    return pd.DataFrame([{"ean_a": a, "ean_b": b, "motivo": "test"} for a, b in pares])


def test_ean_equivalente_por_tabla():
    """Dos codigos del mismo paquete: con la tabla cuentan como uno."""
    a, b = "8719200231252", "7752285031868"
    filas = [_fila(c, f"{c}-1", "Margarina Dorina Clasica 220g", a, nq=0.22) for c in CADENAS[1:]]
    filas.append(_fila("metro", "m-1", "Margarina Dorina Clasica 220g", b, nq=0.22))
    of = _oficial((36, "MARGARINA", 2.4))
    r = _reglas({"id": 36, "incluye": "^margarina"})
    t, _ = CT.cruzar(of, r, _panel(filas), equivalencias=_eq((a, b)))
    assert len(t) == 5
    assert t.set_index("cadena")["identidad"].to_dict() == {
        "metro": "ean_equivalente", "wong": "ean", "plaza_vea": "ean", "vivanda": "ean",
        "tottus": "ean"}
    assert set(t["nivel"]) == {"nucleo"}
    t2, _ = CT.cruzar(of, r, _panel(filas))                       # sin la tabla
    assert "metro" not in set(t2.loc[t2["identidad"] != "por_kilo", "cadena"])


def test_ean_vacio_misma_marca_gramaje_y_nombre_es_equivalente():
    ean = "7750885007108"
    filas = [_fila(c, f"{c}-1", "Semola Molitalia Bolsa 200 g", ean, nq=0.2, brand="Molitalia")
             for c in ["metro", "wong", "tottus"]]
    filas.append(_fila("plaza_vea", "p-1", "Semola MOLITALIA Bolsa 200g", "", nq=0.2,
                       brand="MOLITALIA"))
    filas.append(_fila("vivanda", "v-1", "Semola MOLITALIA Bolsa 250g", "", nq=0.25,
                       brand="MOLITALIA"))                      # otro gramaje: no
    of = _oficial((13, "SEMOLA", 5.0))
    r = _reglas({"id": 13, "incluye": "^semola"})
    t, _ = CT.cruzar(of, r, _panel(filas))
    ident = t[t["presentacion_base"]].set_index("cadena")["identidad"].to_dict()
    assert ident["plaza_vea"] == "ean_equivalente"
    assert ident.get("vivanda") != "ean_equivalente"
    assert "sin_ean_fabricante" in t.set_index("cadena").loc["plaza_vea", "marcas"]


def test_ean_vacio_de_otra_marca_no_es_equivalente():
    ean = "7750885007108"
    filas = [_fila(c, f"{c}-1", "Semola Molitalia Bolsa 200 g", ean, nq=0.2, brand="Molitalia")
             for c in CADENAS[:3]]
    filas.append(_fila("vivanda", "v-1", "Semola Bells Bolsa 200g", "", nq=0.2, brand="BELL'S"))
    t, _ = CT.cruzar(_oficial((13, "SEMOLA", 5.0)), _reglas({"id": 13, "incluye": "^semola"}),
                     _panel(filas))
    assert t.set_index("cadena").loc["vivanda", "identidad"] == "por_kilo"


def test_gramaje_del_ean_vale_para_todas_las_cadenas():
    """Metro y Wong publican 32g x 6; las demas solo "6un". El peso es del EAN."""
    ean = "7622201714819"
    filas = [_fila(c, f"{c}-1", "Sixpack Galletas de Soda Field 32g", ean, nq=0.192)
             for c in ["metro", "wong"]]
    filas += [_fila(c, f"{c}-1", "Galletas Soda FIELD Bolsa 6un", ean, nq=6.0, unit="un")
              for c in ["plaza_vea", "vivanda", "tottus"]]
    of = _oficial((2, "GALLETA DE SODA", 2.3))
    t, _ = CT.cruzar(of, _reglas({"id": 2, "incluye": "soda"}), _panel(filas))
    assert len(t) == 5 and set(t["net_quantity"]) == {0.192}
    assert set(t["identidad"]) == {"ean"}


def test_contenido_declarado_da_el_gramaje():
    ean = "7750106182607"
    filas = [_fila(c, f"{c}-1", "Galletas de Soda San Jorge Pack 7un", ean, nq=7.0, unit="un")
             for c in CADENAS]
    filas[0]["contenido_neto_declarado"] = "280g"
    t, _ = CT.cruzar(_oficial((2, "GALLETA DE SODA", 2.3)), _reglas({"id": 2, "incluye": "soda"}),
                     _panel(filas))
    assert len(t) == 5 and set(t["net_quantity"]) == {0.28}


R_POLLO = _reglas({"id": 21, "tipo": "granel", "incluye": "^pollo entero",
                   "variedad_pref": "con menudencia"})
POLLO = _oficial((21, "POLLO EVISCERADO", 54.2))


def test_granel_gana_la_variedad_fijada_aunque_sea_mas_cara():
    filas = []
    for c in CADENAS[:4]:
        filas.append(_fila(c, f"{c}-con", "Pollo Entero con Menudencia x kg", "", nq=1.0,
                           price=12.0))
        filas.append(_fila(c, f"{c}-sin", "Pollo Entero sin Menudencia x kg", "", nq=1.0,
                           price=8.0))
    filas.append(_fila("tottus", "t-sin", "Pollo Entero sin Menudencia x kg", "", nq=1.0))
    t, _ = CT.cruzar(POLLO, R_POLLO, _panel(filas))
    assert set(t["item_id"]) == {f"{c}-con" for c in CADENAS[:4]}
    assert set(t["identidad"]) == {"granel"} and set(t["nivel"]) == {"nucleo"}
    assert "tottus" not in set(t["cadena"])          # la barra se cumple sin mezclar


def test_granel_completa_con_otra_variedad_marcada():
    filas = [_fila(c, f"{c}-con", "Pollo Entero con Menudencia x kg", "", nq=1.0)
             for c in CADENAS[:3]]
    filas += [_fila(c, f"{c}-sin", "Pollo Entero sin Menudencia x kg", "", nq=1.0)
              for c in CADENAS[3:]]
    t, _ = CT.cruzar(POLLO, R_POLLO, _panel(filas))
    assert len(t) == 5 and set(t["nivel"]) == {"ampliado_1"}
    pk = t[t["identidad"] == "por_kilo"]
    assert set(pk["cadena"]) == set(CADENAS[3:])
    assert pk["marcas"].str.contains("variedad_distinta").all()


def test_peso_variable_de_vtex_es_granel():
    """Sin "x kg" en el nombre pero con unitMultiplier != 1: precio por kg."""
    filas = [_fila(c, f"{c}-1", "Pollo Entero con Menudencia x kg", "", nq=1.0)
             for c in CADENAS[:3]]
    filas.append(_fila("vivanda", "v-1", "Pollo Entero Fresco con Menudencia", "", nq=np.nan,
                       unit=None, unit_multiplier=2.2))
    t, _ = CT.cruzar(POLLO, R_POLLO, _panel(filas))
    assert t.set_index("cadena").loc["vivanda", "identidad"] == "granel"


def test_granel_sin_variedad_fijada_es_por_kilo():
    of = _oficial((62, "MANDARINA", 30.0))
    r = _reglas({"id": 62, "tipo": "granel", "incluye": "^mandarina"})
    filas = [_fila(c, f"{c}-1", "Mandarina Costa x kg", "", nq=1.0) for c in CADENAS]
    t, _ = CT.cruzar(of, r, _panel(filas))
    assert set(t["identidad"]) == {"por_kilo"}
    assert t["marcas"].str.contains("variedad_no_fijada").all()


def _panel_por_kilo(precios):
    filas = [_fila(c, f"{c}-1", "Galleta Soda Costa 170g", "7750885019583", nq=0.17,
                   brand="COSTA", price=precios[0]) for c in CADENAS[:2]]
    for k, c in enumerate(CADENAS[2:]):
        filas.append(_fila(c, f"{c}-a", "Galleta Soda Casa 160g", f"22000000000{k}", nq=0.16,
                           brand="CASA", price=precios[1]))
        filas.append(_fila(c, f"{c}-b", "Galleta Soda Casa 400g", f"22000000001{k}", nq=0.40,
                           brand="CASA", price=precios[2]))
    return _panel(filas)


def test_por_kilo_respeta_r_max_y_no_mira_precios():
    of = _oficial((2, "GALLETA DE SODA", 2.3))
    r = _reglas({"id": 2, "incluye": "soda"})
    t1, _ = CT.cruzar(of, r, _panel_por_kilo([5.0, 3.0, 1.0]))
    t2, _ = CT.cruzar(of, r, _panel_por_kilo([1.0, 9.0, 20.0]))
    pd.testing.assert_frame_equal(t1, t2)
    pk = t1[(t1["identidad"] == "por_kilo") & t1["presentacion_base"]]
    assert set(pk["item_id"]) == {f"{c}-a" for c in CADENAS[2:]}    # 160 g, no 400 g
    assert set(t1["nivel"]) == {"ampliado_1"}


R_HUEVO = _reglas({"id": 34, "tipo": "por_unidad", "incluye": "^huevos?", "g_unidad": 68.4})
HUEVO = _oficial((34, "HUEVOS", 21.5))


def test_por_unidad_convierte_con_el_peso_cenan():
    filas = [_fila(c, f"{c}-30", "Huevos Pardos La Calera Bandeja 30un", "7754470000024",
                   nq=30.0, unit="un", brand="LA CALERA") for c in CADENAS]
    t, _ = CT.cruzar(HUEVO, R_HUEVO, _panel(filas))
    assert len(t) == 5 and set(t["identidad"]) == {"por_unidad"}
    assert np.allclose(t["net_quantity"], 30 * 68.4 / 1000)
    assert set(t["nivel"]) == {"ampliado_2"}


def test_por_unidad_suelta_y_sin_peso_de_referencia():
    of = _oficial((47, "CHOCLO", 12.0))
    filas = [_fila(c, f"{c}-1", "Choclo Serrano x unid", "", nq=np.nan, unit=None)
             for c in CADENAS]
    r = _reglas({"id": 47, "tipo": "por_unidad", "incluye": "^choclo", "g_unidad": 255.9})
    t, _ = CT.cruzar(of, r, _panel(filas))
    assert np.allclose(t["net_quantity"], 0.2559)
    r_sin = _reglas({"id": 47, "tipo": "por_unidad", "incluye": "^choclo"})
    t, e = CT.cruzar(of, r_sin, _panel(filas))
    assert t.empty and "g_unidad" in e.iloc[0]["motivo"]


def test_alternativas_de_tamano_para_el_costo_entero():
    filas = []
    for c in CADENAS:
        filas.append(_fila(c, f"{c}-5", "Arroz Extra Paisana Bolsa 5 kg", "7755139002809",
                           nq=5.0, brand="Paisana"))
        filas.append(_fila(c, f"{c}-1", "Arroz Extra Paisana Bolsa 1 kg", "7755139002793",
                           nq=1.0, brand="Paisana"))
    t, _ = CT.cruzar(ARROZ, _reglas({"id": 5, "incluye": r"^arroz extra\b", "gramaje_ref": 5}),
                     _panel(filas))
    base = t[t["presentacion_base"]]
    alt = t[~t["presentacion_base"]]
    assert set(base["net_quantity"]) == {5.0} and set(alt["net_quantity"]) == {1.0}
    assert len(alt) == 5 and alt["marcas"].str.contains("alternativa").all()


def test_nombre_con_dos_gramajes_se_aparta():
    ean = "7754487002929"
    filas = [_fila(c, f"{c}-1", "Sazonador Ajinomoto Sobre 90g", ean, nq=0.09) for c in CADENAS[:4]]
    filas.append(_fila("tottus", "t-1", "Sazonador Ajinomoto 90g Sobre 100 g", ean, nq=0.1))
    t, _ = CT.cruzar(_oficial((94, "AJI NO MOTO", 1.0)),
                     _reglas({"id": 94, "incluye": "ajinomoto"}), _panel(filas))
    assert "tottus" not in set(t["cadena"])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
