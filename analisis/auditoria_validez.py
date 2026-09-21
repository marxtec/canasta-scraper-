"""Bloque 4 -- auditoria de validez: ¿el precio de la API es el que ve el comprador?

    python -m analisis.auditoria_validez                 # ultimo dia, 50 por cadena
    python -m analisis.auditoria_validez --n 10          # muestra chica
    python -m analisis.auditoria_validez --fecha 2026-09-20 --cadenas metro wong
    python -m analisis.auditoria_validez --solo-muestra  # imprime la muestra, sin navegador

El proyecto lee el JSON interno de cada cadena (API scraping), no el HTML
renderizado. La objecion "eso no es scraping de verdad" tiene una respuesta
empirica: abrir la ficha con un navegador y comparar lo que se ve en
pantalla con lo que guardo el CSV. Esto lo convierte en procedimiento
medible y acumulable, en vez de una verificacion a mano de tres casos.

Diseño:

- Muestra aleatoria REPRODUCIBLE (SEMILLA fija, guardada en cada fila) de N
  productos por cadena del dia indicado (por defecto el mas reciente),
  estratificada por categoria comun y por on_sale con asignacion
  proporcional (al menos 1 por estrato mientras alcance N).
- Cada ficha se abre con Playwright reutilizando `canasta.browser.navegador`
  (el mismo arranque que usa --card-prices) y con el User-Agent academico
  del proyecto. Se respeta el rate limit de config/retailers.yml.
- De la pantalla se leen: los precios visibles en el bloque del producto
  (texto entre el nombre y el boton de agregar / productos relacionados),
  el precio de tarjeta si lo hay, y el vendedor mostrado ("Vendido y
  despachado por"). Del HTML se lee ademas el JSON-LD Product (precio y
  vendedor estructurados), como segunda fuente.
- Se compara contra price, regular_price y card_price del CSV:
  `concuerda_price` = el precio que paga el comprador en pantalla (el menor
  visible, sin contar tarjeta) es EXACTAMENTE price. Las discrepancias se
  guardan con su magnitud. `regular_visible` y `card_visible` dicen si esos
  otros dos aparecen en pantalla.
- Un navegador que no puede leer (Tottus bloquea el renderizado a ratos)
  NO es un fallo de la auditoria: es un resultado y se registra como
  estado='bloqueado'.
- El vendedor mostrado se compara con seller_name del CSV: si es un tercero
  del marketplace, es el riesgo de contaminacion del Modelo A.
- Los resultados se acumulan en validez_acumulado.csv (reemplazando las
  filas de la misma fecha de auditoria: correr dos veces el mismo dia no
  duplica), y el resumen se reporta sobre la corrida y sobre el acumulado.

Limite que hay que tener presente: la comparacion solo es valida si la
ficha se abre EL MISMO DIA del CSV. Si la auditoria corre otro dia, la
columna `mismo_dia` sale False y las discrepancias no se atribuyen a la
API sino al paso del tiempo; el resumen las separa.
"""

import argparse
import datetime as dt
import json
import logging
import re
import time

import numpy as np
import pandas as pd
import yaml

from analisis import panel as P
from canasta import browser
from canasta.vtex import USER_AGENT

SEMILLA = 20260921            # fija: la muestra de un dia es siempre la misma
N_POR_CADENA = 50
ESPERA_MS = 6000              # lo que tarda VTEX en pintar el precio
TIMEOUT_MS = 60000
TOL = 0.005                   # soles: exacto al centimo
MIN_TEXTO_LEIDO = 300         # menos que esto es un DOM vacio (bloqueo)
AUDIT_DIR = P.DERIVED_DIR / "auditoria"
ACUMULADO = AUDIT_DIR / "validez_acumulado.csv"

# La ficha publica de Vivanda vive en el front Next.js; el CSV guarda la URL
# del host canonico de VTEX, que a un navegador le pide login.
HOST_PUBLICO = {"vivanda.vtexcommercestable.com.br": "www.vivanda.com.pe"}

_RX_PRECIO = re.compile(r"S/\s*([\d]{1,5}(?:[.,]\d{1,2})?)\b")
_RX_BLOQUEO = re.compile(
    r"access denied|attention required|captcha|verify you are human|"
    r"request blocked|too many requests|403 forbidden", re.IGNORECASE)
# Donde termina el bloque del producto en el texto renderizado. A partir de
# aqui los precios son de OTROS productos (relacionados, carrusel).
_RX_FIN_BLOQUE = re.compile(
    r"Productos similares|Podr[ií]an interesarte|Ll[eé]valos juntos|"
    r"Tambi[eé]n podr[ií]a|Caracter[ií]sticas Principales|Especificaciones|"
    r"M[eé]todos de entrega|Tipo de entrega|Descripci[oó]n", re.IGNORECASE)
