# Plan de verificación y mejora antes de pagar un reto (28/09/2026)

Objetivo único de Jose: **ganar dinero sin perderlo en retos**. Este plan cuestiona la búsqueda 2
(`resultados-busqueda-2.md`), corrige lo que falta y decide con criterios fijados de antemano si
se compra el reto. Ninguna búsqueda masiva nueva: solo verificar, medir costes reales y añadir una
capa de control de riesgo pensada para el examen. Todo umbral se escribe **antes** de mirar resultados.

## 0. Lo que no se puede prometer
Nadie puede garantizar que una estrategia pase un reto: el mercado futuro no es el pasado. Lo que sí
se puede hacer es: (a) quitar los errores y supuestos optimistas que conocemos, (b) medir los costes
reales de FTMO, (c) poner un "freno de emergencia" que haga casi imposible romper la regla diaria,
(d) probar gratis en el servidor de FTMO y en semanas nuevas, y (e) empezar con el reto de 89 €.

## 1. Autocrítica: dónde puede estar mal la búsqueda 2
| # | Punto débil | Gravedad | Qué se hace |
|---|---|---|---|
| A1 | **Sesgo de selección del procedimiento.** R1 salió de la variante "filtros sencillos", elegida después de ver resultados (4 variantes × 10 familias × 2 carriles ≈ 80 resultados). El placebo solo se corrió en las 3 familias que ya parecían buenas | **Alta** | Placebo de la búsqueda entera: las 10 familias con 2 semillas de placebo, el mismo juez y la misma forma de elegir. Se cuenta cuántas "R1" aparecen por azar (V1) |
| A2 | Un solo placebo por familia en el motor | Media | 2 semillas más para R1 y R2 (V1) |
| A3 | **Costes de FTMO no modelados.** La simulación usa comisión 0 y el spread de Vantage (mediana 0,23 $). FTMO cobra comisión en el oro: una fuente dice ~5,8 $ por lote ida y vuelta y otra ~22 $. R1 gana +1,88 € por cesta a lote base: la comisión se puede comer entre un 10 % y un 30 % | **Alta** | Leer la especificación real en una cuenta gratuita de FTMO. Simular con comisión de 5,8 $ y de 22,5 $, y con spread +0,1 y +0,2 $ (V3) |
| A4 | **Topes en racha.** El estrés inyectaba topes al azar e independientes; en un día de tendencia pueden caer varios seguidos. R1 hace hasta 10 cestas en un día. Con ×20 en 100k, 3 topes en un día son −6.000 € > límite de 5.000 € | **Alta** | Estrés con topes en racha en los peores días y **freno diario** (M1) |
| A5 | Robustez de R1/R2 no comprobada formalmente: vecinos (±1 paso de cada parámetro, filtro 0,7/0,8/0,9) y resultado sin su 5 % de mejores cestas | Media | V2 |
| A6 | **Ejecución tipo R1 no validada contra operaciones reales.** El simulador se examinó con la 555 y Dubai balanced; la escalera con tope de cliente (tipo b210) nunca ha operado en vivo | Media | Sombra y cuenta gratuita de FTMO antes del reto (F1) |
| A7 | Fin de día: la simulación corta las cestas al final del día del broker. En vivo el bot no cierra solo. R1: 0 cestas afectadas; R2: 4 de 140 | Media | Regla de cierre antes del corte diario y del fin de semana, en la simulación y en el bot (M2) |
| A8 | El día de FTMO empieza a las 00:00 CE(S)T; la simulación usa el día del broker (UTC+2/+3), una hora distinto | Baja | Recalcular el límite diario con el día de FTMO (V4) |
| A9 | Noticias en la cuenta fondeada Standard: no se puede abrir **ni cerrar** (incluidos TP/SL) de 2 min antes a 2 min después | Media (solo una vez fondeado) | Bloqueo de nuevas cestas desde 30 min antes de una noticia fuerte, y comprobar que ninguna cesta queda abierta en la ventana (M3) |
| A10 | Muestra corta: Gold 6 meses; fuera de muestra, 4 meses | Alta, sin arreglo con cálculo | Examen semanal en papel y cuenta gratuita de FTMO (F1–F2) |
| A11 | El lote recomendado (×20/×30) salió de maximizar la probabilidad de pasar sin límite de suspenso estricto ni costes | Media | Recalcular el lote con costes, rachas y freno, y la tolerancia de Jose (M4) |

