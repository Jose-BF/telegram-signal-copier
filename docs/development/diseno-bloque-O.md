# Diseño a fondo del bloque O: descubrimiento señal a señal (28/09/2026)

Petición de Jose: que cada paso no sea "un vistazo y las primeras ideas", sino un trabajo en
profundidad, con mente abierta y un millón de posibilidades, pero sin engañarnos. Este documento
fija **qué se explora, cómo, cuánto tiempo y cómo se juzga**. Cada sub-bloque tiene su propia fase
de diseño, su ejecución, su informe corto para Jose y sus filas del mapa de fallos (sección K).

Punto de partida: la 555 salió del buscador recursivo del proyecto
(`research/dubai_iterative/evolution.py`, `research/gold_iterative/search.py`). Ese buscador
diagnostica por qué falla una estrategia y la muta cambiando una sola causa cada vez, generación
tras generación. El método era bueno; le faltaba el juez honesto (walk-forward y placebo), y por eso
la 555 aguantaba en la muestra pero pierde fuera. Se recupera y se amplía.

## O1. El espacio de acciones: la "vida" de una señal y sus puntos de decisión
Una señal no es "entrar con TP y SL". Es una secuencia de decisiones, y en cada una se puede
actuar distinto según cómo va el mercado **en ese momento**. Toda regla tiene la forma
**CUANDO (situación) → HACER (acción)**:

| Fase | Decisiones posibles (ejemplos, no lista cerrada) | Información que se puede usar en ese momento |
|---|---|---|
| D0 · ¿Operar la señal? | operar, no operar, operar con la mitad, operar invertida | rasgos v1/v2 (impulso, rangos, sesión, volatilidad, noticias, rachas del proveedor) |
| D1 · Cuándo y a qué precio entrar | a mercado, esperar retroceso, esperar rebote (555), esperar confirmación, esperar N min, entrar solo si el primer minuto va a favor | el recorrido desde la señal |
| D2 · Tamaño inicial | fijo, según volatilidad, según racha propia, según cómo va el día de la cuenta | ATR, resultado del día |
| D3 · Añadir entradas | escalera en contra (pasos fijos o según ATR), a favor (piramidar), con condición ("solo si el rebote confirma"), número máximo | profundidad del flotante, velocidad de la caída, tiempo transcurrido |
| D4 · Gestión en beneficio | objetivos escalonados, parciales, **break-even escalonado** (idea de Jose: subir el suelo en cada nivel), trailing por precio o por dinero, asegurar beneficio con devolución, trailing que se estrecha con el tiempo | beneficio máximo alcanzado (MFE), tiempo desde el máximo, impulso actual |
| D5 · Gestión en pérdida | aguantar, tope en €, tope según volatilidad, cerrar si lleva X min en contra y sigue empeorando, reducir la mitad, dejar de añadir, cerrar si el mercado rompe un nivel clave | flotante máximo (MAE), velocidad, régimen, noticias cercanas |
| D6 · Salidas por contexto | por tiempo, antes del corte diario, antes de noticias, al abrir una nueva sesión, si llega una señal contraria del proveedor | reloj, calendario, mensajes del canal |
| D7 · Después de cerrar | no volver a entrar, re-entrar si el precio vuelve al origen, bloquear el día | resultado de la cesta |
| D8 · Entre cestas | máximo de cestas abiertas, freno diario, no duplicar exposición | estado de la cuenta |

**Información "dentro de la operación"** (legítima, porque se conoce en ese momento): los primeros
segundos y minutos tras la señal dicen mucho. Por ejemplo, "si en 2 min ya va −3 $ muy rápido, es una
tendencia en contra: no añadir y cortar". Esta vía apenas se ha explorado y es de las más prometedoras.

**Implementación:** un intérprete de reglas en la calculadora (numba), capaz de ejecutar políticas
CUANDO → HACER por fases, con tests por mecanismo. Cada mecanismo nuevo se valida contra el motor
exacto donde el motor lo soporte; los que el motor no tenga se añaden al motor antes de que una
receta que los use pueda ser final. Cada mecanismo lleva una etiqueta de **dificultad de programarlo
en el bot**: a igualdad de resultado, gana lo sencillo.

### Añadidos de Jose (28/09)
- **Nada heredado se da por bueno.** El buscador recursivo de la 555, el simulador, las estrategias y
  las conclusiones anteriores (trabajo de modelos previos) se auditan antes de usarse. Si el
  buscador recursivo tiene fallos o no encaja, se rehace o se descarta.