_RX_VENDEDOR = re.compile(
    r"Vendido (?:y (?:despachado|entregado) )?por:?\s*\n?\s*([^\n|]{2,60})", re.IGNORECASE)
_RX_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)

COLUMNAS = [
    "fecha_csv", "fecha_auditoria", "mismo_dia", "semilla", "retailer", "item_id",
    "product_name", "categoria_comun", "on_sale", "url_abierta", "estado",
    "price", "regular_price", "card_price", "seller_name",
    "precio_pantalla", "precios_visibles", "precio_tarjeta_pantalla",
    "precio_ldjson", "vendedor_pantalla", "vendedor_ldjson",
    "concuerda_price", "magnitud_discrepancia", "regular_visible", "card_visible",
    "precio_menor_visible", "vendedor_es_cadena", "segundos", "nota",
]


# ---------------------------------------------------------------------------
# Muestra
# ---------------------------------------------------------------------------

def muestra(df, n=N_POR_CADENA, semilla=SEMILLA):
    """N filas por cadena, estratificadas por (categoria_comun, on_sale)."""
    rng = np.random.default_rng(semilla)
    partes = []
    for cad, g in df.groupby("retailer", sort=True):
        g = g[g["url"].notna() & g["price"].notna()].copy()
        if g.empty:
            continue
        g["_estrato"] = g["categoria_comun"].astype(str) + "|" + g["on_sale"].astype(str)
        tam = g["_estrato"].value_counts().sort_index()
        n_cad = min(n, len(g))
        cuota = (tam / tam.sum() * n_cad)
        base = np.floor(cuota).astype(int)
        if n_cad >= len(tam):
            base = base.clip(lower=1)
        resto = n_cad - base.sum()
        if resto > 0:
            frac = (cuota - np.floor(cuota)).sort_values(ascending=False)
            for est in frac.index[:resto]:
                base[est] += 1
        elif resto < 0:
            for est in base.sort_values(ascending=False).index[:-resto]:
                base[est] -= 1
        for est, k in base.items():
            k = min(int(k), int(tam[est]))
            if k <= 0:
                continue
            idx = g.index[g["_estrato"] == est]
            elegidos = rng.choice(idx, size=k, replace=False)
            partes.append(g.loc[elegidos])
    if not partes:
        return df.iloc[:0]
    out = pd.concat(partes).drop(columns="_estrato")
    out["semilla"] = semilla
    return out.sort_values(["retailer", "item_id"]).reset_index(drop=True)


def url_publica(url):
    for host, publico in HOST_PUBLICO.items():
        url = url.replace(host, publico)
    return url


# ---------------------------------------------------------------------------
# Lectura de la ficha
# ---------------------------------------------------------------------------

def _a_float(s):
    try:
        return float(s.replace(",", "."))
    except (ValueError, AttributeError):
        return None


def bloque_producto(texto, nombre):
    """Texto de la ficha desde el nombre del producto hasta el fin del bloque.
    None si el nombre no aparece."""
    if not texto or not nombre:
        return None
    clave = nombre.strip()[:30].lower()
    bajo = texto.lower()
    primero, pos = None, bajo.find(clave)
    # El nombre puede aparecer varias veces (migas de pan, descripcion,
    # bloque de compra): se toma el primer bloque que tenga un precio.
    while pos >= 0:
        resto = texto[pos + len(clave):]
        fin = _RX_FIN_BLOQUE.search(resto)
        bloque = resto[: fin.start()] if fin else resto[:1500]
        if _RX_PRECIO.search(bloque):
            return bloque
        primero = bloque if primero is None else primero
        pos = bajo.find(clave, pos + 1)
    return primero


def precios_ldjson(html):
    """(precio, vendedor) del JSON-LD Product, si existe y es coherente."""
    for raw in _RX_LD.findall(html or ""):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for d in items:
            if not isinstance(d, dict) or d.get("@type") != "Product":
                continue
            offers = d.get("offers") or {}
            lista = offers if isinstance(offers, list) else [offers]
            if isinstance(offers, dict) and isinstance(offers.get("offers"), list):
                lista = offers["offers"]
            precio, vendedor = None, None
            for o in lista:
                if not isinstance(o, dict):
                    continue
                p = _a_float(str(o.get("price") or o.get("lowPrice") or ""))
                if p and p > 0:
                    precio = p if precio is None else min(precio, p)
                s = o.get("seller")
                if isinstance(s, dict) and s.get("name") and not vendedor:
                    vendedor = s["name"]
            if precio is None and isinstance(offers, dict):
                precio = _a_float(str(offers.get("lowPrice") or ""))
                if precio == 0:
                    precio = None
            return precio, vendedor
    return None, None


