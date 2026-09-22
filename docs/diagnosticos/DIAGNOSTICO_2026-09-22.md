# Diagnóstico metodológico: canasta-scraper, 22-09-2026

Pasada de diagnóstico. No se modificó código. Se leyeron `BITACORA.md`, incluida la entrada del 22-09 que todavía no está commiteada, y también `README.md`, `informe_avance.txt`, `analisis/*`, `canasta/*`, `collect.py` y la configuración. Se hizo un solo cálculo, la cobertura de EAN por cadena el 21-09, porque su resultado cambia una decisión del emparejamiento.

---

## 1. Estado del repositorio

| Componente | Estado | Dónde | Input → Output |
|---|---|---|---|
| Recolección | **Implementado** | `collect.py`, `canasta/vtex.py`, `catalyst.py`, `eans.py`, `.github/workflows/scrape.yml`; `browser.py` es opcional | `config/retailers.yml` → `data/raw/*.json.gz`, `data/daily/*.csv` (30 columnas), `data/trees/` (desde el 21-09) |
| Almacenamiento | **Implementado** | `canasta/storage.py`, `collect.py --reprocess` | crudo → CSV reprocesable |
| Emparejamiento | **Parcial** | Solo EAN exacto: `panel.ean_comparable` (12 o más dígitos, sin prefijo 2) más un `groupby("ean")` en `dispersion.py` y `precios_faltantes.py` | La conciliación de gramaje (README §4c) está **solo diseñada**. El emparejamiento sin EAN está **solo diseñado** (bitácora del 20-09). El cruce con la lista del INEI está **ausente** |
| Dispersión | **Implementado** | `analisis/dispersion.py` | panel → `dispersion_*.csv`, `.tex`, figuras |
| Rigidez | **Implementado como medición** | `analisis/rigidez.py` | panel → tasas, tamaños y `rigidez_horizontes` (la viabilidad del target de B) |
| oferta_real | **Implementado como regla** | `analisis/oferta_real.py` | `price` y `on_sale` → flags por día. La tabla diaria solo se escribe con `--panel-completo` (`:246`); por defecto sale un resumen del último día |
| Faltantes | **Implementado, pero orientado a índice** | `analisis/precios_faltantes.py` | Canasta = EAN presentes en **todas** las cadenas el primer día (`:190-191`), con q=1, comparada en puntos de índice |
| A | **Solo diseñado** | `informe_avance.txt:723ss`, bitácora | No hay código ni dependencias de ML en `requirements-analisis.txt` |
| B | **Solo diseñado** | Ídem. `rigidez.horizontes` mide la tasa de positivos, no entrena nada | — |
| C | **Implementado como regla, no consumible** | `oferta_real.calcular` | Ver §6 |
| Canasta oficial y canasta de trabajo | **Ausentes** | — | — |
| Optimizador | **Ausente** | No aparece en el código. Ni `optimiz` ni un solver | — |
| Evaluación del sistema | **Ausente** | Hay 68 tests unitarios y una auditoría API frente a pantalla (`auditoria_validez.py`), pero eso valida datos, no el ahorro | — |

## 2. Arquitectura (como queda con las decisiones cerradas)

```
web ──► recolección ──► panel diario (price, regular_price, on_sale, available, atributos)
                           │
   lista oficial ──► emparejamiento ──► canasta de trabajo (ítem i, cadena c, SKU, q_i)
                           │
        ┌──────────────────┼────────────────────┐
        C (regla)          A (transversal)       B (longitudinal)
  precio habitual,     prima relativa por     P(oferta real en h),
  fantasma, desc.      cadena, sin precios    profundidad esperada
  real/anunciado       ajenos
        └──────────────────┼────────────────────┘
                     OPTIMIZADOR  ──► plan: qué, dónde, cuándo
                           │
                     EVALUACIÓN   ──► ahorro fuera de muestra frente a la línea base,
                                      descompuesto por mecanismo
```

