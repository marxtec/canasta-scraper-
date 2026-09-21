# Bitácora de diseño

Registro de las conversaciones de trabajo sobre el **rumbo** del proyecto: qué
se decidió, con qué evidencia y qué quedó abierto. No documenta el código —de
eso se encarga `README.md`— sino las decisiones que el código todavía no
refleja.

Una entrada por sesión, titulada con lo que se vino a resolver. Los números
citados se midieron sobre los datos del repo en la fecha indicada y hay que
**recalcularlos sobre el panel acumulado** antes de que entren al paper.

---

## 2026-09-20 · Cómo funciona el repositorio

Recorrido completo del pipeline para tener un mapa común antes de tocar nada.

**Flujo:** `config/retailers.yml` → `collect.py` → cliente (`vtex` |
`falabella_catalyst`) → `normalize` → `storage`. El cliente baja el árbol de
categorías, filtra alimentos **por nombre** (no por id: los ids cambian y no
son comparables entre cadenas), camina hasta las hojas, pagina, deduplica por
`productId`, y se guarda en dos capas: JSON crudo comprimido + CSV normalizado.

**Las tres piezas donde vive la dificultad real:**

1. `parse_quantity()` — el gramaje no viene en `measurementUnit`, hay que
   sacarlo del nombre. El modo de fallar peligroso no es el gramaje ausente
   sino el **presente y equivocado**.
2. `eans.py` — caché persistente porque el EAN es estático. `data/ean_tottus.json`
   se versiona: si se pierde, nunca converge.
3. `storage.py` — el crudo existe para poder reprocesar cuando aparezca un bug
   en el parser.

**Principio de fondo:** lo único irrecuperable es el precio de hoy. Todo lo
demás (EAN, gramaje, emparejamiento, precio con tarjeta) se repara después
sobre el crudo. De ahí la tolerancia a fallos: una cadena caída no tumba la
corrida, el EAN va en `try/except`, el navegador es un paso aparte.

---

## 2026-09-20 · Pulir el objetivo: ¿redefinir la Propuesta 2?

Contraste de la propuesta original (`Analítica de la Web — Propuestas de
Trabajo.pptx`, slides 4–9) contra lo que los datos ya muestran.

### Qué se cae de la propuesta

| Elemento | Realidad |
|---|---|
| **Makro** | No existe: tienda online muerta, dominio parqueado. Sacar de la slide. |
| **"4 súper (si alcanza: Vivanda)"** | Ya alcanzó. Son **5 cadenas**. |
| **IPC-Web vs IPC del INEI como núcleo** | **No viable.** Panel desde el 19-09-2026; para diciembre hay 2–3 observaciones mensuales del INEI. No se entrena ni se compara nada con n=3. |
| **La comparación con el INEI en general** | Confundida por canal: el INEI releva mercados de abastos, el panel solo supermercado. Una brecha no sería inflación diferencial. |
| **"50–100 alimentos"** | Número inventado. La respuesta empírica son **917** productos con EAN en las 5 cadenas. |

**Lo que aguanta:** el esquema de la slide 6 (implementado, 22 columnas), el
precio por kg, `on_sale` vs inflación, los tres antecedentes (Cavallo &
Rigobon 2016, Jaworski 2021, Bueno 2024) y la fórmula de la canasta.

### El problema de fondo (no es de datos)

*"Un tablero de esta semana, no un oráculo"* describe un **producto**, no una
pregunta de investigación. Un tablero no puede estar equivocado; un paper
necesita una afirmación falseable. Y el hueco declarado —"no hay un paper que
publique la bolsa en soles"— es un hueco de **entregable**, no de conocimiento.

### La evidencia que apareció

Medido sobre `2026-09-20`, replicado sobre `2026-09-19`, agregando por mediana
cuando varios SKU comparten EAN (los duplicados son el 0.1%, irrelevantes):

```
Mismo EAN, mismo día, precio IDÉNTICO entre pares de cadenas:

  metro + wong         5.444 EAN   67.7%   brecha mediana  0.0%   <- Cencosud
  wong + vivanda       1.987 EAN   46.5%                   1.8%
  plaza_vea + tottus   3.081 EAN   44.7%                   1.4%
  metro + vivanda      1.873 EAN   34.1%                   3.7%
  metro + plaza_vea    2.291 EAN   27.5%                   5.3%
  wong + plaza_vea     2.337 EAN   24.7%                   6.1%
  plaza_vea + vivanda  4.725 EAN   23.8%                   2.4%   <- SPSA
  metro + tottus       3.325 EAN   18.2%                   6.3%
  wong + tottus        3.421 EAN   15.3%                   7.1%
  vivanda + tottus     2.466 EAN   14.4%                   5.6%

Dispersión (brecha max/min, 2026-09-20):
  EAN en >=2 cadenas  11.019   mediana  4.0%   p90 28.1%
  EAN en >=3 cadenas   4.711   mediana 10.3%   p90 34.9%
  EAN en  =5 cadenas     906   mediana 13.9%   p90 41.4%
```

