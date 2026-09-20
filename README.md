# Canasta de la semana en Lima:D

Panel diario de precios de alimentos en supermercados de Lima.
Propuesta 2 — Analítica de la Web, Universidad del Pacífico, 2026-II.
Johao Mendoza · Marx Rojas · Joyssie Rivas

---

## Lo único que hay que entender antes de tocar nada

**La historia de precios no se puede scrapear hacia atrás.** La web de un
súper muestra el precio de hoy; no existe un endpoint que devuelva el precio
del arroz el 1 de agosto. La serie histórica **se acumula hacia adelante**.

Consecuencias que mandan sobre todo el diseño:

0. **El panel mide el canal supermercado, no el gasto del hogar.** Queda
   fuera el mercado de abastos, el mayorista y el descuento duro. Es la
   limitación más importante del proyecto: ver **Limitación de cobertura**.
1. **Un día sin correr es un hueco permanente.** No se rellena después.
2. **Hay que arrancar antes de cerrar la pregunta de investigación.** El
   diseño se afina en octubre; los precios del 19 de septiembre, no.
3. **Se sobre-recolecta a propósito.** Bajar 5.000 SKU cuesta casi lo mismo
   que bajar 100, y permite redefinir la canasta en noviembre sin haber
   perdido nada. Restringir hoy es cerrarse puertas que no se reabren.

---

## Instalación

```bash
pip install -r requirements.txt
python3 collect.py --smoke      # prueba rápida, 2 categorías
python3 collect.py              # corrida completa
python3 collect.py --audit      # calidad de lo recolectado
python3 tests/test_scraper.py   # tests (sin red, sin dependencias extra)
python3 collect.py --retailer metro   # una sola cadena
```

## Estructura

```
canasta/vtex.py        Cliente de la API de catálogo VTEX
canasta/catalyst.py    Cliente de la API de catálogo Catalyst (Tottus)
canasta/eans.py        Caché persistente de EAN (solo Tottus lo necesita)
canasta/browser.py     Playwright: precio con tarjeta (paso aparte, opcional)
canasta/normalize.py   JSON de VTEX -> filas planas (esquema de la slide 6)
canasta/storage.py     Guardado en dos capas
collect.py             Orquestador. Es el que corre a diario
tests/test_scraper.py  Tests del parser y la normalización
config/retailers.yml   Cadenas, canal de venta, categorías
data/raw/              JSON crudo comprimido
data/daily/            CSV normalizado, una fila por SKU por día
data/ean_tottus.json   Caché de EAN. VERSIONARLA: si se pierde, no converge
```

**Por qué se guarda el JSON crudo:** el día que encuentres un bug en el
parser —y lo vas a encontrar— vas a poder reprocesar las semanas anteriores.
Sin él, un error detectado en la semana 6 obliga a tirar las semanas 1 a 5.
Comprimido ocupa ~80 KB por cadena por día.

---

## Estado de las cadenas (verificado 2026-09-19)

| Cadena | Plataforma | Hojas | Precio | EAN | Nota |
|---|---|---|---|---|---|
| **Plaza Vea** | VTEX | 343 | 99% | 58% | `sc=1` |
| **Metro** | VTEX | 266 | 100% | 100% | `sc=1` |
| **Wong** | VTEX | 266 | 100% | 100% | `sc=70` (no 1) |
| **Vivanda** | VTEX | 309 | 100% | 82% | host canónico, no el front público |
| **Tottus** | Falabella Catalyst | 448 | 100% | caché | cliente propio, sin navegador |

Las cinco se recolectan en una sola corrida, **1.632 categorías hoja**.

Se evaluó **Flora & Fauna** (VTEX, funciona, 198 hojas) y se **descartó**: es
tienda orgánica/premium, no supermercado de canasta básica. Queda en
`config/retailers.yml` como `enabled: false` por si más adelante interesa una
serie del segmento premium.

### Cadenas de descuento y mayoristas: no publican precios

Se investigaron y **ninguna tiene catálogo online con precios** (verificado
2026-09-20, documentado en `config/retailers.yml` para no reinvestigarlo):

