#!/usr/bin/env python3
"""Tests del pipeline. Corren sin red y sin dependencias extra.

    python3 tests/test_scraper.py      # no necesita pytest
    pytest tests/                      # tambien funciona

Se cubre sobre todo `parse_quantity`, porque es la parte mas fragil y la que
falla de la peor manera: no devolviendo nada, sino devolviendo un numero
plausible y equivocado que despues divide el precio. Cada caso marcado como
REGRESION es un bug que ya ocurrio en produccion.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from canasta.catalyst import _offer_prices, _price_number
from canasta.eans import load_cache, save_cache
from canasta.normalize import _best_seller, normalize, parse_quantity
from canasta.vtex import matches_food


def test_parse_quantity_simple():
    assert parse_quantity("Arroz Costeno Extra Bolsa 1kg") == (1.0, "kg")
    assert parse_quantity("Gaseosa Coca Cola 3 L") == (3.0, "l")
    assert parse_quantity("Detergente Bolivar 800 gr") == (0.8, "kg")
    assert parse_quantity("Gaseosa 1.5 litros") == (1.5, "l")
    assert parse_quantity("Leche 400 gramos") == (0.4, "kg")


def test_parse_quantity_granel():
    """El precio que publica VTEX ya ES por kilo: la cantidad neta es 1."""
    assert parse_quantity("Pollo Entero x kg") == (1.0, "kg")
    assert parse_quantity("Cebolla Roja Metro x Kg") == (1.0, "kg")


def test_parse_quantity_multipack():
    assert parse_quantity("Agua San Luis 350ml Paquete 24un") == (8.4, "l")
    assert parse_quantity("Leche Gloria 400g Pack x 6") == (2.4, "kg")
    assert parse_quantity("Jamon Ingles 200 g x 2") == (0.4, "kg")
    assert parse_quantity("Yogurt Gloria 1 L Six Pack") == (6.0, "l")
    assert parse_quantity("Cerveza Pilsen 310ml Sixpack") == (1.86, "l")
    assert parse_quantity("Atun 170g 6 Unidades") == (1.02, "kg")


def test_parse_quantity_conteo_sin_peso():
    assert parse_quantity("Huevos Pardos Paquete de 15") == (15.0, "un")
    assert parse_quantity("Galletas Costa Caja 12un") == (12.0, "un")
    assert parse_quantity("Papel Higienico 30un") == (30.0, "un")
    assert parse_quantity("Huevos Docena") == (12.0, "un")
    assert parse_quantity("Pack Trozos de Atun 6 Unidades") == (6.0, "un")


def test_parse_quantity_contenido_extra():
    """"900ml + 100ml" son 1.0 L en total, no 900ml ni 100ml."""
    assert parse_quantity("Aceite Primor 900ml + 100ml") == (1.0, "l")


def test_REGRESION_gramaje_no_es_conteo_de_envases():
    """El bug que inflaba el gramaje: "Caja 490 g" son 490 GRAMOS en una caja,
    no 490 cajas. Daba 240 kg de paella y contaminaba el precio por kilo.
    Afectaba al 8% de las filas de Tottus."""
    assert parse_quantity("Kit Paella de Mariscos Carmencita Caja 490 g") == (0.49, "kg")
    assert parse_quantity("Risotto Inverni 4 Quesos Caja 175 g") == (0.175, "kg")
    assert parse_quantity("Higos Enteros Siembra Peru Bandeja 500 gr") == (0.5, "kg")
    assert parse_quantity("Queso Provolone AURICCHIO Caja 150g") == (0.15, "kg")


def test_parse_quantity_sin_dato():
    assert parse_quantity("Pan Frances") == (None, None)
    assert parse_quantity("") == (None, None)
    assert parse_quantity(None) == (None, None)


def test_techo_de_cordura():
    """Por encima de 50 kg/l se declara no parseado: un dato ausente se
    recupera, uno falso contamina el indice en silencio."""
    qty, _ = parse_quantity("Arroz Gigante 900 kg x 90")
    assert qty is None


def test_best_seller_prefiere_el_propio():
    """Si no, el precio puede ser de un tercero del marketplace y la
    comparacion entre cadenas deja de medir lo que dice medir."""
    item = {"sellers": [
        {"sellerId": "9", "sellerName": "Tercero",
         "commertialOffer": {"Price": 99.0}},
        {"sellerId": "1", "sellerName": "Plaza Vea", "sellerDefault": True,
         "commertialOffer": {"Price": 10.0}},
    ]}
    offer, seller = _best_seller(item)
    assert offer["Price"] == 10.0
    assert seller["sellerName"] == "Plaza Vea"


def test_best_seller_cae_al_primero_si_no_hay_default():
    item = {"sellers": [
        {"sellerId": "9", "sellerName": "Tercero",
         "commertialOffer": {"Price": 99.0}},
    ]}
    offer, seller = _best_seller(item)
    assert offer["Price"] == 99.0
    assert seller["sellerName"] == "Tercero"
    assert _best_seller({"sellers": []}) == ({}, {})


def test_normalize_fila_completa():
    productos = [{
        "productId": "1", "brand": "COSTENO", "link": "http://x/p",
        "_category_path": "Abarrotes > Arroz",
        "items": [{
            "itemId": "11", "nameComplete": "Arroz COSTENO Bolsa 5kg",
            "ean": "7750001",
            "sellers": [{"sellerId": "1", "sellerName": "Metro", "sellerDefault": True,
                         "commertialOffer": {"Price": 20.0, "ListPrice": 25.0,
                                             "IsAvailable": True,
                                             "AvailableQuantity": 7}}],
        }],
    }]
    fila = normalize(productos, "metro", "2026-09-19 08:00:00-0500")[0]
    assert fila["price"] == 20.0
    assert fila["regular_price"] == 25.0
    assert fila["on_sale"] is True
    assert fila["net_quantity"] == 5.0 and fila["unit"] == "kg"
    assert fila["category"] == "Abarrotes"
    assert fila["seller_name"] == "Metro"
    assert fila["card_price"] is None      # VTEX no lo expone: no se inventa


def test_normalize_descarta_item_sin_oferta():
    assert normalize([{"items": [{"itemId": "1", "sellers": []}]}], "x", "t") == []


def test_matches_food():
    kw = ["abarrote", "carne", "comida", "plato", "parrill"]
    ex = ["mascota", "limpieza"]
    assert matches_food("Abarrotes", kw, ex)
    assert matches_food("Carnes, Aves y Pescados", kw, ex)
    assert matches_food("Platos Preparados", kw, ex)
    assert matches_food("Mundo Parrillero", kw, ex)     # sufijo, no "parrilla"
    assert not matches_food("Tecnologia", kw, ex)
    assert not matches_food("Mundo Mascotas", kw, ex)   # excluida
    assert not matches_food("Comida para Mascotas", kw, ex)


def test_catalyst_precios():
    assert _price_number(["16.90"]) == 16.9
    assert _price_number("S/ 5,60") == 5.6
    assert _price_number(None) is None
    assert _price_number([]) is None
    precio, regular, _ = _offer_prices({"prices": [
        {"type": "internetPrice", "price": ["17.90"]},
        {"type": "normalPrice", "price": ["22.00"]},
    ]})
    assert precio == 17.9 and regular == 22.0


def test_cache_ean_ida_y_vuelta(tmp=None):
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp())
    p = d / "ean.json"
    save_cache({"1": "7750001", "2": None}, p)
    c = load_cache(p)
    assert c["1"] == "7750001"
    assert c["2"] is None          # memoria negativa: no reintentar cada dia
    assert load_cache(d / "noexiste.json") == {}


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
