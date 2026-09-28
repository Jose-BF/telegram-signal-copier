# Aceptacion Local De E3-B

Revision Astra/alto del 20/09/2026 sobre la
[segunda reparacion](2026-09-20-e3-b-second-repair-results.md). Rama
`feature/gold-555-live-trial`, HEAD base
`7415941a410d18288fa4e08da76809f70b125efa` y cambios locales preservados.
Se ha leido la implementacion y sus consumidores, agregado controles de
revision y ejecutado pruebas offline. La revision tiene acceso al historial
de reparacion; no es una evaluacion ciega ni un ensayo con broker real.

## Dictamen

R1-R2 cerrados en las fronteras revisadas. No se han encontrado nuevos defectos
funcionales materiales en esta reparacion. E3-B se acepta como base offline
para continuar E3-C. Persiste una fragilidad temporal de dos pruebas,
documentada debajo, que debe corregirse en el trabajo de verificacion E3-C.

La aceptacion abarca persistencia y reserva antes del envio, estado actual
reconstruible, aplicacion atomica y reentrega de resultados terminales. No
declara completada la integracion global del runtime, que sigue pendiente de
E3-C, ni la validacion operativa E4 o paridad de ejecucion y riesgo E5.

## Comprobaciones

- UNKNOWN historico no oculta DONE conciliado; REJECTED historico no oculta
  un reintento DISPATCHING. Hay una vista actual por intencion.
- La vista conserva intenciones antiguas sin reserva y excluye reservas
  SUPERSEDED. La historia de proyecciones permanece intacta.
- Releer DONE o REJECTED liberado no modifica una reserva posterior de la
  misma linea. Caducidad y politica incompatibles siguen siendo rechazadas.
- Una reserva sustituida por una revision nueva sigue bloqueando la reentrega
  de la antigua, incluso si esta contiene un rechazo terminal.
- Un cierre releido despues de cerrar y volver a iniciar el cliente devuelve
  el mismo resultado persistido. Dos procesos sucesivos, una sola llamada
  simulada a order_send y una proyeccion.
- Las regresiones previas de atomicidad, cooldown, resultado tardio y reservas
  siguen cubiertas junto con gateway, worker, cliente y consumidores.

## Evidencia Y Fragilidad Temporal

Archivo nuevo: `tests/test_e3_b_acceptance.py`, ocho casos. Solo se han cambiado
pruebas y documentacion durante esta revision; la implementacion se conserva.

Comando de conjunto:

```text
python -m pytest -q --tb=short tests/test_e3_b_acceptance.py tests/test_e3_b_repair_review.py tests/test_e3_b_review_regressions.py tests/test_execution_intents.py tests/test_durable_execution.py tests/test_durable_entry_execution.py tests/test_mt5_trade_client.py tests/test_mt5_trade_worker.py tests/test_mt5_gateway.py tests/test_pending_actions.py tests/test_canal2_entry_intent.py
```

Primera ejecucion: 188 aprobadas, dos fallidas, 88 advertencias, 28,15 s.
Los fallos fueron `test_late_confirmed_management_is_readable_without_second_send`
y `test_slow_native_send_does_not_block_loop_and_late_result_is_durable`.
Ambas esperaban DISPATCHING tras un plazo de 50 ms. Los dos SQLite retenidos
en `pytest-3885` muestran UNKNOWN con error `deadline_before_order_send`.
El worker identifica precisamente ese vencimiento antes del envio; las
pruebas no alcanzaron el bloqueo nativo que querian ejercitar.

No se cambiaron plazos, tolerancias ni expectativas. Ejecucion aislada de las
dos: dos aprobadas en 1,19 s. Repeticion del conjunto exacto: **190 aprobadas**,
88 advertencias, 21,62 s. Esto confirma sensibilidad al tiempo de preparacion;
la repeticion verde no elimina la fragilidad. E3-C debe sincronizar los
ensayos de respuesta tardia con evidencia de entrada al envio y probar aparte
el vencimiento previo, manteniendo acotadas las esperas.

Se conserva como evidencia global previa la ejecucion de Sol: **6.169
aprobadas**, 651 advertencias, 406,56 s, Windows / Python 3.14.2. No se repite
por cambiar de modelo: esta revision no modifica implementacion. Los ocho
casos nuevos se verifican en el conjunto de 190; no se afirma haber ejecutado
una nueva suite global de 6.177 pruebas. El numero de advertencias por si solo
no demuestra que todas sean preexistentes.

## Identidad Del Codigo Revisado

SHA-256, capturado al cierre de la revision:

```text
execution_intents.py BDFA63898801778E57D5C8411C9DE7B0567DBA4EDB1186F1D5292E3188506D86
durable_execution.py 1B4A2543FA46ED0186108C16A7E0F6E51C5F2402025D4D6CA6680E376BB332A4
durable_entry_execution.py C98964B7FBA6206F1A14EF445FB6A3F573D12FC19270FB422BF8F37E62335C3C
mt5_client.py DC8D9C48EA2C03C127942FC2560ED4143306F543B81276EB73088B3229C0E958
mt5_trade_worker.py A308ED040E75754C8F6516FE1FEF93CA542DF81A74EC6AA174D5E2B36FE1BB57
mt5_trade_protocol.py B7D012B75F6926B55304DD65D568B92C6D10CCEE2AC508AAA47089564CB9B227
pending_actions.py 6B8EE176812F52BC0EE871EA00C498C29CCB1E51A217E81B0350E59D50371519
listener.py 0E013154FB2DEC9E615CE031530A2869730FD922125B7F6B02E45963AD01CE71
tests/test_e3_b_acceptance.py A4CE4608B036490DB57DF8A1F7CCB538C5575361FDBA8D781A1A212463FFB320
```

## Continuacion

Sol/alto: E3-C segun el plan vigente. Conectar arranque, recuperacion y
conciliacion con cuenta/sesion/simbolo actuales; migrar monitores, auditor y
lecturas auxiliares al propietario unico. Reconstruir una apertura historica
no demuestra que siga abierta: cotejar posiciones y cierres antes de
materializar exposicion. Conservar UNKNOWN frente a ausencia confirmada y
actualizar conjuntamente heartbeat, frescura y consumidores.

Hacer comprobables prioridad de gestion y limites de capturas; mantener el
inventario de accesos nativos hasta no dejar rutas live fuera del propietario.
La vista historica del servicio no debe usarse como estado operativo actual.
Probar arranque, recuperacion y las rutas de ambos canales antes de otra
revision de conjunto. No activar un modo mixto accidental.

Siguen pendientes Python 3.11/MT5 real, carga comparable a VM y validacion
amplia de entradas, volumen, protecciones, salidas, flotante y drawdown. No
hubo commit, push, despliegue, reinicio, consulta VM ni operacion real.
