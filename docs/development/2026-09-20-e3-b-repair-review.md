# E3-B: Revision De Reparacion F1-F3

Revision offline de la reparacion Sol, HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`, rama
`feature/gold-555-live-trial`. Se conserva el arbol de trabajo existente.
Solo se agregan pruebas y documentacion; no se modifica implementacion,
publica codigo, conecta MT5 real ni consulta/reinicia la VM.

## Veredicto

E3-B no aceptada todavia. Los casos originales F1-F3 pasan y se verifica
rollback real de la escritura atomica con SQLite. Persisten dos defectos
materiales de recuperacion, reproducidos con cuatro casos nuevos.

## R1 - Estado Historico Oculta El Estado Actual (P1)

Ubicacion: `durable_entry_execution.py:59`, `:107` y `:147`.

`reconstruct()` emite todas las proyecciones historicas y utiliza un conjunto
de intent_id para excluir cualquier estado actual ya representado, sin
considerar revision ni un reintento posterior. Tres casos reproducidos:

- UNKNOWN proyectado -> conciliacion DONE sin proyeccion (base antigua o
  interrupcion): solo devuelve UNKNOWN, sin ticket, aunque el ledger tiene DONE.
- UNKNOWN proyectado -> conciliacion DONE proyectada atomicamente: devuelve dos
  estados de la misma intencion, uno ambiguo y otro confirmado.
- REJECTED proyectado -> reintento explicito DISPATCHING: devuelve REJECTED y
  omite la exposicion potencial del intento en curso. Este ultimo caso tiene
  la misma outcome_revision previa; no se arregla eligiendo solo el maximo
  numero de revision entre proyecciones.

La recuperacion debe entregar un estado actual por intencion, obtenido de una
vista coherente del ledger, conservando el historial como evidencia aparte.
Debe incluir payload, ticket, precio, revision y ambiguedad correspondientes al
intento actual. Una proyeccion historica nunca debe ocultar un reintento.

## R2 - Resultado Terminal No Releible Tras Liberar Reserva (P1)

Ubicacion: `execution_intents.py:360`, consumido por `mt5_client.py:267`.

La escritura atomica de una gestion DONE libera su reserva. Una reentrega
identica pasa por `prepare_reserved()` y lanza ReservationConflictError porque
la reserva ya es RELEASED, antes de devolver el resultado confirmado.

Reproducido con CLOSE_POSITION, cliente real de IPC, proceso hijo con backend
doble y marcador de envios: un solo envio confirmado, reserva RELEASED y
excepcion al releer el resultado. Esto afecta al caso en que el consumidor
pierde la respuesta o recibe el DONE tarde y conserva la accion en la cola;
la siguiente consulta de esa accion no puede concluirla normalmente.

Reparacion requerida: permitir recuperar el resultado terminal ya persistido
para una reentrega compatible sin readquirir/liberar la reserva ni enviar otra
orden. Conservar validaciones de identidad, payload, clave y politica; no
relajar el bloqueo de reservas SUPERSEDED ni de estados ambiguos. Verificar
tambien reapertura del store y respuesta tardia de gestion, no solo entrada.

## Evidencia

Nuevos archivos: `tests/test_e3_b_repair_review.py` y backend aislado
`tests/mt5_trade_review_fakes.py`.

Comando ejecutado:

```text
python -m pytest -q --tb=short tests/test_e3_b_repair_review.py tests/test_e3_b_review_regressions.py tests/test_durable_execution.py tests/test_execution_intents.py tests/test_mt5_trade_client.py tests/test_pending_actions.py
```

Resultado: 119 aprobadas, 4 fallidas, 66 advertencias, 9,07 s; Python 3.14.2 /
Windows. Los fallos son exactamente R1 (tres casos) y R2 (uno).

El control positivo inyecta OperationalError despues de insertar la proyeccion
y liberar reserva, antes del commit. Al reabrir SQLite conserva DISPATCHING,
cero proyecciones y reserva HELD. Al persistir de nuevo el resultado confirmado
aparecen juntos DONE, proyeccion y liberacion. Esto verifica la atomicidad
anunciada, sin simular una caida del proceso real ni usar broker real.

La suite global previa de 6.161 aprobadas no se ha repetido: la implementacion
es la misma y las nuevas regresiones ya impiden aceptar la candidata. El arbol
actual contiene deliberadamente cuatro tests rojos hasta reparar R1-R2.

## Continuacion

Sol/alto: reparar conjuntamente R1-R2, cubrir variantes de reinicio, resultado
tardio, reintento y payload incompatible; ejecutar pruebas dirigidas y suite
global. Astra/alto: revisar la reparacion antes de aceptar E3-B. E3-C y la
validacion operativa/paridad siguen pendientes; esto no acredita rentabilidad
ni equivalencia de simulacion y produccion.

## Reparacion Posterior

R1-R2 se han reparado posteriormente en Sol/alto y se documentan por separado
en [E3-B: Segunda Reparacion R1-R2](2026-09-20-e3-b-second-repair-results.md).
Este informe conserva el veredicto y la evidencia de la revision que descubrio
los defectos. La nueva candidata queda pendiente de otra revision Astra/alto;
no se considera aceptada ni activada en produccion por la reparacion local.
