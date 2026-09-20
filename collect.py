#!/usr/bin/env python3
"""Recoleccion diaria de precios de supermercados de Lima.

    python3 collect.py                      # todas las cadenas activas
    python3 collect.py --retailer plaza_vea # una sola
    python3 collect.py --smoke              # prueba rapida, 2 categorias
    python3 collect.py --audit              # calidad del ultimo CSV

Este script debe correr TODOS LOS DIAS. La historia de precios no se puede
recuperar hacia atras: un dia sin correr es un hueco permanente en la serie.!
"""

import argparse
import datetime as dt
import logging
import sys
import pathlib

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from canasta import eans, storage
from canasta.catalyst import CatalystClient, normalize as normalize_catalyst
from canasta.normalize import normalize
from canasta.vtex import VtexClient

ROOT = pathlib.Path(__file__).resolve().parent
LIMA = dt.timezone(dt.timedelta(hours=-5))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("canasta")


def load_config():
    with open(ROOT / "config" / "retailers.yml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


CLIENTS = {
    "vtex": (VtexClient, normalize),
    "falabella_catalyst": (CatalystClient, normalize_catalyst),
}


def _avisar_si_cae_el_volumen(retailer, n_filas, date_str, umbral=0.5):
    """Compara con la ultima corrida de esta cadena y avisa si se desploma.

    Una rotura del parser o un cambio en el arbol de categorias no produce un
    error: produce menos filas. Sin esta comparacion, la corrida termina en
    verde con el 10% de los datos y el hueco se descubre semanas despues,
    cuando ya no se puede rellenar.
    """
    import csv

    previos = sorted(storage.DAILY_DIR.glob(f"*__{retailer}.csv"))
    previos = [f for f in previos if not f.name.startswith(date_str)]
    if not previos:
        return
    with open(previos[-1], encoding="utf-8") as fh:
        anterior = sum(1 for _ in csv.DictReader(fh))
    if anterior and n_filas < anterior * umbral:
        log.warning(
            "%s: CAIDA DE VOLUMEN. %d filas hoy vs %d en %s (-%.0f%%). "
            "Revisar el parser ANTES de fiarse de estos datos.",
            retailer, n_filas, anterior, previos[-1].name,
            100 * (1 - n_filas / anterior),
        )


def run(retailer, cfg, keywords, excluded, date_str, timestamp, smoke=False):
    platform = retailer.get("platform")
    try:
        client_class, normalizer = CLIENTS[platform]
    except KeyError as exc:
        raise ValueError(f"plataforma no soportada: {platform!r}") from exc

    client = client_class(retailer, cfg, log)
    roots = retailer.get("food_categories") or []

    if smoke:
        leaves = client.leaf_categories(roots, keywords, excluded)[:2]
        log.info("SMOKE: solo %d categorias", len(leaves))
        products = []
        for fq, path in leaves:
            for p in client.products_in_category(fq):
                p["_category_path"] = path
                products.append(p)
    else:
        products = client.collect(roots, keywords, excluded)

    if not products:
        log.error("%s: 0 productos. No se escribe nada.", retailer["name"])
        return 0

    rows = normalizer(products, retailer["name"], timestamp)

    # Tottus no trae EAN en el listado: se completa desde cache persistente,
    # con presupuesto por corrida. Nunca bloquea la captura de precios.
    if rows and retailer.get("ean_from_product_page") and not smoke:
        try:
            eans.enrich(rows, cfg, log)
        except Exception:
            log.exception("%s: fallo el enriquecimiento de EAN (se continua)",
                          retailer["name"])

    if not rows:
        # Distinto de "0 productos": la API respondio, el parser no produjo
        # nada. Apunta a un cambio de esquema, no a un fallo de red.
        log.error(
            "%s: %d productos pero 0 filas normalizadas. Revisar el parser.",
            retailer["name"], len(products),
        )
        return 0

    con_precio = sum(1 for r in rows if r["price"])
    con_peso = sum(1 for r in rows if r["net_quantity"])
    con_ean = sum(1 for r in rows if r["ean"])
    en_oferta = sum(1 for r in rows if r["on_sale"])
    if smoke:
        log.info(
            "%s: %d filas validadas (smoke no escribe archivos) | precio %.0f%% | "
            "peso %.0f%% | ean %.0f%% | oferta %.0f%%",
            retailer["name"], len(rows), 100 * con_precio / len(rows),
            100 * con_peso / len(rows), 100 * con_ean / len(rows),
            100 * en_oferta / len(rows),
        )
        return len(rows)

    _avisar_si_cae_el_volumen(retailer["name"], len(rows), date_str)

    storage.save_raw(products, retailer["name"], date_str)
    path = storage.save_daily(rows, retailer["name"], date_str)
    log.info(
        "%s: %d filas -> %s | precio %.0f%% | peso %.0f%% | ean %.0f%% | oferta %.0f%%",
        retailer["name"], len(rows), path.name,
        100 * con_precio / len(rows), 100 * con_peso / len(rows),
        100 * con_ean / len(rows), 100 * en_oferta / len(rows),
    )
    return len(rows)


def audit():
    """Calidad del parseo sobre lo ya recolectado.

    No basta con contar campos vacios: el fallo mas peligroso del pipeline no
    es el gramaje ausente, es el gramaje PRESENTE y equivocado. Un nombre mal
    parseado entrega un numero plausible que despues divide el precio, y en
    un conteo de completitud sale como 100% de exito. Por eso aqui tambien se
    revisan precios por unidad base fuera de rango.
    """
    import csv
    from collections import Counter

    files = sorted(storage.DAILY_DIR.glob("*.csv"))
    if not files:
        log.error("No hay CSV en data/daily todavia.")
        return

    for f in files[-6:]:
        with open(f, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            continue
        n = len(rows)
        print(f"\n=== {f.name} ({n} filas) ===")
        for campo in ("price", "net_quantity", "ean", "brand", "seller_name"):
            ok = sum(1 for r in rows if r.get(campo))
            print(f"  {campo:<14} {100*ok/n:5.1f}%  ({ok}/{n})")

        sin_peso = [r["product_name"] for r in rows if not r["net_quantity"]]
        if sin_peso:
            print(f"  -- sin gramaje parseado ({len(sin_peso)}):")
            for nm in sin_peso[:5]:
                print(f"       {nm}")

        # Precio por kg/l absurdo = gramaje mal parseado. Los umbrales son
        # deliberadamente anchos: se busca el error grosero, no el caro.
        raros = []
        for r in rows:
            try:
                precio = float(r["price"] or 0)
                qty = float(r["net_quantity"] or 0)
            except ValueError:
                continue
            if precio <= 0 or qty <= 0 or r["unit"] not in ("kg", "l"):
                continue
            por_base = precio / qty
            if por_base < 0.5 or por_base > 300:
                raros.append((por_base, r["product_name"], precio, qty, r["unit"]))
        if raros:
            raros.sort(key=lambda x: -x[0])
            print(f"  -- precio por unidad base sospechoso ({len(raros)}, "
                  f"{100*len(raros)/n:.1f}%):")
            for por_base, nm, precio, qty, unit in raros[:5]:
                print(f"       S/{por_base:9.2f}/{unit}  (S/{precio} / {qty}{unit})  {nm[:44]}")

        # Un precio que no viene del supermercado no es comparable entre
        # cadenas: es de un tercero del marketplace.
        propios = Counter(r.get("seller_name") or "?" for r in rows)
        if len(propios) > 1:
            print(f"  -- vendedores: {dict(propios.most_common(4))}")

        cats = Counter(r["category"] for r in rows)
        print(f"  categorias: {dict(cats.most_common(5))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--retailer", help="solo esta cadena")
    ap.add_argument("--smoke", action="store_true", help="prueba rapida")
    ap.add_argument("--audit", action="store_true", help="calidad de datos")
    ap.add_argument("--card-prices", metavar="CSV",
                    help="rellena card_price en un CSV diario usando navegador")
    ap.add_argument("--backfill-ean", action="store_true",
                    help="resuelve de una vez los EAN pendientes de Tottus")
    ap.add_argument("--limit", type=int, default=120,
                    help="max fichas a abrir con --card-prices/--backfill-ean")
    args = ap.parse_args()

    if args.audit:
        audit()
        return 0

    if args.backfill_ean:
        # Pasada unica: resuelve los EAN pendientes del ultimo CSV de Tottus
        # sin el limite del job diario. El EAN es estatico, asi que esto se
        # paga una sola vez.
        import csv as _csv
        from canasta import eans as _eans
        archivos = sorted(storage.DAILY_DIR.glob("*__tottus.csv"))
        if not archivos:
            log.error("No hay CSV de Tottus todavia. Corre la recoleccion primero.")
            return 1
        with open(archivos[-1], encoding="utf-8") as fh:
            filas = list(_csv.DictReader(fh))
        conf = load_config()
        cfg = {k: v for k, v in conf.items()
               if k not in ("retailers", "food_keywords", "excluded_keywords")}
        cfg["ean_budget_per_run"] = args.limit
        log.info("Relleno de EAN sobre %s (%d filas), presupuesto %d",
                 archivos[-1].name, len(filas), args.limit)
        _eans.enrich(filas, cfg, log)
        # Aplicar la cache recien ampliada a TODOS los CSV de Tottus ya
        # escritos. Sin esto, los dias anteriores quedan sin EAN aunque el
        # dato ya este resuelto: el codigo de barras es estatico, asi que
        # aplicarlo hacia atras es correcto.
        for csv_previo in archivos:
            _eans.apply_cache_to_csv(csv_previo, log)
        return 0

    if args.card_prices:
        # Paso aparte y opcional: el navegador es lento y fragil, y la captura
        # de precios -- lo unico que no se recupera hacia atras -- no puede
        # depender de el. Ver canasta/browser.py.
        from canasta import browser
        ruta = pathlib.Path(args.card_prices)
        if not ruta.exists():
            log.error("No existe %s", ruta)
            return 1
        browser.enrich_csv(ruta, log, limite=args.limit)
        return 0

    conf = load_config()
    cfg = {
        k: v for k, v in conf.items()
        if k not in ("retailers", "food_keywords", "excluded_keywords")
    }
    keywords = conf["food_keywords"]
    excluded = conf.get("excluded_keywords", [])

    now = dt.datetime.now(LIMA)
    date_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S%z")

    targets = [r for r in conf["retailers"] if r.get("enabled")]
    if args.retailer:
        targets = [r for r in targets if r["name"] == args.retailer]
        if not targets:
            log.error("Cadena '%s' no existe o esta enabled: false", args.retailer)
            return 1

    log.info("=== %s | %d cadenas ===", date_str, len(targets))
    total, fallos = 0, []
    for retailer in targets:
        try:
            n = run(retailer, cfg, keywords, excluded, date_str, timestamp, args.smoke)
            total += n
            if n == 0:
                fallos.append(retailer["name"])
        except Exception:
            log.exception("%s: fallo no controlado", retailer["name"])
            fallos.append(retailer["name"])

    log.info("=== total %d filas ===", total)
    if fallos:
        log.warning("cadenas sin datos: %s", ", ".join(fallos))
    # exit 1 solo si NADIE devolvio datos: que una cadena falle no debe
    # marcar en rojo la corrida entera ni detener la acumulacion.
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
