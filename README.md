# Canasta de la semana en Lima

Panel diario de precios de alimentos en supermercados de Lima.
Propuesta 2 — Analítica de la Web, Universidad del Pacífico, 2026-II.
Johao Mendoza · Marx Rojas · Joyssie Rivas

---

## Lo único que hay que entender antes de tocar nada OJO

**La historia de precios no se puede scrapear hacia atrás.** La web de un
súper muestra el precio de hoy; no existe un endpoint que devuelva el precio
del arroz el 1 de agosto. La serie histórica **se acumula hacia adelante**.

Consecuencias que mandan sobre todo el diseño:

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
python3 collect.py --retailer metro   # una sola cadena
```

## Estructura

```
canasta/vtex.py        Cliente de la API de catálogo VTEX
canasta/catalyst.py    Cliente de la API de catálogo Catalyst (Tottus)
canasta/normalize.py   JSON de VTEX -> filas planas (esquema de la slide 6)
canasta/storage.py     Guardado en dos capas
collect.py             Orquestador. Es el que corre a diario
config/retailers.yml   Cadenas, canal de venta, categorías
data/raw/              JSON crudo comprimido
data/daily/            CSV normalizado, una fila por SKU por día
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
| **Wong** | VTEX | 266 | 100% | 100% | **sin** `sc` (ver abajo) |
| **Vivanda** | VTEX | 309 | 100% | 82% | host canónico, no el front público |
| **Tottus** | Falabella Catalyst | 411 | 100% | **0%** | cliente propio, sin navegador |

Las cinco se recolectan en una sola corrida, **1.595 categorías hoja**.

Tres cosas que costaron depuración y conviene no volver a descubrir:

- **Wong: el 401 no era el filtro, era el canal.** La API responde literal
  `"sc 1 is not available for account wongio"`. Pero `sc=2`, que sí responde
  200, devuelve catálogo vacío: la única forma de obtener su surtido es
  **omitir** `sc`. Eso deja a Wong en el canal por defecto de la cuenta, o
  sea sin fijar, lo que contradice la regla de abajo sobre fijar el canal. Es
  la única opción viable, pero si VTEX cambia ese default la serie de Wong da
  un salto que no es inflación. Vigilar con `--audit`.

- **Vivanda no era intermitente.** `www.vivanda.com.pe` es un front Next.js
  que devuelve HTML en *toda* ruta `/api/`. El catálogo vive en la cuenta
  `vivanda.vtexcommercestable.com.br`, con árbol de categorías propio.

- **Tottus no es VTEX y aun así no necesita navegador.** El menú viene en el
  `__NEXT_DATA__` del HTML y el catálogo en una API JSON paginada.
  **No expone EAN**: el emparejamiento con las otras cadenas depende
  enteramente del fallback por marca + nombre (ver §4 más abajo).

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

## Decisiones metodológicas pendientes

Estas **no** son de programación y hay que cerrarlas antes de escribir el
paper. El scraper ya recoge lo necesario para cualquiera de las opciones.

### 1. ¿Cuál es *el* precio?
Hay tres: normal, oferta y con tarjeta de la cadena. El scraper guarda los
dos primeros. **El precio con tarjeta no se puede extraer como número:** VTEX
expone `Teasers` que dicen que *hay* promoción (ej. `"Promo Oh-Pay"`), no
cuánto. Se guarda el texto del teaser en la columna `teasers` y `card_price`
queda vacío a propósito, en vez de inventar un número.

**Recomendación:** índice principal con `price`. El precio con tarjeta, si se
logra, como serie paralela. Si no, tu canasta mide condiciones de
financiamiento, no de alimentos.

### 2. Regla de precios faltantes ← la más importante
Un SKU se agota, se renombra o desaparece. La fórmula `Canasta = Σ qᵢ·Pᵢ,t`
**exige** un precio por ítem por día. Hay que decidir y escribir la regla:
¿se arrastra el último precio (lo que hace el INEI), se imputa, o se
sustituye? Sin regla explícita, la canasta sube y baja por agotamientos y no
por precios. Es el error más fácil de cometer y el más difícil de detectar.

### 3. Fijar el canal de venta
`sales_channel: 1` en la config. **No cambiarlo nunca.** En VTEX el precio
varía por tienda/región; si cambia a mitad del panel, la serie tiene un salto
que no es inflación.

### 4. Emparejamiento entre cadenas
Aquí vive el grueso del trabajo analítico. `ean` es la llave limpia, pero su
cobertura es muy desigual y eso condiciona el método:

| Metro | Wong | Vivanda | Plaza Vea | Tottus |
|---|---|---|---|---|
| 100% | 100% | 82% | 58% | **0%** |

**Tottus no expone EAN en ningún endpoint público de listado**, así que entra
entero por el fallback. Plan: EAN cuando exista → marca + categoría + precio
por unidad base. Conviene medir la tasa de acierto del fallback contra los
pares que *sí* tienen EAN en las otras cadenas, porque esa tasa es el techo
de calidad de todo lo que se diga sobre Tottus.

### 5. Vendedor del marketplace ≠ la cadena
Las columnas `seller_id` / `seller_name` existen para esto. VTEX y Catalyst
devuelven surtido propio y de terceros en la misma lista; el scraper prefiere
el vendedor propio (`sellerDefault`), pero cuando ninguno lo declara cae al
primero con oferta. En una muestra de Plaza Vea, 5 de 396 filas venían de
terceros (`THE BLITZ COMPANY`, `Aquago!`). Decidir si esas filas entran al
índice o se filtran: un precio de tercero no es el precio de la cadena.
`--audit` lista el reparto de vendedores por archivo.

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
