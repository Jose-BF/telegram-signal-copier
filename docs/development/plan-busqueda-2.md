# Plan de la búsqueda 2: explorar todo, fiarse solo de lo que aguanta (27/09/2026)

Estado: **PROPUESTA, pendiente de aprobar con Jose.** No se lanza nada hasta su OK.
**Plan de ejecución paso a paso (hitos M0–M9):** `plan-busqueda-2-ejecucion.md`.
Autor: Claude (Opus 5.5, modo plan). Pensado para que otra sesión, aunque sea de menor
esfuerzo, lo pueda ejecutar paso a paso.

## 0. En una frase
Abrir la búsqueda a todas las formas de usar una señal (entrada, salida, dirección, filtros,
tamaño y combinaciones) y cambiar el juez. Deja de preguntarse "¿supera al placebo?" y pasa a
preguntarse "¿gana dinero de forma sostenida, con la pérdida acotada, en cualquier trozo de
tiempo y en situaciones que no hemos visto?".

## 1. Qué he visto al investigar (hechos, no opiniones)
1. **La búsqueda 1 usó solo una parte del motor.** El motor ya sabe hacer cosas que no se
   probaron o que apenas se probaron:
   - entradas: "momentum" (entrar cuando el precio ya va a favor), "delay" (esperar X minutos),
     "no_entry" (no entrar);
   - escalera a favor (añadir cuando va bien);
   - objetivos: parcial más un resto que corre (`partial_runner`), objetivo por movimiento en $
     (`fixed_move`), objetivo por dinero de toda la cesta (`fixed_basket`);
   - protección: break-even por precio, por tiempo o tras un parcial; trailing en Dubai; stop
     fijo por pata en Dubai.

   Fuentes: `research/dubai_iterative/contracts.py:233`, `fast_engine.py:50-78` y el espacio
   usado en `research/history_space.py`. **Matiz (ver 8d):** en la reproducción histórica,
   varias de estas opciones necesitan una extensión de protección que todavía no se ha
   validado contra operaciones reales.
2. **La etapa 1 solo miró el periodo de búsqueda** (`search/stage1.py:17`: Dubai ene–jun; Gold
   abr–jun). La robustez "en todas direcciones" (buscar en los meses recientes y comprobar en
   los antiguos, ventanas móviles, dejar un mes fuera) no se hizo.
3. **Todo 2026 ya está mirado**, incluido el examen final 14–25/09. No queda ningún trozo de
   2026 que sea virgen de verdad. Solo hay dos exámenes limpios posibles:
   - las semanas nuevas a partir del 28/09, que el bot y el broker ya graban (prueba en papel, sin dinero);
   - años anteriores de oro (Dukascopy, 2018–2025). Solo valen para la parte de la ventaja que
     viene del mercado, porque de esos años no tenemos señales.
4. **Señales por mes** (`signal_universe_v1/signals.jsonl`):
   - Dubai: 73–108 al mes, de enero a septiembre (776 en total).
   - Gold: 109–180 al mes, de abril a septiembre (947 en total).

   Con ~90 señales al mes, un mes suelto es poca muestra: los cortes tienen que ser de varios meses.
5. **Límites del motor que se mantienen:**
   - la cesta dura como mucho 240 min (6 h de recorrido cargado) y se corta al final del día del broker;
   - volumen total ≤ 1 lote.

   Operar de un día para otro sería otra frontera, y exige tocar el motor. Queda aparcada (ver sección 9).
6. **El portátil** (i9-12900H, 14 núcleos, 16 GB) es unas 6 veces más rápido que la nube
   (2 CPU). La etapa 1 de la búsqueda 1 (5 h) bajaría a ~1 h. Esto hay que medirlo en el piloto,
   no darlo por hecho.
7. **Herramientas de nico66fx** (revisadas el 27/09; detalle en la sección 8):
   - su analizador y su simulador de fondeo solo miran el saldo tras cada cierre, sin el flotante;
   - lo aprovechable son las ideas: presets de fondeo, métricas, pruebas de robustez y la
     descarga de ticks de Dukascopy.

## 2. Idea central (lo que cambia respecto a la búsqueda 1)
- **Explorar de verdad todo:** gestiones, filtros, dirección y combinaciones, incluidas las raras.
- **No descartar por el origen de la ventaja.** Si una estrategia gana también con señales
  falsas, se etiqueta como **"ventaja de mercado"** y pasa a un control distinto (años
  anteriores, fase E), en lugar de tirarla.
