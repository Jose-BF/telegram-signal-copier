# Búsqueda 2: plan de ejecución (27/09/2026, v2)

Documento para el modelo que ejecuta. El porqué y el contexto están en `plan-busqueda-2.md`;
aquí va el qué, el orden, cómo se comprueba y cuándo se da por hecho. Marcar cada hito como
`[x]` al verificarlo, con la evidencia (ruta y hash o salida de test).

**Mandato de Jose (27/09):** "QUIERO CONSEGUIR DINERO". "Me quiero asegurar que vamos a hacer una
búsqueda amplia y con muchas vertientes, no quiero que se nos escape un camino". "Aquí las riendas
las tomas tú, quiero que me devuelvas un resultado tipo: haciendo esto y esto obtenemos esto = dinero".
Claude interpreta esto como aprobación del plan, incluida la búsqueda grande dentro del
presupuesto de M6. Si el presupuesto medido lo supera, se avisa antes de lanzar.

## 1. Qué se entrega al final
Una tabla por carril. Cada fila es una **receta completa** y su dinero:

> **Receta:** coger las señales de [canal] que cumplan [filtro] → entrar [cómo] → salir [cómo] →
> lote [x] → en una cuenta [tipo y tamaño].
> **Dinero:** +X €/mes típico (mediana), +Y €/mes en un mes malo (percentil 10), peor día −Z €,
> peor cesta −W €, meses positivos N de M. Fondeo: probabilidad de pasar el reto P % en D días
> medianos. Cuenta propia: capital mínimo recomendado C €.
> **Confianza:** en qué pruebas aguantó (cortes de tiempo, brokers, estrés, placebo) y en cuáles no.

Dos carriles, con juez distinto:

| | **Carril A: fondeo** | **Carril B: cuenta propia (tipo 555)** |
|---|---|---|
| Para qué | Pasar un reto (propuesta: FTMO 2 fases) y cobrar | Ganar con capital propio, al estilo de la 555 |
| Stop | Obligatorio (por pata o tope de cesta) | **Opcional**: se permite aguantar sin stop, como la 555 |
| Límite | Peor día ≤ 2,5 % y peor cesta ≤ 1,5 % de la cuenta; con estrés, peor día ≤ 5 % | Sin límite fijo: de su peor flotante (muestra y estrés) sale el **capital mínimo** para sobrevivirlo con margen × 2 |
| Orden | Probabilidad de pasar el reto → días hasta pasarlo → beneficio ÷ peor caída | **€/mes por cada 1.000 € de capital mínimo** → meses positivos → peor mes |

Una misma gestión puede aparecer en los dos carriles.

## 2. Catálogo de vertientes: qué NO se puede quedar sin probar
Cada vertiente tiene un código. **El informe final incluye una tabla de cobertura:** por
vertiente, cuántas variantes se probaron, cuántas pasaron cada filtro y la mejor. Una vertiente
con 0 variantes probadas es un fallo del trabajo, no un resultado.

