# Revision De La Segunda Reparacion E3-C

20/09/2026. Revision local independiente sobre
`feature/gold-555-live-trial`, HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`. Se conserva el arbol de trabajo
preexistente. No hubo modificaciones de runtime, commit, push, despliegue,
reinicio de VM ni orden real. Solo se agregan regresiones y documentacion.

## Dictamen

E3-C no aceptada todavia. Los controles anteriores pasan, incluidos los once
casos de la revision previa. R2 (payload), R3 (memoria) y R4 (relojes) no
presentan nuevos hallazgos materiales en este alcance. R1 sigue incompleta:
recuperar el ticket no equivale a recuperar su gestion de manera segura.
E4 y E5 permanecen pendientes; estas pruebas no miden incidencias de la VM.

## Hallazgos

### F1 P1: El reinicio puede retroceder un stop ya mejorado

`position_lifecycle_monitor.py:159` vuelve a instalar la proteccion inicial
para todo DONE recuperado, aunque el ticket ya este incorporado y gestionado.
`position_lifecycle_monitor.py:1529` calcula el stop inicial sin conservar el
actual ni una solicitud pendiente mas protectora; tambien sobrescribe el
suelo de trailing en memoria. `mt5_trade_worker.py:264` comprueba validez
respecto del mercado, no que el nuevo stop mejore el anterior.

Reproduccion BUY: fill 4298.5, stop actual 4293.5; solicita 4268.5.
Reproduccion SELL: fill 4301.5, stop actual 4306.5; solicita 4331.5.
Son precios, no euros ni pips. En ambos casos empeora la proteccion en 25
unidades de precio. La prueba observa la solicitud, no una ejecucion real.

Regresion: `test_restart_preserves_already_improved_stop`, BUY y SELL.

### F2 P1: Un cierre solicitado no domina la recuperacion tardia

Cuatro controles exponen distintas rutas de la misma obligacion de gestion:

- `listener.py:8254`: una watch restaurada con DONE conserva
  `order_started=False`; CLOSE_ALL la elimina como si no hubiera exposicion.
  El siguiente tick no encola el cierre del ticket confirmado.
- `listener.py:8065`: `gold_555_provider_close_during_open` se considera
  terminal al reconstruir el journal. Tras registrar ese cierre con una
  apertura en curso, reiniciar restaura cero watches pese al DONE durable.
- `listener.py:8585`: al reutilizar una Signal solo se consulta el cierre
  de la watch; se ignora `existing_signal.requested_close_reason`, solicitando
  SL/TP en vez de completar el cierre desde esta recuperacion.
- `position_lifecycle_monitor.py:1742`: una pata tardia recuperada en marcha
  recibe SL/TP aunque `requested_close_reason` ya este establecido. La ruta
  de arranque si tiene esta comprobacion, pero la ruta por tick no.

Regresiones: `test_close_before_first_restored_tick_does_not_discard_done`,
`test_restart_preserves_close_received_while_order_in_flight`,
`test_existing_signal_close_request_wins_over_protection` y
`test_provider_close_also_closes_late_leg`.
El tercer caso demuestra el defecto de esa ruta, no que ninguna otra cola
preexistente pudiera cerrar la posicion. Los otros controles no tienen tal
cierre encolado. No basta con reconocer DONE: su cierre pendiente debe sobrevivir.

### F3 P1: Una aplicacion parcial pierde su reintento

`position_lifecycle_monitor.py:1732` retira la reconciliacion pendiente y
marca el indice como lleno antes de encolar la proteccion. Si encolar el TP
falla, el tick siguiente ya no vuelve a completar ese TP. El control reproduce
el fallo transitorio y un tick posterior con la ventana de entradas vencida;
la gestion de una posicion existente no deberia vencer con esa ventana.

En primera entrada, `listener.py:8796` elimina la watch y publica un aborto
terminal ante una excepcion posterior al DONE. El control falla al encolar
TP sobre una Signal existente: `applied_utc` ya consta en SQLite, pero el
reinicio restaura cero watches y el TP nunca fue encolado.

Regresiones: `test_late_gold_leg_retries_incomplete_protection` y
`test_projection_failure_does_not_terminalize_confirmed_fill`.
Separar confirmacion del broker, proyeccion de exposicion y finalizacion de
efectos de gestion; un acuse anterior no acredita todos los pasos posteriores.

### F4 P1: La reconstruccion mezcla registros de distintas cuentas

`durable_entry_execution.py:61` recorre los intents actuales de todo el ledger
sin filtrar cuenta/servidor; EntryRecoveryRecord tampoco conserva esa identidad.
`position_lifecycle_monitor.py:102` solo compara direccion y magic del payload
con la Signal, y acepta cualquier posicion devuelta por ese ticket.

El control guarda un DONE bajo `other-server/99`, configura el cliente como
`demo/7` y simula una posicion de la cuenta actual con el mismo ticket, raiz y
magic. La recuperacion adopta el ticket y solicita TP 4299.5. Es relevante al
reutilizar un ledger tras cambiar cuenta/servidor; no se afirma que eso haya
ocurrido en produccion. La identidad durable debe limitar la recuperacion
antes de tocar Signal o encolar gestion, no solo el envio de aperturas nuevas.

Regresion: `test_restart_does_not_apply_entry_from_another_account`.

### F5 P2: Cancelar la tarea descarta aplicacion confirmada pendiente

`listener.py:8811` elimina la watch ante CancelledError. Si la cancelacion
ocurre consultando posiciones despues de obtener DONE, desaparece el trabajo
en memoria sin completar protecciones. A diferencia de F3, el journal aun
permite recuperarlo al reiniciar. Si el proceso sigue activo, esa ruta no
conserva su reintento.

Regresion: `test_cancelled_position_read_keeps_confirmed_application_pending`.

## Evidencia

Dos archivos nuevos, doce controles: diez fallan por las condiciones anteriores
y dos pasan (reaplicacion vencida sobre la misma Signal y payload congelado con
rechazo de cambios semanticos). Lectura independiente de listener/adapter y
revision principal del monitor. Resultado reproducido conjuntamente:

```text
python -m pytest -q tests/test_e3_c_second_review_monitor.py tests/test_e3_c_second_review_listener.py tests/test_e3_c_repair_review.py tests/test_e3_c_restart_review.py tests/test_gold_555_listener.py tests/test_durable_entry_execution.py tests/test_mt5_runtime.py tests/test_mt5_read_worker.py --tb=short --disable-warnings
10 failed, 106 passed, 42 warnings in 5.37s
```

Windows, Python 3.14.2, pytest 9.0.3. SQLite temporal, broker simulado y, para
primera entrada, subproceso productor del DONE. Son pruebas offline de
aplicacion; no una certificacion operativa del broker, VM o Python 3.11.
Los siete hashes de runtime coinciden con la segunda entrega; no se repite
la suite global porque no se modifica runtime. Los 6.224 aprobados de aquella
entrega son evidencia anterior, no un resultado nuevo ni una suite verde
actual: las diez regresiones nuevas se dejan fallando deliberadamente.

## Reparacion Coherente Y Cierre

No agregar excepciones por fecha, numero de operacion o prueba individual.
Completar el contrato ya requerido de aplicacion recuperable: identidad de
cuenta y posicion, estado de exposicion, prioridad del cierre y progreso
durable de protecciones. Reusar las primitivas existentes; no hace falta
inventar otra estrategia ni sustituir el motor para resolver estos fallos.

La siguiente implementacion debe cubrir una matriz acotada de primera entrada
y patas, ambos canales cuando corresponda, BUY/SELL, proceso continuo/reinicio,
resultado repetido, cierre solicitado antes/durante/despues del fill, stop
mejorado, fallo entre efectos, cancelacion y cuenta distinta. Los cortes deben
tener controles de que no se abre de nuevo, no se pierde gestion y no empeora
el riesgo. Encolar no equivale a que el broker haya confirmado la proteccion.

Mantener DISPATCHING/UNKNOWN como desconocido hasta conciliarlo con evidencia
del broker. Su prueba operativa en E4 no autoriza descartarlo o reenviarlo.
Tras reparar: regresiones, suite global y revision independiente; despues E4
y E5 segun el plan. No reabrir la busqueda masiva ni publicar con E3-C abierta.
Modelo previsto para la implementacion: Sol/alto; Astra/alto para la revision
del bloque completo, no alternar modelos por cada prueba individual.
