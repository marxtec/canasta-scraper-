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

SCRAPER_VERSION = "1.1"

COLUMNS = [
    "timestamp", "retailer", "product_id", "item_id", "product_name", "brand",
    "category", "category_path", "net_quantity", "unit", "price",
    "regular_price", "card_price", "on_sale", "available", "available_qty",
    "ean", "url", "teasers", "seller_id", "seller_name", "scraper_version",
]

# Techo de cordura para kg/l. Una canasta de alimentos no tiene envases de
# mas de 50 kg: por encima de eso el nombre se parseo mal, y es preferible
# declararlo no parseado (y que --audit lo cuente) antes que dejar pasar un
# numero plausible pero falso que despues divide el precio por kilo.
MAX_PLAUSIBLE_BASE = 50.0

# "1kg", "750 g", "1.5 L", "x 500gr", "500ml", "30un", "6 Unidades"
#
# El orden de la alternancia importa: la regex se queda con la primera
# opcion que encaja, asi que las formas largas van antes. Con "un" delante,
# "6 Unidades" fallaba (tomaba "un" y el \b se rompia contra "idades").
_QTY = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(kilos|kilo|kg|gramos|gra|gr|g|litros|litro|lts|lt|l|ml|cc"
    r"|unidades|unidad|unid|und|un)\b",
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
    "unidad": ("un", 1.0), "unidades": ("un", 1.0),
}


# Granel: "Pollo Entero x kg", "Cebolla Roja Metro x kg". El precio que
# publica VTEX ya ES por kilo, asi que la cantidad neta es 1.
_GRANEL = re.compile(r"\b(?:x|por)\s*(kg|kilo|litro|lt|l)\b", re.IGNORECASE)

# Unidades de medida, para poder EXCLUIRLAS al contar envases.
_UNIT_ALT = (
    r"kg|kilos?|g|gr|gra|gramos|l|lt|lts|litros?|ml|cc|oz|onzas?"
)

# "Paquete 24", "Pack de 6", "Caja 12un" -- numero de ENVASES.
#
# El (?!...) es lo que evita el bug que inflaba el gramaje: en "Caja 490 g"
# el 490 son gramos, no 490 cajas, y multiplicarlo daba 240 kg de paella.
# Solo cuenta como envase el numero que NO va seguido de una unidad de medida.
_PACK = re.compile(
    r"\b(?:paquete|pack|caja|bandeja|blister|display)\s*(?:de\s*|x\s*)?"
    r"(\d+)\s*(?:un|und|unid|unidades)?\b(?!\s*(?:" + _UNIT_ALT + r")\b)",
    re.IGNORECASE,
)

# Multiplicador suelto al final: "Jamon 200 g x 2", "Yogurt 120g X6".
# Mismo cuidado: "x 500 g" es gramaje, no multiplicador.
_MULT = re.compile(
    r"\bx\s*(\d{1,3})\b(?!\s*(?:" + _UNIT_ALT + r")\b)",
    re.IGNORECASE,
)

# "Sixpack", "Six Pack", "Twopack", "Docena", "Media docena"
_WORD_PACK = [
    (re.compile(r"\bmedia\s+docena\b", re.IGNORECASE), 6),
    (re.compile(r"\bdocena\b", re.IGNORECASE), 12),
    (re.compile(r"\bsix\s*pack\b", re.IGNORECASE), 6),
    (re.compile(r"\bfour\s*pack\b", re.IGNORECASE), 4),
    (re.compile(r"\btwo\s*pack\b", re.IGNORECASE), 2),
    (re.compile(r"\btri\s*pack\b", re.IGNORECASE), 3),
]

# Promo de contenido extra: "Aceite 900ml + 100ml" son 1.0 L en total, no
# 900ml ni 100ml. Solo se suma cuando ambas medidas comparten unidad base.
_BONUS = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(" + _UNIT_ALT + r")\s*\+\s*"
    r"(\d+(?:[.,]\d+)?)\s*(" + _UNIT_ALT + r")\b",
    re.IGNORECASE,
)


def _base_measures(name):
    """[(cantidad_en_base, unidad_base)] de todas las medidas del nombre."""
    out = []
    for raw_qty, raw_unit in _QTY.findall(name):
        base = _TO_BASE.get(raw_unit.lower())
        if not base:
            continue
        try:
            qty = float(raw_qty.replace(",", "."))
        except ValueError:
            continue
        unit, factor = base
        out.append((qty * factor, unit))
    return out


