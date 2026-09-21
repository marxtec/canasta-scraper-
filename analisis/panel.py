"""Carga del panel acumulado y utilidades comunes a todos los bloques.

Es el unico modulo del paquete que sabe donde viven los CSV. El resto recibe
un DataFrame y no toca el disco mas que para escribir sus derivados.

Convenciones que comparten todos los scripts:

- `fecha` sale del NOMBRE del archivo (YYYY-MM-DD__cadena.csv), no del
  timestamp de la fila: es el dia de la corrida en hora de Lima y es lo que
  define "un dia del panel".
- Un precio 0 o vacio NO es un precio observado: se convierte en NaN. En el
  panel actual todas las filas con precio 0 vienen con available=False, es
  decir, son agotados que la cadena sigue listando.
- `on_sale` es el cartel de la tienda (regular_price > price), tal cual lo
  guardo el scraper. La version depurada es `oferta_real` (bloque 1).
- Los derivados van a data/derived/ con sufijo __<ultima_fecha>. Al escribir
  se borran las versiones anteriores del mismo derivado: correr dos veces el
  mismo dia deja exactamente un archivo, y correr manana lo reemplaza. La
  historia queda en git, no en el directorio.
"""

import datetime as dt
import pathlib
import re
import sys
import unicodedata

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAILY_DIR = ROOT / "data" / "daily"
TREES_DIR = ROOT / "data" / "trees"
DERIVED_DIR = ROOT / "data" / "derived"
FIGURAS_DIR = DERIVED_DIR / "figuras"

# Grupo corporativo de cada cadena. Es la variable independiente del
# proyecto: la pregunta es si el precio se fija por cadena, por grupo o por
# competencia.
GRUPOS = {
    "metro": "cencosud",
    "wong": "cencosud",
    "plaza_vea": "spsa",
    "vivanda": "spsa",
    "tottus": "falabella",
}
CADENAS = list(GRUPOS)

# Los EAN de fabricante tienen 12 o 13 digitos (UPC-A / EAN-13). Los codigos
# mas cortos que aparecen en el panel (6, 8, 11) son internos de cada cadena y
# no identifican al mismo producto fisico en otra. Es el mismo criterio con el
# que el README midio los 917 productos en 5 cadenas.
EAN_MIN_DIGITOS = 12

# Prefijo GS1 20-29: "distribucion restringida", lo asigna la tienda y no el
# fabricante (marca propia, granel, panaderia). Metro y Wong comparten esos
# codigos porque comparten catalogo Cencosud, asi que SI son el mismo
# producto dentro del grupo, pero entre grupos distintos una coincidencia es
# casualidad. Para la pregunta "mismo producto fisico" se excluyen por
# defecto; `--incluir-internos` replica el criterio de la bitacora del 20-09.
EAN_PREFIJO_INTERNO = "2"

_NOMBRE_DERIVADO = re.compile(r"^(?P<stem>.+?)__(?P<fecha>\d{4}-\d{2}-\d{2})\.(?P<ext>[a-z.]+)$")


