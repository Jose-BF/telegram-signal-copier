# Matriz De Capacidades De Simulacion Dubai Y Gold

## Alcance Y Criterio

Este inventario describe el arbol local de `feature/gold-555-live-trial` sobre
`fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd`, incluyendo los cambios locales
presentes el 09/09/2026. No certifica resultados historicos, no selecciona una
estrategia y no autoriza una busqueda. La gramatica compartida es
`StrategyGenome`; Gold reutiliza los motores Dubai y aporta semillas y un
adaptador de datos propios.

**Corte de las tablas originales:** inventario durante `independent_v1`. Las
referencias de linea y carencias de pruebas de esas tablas describen ese corte;
la actualizacion siguiente identifica las regresiones agregadas despues. La
admision broker permanece pendiente, aunque mejore la cobertura determinista.

## Actualizacion Del Primer Bloque

Evidencia de ese primer bloque en `../audits/2026-09-09-simulation-foundation-implementation.md`:
3.905 pruebas completas aprobadas y 270 ejecuciones fijas en `independent_v3`.
Las 90 comparaciones con MT5 conservan 89 discrepancias y un caso bloqueado.
Se mantiene la prohibicion de avanzar al punto 4 sin revision conjunta.

- `tests/test_iterative_capability_contracts.py`: 17 escenarios con resultados
  calculados independientemente, por los tres motores (51 pruebas). Anaden
  pullback BUY/SELL, momentum SELL, no-entry, basket/fixed stop y sus gaps,
  BE delayed con dos legs BUY/SELL, tiempo profit-only y desactivado, y filtros
  time-window/max-volatility/min-reward-risk. Estas ramas ya no dependen solo
  de aparicion en una poblacion; esto no les aporta evidencia de fill real.
- `tests/test_iterative_entry_boundaries.py`: 66 pruebas de delay, pullback y
  momentum, antes/en/despues de expiracion, ambos lados, preservacion legacy
  y cotizacion invalida. Schema 2 excluye la igualdad con expiracion.
- `tests/test_iterative_entry_fill_latency.py`: 52 pruebas de retraso de fill,
  referencia del ladder, cancelacion antes del request, carreras no modeladas,
  prefijos cerrados que no pueden borrarse por datos futuros y comparacion
  exacta del instante de solicitud. No modelan el acuse ni SL/TP del broker.
- `tests/test_broker_execution_model.py`: 31 pruebas de un kernel SL/TP aislado,
  incluida conservacion del nivel antiguo mientras otro esta pendiente y
  rechazo en su momento de procesamiento aunque la posicion ya se haya cerrado.
  No se cuenta como integracion del simulador ni como emulacion MT5 certificada.

El contrato nuevo de fill-delay se admite como hipotesis para entradas causales
schema 2 sin stop proveedor. No desplaza fills `actual_mt5`. La latencia de
observacion conserva su significado anterior. Los tiempos de ejecucion, acuse
y solicitudes posteriores necesitan un modelo integrado, no una tolerancia unica.

## Actualizacion De Protecciones

El corte siguiente esta en `../audits/2026-09-09-protection-execution-validation.md`:
4.041 pruebas y 135 evaluaciones fijas, sin busqueda ni promocion. Se integro un
perfil opt-in de SL/TP instalado, solicitud pendiente, rechazo atomico, acuse y
reintento en escalar/Numba/oracle; no se delega toda la validacion al kernel aislado.
Las tablas originales conservan su corte historico, no describen ese perfil nuevo.

El perfil tiene `D+I` y un `control` retrospectivo de protecciones Gold: con
aperturas MT5 fijadas coinciden 44/45 precios de salida; el episodio B1 2640 queda
a 6 ms frente a los 253.383 ms del baseline idealizado. No iguala todos los
tiempos, la cola real ni los rechazos (139 frente a 248 en esa senal con el
perfil 250/250/1000). Las entradas propias siguen dando 46 posiciones frente a 45.

Se bloquean cierres a mercado al activarse, freeze distinto de cero, politicas
no admitidas, aperturas iniciales sin evidencia y limites de traza/datos. El
SL monetario Dubai no esta integrado en este perfil. Los acuses de apertura,
cartera/margen y validacion nueva siguen pendientes. Ninguna fila se admite
automaticamente para la busqueda por estas coincidencias condicionadas.

## Estados Del Inventario

Los estados usados en las tablas son deliberadamente distintos:

- **Enumerado/implementado**: el contrato acepta el valor y existe una rama que
  lo interpreta. No implica que la rama haya sido activada por una prueba.
- **Determinista**: `D` significa una regresion con un resultado esperado de esa
  capacidad; `I` anade comparacion con el oracle escalar independiente; `P`
  significa solo que aparece en poblaciones comparadas entre motores, sin
  demostrar que la capacidad haya decidido el resultado; `C` cubre solo
  contrato, identidad o generacion.
- **Broker**: `hecho` conserva un hecho MT5 observado; `input` consume una cinta
  o metadato con contrato de procedencia; `control` contrasta un caso observado
  concreto. Ninguno de esos estados convierte un fill alternativo en observado.
- **No cubierto**: falta una prueba independiente, evidencia de broker o soporte
  funcional necesario para afirmar fidelidad dentro de esa variante.

Un test se clasifica por sus inputs y aserciones, no por su nombre. En
particular, `test_full_gold_basket_stop_preserves_all_losses_and_adverse_gaps`
afirma `trailing_stop`, no ejercita `stop_mode="basket_money"`
(`tests/test_iterative_timestamp_precision.py:121-147`).

## Frontera De Implementacion

