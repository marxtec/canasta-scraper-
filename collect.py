#!/usr/bin/env python3
"""Recoleccion diaria de precios de supermercados de Lima.

    python3 collect.py                      # todas las cadenas activas
    python3 collect.py --retailer plaza_vea # una sola
    python3 collect.py --smoke              # prueba rapida, 2 categorias
    python3 collect.py --audit              # calidad del ultimo CSV

Este script debe correr TODOS LOS DIAS. La historia de precios no se puede
recuperar hacia atras: un dia sin correr es un hueco permanente en la serie.
"""

import argparse
import datetime as dt
import logging
import sys
import pathlib

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from canasta import storage
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


def run(retailer, cfg, keywords, date_str, timestamp, smoke=False):
    client = VtexClient(retailer, cfg, log)
    roots = retailer.get("food_categories") or []

    if smoke:
        leaves = client.leaf_categories(roots, keywords)[:2]
        log.info("SMOKE: solo %d categorias", len(leaves))
        products = []
        for fq, path in leaves:
            for p in client.products_in_category(fq):
                p["_category_path"] = path
                products.append(p)
    else:
        products = client.collect(roots, keywords)

    if not products:
        log.error("%s: 0 productos. No se escribe nada.", retailer["name"])
        return 0

    storage.save_raw(products, retailer["name"], date_str)
    rows = normalize(products, retailer["name"], timestamp)
    path = storage.save_daily(rows, retailer["name"], date_str)

    con_precio = sum(1 for r in rows if r["price"])
    con_peso = sum(1 for r in rows if r["net_quantity"])
    con_ean = sum(1 for r in rows if r["ean"])
    en_oferta = sum(1 for r in rows if r["on_sale"])
    log.info(
        "%s: %d filas -> %s | precio %.0f%% | peso %.0f%% | ean %.0f%% | oferta %.0f%%",
        retailer["name"], len(rows), path.name,
        100 * con_precio / len(rows), 100 * con_peso / len(rows),
        100 * con_ean / len(rows), 100 * en_oferta / len(rows),
    )
    return len(rows)


def audit():
    """Tasa de exito del parseo sobre lo ya recolectado."""
    import csv
    from collections import Counter

    files = sorted(storage.DAILY_DIR.glob("*.csv"))
    if not files:
        log.error("No hay CSV en data/daily todavia.")
        return

    for f in files[-4:]:
        with open(f, encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            continue
        n = len(rows)
        sin_peso = [r["product_name"] for r in rows if not r["net_quantity"]]
        print(f"\n=== {f.name} ({n} filas) ===")
        for campo in ("price", "net_quantity", "ean", "brand"):
            ok = sum(1 for r in rows if r.get(campo))
            print(f"  {campo:<14} {100*ok/n:5.1f}%  ({ok}/{n})")
        if sin_peso:
            print(f"  -- ejemplos sin gramaje parseado ({len(sin_peso)}):")
            for nm in sin_peso[:8]:
                print(f"       {nm}")
        cats = Counter(r["category"] for r in rows)
        print(f"  categorias: {dict(cats.most_common(5))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--retailer", help="solo esta cadena")
    ap.add_argument("--smoke", action="store_true", help="prueba rapida")
    ap.add_argument("--audit", action="store_true", help="calidad de datos")
    args = ap.parse_args()

    if args.audit:
        audit()
        return 0

    conf = load_config()
    cfg = {k: v for k, v in conf.items() if k not in ("retailers", "food_keywords")}
    keywords = conf["food_keywords"]

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
            n = run(retailer, cfg, keywords, date_str, timestamp, args.smoke)
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