def leer_ficha(texto, html, nombre):
    """Interpreta lo que muestra la ficha. Devuelve un dict con las
    columnas de pantalla y el estado ('ok', 'bloqueado', 'sin_producto')."""
    out = {"precio_pantalla": None, "precios_visibles": "", "precio_tarjeta_pantalla": None,
           "precio_ldjson": None, "vendedor_pantalla": None, "vendedor_ldjson": None,
           "estado": "ok", "nota": ""}
    texto = texto or ""
    if len(texto.strip()) < MIN_TEXTO_LEIDO or _RX_BLOQUEO.search(texto[:3000]):
        out["estado"] = "bloqueado"
        out["nota"] = f"texto de {len(texto.strip())} caracteres"
        return out
    out["precio_ldjson"], out["vendedor_ldjson"] = precios_ldjson(html)
    bloque = bloque_producto(texto, nombre)
    if bloque is None:
        out["estado"] = "sin_producto"
        out["nota"] = "el nombre del producto no aparece en la pantalla"
        return out
    tarjeta = browser._parse_card(bloque)
    out["precio_tarjeta_pantalla"] = tarjeta
    visibles = [v for v in (_a_float(x) for x in _RX_PRECIO.findall(bloque)) if v]
    sin_tarjeta = [v for v in visibles if tarjeta is None or abs(v - tarjeta) >= TOL]
    out["precios_visibles"] = ";".join(f"{v:.2f}" for v in visibles[:6])
    if sin_tarjeta:
        out["precio_pantalla"] = min(sin_tarjeta[:3])
    elif "sin stock" in bloque.lower() or "agotado" in bloque.lower():
        out["nota"] = "sin stock en pantalla"
    m = _RX_VENDEDOR.search(texto)
    if m:
        out["vendedor_pantalla"] = m.group(1).strip()
    return out


def comparar(fila, lectura):
    """Columnas de concordancia CSV vs pantalla.

    concuerda_price: el price del CSV aparece EXACTO entre los precios del
    bloque del producto. Si no, magnitud_discrepancia es la distancia al
    precio visible mas cercano. Un precio visible MENOR que price cuando
    price tambien esta en pantalla es un precio con tarjeta (Wong lo pinta
    sin etiqueta de texto): se registra en `precio_menor_visible` y se
    contrasta con card_price; si el CSV no tiene card_price, la nota lo
    dice, porque significa que la cobertura de card_price esta subestimada.
    """
    def _num(x):
        return None if x is None or pd.isna(x) or x <= 0 else float(x)

    price, regular, card = _num(fila.get("price")), _num(fila.get("regular_price")), _num(fila.get("card_price"))
    vis = [float(x) for x in lectura["precios_visibles"].split(";") if x]
    pp = lectura["precio_pantalla"]
    out = {"concuerda_price": None, "magnitud_discrepancia": None,
           "regular_visible": None, "card_visible": None, "vendedor_es_cadena": None,
           "precio_menor_visible": None}
    if lectura["estado"] != "ok" or pp is None or not price:
        return out
    out["concuerda_price"] = any(abs(v - price) < TOL for v in vis)
    if not out["concuerda_price"]:
        cercano = min(vis, key=lambda v: abs(v - price))
        out["magnitud_discrepancia"] = cercano / price - 1
    if regular and abs(regular - price) >= TOL:
        out["regular_visible"] = any(abs(v - regular) < TOL for v in vis)
    menores = [v for v in vis if v < price - TOL]
    tarjeta = lectura["precio_tarjeta_pantalla"]
    if out["concuerda_price"] and menores:
        out["precio_menor_visible"] = min(menores)
        if tarjeta is None:
            tarjeta = min(menores)
            lectura["nota"] = (lectura["nota"] + "; " if lectura["nota"] else "") + \
                "precio menor sin etiqueta de tarjeta"
    if card:
        out["card_visible"] = tarjeta is not None and abs(tarjeta - card) < TOL
    elif tarjeta is not None:
        lectura["nota"] = (lectura["nota"] + "; " if lectura["nota"] else "") + \
            f"tarjeta en pantalla ({tarjeta:.2f}) sin card_price en el CSV"
    vendedor = lectura["vendedor_pantalla"] or lectura["vendedor_ldjson"]
    if vendedor:
        out["vendedor_es_cadena"] = _es_cadena(vendedor, fila["retailer"])
    return out