## 2. ¿Hay un camino mejor? (analizado)
- **Otra estrategia:** en la búsqueda 2 se probaron 36 vertientes. No hay otra candidata con mejores
  pruebas, así que buscar más ahora solo aumentaría el sobreajuste. **No.**
- **Otra empresa u otro tipo de reto:** el 1-Step (3 % diario y pérdida máxima móvil) y Swing
  (menos apalancamiento) encajan peor; The5ers y FundedNext no aportan ventaja clara. **No.**
- **Sí mejora mucho el resultado:**
  - **capa de control de riesgo pensada para el examen:** freno diario, máximo de cestas
    abiertas y parar al llegar al objetivo. Un reto no se gana maximizando el beneficio: se gana
    **no rompiendo la regla diaria** mientras se llega al 10 %;
  - **usar la cuenta gratuita de FTMO** (Free Trial) como banco de pruebas en su servidor real
    (spreads, comisión, ejecución) sin gastar nada;
  - **empezar con el reto de 10k** (89 €);
  - posiblemente **empezar solo con R1**, que tiene las mejores pruebas, y añadir R2 después. Se decide en M4 con números.

## Decisiones de Jose (28/09)
- **Suspenso máximo aceptado: 5 %** en la simulación pesimista (costes reales, topes en racha,
  frecuencia de topes del 1 %).
- **Lo más rápido posible dentro de ese 5 %.** Regla de M4: el lote más grande cuya probabilidad
  simulada de suspender sea ≤ 5 %. Si "rápido" y "≤ 5 %" chocan, manda el 5 %.
- **Cuenta gratuita de FTMO:** sí. Es una demo en el servidor de FTMO, como la demo de Vantage. La
  abre Jose (Claude no introduce contraseñas). Suele durar unos 14 días: abrirla cuando el bot esté
  listo (F1), no antes.
- **Moneda de la cuenta: EUR.**

### Decisiones de Jose (28/09, segunda tanda)
- **Producción = "entorno de examen"**: el bot opera en la demo como si fuera el reto de FTMO:
  - mismos lotes que en el reto;
  - R1/R2 con freno diario, bloqueo de noticias y cierre antes del corte diario y del fin de semana;
  - un **monitor de reto virtual** que aplica las reglas de FTMO en tiempo real: pérdida diaria con
    flotante desde las 00:00 CE(S)T, pérdida total del 10 %, regla del mejor día, días mínimos y
    progreso hacia el +10 % / +5 %;
  - informe diario de "¿habría pasado o suspendido?".
- **Sombra en tiempo real**: las candidatas (555 actual, Dubai balanced, R3…) "operan" en el mismo
  instante que producción, con la misma señal, los precios en vivo y los retrasos medidos del broker,
  pero sin enviar órdenes:
  - se usa el motor validado (no el simulador antiguo de la sombra actual);
  - cada noche, comprobación cruzada contra la reproducción local del mismo día: si difieren, es un
    fallo de la sombra y se registra (mapa G1–G3).
- **Demo de FTMO: cambiar el bot a FTMO durante 2 semanas** como ensayo general (Vantage parado
  esos días), con la exposición plana antes del cambio y la verificación después.

## Hallazgos del 28/09 (tarde) que cambian el plan
**Régimen del oro (idea de Jose, confirmada con datos).** Rango medio diario:
- ene 137 $, feb 154 $, mar 166 $ (caótico);
- abr 104 $, may 85 $, jun 100 $;
- jul 78 $, ago 89 $, sep 85 $ (más tranquilo; la mitad de volatilidad que en el primer trimestre).

Las señales de Gold empiezan en abril, así que el caos de ene–mar solo afecta a Dubai.
Consecuencias:
1. Todo resultado se da **por régimen** (volatilidad alta, media o baja) y **por trimestre**.
2. Los meses caóticos no se tiran: son la prueba de estrés ("¿qué pasa si vuelve el caos?").
3. Se da más peso a los meses recientes, sin juzgar solo con ellos (serían muy pocos datos).