| Código | Vertiente | Variantes mínimas |
|---|---|---|
| S1 | Todas las señales del canal | — |
| S2 | Filtros de contexto v1: hora, día, RSI, ATR, rango 4h, tendencia, movimiento previo | todos los de `grid()` |
| S3 | Filtros v2 (M5): sesión, máximo y mínimo de ayer, rango asiático, barrido de liquidez, números redondos, régimen de volatilidad, noticias, rachas del proveedor, resultado de la señal anterior | simples y pares |
| S4 | Solo meses recientes (idea de Jose): buscar en jul–sep y comprobar en ene–jun, y al revés | los dos sentidos |
| S5 | Tipo de mensaje: sticker, texto, reenvío "VIP 3.0", primera señal del día, señales seguidas | cada uno |
| S6 | Selector aprendido: regresión logística y árbol pequeño sobre rasgos causales, con validación anidada por meses | 2 modelos |
| D1 | Seguir la dirección | — |
| D2 | Invertir la dirección | todas las gestiones |
| D3 | Dirección del mercado: la señal solo marca el momento, y se entra a favor del primer movimiento de X $ | X = 1, 2, 3, 5 |
| D4 | Las dos a la vez: una cesta a favor y otra en contra, cada una con su gestión | pares de gestiones |
| D5 | Sin señal del proveedor: disparadores propios a horas de mercado parecidas (universos placebo como estrategia) | 3 semillas |
| E1 | Entrada a mercado | — |
| E2 | Esperar retroceso X $ (`pullback`) | X = 0,5–5 |
| E3 | Esperar X $ en contra y rebote Y $ (`adverse_reversal`, la de la 555) | rejilla X × Y |
| E4 | Esperar confirmación a favor (`momentum`) | X = 0,5–5 |
| E5 | Esperar N minutos (`delay`) | N = 1–30 |
| L1 | Una sola entrada | — |
| L2 | Escalera en contra: promediar o rejilla (la de Dubai y la 555) | 2–8 patas, pasos 0,5–6 $ |
| L3 | Escalera a favor: piramidar (`favourable`) | 2–5 patas |
| L4 | Forma del lote: plano, cargado al principio, cargado al final (tipo martingala; se marca con bandera roja) | las 3 |
| X1 | Objetivo fijo por pata, escalonado (`per_leg_steps`, la de la 555) | rejillas |
| X2 | Objetivo por movimiento `fixed_move` y por dinero de la cesta `fixed_basket` | rejillas |
| X3 | Parcial y un resto que corre (`partial_runner`) | fracción × objetivo |
| X4 | Trailing | 5–40 $ |
| X5 | Asegurar beneficio: activar a +A € y cerrar si devuelve G € | rejillas |
| X6 | Break-even por precio, por tiempo o tras un parcial | rejillas |
| X7 | Salida por tiempo: siempre, solo en pérdida, solo en beneficio, sin pérdida | 3–240 min |
| R1 | Sin stop (solo carril B) | — |
| R2 | Stop fijo por pata en $ | 3–40 $ |
| R3 | Tope de pérdida de la cesta en € | 20–300 € |
| R4 | Stop duro en € por pata | 5–50 € |
| K1 | Lote fijo | — |
| K2 | Lote según la volatilidad (ATR) | 3 escalas |
| K3 | Lote según la cuenta (porcentaje de riesgo) | 0,25–2 % |
| C1 | Cartera: canal1 y canal2 juntos en una cuenta | las mejores de cada uno |
| C2 | Varias gestiones a la vez sobre la misma señal (repartir el lote) | pares |
| C3 | Cambiar de gestión según el régimen de mercado (volatilidad), decidido con datos anteriores a la señal | 2 regímenes |
| B1 | Los tres brokers: rápido A_p50, lento C_p50 y atascado C_p90 | siempre, en finalistas |

Las vertientes S6, D4, C2 y C3 son las de más riesgo de sobreajuste. Se prueban igual, pero con
validación anidada: la elección se hace dentro de cada corte, nunca con todo el periodo.

## 3. Embudo: cómo se prueba todo sin tardar semanas
El motor exacto cuesta ~4 s de CPU por gestión × mes × universo (medido en la búsqueda 1:
5 h, 2 CPU, 500 gestiones × 3 universos × 6 meses). Con 4 procesos, 2.000 gestiones × 5 universos
× 9 meses serían ~25 h: demasiado para usarlo a ciegas. Por eso hay tres niveles:

1. **Nivel 1: calculadora (M2).** Millones de combinaciones de regla × filtro × dirección en
   minutos u horas, sobre los recorridos precalculados.
   - Reglas simples (una entrada, objetivo, stop, tiempo, dirección): **tablas de primer paso**
     por señal (el segundo en que el precio toca +X y −X, para X = 0,5…60 $). Cada regla es
     entonces una comparación de tiempos, sin recorrer ticks.
   - Reglas de cesta (escaleras, tope en €, asegurar beneficio, trailing, break-even): bucle
     numba por señal a 1 s. Espacio más grueso: ~50–100 mil reglas por canal.
   - Cubre las vertientes S, D, E, L, X, R, K1 y K2 de forma aproximada (sin latencias de broker).
2. **Nivel 2: motor exacto por familias (M6).**
   - Las ~200 mejores zonas del nivel 1 por vertiente y carril, como genomas, más una muestra
     aleatoria de cada familia del motor, para no depender solo de la calculadora.
   - Universo real y un placebo, broker C_p50.
3. **Nivel 3: finalistas exactas.** Tres brokers, todos los universos, curvas minuto a minuto,
   cortes de tiempo, estrés, reto o capital mínimo.