| Cadena | Qué hay |
|---|---|
| **Mass** | WordPress corporativo, sin tienda online. `www.mass.pe` ni siquiera resuelve TLS |
| **Makro** | `tienda.makro.com.pe` redirige a dominio parqueado; su tienda ya no existe |
| **Mayorsa** | WordPress con WP REST API activa pero **sin WooCommerce** |
| **Economax** | el dominio no resuelve |
| **Tambo** | no es catálogo, es app de delivery |

Esto **no** es un fallo del scraper: en Lima, el canal de descuento duro y el
mayorista no venden online. Mass en particular no tiene e-commerce por
diseño, que es parte de cómo mantiene sus precios bajos.

La consecuencia para el paper es una limitación de cobertura que hay que
declarar explícitamente: ver **Limitación de cobertura** más abajo.

Tres cosas que costaron depuración y conviene no volver a descubrir:

- **Wong: el 401 no era el filtro, era el canal — y el canal es el 70.** La
  API responde literal `"sc 1 is not available for account wongio"`, y `sc=2`
  devuelve 200 con catálogo vacío, lo que hacía parecer que el canal no se
  podía fijar. El valor correcto lo publica la propia tienda en
  `/api/segments` → `"channel":"70"`. Verificado: `sc=70` devuelve los mismos
  productos y precios que omitir el parámetro, en 6 de 6 categorías raíz.
  Queda fijado como las demás, así que la regla de abajo se cumple en las
  cuatro cadenas VTEX. Los canales confirmados vía `/api/segments` son:
  Plaza Vea 1, Metro 1, Vivanda 1, Wong 70.

- **Vivanda no era intermitente.** `www.vivanda.com.pe` es un front Next.js
  que devuelve HTML en *toda* ruta `/api/`. El catálogo vive en la cuenta
  `vivanda.vtexcommercestable.com.br`, con árbol de categorías propio.

- **Tottus no es VTEX y aun así no necesita navegador.** El menú viene en el
  `__NEXT_DATA__` del HTML y el catálogo en una API JSON paginada. Su listado
  **no** trae EAN, pero la ficha de cada producto sí (`okayToShopBarcodes`),
  y como el EAN es estático se resuelve con caché persistente en
  `data/ean_tottus.json`: se pide una vez por SKU y nunca más. Ver
  `canasta/eans.py`.

---

## Hallazgos técnicos de la API VTEX

Documentados porque costaron depuración y no están en la documentación obvia:

1. **Responde HTTP 206, no 200.** Las consultas paginadas son *range
   requests*. Un cliente que solo acepte 200 recibe cero productos sin error
   aparente.

2. **Las subcategorías exigen la ruta completa de IDs.**
   `fq=C:950` devuelve 0 productos. `fq=C:/2/6/950/` devuelve el catálogo.
   El id suelto solo funciona en categorías raíz.

3. **La paginación corta a ~2.500 resultados** por consulta. Por eso se
   recorre categoría hoja por hoja y no "todos los alimentos" de una.

4. **El gramaje no viene en `measurementUnit`.** VTEX reporta casi siempre
   `un` / multiplicador 1. El peso real está en el nombre del producto y hay
   que parsearlo. Es la parte más frágil del pipeline: vigilar con `--audit`.

   El modo de fallar que importa **no** es el gramaje ausente, es el gramaje
   presente y equivocado: sale un número plausible, se cuenta como éxito en
   cualquier medida de completitud, y después divide el precio. Ejemplo real
   ya corregido: `"Kit Paella Carmencita Caja 490 g"` daba **240 kg**, porque
   el parser leía el 490 como "490 cajas". Por eso `--audit` no cuenta campos
   llenos: marca los precios por kg/l fuera de rango, que es lo único que
   delata este error. Cuando toques `parse_quantity`, corre `--audit` sobre
   varios días antes de confiar en el resultado.

### Mapeo al esquema de la slide 6

| Atributo | Campo VTEX |
|---|---|
| `price` | `sellers[].commertialOffer.Price` |
| `regular_price` | `ListPrice` / `PriceWithoutDiscount` |
| `available` | `IsAvailable`, `AvailableQuantity` |
| `ean` | `items[].ean` |
| `on_sale` | derivado: `regular_price > price` |
| `card_price` | **no disponible como número** (ver abajo) |

