"""Cliente del catalogo Catalyst de Falabella usado por Tottus Peru.

Tottus ya no comparte la API VTEX de Plaza Vea/Metro. Su web publica el menu
de categorias en el HTML inicial (``__NEXT_DATA__``) y los productos en una
API JSON paginada. Este modulo usa esas dos superficies publicas: no requiere
un navegador, cookies persistentes ni credenciales.
"""

import json
import re
import time

import requests

from canasta.normalize import SCRAPER_VERSION, parse_quantity
from canasta.vtex import USER_AGENT, matches_food


_RETRY_STATUS = {429, 500, 502, 503, 504}
_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
_CATEGORY_ID = re.compile(r"/(CATG\d+)(?:/|$)")


def _price_number(value):
    """Convierte el formato de precio de Catalyst a float, si es posible."""
    if isinstance(value, list):
        # En la API actual suele ser ["16.90"]. Si entrega partes enteras y
        # decimales, mantener el punto evita convertir 16,90 en 1690.
        if len(value) == 2 and all(str(x).isdigit() for x in value):
            value = f"{value[0]}.{value[1]}"
        else:
            value = "".join(str(x) for x in value)
    if value is None:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    return float(match.group().replace(",", "."))


def _offer_prices(product):
    """Devuelve precio web, precio regular y eventual precio de tarjeta."""
    prices = product.get("prices") or []
    by_type = {
        str(entry.get("type", "")).lower(): _price_number(entry.get("price"))
        for entry in prices
    }
    price = (
        by_type.get("internetprice")
        or by_type.get("price")
        or by_type.get("normalprice")
    )
    regular = by_type.get("normalprice") or price
    card = next(
        (amount for kind, amount in by_type.items()
         if "cmr" in kind or "card" in kind),
        None,
    )
    return price, regular, card