Dispersión y rigidez no alimentan al optimizador con números. Lo que hacen es acotar cuánto ahorro existe en cada dimensión (ver §9).

## 3. Problemas encontrados en el código y en los documentos

1. **La entrada del 22-09 de la bitácora contradice las decisiones cerradas.** Llama «A — optimizador de canasta» a la pieza que «asigna cada producto a la cadena más barata» y usa otra pregunta («¿Bajo qué condiciones un precio publicado…?»). Es justo lo que quedó descartado: A no es el optimizador, y elegir el menor precio ya observado no es A. Como no está commiteada, **propuesta:** reescribirla antes del commit.
2. **`informe_avance.txt` sigue con la pregunta vieja** (grupo, cadena o competencia, en `:119ss`). Además propone `h = 30` para B (`:767`), que con el panel de dos meses no se puede evaluar (ver §5) y lista «nivel de precio» como feature de A (`:737`), lo que filtra el target (ver §4).
3. **`precios_faltantes.py` está pensado para un índice**, que ya no se construye. La canasta que usa es el universo común de 5 de 5 cadenas el primer día, no la canasta de trabajo de 4 de 5, y el criterio de revisión se mide en puntos de índice. Además, para decidir qué comprar, **arrastrar un precio es incorrecto**: no se puede comprar a un precio arrastrado si el producto está agotado.
4. **`oferta_real.py` no aplica exactamente la regla de C decidida** (detalle en §6).
5. **Tottus no publica stock.** `available` está vacío en el 100 % de sus filas del 21-09. Para el optimizador, Tottus siempre parece disponible.
6. **La cobertura de EAN de fabricante es desigual.** El 21-09 la tenía el 50 % de las filas de Plaza Vea y el 55 % de Vivanda, frente a alrededor del 74 % en Metro, Wong y Tottus. Si el criterio de 4 de 5 se aplica solo por EAN, la cadena que «falta» será casi siempre de SPSA, y no porque no tenga el producto sino porque no publica el código. Es la única objeción concreta encontrada al criterio, y **no pide cambiar el 4 de 5, sino cómo se opera** (ver §7).

---

## 4. Modelo A (transversal)

- **Unidad:** el par (EAN o ítem emparejado, cadena), en una ventana de entrenamiento y no en un día suelto.
- **Target, propuesta:** una regresión de la prima relativa, `r_ic = log p̃_ic − media_c log p̃_ic`, donde `p̃` es el **precio habitual de C** (la mediana del SKU) y no el precio del día. Así A predice la dispersión estructural y no el ruido de las promociones. La cadena más barata se obtiene como `argmin_c r̂_ic`. Esto resuelve de forma natural que un ítem esté en 4 de 5 cadenas, cosa que un clasificador de 5 clases no maneja bien. La clasificación multiclase queda como variante.
- **Features:** categoría común, marca, marca propia frente a nacional, origen, octógonos, `vendido_por`, log de `net_quantity` y unidad, cadena y grupo, y sus interacciones cadena × categoría.
  - **Ningún feature construido con precios**, ni de otras cadenas ni de la misma, porque `r_ic` contiene `p_ic`. «Nivel de precio» sale; si hace falta, se usa un tramo de tamaño × categoría.
  - La intensidad promocional por categoría se calcula **solo dentro del fold de entrenamiento**.
- **Líneas base:**
  - La cadena ganadora más frecuente del entrenamiento, que es la regla cerrada.
  - Una más dura: la ganadora por categoría. Si A no le gana a esta, el modelo no aporta sobre una tabla.
- **Validación:** `GroupKFold` por EAN, porque el mismo EAN repetido en varios días entre entrenamiento y prueba es memorización. Una variante por marca mide la generalización a lo que no se ve, que es el uso real. Se completa con un holdout temporal de las últimas semanas sobre EAN no vistos.
- **Métricas:**
  - MAE de `r`.
  - Acierto top-1 frente a las dos líneas base.
  - **Regret en soles**: el precio en la cadena predicha menos el mínimo, en %. Es la métrica que le importa al optimizador.
