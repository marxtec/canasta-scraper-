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
python3 collect.py --reprocess  # re-normaliza todo desde el crudo (parser nuevo)
python3 tests/test_scraper.py   # tests (sin red, sin dependencias extra)
python3 collect.py --retailer metro   # una sola cadena

pip install -r requirements-analisis.txt   # pandas, numpy, matplotlib: SOLO para analisis/
python3 -m analisis.run_all               # regenera todos los derivados (ver "Análisis")
```

## Estructura

```
canasta/vtex.py        Cliente de la API de catálogo VTEX
canasta/catalyst.py    Cliente de la API de catálogo Catalyst (Tottus)
canasta/eans.py        Caché persistente de EAN (solo Tottus lo necesita)
canasta/browser.py     Playwright: precio con tarjeta (paso aparte, opcional)
canasta/normalize.py   JSON de VTEX -> filas planas (esquema de la slide 6)
canasta/storage.py     Guardado en tres capas (crudo, CSV, arbol)
collect.py             Orquestador. Es el que corre a diario
analisis/              Capa de análisis sobre el panel acumulado (ver "Análisis")
tests/test_scraper.py  Tests del parser y la normalización
tests/test_*.py        Tests de cada bloque de análisis, con series sintéticas
config/retailers.yml   Cadenas, canal de venta, categorías
data/raw/              JSON crudo comprimido
data/daily/            CSV normalizado, una fila por SKU por día
data/trees/            Árbol de categorías y hojas recorridas, por cadena y día
data/ean_tottus.json   Caché de EAN. VERSIONARLA: si se pierde, no converge
data/canasta_oficial.csv   CBA del INEI (fuente en canasta_oficial.fuente.txt)
data/canasta_reglas.csv    Reglas de emparejamiento, fijadas antes de mirar precios
data/ean_equivalencias.csv Pares de EAN del mismo envase, con su motivo
data/canasta_trabajo*.csv  Canasta de trabajo, exclusiones y tolerancia de tamaño
data/derived/          Derivados del análisis, sufijo __<última fecha>; se regeneran
data/derived/figuras/  Figuras (matplotlib, en español)
data/derived/auditoria/ Auditoría API vs pantalla: una corrida por semana + acumulado
BITACORA.md            Decisiones fechadas del proyecto
docs/informe/          Informe de avance (LaTeX IEEE, run_all pega sus tablas) y resumen
docs/diagnosticos/     Pasadas de diagnóstico del código
docs/prompts/          Prompts de trabajo con el asistente
docs/plantilla_paper/  Estructura del paper y enlace a la plantilla de Overleaf
docs/presentaciones/   Presentaciones del curso
```

**Por qué se guarda el JSON crudo:** el día que encuentres un bug en el
parser —y lo vas a encontrar— vas a poder reprocesar las semanas anteriores.
Sin él, un error detectado en la semana 6 obliga a tirar las semanas 1 a 5.
Comprimido ocupa ~80 KB por cadena por día. Ya se pagó solo una vez: la
versión 1.2 del parser extrajo precio con tarjeta, vigencia de promos y
especificaciones que estaban en el crudo desde el día uno, con
`python3 collect.py --reprocess`.

**Por qué se guarda el árbol de categorías:** cada día desaparecen ~440
productos del panel. Sin el árbol no se puede distinguir "la cadena lo
deslistó" de "la cadena reorganizó sus categorías y el scraper dejó de
verlo". Para medir cuánto dura una oferta esa diferencia lo es todo. Es lo
único que no se recupera del crudo. La guardia de volumen compara filas por
categoría raíz y hojas recorridas contra la corrida anterior, no solo el
total: una hoja de 300 productos que se cae es −2.5% del total y el umbral
global no la ve.

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
| `card_price` | derivado del **porcentaje del teaser** en Cencosud (ver abajo); vacío en el resto |
| `promo_start` / `promo_end` | vigencia parseada del teaser (`del 01al30 Setiembre`) |
| `promo_type` | `descuento` · `tarjeta` · `paga_x_lleva_y` (esta última no mueve el precio unitario: sin ella `on_sale` la ignora) |
| `vendido_por`, `origen`, `octogonos`, `contenido_neto_declarado` | especificaciones que VTEX cuelga como claves sueltas del producto |
| `unit_multiplier` | peso de una unidad en el carrito para peso variable (`kg`, 0.16 = un plátano). El precio sigue siendo por kilo |

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

### Precio con tarjeta: la API sí lo da (corregido 2026-09-20)

Se creyó que era el único dato que la API no exponía. **No es así en
Cencosud:** el teaser trae el porcentaje y la vigencia, y con eso basta:

```
[TCENCO] Set26 - Supermercado - Con 4% Dscto Con TC Metro del 01al30 Setiembre
                                    ^^                        ^^^^^^^^^^^^^^^^^