| Pieza | Soportado actualmente | Evidencia determinista | Vinculo con broker | Limite para fidelidad real |
| --- | --- | --- | --- | --- |
| Contrato | `StrategyGenome` schema 1 y 2, `SearchSpace`, volumen, numero de legs y horizontes (`research/dubai_iterative/contracts.py:41-134`, `:137-312`) | Validacion, round-trip y fingerprints: `tests/test_dubai_iterative_contracts.py:12-184`, `tests/test_gold_iterative_contracts.py:26-115` | Ninguno | Un valor valido solo es sintacticamente admisible. Los campos reutilizan unidades distintas segun el modo. |
| Motor escalar | Estado por senal y tick; entradas, posiciones, salidas y dinero (`research/dubai_iterative/engine.py:107-725`) | Muchas regresiones dirigidas, detalladas abajo | `input` cuando el `SignalPath` procede del loader | No envia ni reproduce ordenes, confirmaciones o rechazos del broker. |
| Motor rapido | Kernel fixed-point para gestion y dinero (`research/dubai_iterative/fast_engine.py:347-478`, `:788-1360`) | Comparado con escalar y oracle | Ninguno directo | Reutiliza `_prepare_entries` del motor escalar (`fast_engine.py:29-38`, `:355-359`): entrada, filtro y ladder no son independientes entre esos dos motores. |
| Oracle | Segunda implementacion escalar de entradas, gestion y dinero, sin importar los motores (`research/dubai_iterative/oracle.py:1-18`, `:140-517`, `:587-1345`) | Compara todos los campos, entradas y salidas (`oracle.py:519-555`, `:1347-1384`) | Ninguno directo | Igualdad con el motor prueba coherencia bajo los mismos supuestos, no esos supuestos contra MT5. |
| Datos Dubai/Gold | Ticks Bid/Ask, revisiones causales, niveles, conversion y rollover; Gold crea templates independientes de tickets MT5 (`research/dubai_iterative/dataset.py:32-80`, `:228-315`; `research/gold_iterative/dataset.py:161-244`) | Loaders fail-closed y tests de catalogo/causalidad | `input` por hash, simbolo, reloj UTC y sidecar verificado | El engine aun exige al menos un leg-template, incluso para reglas sin niveles (`engine.py:1669-1692`). |
| Controles Gold observados | Replay de gestion condicionado a fills reales y auditoria por ticket (`research/gold_iterative/live_parity.py:33-50`, `:95-170`, `:264-324`) | Tres motores mas conciliacion de ledger | `hecho/control` | `_actual_fill_genome` sustituye la entrada por `actual_mt5`; conserva hechos suministrados, no predice entrada ni fill. |
| Replay continuo raw-only | Compila mensajes raw y mantiene entradas/estado propios sin usar resultados broker, fills reales ni snapshots live (`research/causal_replay.py:1-18`, `:44-139`, `:164-211`) | En el corte `independent_v1`, escalar, fast y oracle coinciden en 54 escenarios | `input` de mensajes/ticks; los deals entran solo en comparacion posterior | Es diagnostico retrospectivo, no OOS ni paridad: todas las comparaciones de hechos divergen. No cambia por si solo la admision de ninguna fila. |
| Barrera integral | El informe separa observado, retrospectivo y prospectivo | Regresiones fail-closed (`tests/test_gold_555_pipeline_truth.py:166-351`) | `control` | Siempre conserva `exit_decision_and_deal_sequence_not_verified` (`research/gold_iterative/pipeline_truth.py:149-190`). |

### Evidencia Raw-Only Al Corte

El run inmutable
`runtime_data/simulation_foundation_20260909/independent_v1/` conserva 525
mensajes raw, 18 senales y tres escenarios de observacion (0/250/1000 ms): 54
filas de escenario y 162 ejecuciones de motor. Sus listas de diferencias entre
escalar, fast y oracle estan vacias y `search_candidates=0`. El protocolo declara
estado `retrospective_not_certified_not_oos`, independencia de fills/estado live
y `full_live_parity_verified=false`.

La comparacion posterior es `diagnostic_only`: las 54 filas tienen status
`mismatch`, no `exact_facts_only`. En baseline produce 60 entradas frente a 59
observadas, con la entrada adicional en `canal2_2590`; las demas diferencias no
son reducibles en bloque a simple timing. Los artefactos
`protocol.json`, `independent_results.json` y `first_divergences.json` preservan
ademas como blockers la cinta live incompleta, ausencia de lifecycle de orden en
el tick engine, escenarios/tolerancias no calibrados, margen no simulado,
contrato hipotetico de cero comision/sin rollover y falta de validacion held-out.
El comparador mantiene sin verificar decision sequence, modify/reject/confirm y
realizabilidad de fills alternativos (`research/causal_comparison.py:38-87`).

Un kernel broker en construccion pero aun no integrado no se contabiliza como
capacidad del simulador compartido ni como evidencia de esta matriz.

## Entrada Y Filtros

`entry_value` esta en segundos para `delay`, en distancia de precio para
`pullback`, `momentum` y el tramo adverso de `adverse_reversal`.
`entry_confirmation_value` es distancia de precio. `entry_expiry_min` esta en
minutos y, en schema 2, se ancla a `entry_expiry_anchor_at` (Telegram enviado si
el loader lo conoce), no necesariamente a la recepcion local.