---

## Scraping con navegador: qué se probó y qué se decidió

Sí es *web scraping*: son endpoints internos, no documentados, sin API key ni
términos de uso que los habiliten — los mismos que la web usa para pintarse.
En la literatura esto es *API scraping*. La pregunta legítima es si no habría
sido mejor renderizar las páginas con un navegador. **Se probó con Playwright
(Chromium headless), y sí funciona.** Estos son los números.

### Qué da cada técnica (medido 2026-09-19)

| Página | HTTP simple | Navegador headless |
|---|---|---|
| Plaza Vea categoría | 0 precios | **64 precios**, 7.1 s |
| Metro categoría | 60 precios (en JSON embebido) | 26 precios, 15.0 s |
| Tottus categoría | HTTP 503 | **bloqueado** (DOM vacío) |

Dos correcciones a lo que parecía a simple vista:

- **El HTML de Metro sí trae precios**, dentro de un JSON embebido. Una
  búsqueda del formato visual `S/ 17.90` da cero y hace creer que no están.
- **Tottus bloquea el navegador** pero no el cliente HTTP. La protección
  anti-bot reacciona al renderizado, no a la petición. Para Tottus, el
  navegador es estrictamente peor.

### Por qué el navegador no es el recolector principal

No es que no funcione: es que pierde en lo que este proyecto necesita.

1. **Le faltan campos que la página no muestra.** `ean`, `AvailableQuantity`,
   `sellerDefault`, `itemId`. El EAN es la llave de emparejamiento entre
   cadenas — el núcleo analítico — y **no aparece en pantalla**. Tampoco si
   el precio es del supermercado o de un tercero del marketplace (ver §5).
   Aun renderizando, habría que ir igual a la API por esos campos.

2. **Costo.** ~7 s por ficha contra ~0.5 s por 50 productos vía API. Sobre
   1.632 categorías hoja serían días, no minutos, y el job de CI no cabría.

3. **Fragilidad.** Depende de texto visible y de clases CSS, que cambian con
   cualquier rediseño. `commertialOffer.Price` no puede cambiar sin romper la
   web de la propia cadena.

4. **Tottus quedaría fuera**, que es justo la cadena que más costó habilitar.

### Dónde el navegador SÍ es la única opción: precio con tarjeta

Es el único dato de la propuesta que la API no expone. VTEX publica `Teasers`
diciendo que *hay* promoción (`"Promo Oh-Pay"`) pero no el monto. En la ficha
renderizada sí aparece. Verificado en Metro:

```
Tarjeta Cencosud
S/ 17.76     <- SOLO visible con navegador
S/ 18.50     <- API: commertialOffer.Price
S/ 22.00     <- API: commertialOffer.ListPrice
```

Implementado en `canasta/browser.py`, como paso **aparte y opcional**:

```bash
pip install -r requirements-browser.txt
python3 -m playwright install chromium
python3 collect.py --card-prices data/daily/2026-09-19__metro.csv --limit 120
```

Rellena la columna `card_price` de un CSV ya escrito. Es un paso separado a
propósito: el navegador es lento y frágil, y la captura de precios —lo único
que no se recupera hacia atrás— no puede depender de él. Si el navegador
falla, el panel diario sigue intacto.

Tasa observada: 3 de 4 productos de abarrotes en Metro tienen precio de
tarjeta; 0 de 4 en Plaza Vea, y 0 de 8 en frescos a granel, que no llevan
promoción. A diferencia del EAN, **el precio con tarjeta cambia a diario y no
se cachea**, así que se aplica sobre la canasta definida (~100 SKU), no sobre
el catálogo entero.

### Dónde se parsea HTML sin navegador

La regla no fue "nunca HTML", fue **ir donde está el dato**. En Tottus:

- El árbol de categorías, del `<script id="__NEXT_DATA__">`.
- El EAN, con regex sobre `okayToShopBarcodes` en la ficha de producto.

---

## Decisiones metodológicas pendientes

Estas **no** son de programación y hay que cerrarlas antes de escribir el
paper. El scraper ya recoge lo necesario para cualquiera de las opciones.

