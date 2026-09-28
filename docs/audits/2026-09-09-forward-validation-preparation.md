# Preparacion De La Primera Cohorte Futura

Alcance local: preparar la fase 3 del simulador antes de las 08:30 Madrid del
09/09. El motor y sus puertos siguen a cargo de la tarea principal. Este arnes
no ejecuta colectores, busquedas, ordenes ni cambios en la VM. Ejecuta controles
locales con los tres motores existentes, sin resultados observados como inputs.
No ingiere exportaciones antiguas ni selecciona estrategias.

La cohorte queda fijada a **2026-09-09 06:30 <= hora UTC < 08:30**, equivalentes
a 08:30-10:30 Madrid. Preparar antes de las 08:30 Madrid es distinto de tener
dos horas de evidencia observada a esa hora. Se requieren al menos dos senales
naturales y, para revisar operaciones completas, dos trades terminados. La
ventana no se prolonga para conseguir casos; ausencias, operaciones abiertas,
limitaciones de motor y contradicciones quedan incompletas o bloqueadas.

## API Del Arnes

`tools/prepare_simulator_forward.py` expone:

- `ready --profile <perfil_final.json>`: lectura local. Comprueba contratos
  de ejecucion de escalar/oracle, capacidades declaradas por el padre y prueba
  del codigo actual. No congela ni demuestra por si mismo esas capacidades.
- `freeze --profile <perfil_final.json> --out <directorio_nuevo>`: publica
  `protocol.json` exclusivo, solamente con readiness sin bloqueos y antes de
  06:30 UTC. Un freeze tardio no se convierte en validacion futura.
- `check --protocol <protocol.json> --dataset <directorio> --stage inputs`:
  valida hashes, productor congelado, mensajes, Bid/Ask, reloj, fechas y todos
  los IDs elegibles. Solo consume inputs independientes y sus pruebas.
- `check ... --stage independent --results <resultados.json>`: exige registros
  completos de los tres motores, compara todos sus campos salvo behavior_digest,
  conserva sus bloqueos y comprueba el denominador entero. Antes de leer el
  resultado presentado, vuelve a ejecutar el control causal de los tres motores.
  JSON coincidentes pero distintos de esa ejecucion local quedan bloqueados.
- `check ... --stage observed --results <resultados.json> --comparison <comparacion.json>`:
  exige la comparacion posterior y pruebas identificadas de solicitud,
  aceptacion/rechazo, estado observado de proteccion y deals nativos. Reejecuta primero
  el control independiente; si este discrepa o tiene bloqueos, no lee la
  comparacion observada. Diferencias simulacion/MT5 quedan para revision, no
  se impone igualdad milimetrica como criterio general de correccion del motor.

El comando de produccion/ejecucion es `tools/run_simulator_forward.py`, propiedad
de la tarea de integracion de Harvey. Su hook de ejecucion es
`prepare_simulator_forward.run(protocol_path, dataset_path, out=directorio_nuevo)`.
Este hook ejecuta `_simulate`, usando compile_signals, make_path, simulate,
FastEvaluator y oracle_simulate existentes, y escribe `independent_results.json`
exclusivo. El CLI de preparacion no duplica el comando run del sidecar.

Un resultado positivo significa `evidence_ready_for_review`, nunca fidelidad
MT5 certificada. `full_live_parity_verified` y `mass_search_authorized` siguen
false. `capability_scope=local_bounded_declared_profile` limita toda admision al
perfil local declarado. `quantitative_agreement_admitted=false` porque las
tolerancias aprobadas siguen null. Los comandos imprimen JSON; los estados blocked/incomplete devuelven
codigo 2. `check` no modifica resultados. Las comprobaciones de hashes y codigo
se repiten al terminar; los limites son explicitos en el protocolo.

`engine_state=engine_incomplete` conserva el diagnostico mientras falten el
perfil de mercado, capacidades admitidas o verificacion final. No permite
freeze. `admitted_for_bounded_control` solo identifica la admision local de ese
perfil declarado y probado; no sustituye la revision final del motor ni afirma
fidelidad real. Un plazo de freeze vencido sigue bloqueado aunque el motor
este admitido. La hora se comprueba tambien tras verificar los hashes finales.