| Capacidad | Semantica actual e implementacion | Determinista | Broker | No cubierto / estado admisible |
| --- | --- | --- | --- | --- |
| `actual_mt5` | Programa cada fill desde `path.legs`; conserva precio, hora y ticket solo con supuestos de coste cero. Si cambia volumen/numero de legs, pasa a `counterfactual_entry`; puede truncar legs o crear extras en tiempos observados (`engine.py:727-851`; oracle `:587-701`). | `D+I`: familias Dubai, frontera exacta de microsegundos y rechazo de template Gold (`tests/test_dubai_iterative_oracle.py:253-294`; `tests/test_iterative_timestamp_precision.py:64-81`; `tests/test_gold_iterative_oracle.py:143-157`). | `hecho`, exclusivamente para los fills suministrados | Admitir solo como gestion de entrada fija cuando shape, volumen, ticket, hora y precio coinciden. No es una regla de entrada prospectiva. Slippage distinto de cero deja de representar el fill observado. |
| `signal_market` | Primer tick util en el lado ejecutable desde recepcion+latencia y antes de expiracion (`engine.py:1007-1030`; oracle `:858-883`). Abre todos los legs simultaneamente al mismo quote ajustado. | `D+I` en C490 y varios casos Gold (`tests/test_gold_iterative_engine.py:219-235`; `tests/test_gold_iterative_oracle.py:389-401`). | `input` de tick; no de fill | Falta orden enviada, tiempo de ida/vuelta, retcode, fill parcial, desviacion permitida y precio confirmado. Solo admisible como hipotesis offline. |
| `delay` | Primer tick en o despues de recepcion+latencia+`entry_value` **segundos**, limitado por expiracion (`engine.py:1068-1078`; oracle `:871-883`). | `D+I` para 90 s y cruce de rollover (`tests/test_gold_iterative_oracle.py:237-293`). | `input` | Falta regresion dirigida BUY/SELL para igualdad exacta con expiracion, huecos de ticks y sesiones; no hay validacion broker. |
| `pullback` | Primer Ask BUY menor/igual a referencia-distancia, o Bid SELL mayor/igual a referencia+distancia (`engine.py:1079-1088`; oracle `:884-944`). | `P`, no `D`: aparece en las poblaciones de `tests/test_dubai_iterative_oracle.py:586-680`; tests de refinement/search solo prueban generacion. | `input` | No hay caso independiente con precio/hora esperados, ambos lados, expiracion y gaps. No admitir como fiel al broker. |
| `momentum` | Primer Ask BUY mayor/igual a referencia+distancia, espejo en Bid SELL (`engine.py:1079-1088`). | `D+I` para BUY causal y cancelacion por SL proveedor (`tests/test_dubai_iterative_engine.py:214-236`, `:639-658`; `tests/test_dubai_iterative_oracle.py:367-391`). | `input` | Falta una regresion SELL dirigida y cualquier contraste de fill real de esta familia. |
| `adverse_reversal` | Arma tras movimiento adverso desde el primer quote util; actualiza el extremo y entra tras rebote de `entry_confirmation_value` (`engine.py:1032-1066`; oracle `:884-933`). | `D+I` BUY/SELL y expiracion schema 2 (`tests/test_gold_iterative_oracle.py:295-338`). | `control` solo para la regla 555 observada, separado del motor generico | El control de entry-watch mide outcome/tick y broker outcome, pero declara que no prueba fills (`research/gold_iterative/entry_watch_parity.py:24-35`, `:144-172`). No extrapolable a otros parametros. |
| `no_entry` | Resultado explicito sin entradas, exposicion cero y P/L cero (`engine.py:1023-1025`; oracle `:869-870`). | `D` escalar; `C` en seeds Gold, sin comparacion oracle dirigida (`tests/test_gold_iterative_engine.py:273-286`; `research/gold_iterative/seeds.py:46-56`). | Ninguno | Control negativo, no capacidad de ejecucion. |
| Expiracion | La frontera no es uniforme: schema 2 excluye el tick exacto para `signal_market`, `adverse_reversal` y ladders; `delay`, `pullback` y `momentum` lo incluyen. El ladder schema 1 tambien lo incluye (`engine.py:884-900`, `:1007-1088`). | `D+I` solo para la exclusion exacta de 555/adverse-reversal y su ancla Telegram (`tests/test_gold_iterative_oracle.py:310-338`). | `input` de marcas temporales | Falta fijar y probar una semantica por modo para tick anterior/igual/posterior, latencia, revision tardia y cierre de mercado. La inconsistencia actual impide una promesa generica de expiracion. |
| Slippage/spread/latencia | Ajustes adversos constantes; latencia desplaza inicio de entrada y disponibilidad de artefactos proveedor (`engine.py:21-38`, `:1626-1653`). | `D+I` en costes y latencia (`tests/test_dubai_iterative_oracle.py:393-584`; `tests/test_iterative_timestamp_precision.py:84-104`). | `input/control` solo para muestras observadas | Son escenarios fijos, no distribuciones calibradas por operacion, volumen, hora, volatilidad o retcode. `spread_addition` se suma aun usando Bid/Ask y debe justificarse para no duplicar coste. |
| `max_spread` | Rechaza si Ask-Bid supera `context_filter_value` (`engine.py:1096-1109`; oracle `:946-960`). | `D+I` sobre entrada observada (`tests/test_dubai_iterative_engine.py:554-570`; `tests/test_dubai_iterative_oracle.py:353-365`). | `input` | No existe contraste de decision contra bot/broker para esta regla. |
| `time_window` | Acepta solo si hora UTC decimal `<= context_filter_value`; no hay hora inicial, intervalo ni zona configurable (`engine.py:1110-1113`). | `P`, sin regresion dirigida | `input` de reloj | El nombre sugiere una ventana, pero implementa un unico corte superior UTC. No admitir variantes que requieran ventana real, rollover o sesion del broker. |
| `max_volatility` | Rango high-low de midpoint en los cinco minutos previos sobre los ticks disponibles (`engine.py:1114-1119`). | `P`, sin regresion dirigida | `input` | No fija muestreo minimo, cobertura continua ni tratamiento independiente de gaps; no esta validado contra una medida broker. |
| `min_reward_risk` | Usa el primer leg, ultimo TP/SL proveedor causal y quote de entrada; sin ambos niveles rechaza (`engine.py:1120-1138`). | `P`, sin regresion dirigida | `input` de niveles | Falta probar revisiones, latencia, BUY/SELL, multiples targets y nivel invalidado. No admite una definicion R:R sin niveles proveedor. |

