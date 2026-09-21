#!/usr/bin/env python3
"""Tests del bloque 4 (auditoria de validez). Sin red: el parser se prueba
sobre texto de fichas fabricado con el formato real de cada cadena.

    python tests/test_auditoria_validez.py
"""

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from analisis import auditoria_validez as A
from analisis import panel as P

# Texto renderizado, tal como lo devuelve inner_text("body"), por cadena.
PLAZA_VEA = (
    "Plazavea\nSupermercado Vinos\nLA MASCOTA\nSKU: 20422209\n"
    "Vino Tinto LA MASCOTA Cabernet Franc Botella 750ml\n\nPrecio Regular\n\n"
    "S/ 94.90 x Und\n\nPrecio Online\n\nS/ 52.90 x Und\n-44%\nAgregar7+ unidades\n\n"
    "Vendido y despachado por:\n\nPlaza Vea\n\nTipo de entrega disponible:\n"
    "Llévalos juntos\nOtro producto\nS/ 27.90\n" + "x" * 400
)
METRO = (
    "CATEGORIAS\nLácteosLechesFórmula en Polvo Enfagrow Premium 1.35kg\n"
    "Fórmula en Polvo Enfagrow Premium 1.35kg\nENFAGROW\nREFERENCIA: 961847\n"
    "Tarjeta Cencosud\nS/ 8.46\n\nS/ 9.40\n＋\n－\nAGREGAR\nCaracterísticas Principales\n"
    "Podrían interesarte\nOtro\nS/ 190.90\n" + "x" * 400
)
WONG_SIN_ETIQUETA = (
    "Vino Blanco Matetic Corralillo Botella 750ml\nMATETIC\nREFERENCIA: 515988\n- 8%\n\n"
    "S/ 53.91\n\nS/ 54.90\nS/ 59.90\n＋\n－\nAGREGAR\nCaracterísticas Principales\n" + "x" * 400
)
TOTTUS = (
    "Información al 05/2026.\nMultipack Cabanossi Braedt Empaque 150 g\nMostrar Más\n"
    "Especificaciones\nContenido 150 g\nBRAEDT\nMultipack Cabanossi Braedt Empaque 150 g\n"
    "S/ 14.90\nUN\nAgregar al Carro\nProductos similares\nOTTO\nS/ 15.40\n" + "x" * 400
)
LD_TOTTUS = ('<html><script type="application/ld+json">{"@context":"https://schema.org",'
             '"@type":"Product","offers":[{"price":"14.9","seller":{"@type":"Organization",'
             '"name":"Tottus"}}],"name":"x"}</script></html>')
LD_VTEX = ('<script type="application/ld+json">{"@type":"Product","offers":{"@type":'
           '"AggregateOffer","lowPrice":9.4,"offers":[{"@type":"Offer","price":9.4,'
           '"seller":{"@type":"Organization","name":"CENCOSUD RETAIL PERU S.A."}}]}}</script>')


def _fila(**kw):
    base = {"retailer": "metro", "price": 9.4, "regular_price": 9.4, "card_price": np.nan}
    base.update(kw)
    return base


def test_plaza_vea_precio_online_y_vendedor():
    lec = A.leer_ficha(PLAZA_VEA, "", "Vino Tinto LA MASCOTA Cabernet Franc Botella 750ml")
    assert lec["estado"] == "ok"
    assert lec["precios_visibles"] == "94.90;52.90"      # no entra el 27.90 del carrusel
    assert lec["precio_pantalla"] == 52.90
    assert lec["vendedor_pantalla"] == "Plaza Vea"
    comp = A.comparar(_fila(retailer="plaza_vea", price=52.9, regular_price=94.9), lec)
    assert comp["concuerda_price"] is True
    assert comp["regular_visible"] is True
    assert comp["vendedor_es_cadena"] is True
    assert comp["magnitud_discrepancia"] is None


def test_metro_tarjeta_etiquetada():
    lec = A.leer_ficha(METRO, LD_VTEX, "Fórmula en Polvo Enfagrow Premium 1.35kg")
    assert lec["precio_tarjeta_pantalla"] == 8.46
    assert lec["precio_pantalla"] == 9.40                 # el de tarjeta no cuenta
    assert lec["precio_ldjson"] == 9.4
    assert lec["vendedor_ldjson"].startswith("CENCOSUD")
    comp = A.comparar(_fila(price=9.4, card_price=8.46), lec)
    assert comp["concuerda_price"] is True and comp["card_visible"] is True
    assert comp["vendedor_es_cadena"] is True


def test_wong_precio_menor_sin_etiqueta():
    """Wong pinta el precio con tarjeta sin texto. price SI esta en pantalla,
    asi que concuerda; el menor se registra y, sin card_price en el CSV, se
    anota que la cobertura de card_price esta subestimada."""
    lec = A.leer_ficha(WONG_SIN_ETIQUETA, "", "Vino Blanco Matetic Corralillo Botella 750ml")
    comp = A.comparar(_fila(retailer="wong", price=54.9, regular_price=59.9), lec)
    assert comp["concuerda_price"] is True
    assert comp["regular_visible"] is True
    assert comp["precio_menor_visible"] == 53.91
    assert "sin card_price" in lec["nota"]
    # con card_price en el CSV, se contrasta
    lec2 = A.leer_ficha(WONG_SIN_ETIQUETA, "", "Vino Blanco Matetic Corralillo Botella 750ml")
    comp2 = A.comparar(_fila(retailer="wong", price=54.9, regular_price=59.9, card_price=53.91), lec2)
    assert comp2["card_visible"] is True


