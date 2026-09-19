"""Cliente para la API publica de catalogo de VTEX.

Plaza Vea y Metro estan verificados sobre esta plataforma. El endpoint
/api/catalog_system/pub/products/search/ devuelve JSON con precio, precio
tachado, EAN y stock, asi que no hace falta parsear HTML.

Limitacion importante de VTEX: la paginacion revienta pasados ~2500
resultados por query. Por eso se recorre el arbol de categorias y se consulta
hoja por hoja, en vez de pedir "todos los alimentos" de una.
"""

import time
import unicodedata

import requests

USER_AGENT = (
    "canasta-lima/1.0 (proyecto academico Universidad del Pacifico; "
    "analitica de la web 2026-II)"
)


def _strip_accents(text):
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


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

    def _get(self, url, params=None):
        """GET con reintentos y backoff exponencial.

        Devuelve None si agota los reintentos: un fallo en una categoria no
        debe tumbar la corrida entera del dia.
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
                    return r.json()
                # 404 en una hoja vacia es normal, no vale reintentar
                if r.status_code == 404:
                    return []
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

    def leaf_categories(self, roots, keywords):
        """Devuelve [(fq, ruta_legible)] de las hojas bajo las categorias de
        alimentos.

        `fq` es el filtro listo para usar. VTEX NO acepta el id suelto en
        subcategorias: "C:950" devuelve 0 productos, hay que dar la ruta
        completa de ids, "C:/2/6/950/". Verificado contra Plaza Vea.

        Si `roots` trae ids, se usan esos. Si viene vacio, se filtran las
        categorias de primer nivel cuyo nombre contenga alguna keyword.
        """
        tree = self.category_tree()
        if not tree:
            return []

        if roots:
            selected = [n for n in tree if n.get("id") in roots]
        else:
            selected = [
                n for n in tree
                if any(k in _strip_accents(n.get("name", "")) for k in keywords)
            ]
            self.log.info(
                "%s: categorias de alimentos detectadas: %s",
                self.name, [n.get("name") for n in selected],
            )

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
        return leaves

    def products_in_category(self, fq):
        """Pagina una categoria hoja completa. `fq` es "C:/2/6/950/".
        Devuelve la lista de productos crudos, tal cual los entrega VTEX."""
        url = f"{self.base_url}/api/catalog_system/pub/products/search/"
        step = self.cfg["page_size"]
        out, offset = [], 0

        while offset < self.cfg["max_offset"]:
            params = {
                "fq": fq,
                "_from": offset,
                "_to": offset + step - 1,
                "sc": self.sales_channel,
            }
            batch = self._get(url, params=params)
            if batch is None:      # error tras reintentos
                break
            if not batch:          # categoria agotada
                break
            out.extend(batch)
            if len(batch) < step:  # ultima pagina
                break
            offset += step
            time.sleep(self.cfg["rate_limit_seconds"])

        return out

    def collect(self, roots, keywords):
        """Baja el catalogo de alimentos completo de esta cadena."""
        leaves = self.leaf_categories(roots, keywords)
        if not leaves:
            self.log.error("%s: no se encontraron categorias. Revisar config.",
                           self.name)
            return []

        self.log.info("%s: %d categorias hoja por recorrer", self.name, len(leaves))
        seen, products = set(), []

        for i, (fq, path) in enumerate(leaves, 1):
            batch = self.products_in_category(fq)
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

        self.log.info("%s: %d productos unicos", self.name, len(products))
        return products