## Ladder, Volumen Y Pending

| Capacidad | Semantica actual e implementacion | Determinista | Broker | No cubierto / estado admisible |
| --- | --- | --- | --- | --- |
| `simultaneous` | Todos los legs usan el mismo tick y precio; `volume_weights` asigna volumen por leg (`engine.py:807-851`). | `D+I` en Dubai y Gold | `hecho` solo si `actual_mt5` exacto; si no, `input` | No modela ordenes separadas, fills parciales ni diferencias de precio/latencia entre legs. |
| `adverse` | Primer leg en la entrada base; leg `n` al primer quote que alcanza `n * entry_ladder_step` en contra. Schema 2 mide desde el fill ajustado del primer leg (`engine.py:854-1004`). | `D+I`, incluido basket temporalmente plano y cancelacion por SL proveedor (`tests/test_dubai_iterative_engine.py:190-260`; `tests/test_gold_iterative_oracle.py:340-362`). | `input`; control 555 limitado | Los fills se preprograman desde la cinta y se consideran ejecutados sin solicitud/respuesta del broker. |
| `favourable` | Igual que ladder adverso con signo favorable, es decir, piramidacion (`engine.py:949-1004`; oracle `:795-854`). | `P`, no `D`; contrato/generadores si la enumeran | `input` | Falta regresion independiente con BUY/SELL, gaps, expiracion, prioridad con TP/SL y fill real. No admitir como capacidad verificada. |
| Volumen/legs | Pesos positivos; `SearchSpace` limita total, maximo de 12 por defecto y step de 0.01 (`contracts.py:41-134`). Fast usa `VOLUME_SCALE=100` (`fast_engine.py:39-43`). | `D` para volumen superior al observado y limites; `I` en poblaciones (`tests/test_dubai_iterative_engine.py:572-587`; `tests/test_dubai_iterative_contracts.py:61-127`). | `hecho` solo para volumen observado; metadato broker no decide admision | No hay min/max/step por simbolo, volumen disponible, margen ni rechazo por exposicion. La configuracion de busqueda no sustituye `SYMBOL_VOLUME_*`. |
| `pending_entry_policy="none"` | Al quedar plano, finaliza aunque existan legs ya programados (`engine.py:601-612`, `:1568-1577`). | `C`; no hay regresion de comportamiento que afirme la cancelacion del siguiente leg | Ninguno generico | No cancela una orden broker porque el motor no tiene orden pendiente. Falta caso independiente antes/igual/despues de flat. |
| `pending_entry_policy="until_expiry"` | Si queda plano, sigue recorriendo la cinta hasta los legs programados restantes (`engine.py:601-612`, `:1568-1577`). | `D+I` para segunda entrada despues de flat (`tests/test_gold_iterative_engine.py:146-194`; `tests/test_gold_iterative_oracle.py:340-362`). | `control` 555, no generico | Es continuidad de un schedule calculado, no persistencia/recreacion de una pending order confirmada por broker. |

## Targets, Parciales Y Proteccion

`target_value` significa EUR/account-currency para `fixed_basket` y el primer
umbral de `partial_runner`, distancia de precio para `fixed_move`, y ordinal de
target para `provider_target_all`. `stop_value` significa distancia de precio
en `fixed_move` y dinero en `basket_money`. El tipo no codifica estas unidades.