- **La protección contra la suerte ya no es el placebo, sino tres cosas:**
  1. ganar en muchos cortes de tiempo distintos (fase B);
  2. aguantar situaciones malas inventadas y años anteriores (fase E);
  3. un examen en semanas futuras que nadie ha visto (fase G).
- **Pérdida máxima por diseño:** toda candidata lleva un stop o un tope de cesta, y su peor
  caso se calcula, no se observa.

## 3. Fases

### Fase A. Mapa del recorrido de cada señal (nuevo, rápido)
Objetivo: responder a la idea de Jose de un "punto dulce" viendo todo el terreno, no un
número suelto.
1. **Recorridos:** para cada señal (reales, las tres semillas de hora aleatoria e invertidas),
   guardar el recorrido del precio de las 6 h siguientes a la hora en que la ve el bot
   (bid/ask por segundo, a favor y en contra).
   - Código nuevo: `research/signal_paths.py`.
   - Salida inmutable con hash en `runtime_data/signal_paths_v1/`.
   - Reutilizar la carga de ticks de `research/history_replay.py` (`History.days`, reloj UTC+2/+3 ya validado).
2. **Calculadora vectorizada de reglas simples** (`research/bracket_grid.py`):
   - Dimensiones de cada regla:
     - dirección: seguir, invertir, o "a favor del primer movimiento de X $";
     - entrada: a mercado, esperar X $ en contra, esperar X $ a favor, esperar N min;
     - objetivo en $; stop en $;
     - break-even tras X $; trailing de X $;
     - salida por tiempo (15–240 min).
   - Con numpy sobre las ~1.700 señales: cientos de miles de reglas en minutos.
   - Resultado por regla y por señal en $/oz: el lote se decide después.
3. **Informes:**
   - mapas de calor "objetivo × stop" (uno por tiempo de salida) de la media por operación y
     del porcentaje de meses positivos, por canal y por subconjunto (hora, filtros actuales);
   - curvas MFE/MAE: cuánto va a favor y en contra una señal típica, y cuándo;
   - lo mismo con los placebos, para ver qué parte del terreno es de mercado y cuál es de la señal.
4. **Control de exactitud antes de fiarse:**
   - pasar ~20 reglas por la calculadora y por el motor exacto (genomas equivalentes, broker lento);
   - hecho cuando el signo coincide en ≥95 % de las cestas y el total por regla difiere ≤10 %;
   - si no cuadra, se corrige la calculadora y nunca se ajusta el motor.
5. **Uso:** la calculadora es una criba aproximada. No elige estrategias; solo marca **zonas
   prometedoras** que luego se prueban con el motor exacto (fase C).
6. **Tests** en `tests/test_signal_paths.py` y `tests/test_bracket_grid.py`: casos sintéticos
   con resultado conocido a mano (tocar TP antes que SL, empate en el mismo tick = se asume lo peor).

### Fase B. Cortes de tiempo en todas direcciones (se diseña antes de mirar nada)
Código nuevo: `research/folds.py` (+ test). Unidad: meses completos.
- **Dubai** (ene–sep, 9 meses) y **Gold** (abr–sep, 6 meses).
- **Cortes:**
  1. hacia delante: elegir en la primera mitad y comprobar en la segunda;
  2. hacia atrás: elegir en la segunda mitad y comprobar en la primera;
  3. ventanas móviles: elegir en 3 meses seguidos y comprobar en los 2 siguientes, avanzando de mes en mes;
  4. un mes fuera: elegir con todos menos uno y comprobar en ese, uno por uno;
  5. combinatorio: todas las formas de partir los meses en dos grupos (Gold 6 meses: 20 formas de 3+3).
- **Probabilidad de sobreajuste (PBO).** En cada corte se toma la mejor combinación del lado
  "elegir" y se mira en qué puesto queda en el lado "comprobar". PBO = porcentaje de cortes en
  que la elegida queda por debajo de la mediana. Explicado a Jose: "de cada 100 veces que elijo
  la mejor en unos meses, cuántas decepciona en los otros". Objetivo: PBO < 30 %.
- Estos cortes se aplican a los resultados de las fases A y C: no hace falta re-simular.