- **Qué recibe el optimizador:** `r̂_ic` con un intervalo de incertidumbre, para las **celdas sin precio observado**: una cadena donde falta el ítem, ítems de la canasta sin emparejar, SKU nuevos o una cadena caída ese día. En la inferencia se compone `p̂_ic = p_ic' · exp(r̂_ic − r̂_ic')`. **Esto es una propuesta:** el modelo no usa precios ajenos para aprender, pero el optimizador sí los combina después. Si se considera que viola la decisión, A entrega solo el ranking.
- **Riesgo declarado:** puede no ganarle a la línea base por categoría si la dispersión es mayormente idiosincrática. Hay que aceptar ese resultado nulo desde ahora.

## 5. Modelo B (longitudinal)

- **Unidad:** (cadena, SKU, origen t), solo en orígenes **que no están en oferta real en t**, como ya hace `rigidez.py:204`. Por construcción, B no puede reducirse a mirar el precio de hoy: se pregunta por una entrada futura. Si el producto ya está en oferta real hoy, se compra hoy, y eso lo decide una regla, no B.
- **Target:** `oferta_real` en (t, t+h]. Como segunda salida, la profundidad `1 − min p(t,t+h]/p_habitual(t)`, o bien la profundidad media por categoría estimada en el entrenamiento.
- **Features, calculados solo con información hasta t:**
  - Días desde la última oferta real y duración de la última.
  - Frecuencia histórica de ofertas del SKU y de la categoría.
  - `descuento_real(t)`, `frac_on_sale(t)` y si el cartel es fantasma en t.
  - `promo_end` (existe para Metro y Wong).
  - Si la cadena hermana o una competidora entró en oferta en t o antes.
  - Día de la semana, quincena y fin de mes.
  - **Prohibido:** `frac_dias_oferta_real` y `dias_con_hueco` de `resumen_productos`, porque usan todo el panel, incluido el futuro (`oferta_real.py:179`).
- **Restricción de calendario, la más importante:** C necesita 21 observaciones antes de ser fiable. En un panel de alrededor de 60 días, los orígenes con etiqueta real van del día 21 al día 60−h. Con h=30 quedan unos 9 días de orígenes para entrenar y evaluar juntos, **y eso no alcanza**. **Propuesta:** h ∈ {7, 14}, fijado antes de entrenar. El 30 del informe debe salir.
- **Líneas base:**
  - «Comprar hoy» como línea base de decisión: su ahorro es 0 por definición.
  - Una base predictiva: la tasa histórica de ofertas del SKU o de la categoría. Si B no le gana, el aporte del ML es nulo.
- **Validación:** origen rodante temporal con **embargo ≥ h** entre el fin del entrenamiento y el inicio de la prueba, porque la etiqueta de los últimos orígenes de entrenamiento mira dentro del periodo de prueba.
- **Métricas:** PR-AUC, calibración (Brier, porque el optimizador usa probabilidades) y el ahorro realizado de la política en el backtest.
- **Qué recibe el optimizador:** `P(oferta en h)` y la profundidad esperada. Con eso calcula el ahorro esperado de esperar: `P · prof · p_habitual`.

## 6. C (diagnóstico, regla)

**Lo que ya está en el código (`oferta_real.py`):**
- El precio de referencia propio del SKU frente al precio declarado (Nakamura y Steinsson).
- Una ventana móvil hacia atrás al estilo de Bueno, con mediana y no media.
- Un umbral de profundidad.
- Un techo de frecuencia del cartel.
- El requisito de 21 observaciones (`fiable`).
- El límite conocido de un cambio permanente de precio, documentado en el propio archivo.

El docstring solo cita a Nakamura y Steinsson y a Bueno. El resto de la literatura de respaldo (Eichenbaum, Jaimovich y Rebelo; Kehoe y Midrigan; Anderson et al.; Ray, Snir y Levy; Friedman) **no está reflejada en el código**. Falta en particular la **moda** como precio de referencia (Eichenbaum, Jaimovich y Rebelo), que es la prueba de robustez acordada.