| Capacidad | Semantica actual e implementacion | Determinista | Broker | No cubierto / estado admisible |
| --- | --- | --- | --- | --- |
| `provider_per_leg` | Cada leg cierra completo en su ultimo TP `confirmed`/`snapshot` visible; el precio simulado es el limite, no el overshoot (`engine.py:422-454`, `:1210-1228`). | `D+I`, incluidos visibilidad tardia y limites (`tests/test_dubai_iterative_engine.py:262-305`, `:454-478`; `tests/test_gold_iterative_oracle.py:516-537`). | `input` de revision proveedor | No prueba que el TP se instalara o aceptara en MT5. Legs extra reutilizan el ultimo template, una suposicion no contrastada. |
| `provider_target_all` | Reune targets vigentes unicos, ordena por direccion, redondea el ordinal y lo limita silenciosamente al ultimo disponible (`engine.py:485-512`, `:1276-1300`). | `P`, sin `D` dirigida | `input` | Faltan ordinal fraccionario/fuera de rango, revisiones, targets duplicados, BUY/SELL y confirmacion broker. |
| `fixed_basket` | Cierra toda la cesta cuando P/L realizado+flotante redondeado a centimos alcanza `target_value` (`engine.py:513-533`). | `D+I` BUY/SELL y frontera monetaria (`tests/test_dubai_iterative_engine.py:121-140`; `tests/test_dubai_iterative_oracle.py:253-336`). | `input` de dinero | Trigger observado por tick, sin tiempo de envio/fill ni impacto de volumen. |
| `fixed_move` target | Cierra toda la cesta cuando el exit quote supera una distancia desde la entrada media ponderada por volumen (`engine.py:534-552`, `:1517-1536`). | `D+I`, incluida frontera Decimal (`tests/test_dubai_iterative_engine.py:142-188`; `tests/test_dubai_iterative_oracle.py:297-322`). | `input` | No hay orden limit real ni gap/partial fill. |
| `partial_runner` | Una vez, cierra la misma fraccion de cada leg activo al umbral monetario; cierra el resto al segundo umbral, incluyendo P/L ya realizado (`engine.py:553-600`). | `D+I` para un leg 0.02/50%; el contrato prueba ejecutabilidad de step (`tests/test_dubai_iterative_engine.py:362-381`; `tests/test_dubai_iterative_contracts.py:82-93`). | `input` | Solo un parcial por ticket (`MAX_EXITS_PER_POSITION=2`). No hay parciales distintos por leg, multiples escalones, volumen realmente aceptado ni close parcial del proveedor. |
| `per_leg_steps` | Target de distancia distinto por indice; cada leg cierra completo al limite (`engine.py:455-484`). | `D+I` Gold al precio limite (`tests/test_gold_iterative_oracle.py:516-537`) | `input` | Falta matriz multi-leg con cruces simultaneos, gaps, todos los steps y respuesta broker. |
| `none` target | Desactiva targets propios | `C/P` | Ninguno | Requiere otra finalizacion; de lo contrario acaba en `data_end` bloqueado. |
| BE `price` | Mueve SL al precio de cada entrada al alcanzar movimiento favorable (`engine.py:1142-1160`). | `D+I` y prioridad frente a giveback (`tests/test_dubai_iterative_engine.py:307-318`; `tests/test_gold_iterative_oracle.py:539-554`). | `input` | Es nivel instantaneo interno; no modela modify/confirmacion/rechazo. |
| BE `delayed` | Mueve SL a entrada tras `be_trigger` **minutos** desde apertura de cada leg (`engine.py:1149-1154`). | `P`, sin `D` dirigida | `input` | Faltan borde temporal, legs abiertos en momentos distintos y ciclo de modificacion broker. |
| BE `partial` | Solo legs cuyo `role != "market_a"` pasan a BE tras distancia favorable (`engine.py:1154-1160`). | `D+I` en SELL (`tests/test_dubai_iterative_engine.py:320-344`; `tests/test_dubai_iterative_oracle.py:184-211`) | `input` | El nombre no significa cierre parcial. Depende de roles de template, no de una fraccion parametrizada. Falta BUY y mapeo de roles Gold/Dubai. |
| BE `provider` | `MOVE_SL_TO_BE` usa cada entrada simulada; `MOVE_SL_TO_PRICE` extrae un nivel anunciado (`engine.py:348-350`, `:1302-1339`). | `D+I` para BE y latencia; `D` escalar, sin oracle dirigido, para precio anunciado (`tests/test_dubai_iterative_engine.py:589-637`; `tests/test_dubai_iterative_oracle.py:438-549`). | `input/control` | Disponibilidad del mensaje no demuestra instalacion del SL; no hay estado requested/accepted/rejected. Falta la regresion independiente de `MOVE_SL_TO_PRICE`. |
| Stop `provider` | Ultimo SL confirmado/snapshot visible, excluyendo BE salvo `be_mode=provider`; invalida entradas tardias si el stop ya fue tocado (`engine.py:1162-1208`, `:1230-1274`). | `D+I` para prioridad, latencia y entrada cancelada (`tests/test_dubai_iterative_engine.py:214-305`, `:480-504`; `tests/test_gold_iterative_oracle.py:581-599`). | `input` de niveles | No reproduce solicitud de SL, stops level/freeze, rechazo, retry ni instante de instalacion. Este es un hueco directo frente a las divergencias observadas. |
| Stop `fixed_move` | Nivel fijo adverso desde la entrada de cada leg (`engine.py:1177-1197`). | `P`, sin `D` de salida; las pruebas de risk solo evaluan la envolvente | `input` | Falta prueba independiente BUY/SELL, gaps, interaccion con BE/trailing y ejecucion broker. No admitir como verificado. |
| Stop `basket_money` | Primera prioridad del tick; cierra toda la cesta cuando P/L total `<= -stop_value` (`engine.py:278-302`). | `P`, sin regresion dirigida del motor/oracle que afirme `basket_stop` | `input` | Falta frontera exacta, gap, conversion stale, parciales y contraste broker. El test con "basket_stop" en el nombre prueba trailing. |
| `hard_stop_eur_per_leg` | Cap monetario independiente, evaluado despues de basket stop y antes de proveedor (`engine.py:303-342`). | `D+I` C490 (`tests/test_gold_iterative_oracle.py:389-401`) | `input` | En realidad usa la moneda y digitos de `path`, aunque el campo dice EUR. No es stop server-side y el gate de capital no lo reconoce como unica cota si `stop_mode=none`. |
| `trailing_distance` | Stop por leg, inicial desde entrada; se ajusta monotonicamente con el exit quote al final del tick y solo queda efectivo en el tick siguiente (`engine.py:665-674`, `:1538-1566`). | `D+I`, monotonia y gap adverso (`tests/test_gold_iterative_oracle.py:364-387`; `tests/test_iterative_timestamp_precision.py:121-147`) | `input` | No modela frecuencia del monitor, modify enviado/aceptado/rechazado, freeze level ni stops level. |
| `profit_lock_arm/giveback` | Arma por maximo P/L total y cierra cesta tras giveback monetario (`engine.py:614-642`). | `D+I`, incluida frontera exacta Gold (`tests/test_dubai_iterative_engine.py:346-360`; `tests/test_gold_iterative_oracle.py:403-427`) | `input` | El maximo se observa solo en ticks y la orden de cierre es instantanea; no hay ejecucion real ni distribucion de retraso. |

## Proveedor, Tiempo Y Finalizacion