### 1. ¿Cuál es *el* precio?
Hay tres: normal, oferta y con tarjeta de la cadena. El scraper guarda los
dos primeros. **El precio con tarjeta no se puede extraer como número:** VTEX
expone `Teasers` que dicen que *hay* promoción (ej. `"Promo Oh-Pay"`), no
cuánto. Se guarda el texto del teaser en la columna `teasers` y `card_price`
queda vacío a propósito, en vez de inventar un número.

**Ya no es una limitación técnica: se logró.** `canasta/browser.py` lo extrae
con navegador (ver la sección anterior). Queda como decisión metodológica, no
de programación: índice principal con `price`, y el precio con tarjeta como
**serie paralela** sobre la canasta definida. Si se usara como índice
principal, la canasta mediría condiciones de financiamiento, no de alimentos.

### 2. Regla de precios faltantes ← la más importante
Un SKU se agota, se renombra o desaparece. La fórmula `Canasta = Σ qᵢ·Pᵢ,t`
**exige** un precio por ítem por día. Hay que decidir y escribir la regla:
¿se arrastra el último precio (lo que hace el INEI), se imputa, o se
sustituye? Sin regla explícita, la canasta sube y baja por agotamientos y no
por precios. Es el error más fácil de cometer y el más difícil de detectar.

### 3. Fijar el canal de venta — ya resuelto, no tocar
`sales_channel` en la config: Plaza Vea 1, Metro 1, Vivanda 1, **Wong 70**.
**No cambiarlos nunca.** En VTEX el precio varía por tienda/región; si cambia
a mitad del panel, la serie tiene un salto que no es inflación. Los cuatro
valores están confirmados contra `/api/segments` de cada tienda, que es lo
que la propia web usa. Tottus no tiene canales: no aplica.

### 4. Emparejamiento entre cadenas
Aquí vive el grueso del trabajo analítico. `ean` es la llave limpia, pero su
cobertura es muy desigual y eso condiciona el método:

| Metro | Wong | Vivanda | Plaza Vea | Tottus |
|---|---|---|---|---|
| 100% | 100% | 82% | 58% | vía caché |

Tottus no publica EAN en su API de listado, solo en la ficha de producto. Se
resuelve con `canasta/eans.py`: caché persistente en `data/ean_tottus.json`,
El arranque **no se raciona**: se resuelve de una sola pasada con el
workflow *Relleno único de EAN* (~3.3 h a 1 petición/segundo, dentro del
límite de 6 h de un job), o en local:

```bash
python3 collect.py --backfill-ean --limit 13000
```

Como el EAN es estático —el código de barras de un producto no cambia
nunca— ese coste se paga **una vez**. Después, la corrida diaria solo
consulta los SKU nuevos (decenas), con tope `ean_budget_per_run`.

**No hay vía masiva** (verificado 2026-09-20): no existe endpoint bulk
(`/s/product/v1` da 503), el listado no acepta parámetros para pedir más
campos, y el sitemap de fichas trae solo URLs y fechas, sin GTIN. El
`robots.txt` de Tottus sí permite explícitamente las fichas de producto;
solo bloquea `/basket`, `/myaccount`, `/checkout` y `/orders`. Los **precios**
se capturan completos desde el primer día: el EAN solo hace falta al
emparejar cadenas, que es análisis posterior y sí se puede hacer hacia atrás.

Plan de emparejamiento: EAN cuando exista → fallback a marca + categoría +
precio por unidad base.

### 5. Vendedor del marketplace ≠ la cadena
Las columnas `seller_id` / `seller_name` existen para esto. VTEX y Catalyst
devuelven surtido propio y de terceros en la misma lista; el scraper prefiere
el vendedor propio (`sellerDefault`), pero cuando ninguno lo declara cae al
primero con oferta. En una muestra de Plaza Vea, 5 de 396 filas venían de
terceros (`THE BLITZ COMPANY`, `Aquago!`). Decidir si esas filas entran al
índice o se filtran: un precio de tercero no es el precio de la cadena.
`--audit` lista el reparto de vendedores por archivo.

---

## Limitación de cobertura: qué canal mide este panel

**Esta es la limitación más importante del proyecto y debe ir declarada en el
paper, no descubrirse en la sustentación.**