class CatalystClient:
    """Recolector de categorias y productos de Tottus sobre Catalyst."""

    def __init__(self, retailer, cfg, logger):
        self.name = retailer["name"]
        self.base_url = retailer["base_url"].rstrip("/")
        self.catalog_path = retailer.get("catalog_path", "/tottus-pe")
        self.cfg = cfg
        self.log = logger
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        # Lo ultimo que se bajo, para que collect.py lo guarde junto a los
        # datos. Sin esto, cuando un producto desaparece no se puede saber si
        # la cadena lo deslisto o si movio la categoria y dejamos de verlo.
        self.last_tree = None
        self.last_leaves = []

    def _get(self, url, params=None, expect_json=True):
        """GET con reintentos solo para fallos transitorios."""
        delay = 2.0
        for attempt in range(1, self.cfg["max_retries"] + 1):
            try:
                response = self.session.get(
                    url, params=params, timeout=self.cfg["request_timeout"]
                )
                if response.status_code == 200:
                    if expect_json:
                        ctype = response.headers.get("content-type", "")
                        if "json" not in ctype.lower():
                            self.log.error(
                                "%s: %s respondio %s, se esperaba JSON",
                                self.name, url, ctype or "(sin content-type)",
                            )
                            return None
                        return response.json()
                    return response.text
                if response.status_code not in _RETRY_STATUS:
                    self.log.error(
                        "%s: HTTP %s no reintentable en %s -> %s",
                        self.name, response.status_code, url,
                        response.text[:200].strip(),
                    )
                    return None
                self.log.warning(
                    "%s HTTP %s en %s (intento %d/%d)",
                    self.name, response.status_code, url, attempt,
                    self.cfg["max_retries"],
                )
            except (requests.RequestException, ValueError) as exc:
                self.log.warning(
                    "%s fallo %s en %s (intento %d/%d)", self.name,
                    type(exc).__name__, url, attempt, self.cfg["max_retries"],
                )
            if attempt < self.cfg["max_retries"]:
                time.sleep(delay)
                delay *= 2
        self.log.error("%s agoto reintentos en %s", self.name, url)
        return None

    def category_tree(self):
        """Lee el menu de categorias que Tottus entrega en el HTML inicial."""
        html = self._get(
            f"{self.base_url}{self.catalog_path}", expect_json=False,
        )
        if not html:
            return []
        match = _NEXT_DATA.search(html)
        if not match:
            self.log.error("%s: no se encontro __NEXT_DATA__ en el catalogo", self.name)
            return []
        try:
            data = json.loads(match.group(1))
            return (
                data["props"]["pageProps"]["serverData"]["headerData"]
                ["taxonomy"]["entry"]["all_accesses"]["categories"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            self.log.error("%s: taxonomia Catalyst inesperada: %s", self.name, exc)
            return []

    @staticmethod
    def _category_id(node):
        match = _CATEGORY_ID.search(node.get("item_url", ""))
        return match.group(1) if match else None

    def leaf_categories(self, roots, keywords, excluded=()):
        """Devuelve categoria, ruta legible de las hojas de alimentos."""
        tree = self.category_tree()
        self.last_tree = tree
        if not tree:
            return []

        if roots:
            selected = [node for node in tree if self._category_id(node) in roots]
            missing = set(roots) - {self._category_id(node) for node in selected}
            if missing:
                self.log.warning("%s: categorias configuradas inexistentes: %s",
                                 self.name, sorted(missing))
        else:
            selected = [
                node for node in tree
                if matches_food(node.get("item_name", ""), keywords, excluded)
            ]

        self.log.info(
            "%s: %d categorias de alimentos: %s", self.name, len(selected),
            [node.get("item_name") for node in selected],
        )
        if not selected:
            self.log.error("%s: ninguna categoria de alimentos coincidió", self.name)
            return []

        leaves, seen = [], set()

        def walk(node, names):
            name = node.get("item_name", "?")
            names = names + [name]
            children = (
                node.get("second_level_categories")
                or node.get("third_level_categories")
                or []
            )
            if children:
                for child in children:
                    walk(child, names)
                return
            category_id = self._category_id(node)
            if category_id and category_id not in seen:
                seen.add(category_id)
                leaves.append((category_id, " > ".join(names)))

        for node in selected:
            walk(node, [])
        self.last_leaves = leaves
        return leaves

    def products_in_category(self, category_id):
        """Pagina una categoria Catalyst y conserva solo sus productos."""
        url = f"{self.base_url}/s/browse/v1/listing/pe"
        products = []
        for page in range(1, self.cfg.get("max_pages", 100) + 1):
            payload = self._get(url, params={"categoryId": category_id, "page": page})
            if payload is None:
                break
            data = payload.get("data") or {}
            batch = data.get("results") or []
            products.extend(batch)
            pagination = data.get("pagination") or {}
            current = pagination.get("currentPage", page)
            total_pages = pagination.get("totalPages")
            if total_pages is not None:
                if current >= total_pages:
                    break
            elif len(batch) < pagination.get("perPage", 48):
                break
            time.sleep(self.cfg["rate_limit_seconds"])
        return products

    def collect(self, roots, keywords, excluded=()):
        leaves = self.leaf_categories(roots, keywords, excluded)
        if not leaves:
            return []
        self.log.info("%s: %d categorias hoja por recorrer", self.name, len(leaves))
        products, seen, empty = [], set(), 0
        for index, (category_id, path) in enumerate(leaves, 1):
            batch = self.products_in_category(category_id)
            if not batch:
                empty += 1
            new = 0
            for product in batch:
                product_id = product.get("productId") or product.get("skuId")
                if product_id and product_id not in seen:
                    seen.add(product_id)
                    product["_category_path"] = path
                    products.append(product)
                    new += 1
            self.log.info("  [%d/%d] %s -> %d productos (%d nuevos)",
                          index, len(leaves), path[:60], len(batch), new)
            time.sleep(self.cfg["rate_limit_seconds"])
        self.log.info("%s: %d productos unicos en %d hojas (%d hojas vacias)",
                      self.name, len(products), len(leaves), empty)
        return products


def normalize(products, retailer, timestamp):
    """Aplana los productos Catalyst al mismo esquema diario que VTEX."""
    rows = []
    for product in products:
        name = product.get("displayName")
        price, regular, card = _offer_prices(product)
        quantity, unit = parse_quantity(name)
        category_path = product.get("_category_path", "")
        discount = (product.get("discountBadge") or {}).get("label")
        on_sale = bool(regular and price and regular > price)
        flags = []
        if on_sale:
            flags.append("descuento")
        if card:
            flags.append("tarjeta")
        rows.append({
            "timestamp": timestamp,
            "retailer": retailer,
            "product_id": product.get("productId"),
            "item_id": product.get("skuId") or product.get("offeringId"),
            "product_name": name,
            "brand": product.get("brand"),
            "category": category_path.split(" > ")[0] if category_path else None,
            "category_path": category_path,
            "net_quantity": quantity,
            "unit": unit,
            "unit_multiplier": None,
            "price": price,
            "regular_price": regular,
            "card_price": card,
            "on_sale": on_sale,
            # La API de listado no publica inventario por SKU. Un producto sin
            # precio no se considera disponible; fuera de ese caso se deja
            # vacio en vez de afirmar stock sin evidencia.
            "available": None if price is not None else False,
            "available_qty": None,
            "ean": None,
            "url": product.get("url"),
            "teasers": json.dumps([discount], ensure_ascii=False) if discount else None,
            "promo_type": ";".join(flags) or None,
            # Catalyst no publica vigencia de la promo en el listado.
            "promo_start": None,
            "promo_end": None,
            # Catalyst tambien mezcla marketplace con el surtido propio.
            "seller_id": product.get("sellerId"),
            "seller_name": product.get("sellerName"),
            # Especificaciones: solo en la ficha, no en el listado.
            "vendido_por": None,
            "origen": None,
            "octogonos": None,
            "contenido_neto_declarado": None,
            "scraper_version": SCRAPER_VERSION,
        })
    return rows