| Capacidad | Semantica actual e implementacion | Determinista | Broker | No cubierto / estado admisible |
| --- | --- | --- | --- | --- |
| `provider_management_mode="exact"` | Cierra toda la cesta ante cualquier action que contenga `CLOSE`, ademas de `EXIT/CERRAR` (`provider_action_semantics.py:15-32`). | `D+I` para `CLOSE_ALL` y orden causal; `D` escalar para el legacy `CLOSE_FIRST` (`tests/test_dubai_iterative_engine.py:397-452`; `tests/test_iterative_timestamp_precision.py:64-118`) | `input/control` | El nombre `exact` no significa reproduccion exacta del proveedor: `CLOSE_FIRST` o `CLOSE_PARTIAL` se convierten en cierre total. Falta oracle dirigido para el vocabulario amplio. |
| `close_only` | En el motor compartido tiene la misma funcion de cierre amplio que `exact`; BE proveedor depende de `be_mode`, no de este valor (`engine.py:348-379`; `provider_action_semantics.py:28-31`). | `C/P`, sin regresion dirigida que lo distinga | `input` | Es una variante enumerada sin semantica diferenciada en este motor. No prometer cobertura separada. |
| `explicit_close_only` | Solo `CLOSE_ALL`, `EXIT` y `CERRAR` cierran toda la cesta (`provider_action_semantics.py:6-30`). | `D+I` para cierre total e ignorar `CLOSE_PARTIAL` (`tests/test_gold_iterative_oracle.py:456-514`) | `input/control` 555 | Ignorar un parcial no equivale a ejecutarlo. |
| `ignore` | No ejecuta cierres proveedor; las protecciones proveedor aun pueden actuar si `be_mode=provider` | `C/P`; aparece en reglas y poblaciones, sin asercion dirigida de esa interaccion | Ninguno | Falta prueba dirigida de `ignore + close` y `ignore + be_mode=provider`, que puede sorprender al interpretar el nombre. |
| Parcial proveedor | No existe una rama que reduzca volumen segun una instruccion del proveedor | Prueba explicita solo demuestra que `explicit_close_only` lo ignora | Ninguno | **No soportado**. Con `exact/close_only` puede convertirse en cierre total; con `explicit_close_only` se ignora. Variantes que lo requieran quedan fuera. |
| Tiempo schema 1 | Al cumplirse `time_exit_min` desde la primera apertura, cierra incondicionalmente; `time_exit_mode` no forma parte del payload v1 (`engine.py:644-663`, `:1579-1595`). | `D+I` para tiempo transcurrido (`tests/test_dubai_iterative_engine.py:383-395`) | `input` | No reproduce scheduler/cola de cierre. |
| Tiempo schema 2 `loss_only` | Cierra si P/L total `<= 0` al/tras horizonte | `D+I` Gold (`tests/test_gold_iterative_oracle.py:556-579`) | `input` | Falta borde con conversion stale, parciales y leg tardio. |
| Tiempo schema 2 `non_negative` | Cierra si P/L total `>= 0` | `D+I` Gold (`tests/test_gold_iterative_oracle.py:429-454`) | `input` | Mismos huecos de ejecucion real. |
| Tiempo schema 2 `profit_only` | Cierra solo si P/L total `> 0` | Enumerado e implementado (`engine.py:1588-1593`) | Ninguna prueba dirigida ni poblacion Gold actual que lo active | No admitir hasta disponer de caso independiente en cero/positivo/negativo y conversion no exacta. |
| Tiempo schema 2 `none` | Desactiva cierre temporal aunque `time_exit_min` siga siendo obligatorio | Enumerado; sin regresion dirigida que llegue vivo al horizonte y demuestre que no cierra | Ninguno | Semantica no evidente por el campo obligatorio; requiere prueba de borde y finalizacion posterior. |
| Prioridad intratick | Basket stop, hard stop por leg, close proveedor, SL/BE, targets, profit lock, time exit y finalmente actualizacion de trailing (`engine.py:278-674`) | `D+I` para algunas colisiones: SL antes de TP y BE antes de giveback (`tests/test_dubai_iterative_engine.py:284-305`; `tests/test_gold_iterative_oracle.py:539-599`) | Ninguno | Falta matriz de colisiones por pares y triples, entradas/rollover en el mismo timestamp, parciales y provider close frente a hard stop. |
| `unfilled` | Una entrada causal que no dispara conserva la senal con cero exposicion y P/L cero (`engine.py:129-151`, `:1705-1731`) | `D+I` para expiracion 555; `D` para momentum sin trigger (`tests/test_gold_iterative_oracle.py:310-338`; `tests/test_dubai_iterative_engine.py:534-552`) | `control` de outcome solo para 555 | No distingue no intento, intento rechazado, expirado en broker o falta de mercado salvo evidencia externa. |
| `data_end` | Fuerza cierre al ultimo quote util para materializar la traza y agrega `path_ended_before_strategy_exit`; el resultado no es evidencia completa (`engine.py:676-694`) | `D+I` (`tests/test_iterative_timestamp_precision.py:150-156`) | `input` | No es una salida ejecutable ni debe contarse como resultado valido. |
| Reentrada | Solo puede aparecer un leg que ya estaba preprogramado por el ladder despues de quedar temporalmente plano | `D+I` para `until_expiry` | Ninguno | **No soporta** una nueva decision de reentrada, multiples ciclos, cooldown, nuevo basket o rearmado tras cierre final. |

## Dinero, Riesgo Y Portfolio