El panel mide el **canal supermercado moderno de Lima**. No mide el universo
de compra de un hogar limeño. Los tres canales que quedan fuera:

| Canal | Quién compra ahí | Por qué no está |
|---|---|---|
| **Descuento duro** (Mass) | hogares, compra de reposición | no tienen e-commerce, por diseño |
| **Mayorista** (Unicachi, GMML Santa Anita, Caquetá) | bodegueros, restaurantes, puestos de mercado | no publican catálogo online |
| **Mercado de abastos / de barrio** | **hogares, y es el grueso en frescos** | ninguna fuente publica precios a diario |

### Por qué esto sesga la comparación con el INEI

El IPC del INEI **sí** releva mercados minoristas: su canasta recoge el
precio que paga el hogar, compre donde compre. Este panel solo tiene
supermercado. Los dos universos no son el mismo.

Eso importa porque supermercado y mercado de barrio **no se mueven igual ni
al mismo tiempo**, sobre todo en frescos (papa, cebolla, pollo, limón). Si el
índice construido aquí se despega del IPC, parte de la explicación puede ser
**de dónde viene el dato**, no inflación diferencial. Atribuir esa brecha a
un fenómeno económico sin controlar por el canal sería un error de
interpretación, no de cálculo.

### Distinción que conviene no confundir

- **Mercado mayorista** (Unicachi, Santa Anita): ahí *no* compra el hogar,
  compran los revendedores. Su precio es un insumo del precio minorista, no
  el precio final. Unicachi es un caso mixto: mayorista que también atiende
  al público.
- **Mercado de abastos / de barrio**: ahí *sí* compra el hogar. Es lo que de
  verdad falta, y no hay fuente online.

### Una vía si más adelante se quisiera cubrir el canal mayorista

**EMMSA**, la empresa municipal que opera el Gran Mercado Mayorista de Lima
(Santa Anita), publica precios diarios (verificado 2026-09-20, responde):

```
https://www.emmsa.com.pe/index.php/precios-diarios/
https://www.emmsa.com.pe/index.php/boletines/
```

No se implementó, y no sería "una cadena más" en `config/retailers.yml`: es
otro formato, otra unidad de medida (precio por kg mayorista, no por SKU
empaquetado) y **sin EAN**, así que no empareja con el resto del panel.
Requiere un módulo propio.

Se descartó **SISAP (MIDAGRI)**: `sisap.midagri.gob.pe` no resuelve y
`sistemas.midagri.gob.pe` tiene el certificado TLS roto (verificado
2026-09-20).

Para el **mercado de barrio** —el canal que realmente falta— no se encontró
ninguna fuente que publique precios diarios. Ese dato se levanta a pie, que
es exactamente lo que hace el INEI y por lo que su operativo es caro. No hay
atajo por scraping.

### Cómo formularlo en el paper

> El panel cubre el canal de supermercados modernos de Lima Metropolitana
> (5 cadenas, ~54.000 observaciones diarias). Quedan fuera el canal de
> descuento duro, el mayorista y el mercado de abastos, por no publicar
> precios en línea. Los resultados deben leerse como inflación de precios
> del canal supermercado, no como una medida del costo de vida del hogar
> limeño, y su comparación con el IPC del INEI —que sí releva mercados
> minoristas— debe controlar por esta diferencia de cobertura.

---

## Qué NO hace este repositorio

- **No calcula la canasta.** Recolecta. El índice se arma después, sobre el
  panel acumulado.
- **No scrapea al INEI.** El IPC se descarga de su web como serie publicada.
- **No hace nowcast.** Entrenar el Modelo B contra el IPC mensual requiere
  meses de coexistencia de ambas series. Arrancando hoy, para diciembre hay
  2 o 3 observaciones mensuales del INEI: no se entrena nada con n=3. El
  nowcast es diseño documentado, no resultado.

## Cortesía y uso

Dato público, uso académico, precedente establecido (Cavallo & Rigobon 2016;
Jaworski 2021; Bueno 2024 para Perú). Aun así: `rate_limit_seconds: 1.5`,
`User-Agent` identificable, y revisar `robots.txt` antes de ampliar cadenas.
No hay razón para golpear fuerte un servidor por 100 productos al día.
