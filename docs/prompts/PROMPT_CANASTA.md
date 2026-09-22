# Prompt: completar la canasta de trabajo

Amplía la canasta de trabajo con los productos de la canasta oficial de Lima que el scraper ya baja y que hoy quedan fuera. No replantees el proyecto. No toques la recolección diaria (`collect.py`, `canasta/vtex.py`, `canasta/catalyst.py`). No entrenes modelos. No incluyas comida fuera del hogar.

Lee `analisis/canasta_trabajo.py`, `data/canasta_oficial.csv`, `data/canasta_reglas.csv`, `data/canasta_trabajo.csv`, `data/canasta_trabajo_exclusiones.csv` y `canasta/eans.py`.

## Qué no se toca

- Los 6 ítems `fuera_del_hogar` (ids 104, 106, 107, 108, 109, 110) siguen excluidos. No son una compra de supermercado.
- La barra sigue siendo al menos 4 de las 5 cadenas, con precio válido y disponible. No la bajes a 3 para inflar la lista.
- El núcleo actual (mismo EAN, o granel «x kg» de la misma variedad) no se mezcla con un producto solo parecido.
- Las reglas de matching se fijan antes de mirar precios. No elijas el candidato más barato.

## Cómo volver comparable lo que ya está en `data/daily/`

El scraper ya guardó estos productos. El trabajo es el cruce, no una descarga nueva. Hay cuatro vías. Cada fila de la canasta debe decir por cuál entró (`identidad`: `ean`, `ean_equivalente`, `por_kilo`, `por_unidad`).

1. **Mismo paquete, EAN vacío** (`identidad=ean`). Plaza Vea y Vivanda a menudo traen el producto con `ean` vacío. Completa el código desde la ficha, con el mismo mecanismo de `canasta/eans.py` (caché en disco, una vez por SKU, también el negativo). Si el EAN recuperado es el que ya usan las otras cadenas, el ítem se une al núcleo. Empieza por los que hoy están en 3 cadenas y subirían a 4: sémola, arveja seca partida, ají molido, canela entera, sal, y los que están en 4 y les falta una cadena con EAN vacío (arroz Paisana 5 kg en Vivanda, tallarín Molitalia en Vivanda, ajo molido). No scrapees el catálogo entero: solo los SKU candidatos de esos ítems.

2. **Mismo paquete, otro EAN** (`identidad=ean_equivalente`). Si el nombre, la marca y el gramaje coinciden y el código no, escríbelo en `data/ean_equivalencias.csv` (`ean_a`, `ean_b`, `motivo`) sin usar el precio como criterio. Caso ya visto: margarina Dorina. Revísalo también en leche evaporada. La tabla es chica y a mano. Después el cruce trata esos EAN como uno solo.

3. **No es el mismo paquete** (`identidad=por_kilo`). Galleta de soda, maíz cancha, grated de sardina o atún, ají seco entero, verdura picada, y cualquier envasado que tras los pasos 1 y 2 siga sin un EAN en 4 cadenas. Cada cadena aporta un producto de esa regla en `canasta_reglas.csv`. El precio comparable es `price / net_quantity` (precio por kg o por litro). Estas filas van en la canasta, marcadas aparte del núcleo por EAN. No las uses para decir que es el mismo producto físico.

4. **El INEI está en gramos y la tienda vende por unidad** (`identidad=por_unidad`). Pan francés (id 4) y té filtrante (id 86). Fija en `canasta_reglas.csv`, en `g_por_unidad_base`, los gramos de un pan y de un filtrante, con la fuente del número escrita en `motivo`. El precio comparable es el precio de la bolsa dividido por esos gramos. Si no hay una fuente que no sea el precio del supermercado, déjalos excluidos y dilo. No inventes el gramaje para que entren.

A granel (apio, lechuga, culantro, choclo, arveja verde, menudencia, huevos, mondongo, jurel, olluco, ajo entero, pepinillo, plátano de la isla): si el CSV tiene «x kg» o «por kg» y está disponible, el representante de `_granel` ya debería haberlo tomado. Si solo está como unidad, atado o bolsa, no lo conviertas con un peso inventado. Puedes ampliar `incluye` solo cuando el nombre del CSV muestra que es ese alimento a granel y el precio ya es por kilo. Si una cadena no lo vende así, el ítem sigue fuera.

## Salida

- Regenera `data/canasta_trabajo.csv` y `data/canasta_trabajo_exclusiones.csv` con `python -m analisis.canasta_trabajo --recongelar` solo después de que las reglas y la caché de EAN estén escritas.
- Cada fila lleva `identidad`. Un resumen al final: cuántos ítems de los 68 de Lima entran por `ean`, cuántos por `ean_equivalente`, cuántos por `por_kilo`, cuántos por `por_unidad`, y cuáles de los 26 excluidos (sin contar fuera del hogar) siguen fuera y por qué.
- Tests sintéticos de las cuatro identidades. No hace falta red en los tests. El backfill de EAN, si lo corres, va aparte y con el límite de tasa que ya usa `eans.py`.

No cuentes como canasta más llena un ítem que solo está en tres cadenas. No mezcles el ahorro de cambiar de marca con el de cambiar de cadena.