**Diferencias con la regla decidida:**

| Regla decidida | Código |
|---|---|
| Fantasma = `on_sale` y el precio **no cae** de la mediana | Fantasma (`tachado_permanente`) = `on_sale` y no (caída ≥ **10 %** y `frac_on_sale` < 0,5) (`:144-145`). Una caída del 0 al 10 % cuenta como fantasma, y un cartel presente más de la mitad de los días también |
| La mediana del precio **habitual** | La ventana `(t−45, t]` **incluye el día t** (`:121`). Con la mediana y n ≥ 21 el efecto es pequeño, pero la regla literal es «habitual antes de hoy» |
| — | `oferta_real` no exige `on_sale`: una rebaja sin cartel cuenta como oferta real. Eso es útil (es una «rebaja silenciosa»), pero hay que nombrarlo |
| — | `regular_price` no se carga (`:75`), así que **no se calcula el descuento anunciado ni la inflación del ancla**, que son la medida misma de la opacidad |

**Qué falta para que el optimizador pueda consumirlo:** una tabla diaria siempre escrita, con una fila por (cadena, SKU, t) y estas columnas:
- `precio_habitual` (ventana hasta t−1)
- `descuento_real`
- `descuento_anunciado = 1 − price/regular_price`
- `fantasma`
- `rebaja_silenciosa`
- `fiable`
- `ref_moda`

**Regla, feature, etiqueta y modelo, separados:**
- **Regla:** `calcular()`. Es determinista y el único artefacto de C.
- **Feature (para B y el optimizador):** las columnas anteriores en t, calculadas hacia atrás.
- **Etiqueta (para B):** `oferta_real` en (t, t+h]. Se calcula con ventanas que terminan en t+k, lo cual es legítimo porque solo se usa como etiqueta.
- **Modelo:** ninguno. El único caso en que la regla no basta es un SKU con `fiable=False` (nuevo, o en los primeros 21 días). Para esos casos, el optimizador trata el cartel como **no informativo** y usa solo el precio. Eso es conservador y no necesita un clasificador.

**Cómo se evalúa la regla (no es una evaluación de exactitud):**
1. Sensibilidad a la ventana (30, 45 o 60 días), al umbral (0, 5, 10 o 15 %) y a la mediana frente a la moda. Se reporta qué fracción del cartel es fantasma de forma robusta a todas las variantes.
2. Validez interna: los episodios de oferta real deben revertir hacia el precio habitual y los fantasma deben persistir. Se mide con la tasa de reversión en k días.
3. Validez predictiva: tras un fantasma, el precio de los días siguientes no debe quedar por debajo del habitual.
4. Los cambios permanentes que se clasificaron como oferta real se cuentan y se reportan.
5. Una muestra de la auditoría de pantalla, para validar las entradas `on_sale` y `regular_price`.

## 7. Canasta

| Conjunto | Qué es | Estado |
|---|---|---|
| Canasta oficial | Lista del INEI o de una fuente del mismo tipo, con cantidades o ponderaciones | Ausente |
| Canasta de trabajo | Cruce de esa lista con productos emparejados en ≥ 4 de 5 cadenas | Ausente |
| Universo scrapeado | Todo el panel. El «universo común» de EAN en 5 cadenas es un subconjunto de él, **no la canasta** | Existe |

