"""Guardado en dos capas.

raw/   JSON crudo comprimido, tal cual lo devolvio la API.
daily/ CSV normalizado, una fila por SKU por dia.

El crudo existe porque el dia que encuentres un bug en el parser -- y lo vas
a encontrar -- puedas reprocesar las semanas anteriores. Sin el, un error de
normalizacion en la semana 6 te obliga a tirar las semanas 1 a 5. Ocupa poco
comprimido y es la unica red de seguridad real del proyecto.
"""

import csv
import gzip
import json
import pathlib

from canasta.normalize import COLUMNS

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DAILY_DIR = ROOT / "data" / "daily"


def save_raw(products, retailer, date_str):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{date_str}__{retailer}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(products, fh, ensure_ascii=False)
    return path


def save_daily(rows, retailer, date_str):
    """CSV por cadena y dia. Reescribe si ya existe: correr dos veces el mismo
    dia no duplica filas."""
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    path = DAILY_DIR / f"{date_str}__{retailer}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def load_raw(path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)
