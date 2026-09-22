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
          seller=None, brand=""):
    return {"retailer": cad, "item_id": item, "product_name": nombre, "brand": brand,
            "ean": ean, "net_quantity": nq, "unit": unit, "price": price,
            "available": available, "seller_name": seller or CT.VENDEDOR_PROPIO[cad]}


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


def test_ean_interno_no_es_identidad():
    filas = [_fila(c, f"{c}-1", "Arroz Extra Casa 5kg", "2200201985535") for c in CADENAS]
    t, e = CT.cruzar(ARROZ, R_ARROZ, _panel(filas))
    assert t.empty
    assert "EAN de fabricante" in e.iloc[0]["motivo"]


def test_packs_y_terceros_no_cuentan():
    ean = "7755139246890"
    filas = [_fila(c, f"{c}-1", "Arroz Extra Costeno 5kg", ean) for c in CADENAS[:3]]
    filas.append(_fila("vivanda", "v-1", "Pack Arroz Extra Costeno 5kg x 2un", ean))
    filas.append(_fila("tottus", "t-1", "Arroz Extra Costeno 5kg", ean, seller="Otro SAC"))
    t, e = CT.cruzar(ARROZ, R_ARROZ, _panel(filas))
    assert t.empty
    assert "esta en 3" in e.iloc[0]["motivo"]


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
    r = _reglas({"id": 79, "tipo": "granel", "incluye": r"^papa blanca\b", "excluye": "pure"})
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
    assert set(r["tipo"]) <= {"envasado", "granel", "excluido"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
