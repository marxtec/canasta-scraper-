# Prompt de desarrollo

Actúa como arquitecto técnico de este repositorio. Implementa la versión que se puede construir ahora y deja listo, sin entrenar, el código de los modelos que esperan más días de panel.

Lee antes `BITACORA.md`, `DIAGNOSTICO_2026-09-22.md`, `analisis/oferta_real.py`, `analisis/dispersion.py`, `analisis/rigidez.py`, `analisis/precios_faltantes.py` y `canasta/normalize.py`. No reabras el título ni la pregunta. No presentes los días ya scrapeados como resultado del paper: prueban que el scraper corre. No entrenes modelos con esos días. No construyas un índice ni un nowcast del IPC. No toques la recolección (`collect.py`, `canasta/vtex.py`, `canasta/catalyst.py`) salvo un bug que impida esta pasada.

## Decisiones cerradas

Título: Inteligencia de precios para el consumidor. De la dispersión y la rigidez en la web del retail limeño a un sistema predictivo y prescriptivo de ahorro familiar.

Pregunta: ¿En qué medida la dispersión, la rigidez y la opacidad promocional de los precios que publica el retail limeño permiten reducir, con un sistema predictivo y prescriptivo, el costo de una canasta básica de Lima Metropolitana observada en sus supermercados?

Hay tres conjuntos distintos. No los mezcles.

- Canasta oficial: la canasta básica alimentaria del INEI, con cantidades físicas (kilos, litros, unidades). El INEI entra como lista y cantidades, no como serie del IPC.
- Canasta de trabajo: el cruce de esa lista con productos del panel presentes en al menos 4 de las 5 cadenas. Se congela cuando exista una ventana de inclusión; no se rearma cada día según el resultado.
- Universo scrapeado: todo el panel. Los 917 EAN de un día son universo común, no la canasta.

Emparejamiento, en este orden:

1. El INEI no publica EAN. Cada ítem oficial se vincula a un producto del panel por descripción normalizada, gramaje (`parse_quantity`) y marca cuando la fuente la trae. No sustituyas por otra marca: eso sería otro ahorro y se mezclaría con el de elegir cadena.
2. Una vez hay un EAN de fabricante (12 o más dígitos, sin prefijo 2), las demás cadenas se unen por ese EAN. Esa es la identidad entre supermercados.
3. Sin EAN (granel, prefijo 2), un representante por kilo o por litro dentro de cada cadena, no un EAN inventado.
4. Se excluyen packs, combos y vendedores que no son la cadena (`seller_name` de un tercero).
5. Las reglas de matching se fijan antes de mirar precios. Documenta las categorías oficiales que quedan fuera y por qué (sin equivalente envasado ni a granel en la web, o no alimento).

Modelo C, regla principal, ya esbozada en `analisis/oferta_real.py`. No lo conviertas en un clasificador.

- Precio habitual: mediana de `price` del mismo SKU en la ventana hacia atrás, sin incluir el día de hoy.
- Oferta real: el precio de hoy cae al menos 10 % frente a esa mediana y el cartel no estuvo encendido más de la mitad de los días de la ventana.
- Oferta fantasma: `on_sale` está encendido y no se cumple lo anterior.
- Rebaja silenciosa: cae al menos 10 % y no hay cartel. Nómbrala; no la metas en el fantasma.
- Con menos de 21 días observados el flag queda vacío (`fiable=False`). En ese caso el optimizador no usa el cartel: usa solo el precio de venta.
- Sensibilidad, no regla principal: umbral 0 %, 5 % y 15 %, y la moda en lugar de la mediana. No elijas la variante que “queda mejor”.
- La tarjeta no entra.

Modelo A, código listo, sin entrenar. Predice la prima relativa de cada cadena frente al precio habitual del producto, `r_ic = log(precio habitual_ic) − media entre cadenas`. No usa precios de otras cadenas como features, ni el precio del día, ni “nivel de precio”. Features: categoría, marca, marca propia frente a nacional, gramaje, unidad, cadena, grupo, y la interacción cadena × categoría. La intensidad promocional de la categoría, si se usa, se calcula solo dentro del fold de entrenamiento. Líneas base: la cadena ganadora más frecuente, y la ganadora por categoría. Validación: grupos por EAN, más un holdout temporal. Métrica que le importa al optimizador: regret en soles (precio en la cadena predicha menos el mínimo). A solo rellena celdas sin precio observado. Elegir el menor precio ya visto no es A.