**555: los stops cortos no sirven** (año completo, broker lento, 836 cestas):
- de las cestas que llegaron a −100 € en flotante, el 56 % acabó en positivo;
- cortar en −50 / −100 / −200 € empeora −3.089 / −2.229 / −279 €; solo cortar en −300 € mejora (+440 €);
- la 555 pierde tanto en abr–jun (−219 €) como en jul–sep (−244 €).

**555: lo que sí la cambia es elegir qué señales operar:**
- con el filtro de R1 (ruptura asiática): −463 → +255 €, 5/6 meses, cestas < −100 € de 36 a 11;
- los mejores filtros sencillos para la 555 (en muestra, orientativo) cuentan todos lo mismo:
  **la señal va a favor del movimiento que ya lleva el precio** (RSI5m > 55, posición 4h > 0,5,
  movimiento 15m > 0, movimiento del día > 20). Una idea coherente, no un filtro suelto.

**Nuevo bloque T (taller 555 → "666"), antes de programar el bot:**
- T1: filtro de impulso compuesto (varias señales de "a favor del movimiento", definido **antes**
  de ver resultados) sobre la gestión 555 y sobre la de R1.
- T2: protección del beneficio (idea de Jose): subir el break-even o el "asegurar beneficio"
  escalón a escalón según se alcanzan niveles. Medir primero con el motor cuántas veces el precio
  vuelve a la entrada tras ir a favor +X $, y cuánto beneficio flotante se pierde.
  Variantes: asegurar a +5/+10/+15/+20 € con devolución de 1/3/5 €, trailing por pata de
  3/5/10/20 $ y break-even tras TP1 (necesita la extensión `own_rule_be_partial_v1`, sin validar →
  se marca).
- T3: salidas "inteligentes" en pérdida, no stops fijos: tope escalado por volatilidad (ATR), y
  cierre si la cesta lleva mucho tiempo en contra y sigue empeorando.
- T4: todo pasa por el mismo juez honesto (walk-forward, placebo con 3 semillas, 3 brokers, vecinos,
  régimen) y por el placebo de la búsqueda entera (V1). Explorar con libertad, juzgar con rigor.

## Bloque O: descubrimiento "señal a señal" (método de Jose, 28/09)
**Diseño a fondo (manda sobre el resumen de abajo): `diseno-bloque-O.md`.**
Cómo se descubrió la 555 y cómo quiere Jose seguir buscando: analizar cada señal, ver qué gestión
habría dado más beneficio, variarla y mejorarla de forma recursiva, repetir con todas y encontrar
**el punto común**. Se hace así, con sus protecciones:
- **O1. Matriz señal × acción.** Para cada señal real, miles de gestiones: familias 555 y R1,
  escaleras, objetivos, stops, asegurar beneficio, break-even escalonado, trailing, tiempos,
  seguir o invertir. Con la calculadora y confirmación en el motor. Resultado: cuánto habría dado cada
  acción en cada señal, **incluido su peor flotante**.
- **O2. "Acciones buenas" por señal.** No solo la mejor en retrospectiva (eso siempre es perfecto y
  engaña), sino el conjunto de acciones cercanas a la mejor, penalizando las que exigen aguantar
  flotantes enormes.
- **O3. El punto común.** Agrupar las señales según qué acciones les funcionan y buscar qué rasgos,
  conocidos antes de la señal, distinguen cada grupo: árboles de decisión pequeños o reglas
  legibles en español. Ejemplo de la forma del resultado: "si la señal va a favor del impulso y la
  volatilidad es normal → gestión A; si va contra el impulso → no operar o gestión B".
- **O4. Mejora recursiva.** Búsqueda local alrededor de cada punto común (ajustar un parámetro,
  volver a medir, repetir), siempre dentro del periodo de aprendizaje.
- **O5. Juez.** El procedimiento entero (O1–O4) se ejecuta en walk-forward: aprende con los meses
  anteriores y opera el mes siguiente sin verlo. Después se repite igual con placebos (3 semillas):
  **si con señales falsas también "descubre" algo igual de bueno, lo descubierto es suerte.** Luego
  motor exacto con 3 brokers, régimen y estrés.
- **O6. Salida.** Candidatas con nombre ("666", "H6668"…), su receta en lenguaje llano y su dinero
  honesto, o la conclusión de que no aparece nada mejor que R1/R2.

