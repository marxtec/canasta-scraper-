"""Cache persistente de codigos EAN para Tottus.

Por que existe: Tottus no publica el EAN en su API de listado, solo en la
ficha de cada producto (campo ``okayToShopBarcodes``), y no hay endpoint
masivo. Pedir una ficha por producto cada dia costaria horas y golpearia el
servidor sin necesidad.

La clave es que **el EAN es un atributo estatico**: el precio del arroz cambia
a diario, su codigo de barras no. Asi que se pide UNA vez por SKU, se guarda
en disco y los dias siguientes se lee de ahi.

Como el catalogo completo son miles de SKU, la primera pasada se reparte en
varios dias con un presupuesto por corrida (``ean_budget_per_run``). Esto es
deliberado y no compromete nada: la recoleccion de PRECIOS -- lo unico que no
se puede recuperar hacia atras -- funciona completa desde el dia uno. El EAN
solo hace falta al emparejar cadenas, que es trabajo de analisis posterior.
"""

import json
import pathlib
import re
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "ean_tottus.json"

# EAN-13 / EAN-8. Se exige longitud exacta para no confundirlo con los
# timestamps en milisegundos que tambien aparecen en la ficha.
_BARCODES = re.compile(r'"okayToShopBarcodes"\s*:\s*\[\s*"(\d{8,14})"')
_DATA_EAN = re.compile(r'data-ean="(\d{8,14})"')


def load_cache(path=CACHE_PATH):
    """{sku: ean_o_null}. El null es memoria negativa: marca los SKU cuya
    ficha ya se consulto y no traia codigo, para no reintentarlos cada dia."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_cache(cache, path=CACHE_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=0, sort_keys=True)
    tmp.replace(path)     # atomico: una corrida cortada no deja la cache rota
    return path


def _scrape_ean(session, url, timeout):
    try:
        r = session.get(url, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    for rx in (_BARCODES, _DATA_EAN):
        m = rx.search(r.text)
        if m:
            return m.group(1)
    return None


def enrich(rows, cfg, log, session=None):
    """Rellena row["ean"] desde la cache y amplia la cache con el presupuesto.

    Muta `rows` y devuelve (desde_cache, nuevos_pedidos).
    """
    cache = load_cache()
    desde_cache = 0

    pendientes = []
    for row in rows:
        sku = str(row.get("item_id") or "")
        if not sku:
            continue
        if sku in cache:
            if cache[sku]:
                row["ean"] = cache[sku]
                desde_cache += 1
        elif row.get("url"):
            pendientes.append((sku, row))

    budget = int(cfg.get("ean_budget_per_run", 400))
    if not pendientes or budget <= 0:
        log.info("EAN: %d desde cache, 0 pendientes nuevos (cache: %d SKU)",
                 desde_cache, len(cache))
        return desde_cache, 0

    session = session or requests.Session()
    objetivo = pendientes[:budget]
    nuevos = 0
    for sku, row in objetivo:
        ean = _scrape_ean(session, row["url"], cfg.get("request_timeout", 60))
        cache[sku] = ean          # se guarda tambien el None (memoria negativa)
        if ean:
            row["ean"] = ean
            nuevos += 1
        time.sleep(cfg.get("ean_rate_limit_seconds", cfg.get("rate_limit_seconds", 1.5)))

    save_cache(cache)
    log.info(
        "EAN: %d desde cache + %d nuevos de %d fichas consultadas | "
        "cache %d SKU | quedan %d por resolver",
        desde_cache, nuevos, len(objetivo), len(cache),
        max(0, len(pendientes) - len(objetivo)),
    )
    return desde_cache, nuevos


def apply_cache_to_csv(path, log):
    """Rellena la columna ean de un CSV ya escrito, usando solo la cache.

    No toca la red. Existe porque la cache se llena DESPUES de que el CSV se
    escribio: sin esto, el primer dia del panel queda sin EAN aunque el dato
    ya este resuelto en disco. Y como el EAN es estatico, aplicarlo hacia
    atras es correcto: el codigo de barras de ese SKU era el mismo ese dia.
    """
    import csv

    from canasta.normalize import COLUMNS

    cache = load_cache()
    if not cache:
        log.info("%s: cache de EAN vacia, nada que aplicar", path.name)
        return 0

    with open(path, encoding="utf-8") as fh:
        filas = list(csv.DictReader(fh))
    if not filas:
        return 0

    n = 0
    for fila in filas:
        if fila.get("ean"):
            continue
        ean = cache.get(str(fila.get("item_id") or ""))
        if ean:
            fila["ean"] = ean
            n += 1

    if not n:
        log.info("%s: sin EAN nuevos que aplicar", path.name)
        return 0

    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)
    tmp.replace(path)

    con = sum(1 for f in filas if f.get("ean"))
    log.info("%s: +%d EAN desde cache -> %d/%d (%.1f%%)",
             path.name, n, con, len(filas), 100 * con / len(filas))
    return n