def _container_count(name):
    """Numero de envases del multipack, o None.

    Se queda con el mayor de los indicios porque los nombres suelen repetir
    el dato ("Agua 350ml Paquete 24un" trae el 24 dos veces) y nunca mezclan
    dos multipacks distintos en un mismo SKU.
    """
    counts = []
    for rx in (_PACK, _MULT):
        for m in rx.finditer(name):
            try:
                counts.append(float(m.group(1)))
            except ValueError:
                pass
    for rx, value in _WORD_PACK:
        if rx.search(name):
            counts.append(float(value))
    return max(counts) if counts else None


def parse_quantity(name):
    """(cantidad_en_unidad_base, unidad_base) desde el nombre.
    (None, None) si no se puede determinar o si el resultado es absurdo.

    Casos, en este orden:

    1. Granel     "Pollo Entero x kg"             -> (1.0, kg)
    2. Contenido extra "Aceite 900ml + 100ml"     -> (1.0, l)    <- 0.9 + 0.1
    3. Multipack  "Agua 350ml Paquete 24un"       -> (8.4, l)    <- 24 x 0.35
    4. Multipack  "Leche 400g Pack x 6"           -> (2.4, kg)   <- 6 x 0.4
    5. Simple     "Arroz Costeno 1kg"             -> (1.0, kg)
    6. Solo conteo "Huevos Paquete de 15"         -> (15.0, un)

    El multipack importa: sin el, ese producto entra al indice como "24
    unidades" y el precio por litro sale mal por un factor de ~3. Y el
    conteo de envases NUNCA debe leerse de una cifra seguida de unidad de
    medida: "Caja 490 g" son 490 gramos en una caja, no 490 cajas.
    """
    name = name or ""

    granel = _GRANEL.search(name)
    if granel:
        unit, _ = _TO_BASE[granel.group(1).lower()]
        return 1.0, unit

    todas = _base_measures(name)
    measures = [(q, u) for q, u in todas if u != "un"]

    # Un conteo suelto de unidades ("Atun 170g 6 Unidades") es el numero de
    # envases igual que un "Pack 6", solo que sin la palabra delante.
    sueltos = [q for q, u in todas if u == "un"]
    candidatos = [c for c in (_container_count(name),) if c] + sueltos
    counts = max(candidatos) if candidatos else None

    if measures:
        # "900ml + 100ml": contenido base mas promocion, misma unidad.
        bonus = _BONUS.search(name)
        if bonus:
            a = _TO_BASE.get(bonus.group(2).lower())
            b = _TO_BASE.get(bonus.group(4).lower())
            if a and b and a[0] == b[0]:
                try:
                    qty = (float(bonus.group(1).replace(",", ".")) * a[1]
                           + float(bonus.group(3).replace(",", ".")) * b[1])
                    unit = a[0]
                except ValueError:
                    qty, unit = max(measures)
            else:
                qty, unit = max(measures)
        else:
            # El envase mas grande del nombre es el contenido; los demas
            # suelen ser gramajes de regalo o referencias secundarias.
            qty, unit = max(measures)

        if counts:
            qty *= counts
        if qty > MAX_PLAUSIBLE_BASE:
            return None, None
        return round(qty, 6), unit

    # Sin medida de peso/volumen: solo cuenta de unidades.
    if counts:
        return counts, "un"

    return None, None


def _best_seller(item):
    """(oferta, seller) del vendedor de la cadena, no del marketplace.

    VTEX devuelve el catalogo propio y el de terceros en la misma lista. El
    primer seller no es necesariamente el supermercado: si es un tercero, el
    precio que entra a la serie no es el de la cadena y la comparacion entre
    cadenas deja de medir lo que dice medir. `sellerDefault` marca al
    vendedor propio; solo si ninguno lo trae se cae al primero con oferta.
    """
    sellers = item.get("sellers") or []
    for seller in sellers:
        if seller.get("sellerDefault") and (seller.get("commertialOffer") or {}):
            return seller["commertialOffer"], seller
    for seller in sellers:
        offer = seller.get("commertialOffer") or {}
        if offer:
            return offer, seller
    return {}, {}


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
            offer, seller = _best_seller(item)
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
                # Se guardan para poder auditar despues si algun precio vino
                # de un tercero del marketplace y no de la cadena.
                "seller_id": seller.get("sellerId"),
                "seller_name": seller.get("sellerName"),
                "scraper_version": SCRAPER_VERSION,
            })
    return rows
