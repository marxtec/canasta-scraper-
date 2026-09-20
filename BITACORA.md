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