**Metodología propuesta para la siguiente pasada:**
1. **Fuente.** Un documento con versión fijada (URL, fecha y hash), transcrito a `canasta_oficial.csv`: `id`, descripción, grupo, unidad y cantidad o ponderación. Queda por decidir cuál (ver §12). La lista no se simula aquí porque todavía no se tiene.
2. **Alcance, con exclusiones declaradas:** comidas fuera del hogar, rubros que no son alimentos (si la fuente los trae) e ítems sin equivalente envasado ni a granel en la web. Se deja constancia de qué categorías quedan fuera y de cuánto pesan en la canasta oficial.
3. **Emparejamiento por cadena, por descripción y no por EAN.** Este es el ajuste que pide la objeción del §3.6:
   - Texto normalizado (`panel._plano`) más un diccionario de sinónimos por ítem, versionado.
   - Filtro de gramaje con `parse_quantity`, en unidades convertibles.
   - Marca, si la fuente la especifica.
   - La categoría común como restricción.
   - Se excluyen terceros (`seller_name`), packs y combos.
4. **Representante.** En los envasados, el EAN como identidad fuerte cuando existe. En los productos a granel (código interno con prefijo 2), un SKU por kilo por cadena. **La conciliación de gramaje del §4c va antes**, porque el emparejamiento por especificación depende del gramaje.
5. **Revisión manual.** Doble codificación de los candidatos, con acuerdo reportado. Las reglas se fijan **antes de mirar precios**, para que nadie elija sin darse cuenta la opción más barata.
6. **Inclusión.** ≥ 4 de 5 cadenas con precio y disponibilidad en al menos X % de los días de una ventana inicial (por ejemplo, los primeros 14 días). Después **se congela**, para que la selección no dependa del resultado.
7. **Salida:** `canasta_trabajo.csv` con ítem, cadena, SKU, EAN, regla aplicada, confianza y revisor.

## 8. Optimizador

**Objetivo.** Minimizar el costo esperado de la canasta. Es un programa entero mixto (MILP) pequeño, de unas decenas o centenas de ítems por 5 cadenas:

```
min  Σ_i Σ_c Σ_τ q_i · p̂_{i,c,τ} · x_{i,c,τ}  +  Σ_c F_c · y_c
s.a. Σ_{c,τ} x_{i,c,τ} = 1                     (cada ítem se compra una vez)
     x_{i,c,τ} ≤ y_c ;   Σ_c y_c ≤ K           (cadenas visitadas)
     x_{i,c,τ} = 0 si (i,c) no está emparejado o available = False en t
     τ ∈ {hoy, esperar ≤ h} solo para ítems no perecibles
```

- **Faltantes.** Una celda agotada es **infactible**, no se arrastra. Si no está observada, se usa A (§4) con su incertidumbre, o se declara infactible en el escenario estricto. El arrastre de `precios_faltantes.py` solo sirve para valorar la línea base cuando le falta un ítem.
- **Promociones.**
  - El precio que se paga es `price`, crea o no uno en el cartel. **C no cambia el costo de hoy.**
  - C cambia dos cosas. La primera es la elección de la línea base, que decide por el cartel. La segunda es la opción de esperar: un fantasma no es razón para comprar ya, y el precio habitual es el contrafactual de la espera.
  - B decide cuándo comprar, con `P · prof · p_habitual`.
  - La tarjeta no entra.
- **Tottus sin stock:** es infactibilidad no observable. Se corre un escenario con y otro sin Tottus.
- **Si solo hay precio observado** (sin A, sin B y con C no fiable): el optimizador queda como un comparador determinista `argmin` con restricción K, que compra todo hoy. Es la versión v0. Se puede construir en cuanto existan la canasta de trabajo y la tabla de C.

## 9. Integración y coherencia