### Fase C. Búsqueda amplia con el motor exacto
1. **Primero, la comprobación pendiente** (`search/placebo_on_real.py`, ~5 min): las 10
   finalistas placebo de Dubai sobre señales reales de enero a septiembre, con los 3 brokers.
   Queda incluida aquí, dentro del plan aprobado.
2. **Espacio de gestiones ampliado** (`research/history_space2.py`, sin tocar el 1):
   - usa todos los modos del motor de la sección 1.1, más las zonas prometedoras de la fase A;
   - Dubai y Gold con las mismas familias disponibles (hoy Gold no prueba trailing por pata ni entradas momentum);
   - regla fija: toda gestión lleva stop por pata o tope de cesta. La pérdida máxima por cesta
     se calcula a partir del genoma (lote × distancia, o tope) y se guarda con él.
   - se incluyen como referencia la estrategia actual, la candidata Dubai #3 y la Gold #1.
3. **Filtros ampliados** (`research/signal_features.py` v2; causales, es decir, solo con datos anteriores a la señal):
   - los actuales;
   - sesión: Asia, Londres o NY, y minutos desde la apertura de sesión;
   - distancia al máximo y al mínimo de ayer, y del rango asiático;
   - si la señal llega justo después de barrer un máximo o mínimo reciente (idea "liquidity sweep");
   - distancia a número redondo (x00 / x50);
   - régimen de volatilidad: ATR 1h frente a su media de 20 días;
   - día con dato fuerte (NFP, IPC, FOMC), con calendario fijo escrito a mano por fechas;
   - rachas del proveedor: señales en la última hora y si van en la misma dirección que la anterior;
   - resultado de la señal anterior, solo si ya estaba cerrada (causal).

   Cada rasgo nuevo lleva un test de causalidad: al recalcularlo sin ningún dato posterior a la
   señal, tiene que dar lo mismo.
4. **Universos:**
   - reales, 3 semillas de hora aleatoria, invertidas, y "misma hora, dirección elegida por el mercado";
   - Dubai de enero a septiembre y Gold de abril a septiembre, todo el periodo: los cortes de la fase B se hacen después;
   - tres brokers: A_p50, C_p50 y C_p90 (`execution_calibration_v2/*_q450.json`).
5. **Presupuesto:** fijado antes del piloto y apuntado aquí tras medir.
   - Propuesta: 2.000 gestiones por canal × ~1.000 filtros (los filtros se aplican después, sin re-simular).
   - Semilla registrada; todas las variantes se guardan, también las malas.
6. **Piloto de tiempos primero:**
   - 50 gestiones × 1 mes × 1 universo, con `workers` = 4, 8 y 12;
   - se mide tiempo y memoria (16 GB) y con eso se calcula el total;
   - si el total pasa de ~10 h, se reduce el presupuesto y se consulta con Jose.
7. **Scripts:** `search2/stage1.py` y siguientes. Copiar el patrón de `search/` (reanudable por
   lotes, protección `main()` para Windows) y subir `workers`.

### Fase D. Juez nuevo: "dinero sostenido con riesgo acotado"
Código nuevo: `research/search_judge2.py` (+ tests). Todo se fija **antes** de mirar los resultados de C.
Una combinación (gestión + filtro + universo) es **candidata** si cumple todo lo siguiente:
1. **Riesgo por diseño:**
   - pérdida máxima por cesta calculada ≤ **L_cesta**;
   - peor día simulado con flotante (`research/account_sim.py`) ≤ **L_día**.

   Los dos límites los decide Jose (sección 6).
2. **Constancia:**
   - positiva en ≥ 70 % de los meses;
   - ningún mes peor que −2 × L_cesta;
   - con n ≥ 40 cestas (Dubai) o ≥ 30 (Gold) en el total.
3. **Fuera de muestra** (fase B): positiva en el lado "comprobar" en ≥ 75 % de los cortes.
4. **Los tres brokers:** positiva con A_p50, C_p50 y C_p90 en el total.
5. **Vecinos:** ≥ 70 % de los vecinos (cada parámetro y umbral, un paso) siguen positivos.
6. **Sin depender de unas pocas:** quitando el 5 % de sus mejores cestas, sigue positiva
   (idea de la auditoría de nico66fx).
