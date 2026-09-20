"""Guardado en tres capas.

raw/   JSON crudo comprimido, tal cual lo devolvio la API.
daily/ CSV normalizado, una fila por SKU por dia.
trees/ Arbol de categorias y hojas recorridas, por cadena y dia.

El arbol existe porque cada dia desaparecen ~440 productos del panel y sin el
no se puede distinguir "la cadena lo deslisto" de "la cadena reorganizo sus
categorias y el scraper dejo de verlo". Para medir cuanto dura una oferta esa
diferencia lo es todo: un hueco por reorganizacion parece que la oferta
termino. Es lo unico del pipeline que no se recupera del crudo: se baja cada
manana y, sin esto, se tira.

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
TREES_DIR = ROOT / "data" / "trees"


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


def save_tree(tree, leaves, retailer, date_str):
    """Arbol completo + hojas efectivamente recorridas [(fq, ruta)].

    Se guardan las dos cosas: el arbol dice que publico la cadena; las hojas
    dicen que decidio recorrer el filtro por nombre. Si una hoja falta manana,
    comparar ambos dice de quien fue el cambio.
    """
    TREES_DIR.mkdir(parents=True, exist_ok=True)
    path = TREES_DIR / f"{date_str}__{retailer}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump({"tree": tree, "leaves": leaves}, fh, ensure_ascii=False)
    return path


def load_tree(retailer, date_str):
    """{"tree", "leaves"} o None si ese dia no se guardo."""
    path = TREES_DIR / f"{date_str}__{retailer}.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)