| Componente | Pregunta | Tipo | Input | Output | Al optimizador |
|---|---|---|---|---|---|
| Recolección | ¿Qué publica cada cadena hoy? | Infraestructura | APIs | `daily`, `raw`, `trees` | precio, cartel, ancla, stock |
| Emparejamiento y canasta | ¿Qué se compra y qué es «lo mismo»? | Preprocesamiento | lista oficial más panel | `canasta_trabajo` | conjunto factible y q_i |
| Dispersión | ¿Cuánto difiere el precio entre cadenas? | Descriptivo | panel con EAN | tablas | **Redefinir:** calcularla sobre la canasta de trabajo como **cota del ahorro por elegir cadena**. Features de grupo para A |
| Rigidez | ¿Cada cuánto cambian los precios y entran las ofertas? | Descriptivo | panel | tasas, horizontes | **Redefinir** como cota del ahorro por esperar. Fija h y la viabilidad de B |
| Faltantes | ¿Qué precio vale cuando falta? | Preprocesamiento | panel, árboles | reglas | **Redefinir:** de regla de índice a regla de factibilidad más la valoración de la línea base, sobre 4 de 5 y en soles |
| C | ¿El cartel dice la verdad? | Diagnóstico | `price`, `on_sale`, `regular_price` | tabla diaria | precio habitual, fantasma, descuento real y anunciado |
| A | ¿Dónde sale más barato, sin mirar precios ajenos? | Predictivo | atributos | `r̂_ic` ± incertidumbre | celdas no observadas |
| B | ¿Entrará en oferta real en h? | Predictivo | historia hasta t | P y profundidad | decisión de esperar |
| Optimizador | ¿Qué, dónde y cuándo? | Prescriptivo | todo lo anterior | plan de compra | — |
| Evaluación | ¿Cuánto ahorra y por qué mecanismo? | Evaluación | plan más precios realizados | ahorro descompuesto | — |
| Auditoría | ¿El precio de la API es el de pantalla? | Validación | fichas | concordancia | confianza en las entradas |

**La evaluación responde el «en qué medida».** Es un backtest fuera de muestra en las últimas semanas, con una escalera de ablaciones:
1. Línea base: una sola cadena, elegida por el cartel, comprando hoy.
2. La mejor cadena única, elegida con información hasta t. Mide la dispersión entre cadenas.
3. K > 1. Mide la dispersión a nivel de ítem.
4. Más C. Mide la opacidad.
5. Más B. Mide la dinámica temporal.
6. Más A. Mide la cobertura.

Los intervalos de confianza se sacan con bootstrap por bloques de días. Como el orden de la escalera altera el reparto, se reporta además la contribución de Shapley de cada componente.

## 10. Pregunta

**Qué cubre:** el ahorro alcanzable con precios web de supermercado moderno sobre una canasta oficial emparejada, y cuánto de ese ahorro aporta cada mecanismo. La escalera de ablaciones lo mapea uno a uno: dispersión en los pasos 2 y 3, opacidad en el 4, rigidez y ofertas en el 5.

**Qué no cubre:**
- El gasto real del hogar limeño (mercados de abastos, bodegas).
- El precio en tienda física: el precio es del canal web fijado.
- El precio con tarjeta.
- El costo de envío, salvo como escenario con `F_c`.
- Por qué las cadenas fijan sus precios: H1 a H4 son contexto, no la respuesta.

**Incoherencia con el código:** no se encontró ninguna que obligue a cambiar la pregunta. Hay un matiz: la *rigidez* por sí sola no reduce costos, lo que los reduce son las rebajas transitorias. Se sostiene si la rigidez se lee como la condición que limita el ahorro por esperar, y así hay que redactarlo. El título no se toca. Lo que sí hay que alinear son los documentos (§3.1 y §3.2).

## 11. Riesgos