_ALIAS_CADENA = {
    "metro": ("metro", "cencosud"), "wong": ("wong", "cencosud"),
    "plaza_vea": ("plaza vea", "plazavea", "supermercados peruanos"),
    "vivanda": ("vivanda", "supermercados peruanos", "food retail"),
    "tottus": ("tottus", "falabella", "hipermercados tottus"),
}


def _es_cadena(vendedor, retailer):
    v = P._plano(vendedor)
    return any(a in v for a in _ALIAS_CADENA.get(retailer, (retailer,)))


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------

def _rate_limit():
    try:
        with open(P.ROOT / "config" / "retailers.yml", encoding="utf-8") as fh:
            return float(yaml.safe_load(fh).get("rate_limit_seconds", 1.5))
    except Exception:
        return 1.5


def auditar(muestra_df, fecha_csv, log, espera_ms=ESPERA_MS, timeout_ms=TIMEOUT_MS):
    hoy = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date().isoformat()
    pausa = _rate_limit()
    filas = []
    with browser.navegador(user_agent=USER_AGENT) as ctx:
        for i, (_, r) in enumerate(muestra_df.iterrows(), 1):
            url = url_publica(str(r["url"]))
            pagina = ctx.new_page()
            t0 = time.time()
            texto, html, error = "", "", None
            try:
                pagina.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                pagina.wait_for_timeout(espera_ms)
                texto = pagina.inner_text("body")
                html = pagina.content()
            except Exception as exc:          # una ficha caida no tumba el lote
                error = type(exc).__name__
            finally:
                pagina.close()
            seg = time.time() - t0
            if error:
                lectura = {"precio_pantalla": None, "precios_visibles": "",
                           "precio_tarjeta_pantalla": None, "precio_ldjson": None,
                           "vendedor_pantalla": None, "vendedor_ldjson": None,
                           "estado": f"error:{error}", "nota": ""}
            else:
                lectura = leer_ficha(texto, html, r["product_name"])
            comp = comparar(r, lectura)
            filas.append({
                "fecha_csv": fecha_csv, "fecha_auditoria": hoy,
                "mismo_dia": fecha_csv == hoy, "semilla": r["semilla"],
                "retailer": r["retailer"], "item_id": r["item_id"],
                "product_name": r["product_name"], "categoria_comun": r["categoria_comun"],
                "on_sale": bool(r["on_sale"]), "url_abierta": url,
                "price": r["price"], "regular_price": r["regular_price"],
                "card_price": r["card_price"], "seller_name": r["seller_name"],
                **lectura, **comp, "segundos": round(seg, 1),
            })
            log.info("%s %d/%d %-12s %-10s pantalla=%s csv=%s %s",
                     r["retailer"], i, len(muestra_df), lectura["estado"],
                     ("OK" if comp["concuerda_price"] else "DIFIERE")
                     if comp["concuerda_price"] is not None else "-",
                     lectura["precio_pantalla"], r["price"], str(r["product_name"])[:40])
            time.sleep(pausa)
    return pd.DataFrame(filas, columns=COLUMNAS)


