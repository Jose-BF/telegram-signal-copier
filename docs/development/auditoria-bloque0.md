# Bloque 0: auditoría de lo heredado (28–29/09/2026)

Objetivo: saber qué piezas se pueden usar tal cual, cuáles hay que auditar antes de usarlas y
cuáles se descartan. Filas del mapa atacadas: L1, L2, I1, A1–A4, B1, y una nueva (A6).

## Clasificación
| Pieza | Qué es | Evidencia | Veredicto |
|---|---|---|---|
| `research/dubai_iterative/engine.py`, `fast_engine.py` | Motor exacto (lento y rápido) | Examen contra operaciones reales 21–25/09 (55/55) y agosto (28/28); los motores coinciden (49/49) | **Usable**, con dos límites: la ejecución tipo R1 (escalera con tope y cierres del cliente) no está examinada en vivo (B2), y la extensión `own_rule_be_partial_v1` tampoco (8d) |
| `research/dubai_iterative/oracle.py` | Tercer motor (referencia) | Pruebas de paridad en su día | Solo referencia; no se usa para decidir |
| `research/history_replay.py`, `history_search.py` | Reproducción histórica señal → broker | Validada: Dubai 28/28 y 18/18, Gold 35/37; reloj UTC+2/+3 confirmado por el NFP | **Usable** |
| `research/signal_features.py` (v1) | Rasgos causales v1 | **Fallo encontrado y corregido el 29/09**: usaba el tick que llega justo en t0 (1–2 % de señales, diferencias ≤ 0,11 $). Ahora con test de causalidad en ticks reales (`tests/test_signal_features_v1_causal.py`). R1 y R2 no cambian (294 cestas / +552,81 €; 140 / +570,78 €) | **Usable** (corregido) |
| `research/signal_features_v2.py` | Rasgos v2 | Test de causalidad sintético | **Usable** |
| `research/signal_paths.py`, `bracket_grid.py`, `basket_grid.py` | Calculadora | Tests; control contra el motor (simples 20/20; cestas con ±0,5 €/cesta) | **Usable como criba**, nunca para decidir |
| `research/walkforward.py`, `folds.py` | Juez honesto y PBO | Tests (ventaja plantada recuperada; ruido ≈ 0,5) | **Usable** |
| `research/account_sim.py`, `prop_sim.py`, `prop_rules.py` | Cuenta y reto | Tests | **Usable**; falta el día de FTMO en CE(S)T (D1) |
| **Buscador recursivo heredado** (`research/dubai_iterative/search.py`, `evolution.py`, `research/gold_iterative/search.py`) | El que encontró la 555: diagnóstico → mutación de una causa, por pliegues cronológicos | **Diseño correcto**: congela cada regla al descubrirla y solo la juzga con días posteriores (`cross_validate_frontier_candidates`). **Fallos de uso**: pliegues de 2 días de aprendizaje y 1 de prueba; por defecto basta 1 señal futura para validar; sin placebo; simulador anterior a la validación del 24–26/09; sin control de cuántas reglas se prueban | **Reutilizar sus operadores** (crítico y mutaciones) **dentro del juez nuevo**, y **no sus resultados**. Prueba pendiente para el bloque O: que recupere una estrategia escondida en datos sintéticos (L1) |
| La 555 y Dubai balanced | Estrategias en vivo | Walk-forward de la búsqueda 2: la 555 pierde (−548 €); con el filtro de impulso mejora | Siguen en vivo hasta que Jose decida; candidatas a sombra |
| `search/` (búsqueda 1) | Pipeline anterior | Superado por la búsqueda 2 | Solo como historial |
| `strategy_shadow_*` (sombra de la VM) | Sombra en el bot | Parada desde el 30/08; usa el simulador antiguo | **Rehacer** (bloque F) |
| Bot en vivo (`listener.py`, `executor.py`, `main.py`…) | Producción | Funciona; se conecta a un solo terminal MT5 | Sin tocar sin OK; cambios en el bloque F |

## Batería completa de tests (29/09)
**8.054 pasan y 63 fallan** (`out/bloque0_pytest_full.log`, 10 min).
- **Ninguno de los que fallan usa los módulos tocados en esta sesión** (`history_search`,
  `signal_features`, `signal_filters`, calculadora). Son fallos previos, que vienen de cambios del
  motor sin commit (`engine.py`, `fast_engine.py` modificados; `latency_model.py` y
  `execution_profile.py` nuevos, 24–26/09). El último commit es del 19/09.

| Grupo | Nº | Causa | ¿Afecta a nuestro trabajo? |
|---|---|---|---|
| `test_absolute_level_execution` | 30 | `ExecutionScenario(latency=…)` pasó a `latency_ms` | No: los modos de niveles publicados no se usan |
| `test_basket_guard_execution::old_profile…` | 1 | El motor rápido ya no bloquea `basket_money` sin la extensión (cambio del 26/09 para el tope de Gold); el oráculo sí | **Revisar**: test antiguo o regresión. Afecta a familias con tope de cesta |
| `test_iterative_provenance` | 1 | `latency_model.py` no entra en la huella de procedencia | Sí, para reproducibilidad: arreglar |
| forward y simulator_forward (`test_forward_window_contract`, `test_*_simulator_forward`, `test_compare_simulator_forward`) | ~20 | Mismo cambio de API | No, mientras se use `tools/shadow_week_replay.py` |
| `test_dubai_iterative_cli` | 1 | CLI del buscador recursivo heredado rota | Sí, si se reutiliza el buscador (L1) |
| `test_mt5_native_access_inventory`, `test_shared_dynamic_provider` | 2 | Inventario desactualizado; break-even del proveedor bloqueado por diseño | No |

**Acciones:**
- revisar el caso de `basket_guard` antes de juzgar familias con tope de cesta en V/O (fila B9 nueva);
- arreglar la procedencia;
- los demás se arreglan cuando se usen esas herramientas.
- **Riesgo operativo:** mucho trabajo sin commit. Conviene que Jose autorice un commit de guardado
  (sin push, para no desplegar) — pendiente de preguntarle.