7. **Etiqueta de origen, que ya no elimina:** se compara con los universos placebo.
   - Si gana igual con señales falsas → "ventaja de mercado" y debe pasar la fase E2.
   - Si gana solo con las reales → "ventaja del proveedor".

**Orden de las candidatas:** beneficio total ÷ peor caída con flotante (broker lento). Se
informa también beneficio al mes, cestas al mes, peor día y PBO de su familia.

### Fase E. Estrés y años anteriores
1. **Situaciones malas inventadas** (`research/stress2.py`), a cada candidata:
   - golpe en contra de X $ en 1 minuto justo después de entrar: X = 10, 20 y 40 $; el stop se
     llena con deslizamiento del golpe entero;
   - spread × 3 y retraso de broker de 2 min durante toda la cesta;
   - repetir sus cestas en los 10 peores días de mercado de 2026 (por ejemplo el 29/01), con
     todas las horas de señal de ese día.

   Pasa si en todos los casos la pérdida queda ≤ L_día. Esta es la idea de Jose de contar con
   pérdidas que la muestra no enseña.
2. **Años anteriores** (solo "ventaja de mercado"; requiere OK de descarga, sección 6):
   - ticks XAUUSD de Dukascopy 2018–2025 (fuente pública; varios GB; descarga reanudable
     siguiendo las fases 1–4 del prompt de nico66fx, sin la parte de MT5);
   - ajuste de spread calibrado con los meses de 2026 en que hay a la vez Dukascopy y Vantage;
   - señales falsas con la misma distribución de hora, día y dirección que el canal;
   - pasa si gana en ≥ 6 de 8 años y ningún año pierde más de 3 × L_día.

### Fase F. Cuenta completa y fondeo
- Juntar en una cuenta, con flotante real minuto a minuto (`account_sim.py`), las candidatas de
  los dos canales. Probar combinaciones y lote por volatilidad.
- Monte Carlo por días (existente).
- **Fondeo con presets:**
  - FTMO 2 fases, The5ers y 1 fase genérica;
  - variantes con y sin límite de días;
  - **verificar las reglas actuales en las webs oficiales antes de usarlas.** El preset de nico66fx
    trae FTMO sin límite de días; nuestra búsqueda 1 asumió 60, y eso puede cambiar la conclusión.
- También la cuenta propia de ~500 € con lote mínimo 0,01: ver si el riesgo cabe.

### Fase G. Examen virgen (semanas futuras)
- **Congelar** la lista final con hash, fechada, antes de ver ninguna semana posterior al 27/09.
- **Cada fin de semana,** con los datos que ya graba el bot (señales del diario, ticks del broker):
  - pasar las candidatas congeladas por la semana nueva;
  - apuntar el resultado en `docs/development/examen-forward.md`;
  - sin cambiar nada de las candidatas.
- **Tras 4 semanas:** revisión con Jose. Solo entonces se habla de demo en paralelo (con su OK
  expreso, como siempre).

## 4. Qué NO se hace
- No se toca el bot, ni la VM, ni la política en vivo; no se lanzan órdenes; no hay despliegues.
- No se ajusta el motor para que salgan mejores números.
- No se borra evidencia. Los resultados de la búsqueda 1 se quedan como están.
- No se repite la búsqueda sobre los mismos datos cambiando el juez tras ver los resultados. Si
  hay que cambiar algo, se apunta como "búsqueda 2b" y se sabe que ha perdido limpieza.

## 5. Orden de trabajo y entregas a Jose (cada una corta, en español llano)
1. Fase A completa, con el control de exactitud → **entrega 1:** "cómo se mueve el precio tras
   una señal y dónde está el terreno bueno", con los mapas.
2. Fase B (código y test) en paralelo; fase D escrita y congelada.
3. Piloto de tiempos → presupuesto final apuntado aquí → fase C → **entrega 2:** candidatas y cuántas pasan cada filtro.
4. Fase E1 (y E2 si Jose aprueba la descarga) → fase F → **entrega 3:** qué da en cuenta y en
   fondeo, con peor día y peor mes.
5. Congelado → fase G semanal.

Tiempos orientativos (a confirmar con el piloto): A, 1 día de trabajo; C, unas horas de cálculo;
E2, depende de la descarga.