Presupuesto: cero candidatas, un Gold 555 fijo con profit lock conservado,
maximo 11 senales, 132 evaluaciones como techo, 600 segundos por comprobacion,
50.000 mensajes, dos millones de filas por cinta y 16 MB por archivo JSON.
Un solo perfil final implica tres evaluaciones por senal. Las senales Dubai
permanecen excluidas con motivo `unsupported_protection_profile`.

## Perfil Que Debe Aportar El Padre

Contrato `simulator_forward_profile_v1`, con estos campos:

- `execution`: argumentos del ExecutionAssumptions final. `protection` es el
  diccionario de ProtectionProfile, sin protecciones de aperturas reales.
  `market` es obligatorio para este control y contiene MarketProfile:
  entry_acknowledgement_delay_ms, close_processing_delay_ms,
  close_acknowledgement_delay_ms, volume_min, volume_max y volume_step.
  Los valores son hipotesis explicitas de cliente serial y reloj de quotes,
  no parametros ajustados a fills observados. Se conservan los defaults
  validados de max_events y name cuando no se indican.
  Se valida tambien mediante ExecutionScenario. Punto 0.01, digits 2,
  stops 20 y freeze 0; retrasos elegidos antes de las nuevas observaciones.
- `capabilities`: estados de `market_open_request_fill_ack`,
  `sltp_request_accept_install`, `market_close` y `money`. Cada capacidad debe
  estar declarada `verified` **bajo el perfil local acotado**; cualquier otro
  estado impide el freeze. No es una declaracion general de realismo MT5.
- `verification`: `{path, sha256}` del informe de pruebas del codigo final,
  con formato de `verify_protection.py` (`local_suite_verified`, suite sin
  fallos/errores/omisiones, implementation_sha256 actual y fuentes estables).
  Este informe mas la revision del padre justifican la declaracion anterior;
  el arnes no deduce capacidades del simple numero de tests aprobados.
- `additional_sources`: lista de `{path, sha256}` de los adaptadores locales
  de captura/preparacion/ejecucion/comparacion que vayan a emplearse. Deben
  existir antes del freeze; cualquier cambio posterior invalida el protocolo.
  Debe incluir exactamente una vez `tools/run_simulator_forward.py`; tanto el
  productor de datasets como el de comparaciones deben estar ligados a fuentes
  admitidas. El colector de Dewey se prepara por separado bajo
  `runtime_data/causal_capture_today_20260909/operations/`; no se ejecuta desde
  este arnes ni se supone completado por sus tests.

Las rutas de datos y fuentes adicionales deben permanecer dentro del proyecto.
El protocolo conserva el perfil, su hash, la identidad de implementacion y
entorno, el genoma original y las reglas de admision del futuro dataset. No se
omiten los nuevos market_contract.py y market.py de la identidad congelada.
La identidad del informe de pruebas debe incluir ambos archivos. No se pueden
congelar los bytes de datos que aun no existen: sus reglas se fijan
ahora y los bytes se identifican cuando se retienen.

## Conexion Con Los Controles Existentes

Se reutilizan directamente hash/read_frozen/save, identity, load_tape y la
comparacion exacta de `run_protection_controls.py`, asi como compile_signals y
las convenciones de `run_causal_controls.py`. No hay un nuevo motor.

Los CLI historicos no se relanzan como cohortes nuevas: protection prepare
exige 11 Gold/7 Dubai y datos del 08/09. El sidecar nuevo produce los inputs de
la cohorte futura y llama al hook comun. Ninguna ejecucion condicionada se
convierte en independiente cambiando su etiqueta.

