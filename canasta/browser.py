"""Scraping con navegador para el precio con tarjeta.

Por que existe: es el UNICO dato de la propuesta que la API de catalogo no
expone. VTEX publica `Teasers` que dicen que HAY promocion de tarjeta
("Promo Oh-Pay"), pero no el monto. En la ficha renderizada si aparece.

Verificado 2026-09-19 en Metro:

    Tarjeta Cencosud
    S/ 17.76     <- solo visible en el navegador
    S/ 18.50     <- API: commertialOffer.Price
    S/ 22.00     <- API: commertialOffer.ListPrice

Por que NO reemplaza al cliente de API:

1. Cuesta ~7 s por producto contra ~0.5 s por 50 productos via API. Aplicado
   al catalogo completo (1.632 hojas) serian dias, no minutos.
2. No entrega `ean`, `AvailableQuantity` ni `sellerDefault`, que si vienen en
   el JSON y hacen falta para emparejar cadenas y descartar marketplace.
3. Depende de texto visible ("Tarjeta Cencosud") en vez de un campo con
   nombre estable, asi que se rompe con cualquier rediseno.

Por eso se usa de forma quirurgica: sobre una LISTA ACOTADA de SKU -- los de
la canasta definida, no el catalogo entero -- y como serie paralela.

A diferencia del EAN, el precio con tarjeta cambia a diario: no se cachea.

Dependencia opcional:
    pip install -r requirements-browser.txt
    python3 -m playwright install chromium
"""

import contextlib
import re

# Marcas comerciales de precio con tarjeta de cada cadena. Se amplia aqui
# cuando aparezca una nueva; el resto del modulo no cambia.
_MARCAS = (
    r"(?:Tarjeta\s+Cencosud|Tarjeta\s+Oh!?|Precio\s+Oh!?|Oh!?\s*Pay"
    r"|Tarjeta\s+CMR|Precio\s+socio|Precio\s+con\s+tarjeta)"
)
_RX_CARD = re.compile(_MARCAS + r"[^\d]{0,80}S/\s*([\d.,]+)", re.IGNORECASE)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _parse_card(texto):
    m = _RX_CARD.search(texto or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


@contextlib.contextmanager
def navegador(user_agent=_UA, headless=True):
    """Contexto de Chromium listo para abrir fichas: UA, locale y viewport.

    Es el UNICO sitio donde se arranca el navegador; la auditoria de validez
    (analisis/auditoria_validez.py) lo reutiliza en vez de duplicar el
    arranque. Importa playwright aqui dentro y no arriba: el pipeline diario
    no lo necesita y no debe fallar si no esta instalado. Lanza ImportError
    con el mensaje de instalacion si falta.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ImportError(
            "playwright no esta instalado. "
            "pip install -r requirements-browser.txt && "
            "python3 -m playwright install chromium"
        ) from exc

    with sync_playwright() as pw:
        nav = pw.chromium.launch(
            headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = nav.new_context(
            user_agent=user_agent, locale="es-PE",
            viewport={"width": 1440, "height": 1000},
        )
        try:
            yield ctx
        finally:
            nav.close()


def scrape_card_prices(urls, log, espera_ms=7000, timeout_ms=60000, limite=None):
    """{url: precio_tarjeta_o_None} para las URLs de ficha dadas."""
    urls = list(dict.fromkeys(urls))
    if limite:
        urls = urls[:limite]
    out = {}

    try:
        arranque = navegador()
    except ImportError as exc:
        log.error("%s", exc)
        return {}

    with arranque as ctx:
        for i, url in enumerate(urls, 1):
            pagina = ctx.new_page()
            try:
                pagina.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                pagina.wait_for_timeout(espera_ms)
                out[url] = _parse_card(pagina.inner_text("body"))
            except Exception as exc:
                # Una ficha caida no debe tumbar el lote entero.
                log.warning("tarjeta: fallo %s en %s", type(exc).__name__, url[:70])
                out[url] = None
            finally:
                pagina.close()
            if i % 10 == 0:
                con = sum(1 for v in out.values() if v)
                log.info("tarjeta: %d/%d fichas, %d con precio", i, len(urls), con)

    con = sum(1 for v in out.values() if v)
    log.info("tarjeta: %d de %d fichas con precio de tarjeta", con, len(out))
    return out


def enrich_csv(path, log, limite=None):
    """Rellena la columna card_price de un CSV diario ya escrito.

    Se hace como paso aparte y no dentro de la corrida diaria: el navegador
    es lento y fragil, y la captura de PRECIOS -- lo unico irrecuperable --
    no puede depender de el.
    """
    import csv

    from canasta.normalize import COLUMNS

    with open(path, encoding="utf-8") as fh:
        filas = list(csv.DictReader(fh))
    if not filas:
        log.error("%s esta vacio", path)
        return 0

    pendientes = [f["url"] for f in filas if f.get("url") and not f.get("card_price")]
    if not pendientes:
        log.info("%s: nada que enriquecer", path)
        return 0

    precios = scrape_card_prices(pendientes, log, limite=limite)
    n = 0
    for fila in filas:
        valor = precios.get(fila.get("url"))
        if valor:
            fila["card_price"] = valor
            n += 1

    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)
    log.info("%s: %d filas con card_price", path, n)
    return n
