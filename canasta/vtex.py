"""Cliente para la API publica de catalogo de VTEX.

Plaza Vea, Metro, Wong y Vivanda estan verificados sobre esta plataforma. El
endpoint /api/catalog_system/pub/products/search/ devuelve JSON con precio,
precio tachado, EAN y stock, asi que no hace falta parsear HTML.

Tres limitaciones de VTEX que condicionan el diseno de este cliente:

1. La paginacion revienta pasados ~2500 resultados por query. Por eso se
   recorre el arbol de categorias y se consulta hoja por hoja, en vez de
   pedir "todos los alimentos" de una.

2. El canal de venta (sc) valido NO es el mismo en todas las cuentas, y un
   canal que responde 200 puede estar vacio. Wong rechaza sc=1 con 401
   ("sc 1 is not available for account wongio") y con sc=2 responde 200 pero
   con catalogo vacio; el unico modo de obtener su surtido es omitir el
   parametro. Por eso sales_channel admite null y el cliente registra el
   cuerpo de los 4xx: VTEX casi siempre trae el diagnostico escrito ahi.

   CAVEAT METODOLOGICO: omitir sc deja a Wong en el canal por defecto de la
   cuenta, es decir sin fijar. Si VTEX lo cambia, la serie de Wong da un
   salto que no es inflacion y nada lo avisa. Vigilar con --audit.

3. No todas las cadenas exponen el catalogo en su dominio publico. Vivanda
   sirve HTML en toda ruta /api/ desde www.vivanda.com.pe; su catalogo vive
   en vivanda.vtexcommercestable.com.br. Por eso base_url es configurable y
   este cliente distingue "respondio HTML" de "fallo la red".
"""

import time
import unicodedata

import requests

USER_AGENT = (
    "canasta-lima/1.0 (proyecto academico Universidad del Pacifico; "
    "analitica de la web 2026-II)"
)

# Se reintenta solo lo que puede cambiar al repetir: cortes de red, limites de
# tasa y errores de servidor. Un 4xx es un error nuestro (canal invalido, ruta
# mal formada) y reintentarlo cuatro veces con backoff solo pierde ~14s por
# categoria y esconde el mensaje que explica el fallo.
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _strip_accents(text):
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def matches_food(name, keywords, excluded=()):
    """True si el nombre de categoria parece de alimentos."""
    plain = _strip_accents(name)
    if any(x in plain for x in excluded):
        return False
    return any(k in plain for k in keywords)