**Dos hallazgos:**

- **Los grupos corporativos no se comportan igual.** Cencosud sirve el mismo
  precio en Metro y Wong 2 de cada 3 veces — backend de pricing compartido, que
  es un hecho de arquitectura web. SPSA **no**: Plaza Vea y Vivanda coinciden
  solo 23.8% porque Vivanda está posicionada premium. Mismo grupo, dos
  estrategias opuestas.
- **Plaza Vea coincide más con Tottus (44.7%) que con su hermana Vivanda
  (23.8%).** La competencia disciplina más el precio que la propiedad común.
  Contraintuitivo y falseable.

### Redefinición propuesta

Mantener scraper y canasta; **mover el centro de gravedad** de *"medimos la
inflación antes que el INEI"* a *"medimos cómo se forma el precio en la web del
retail limeño"*:

> **¿El precio de un mismo producto físico (mismo EAN) en la web de los
> supermercados de Lima se fija por cadena, por grupo corporativo, o por
> competencia?**

Sub-preguntas: (1) dispersión, (2) estructura de propiedad y formato,
(3) rigidez y liderazgo de precios, (4) promoción vs. inflación.

**Por qué es mejor:**

- **Desriesga.** (1) y (2) ya están contestadas con un día de datos. El nowcast
  deja de ser el núcleo y pasa a anexo honesto.
- **Convierte la peor limitación en el diseño.** "Solo mido supermercado" es
  fatal si la pregunta es inflación del hogar; es **irrelevante** si la pregunta
  es cómo compiten los supermercados entre sí.
- **Convierte el peor caveat en la variable independiente.** "Metro y Wong son
  la misma empresa" deja de ser molestia y pasa a ser el tratamiento.
- **Es más Analítica de la Web.** Dos storefronts distintos sirviendo el mismo
  precio desde el mismo backend es un hecho sobre infraestructura web. Y la
  asimetría de publicación del EAN (Metro 100%, Plaza Vea 58%, Tottus solo en
  ficha) es contribución metodológica por sí sola.
- **La canasta sobrevive entera** como entregable de cierre; solo deja de
  cargar el peso del argumento.

### Advertencia sobre la rigidez de precios

Medición inicial: **0.48%** de los precios cambió entre el 19 y el 20 de
septiembre (Metro 0.80%, Wong 0.75%, Tottus 0.28%, Plaza Vea 0.24%, Vivanda
0.04%).

**No usar esta cifra.** El 19 y 20 de septiembre de 2026 son **sábado y
domingo**; los súper hacen repricing en días hábiles. Además, como cambió tan
poco, los dos días no son una replicación independiente: confirman que el
pipeline es estable, no que el hallazgo aguante en el tiempo. **Rehacer con
lunes–martes.**

### Horizonte

| Cuándo | Qué |
|---|---|
| Esta semana | Corregir slides (Makro fuera, Vivanda dentro, 5 cadenas). Correr el backfill de EAN. Confirmar tasa de cambio con días hábiles. |
| Octubre | Cerrar la canasta sobre panel acumulado. Escribir la regla de precios faltantes (§2 del README) — sigue abierta y sigue siendo lo más importante. |
| Noviembre | Rigidez y liderazgo de precios, ya con 6+ semanas. |
| Diciembre | Canasta en soles + IPC-Web como anexo descriptivo, sin pretensión de nowcast. |

---

## 2026-09-20 · ¿Dónde entra el modelo de ML/deep?

> **Corregido en la entrada siguiente.** Lo de abajo sigue siendo válido como
> tecnica, pero el emparejamiento es **preprocesamiento**: construye el dataset,
> no lo explota. Va en la seccion de datos, no en la de modelamiento.

El curso exige un modelo. La redefinición de arriba es descriptiva y no lo
tiene, así que hay que ubicarlo donde sea **necesario**, no decorativo.

### Dónde NO meterlo

*"Predecimos el precio de mañana con un LSTM"*. El 99.5% de los precios no
cambia de un día a otro. Un modelo que predice "mañana igual que hoy" acierta
~99% y no aprendió nada. Es una tautología con apariencia de resultado.

### Dónde sí: emparejamiento entre cadenas sin EAN

Es lo que el README ya declara como "el grueso del trabajo analítico" (§4) y
hoy se resuelve solo a medias.

```
Sin EAN publicado:     Plaza Vea 41%  ·  Vivanda 34%  ·  Tottus 15%
                       Metro 6%       ·  Wong 7%
EAN único a 1 cadena:  13.119 de 24.188 productos
Universo no emparejable: ~10.700 filas + 13.119 productos  (~la mitad)
```