card_price = 18.50 × (1 − 0.04) = 17.76   <- exactamente lo que muestra la web
```

Verificado contra la ficha renderizada en Metro (S/17.76 con tarjeta, S/18.50
`Price`, S/22.00 `ListPrice`). Cubre el 43% del catálogo de Metro y el 22% de
Wong, con ventanas que van del mes entero (`01al30`) al fin de semana
(`18al20`). Plaza Vea solo publica `"con Tarjeta Oh!"` como especificación,
sin porcentaje (185 productos): ahí sigue haciendo falta el navegador.

### Dónde el navegador sigue siendo la única opción

Para las cadenas que no publican el porcentaje. Antes de la corrección se
verificó en Metro que el navegador sí lo lee:

```
Tarjeta Cencosud
S/ 17.76     <- visible con navegador (hoy también derivable del teaser)
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
tres donde existen: `card_price` se deriva del porcentaje del teaser en
Cencosud (43% de Metro, 22% de Wong) y viene en el listado de Tottus (CMR);
en Plaza Vea y Vivanda queda vacío porque no publican el monto, y no se
inventa. `canasta/browser.py` sigue disponible para esos casos.

Queda como decisión metodológica, no de programación: índice principal con
`price`, y el precio con tarjeta como **serie paralela**. Si se usara como
índice principal, la canasta mediría condiciones de financiamiento, no de
alimentos.

### 2. Regla de precios faltantes — decidida (provisional, con criterio de revisión)
Un SKU se agota, se renombra o desaparece. La fórmula `Canasta = Σ qᵢ·Pᵢ,t`
**exige** un precio por ítem por día; sin regla explícita, la canasta sube y
baja por agotamientos y no por precios. La regla está **medida, implementada
y decidida** en `analisis/precios_faltantes.py` (bitácora del 2026-09-21):

- **Un precio publicado con `available=False` no es un precio.** Plaza Vea
  y Vivanda listan con precio el 26 % y el 33 % de sus SKU-día sin stock;
  Cencosud casi nunca; Tottus no publica stock. Exigirlo baja la base de
  EAN en las 5 cadenas de 915 a 701 (19-09-2026).
- **Regla principal: arrastre del último precio con tope de 7 días** (la
  práctica del INEI) para agotados y deslistados. Un hueco por cambio del
  árbol de categorías no es un hueco: se arrastra sin tope.
- **Pasado el tope, exclusión** con índice encadenado de composición
  emparejada (solo ítems con precio en ambos días).
- **Imputación por la variación mediana de la categoría** se calcula como
  prueba de robustez y se reporta la diferencia.
- **Criterio de revisión, fijado antes de ver el resultado:** con 14+ días,
  si la divergencia máxima entre reglas supera 0,5 puntos de índice en
  alguna cadena, la divergencia se publica y la regla principal pasa a ser
  la exclusión emparejada. Con 2 días las tres difieren en < 0,001 puntos:
  el contraste todavía no dice nada.

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

### 4b. Cuántos productos son realmente comparables

Medido sobre el primer día del panel (19-09-2026, 53.958 filas). **Este es el
dato que define el tamaño posible de la canasta.**

De los **24.137 productos únicos** (identificados por EAN de 12+ dígitos):

| Presente en | Productos | ¿Sirve para comparar? |
|---|---|---|
| 1 sola cadena | 13.073 | no |
| 2 cadenas | 6.346 | sí, pero limitado |
| 3 cadenas | 2.418 | sí |
| 4 cadenas | 1.383 | sí |
| **Las 5 cadenas** | **917** | **el caso ideal** |

Visto desde cada cadena:

| Cadena | Productos | En 2+ | En 3+ | **En las 5** |
|---|---|---|---|---|
| Wong | 12.407 | 7.029 (57%) | 3.382 (27%) | 917 (7.4%) |
| Tottus | 12.315 | 5.499 (45%) | 3.912 (32%) | 917 (7.4%) |
| Plaza Vea | 11.859 | 5.786 (49%) | 3.584 (30%) | 918 (7.7%) |
| Metro | 9.525 | 6.837 (72%) | 3.328 (35%) | 917 (9.6%) |
| Vivanda | 7.852 | 4.964 (63%) | 3.183 (41%) | 917 (11.7%) |

Los grupos son **anidados**, no excluyentes: los 917 están dentro de los
"en 3+", que están dentro de los "en 2+". Decir "6.837 comparables en Metro"
es un criterio laxo — la mayoría de esos está en solo 2 cadenas, y a menudo
son Metro y Wong, que son la misma empresa (Cencosud).

#### Por qué la mitad del catálogo no es comparable

Dos causas, ninguna es un fallo del scraper:

- **Marca propia y granel.** El pan de la panadería de Metro, los "Bell's"
  de Plaza Vea, el pollo por kilo. No llevan código de barras común: cada
  cadena les asigna un código interno (los `2200…`, `2050…`) que no existe
  fuera de su sistema. Explica por qué Plaza Vea y Tottus quedan más abajo.
- **Surtido exclusivo.** Productos que esa cadena vende y las otras no.

#### Qué implica para la canasta

**Los 917 son la base recomendada.** Un ítem de canasta necesita precio en
todas las cadenas todos los días; si no, hay que imputar precios faltantes
(§2), que es el error más fácil de cometer y el más difícil de detectar. Con
cobertura completa ese problema casi no aparece.

Son el **8.5% del catálogo promedio** (10.792 productos por cadena). Suena
poco y no lo es: el INEI construye su canasta de alimentos con unas pocas
decenas de productos representativos. Recolectar 10.000 para usar 900 es
exactamente el diseño buscado — ver el principio de sobre-recolección
arriba: el 91.5% restante queda acumulado por si la canasta se redefine.

**Advertencia importante:** 917 es la foto de **un solo día**. Cuando se
exija presencia estable durante semanas —que es lo que necesita una serie
temporal— ese número va a caer: productos que se agotan, que la cadena deja
de listar, que cambian de código. La canasta definitiva se define **sobre el
panel acumulado**, no sobre los datos de hoy. Conviene recalcular esta tabla
cada par de semanas.

### 4c. Gramaje discrepante entre cadenas — para el preprocesamiento

Medido sobre los 917 productos presentes en las 5 cadenas (19-09-2026).
**134 (15%) reportan pesos distintos en distintas cadenas.**

Como el EAN garantiza que es el mismo producto físico, cualquier desacuerdo
en `net_quantity` es un error **por definición**. Es una validación cruzada
gratuita: no hace falta revisar nada a mano para detectarlos.

| Tipo | Casos | Ejemplo |
|---|---|---|
| **A. Unidad vs peso** | 87 | Plaza Vea `"Malla 25g x 5un"` → 125 g; Metro `"5un"` → 5 unidades |
| **B. Peso muy distinto** | 26 | Tottus dice 500 g; las otras cuatro dicen 250 g |
| **C. Diferencia chica** | 21 | 400 ml vs 475 ml (cambio de formato no actualizado) |

El tipo A **no es un bug del parser**: cada cadena nombra distinto y el parser
hace lo correcto con lo que recibe. Pero rompe la comparación, porque no se
puede comparar precio por kilo contra precio por unidad.

#### Cómo resolverlo (al preprocesar, no al recolectar)

Dos reglas, ambas sobre datos ya recolectados:

1. **Copiar el peso vía EAN.** Si una cadena publica el gramaje y otra no, se
   copia: es el mismo paquete físico. Resuelve los 87 del tipo A.
2. **Mayoría.** Si varias publican pesos distintos, gana el que reportan más
   cadenas. Resuelve los 47 de tipo B y C.

**Esto NO va en el scraper.** El scraper guarda lo que cada cadena dice, tal
cual — si empieza a "corregir" datos en la captura, se pierde la evidencia de
qué publicó realmente cada cadena y no se puede auditar después. La
reconciliación es una decisión metodológica y va en la capa de análisis,
documentada aparte.

Recalcular estas cifras sobre el panel acumulado antes de fijar la canasta.

#### Otros casos a filtrar en los 917

| Problema | Casos |
|---|---|
| Brecha de precio entre cadenas >50% | 55 — revisar antes de incluir: puede ser promoción real o error de catalogación |
| Gramaje sin parsear en alguna cadena | 20 |
| Algún precio en cero | 2 |

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