| Capacidad | Soportado actualmente | Determinista | Broker | No cubierto / estado admisible |
| --- | --- | --- | --- | --- |
| P/L de precio | `(exit-entry) * direccion * contract_size * volume`, con redondeo por posicion a digitos de cuenta (`engine.py:1489-1514`; oracle `:1273-1289`) | `D+I` en BUY/SELL, fronteras y centimo discrepante (`tests/test_dubai_iterative_engine.py:121-188`; `tests/test_dubai_iterative_oracle.py:297-350`) | `input` de contrato | Los nombres `pnl_eur`/`*_eur` asumen EUR, pero el calculo usa la moneda de la cuenta sin exigir que sea EUR. |
| Conversion | `identity`, cuenta-base/profit-quote y profit-base/cuenta-quote; usa lado FX distinto para ganancia/perdida y bloquea stale (`engine.py:1489-1514`) | `D` en motor y `broker_money`; oracle bloquea evidencia incompleta (`tests/test_dubai_iterative_engine.py:660-675`; `tests/test_broker_money.py:1366-1522`) | `input` causal verificado | Fast y portfolio fijan FX a 5 decimales; no hay dominio declarado para otra precision. |
| Comision/fees | Loader solo acepta `observed_zero_intraday` para ambas (`dataset.py:253-289`) | Validacion fail-closed del contrato | `input` de la afirmacion observada | **No soporta importes no cero**, tiers, minimo por orden, comision por lado ni fees. Toda variante/horizonte donde no sea cero queda fuera. |
| Swap/rollover | Loader usa contrato intraday o lookup por rollover y unidad de 0.01; motores cargan swap antes de decisiones y lo prorratean en parcial (`dataset.py:787-874`; `engine.py:167-214`, `:1396-1486`) | `D+I` para coste, desconocido y apertura posterior (`tests/test_gold_iterative_oracle.py:159-293`); `broker_money` prueba snapshots/rollovers (`tests/test_broker_money.py:859-1355`) | `input` broker por snapshots, reloj y conversion | Exactitud overnight exige brackets validos; lookup actual llega a 1.00 lot por defecto. No cubre cambio no observado de especificacion. |
| Precision ejecutable | Fast exige precio 0.01, FX 0.00001, volumen 0.01 y moneda de 2 digitos (`fast_engine.py:39-43`, `:252-345`, `:656-705`) | Bloqueo `fast_path_unsupported` | Indirecto | Un simbolo con 3 digitos, volumen 0.001, cuenta con otros digitos o total superior al lookup no pertenece al dominio certificado actual. |
| Riesgo por capital | Evalua `basket_money`, `fixed_move` o envolvente de SL proveedor, escala por concurrencia configurada/observada y declara mercado continuo (`risk.py:84-205`) | Regresiones de volumen, concurrencia y fail-closed (`tests/test_dubai_iterative_risk.py:65-423`) | `input` de contrato y paths | No incluye gap, margen ni stop-out. Si `stop_mode=none`, marca no acotado aunque exista solo `hard_stop_eur_per_leg` o trailing; si hay varias protecciones no toma necesariamente la menor perdida. |
| Portfolio | Reconstruye equity conjunta, minimo, peak, drawdown, volumen y senales concurrentes sobre una cinta canonica (`portfolio.py:80-237`) | Regresiones de overlap, identidad temporal, cinta y conversion (`tests/test_dubai_iterative_portfolio.py:89-404`) | `input` de mercado/conversion | No modela saldo inicial, margen libre, leverage, stop-out, netting/hedging, limites de orden ni rechazo por capital. |
| Portfolio con swap | Los resultados del motor incluyen swap dentro de cada salida; `_result_slices` recalcula esa salida solo desde precio y exige igualdad (`engine.py:1396-1413`; `portfolio.py:432-477`) | No hay regresion de portfolio con swap no cero | Ninguno integral | **Hueco concreto**: un overnight con swap no cero queda bloqueado como `exit_money_mismatch`; portfolio y motor no estan integrados para ese dominio. |
| Mundos de ejecucion | Certificacion puede recalcular escenarios y exigir oracle+portfolio completos (`certification.py:108-191`, `:311-416`) | Regresiones de mundos/cache/portfolio (`tests/test_dubai_iterative_certification.py:439-625`) | Ninguno por si mismo | Los mundos son supuestos fijos. No estan calibrados ni validados como distribucion de ejecucion broker. |

## Alcance De Los Generadores Actuales

- Dubai genera schema 1. Su seed/scout alcanza `actual_mt5`, `delay`,
  `pullback`, `momentum`, los tres ladders, seis targets legacy, cinco BE,
  cuatro stops, tres modos proveedor y cinco filtros
  (`research/dubai_iterative/evolution.py:780-1053`). No genera las capacidades
  schema 2 (`adverse_reversal`, `no_entry`, `per_leg_steps`, trailing por precio,
  hard stop por leg, tiempo condicional o pending persistente).
- Gold genera schema 2. Sus seeds cubren familias seleccionadas, incluidos 555
  y C490 (`research/gold_iterative/seeds.py:28-185`); el vecindario puede anadir
  el resto de la gramatica generica y los campos v2, pero filtra `actual_mt5`
  para provider-first (`seeds.py:13-25`; `research/dubai_iterative/refinement.py:13-269`).
- La comparacion de poblaciones de 159/256 genomas en un unico path sintetico
  (`tests/test_dubai_iterative_oracle.py:586-680`) demuestra igualdad de trazas
  para esos inputs. No prueba que cada rama haya sido decisiva, todos los bordes,
  todas las interacciones o ningun comportamiento real del broker.
- Una capacidad enumerada pero sin prueba `D+I` no debe entrar en el dominio
  admitido de una busqueda de alta fidelidad solo porque el generador pueda
  producirla.

## Dominio Que Si Puede Afirmarse Hoy

1. Hay una gramatica compartida y ejecutable para simulacion offline causal por
   tick, con un oracle separado y salidas fail-closed cuando faltan inputs.
2. Para las filas marcadas `D+I` se puede afirmar coherencia determinista entre
   la semantica documentada y dos implementaciones bajo el mismo `SignalPath` y
   los mismos supuestos. No se puede afirmar fidelidad de fill real.