El directorio del dataset debe tener `protocol.json` y `raw_messages.json`,
con los mismos campos de mensajes y pruebas `tapes` que los controles actuales.
Su protocolo incorpora `forward_protocol_sha256`, `universe=independent`,
`producer={path,sha256}`, `execution` normalizada identica al freeze,
`genome_fingerprint`, `start_utc`, `cutoff_utc`, `expected_signal_ids`,
`raw_messages_sha256`, presupuesto cero y mass_search false. Conserva los
campos contract_size=100, currency_digits=2, fx_max_age_ms=5000 y offset=10800.
Se admite hasta cinco segundos de contexto FX anterior al inicio; los mensajes
y XAUUSD pertenecen a la ventana nueva. Las comisiones cero son hipoteticas;
no se admite rollover. La revision de cartera es una salida separada del
sidecar y no certifica margen ni capacidades adicionales de este protocolo.

Los resultados conservan `forward_protocol_sha256`, `protocol_sha256` del
dataset, universe, search_candidates=0, mass_search_authorized=false,
engine_evaluations y `results`, en orden del universo. El contrato es
`simulator_forward_run_v1`; runner identifica el sidecar congelado e
implementation/execution coinciden con el protocolo. Cada fila conserva
signal_id y los asdict completos `scalar`, `fast`, `oracle`.

La comparacion posterior liga `forward_protocol_sha256` e
`independent_results_sha256`; conserva search_candidates, mass_search_authorized
y `rows` para todas las senales en orden. `producer={path,sha256}` debe estar
congelado. Cada fila incluye signal_id, status, blockers, observed_entries,
observed_exits, differences y lifecycle_checks. Cada comprobacion requerida
(`requests`, `accepted_or_rejected`, `installed_protection`, `native_deals`)
incluye status y evidence con referencias `{path,sha256}`. Sin prueba o con
estado distinto de verified queda bloqueada. Se exigen tambien
`native_accounting` y `request_native_binding` con estado **exact** y sus pruebas:
son conciliaciones de hechos nativos, no igualdad de fills hipoteticos.
status de fila puede ser reviewed, differences_for_review o exact; differences
debe ser una lista y se conserva en observed_hypothesis_reviews del resultado.
Una diferencia simulacion/MT5 no invalida automaticamente el motor. Dos cierres
observados como minimo son necesarios para revisar operaciones completas.
El padre realiza el contraste
factual con los auditores existentes; el arnes comprueba su identidad y gates,
no inventa ni reconstruye la hora de procesamiento del servidor.

## Secuencia De Uso

Antes de 06:30 UTC, el padre finaliza perfil, prueba del codigo estable y fuentes
adicionales; ejecuta ready y despues freeze. Todavia no se ha hecho un freeze real.
Despues de retener la captura nueva, el sidecar produce el dataset source-only.
Se ejecuta check inputs, el run del sidecar y, solo despues, la comparacion
factual separada. check observed revalida la ejecucion independiente antes de
consumir esa comparacion. Un corte parcial puede producir un control diagnostico,
pero seguira incomplete hasta el fin fijo de la ventana y los casos minimos.

```text
python tools/prepare_simulator_forward.py ready --profile <perfil_final.json>
python tools/prepare_simulator_forward.py freeze --profile <perfil_final.json> --out <freeze_nuevo>
python tools/prepare_simulator_forward.py check --protocol <freeze_nuevo>/protocol.json --dataset <dataset> --stage inputs
python tools/run_simulator_forward.py run --protocol <freeze_nuevo>/protocol.json --dataset <dataset> --out <run_nuevo>
python tools/prepare_simulator_forward.py check --protocol <freeze_nuevo>/protocol.json --dataset <dataset> --stage observed --results <run_nuevo>/independent_results.json --comparison <comparacion.json>
```

## Inputs De Captura Identificados

Referencias inspeccionadas, todas del 08/09 y sin ejecucion nueva:

- `runtime_data/causal_capture_today_20260908/collect_capture.py`.
- `runtime_data/causal_capture_today_20260908/collect_end_incremental.py`.
- `runtime_data/causal_capture_today_20260908/operations/reuse_and_capture_plan.md`.
- `runtime_data/causal_capture_today_20260908/end_1919/manifest.json` y los
  contratos de los controles ya conservados.