**Aviso metodológico (por qué la 555 engaña):** optimizar señal a señal premia de forma natural
"aguantar hasta que vuelva", porque casi todas las señales acaban volviendo, y esconde las pocas
cestas que no vuelven y se comen el mes. Por eso O2 penaliza el flotante y O5 juzga el dinero total,
incluidas las colas. El taller T (555 → 666) queda dentro de este bloque.

## 3. Trabajo (orden)
**Paso 0, antes de nada:**
- leer `mapa-de-fallos.md`;
- cada tarea dice al empezar qué filas del mapa cierra, y al terminar deja la evidencia y cambia su estado;
- ninguna tarea se da por hecha sin su evidencia.

Orden: 0 → V1–V4 → O1–O6 (incluye T1–T4) → M1–M5 → F1–F2 → puerta de decisión.
V1 va primero porque su "listón de suerte" (placebo de la búsqueda entera) sirve también para juzgar el bloque O.
**V. Verificación (≈6–8 h de cálculo, sin tocar el bot)**
- V1: placebo de la búsqueda entera, con 2 semillas × 10 familias y el mismo juez y elección de
  variante. Criterio fijado: R1 solo sigue si "R1s por azar" ≤ 1 de cada 10 búsquedas placebo.
- V2: vecinos de R1 y R2 y resultado sin su 5 % mejor. Criterio: ≥ 70 % de vecinos positivos y
  positiva sin su 5 % mejor.
- V3: costes. Comisión 5,8 $ y 22,5 $ por lote ida y vuelta; spread +0,1 y +0,2 $; retraso de 2 min.
  Criterio: positiva con la comisión real medida y spread +0,1 $.
- V4: día de FTMO en CE(S)T y cierre antes del corte diario (A7, A8).

**M. Capa de riesgo para el examen (reglas simples, pocas, elegidas con estrés pesimista)**
- M1: freno diario. Sin cestas nuevas el resto del día si la pérdida del día (con flotante) supera
  X % o tras 1 tope. Se prueban solo X = 1,5 %, 2 % y 2,5 %.
- M2: cierre antes del corte diario y del fin de semana.
- M3: bloqueo por noticias.
- M4: lote por tamaño de cuenta, que maximice la probabilidad de pasar **con suspenso ≤ tolerancia
  de Jose**, con costes reales, topes en racha y una frecuencia de topes pesimista (1 %). Comparar
  R1 sola, R1+R2 y R1+R2+R3.

- M5 (pedido por Jose el 28/09): **economía del "modo examen"**. Para cada lote, de prudente a
  agresivo, y para R1, R1+R2 y **la 555 actual**, calcular:
  - probabilidad de pasar las dos fases;
  - días hasta pasar;
  - **coste esperado por cuenta fondeada** = precio × (1 − p) / p (el precio se devuelve al pasar);
  - tiempo esperado hasta cobrar.

  Límites que no se discuten:
  - las prácticas prohibidas de FTMO: sobreapalancamiento, apuestas a un solo lado, "account
    rolling" y posiciones sustancialmente mayores que las demás. **El lote del reto debe ser el
    mismo que el de la cuenta fondeada**;
  - el margen disponible: 1:50 en el oro;
  - la regla del mejor día y los 4 días mínimos, que impiden pasar en 3 días.
- Mapa completo de fallos y su estado: `mapa-de-fallos.md`.

**F. Pruebas en vivo sin riesgo**
- F1: bot con los filtros, R1/R2, capa de riesgo, noticias y cierre de fin de día. Primero en sombra
  (sin órdenes), luego en la **cuenta gratuita de FTMO** con los lotes del reto de 10k.
- F2: examen semanal en papel con las recetas congeladas (4 semanas).

**Puerta de decisión para comprar el reto de 10k (fijada ya):**
1. V1–V3 superadas.
2. 4 semanas en papel con resultado ≥ 0 y sin días que habrían roto la regla diaria.
3. Cuenta gratuita de FTMO: el bot ejecuta igual que el simulador (entradas y cierres dentro de un
   margen), sin errores, y los costes medidos no superan los supuestos.
4. Probabilidad simulada de suspender ≤ tolerancia de Jose.

Si algo falla, no se compra y se explica el porqué.