def test_tottus_salta_la_descripcion_y_lee_ldjson():
    lec = A.leer_ficha(TOTTUS, LD_TOTTUS, "Multipack Cabanossi Braedt Empaque 150 g")
    assert lec["precio_pantalla"] == 14.90
    assert lec["precio_ldjson"] == 14.9 and lec["vendedor_ldjson"] == "Tottus"
    comp = A.comparar(_fila(retailer="tottus", price=12.9, regular_price=14.9), lec)
    assert comp["concuerda_price"] is False
    assert abs(comp["magnitud_discrepancia"] - (14.9 / 12.9 - 1)) < 1e-9


def test_bloqueado_y_sin_producto():
    assert A.leer_ficha("", "", "x")["estado"] == "bloqueado"
    assert A.leer_ficha("Access Denied " + "x" * 400, "", "x")["estado"] == "bloqueado"
    lec = A.leer_ficha("pagina normal sin el producto " * 30, "", "Producto Inexistente")
    assert lec["estado"] == "sin_producto"
    comp = A.comparar(_fila(), lec)
    assert comp["concuerda_price"] is None


def test_vendedor_tercero():
    texto = PLAZA_VEA.replace("Vendido y despachado por:\n\nPlaza Vea", "Vendido y despachado por:\n\nNUA PERÚ")
    lec = A.leer_ficha(texto, "", "Vino Tinto LA MASCOTA Cabernet Franc Botella 750ml")
    comp = A.comparar(_fila(retailer="plaza_vea", price=52.9), lec)
    assert comp["vendedor_es_cadena"] is False


def test_url_publica_vivanda():
    assert A.url_publica("https://vivanda.vtexcommercestable.com.br/x/p") == "https://www.vivanda.com.pe/x/p"
    assert A.url_publica("https://www.metro.pe/x/p") == "https://www.metro.pe/x/p"


def _panel_sintetico(n=200):
    rng = np.random.default_rng(0)
    df = pd.DataFrame({
        "retailer": rng.choice(["metro", "wong"], n),
        "item_id": [str(i) for i in range(n)],
        "product_name": "p", "url": "https://x/p", "price": 1.0,
        "on_sale": rng.random(n) < 0.3,
        "categoria_comun": rng.choice(["abarrotes", "lacteos", "bebidas"], n),
    })
    return df


def test_muestra_reproducible_y_estratificada():
    df = _panel_sintetico()
    m1 = A.muestra(df, n=20, semilla=7)
    m2 = A.muestra(df, n=20, semilla=7)
    assert m1["item_id"].tolist() == m2["item_id"].tolist()
    assert (m1.groupby("retailer").size() == 20).all()
    assert (m1["semilla"] == 7).all()
    # todos los estratos con al menos 1 y proporcion de on_sale parecida
    for cad, g in m1.groupby("retailer"):
        estratos = df[df["retailer"] == cad].groupby(["categoria_comun", "on_sale"]).size()
        assert g.groupby(["categoria_comun", "on_sale"]).size().reindex(estratos.index).fillna(0).min() >= 1
    m3 = A.muestra(df, n=20, semilla=8)
    assert m3["item_id"].tolist() != m1["item_id"].tolist()
    # pedir mas de lo que hay devuelve todo
    assert len(A.muestra(df, n=10_000, semilla=1)) == len(df)


def test_acumular_es_idempotente():
    d = pathlib.Path(tempfile.mkdtemp()) / "acum.csv"
    fila = {c: None for c in A.COLUMNAS}
    a = pd.DataFrame([{**fila, "fecha_auditoria": "2026-09-21", "retailer": "metro", "item_id": "1"}])
    b = pd.DataFrame([{**fila, "fecha_auditoria": "2026-09-28", "retailer": "metro", "item_id": "2"}])
    A.acumular(a, d)
    A.acumular(a, d)                        # misma fecha: reemplaza, no duplica
    assert len(pd.read_csv(d)) == 1
    todo = A.acumular(b, d)
    assert len(todo) == 2 and set(todo["fecha_auditoria"]) == {"2026-09-21", "2026-09-28"}


def test_resumen_separa_mismo_dia():
    fila = {c: None for c in A.COLUMNAS}
    df = pd.DataFrame([
        {**fila, "retailer": "metro", "estado": "ok", "mismo_dia": True, "concuerda_price": True},
        {**fila, "retailer": "metro", "estado": "ok", "mismo_dia": True, "concuerda_price": False,
         "magnitud_discrepancia": 0.1},
        {**fila, "retailer": "metro", "estado": "ok", "mismo_dia": False, "concuerda_price": False},
        {**fila, "retailer": "tottus", "estado": "bloqueado", "mismo_dia": True},
    ])
    r = A.resumen(df).set_index("cadena")
    assert r.loc["metro", "evaluables"] == 2
    assert abs(r.loc["metro", "concordancia_exacta"] - 0.5) < 1e-9
    assert r.loc["metro", "distinto_dia"] == 1
    assert r.loc["tottus", "bloqueadas"] == 1
    assert r.loc["TOTAL", "fichas"] == 4


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