3. El modo `actual_mt5` y los controles Gold ligan tickets, fills, deals y dinero
   observados, pero estan condicionados a esos hechos. No validan entradas o
   salidas alternativas.
4. Los contratos de tick, conversion y rollover pueden ligar inputs al broker.
   El motor de ejecucion no esta ligado aun a una secuencia completa de
   request/result/deal ni a sus restricciones.
5. El replay raw-only ya elimina la inyeccion de fills y estado live para 18
   senales, pero su acuerdo interno convive con 54/54 divergencias de hechos y
   con una entrada baseline adicional. Es evidencia de localizacion del problema,
   no de fidelidad conseguida.

Por tanto, la matriz queda definida, pero **ninguna combinacion arbitraria de la
gramatica esta admitida todavia como simulacion fiel a MT5**. La admision debe
ser por capacidad, interaccion, dataset y horizonte. Una variante que use una
fila marcada como no cubierta queda fuera hasta cerrar su prueba y sus datos;
no hereda cobertura de Gold 555, C490, Dubai ni de un resultado rentable.

## Huecos Prioritarios Y Pruebas Independientes Faltantes

1. **Cerrar las divergencias end-to-end ya expuestas.** El replay continuo desde
   mensajes y Bid/Ask ya mantiene estado propio y registra primera divergencia;
   ahora debe explicar y reparar las 54 diferencias de hechos, empezando por la
   entrada adicional `canal2_2590`, y ampliar la comparacion a requests, results,
   niveles instalados, retries y deals. Mantener el blocker
   `exit_decision_and_deal_sequence_not_verified` hasta cerrar esa evidencia.
2. **Modelo de orden y modificacion.** Casos observados y luego held-out para
   market entry, cierre, TP/SL/trailing: aceptado, rechazado, retcode, retry,
   fill parcial, slippage, latencia, stops/freeze level, sesion y volumen. Un
   unico `latency_ms` y slippage constante no bastan.
3. **Entradas no dirigidas.** Regresiones `D+I` con resultado esperado para
   `pullback`, ladder `favourable`, cada frontera de expiracion por modo y
   BUY/SELL; para
   cada una, no-fill, gap, tick invalido y SL proveedor anterior al fill.
4. **Ramas enumeradas sin activacion probada.** Casos `D+I` para
   `provider_target_all`, stop `fixed_move`, stop `basket_money`, BE `delayed`,
   `time_exit_mode=profit_only/none`, pending `none`, `close_only` y filtros
   `time_window/max_volatility/min_reward_risk`.
5. **Interacciones y prioridad.** Tabla adversarial por pares/triples en el
   mismo tick: rollover/entry, hard/basket/provider stop, provider close, BE,
   target/parcial, profit lock, time exit, trailing y pending leg. Afirmar tanto
   orden de eventos como dinero, volumen y estado final en scalar y oracle.
6. **Parciales y proveedor.** Decidir si se excluyen o se modelan parciales de
   proveedor, multiples parciales, parciales por leg y volumen confirmado. Hasta
   entonces no admitir estrategias que dependan de ellos.
7. **Dinero y portfolio.** Integrar swap realizado en la reconstruccion de
   portfolio; probar no cero, parcial y multiples rollovers. Mantener fuera
   comision/fees no cero. Incorporar margin/free-margin/stop-out y limites de
   volumen antes de cualquier afirmacion de cartera ejecutable.
8. **Unidades y precisiones.** Hacer explicito en contrato/reporte que `delay`
   son segundos, BE delayed son minutos y los campos target/stop/context cambian
   unidad por modo. Admitir solo las precisiones 0.01 precio/volumen, 0.00001 FX
   y dos digitos de cuenta hasta probar otras.
9. **Finalizacion/reentrada.** Probar cancelacion pending `none`, leg tardio
   despues de primera apertura, flat temporal cerca del time exit y `data_end`.
   Reentrada real, cooldown y multiples ciclos permanecen fuera de gramatica.
10. **Held-out por capacidad.** Tras reparar o calibrar, congelar supuestos y
    tolerancias y repetir cada capacidad admitida sobre sesiones no usadas para
    la reparacion. El acuerdo de motores y los controles condicionados no
    sustituyen esta fase.

## Referencias Principales

- Contrato y espacio: `research/dubai_iterative/contracts.py`.
- Semantica escalar: `research/dubai_iterative/engine.py`.
- Kernel de busqueda: `research/dubai_iterative/fast_engine.py`.
- Oracle independiente: `research/dubai_iterative/oracle.py`.
- Datos y dinero: `research/dubai_iterative/dataset.py`, `broker_money.py`,
  `research/gold_iterative/dataset.py`.
- Cartera y riesgo: `research/dubai_iterative/portfolio.py`,
  `research/dubai_iterative/risk.py`, `research/dubai_iterative/certification.py`.
- Controles observados: `research/gold_iterative/entry_watch_parity.py`,
  `research/gold_iterative/live_parity.py`,
  `research/gold_iterative/pipeline_truth.py`.
- Replay raw-only y comparacion: `research/causal_replay.py`,
  `research/causal_comparison.py`, `tools/run_causal_controls.py`,
  `tools/compare_causal_controls.py` y
  `runtime_data/simulation_foundation_20260909/independent_v1/`.
- Regresiones nucleares: `tests/test_dubai_iterative_engine.py`,
  `tests/test_dubai_iterative_oracle.py`, `tests/test_gold_iterative_engine.py`,
  `tests/test_gold_iterative_oracle.py`,
  `tests/test_iterative_timestamp_precision.py`,
  `tests/test_dubai_iterative_portfolio.py`,
  `tests/test_dubai_iterative_risk.py` y
  `tests/test_dubai_iterative_certification.py`.