## Análisis

Todo vive en `analisis/`, separado del recolector: se puede romper, reescribir
o correr diez veces sin que la captura diaria se entere. Dependencias aparte
(`requirements-analisis.txt`: pandas, numpy, matplotlib; el scraper sigue con
requests + PyYAML).

**Premisa de diseño.** Ningún script tiene fechas, rutas ni recuentos de días
fijos: cada uno descubre qué días hay en `data/daily/` y trabaja con todos.
Los umbrales son constantes con nombre al inicio de cada módulo y argumentos
de línea de comandos. Correr dos veces el mismo día deja exactamente un
derivado por bloque (`data/derived/<bloque>__<última fecha>.csv`; las
versiones anteriores se borran, la historia queda en git). Lo que no se puede
calcular todavía sale como `NA` o como `PROVISIONAL` con los días que faltan;
nunca como una cifra sesgada en silencio. Un precio 0 no es un precio
observado; un precio con `available=False` cuenta para dispersión (es lo que
la cadena publica) pero no para la canasta.

### El comando único

```bash
python3 -m analisis.run_all                  # bloques 1, 2, 3 y 5; pega tablas en el informe
python3 -m analisis.run_all --con-auditoria  # además el bloque 4 (navegador, ~35 min)
python3 -m analisis.run_all --sin-informe    # no toca docs/informe/informe_avance.txt
```

Regenera todos los CSV, el `.tex` de tablas y las figuras, sustituye el
bloque entre `% >>> TABLAS GENERADAS` y `% <<< TABLAS GENERADAS` de
`docs/informe/informe_avance.txt`, y termina con un **veredicto** por resultado: `SOLIDO`
o `PROVISIONAL`, con los días que faltan. Es el comando que se corre en
noviembre con el panel completo, sin argumentos y sin editar nada.

### Bloque por bloque

| Script | Qué responde | Derivados | Umbrales por defecto |
|---|---|---|---|
| `oferta_real.py` (C) | ¿El cartel `on_sale` es una oferta o el precio normal? Habitual = mediana de `price` en [t−45, t−1]; `oferta_real`, `fantasma`, `rebaja_silenciosa`, `descuento_anunciado`, `ref_moda`, `fiable` | `oferta_real_panel` (tabla diaria, en .gitignore), `oferta_real`, `oferta_real_cadenas`, `oferta_real_sensibilidad` | ventana 45 d sin hoy, 21 observados, caída ≥ 10 %, cartel no más de la mitad de la ventana |
| `canasta_trabajo.py` | Cruce de la CBA del INEI (`data/canasta_oficial.csv`) con el panel, con las reglas de `data/canasta_reglas.csv` y `data/ean_equivalencias.csv`. Cada fila con su `identidad` (ean, ean_equivalente, granel, por_kilo, por_unidad) y cada ítem con su `nivel` (núcleo, ampliado_1, ampliado_2); ver BITACORA 22-09 | `data/canasta_trabajo.csv`, `data/canasta_trabajo_exclusiones.csv`, `data/canasta_trabajo_beta.csv` | ≥ 4 de 5 cadenas, presencia ≥ 80 % de la ventana de 14 días; tolerancia de tamaño con δ = 5 %; se congela |
| `tamano.py` | Efecto del tamaño del envase en el precio por kg (β con efectos fijos por línea) y la tolerancia `r_max` que usa el cruce | se escribe con la canasta | δ = 5 %, β por ítem con ≥ 20 líneas |
| `optimizador.py` | Plan de compra de hoy: cada ítem en la cadena más barata con precio observado y disponible; línea base = una cadena, hoy, creyendo el cartel. Costo continuo per cápita por nivel y costo en paquetes enteros para un hogar | `optimizador_plan`, `optimizador_resumen`, `optimizador_linea_base` | escenarios con y sin Tottus; visita apagada; `--personas 4` |
| `modelo_a.py`, `modelo_b.py` | A: prima relativa por cadena sin precios como features. B: entra en oferta real en 7 / 14 días. **Sin entrenar**: `fit` se niega sin panel suficiente | — | A: 60 días; B: 14 + h + 7 días de origen después de los 21 de C |
| `rigidez.py` | Tasa de cambio de precio, entradas/salidas de oferta, altas/bajas, tamaño y bimodalidad, separando **días hábiles** de fin de semana; tasa de positivos del Modelo B a 7/14/30 días | `rigidez`, `rigidez_transiciones`, `rigidez_tamanos`, `rigidez_horizontes` | cambio > medio céntimo; banda de Bueno 15–25 % |
| `dispersion.py` | Coincidencia por pares, brecha máx/mín con masa en cero, SD del log-precio (G&T), descomposición de varianza por efectos fijos anidados en dos órdenes, evolución diaria | `dispersion*`, `tablas_dispersion__*.tex`, `figuras/*.png` | EAN de fabricante (12+ dígitos, sin prefijo 2; `--incluir-internos` replica la bitácora); `--solo-disponibles` |
| `precios_faltantes.py` | Huecos por tipo y duración (usa `data/trees/`); canasta bajo arrastre / imputación / exclusión y contraste | `huecos*`, `canasta_reglas*` | tope de arrastre 7 d; base = EAN en todas las cadenas el primer día |
| `auditoria_validez.py` | ¿El precio de la API es el que ve el comprador? Muestra reproducible por cadena, ficha abierta con Playwright, concordancia exacta, vendedor mostrado, bloqueos como resultado | `auditoria/validez__<fecha>.csv`, `validez_acumulado.csv`, `validez_resumen.csv` | 50 por cadena, semilla 20260921 |