- **Recuperación con más lote (D3/D5):** si el precio se va mucho en contra, en ciertas situaciones
  añadir entradas mayores para salir por el beneficio total de la cesta sin volver a la primera
  entrada; y si se va demasiado, cerrar todo. Se explora con límites duros: lote total máximo,
  tope en €, y peor caso calculado y estresado.
  **Aviso:** es de la familia "martingala". Tiene dos riesgos: colas enormes, y las reglas de FTMO
  contra el sobreapalancamiento y las posiciones sustancialmente mayores. Solo pasa si cumple el
  juez y esas reglas.
- **Indicadores técnicos:** se amplía la biblioteca de rasgos causales (MACD, Bollinger, ADX, VWAP,
  medias, estructura de máximos y mínimos, perfiles de volatilidad…) para D0 y como información
  durante la operación. Cada indicador pasa el test de causalidad. Se usan en combinaciones que
  cuenten una misma idea, no como miles de filtros sueltos (más filtros = más suerte disfrazada).

## O2. Qué habría funcionado en cada señal (análisis señal a señal, solo con meses de aprendizaje)
No solo "la mejor acción en retrospectiva" (engaña siempre), sino varias lecturas:
1. **Arquetipos de recorrido:** agrupar las señales por la forma de su recorrido de 0 a 60/240 min
   (va directo a favor, va en contra y vuelve, va en contra y no vuelve, lateral…) y medir qué
   gestión le va bien a cada arquetipo.
2. **Probabilidades de giro por tramo** (la pregunta de Jose sobre los stops, generalizada): "si
   la cesta va −X € tras T min con impulso Y, ¿qué probabilidad hay de que vuelva?". Y lo mismo a
   favor: "si va +X $, ¿cuántas veces vuelve a la entrada?". Es la base del break-even escalonado.
3. **Arrepentimiento en cada punto de decisión:** para cada cesta y cada momento clave, comparar
   "seguir como estaba" con "haber hecho otra cosa" (cerrar, añadir, subir el stop). Si una
   alternativa gana de forma consistente en una situación concreta, esa situación pide otra regla.
4. **Acciones buenas por señal:** las cercanas a la mejor, penalizando las que exigen aguantar
   flotantes enormes (la trampa de la 555).

## O3. El punto común
- Relacionar arquetipos, giros y arrepentimientos con lo que se sabía **antes de la señal** (D0) y
  **durante la operación** (D3–D5).
- Herramientas: reglas legibles, árboles pequeños (profundidad ≤ 3, mínimo de señales por hoja) y
  combinaciones de varios filtros que cuenten la misma historia (como el "a favor del impulso").
- Resultado: pocas políticas completas y explicables, del tipo "familia 666: la señal a favor del
  impulso → escalera corta y break-even escalonado; en contra → no operar".

## O4. Mejora recursiva (el buscador de la 555, recuperado y ampliado)
- **Diagnóstico → mutación de una causa:** el crítico detecta por qué pierde una política ("pierde
  en días de tendencia", "devuelve beneficio tras +5 $", "entra tarde") y propone variantes que
  cambian solo esa causa.
- **Descenso por coordenadas:** ajustar un parámetro cada vez, midiendo en los meses de aprendizaje.
- **Evolución:** una población de políticas con cruces y mutaciones, con penalización por complejidad
  (más reglas = más riesgo de sobreajuste).
- **Criterio de parada:** presupuesto fijo de generaciones y parada si deja de mejorar fuera del
  periodo de aprendizaje.

## O5. Juez (igual para todo lo descubierto)
- **Walk-forward del procedimiento entero:** aprende con los meses anteriores y opera el siguiente.
- **Placebo del procedimiento entero** con 3 semillas: si con señales falsas "descubre" algo
  parecido, es suerte (listón de V1).
- Motor exacto con 3 brokers, costes de FTMO, resultado por régimen y trimestre, estrés, vecinos,
  sin su 5 % mejor, y simulación del reto con el freno diario.

## Tiempo y controles
- Cada sub-bloque (O1 intérprete, O2 análisis, O3 punto común, O4 recursión, O5 juez) es una tanda
  de trabajo propia, con: diseño → tests → ejecución → informe corto a Jose → revisión de sus filas del mapa.
- Se trabaja por capas: primero D0 + D1 + D4 + D5 (entrada, beneficio, pérdida), que es donde más
  dinero se va; luego el resto.
- Todo el análisis (O2–O4) se hace **solo con los meses de aprendizaje** de cada paso del walk-forward.
