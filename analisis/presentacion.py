"""Lectura de la presentacion comercial desde el nombre, para el cruce.

canasta/normalize.py:parse_quantity ya deja net_quantity/unit en el CSV
diario y NO se toca (la recoleccion no cambia). Este modulo agrega, sobre el
nombre ya guardado, lo que el cruce necesita y el parser no marca
(docs: Metodologia de estandarizacion, seccion 6):

- unidad suelta: "x unid", "x und.", "x un" = 1 unidad; "x atado" = 1 atado
  (un atado no tiene peso de referencia y no se convierte).
- peso aproximado: "Pack x 250g aprox." se usa con la marca `peso_aprox`.
- gramaje ambiguo: dos medidas distintas en el nombre sin ser "900ml + 100ml"
  ("Sazonador 90g ... Sobre 100 g"). parse_quantity toma la mayor sin
  avisar; aqui la fila se aparta del cruce.
- contenido neto declarado por la cadena ("280g", "5Kg", "500 ML"). Una cifra
  sin unidad ("300") no se interpreta.
- linea de producto: el nombre sin cantidades ni envases, para agrupar los
  tamanos de un mismo producto.
- parecido de nombres (Jaccard de palabras), para la regla de equivalencia.
"""

import re
import unicodedata

from canasta.normalize import _TO_BASE, _base_measures

_UNIDAD_SUELTA = re.compile(r"\b(?:x|por)\s*(?:unid|unidad|und|un)\b\.?", re.IGNORECASE)
_ATADO = re.compile(r"\batado\b", re.IGNORECASE)
_APROX = re.compile(r"\baprox", re.IGNORECASE)
_MAS = re.compile(r"\d\s*(?:kg|g|gr|ml|l|lt|cc)\s*\+\s*\d", re.IGNORECASE)
_DECLARADO = re.compile(
    r"^\s*(\d+(?:[.,]\d+)?)\s*(kilos?|kg|gramos|gra|gr|g|litros?|lts?|l|ml|cc)\s*\.?\s*$",
    re.IGNORECASE,
)

_ENVASES = (
    r"bolsa|botella|pote|frasco|lata|caja|doypack|sobre|sobres|paquete|envase|empaque|"
    r"display|taper|tarro|bandeja|galonera|bidon|sachet|pack|sixpack|x|de|con|en|y|la|el"
)
_CANTIDAD = (
    r"\d+(?:[.,]\d+)?\s*(?:kilos?|kg|gramos|gra|gr|g|litros?|lts?|l|ml|cc|un|und|unid|"
    r"unidades)?\b"
)


def plano(texto):
    nfkd = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def unidad_suelta(nombre):
    """"unidad" | "atado" | None. Solo cuando el nombre no trae otra cantidad."""
    if _ATADO.search(nombre or ""):
        return "atado"
    if _UNIDAD_SUELTA.search(nombre or ""):
        return "unidad"
    return None


def peso_aprox(nombre):
    return bool(_APROX.search(nombre or ""))


def gramaje_ambiguo(nombre):
    """Dos medidas de peso o volumen distintas que no son contenido + regalo."""
    medidas = {(round(q, 6), u) for q, u in _base_measures(nombre or "") if u != "un"}
    if len(medidas) < 2:
        return False
    return not _MAS.search(nombre or "")


def contenido_declarado(texto):
    """(cantidad_base, unidad_base) desde "280g", "5Kg", "500 ML"; o (None, None)."""
    m = _DECLARADO.match(str(texto or ""))
    if not m:
        return None, None
    base = _TO_BASE.get(m.group(2).lower())
    if not base or base[0] == "un":
        return None, None
    try:
        q = float(m.group(1).replace(",", ".")) * base[1]
    except ValueError:
        return None, None
    return (round(q, 6), base[0]) if q > 0 else (None, None)


def palabras(nombre, marca=""):
    """Palabras del nombre sin cantidades, envases ni la marca."""
    t = plano(nombre)
    t = re.sub(_CANTIDAD, " ", t)
    t = re.sub(r"[^a-z ]", " ", t)
    fuera = set(plano(marca).split())
    return {w for w in t.split() if len(w) > 1 and w not in fuera
            and not re.fullmatch(_ENVASES, w)}


def linea(nombre):
    """Clave de linea de producto: mismo producto en otro tamano."""
    return " ".join(sorted(palabras(nombre)))


def jaccard(a, b):
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)