```bash
python3 -m analisis.oferta_real --lookback 30 --umbral 0.15
python3 -m analisis.rigidez --horizontes 7 14 30 60
python3 -m analisis.dispersion --incluir-internos --sin-figuras
python3 -m analisis.precios_faltantes --regla arrastre --tope-arrastre 14
python3 -m analisis.auditoria_validez --n 10 --solo-muestra   # imprime la muestra sin navegador
python3 -m analisis.auditoria_validez --n 50                   # corre (mismo día que el CSV)
```

La auditoría corre además **semanalmente** en `.github/workflows/auditoria-validez.yml`
(miércoles por la tarde, hora de Lima, para auditar el CSV del mismo día),
en un workflow independiente del de recolección: si falla, la captura de
precios no se entera. Necesita `requirements-browser.txt` y Chromium.

### Análisis diario automático

`.github/workflows/analisis-diario.yml` corre cuando termina con éxito la
recolección, en un workflow aparte: si falla, la recolección no se entera.
Trabaja solo si hay datos diarios más nuevos que el último análisis, así
que los intentos del cron que no recolectaron no repiten nada. Hace tres
cosas y un commit (`analisis <fecha>`):

1. **Canasta de trabajo**, solo si existe `data/canasta_reglas.APROBADAS` y
   todavía no está congelada. Ese archivo lo crea el equipo cuando termina
   de revisar `data/canasta_reglas.csv`; sin él el bot no toca la canasta.
   Con 14+ días de panel, la primera corrida la congela.
2. `run_all --sin-informe --sin-figuras`: derivados y veredicto, sin pegar
   tablas en `informe_avance.txt` (eso se hace a mano al entregar).
3. `optimizador`: el plan del día, guardado en git antes de conocer los
   precios siguientes.

No entrena los Modelos A ni B: eso se hace una vez, a mano. También se
puede lanzar desde la pestaña Actions (`forzar` corre aunque no haya datos
nuevos).

### Tests

```bash
python3 tests/test_scraper.py             # parser y normalización (sin dependencias extra)
python3 tests/test_oferta_real.py         # series sintéticas: oferta real, fantasma, rebaja silenciosa
python3 tests/test_canasta_trabajo.py     # cruce CBA x panel con filas sintéticas: las cinco identidades
python3 tests/test_presentacion.py       # unidad suelta, peso aprox., gramaje ambiguo, r_max
python3 tests/test_optimizador.py         # agotado infactible, fantasma fuera del ahorro, se paga price
python3 tests/test_modelos.py            # A y B: se niegan sin panel, features sin futuro, embargo
python3 tests/test_rigidez.py
python3 tests/test_dispersion.py          # recupera un efecto de cadena conocido
python3 tests/test_precios_faltantes.py
python3 tests/test_auditoria_validez.py   # parser sobre texto de fichas reales, sin red
python3 tests/test_analisis_e2e.py        # panel sintético de 40 días en un directorio temporal
pytest tests/                             # todo junto
```

Ninguno toca la red ni depende de cuántos días haya en `data/`.

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