## 6. Decisiones que tiene que tomar Jose antes de empezar
1. **L_día:** pérdida máxima aceptable en un mal día (por ejemplo, 5 % de la cuenta de fondeo, o X € en la propia).
2. **L_cesta:** pérdida máxima por cesta. Propuesta: la mitad de L_día.
3. **Descargar los ticks de Dukascopy 2018–2025** (fuente pública, varios GB, en disco local
   fuera de OneDrive): ¿sí o no?
4. **Cuenta objetivo** para dimensionar: fondeo de 10k, cuenta propia de ~500 €, o las dos.
5. **Aceptar que el examen limpio de verdad son semanas futuras:** mínimo 4 semanas antes de
   hablar de demo.

### 6b. Respuestas de Jose (27/09, dirección aún abierta)
- **Horizonte corto/medio plazo:** la fase E2 (Dukascopy 2018–2025) queda **descartada**. Se
  mantienen las pruebas de estrés E1.
- **Pérdida por cesta:** no más de 150 € como techo absoluto, pero "depende del capital y de la
  estructura: equilibrio". Pendiente de fijarlo en % de la cuenta (ver 6c).
- **Cuenta:** probablemente una cuenta propia de ~700 €; el fondeo aún no está decidido.
- **Le gustan** el Monte Carlo (nico66fx.github.io/montecarlo) y el Backtest Analyzer
  (nico66fx.github.io/backtest). Pide también buscar **repositorios de GitHub** de los que coger inspiración.

- **Objetivo (27/09, segunda respuesta):** enfocar el riesgo a **pasar un examen de cuenta de
  fondeo**. Con eso, los límites salen de las reglas del reto y no de un número fijo:
  - reto típico de 2 fases: 5 % de pérdida máxima diaria, 10 % total, objetivos del 10 % y 5 %;
  - margen de seguridad: L_día ≈ 2,5–3 % y L_cesta ≈ 1–1,5 % de la cuenta.
- **Capital inicial:** secundario. Se busca la **máxima rentabilidad** dentro de esos límites.
  El juez de la fase D pasa a ordenar por **probabilidad de pasar el reto y días hasta pasarlo**,
  con beneficio ÷ peor caída como segundo criterio.
- **Pendiente:** elegir la empresa de fondeo y comprobar sus reglas en su web. Algunas prohíben
  copiar señales de terceros, operar en noticias o mantener rejillas.

### 6c. Pendiente de cerrar
- Con 700 €, 150 € por cesta es el 21 % de la cuenta. Propuesta: fijar el límite en % de la
  cuenta (por ejemplo, un 5–7 % por cesta y un 10 % por día) para que escale con el capital.

## 7. Riesgos, dicho claro
- Cuantas más combinaciones se prueban, más "falsos ganadores" aparecen. Por eso las fases B, E y G no son opcionales.
- Puede salir que lo sostenible gana poco, como la Dubai #3. Eso también es una respuesta útil;
  entonces el trabajo pasa a escalar lo que funciona (más cestas o varias estrategias juntas),
  no a forzar números.
- El drawdown del modo histórico sale ~9 % optimista: los límites de riesgo se comprueban con ese margen añadido.

## 8. nico66fx: qué nos sirve (revisado el 27/09)
| Herramienta | ¿Sirve? | Cómo |
|---|---|---|
| Backtest Analyzer | Idea | Métricas (Sortino, Calmar, rachas, tiempo bajo el agua) y presets de fondeo. Su curva es solo de saldo al cerrar, sin flotante: con cestas sería optimista. Se replica en `account_sim.py`. |
| Monte Carlo de robustez | Idea | Ya lo tenemos por días. Se añade el remuestreo por operación como segunda vista. |
| Battle Bots | No | Comparar dos informes: lo hacemos en tablas propias. |
| Prompt "Ticks de Dukascopy a MT5" | **Sí** | Base para la fase E2: descarga, validación y desfase horario. No hace falta meterlo en MT5; usamos nuestro motor. |
| Prompt "Auditoría cuantitativa" | **Sí** | Lista de banderas rojas (martingala, rejilla, sin stop, dependencia de pocas operaciones, estabilidad por tercios): se incorpora al informe de cada candidata. |
| Fábrica de Estrategias (app) | Quizá | Genera miles de EA en MT5 con filtros de robustez: es lo mismo que hacemos nosotros, pero sin nuestras señales ni el flotante. Revisarla exige descargar un .zip de un tercero: solo con OK de Jose y revisando el código antes. |
| Indicadores ICT/SMC (sesiones, barridos, FVG, máximo y mínimo de ayer) | Idea | Se convierten en filtros causales de la fase C3. |
| EAs sueltos (.mq5/.ex5) | No | Estrategias genéricas sin nuestras señales. Los .ex5 no se pueden revisar. |