| Riesgo | Gravedad | Solución |
|---|---|---|
| A: features construidos con precios, y el mismo EAN en entrenamiento y prueba | Alta | Sin precios en los features. `GroupKFold` por EAN o por marca. Estadísticos solo dentro del fold |
| B: la etiqueta cerca del corte mira el periodo de prueba, y agregados de todo el panel usados como features | Alta | Embargo ≥ h. Un constructor de features con fecha de corte y un test que altere días futuros y compruebe que los features no cambian |
| B: h=30 inviable con 21 días de arranque y un panel de dos meses | Alta | h ∈ {7, 14}, fijado antes de entrenar |
| B: un cambio permanente etiquetado como oferta real | Media | Para el optimizador es benigno (esperar igual paga), pero hay que contarlos y no llamarlos «oferta» |
| Disponibilidad: Tottus no publica stock y SPSA publica precio sin stock | Alta | Infactibilidad explícita, escenario sin Tottus y la muestra de la auditoría |
| Emparejamiento con el INEI: falsos pares y elección sesgada por el analista | Alta | Reglas fijadas antes de ver precios, doble codificación y congelamiento |
| 4 de 5 aplicado por EAN, que castiga a SPSA | Media | Emparejar por descripción en cada cadena (§7) |
| Supervivencia: la canasta seleccionada según lo que sobrevivió | Media | Ventana de inclusión y congelamiento |
| Línea base de paja («creyendo el cartel» definida a conveniencia) | Alta | Definirla antes del backtest y reportar también contra la mejor cadena única ex ante |
| Afirmar que C reduce el costo cuando el optimizador ya usa el precio observado | Alta | Atribuirle a C solo lo que muestre la ablación 4 |
| Ahorro medido dentro de muestra | Alta | Solo backtest fuera de muestra |
| Muchos umbrales de C: jardín de senderos que se bifurcan | Media | Preregistro en la bitácora y análisis de sensibilidad |
| Gramaje equivocado en las comparaciones por kilo | Media | Implementar el §4c antes de emparejar |
| Estacionalidad de quincena y fin de mes con solo 2 ciclos en el panel | Media | Declararla como limitación y no afirmar efectos estacionales |

## 12. Decisiones que todavía hay que tomar

1. **Fuente oficial** y de dónde sale `q_i`: cantidades físicas (CBA) frente a ponderaciones del IPC convertidas a cantidades.
2. **Nivel de emparejamiento:** un producto representativo idéntico frente a una variedad equivalente (permitir sustituir marca o tamaño es otro ahorro, y habría que separarlo).
3. **Umbral de C:** 0 % (la regla decidida) frente al 10 % del código. Si se mantiene el techo de `frac_on_sale`. Si la ventana excluye el día t.
4. **Cómo se opera «creyendo el cartel»:** qué cadena elige esa línea base.
5. **h de B, ritmo de compra** (semanal o quincenal) y **qué categorías pueden esperar**.
6. **K y `F_c`** (costo de envío o de visita) como escenarios.
7. **Qué hacer con Tottus** sin stock.
8. **Target de A:** regresión de la prima (recomendada) o clasificación.
9. **Fechas de entrenamiento y de prueba**, fijadas en la bitácora antes de entrenar.

## 13. Roadmap (en orden de dependencias)

1. **Decisiones (esta semana).** Cerrar el §12, preregistrarlas en la bitácora y reescribir la entrada del 22-09 y la pregunta del informe.
2. **Canasta.** Transcribir la fuente, implementar la conciliación de gramaje, escribir el script de emparejamiento y hacer la revisión manual. Con unos 14 días de panel, a comienzos de octubre, aplicar la inclusión y congelar `canasta_trabajo.csv`. Ese mismo paso reorienta `precios_faltantes.py` a factibilidad y soles sobre 4 de 5.
3. **C consumible.** Tabla diaria, descuento anunciado, regla alineada con la decisión 3, moda como robustez y evaluación de la regla. Será fiable desde unos 21 días de panel (alrededor del 10-10).
4. **A.** Constructor de features y especificación de los splits ya. Entrenamiento con unos dos meses de panel (alrededor del 19-11).
5. **B.** Constructor de features con fecha de corte y sus tests ya. Entrenamiento con el panel de dos meses y el h ya decidido.
6. **Optimizador.** La v0 (solo precio observado más C) se puede construir tras el paso 3, porque no depende de A ni de B. Después se integran A (celdas faltantes) y B (espera).
7. **Evaluación.** Backtest fuera de muestra en las semanas finales, con la escalera de ablaciones, los intervalos de confianza por bootstrap en bloques y las variantes de robustez: Tottus, umbral de C, K y `F_c`.