No es una mitad cualquiera: es **marca propia, granel y panadería**, justo
donde las cadenas se diferencian. Hoy no se puede responder "¿la marca propia
de Metro es más barata que la de Plaza Vea?".

### El emparejamiento por nombre NO se resuelve sin modelo

Usando los pares con EAN compartido como verdad de campo (Metro vs Plaza Vea,
2.294 pares reales):

| Métrica | Valor |
|---|---|
| Nombre idéntico tras normalizar | **3.5%** |
| Jaccard de tokens ≥ 0.8 | 24% |
| Jaccard mediana / p10 | 0.60 / **0.30** |
| Negativos con Jaccard ≥0.8 (264k pares muestreados) | **0** |

Pares que son el **mismo producto físico** y que un match textual no agarra:

```
[0.00]  Tostadas Integrales 142 g
        Tostada Integral BAUDUCCO Paquete de 142g
[0.07]  Ajos en Pasta Sibarita Pack 3 Unid x 24 g
        Ajo SIBARITA Sobre 24g Paquete 3un
[0.08]  Tortillas Multigrano Mexi Nachos Bolsa 90 g
        Chips de Maíz PERUNACHOS Multigrain Bolsa 90g
```

Singular/plural, marca presente en una y ausente en la otra, orden invertido,
sinónimos ("tortillas" ↔ "chips de maíz"). El texto es **preciso pero ciego**:
cuando dice "sí" acierta (0 falsos positivos a Jaccard ≥0.8), pero casi siempre
dice "no sé". Un umbral da ~24% de recall; **el 76% restante es el modelo**.

### Por qué es metodológicamente fuerte

**El EAN regala las etiquetas** — no se anota nada a mano:

- **Positivos:** ~31.000 pares sumando las 10 combinaciones de cadenas.
- **Negativos difíciles:** misma categoría y marca, EAN distinto. Gratis.
- **Evaluación:** holdout de EAN, se esconde el EAN, se mide precision/recall/F1
  contra la verdad.
- **Aplicación:** entrenar donde hay EAN, aplicar donde no lo hay. El modelo
  **aprende de la parte observable de la web para recuperar la que la web no
  publica** — eso es lo que lo hace un trabajo de Analítica de la Web.

### Arquitectura propuesta

```
1. BASELINE (obligatorio, para tener contra qué comparar)
   TF-IDF char n-gram + coseno              -> ~24% recall

2. DEEP (el corazón)
   Sentence-BERT multilingüe fine-tuned con pérdida contrastiva
   sobre los ~31k pares con EAN. Arquitectura siamesa.
   Opcional: cross-encoder para re-ranking del top-k.

3. ML CLÁSICO (clasificador de decisión)
   Gradient boosting sobre:
     similitud del embedding · marca coincide · ratio de net_quantity
     misma unidad · ratio de precio · misma categoría
   Salida: probabilidad de ser el mismo producto + umbral calibrado.
```

La capa 3 importa: **gramaje y precio** son señales potentes que el texto solo
no aprovecha, y ya están parseadas en el CSV. Ojo: el README midió que el 15%
de los pares con EAN **discrepa en peso**, así que el modelo debe tolerar ese
ruido, no confiar ciegamente.

### Cómo encaja con la redefinición

No la parcha, la **habilita**:

- La canasta crece de ~917 a varios miles.
- **Entra la marca propia**, donde el efecto de grupo corporativo debería ser
  *más* fuerte (el Bell's de Plaza Vea vs. la marca Metro). La hipótesis central
  se vuelve testeable donde más importa.
- Los desacuerdos modelo-vs-EAN **detectan errores de catalogación**. Ya salió
  uno: `"Pitacrisps Sal de Maras 80g"` y `"Galleta Salada TABLA GOURMET Caja
  100g"` comparten EAN y no son el mismo producto — una cadena tiene el código
  mal. Hallazgo publicable sobre calidad de datos web.

### Planes B (si piden más de un modelo)

1. **Imputación de precios faltantes** — resuelve la §2 del README, que sigue
   abierta y es la decisión metodológica más importante.
2. **Extracción de gramaje como NER** — reemplaza el parser de regex, la parte
   más frágil. Auto-etiquetado desde los casos donde el regex es confiable.
3. **Clasificación a COICOP** — para mapear al IPC de alimentos. Bueno (2024)
   hizo exactamente eso: antecedente citable.

### Abierto

- **Confirmar si el curso exige deep learning o acepta ML clásico.** Decide si
  la capa 2 es obligatoria; si basta ML clásico, se ahorran ~2 semanas.
- Construir el dataset de entrenamiento (~31k positivos + negativos difíciles)
  para ver el volumen real antes de comprometerse.

---

## 2026-09-20 · El modelo tiene que ir DESPUÉS del preprocesamiento

Corrección de rumbo: el proyecto pide **una parte de analytics y otra de
modelamiento**. El emparejamiento entre cadenas construye el dataset, así que
es preprocesamiento y va en la sección de datos. El modelo debe consumir el
panel ya limpio y producir una respuesta.

### Cuánta señal hay en el panel procesado

```
=== OFERTAS (2026-09-20) ===
cadena        filas    on_sale   desc.mediano
plaza_vea    11.860      32%         15%
wong         12.415      29%         17%
metro         9.520      28%         16%
tottus       12.340      17%         13%
vivanda       7.852      13%         14%
                     TOTAL 24.4%

=== DINÁMICA (sab -> dom, 52.451 SKU seguidos) ===
  subio precio     0.27%      entra a oferta   0.11%
  bajo precio      0.17%      sale de oferta   0.17%
  altas de catalogo  479      bajas              442   (~1% diario)
```

Una de cada cuatro filas está en oferta, con 13–17% de descuento mediano, y la
intensidad promocional varía 2.5x entre cadenas (Vivanda 13% vs Plaza Vea 32%).
Señal abundante.

### La idea que ordena todo

La slide 4 de la propuesta dice que quien compra decide **tres cosas**. Dos de
ellas son problemas de predicción que nunca se operacionalizaron:

| Decisión de la propuesta | Qué es |
|---|---|
| *"si compra ahora o espera"* | **Predicción** -> Modelo B |
| *"en qué cadena"* | **Predicción** -> Modelo A |
| *"si la quincena le alcanza"* | La canasta en soles -> analytics |

No hay que inventar un modelo ni pegarlo con cinta: **ya estaba prometido en
septiembre, solo no se construyó.** Resuelve además el reproche de que el
tablero no es falseable — un modelo tiene métricas y puede equivocarse.

### Modelo A — "¿en qué cadena?" · disponible hoy

> ¿Para qué productos vale la pena comparar entre cadenas, y dónde está más
> barato?

- **Target:** brecha de precio entre cadenas del mismo producto (regresión) y/o
  cuál cadena es la más barata (clasificación multiclase).
- **Features:** categoría, marca, marca propia vs nacional, `net_quantity`,
  `unit`, nivel de precio, intensidad promocional de la categoría.
  **Sin usar los precios de las otras cadenas** — sería fuga trivial.
- **Datos:** 11.019 EAN en >=2 cadenas, hoy. No espera nada.
- **Interpretación:** SHAP sobre qué explica la dispersión. Convierte el
  hallazgo de grupos corporativos en efecto cuantificado.
- **Riesgo:** el efecto de cadena puede dominar y volverlo trivial. Se mitiga
  modelando la *magnitud* de la brecha, no solo quién gana, y apoyándose en las
  interacciones cadena x categoría.

### Modelo B — "¿compro ahora o espero?" · noviembre

> ¿Probabilidad de que este producto entre en oferta en los próximos 7 días?

- **Target:** `P(on_sale en t+1..t+7)` o la caída de precio esperada.
- **Features:** días desde la última oferta, duración típica de oferta en la
  categoría/marca, precio vs su propia mediana histórica, precio vs
  competidores, si la cadena hermana ya bajó, día de semana, quincena.
- **Datos:** requiere panel acumulado. **Este es el modelo que justifica
  recolectar todos los días** — sin historia no existe.

**RIESGO A MEDIR YA:** con 0.11% de entradas a oferta por día, una ventana de 7
días da ~0.8% de positivos: desbalance severo. Pero ese 0.11% es de fin de
semana y casi seguro subestima. **Remedir con lunes–viernes antes de
comprometerse.** Si sube a 1–2% diario el modelo es cómodo; si se queda en
0.1%, cambiar el target a horizonte de 14–30 días o modelar la caída de precio
como regresión.

### Modelo C — opcional

**Duración de la oferta** como análisis de supervivencia (hazard en tiempo
discreto): dado que entró en oferta, ¿cuántos días dura? Conecta directo con
Bueno (2024), que estudió duración de precios.

### Cómo queda partido el trabajo

```
ANALYTICS (descriptivo, ya hay resultados)
  · Dispersión entre cadenas: 13.9% mediana en los 906 productos comunes
  · Estructura de grupos: Cencosud centraliza 67.7%, SPSA solo 23.8%
  · Rigidez de precios          (pendiente: remedir en días hábiles)
  · Intensidad promocional: 13% Vivanda ... 32% Plaza Vea
  · La canasta en soles por cadena      <- decisión #3, entregable de cierre

PREPROCESAMIENTO (sección de datos, NO es el modelo)
  · Emparejamiento por EAN + extensión sin EAN
  · Reconciliación de gramaje
  · Regla de precios faltantes

MODELAMIENTO
  · Modelo A: ¿en qué cadena?     <- decisión #2   · disponible hoy
  · Modelo B: ¿compro o espero?   <- decisión #1   · noviembre
```

### Abierto

- **Remedir la tasa de entrada a oferta en días hábiles.** Decide si el Modelo B
  es viable a 7 días o hay que alargar la ventana.
- Decidir si el Modelo C entra o queda como extensión.

---

## 2026-09-20 · ¿Qué cambiar en el scraper para el nuevo objetivo?

Respuesta corta: **poco, y casi todo son adiciones**. La cadencia diaria, la
sobre-recolección, la capa de crudo, el canal fijo y la caché de EAN fueron
diseñados para "medir inflación" y sirven igual o mejor para "cómo se forma el
precio": el Modelo B no existe sin panel diario, y la capa de crudo es lo que
permitió encontrar todo lo de abajo sin volver a scrapear.

### Lo que el crudo ya contiene y el README dice que no

Revisado sobre `data/raw/2026-09-20__{plaza_vea,metro,vivanda}.json.gz`.

**1. El precio con tarjeta SÍ está en la API.** Los teasers de Metro traen el
porcentaje y la vigencia:

```
[TCENCO] Set26 - Supermercado - Con 5% Dscto Con TC Metro del 01al30 Setiembre   2.135
[TCENCO] Set26 - Supermercado - Con 10% Dscto Con TC Metro del 01al30 Setiembre  1.388
[TCENCO] Set26 - Supermercado - Con 8% Dscto ...                                   315
[TCENCO] Set26 - Supermercado - Con 4% Dscto ...                                   127
```

`card_price = price x (1 - pct)`. El ejemplo del README (S/17.76 con tarjeta
vs S/18.50) es exactamente 4%, el tier que aparece ahí. **`browser.py` es
innecesario para Cencosud** (Metro y, casi seguro, Wong). La sección "card_price
no disponible como número" del README está equivocada para estas cadenas.

**2. Los teasers traen ventana de vigencia** (`del 01al30`, `del 17al28`).
Es `promo_end`: para el Modelo B, saber cuándo termina la oferta.

**3. Un tipo de promo invisible hoy.** Plaza Vea `"Promo paga x lleva y"` en
156 productos: el precio unitario no cambia, `on_sale = False`, el panel no la
ve. `"con Tarjeta Oh!"` en 185: flag sin monto.

**4. Features ya descargadas y no extraídas:**

| Campo | Cobertura | Uso |
|---|---|---|
| Metro `Octogonos` | 37% | Feature: lo ultraprocesado se promociona distinto |
| Metro `Origen` Nacional/Importado | 75% | Feature |
| Plaza Vea `Vendido por: Marcas Aliadas` | **13%** | Terceros del marketplace |
| Vivanda `Contenido Neto` | 33% | Validar el parser de gramaje contra dato declarado |
| `measurementUnit=kg, unitMultiplier=0.25` | ~200/cadena | Peso variable: el multiplicador es el peso real; el parser hoy lo trata como 1 kg |

**5. `referenceId` NO rescata EANs faltantes**: 0 casos en las tres cadenas.
Confirma que el emparejamiento por modelo es necesario; no hay atajo.

### Cambios al scraper — lo irreversible, hacer ya

**A. Guardar el árbol de categorías por corrida.** Se baja cada día y se
descarta. Cuando un producto desaparece (~442/día) no se puede distinguir
"la cadena lo deslistó" de "reorganizó el árbol y el scraper dejó de verlo".
Para el Modelo B, un hueco por reorganización trunca la duración de la oferta.
Pesa KB. Es lo único de esta lista que no se recupera después.

**B. Guardia de volumen por categoría raíz, no global.** El 50% detecta un
colapso, no una hoja que se cayó (-5%). Comparar hojas y filas por raíz contra
la corrida anterior, avisar a -15%. Va en `_avisar_si_cae_el_volumen`.

**Verificar:** cruzar `Vendido por: Marcas Aliadas` (13%) con `seller_name`.
El README midió 5 de 396 filas de terceros; si `_best_seller` no captura esos
1.599, hay precios de terceros entrando como precio de Plaza Vea y contaminan
el Modelo A.

### Cambios a `normalize` — recuperables, reprocesando el crudo

- `card_price` derivado del % del teaser (Cencosud).
- `promo_end` parseado del teaser.
- `promo_type`: `descuento` / `paga_x_lleva_y` / `tarjeta`.
- Columnas nuevas: `octogonos`, `origen`, `vendido_por`, `contenido_neto_declarado`.
- Peso variable: usar `unitMultiplier` cuando `measurementUnit == 'kg'`.

Todo se aplica hacia atrás sobre los `.json.gz` existentes.

### Retirar

**`browser.py` sale del camino crítico.** Su única justificación era el precio
con tarjeta, y está en la API para Cencosud. Queda como opcional para los 185
de "Tarjeta Oh!".

### Abierto

- Verificar `Marcas Aliadas` vs `seller_name` antes de tocar nada.
- Confirmar que los teasers de Wong tienen el mismo formato que Metro.
- Corregir el README: la sección de `card_price` y la de navegador.

---

## 2026-09-20 · Aplicar los cambios al scraper (todo menos retirar `browser.py`)

Se aplicaron los puntos 1–8 de la entrada anterior. `browser.py` se queda por
decisión del equipo.

### Tres verificaciones previas, una me corrigió

| Duda | Resultado |
|---|---|
| ¿Los 1.599 "Marcas Aliadas" de Plaza Vea contaminan `seller_name`? | **No.** `_best_seller` ya los separa: en 793 Plaza Vea es el vendedor real (la etiqueta es marketing) y los ~800 restantes salen con su tercero. Terceros reales: ~7%, no el 1% del README. Sin cambio en el código. |
| ¿Wong tiene el mismo formato de teaser que Metro? | **Sí**, con variantes (`20% dscto Con TC BBVA Wong`) y ventanas de **fin de semana** (`del 18al20`). |
| ¿`unitMultiplier=0.25` significa que el parser pone 1 kg donde son 250 g? | **No, estaba equivocado.** `Plátano x kg, mult=0.16, price=2.99`: S/2.99 es por kilo (S/18.7/kg sería absurdo); el multiplicador es cuánto pesa una unidad en el carrito. El parser hace lo correcto. Se guarda el multiplicador como columna, no se toca `net_quantity`. |

### Lo que cambió

**`normalize.py` → v1.2, 22 → 30 columnas.**
- `card_price` derivado del `%` del teaser cuando menciona tarjeta. Test de
  regresión con el caso real del README: S/18.50 con 4% = S/17.76.
- `promo_start` / `promo_end` desde `del 01al30 Setiembre`. Si hay varios
  teasers gana el mayor descuento y la ventana es la de ese mismo teaser.
- `promo_type`: `descuento` · `tarjeta` · `paga_x_lleva_y`.
- `vendido_por`, `origen`, `octogonos`, `contenido_neto_declarado` desde las
  especificaciones sueltas de VTEX, tal cual las publica la cadena.
- `unit_multiplier`.

**`storage.py` → tercera capa `data/trees/`.** Árbol completo + hojas
recorridas por cadena y día. `vtex.py` y `catalyst.py` exponen `last_tree`
y `last_leaves`; `collect.py` los guarda aunque el catálogo venga vacío.

**`collect.py` → guardia de volumen en tres niveles.** Global (50%), por
categoría raíz (15%, mínimo 50 filas) y por hoja recorrida contra el árbol de
la corrida anterior. Probado offline: dispara con Lácteos −40% y una hoja
perdida, calla con el total a −17%.

**`collect.py --reprocess`.** Re-normaliza `data/daily/` desde `data/raw/`
con el parser actual, rescatando el timestamp del CSV existente y volviendo a
aplicar la caché de EAN de Tottus. Es el comando que faltaba para que la capa
de crudo cumpla su promesa.

**`scrape.yml`** copia `data/trees/` al commit del CI. Sin esto los árboles
se generarían en el runner y se perderían.

**README** corregido: `card_price` sí está en la API para Cencosud; la
sección del navegador queda para las cadenas que no publican el porcentaje.

### Resultado del reprocesamiento (10 archivos, mismas filas que antes)

```
cadena      card_price  promo_end   pxl  vend_por  origen  octog  cont.neto
metro              43%        43%  0.0%        0%     75%    37%        0%
plaza_vea           0%         0%  1.3%      100%     15%     0%       33%
tottus              3%         0%  0.0%        0%      0%     0%        0%
vivanda             0%         0%  0.0%        0%     13%     0%       33%
wong               22%        22%  0.0%        0%     71%    34%        0%
```

Los 156 `paga_x_lleva_y` de Plaza Vea que antes eran invisibles ahora están
etiquetados (65 con descuento, 10 con descuento y tarjeta, 81 solos).

### Lo que esto habilita

- **Modelo B** tiene `promo_end`: para el 43% de Metro y el 22% de Wong sabe
  cuándo termina la oferta en vez de adivinarlo.
- **Modelo A** tiene `octogonos`, `origen` y `vendido_por` como features, y
  puede filtrar terceros con `seller_name`.
- El panel deja de subcontar promociones en Plaza Vea.
- Un cambio en el árbol de una cadena se detecta el mismo día.

### Cierre (2026-09-20, noche)

- Pusheado como `3a90614` una vez aceptada la invitación de colaborador
  (estaba pendiente; GitHub no da permisos hasta aceptarla).
- **Marx corrigió un bug del redondeo** en `0c66257`: `round()` con floats
  daba `12.82` donde Metro muestra `12.83`, un céntimo de menos en 2.224 de
  6.789 filas con `card_price` derivado (33%). Fix con `Decimal` +
  `ROUND_HALF_UP`, verificado contra la web con Playwright (3 de 3
  coinciden). Datos reprocesados. El método queda validado de forma
  independiente.
- CI en verde: tests y recolección.

### Abierto

- Primera corrida real con `data/trees/` es la del cron del 21-09 a las
  08:07 Lima: revisar que el commit del bot incluya
  `data/trees/2026-09-21__*.json.gz`.
- `resumen_bitacora.tex` sigue sin versionar en la raíz.

---

## 2026-09-21 · Regla de precios faltantes: medida, implementada y decidida (provisional)

Cierra la §2 del README, que estaba declarada como la decisión metodológica
más importante y seguía abierta. Primero se midió sobre el panel real,
después se implementaron las tres reglas candidatas y se compararon. El
código es `analisis/precios_faltantes.py`; los números salen de
`data/derived/huecos_resumen__2026-09-20.csv` y
`canasta_reglas_resumen__2026-09-20.csv` y se recalculan con
`python -m analisis.run_all`.

### Qué se midió (19 y 20 de septiembre, 2 días)

Un hueco es un (cadena, SKU, día) sin precio válido desde la primera vez
que el SKU aparece. Tipos: agotado en listado (`available=False` o precio
0), desaparecido con la hoja recorrida (la cadena lo deslistó),
desaparecido sin la hoja (cambió el árbol) y desaparecido sin árbol
guardado (no se puede distinguir; los árboles se guardan desde el 21-09).

| Cadena | SKU | Celdas SKU-día | Huecos | Tasa | Agotado en listado | Desaparecido (sin árbol) |
|---|---|---|---|---|---|---|
| Metro | 9.549 | 19.074 | 151 | 0,79 % | 122 | 29 |
| Plaza Vea | 11.860 | 23.719 | 6.113 | **25,8 %** | 6.113 | 0 |
| Tottus | 12.711 | 25.026 | 371 | 1,48 % | 0 (no publica stock) | 371 |
| Vivanda | 7.852 | 15.704 | 5.194 | **33,1 %** | 5.194 | 0 |
| Wong | 12.444 | 24.851 | 113 | 0,45 % | 84 | 29 |

**El hallazgo no es el agotamiento: es que SPSA publica precio sin
stock.** Plaza Vea y Vivanda listan con precio uno de cada cuatro y uno de
cada tres productos que declaran no disponibles; Cencosud casi nunca lo
hace y Tottus no publica stock. Esto tiene dos consecuencias:

1. Para la canasta, un precio al que no se puede comprar no es un precio.
   Al exigir `available != False`, la base de EAN con precio en las 5
   cadenas el 19-09 **cae de 915 a 701**.
2. Para la dispersión (bloque 3) apenas importa: `--solo-disponibles`
   mueve la masa en cero de 25,0 % a 26,4 % y Metro–Wong de 66,6 % a
   66,6 %; el 8,4 % de las observaciones EAN-cadena-día comparables
   viene de filas sin stock (Vivanda 33 %, Plaza Vea 16 %, el resto 0 %).

Las rachas duran 2 días de 2 (censuradas en el 92–100 % de los casos):
con dos días no se puede medir duración. Es exactamente lo que el panel
acumulado va a responder.

### Las tres reglas, sobre la canasta de referencia (701 EAN, q_i = 1)

| Regla | Qué hace | Cuándo falla |
|---|---|---|
| **Arrastre** | último precio observado, tope 7 días (INEI) | si el hueco esconde un cambio de precio, lo arrastra |
| **Imputación** | último precio × variación mediana de su categoría en esa cadena | si la categoría es heterogénea, inventa movimiento |
| **Exclusión** | el ítem sale ese día; índice encadenado de composición emparejada | si los huecos no son aleatorios (se agota lo que sube), sesga |

Con dos días hay 8 celdas faltantes de 7.010 (Tottus 7, Wong 1) y las
tres reglas difieren en menos de 0,001 puntos de índice en todas las
cadenas. **El contraste todavía no dice nada**, y así se declara.

### Decisión (provisional, con criterio de revisión preregistrado)

- **Regla principal: arrastre con tope de 7 días** para los huecos de tipo
  *agotado en listado* y *desaparecido con hoja recorrida*. Es la práctica
  del INEI, es la más simple y es la única que no inventa movimiento.
- **Los huecos por cambio de árbol no son huecos**: se tratan como
  arrastre sin tope, porque el producto sigue a la venta y fue el scraper
  el que dejó de verlo.
- **Pasado el tope, exclusión con composición emparejada.** Un producto
  que lleva más de una semana sin precio se ha ido, y arrastrarlo es
  fingir que sigue.
- **Imputación por categoría queda como prueba de robustez**, no como
  regla: se reporta la diferencia con la principal.
- **Criterio de revisión:** cuando el panel tenga 14+ días,
  `run_all` reporta la divergencia máxima entre reglas por cadena. Si
  supera 0,5 puntos de índice en alguna cadena, la divergencia se publica
  como resultado y la regla principal pasa a ser la exclusión emparejada
  (la que no depende de supuestos sobre el hueco). Si no la supera, la
  elección es irrelevante y se dice.

Lo que se decidirá con más panel: el tope (7 vs 14 días) se fijará con
la distribución real de duración de las rachas, que hoy está censurada.

---

## 2026-09-21 · Preregistro de hipótesis

Declarado **antes** de tener el panel completo, para poder demostrar que
no se buscó hasta que salió algo. La evidencia preliminar es de dos días
(19 y 20 de septiembre, fin de semana), recalculada hoy sobre los dos
días juntos con `analisis/dispersion.py` y `rigidez.py` con el criterio
de EAN de fabricante (12+ dígitos, sin prefijo 2). Lo que está "por
verse" se contrastará con `python -m analisis.run_all` sobre el panel
acumulado, sin cambiar umbrales.

### H1 · Uniformidad dentro del grupo corporativo

- **Se espera:** que la fracción de EAN con precio idéntico sea mayor
  dentro de un grupo que entre grupos, y que Cencosud sea mucho más
  uniforme que SPSA.
- **Se contrasta con:** `identico` de `dispersion_pares` para
  Metro+Wong y Plaza Vea+Vivanda, contra el promedio de los 8 pares de
  competidores, sobre todos los días.
- **La falsaría:** que Metro+Wong caiga por debajo del promedio de los
  competidores, o que Plaza Vea+Vivanda suba por encima de Plaza
  Vea+Tottus de forma estable.
- **Evidencia preliminar (2 días):** Metro+Wong 66,6 % (8.442 EAN-días);
  Plaza Vea+Vivanda 24,6 %; competidores entre 14,4 % y 46,5 %. La mitad
  de H1 (Cencosud) ya tiene soporte; la otra mitad (SPSA no uniforme) es
  la que la vuelve interesante y es la que hay que ver sostenerse.

### H2 · Competencia vs. propiedad común

- **Se espera:** que Plaza Vea coincida más con Tottus (mismo formato,
  distinto dueño) que con Vivanda (mismo dueño, distinto formato).
- **Se contrasta con:** `identico` y `brecha_mediana` de Plaza Vea+Tottus
  vs Plaza Vea+Vivanda, día a día (`dispersion_pares_diario`).
- **La falsaría:** que Plaza Vea+Vivanda supere a Plaza Vea+Tottus en la
  mayoría de los días del panel.
- **Evidencia preliminar:** 44,9 % vs 24,6 % en identidad; 1,4 % vs 2,9 %
  en brecha mediana. Los dos días dan lo mismo (45,1/44,7 vs 24,6/24,6).
  Por verse: si es estable en semanas con promociones distintas.

### H3 · Intensidad promocional según posicionamiento

- **Se espera:** que la intensidad promocional *real* (no el cartel)
  ordene las cadenas por posicionamiento: Plaza Vea y las Cencosud arriba,
  Vivanda abajo, Tottus en medio; y que una parte grande del cartel
  `on_sale` sea tachado permanente (precio ancla), no oferta.
- **Se contrasta con:** `oferta_real` y `tachado_permanente` por cadena
  (`oferta_real_cadenas`), que exigen 21 días de historia por producto.
- **La falsaría:** que el orden por `oferta_real` no coincida con el del
  cartel, o que `tachado_permanente` sea despreciable (< 10 % del cartel)
  en todas las cadenas.
- **Evidencia preliminar:** solo del cartel: Plaza Vea 34,1 %, Wong
  29,3 %, Metro 28,6 %, Tottus 16,9 %, Vivanda 13,3 % (20-09). Del
  tachado permanente **no hay evidencia**: el 100 % del panel es todavía
  no evaluable. Es la hipótesis más genuinamente abierta.

### H4 · Qué nivel domina la descomposición de varianza

- **Se espera:** que el producto explique casi toda la varianza del
  log-precio, y que de la varianza *intra*-producto (la dispersión entre
  cadenas), la parte sistemática por cadena sea pequeña frente a la
  idiosincrática; y que cadena × categoría explique más que grupo.
- **Se contrasta con:** `share_within` de `dispersion_varianza` (orden A).
- **La falsaría:** que grupo + cadena + cadena×categoría expliquen más de
  la mitad de la varianza intra-producto (precio fijado "por cadena"), o
  que grupo supere a cadena.
- **Evidencia preliminar:** producto 98,7 %; de la varianza
  intra-producto: cadena 3,5 %, cadena×categoría 1,3 %, grupo 0,6 %,
  idiosincrático 94,6 %. Estable en los dos días. Por verse: si con más
  días y más promociones la parte sistemática crece.

### Lo que NO es hipótesis todavía

La rigidez de precios (0,44 % de cambio diario entre sábado y domingo)
no entra en el preregistro porque no hay ninguna transición entre días
hábiles: la primera será la del 21 al 22 de septiembre.