class VtexClient:
    def __init__(self, retailer, cfg, logger):
        self.name = retailer["name"]
        self.base_url = retailer["base_url"].rstrip("/")
        self.sales_channel = retailer.get("sales_channel", 1)
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

    def _get(self, url, params=None):
        """GET con reintentos y backoff exponencial.

        Devuelve None si agota los reintentos o si el error no es
        reintentable: un fallo en una categoria no debe tumbar la corrida
        entera del dia.
        """
        delay = 2.0
        for attempt in range(1, self.cfg["max_retries"] + 1):
            try:
                r = self.session.get(
                    url, params=params, timeout=self.cfg["request_timeout"]
                )
                # VTEX responde 206 Partial Content en consultas paginadas:
                # es exito, no error.
                if r.status_code in (200, 206):
                    ctype = r.headers.get("content-type", "")
                    if "json" not in ctype.lower():
                        # Sintoma tipico de una cadena cuyo dominio publico es
                        # un front SPA y no expone el catalogo legacy.
                        self.log.error(
                            "%s: %s respondio %s en vez de JSON. El catalogo no "
                            "esta en este host; revisar base_url.",
                            self.name, url, ctype or "(sin content-type)",
                        )
                        return None
                    return r.json()
                # 404 en una hoja vacia es normal, no vale reintentar
                if r.status_code == 404:
                    return []
                # El cuerpo de un 4xx de VTEX trae el diagnostico literal
                # (p.ej. "sc 1 is not available for account wongio"). Sin
                # registrarlo, el fallo es indistinguible de un corte de red.
                if r.status_code not in _RETRY_STATUS:
                    self.log.error(
                        "%s: HTTP %s no reintentable en %s -> %s",
                        self.name, r.status_code, url, r.text[:200].strip(),
                    )
                    return None
                self.log.warning(
                    "%s HTTP %s en %s (intento %d/%d)",
                    self.name, r.status_code, url, attempt, self.cfg["max_retries"],
                )
            except (requests.RequestException, ValueError) as exc:
                self.log.warning(
                    "%s fallo %s en %s (intento %d/%d)",
                    self.name, type(exc).__name__, url, attempt,
                    self.cfg["max_retries"],
                )
            if attempt < self.cfg["max_retries"]:
                time.sleep(delay)
                delay *= 2
        self.log.error("%s agoto reintentos en %s", self.name, url)
        return None

    def category_tree(self, depth=4):
        url = f"{self.base_url}/api/catalog_system/pub/category/tree/{depth}"
        return self._get(url) or []

    def leaf_categories(self, roots, keywords, excluded=()):
        """Devuelve [(fq, ruta_legible)] de las hojas bajo las categorias de
        alimentos.

        `fq` es el filtro listo para usar. VTEX NO acepta el id suelto en
        subcategorias: "C:950" devuelve 0 productos, hay que dar la ruta
        completa de ids, "C:/2/6/950/". Verificado contra Plaza Vea.

        Si `roots` trae ids, se usan esos. Si viene vacio -- que es el caso
        por defecto y el recomendado -- se filtran las categorias de primer
        nivel por nombre. Los ids de categoria no son comparables entre
        cadenas (Abarrotes es 431 en Plaza Vea, 1700 en Metro, 17 en Vivanda)
        y cambian cuando la cadena reordena su arbol; los nombres no.
        """
        tree = self.category_tree()
        self.last_tree = tree
        if not tree:
            self.log.error(
                "%s: el arbol de categorias vino vacio. Revisar base_url.",
                self.name,
            )
            return []

        if roots:
            selected = [n for n in tree if n.get("id") in roots]
            faltantes = set(roots) - {n.get("id") for n in selected}
            if faltantes:
                # Silenciar esto fue lo que dejo a Plaza Vea bajando 3
                # categorias de 13 sin que nadie lo notara.
                self.log.warning(
                    "%s: ids de food_categories que NO existen en el arbol: %s",
                    self.name, sorted(faltantes),
                )
        else:
            selected = [
                n for n in tree
                if matches_food(n.get("name", ""), keywords, excluded)
            ]
            descartadas = [
                n.get("name") for n in tree
                if not matches_food(n.get("name", ""), keywords, excluded)
            ]
            self.log.info(
                "%s: %d categorias de alimentos: %s",
                self.name, len(selected), [n.get("name") for n in selected],
            )
            self.log.debug("%s: descartadas: %s", self.name, descartadas)

        if not selected:
            self.log.error(
                "%s: ninguna categoria de primer nivel coincidio. "
                "Nombres disponibles: %s",
                self.name, [n.get("name") for n in tree][:20],
            )
            return []

        leaves = []

        def walk(node, names, ids):
            names = names + [node.get("name", "?")]
            ids = ids + [str(node.get("id"))]
            children = node.get("children") or []
            if children:
                for child in children:
                    walk(child, names, ids)
            else:
                fq = "C:/" + "/".join(ids) + "/"
                leaves.append((fq, " > ".join(names)))

        for node in selected:
            walk(node, [], [])
        self.last_leaves = leaves
        return leaves

    def products_in_category(self, fq):
        """Pagina una categoria hoja completa. `fq` es "C:/2/6/950/".
        Devuelve la lista de productos crudos, tal cual los entrega VTEX."""
        url = f"{self.base_url}/api/catalog_system/pub/products/search/"
        step = self.cfg["page_size"]
        out, offset = [], 0

        while offset < self.cfg["max_offset"]:
            params = {"fq": fq, "_from": offset, "_to": offset + step - 1}
            if self.sales_channel is not None:
                params["sc"] = self.sales_channel
            batch = self._get(url, params=params)
            if batch is None:      # error tras reintentos, o no reintentable
                break
            if not batch:          # categoria agotada
                break
            out.extend(batch)
            if len(batch) < step:  # ultima pagina: no hay otra peticion que esperar
                break
            offset += step
            time.sleep(self.cfg["rate_limit_seconds"])

        return out

    def collect(self, roots, keywords, excluded=()):
        """Baja el catalogo de alimentos completo de esta cadena."""
        leaves = self.leaf_categories(roots, keywords, excluded)
        if not leaves:
            self.log.error("%s: no se encontraron categorias. Revisar config.",
                           self.name)
            return []

        self.log.info("%s: %d categorias hoja por recorrer", self.name, len(leaves))
        seen, products = set(), []
        vacias = 0

        for i, (fq, path) in enumerate(leaves, 1):
            batch = self.products_in_category(fq)
            if not batch:
                vacias += 1
            nuevos = 0
            for p in batch:
                # un producto puede colgar de varias categorias
                pid = p.get("productId")
                if pid and pid not in seen:
                    seen.add(pid)
                    p["_category_path"] = path
                    products.append(p)
                    nuevos += 1
            self.log.info(
                "  [%d/%d] %s -> %d productos (%d nuevos)",
                i, len(leaves), path[:60], len(batch), nuevos,
            )
            time.sleep(self.cfg["rate_limit_seconds"])

        self.log.info(
            "%s: %d productos unicos en %d hojas (%d hojas vacias)",
            self.name, len(products), len(leaves), vacias,
        )
        return products