def configurar_stdout():
    """UTF-8 en la consola de Windows, para que las tildes no revienten."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Descubrimiento de dias
# ---------------------------------------------------------------------------

def archivos_diarios(retailers=None):
    """[(fecha_iso, cadena, ruta)] de todo lo que hay en data/daily/."""
    out = []
    for path in sorted(DAILY_DIR.glob("*__*.csv")):
        fecha, cadena = path.name[:-4].split("__", 1)
        if retailers and cadena not in retailers:
            continue
        out.append((fecha, cadena, path))
    return out


def dias_disponibles(retailers=None):
    """Fechas ISO ordenadas con al menos una cadena recolectada."""
    return sorted({f for f, _, _ in archivos_diarios(retailers)})


def ultima_fecha(retailers=None):
    dias = dias_disponibles(retailers)
    if not dias:
        raise SystemExit("No hay CSV en data/daily/. Corre collect.py primero.")
    return dias[-1]


# ---------------------------------------------------------------------------
# Calendario
# ---------------------------------------------------------------------------

def _pascua(anio):
    """Domingo de Pascua (algoritmo de Meeus/Jones/Butcher)."""
    a = anio % 19
    b, c = divmod(anio, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    mes, dia = divmod(h + m - 7 * n + 114, 31)
    return dt.date(anio, mes, dia + 1)


# Feriados nacionales del Peru con fecha fija (Ley 29459 y modificatorias).
_FERIADOS_FIJOS = {
    (1, 1), (5, 1), (6, 7), (6, 29), (7, 23), (7, 28), (7, 29), (8, 6),
    (8, 30), (10, 8), (11, 1), (12, 8), (12, 9), (12, 25),
}


def es_feriado(fecha):
    fecha = pd.Timestamp(fecha).date()
    if (fecha.month, fecha.day) in _FERIADOS_FIJOS:
        return True
    pascua = _pascua(fecha.year)
    return fecha in (pascua - dt.timedelta(days=3), pascua - dt.timedelta(days=2))


def es_dia_habil(fecha):
    """Lunes a viernes que no es feriado nacional.

    Los supermercados hacen repricing en dias habiles; un sabado o un
    domingo miden otra cosa. Bloque 2 separa las transiciones con esto.
    """
    fecha = pd.Timestamp(fecha).date()
    return fecha.weekday() < 5 and not es_feriado(fecha)


# ---------------------------------------------------------------------------
# Categorias comunes entre cadenas
# ---------------------------------------------------------------------------

def _plano(texto):
    nfkd = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


# Cada cadena nombra sus raices a su manera ("Lacteos", "Lacteos y Huevos",
# "Lacteos y Quesos"). Para la descomposicion de varianza hace falta una
# categoria que signifique lo mismo en las cinco. El orden importa: la primera
# regla que encaja gana.
_REGLAS_CATEGORIA = [
    ("abarrote", "abarrotes"),
    ("cervez", "licores"), ("vino", "licores"), ("licor", "licores"),
    ("alcohol", "licores"),
    ("desayuno", "desayuno"),
    ("lacteo", "lacteos"),
    ("huevo", "lacteos"),       # "Lacteos y Huevos", "Huevos y Fiambres"
    ("queso", "quesos_fiambres"), ("fiambre", "quesos_fiambres"),
    ("embutido", "quesos_fiambres"),
    ("agua", "bebidas"), ("bebida", "bebidas"), ("gaseosa", "bebidas"),
    ("carne", "carnes"), ("pescado", "carnes"), ("parrill", "carnes"),
    ("fruta", "frutas_verduras"), ("verdura", "frutas_verduras"),
    ("panader", "panaderia"), ("pastel", "panaderia"), ("reposter", "panaderia"),
    ("congelado", "congelados"),
    ("dulce", "snacks_dulces"), ("galleta", "snacks_dulces"),
    ("snack", "snacks_dulces"),
    ("comida", "preparados"), ("rostizado", "preparados"),
    ("plato", "preparados"),
    ("saludable", "saludable"),
    ("pack", "packs"),
]


def categoria_comun(nombre):
    plano = _plano(nombre)
    for clave, comun in _REGLAS_CATEGORIA:
        if clave in plano:
            return comun
    return "otros"


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------

_DTYPES = {
    "retailer": "string", "product_id": "string", "item_id": "string",
    "product_name": "string", "brand": "string", "category": "string",
    "category_path": "string", "unit": "string", "ean": "string",
    "url": "string", "seller_name": "string", "promo_type": "string",
}


def _a_bool(serie):
    """'True'/'False'/vacio -> True/False/None (object, no bool numpy)."""
    return serie.map({"True": True, "False": False}).astype(object).where(
        serie.isin(["True", "False"]), None
    )


def cargar_panel(retailers=None, dias=None, columnas=None):
    """Todo data/daily/ en un DataFrame, un dia = una fecha del nombre.

    `precio_valido` marca las filas con price > 0. Los scripts que necesitan
    un precio observado filtran por esa columna; los que cuentan huecos no.
    """
    partes = []
    for fecha, cadena, path in archivos_diarios(retailers):
        if dias and fecha not in dias:
            continue
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""],
                         usecols=columnas, encoding="utf-8")
        df["fecha"] = pd.Timestamp(fecha)
        partes.append(df)
    if not partes:
        return pd.DataFrame()
    panel = pd.concat(partes, ignore_index=True)

    for col in ("price", "regular_price", "card_price", "net_quantity"):
        if col in panel:
            panel[col] = pd.to_numeric(panel[col], errors="coerce")
    if "price" in panel:
        panel.loc[panel["price"] <= 0, "price"] = np.nan
        panel["precio_valido"] = panel["price"].notna()
    if "on_sale" in panel:
        panel["on_sale"] = panel["on_sale"].eq("True")
    if "available" in panel:
        panel["available"] = _a_bool(panel["available"])
    if "category" in panel:
        panel["categoria_comun"] = panel["category"].map(categoria_comun)
    if "retailer" in panel:
        panel["grupo"] = panel["retailer"].map(GRUPOS)
    if "ean" in panel:
        panel["ean"] = panel["ean"].where(panel["ean"].notna(), None)
    for col, tipo in _DTYPES.items():
        if col in panel:
            panel[col] = panel[col].astype(tipo)
    return panel


def ean_comparable(serie, incluir_internos=False):
    """Mascara: EAN de fabricante, util para emparejar entre cadenas."""
    s = serie.fillna("").astype(str)
    ok = s.str.fullmatch(r"\d+") & (s.str.len() >= EAN_MIN_DIGITOS)
    if not incluir_internos:
        ok &= ~s.str.startswith(EAN_PREFIJO_INTERNO)
    return ok


def dias_por_cadena(panel):
    """{cadena: [fechas ordenadas]} de lo que hay en el panel cargado."""
    return {c: sorted(g["fecha"].unique()) for c, g in panel.groupby("retailer")}


# ---------------------------------------------------------------------------
# Escritura de derivados
# ---------------------------------------------------------------------------

def _borrar_versiones_anteriores(stem, ext, directorio):
    for viejo in directorio.glob(f"{stem}__*.{ext}"):
        m = _NOMBRE_DERIVADO.match(viejo.name)
        if m and m.group("stem") == stem:
            viejo.unlink()


def ruta_derivado(stem, fecha, ext="csv", directorio=None):
    """data/derived/<stem>__<fecha>.<ext>, borrando versiones anteriores."""
    directorio = directorio or DERIVED_DIR
    directorio.mkdir(parents=True, exist_ok=True)
    _borrar_versiones_anteriores(stem, ext, directorio)
    return directorio / f"{stem}__{fecha}.{ext}"


def escribir_csv(df, stem, fecha, directorio=None):
    path = ruta_derivado(stem, fecha, "csv", directorio)
    df.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
    return path


def escribir_texto(texto, stem, fecha, ext="tex", directorio=None):
    path = ruta_derivado(stem, fecha, ext, directorio)
    path.write_text(texto, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Presentacion
# ---------------------------------------------------------------------------

def aviso_fiabilidad(n_dias, minimo, que):
    """Frase homogenea para decir si un resultado es solido o provisional."""
    if n_dias >= minimo:
        return f"SOLIDO: {que} medido sobre {n_dias} dias (minimo {minimo})."
    return (f"PROVISIONAL: {que} medido sobre {n_dias} dias; harian falta "
            f"{minimo - n_dias} dias mas (minimo {minimo}) para publicarlo.")


def tabla(df, floatfmt="{:.3f}"):
    """DataFrame -> texto alineado, sin dependencias extra."""
    if df.empty:
        return "(vacio)"
    d = df.copy()
    for col in d.columns:
        if pd.api.types.is_float_dtype(d[col]):
            d[col] = d[col].map(lambda v: "" if pd.isna(v) else floatfmt.format(v))
    return d.to_string(index=False)


def pct(x, decimales=1):
    return "" if pd.isna(x) else f"{100 * x:.{decimales}f}%"