Fuente remota documentada: `C:/Users/bot/telegram-signal-copier/runtime_data/trade_events.jsonl`.
Terminal documentado: `C:/Program Files/MetaTrader 5/terminal64.exe` en la sesion
interactiva existente. Estos paths son referencias; no se ha consultado su
estado actual. El colector retiene un prefijo hasta una linea completa, su
numero de bytes y SHA-256, o un delta gzip respecto de un prefijo probado.

Entradas exactas para la nueva copia de solo lectura: mensajes telegram_raw
con IDs de revision y recepcion; XAUUSD/EURUSD con time_utc, source_time_msc,
bid/ask y orden original; anclas nuevas del reloj; metadatos point/digits/stops/
freeze/contrato; cuenta y moneda identificadas sin exponer credenciales.
La comparacion separada necesita intentos completos request/result y estado
anterior, history_deals_get/history_orders_get con entry, position_id, order,
ticket, price, volume, reason y time_msc. Los SL/TP posteriores y el estado
live nunca entran en la simulacion independiente.

Los helpers antiguos tienen fecha, destinos, prefijo y commit fijados; el
colector de cierre ademas exige cuenta flat. Su reutilizacion requiere una
adaptacion explicita por el padre y destinos nuevos, no ejecutarlos con otro
nombre. Una captura o hashes correctos no prueban continuidad de ticks ni
certeza de la instalacion en el servidor. Los huecos conocidos se mantienen;
los 19 huecos live del archivo del 08/09 no se trasladan como recuento del 09/09.

## Estado De Entrega

Verificacion enfocada: Python 3.14.2 / pytest 9.0.3, 35 pruebas aprobadas de
`tests/test_prepare_simulator_forward.py`. Tambien pasan sus 35 casos cuando se
recoge `tests/test_gold_live_parity.py` (73 casos no ejecutados) para comprobar
que la carga de otras pruebas no contamina sus fixtures. La barrera real de
imports se prueba aparte en un proceso limpio. Una prueba ejecuta escalar,
FastEvaluator y oracle reales sobre dos senales sinteticas, conserva entradas
y eventos de mercado, y repite el control antes de aceptar sus resultados.
No se ha ejecutado aqui la suite completa mientras se integra el motor.

Actualizacion de integracion: el productor acepta ahora `produce --capture`
con el contrato real `morning_causal_capture_v1`. Proyecta mensajes y columnas
de precios; comprueba seis anclas de reloj y metadatos nativos; liga cuenta,
commit y hashes de colector/wrapper al perfil congelado. El corte permanece
fijo aunque las muestras de recogida se tomen hasta 120 segundos despues.
Los bloqueos operativos se conservan. El comparador factual es
`tools/compare_simulator_forward.py`, con reejecucion independiente previa.

La instalacion tiene alcance `observed_state`: puede verificarse mediante
`mt5_position_snapshot` concordante, obtenido con lectura nativa y vinculado
a solicitud, intento y revision. El instante real de instalacion del servidor
permanece `server_install_time=null`; no se sustituye por ACK ni hora del
snapshot. Sin lectura, `not_observed` produce cobertura incompleta conservando
contabilidad y diferencias revisables. Una lectura contradictoria bloquea.
El consumidor conserva `observed_coverage_gaps` y nunca declara fidelidad total.
La conciliacion de dinero y la identidad request/deal siguen siendo exactas.

El procedimiento operativo actualizado, capturas previas y fuentes finales
estan en `../development/2026-09-09-morning-forward-runbook.md`. La ultima
seccion de este informe describe la entrega original del arnes, no el estado
final posterior de toda la integracion.

Arnes y hook de ejecucion local preparados; la entrega del sidecar y del
colector pertenecen a sus tareas respectivas. Pendientes: perfil final, verificacion del
codigo estable, adaptador congelado, freeze real anterior a 06:30 UTC y captura
prospectiva por la tarea principal. Ninguna de estas acciones se declara
realizada por las pruebas sinteticas. No se publico ni se comprobo de nuevo la VM.