La calculadora solo decide qué se mira con más detalle. **Ninguna receta final sale del nivel 1.**

## 4. Fuera de alcance
- No se toca el bot, ni la VM, ni la política en vivo; no se lanzan órdenes; no hay despliegues.
- No se pagan retos, no se crean cuentas, no se instalan paquetes de terceros sin OK.
- No hay datos de años anteriores (Dukascopy descartado por Jose).
- No se ajusta el motor para mejorar números, ni se cambian criterios tras ver resultados.

## 5. Reglas de trabajo
- Código nuevo en `research/` (lógica) y `search2/` (scripts), con test en `tests/`. Seguir el
  estilo de `search/`: reanudable por lotes, protección `main()` para Windows.
- **Recursos:** como máximo **4 procesos**. Con 10 se agotó la RAM (16 GB) el 27/09. Mirar la
  memoria libre antes de un cálculo largo.
- Datos en `runtime_data/<nombre>_v1/` con `manifest.json` (hashes, semilla, versión del código),
  sin sobrescribir nunca.
- Tests base que deben seguir pasando (26 a 27/09):
  `python -m pytest -q tests/test_history_replay.py tests/test_gold_basket_cap.py tests/test_run_week_exam_score.py tests/test_latency_model.py tests/test_history_search_extension.py`
- **Avisar a Jose en cada entrega,** en corto y en español llano. El detalle, en los documentos.

## 6. Interfaces existentes que se reutilizan (no reescribir)
- Días, reloj del broker y retraso de Telegram: `research/history_replay.py` (`History.days`, `make_trigger_path`).
- Motor exacto en paralelo: `research/history_search.py` (`evaluate`, `metrics`, `execution_for`,
  `basket_curve`). `execution_for` admite `cal["policy_extension"][canal]`.
- Genomas: `research/dubai_iterative/contracts.py` (`StrategyGenome`, `validation_errors`, `with_change`).
- Rasgos y filtros: `research/signal_features.py`, `research/signal_filters.py`.
- Cuenta con flotante: `research/account_sim.py`.
- Estrategias en vivo y de sombra como genomas: `tools/shadow_week_replay.py` (`shadow_genomes`).
- Señales y placebos: `runtime_data/signal_universe_v1/`. Calibraciones: `runtime_data/execution_calibration_v2/`.

## 7. Hitos

### M0. Reglas de fondeo como datos — [x] 27/09
Evidencia:
- `research/prop_rules.py` con FTMO, The5ers y FundedNext, fuentes oficiales consultadas el 27/09;
- `tests/test_prop_rules.py`: 4 tests OK;
- `docs/development/pregunta-soporte-fondeo.md`.

Hallazgos:
- en FTMO la ventana de noticias (±2 min) se aplica **solo a la cuenta fondeada estándar**, no al
  reto (esto corrige plan-busqueda-2 §8c);