Modelo B, código listo, sin entrenar. Unidad: cadena, SKU y día t en que el producto no está en oferta real. Target: entra en oferta real en los próximos 7 días, y por separado en los próximos 14. El horizonte de 30 días no se entrena. Segunda salida: profundidad esperada de esa caída. Features solo con información hasta t (días desde la última oferta real, frecuencia del SKU y de la categoría, si el cartel de hoy es fantasma, `promo_end`, si una cadena hermana ya bajó, día de semana y quincena). Prohibido usar agregados calculados con el panel completo, porque miran el futuro. Validación temporal con un embargo de al menos h días entre entrenamiento y prueba. Línea base predictiva: la tasa histórica de ofertas del SKU o de la categoría. Línea base de decisión: comprar hoy. Métricas: PR-AUC, calibración y el ahorro de la política en un backtest, cuando haya panel. Si el producto ya está en oferta real hoy, se compra hoy por regla, no por B.

Optimizador, versión principal, prescriptiva. No es A.

- Minimiza el costo de hoy de la canasta de trabajo: cada ítem en la cadena más barata entre las que lo tienen emparejado, con precio de venta observado y `available` distinto de falso.
- El precio que se paga es `price`. C no cambia ese monto. C cambia la línea base (no contar el tachado como ahorro) y, cuando B exista, la decisión de esperar: un fantasma no es motivo para comprar ya ni para esperar.
- Un agotado es infactible. No arrastres el precio de `precios_faltantes.py` como si se pudiera comprar.
- Tottus no publica stock. No lo inventes ni lo trates como siempre disponible. Entrega el mismo plan en dos escenarios: con Tottus y sin Tottus.
- La versión principal no penaliza comprar en varias cadenas. Deja un escenario opcional, apagado por defecto, con un costo de visita `F_c` y un tope de cadenas. No es el resultado principal.
- Si no hay canasta de trabajo o C no es fiable, el optimizador igual corre como comparador del precio observado.

Línea base del sistema: una sola cadena, la misma para toda la canasta, comprando hoy y tratando el tachado como si fuera ahorro. Defínela en código antes de calcular cualquier ahorro. El sistema aporta si el costo queda debajo de esa regla, medido fuera de muestra cuando haya panel. Hoy no reportes ese ahorro como hallazgo.

## Qué construir

1. Anota estas decisiones en `BITACORA.md`, en la entrada del 22-09, reemplazando la pregunta vieja y la fila que llama “optimizador” al Modelo A. Alinea la pregunta y la subsección de modelos de `informe_avance.txt` con este prompt. No inventes cifras. No reescribas el resto del informe.
2. Canasta. Localiza la canasta básica alimentaria del INEI (documento, fecha, URL). Transcríbela a `data/canasta_oficial.csv` con id, descripción, grupo, unidad y cantidad. Si no puedes fijar la fuente con un documento real, no simules la lista: deja el esquema, el script de cruce y di qué documento falta. El script de cruce lee esa lista y el panel, aplica las reglas de arriba y escribe `data/canasta_trabajo.csv` (ítem, cadena, SKU, EAN si hay, cantidad, regla usada). Tests con filas sintéticas, sin red.
3. C consumible. Una tabla diaria por cadena, SKU y fecha, con precio habitual, descuento real, descuento anunciado (`1 - price/regular_price` cuando ambos existen), fantasma, rebaja silenciosa, fiable y la referencia por moda. La regla principal es la de las decisiones. La sensibilidad se calcula aparte, no pisa la columna principal. `oferta_real.py` puede crecer; no dupliques la regla en otro sitio.
4. Optimizador v0 en `analisis/optimizador.py`. Lee la canasta de trabajo, los precios del día y la tabla de C. Escribe el plan (ítem, cadena, precio pagado, costo de la canasta) y el costo de la línea base. Incluye el escenario opcional de visita apagado por defecto y los dos escenarios de Tottus. Tests sintéticos: el agotado no se elige; un tachado fantasma no cuenta como ahorro de la línea base; el precio pagado es `price`.
5. A y B como módulos entrenables (`analisis/modelo_a.py`, `analisis/modelo_b.py`) con la especificación de arriba, una función `fit` que refuse correr si el panel no cubre el mínimo de días (A: del orden de 60; B: orígenes completos para h=7 y h=14 después de los 21 días de C), y tests de que un feature no cambia si alteras días futuros. No llames `fit` sobre `data/daily`. No agregues una dependencia pesada si el modelo aún no se entrena: deja el algoritmo (gradient boosting) detrás de un import claro y el esqueleto testeado con datos sintéticos mínimos.
6. No regeneres el informe con `analisis.run_all` salvo que un cambio de columnas lo exija, y en ese caso no inventes el texto que comenta las tablas.

Al terminar, lista qué quedó corriendo ahora, qué quedó listo para entrenar después, y qué no se pudo hacer porque faltaba la fuente del INEI o días de panel.