## 8b. Repositorios de GitHub revisados (27/09)
| Repo | Qué aporta | Uso propuesto |
|---|---|---|
| [LuxAlgo/prop-firm-sim](https://github.com/LuxAlgo/prop-firm-sim) (MIT, TypeScript, npm) | Monte Carlo de retos con las reglas de más de 25 empresas: pérdida diaria estática o móvil, reglas de consistencia, noticias, número de intentos, coste esperado y riesgo óptimo. Acepta historiales MT4/MT5/CSV. Solo mira el cierre de cada operación, sin excursiones intradía. | **Fuente de reglas por empresa** y contraste de nuestra fase F. Nuestro simulador de cuenta sigue siendo el que manda, porque usa el flotante real. Se ejecuta con `npx` en local; antes, revisar el código y pedir OK para instalar. |
| [DaniyalMlk/holdout](https://github.com/DaniyalMlk/holdout) (MIT, Python, solo numpy) | PBO por CSCV, Sharpe deflactado, validación cruzada con purga, correcciones por pruebas múltiples. Proyecto joven (0 estrellas), pero con tests contra ejemplos publicados. | Referencia para la fase B. Lo implementamos nosotros (es corto) y lo contrastamos con su resultado en un caso de prueba. |
| [tulinette/backtest-overfitting-lab](https://github.com/tulinette/backtest-overfitting-lab), [mrbcuda/pbo](https://github.com/mrbcuda/pbo) | Otras implementaciones de PBO. | Segunda referencia para los tests. |
| [polakowo/vectorbt](https://github.com/polakowo/vectorbt) | Backtest vectorizado de miles de reglas. | Idea para la calculadora de la fase A. No lo usamos tal cual: nuestras reglas van sobre los recorridos por señal, con bid/ask. |
| quantstats | Informes de métricas. | Métricas que copiar en el informe (Sortino, Calmar, rachas, tiempo bajo el agua). |

## 8c. Reglas de fondeo: avisos (27/09, a verificar con la empresa elegida)
- **FTMO:** no hay límite de días (confirmado en su FAQ); mínimo 4 días de trading por fase; los EA están permitidos.
- **Copiar señales de terceros es zona gris.** FTMO no permite que un tercero opere por ti. Además, si mucha gente opera exactamente lo mismo (un EA o un canal compartido), puede superarse el tope de capital por estrategia. Nuestra gestión propia hace que las operaciones no sean idénticas a las de otros seguidores del canal, pero **hay que preguntárselo a la empresa antes de pagar un reto**.
- **Noticias:** en el reto de FTMO no se puede abrir operación de 2 min antes a 2 min después de una noticia de alto impacto. El filtro "día o hora con dato fuerte" de la fase C sirve también para cumplir esto.

## 8d. Corrección sobre el motor (27/09, al simular la sombra)
En la reproducción histórica (`research/history_search.execution_for`), cada canal usa una extensión de protección fija:
- **canal1** usa `basket_guard_v1`, que solo admite tope de cesta sin objetivos, break-even, trailing ni stop por pata;
- **canal2** no usa ninguna, así que el break-even por precio y el parcial con resto que corre dan `protection_policy_unsupported` y la cesta no se abre.

Se ha añadido una opción compatible (`cal["policy_extension"][canal]`) para elegir la extensión (`own_rule_be_partial_v1`). Antes de usarla en la búsqueda 2 hay que:
1. comprobar con tests que, con la extensión, las gestiones antiguas dan lo mismo;
2. hacer un examen contra una semana real con alguna estrategia que la use; hoy no hay ninguna en vivo con break-even por precio.

Hasta entonces, las familias que la necesitan se marcan como "sin validar contra la realidad".

## 9. Fronteras aparcadas (para más adelante, si Jose quiere)
- Mantener cestas de un día para otro: exige ampliar el motor y validarlo otra vez contra la realidad.
- Aprender la estrategia del proveedor: en pausa por decisión de Jose.
- Otros activos: no hay señales.