- FTMO tiene regla del "mejor día" (≤ 50 % del beneficio de los días positivos);
- The5ers exige 3 días con beneficio ≥ 0,5 % y aplica la ventana de noticias también en el reto;
- FundedNext tiene objetivo del 8 % en la fase 1, pero los EA van con complementos de pago.
- `research/prop_rules.py`: FTMO 2 fases, The5ers y FundedNext.
  - Campos: objetivos, pérdida diaria (%, si cuenta el flotante y desde qué saldo), pérdida total
    (estática o móvil), días mínimos, límite de días, ventana de noticias, consistencia.
  - Cada valor lleva URL oficial y fecha; lo no confirmado se marca `unverified`.
  - Referencia de campos: [LuxAlgo/prop-firm-sim](https://github.com/LuxAlgo/prop-firm-sim) (solo leer).
- `docs/development/pregunta-soporte-fondeo.md`: texto para que Jose pregunte si se permite un EA
  propio que usa avisos de un canal como disparador.
- **Hecho cuando:** hay test que carga cada preset y rechaza valores sin fuente.

### M1. Recorridos y tablas de primer paso — [x] 27/09
Evidencia:
- `research/signal_paths.py`, `tests/test_signal_paths.py` (5 OK);
- `runtime_data/signal_paths_v1/{real,placebo_random_time_s0..s2,placebo_flipped_s0}.npz/.json`;
- recuento: reales 1.723/1.723; placebos de hora 1.716, 1.717 y 1.714, con las exclusiones
  registradas ("no_tape_or_after_tape_end"); invertido 1.723/1.723;
- cordura: la señal real gana la carrera ±3/5/10 $ el 45–48 %, igual que el estudio de la sección 17.

**Cambio frente al plan:** el disco C: tiene solo ~7,8 GB libres, así que los recorridos a 1 s
**no se guardan**. `iter_paths()` / `second_path()` los reconstruyen en memoria día a día
(11 s el universo real con 4 procesos). En disco solo va lo compacto: ~1 MB por universo.
- `research/signal_paths.py`, para cada señal de cada universo (reales, `placebo_random_time_s0..s2`,
  `placebo_flipped_s0`):
  - 6 h desde `observed_at` a 1 s: bid, ask y máximo/mínimo dentro del segundo;
  - tablas de primer paso para X = 0,5…60 $ a favor y en contra, en precio de salida real (bid
    para compras, ask para ventas);
  - rasgos del recorrido: MFE y MAE a 5, 15, 60 y 240 min.
- Salida en `runtime_data/signal_paths_v1/`.
- Tests:
  - recorrido sintético con primer paso conocido;
  - corte al final del día del broker;
  - reloj alrededor del 08/03;
  - el recuento cuadra con `signals.jsonl` (cada señal excluida lleva motivo).

### M2. Calculadora (nivel 1) — [x] 27/09, con límites
Evidencia:
- `research/bracket_grid.py` (reglas simples), `research/basket_grid.py` (cestas, numba en paralelo),
  `research/basket_bridge.py` (regla ↔ genoma);
- tests `tests/test_bracket_grid.py` (3) y `tests/test_basket_grid.py` (6);
- control `search2/check_grid_vs_engine.py` → `out/search2/check_grid_vs_engine.json`, sobre
  todas las señales reales con broker C_p50.

Resultado del criterio fijado:
- **Simples: 20/20 pasan.** El signo coincide en el 98,5–99,9 % y el error es ≤ 3,5 %. Correlación de
  rangos entre reglas 0,95; la calculadora es optimista en +0,03 €/operación.
- **Cestas: 5/9 pasan, 4 fallan** (555, b210, escalera de 3 patas, pullback).
  - El signo coincide en el 88–97 %. Correlación de rangos 0,73 y ruido de ±0,5 €/cesta por regla.
  - La 555 queda entre +345 € (llenando en el nivel) y −1.520 € (llenando a mercado el segundo
    siguiente), frente a −483 € del motor: con 1 s de resolución no se reproduce el retraso de
    ~1,2 s del bot en rebotes rápidos.
  - La Dubai #3 cuadra (−427 frente a −420) y la Gold #1 se acerca (−2.506 frente a −2.814).

**Decisión (Claude, 27/09):**
- las reglas simples usan la calculadora como criba;
- en las de cesta, la calculadora es solo orientativa: toda familia a menos de 1 €/cesta del cero
  (o mejor) pasa al motor exacto, y en M6 la muestra aleatoria por familia del motor se dobla
  (~400 genomas por familia), para no depender de la calculadora en cestas.

Hallazgo del primer barrido de reglas simples:
- lo "mejor" es invertir y aguantar 60–90 min, pero los placebos de hora con la misma dirección
  lo superan, así que es tendencia del oro y no ventaja;
- **el juez tiene que comparar siempre con placebos de la misma dirección.**
- `research/bracket_grid.py`:
  - reglas simples con las tablas de primer paso;
  - reglas de cesta con numba a 1 s (escalera en contra y a favor con pesos, objetivos por pata,
    tope en €, asegurar beneficio, trailing, break-even, salida por tiempo, D1–D4);
  - si objetivo y stop caen en el mismo segundo, se asume lo peor;
  - salida: matriz regla × señal en €, al lote de la regla, con el flotante mínimo por señal (para el carril B).
- **Control de exactitud** (`search2/check_grid_vs_engine.py`): 30 reglas, de ellas 10 de cesta
  (incluida la 555 tal cual), pasadas también por `evaluate` con C_p50.
  - **Hecho cuando** el signo coincide en ≥ 95 % de las cestas y el total difiere ≤ 10 % en las simples y ≤ 20 % en las de cesta.
  - Si falla, se corrige la calculadora; el motor no se toca.

### M3. Entrega 1: el mapa — [x] 27/09
Evidencia: `search2/map_report.py` → `out/search2/mapa/{informe.html,mapa.json}`; comprobación
de pistas con el motor exacto `search2/check_leads.py` → `out/search2/pistas_motor.json`.

**Resumen para Jose (entrega 1):**
1. Seguir la señal tal cual, con una sola entrada, pierde en casi todo el terreno en los dos canales.
2. **Canal 1 (Dubai): el precio tiende a darse la vuelta tras la señal.** Invertir gana con
   cualquier espera de 15 a 240 min, y los placebos con la misma dirección y día dan ~0.
3. La mejor forma "repartida" es invertir con objetivo +20 $ y cierre a 60 min: motor exacto
   +730/+753/+754 € (brokers C/A/C_p90) a 0,01 lotes; 7 de 9 meses positivos; peor cesta −107 €.
   Pero **julio y agosto negativos**, y sale casi todo de invertir las compras de Dubai de 7 a 17 UTC.
4. Invertir y aguantar 60 min sin objetivo depende casi entero de enero (desplome del 29/01):
   sin su 5 % mejor pierde. Frágil.
5. **Canal 2 (Gold):** la pista de invertir y aguantar (+833 €) la igualan los placebos (+786/+866): es
   tendencia del oro. Descartada.
6. Familia Dubai (escalera 3 $, tope 50 €): +685 € con broker lento, pero −443 € con el atascado. Frágil.
7. Familia 555: solo 1 de 100 variantes positiva en la calculadora, con cestas de hasta −412 €.

### M4. Cortes de tiempo y PBO — [x] 27/09
Evidencia: `research/folds.py`, `tests/test_folds.py` (5 OK: ruido ≈ 0,49 de media en 40 sorteos,
candidata dominante ≈ 0, candidatas sobreajustadas ≥ 0,5).

**Resultado clave:**
- **elegir la mejor de las 4.055 reglas simples no generaliza:** PBO 0,63 (canal 1) y 0,56 (canal 2);
  la elegida solo gana fuera de muestra en el 32 % / 31 % de los cortes. Con placebos sale igual o
  mejor, así que es ruido;
- con hipótesis estructuradas (seguir o invertir × tiempo de espera, 14 candidatas), canal 1 tiene
  toda la familia "invertir" en positivo, pero el PBO sigue en 0,55 porque la mejor espera cambia de mes a mes.

**Consecuencia para M6–M7:** no se elige "la mejor de miles". Se eligen **familias** con sentido y
se exige que la familia entera (vecindario) gane fuera de muestra. Se prefieren reglas que
limitan la dependencia de pocas operaciones (objetivo que corta las colas), y siempre con el
control de placebo de la misma dirección.
- `search2/map_report.py` genera `out/search2/mapa/informe.html` (offline):
  - mapas de calor objetivo × stop por tiempo de salida, y mapa de la familia 555 (retroceso ×
    rebote × paso), con la media por señal y los meses positivos, por canal;
  - la misma rejilla con placebos, para separar la parte de mercado de la de señal;
  - curvas MFE/MAE;
  - el mapa mes a mes, para ver si el terreno bueno se mueve.
- Resumen de 10 líneas para Jose.

### M4 (plan original)
- `research/folds.py`:
  - hacia delante y hacia atrás;
  - ventanas de 3 meses que avanzan de 1 en 1;
  - dejar un mes fuera;
  - CSCV (todas las formas de partir los meses en dos grupos iguales);
  - S4: solo meses recientes, en los dos sentidos.
- `pbo()`: porcentaje de cortes en que la mejor del lado "elegir" queda por debajo de la mediana
  en el lado "comprobar".
- Tests:
  - ruido → ≈ 50 %;
  - candidata dominante → ≈ 0 %;
  - un ejemplo de [holdout](https://github.com/DaniyalMlk/holdout) o [pbo](https://github.com/mrbcuda/pbo) reproducido.
- Se aplica al nivel 1 y el PBO por vertiente entra en el informe.

### M5. Rasgos v2 y selector aprendido — [ ]
- `signal_features.py` v2, en una función nueva que no cambia la v1, con los rasgos de S3.
  - Calendario de noticias escrito a mano en `runtime_data/news_calendar_2026.json` (NFP, IPC, FOMC,
    con hora UTC y fuente); sirve también para la regla de 2 min de FTMO.
- Test de causalidad por rasgo: recalcular con los ticks cortados en la hora de la señal da lo mismo.
- `grid_v2()`, sin cambiar `grid()`.
- `research/signal_selector.py` (S6):
  - regresión logística y árbol de profundidad ≤ 3 que predicen "señal buena" para una regla dada;
  - entrenados solo dentro de cada corte de M4;
  - informan de su resultado fuera de muestra, no dentro.

### M5. Rasgos v2 — [x] 27/09 (el selector aprendido pasa a M7, dentro de los cortes)
Evidencia:
- `research/signal_features_v2.py`, `tests/test_signal_features_v2.py` (3 OK: un salto de +500 $
  después de t0 no cambia ningún rasgo; la orientación se invierte con la dirección; el resultado
  previo solo cuenta si ya estaba decidido);
- `runtime_data/news_calendar_2026.json` (NFP, IPC y FOMC con fuente oficial BLS/Fed);
- `runtime_data/minute_bars_v1.parquet` (260.609 barras);
- `runtime_data/features_v2_{real,placebo_random_time_s0..s2,placebo_flipped_s0}.jsonl`;
- `signal_filters.grid_v2()`: 1.287 filtros (448 de la v1 intactos).

Dato: 22 señales reales caen a menos de 30 min de una noticia fuerte, frente a 10–15 en los placebos.

### Rediseño de M6–M7 tras M4 (27/09)
M4 demostró que "la mejor de miles" es suerte. Por eso la unidad de decisión pasa a ser la
**familia** (una hipótesis con una rejilla pequeña de parámetros), y la estimación de dinero es un
**walk-forward**:
- cada mes m desde el cuarto, se elige la mejor combinación (parámetros × filtro) de la familia
  **solo con los meses anteriores a m** y se opera el mes m;
- la curva de esos meses operados es lo que habría ganado el procedimiento;
- se repite igual con placebos: el procedimiento con placebos debe quedarse en ~0;
- la receta final es la elección con todos los meses, pero **el dinero que se promete es el del
  walk-forward**, no el de la muestra;
- los 3 brokers y el estrés se aplican a la receta final.

Coste medido del motor: ~18 s de CPU por gestión en todo el periodo con un universo (29 gestiones
en 128 s con 4 procesos). Una familia de 200 gestiones con real + placebo son ~30 min.

### Estado 28/09
**M6, M7 y M8 hechos** (resumen en `resultados-busqueda-2.md`); **M9 pendiente** (examen semanal).
- Placebo de las 3 familias con indicios: las 3 superan a su placebo.
- Recetas congeladas en `search2/frozen_v1.json`.
- Estrés con topes inyectados y cartera en FTMO: `out/search2/final/`.

### M6. Motor exacto por familias (nivel 2) — [x]
Operativa:
- el portátil se suspendió de 17:30 a 22:32 del 27/09 y el cálculo se paró; ahora el script corre
  pidiendo "mantener despierto" al sistema (SetThreadExecutionState), sin cambiar la configuración;
- velocidad real ~40 s de CPU por gestión (el doble de lo estimado, por las cestas de hasta 4 h),
  así que primero van todas las familias con señales reales y luego el placebo solo de las prometedoras.

**Primer juicio: c1_invertir_1pata** (walk-forward abril–septiembre, 0,01 lotes, broker C):
- carril A +28 € (3/6 meses); carril B −92 € (1/6); sin filtro −594 €;
- variantes prudentes entre −594 € y +168 €;
- **la pista del mapa (+740 € mirando todo el año) no se sostiene al elegir de forma honesta:
  es ruido.**
1. **Validar `own_rule_be_partial_v1`** (plan-busqueda-2 §8d): test de que las gestiones sin
   break-even ni parcial dan lo mismo con y sin la extensión. Las familias que la necesitan se
   marcan `unvalidated_extension`.
2. **`research/history_space2.py`:**
   - genomas desde las zonas del nivel 1, más una muestra aleatoria por familia del motor;
   - referencias: la estrategia actual, 555, Dubai #3, Gold #1 y las 6 de la sombra;
   - pérdida máxima por cesta calculada y guardada con cada genoma.
3. **`search/placebo_on_real.py`:** la comprobación pendiente de la búsqueda 1.
4. **Piloto:** 50 gestiones × 1 mes × `workers` 2/3/4. Anotar segundos y RAM aquí y fijar el
   presupuesto. Si pasa de ~10 h, se avisa a Jose antes.
5. **`search2/stage2.py`:** todo el periodo, universo real y un placebo, broker C_p50, reanudable.

**c1_seguir_1pata** (walk-forward): carril A −161 € (2/6); **carril B +122 € (6/6 meses)**; variantes
prudentes entre −15 € y +210 €. La elección final es "precio cerca del mínimo de ayer y barrido de
liquidez a favor". Cantidades pequeñas (~20 €/mes a 0,01 lotes). Falta el placebo.

**Las 10 familias con señales reales, broker C** (walk-forward honesto, € a los lotes de la familia;
Dubai abr–sep, Gold jun–sep; resumen de `out/search2/juicio/C_p50.json`):

| Familia | Procedimiento principal | Variantes prudentes | Lectura |
|---|---|---|---|
| c1_invertir_1pata | A +28 / B −92 | −594 … +168 | ruido |
| c1_seguir_1pata | A −161 / **B +122 (6/6)** | −92 … +210 | indicio pequeño → placebo |
| c1_escalera | +90 | −245 … +51 | ruido |
| c1_invertir_escalera | −75 | −263 … +247 | ruido |
| c2_555 (B) | **−548 (1/4)** | −721 … +79 | pierde |
| c2_555_tope | −299 | −608 … +66 | pierde |
| c2_seguir_1pata | +10 | −206 … −23 | ruido/pierde |
| c2_invertir_1pata | −4 | −135 … **+197** | indicio → placebo |
| **c2_escalera** | +34 | **+334 (4/4)** … +335 | **candidata** → placebo + brokers |
| c2_pullback | −43 | −67 … +37 | ruido |

**c2_escalera:**
- con filtros sencillos elige **cada mes** `asia_loc > 0.8` (la señal llega con el precio en el
  extremo del rango asiático del lado de la señal, es decir, a favor de la ruptura) y una gestión
  casi igual: escalera de 4–6 × 0,01, tope 100 €, asegurar beneficio a +15 € con 5 € de margen;
  gana los 4 meses (+100/+11/+124/+99 €);
- sin filtro, la mejor gestión del año gana 5 de 6 meses.

**Receta candidata R1: c2_escalera + `asia_loc > 0.8`** (`out/search2/receta_c2_escalera_n30.json`,
`out/search2/final/receta_c2_escalera_n30_final.json`):
- canal 2 (Gold), solo señales que llegan con el precio en el extremo del rango asiático
  (00–07 UTC) del lado de la señal;
- 6 entradas de 0,01 a mercado, en escalera en contra cada 2 $;
- tope de cesta −100 €; asegurar beneficio a +15 € con 5 € de margen; pasados 3 min, cerrar si hay beneficio.

Motor exacto, abril–septiembre, 294 cestas:
- neto +474 / +553 / +518 € (brokers rápido / lento / atascado), 6/6 meses positivos en los tres;
- peor día −93 / −92 / −76 €; peor cesta −100 (el rápido tocó el tope 1 vez) / −1 / −16 €;
- flotante de hasta −94 €: **gana aguantando**. Sin el filtro, la misma gestión pierde −570 € y toca
  el tope 23 veces (2,4 %).

Estrés con topes inyectados. La receta deja de ganar si más del ~1,8 % de las cestas tocan el tope.
Con lote ×30 en FTMO 100k (1,8 lotes por cesta, tope de 3.000 €):

| Topes | €/mes (mediana) | Pasa | Suspende | Días |
|---|---|---|---|---|
| 0 % | 2.950 | 100 % | 0 % | 157 |
| 0,5 % | 2.170 | 93 % | 4 % | 200 |
| 1 % | 1.410 | 61 % | 17 % | 242 |
| 1,6 % | 360 | 19 % | 44 % | 265 |
| 2,4 % | pierde | 2 % | 82 % | — |

Visto en la muestra: 0–1 topes de 294 (0–0,34 %). Cota alta del 95 % ≈ 1,6 %.

**Cobertura con la calculadora** (`search2/calc_coverage.py` → `out/search2/cobertura/calc_coverage.json`):
- probadas E4, E5, L3, X3, X6, R4, D3, D4, K2 y S6, cada una seguir e invertir, con walk-forward
  real frente a placebo;
- **todo queda entre −380 € y +200 € (a 0,01 lotes y 4–6 meses) y los placebos dan parecido: ruido**;
- algo mejores que su placebo, pero pequeños:
  - c2 D3 (dirección del mercado, 1 $): +91 € frente a +3 €;
  - c1 R4 (555 con stop duro): +101 € frente a −127 €;
  - c2 K2 (lote por volatilidad): +124 € frente a +16 €;
- el selector aprendido (S6) no mejora de forma consistente.

### M7. Juez de los dos carriles y estrés (nivel 3) — [ ]
- `research/search_judge2.py`. Umbrales escritos y con hash en `search2/judge2_frozen.json`
  **antes** de ver los resultados de M6.
  - **Comunes:**
    - ≥ 70 % de meses positivos;
    - positiva en el lado "comprobar" en ≥ 75 % de los cortes;
    - positiva con los tres brokers;
    - vecinos ≥ 70 %;
    - sigue positiva sin su 5 % de mejores cestas;
    - etiqueta de origen (mercado o proveedor) por placebos, sin eliminar.
  - **Carril A:** stop obligatorio; límites de la sección 1.
  - **Carril B:** capital mínimo = 2 × el peor de (flotante más profundo en la muestra, peor caso
    del estrés). Se ordena por €/mes por cada 1.000 € de capital mínimo.
- `research/stress2.py`:
  - golpe en contra de 10, 20 y 40 $ en 1 min tras la entrada, con el stop deslizado entero;
  - spread × 3;
  - retraso de 2 min;
  - las cestas repetidas en los 10 peores días de 2026, con todas las horas de señal;
  - **en el carril B, el estrés dice cuánto capital hace falta, no si pasa.**
- Banderas rojas en cada candidata: martingala, rejilla, sin stop, dependencia de pocas
  operaciones, estabilidad por tercios.

### M8. Cuenta, reto y entrega 2 — [ ]
- `research/prop_sim.py` sobre `account_sim.py`:
  - flotante minuto a minuto, reglas de M0;
  - arranques en cada día y Monte Carlo por bloques de días;
  - probabilidad de pasar cada fase, días medianos, motivo de suspenso y lote óptimo;
  - cartera C1–C3, respetando que canal1 lleva una cesta cada vez.
- Carril B: curva con el capital mínimo; €/mes mediano y en un mes malo; peor racha.
- `out/search2/informe_final.html`, al estilo del Backtest Analyzer y el Monte Carlo de nico66fx,
  **con flotante**:
  - las tablas "receta → dinero" de la sección 1;
  - la tabla de cobertura de vertientes;
  - comparativa lado a lado.
  - Publicarlo como Artifact privado para verlo en el móvil.
- **Entrega 2 a Jose:** las recetas y el dinero, en una pantalla.

### M9. Congelado y examen semanal — [ ]
- Recetas finales con hash en `search2/frozen_v1.json`, antes de ver cualquier semana posterior.
- **Cada fin de semana:** datos de la VM por SSH en solo lectura (clave `C:\Users\josea\claude-vm`),
  versión generalizada de `tools/shadow_week_replay.py`, resultado en `docs/development/examen-forward.md`.
- **Tras 4 semanas:** revisión con Jose. Si aguantan, se proponen demo, reto o cambio en vivo,
  siempre con su OK expreso para ese cambio.

## 8. Orden
M0 → M1 → M2 → M3 (entrega 1) → M4 y M5 en paralelo → M6 → M7 → M8 (entrega 2) → M9.
Los hitos M1, M2 y M4 son la base de todo: al terminarlos, revisarlos con un modelo de más esfuerzo antes de seguir.

## 9. Decisiones de Jose
1. **Empresa de fondeo:** propuesta FTMO 2 fases. Antes de pagar, enviar la pregunta de M0 a soporte.
2. **Presupuesto de cálculo:** aprobado dentro de ~10 h por tanda. Si hace falta más, se pregunta.