def acumular(nuevo, path=ACUMULADO):
    """Reemplaza en el acumulado las filas de la misma fecha de auditoria."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        viejo = pd.read_csv(path, dtype={"item_id": str}, encoding="utf-8")
        viejo = viejo[~viejo["fecha_auditoria"].isin(nuevo["fecha_auditoria"].unique())]
        todo = pd.concat([viejo, nuevo], ignore_index=True)
    else:
        todo = nuevo
    todo = todo.sort_values(["fecha_auditoria", "retailer", "item_id"]).reset_index(drop=True)
    todo.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
    return todo


def resumen(df):
    """Concordancia por cadena (y total). Solo cuenta filas del mismo dia
    para la concordancia; las demas se reportan aparte."""
    filas = []
    grupos = list(df.groupby("retailer")) + [("TOTAL", df)]
    for cad, g in grupos:
        leidas = g[g["estado"] == "ok"]
        mismo = leidas[leidas["mismo_dia"].astype(bool)]
        ev = mismo[mismo["concuerda_price"].notna()]
        disc = ev[~ev["concuerda_price"].astype(bool)]
        vend = leidas[leidas["vendedor_es_cadena"].notna()]
        filas.append({
            "cadena": cad, "fichas": len(g),
            "leidas": len(leidas),
            "bloqueadas": int((g["estado"] == "bloqueado").sum()),
            "sin_producto": int((g["estado"] == "sin_producto").sum()),
            "errores": int(g["estado"].astype(str).str.startswith("error").sum()),
            "mismo_dia": len(mismo),
            "evaluables": len(ev),
            "concordancia_exacta": ev["concuerda_price"].astype(bool).mean() if len(ev) else np.nan,
            "discrepancias": len(disc),
            "magnitud_mediana": disc["magnitud_discrepancia"].abs().median() if len(disc) else np.nan,
            "regular_visible": (leidas["regular_visible"].dropna().astype(bool).mean()
                                if leidas["regular_visible"].notna().any() else np.nan),
            "card_visible": (leidas["card_visible"].dropna().astype(bool).mean()
                             if leidas["card_visible"].notna().any() else np.nan),
            "con_vendedor": len(vend),
            "vendedor_tercero": (~vend["vendedor_es_cadena"].astype(bool)).mean() if len(vend) else np.nan,
            "tarjeta_sin_dato": int(leidas["nota"].astype(str).str.contains("sin card_price").sum()),
            "distinto_dia": int((~leidas["mismo_dia"].astype(bool)).sum()),
        })
    return pd.DataFrame(filas)


def _imprimir(titulo, res):
    print(f"--- {titulo} ---")
    v = res.copy()
    for c in ("concordancia_exacta", "magnitud_mediana", "regular_visible", "card_visible",
              "vendedor_tercero"):
        v[c] = v[c].map(P.pct)
    print(P.tabla(v))
    print()


def correr(fecha=None, n=N_POR_CADENA, semilla=SEMILLA, cadenas=None, solo_muestra=False,
           espera_ms=ESPERA_MS, timeout_ms=TIMEOUT_MS):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("auditoria")
    fecha = fecha or P.ultima_fecha()
    df = P.cargar_panel(retailers=cadenas, dias=[fecha])
    if df.empty:
        raise SystemExit(f"No hay CSV para {fecha}.")
    m = muestra(df, n, semilla)
    print(f"=== auditoria de validez | CSV del {fecha} | {len(m)} fichas "
          f"({n} por cadena, semilla {semilla}) ===")
    print(m.groupby(["retailer", "on_sale"]).size().unstack(fill_value=0).to_string())
    if solo_muestra:
        return m
    res = auditar(m, fecha, log, espera_ms, timeout_ms)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    hoy = res["fecha_auditoria"].iloc[0]
    res.to_csv(AUDIT_DIR / f"validez__{hoy}.csv", index=False, encoding="utf-8",
               lineterminator="\n")
    todo = acumular(res)
    print()
    _imprimir(f"corrida {hoy} (CSV del {fecha})", resumen(res))
    _imprimir(f"acumulado ({todo['fecha_auditoria'].nunique()} corridas, {len(todo)} fichas)",
              resumen(todo))
    resumen(todo).to_csv(AUDIT_DIR / "validez_resumen.csv", index=False, encoding="utf-8",
                         lineterminator="\n")
    if not res["mismo_dia"].all():
        print("AVISO: la auditoria corrio en un dia distinto al del CSV; las discrepancias\n"
              "de esas fichas no se atribuyen a la API (mismo_dia=False) y no entran\n"
              "en la concordancia. Correr el mismo dia que la recoleccion.")
    bloq = res[res["estado"] == "bloqueado"]
    if not bloq.empty:
        print(f"{len(bloq)} fichas bloqueadas por la cadena ({', '.join(sorted(bloq['retailer'].unique()))}):"
              " es un resultado de la auditoria, no un fallo.")
    print(f"-> {AUDIT_DIR / f'validez__{hoy}.csv'}  y  {ACUMULADO}")
    return res


def main(argv=None):
    P.configurar_stdout()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--fecha", help="dia del CSV a auditar (def. el mas reciente)")
    ap.add_argument("--n", type=int, default=N_POR_CADENA, help=f"fichas por cadena (def. {N_POR_CADENA})")
    ap.add_argument("--semilla", type=int, default=SEMILLA)
    ap.add_argument("--cadenas", nargs="+", help="solo estas cadenas")
    ap.add_argument("--solo-muestra", action="store_true", help="no abre el navegador")
    ap.add_argument("--espera-ms", type=int, default=ESPERA_MS)
    ap.add_argument("--timeout-ms", type=int, default=TIMEOUT_MS)
    args = ap.parse_args(argv)
    correr(args.fecha, args.n, args.semilla, args.cadenas, args.solo_muestra,
           args.espera_ms, args.timeout_ms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
