"""Convierte el JSON crudo de VTEX a filas planas.

Una fila = un producto, en un super, un dia. El esquema sigue la slide 6 de
la propuesta.

Nota sobre gramaje: VTEX casi siempre reporta measurementUnit="un" y
unitMultiplier=1, es decir "una unidad", que no sirve para precio por kg. El
peso real vive en el nombre ("Arroz Costeno Extra Bolsa 1kg"), asi que hay
que parsearlo de ahi. Es la parte mas fragil del pipeline: revisar la tasa de
exito con `python3 collect.py --audit`.
"""

import json
import re

SCRAPER_VERSION = "1.0"

COLUMNS = [
    "timestamp", "retailer", "product_id", "item_id", "product_name", "brand",
    "category", "category_path", "net_quantity", "unit", "price",
    "regular_price", "card_price", "on_sale", "available", "available_qty",
    "ean", "url", "teasers", "scraper_version",
]

# "1kg", "750 g", "1.5 L", "x 500gr", "500ml", "30un"
_QTY = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(kg|kilo|kilos|g|gr|gra|gramos|l|lt|lts|litro|litros|ml|cc|un|und|unid)\b",
    re.IGNORECASE,
)

# a kg o litro. Se deja 'un' aparte: no es peso.
_TO_BASE = {
    "kg": ("kg", 1.0), "kilo": ("kg", 1.0), "kilos": ("kg", 1.0),
    "g": ("kg", 0.001), "gr": ("kg", 0.001), "gra": ("kg", 0.001),
    "gramos": ("kg", 0.001),
    "l": ("l", 1.0), "lt": ("l", 1.0), "lts": ("l", 1.0),
    "litro": ("l", 1.0), "litros": ("l", 1.0),
    "ml": ("l", 0.001), "cc": ("l", 0.001),
    "un": ("un", 1.0), "und": ("un", 1.0), "unid": ("un", 1.0),
}


# Granel: "Pollo Entero x kg", "Cebolla Roja Metro x kg". El precio que
# publica VTEX ya ES por kilo, asi que la cantidad neta es 1.
_GRANEL = re.compile(r"\b(?:x|por)\s*(kg|kilo|litro|lt|l)\b", re.IGNORECASE)

# "Paquete 24", "Pack 6", "Sixpack" -- numero de envases sin sufijo de unidad
_PACK = re.compile(r"\b(?:paquete|pack|caja|bandeja)\s*(?:de\s*)?(\d+)\b", re.IGNORECASE)


def parse_quantity(name):
    """(cantidad_en_unidad_base, unidad_base) desde el nombre.
    (None, None) si no se puede determinar.

    Tres casos, en este orden:

    1. Granel  "Pollo Entero x kg"            -> (1.0, kg)
    2. Multipack "Agua 350ml Paquete 24un"    -> (8.4, l)   <- 24 x 0.35
    3. Simple  "Arroz Costeno 1kg"            -> (1.0, kg)

    El multipack importa: sin el, ese producto entra al indice como "24
    unidades" y el precio por litro sale mal por un factor de ~3.
    """
    name = name or ""

    granel = _GRANEL.search(name)
    if granel:
        unit, _ = _TO_BASE[granel.group(1).lower()]
        return 1.0, unit

    matches = _QTY.findall(name)

    measures, counts = [], []
    for raw_qty, raw_unit in matches:
        base = _TO_BASE.get(raw_unit.lower())
        if not base:
            continue
        try:
            qty = float(raw_qty.replace(",", "."))
        except ValueError:
            continue
        unit, factor = base
        if unit == "un":
            counts.append(qty)
        else:
            measures.append((qty * factor, unit))

    # "Paquete 24" / "Pack 6" sin sufijo de unidad
    pack = _PACK.search(name)
    if pack:
        try:
            counts.append(float(pack.group(1)))
        except ValueError:
            pass

    if measures:
        qty, unit = measures[-1]
        if counts:
            qty *= max(counts)   # multipack: contenido x cantidad de envases
        return round(qty, 6), unit

    if counts:
        return max(counts), "un"

    return None, None


def _first_offer(item):
    for seller in item.get("sellers") or []:
        offer = seller.get("commertialOffer") or {}
        if offer:
            return offer
    return {}


def _teaser_names(offer):
    names = []
    for t in (offer.get("Teasers") or []):
        if isinstance(t, dict):
            # VTEX serializa raro: "<Name>k__BackingField"
            for k, v in t.items():
                if "Name" in k and isinstance(v, str):
                    names.append(v)
                    break
    return names


def normalize(products, retailer, timestamp):
    """Aplana la respuesta de VTEX. Un item (SKU) = una fila."""
    rows = []
    for p in products:
        brand = p.get("brand")
        cat_path = p.get("_category_path", "")
        category = cat_path.split(" > ")[0] if cat_path else None
        link = p.get("link") or p.get("linkText")

        for item in p.get("items") or []:
            offer = _first_offer(item)
            if not offer:
                continue

            name = item.get("nameComplete") or item.get("name") or p.get("productName")
            price = offer.get("Price")
            list_price = offer.get("ListPrice")
            no_disc = offer.get("PriceWithoutDiscount")

            # precio tachado: ListPrice es el de referencia; si no viene o es
            # menor al de venta, no hay tachado real
            regular = list_price if (list_price or 0) > (price or 0) else price
            if no_disc and no_disc > (regular or 0):
                regular = no_disc

            qty, unit = parse_quantity(name)
            teasers = _teaser_names(offer)
            ean = (item.get("ean") or "").strip()

            rows.append({
                "timestamp": timestamp,
                "retailer": retailer,
                "product_id": p.get("productId"),
                "item_id": item.get("itemId"),
                "product_name": name,
                "brand": brand,
                "category": category,
                "category_path": cat_path,
                "net_quantity": qty,
                "unit": unit,
                "price": price,
                "regular_price": regular,
                # card_price: VTEX no lo expone como numero limpio. Los teasers
                # dicen que HAY promo de tarjeta, no cuanto. Se deja vacio a
                # proposito en vez de inventarlo.
                "card_price": None,
                "on_sale": bool(regular and price and regular > price),
                "available": bool(offer.get("IsAvailable")),
                "available_qty": offer.get("AvailableQuantity"),
                "ean": ean or None,
                "url": link,
                "teasers": json.dumps(teasers, ensure_ascii=False) if teasers else None,
                "scraper_version": SCRAPER_VERSION,
            })
    return rows
