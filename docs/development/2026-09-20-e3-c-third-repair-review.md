# Revision De La Tercera Reparacion E3-C

20/09/2026. Revision Astra/alto sobre la tercera reparacion local de E3-C en
`feature/gold-555-live-trial`, HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`. No hubo cambios de runtime,
commit, push, despliegue, reinicio de VM ni orden real. Solo se agregaron
contrapruebas y este dictamen.

## Dictamen

E3-C sigue abierta. Las diez regresiones de la revision anterior permanecen
verdes, pero cuatro carreras generales nuevas fallan. No dependen de una fecha,
ticket historico ni operacion concreta. La tercera reparacion mejora de forma
material la recuperacion, pero aun no demuestra que toda pata confirmada
conserve su gestion ni que un cierre o stop mas protector prevalezcan en todos
los cortes posibles.

## Hallazgos

### F1 P1: Cancelar durante el dispatch de una pata puede perder su sondeo

`position_lifecycle_monitor.py:1781` espera el resultado durable antes de
anotar el indice como pendiente de conciliacion en la linea 1805. Si la tarea
recibe `CancelledError` despues de que el envio haya podido empezar, la marca
queda vacia. Sin un nuevo cruce de precio, el proceso activo no vuelve a
consultar la identidad determinista; la exposicion solo reaparece tras otro
reinicio.

Regresion:
`test_cancelled_delayed_dispatch_remains_reconcilable_without_new_cross`.

### F2 P1: Un cierre durante la preparacion asincrona no impide una pata nueva

`position_lifecycle_monitor.py:1397` y la preparacion Dubai contienen esperas
antes del envio, pero la condicion de cierre solo se consulta antes de entrar
en `_open_candidate_leg`. Si llega `PROVIDER_CLOSE` durante esas esperas,
`position_lifecycle_monitor.py:1464` envia igualmente la apertura. La primera
entrada 555 tiene una watch cancelable; las patas tardias no repiten ese control
ni pasan un `dispatch_guard`.

Regresion: `test_delayed_leg_rechecks_close_after_async_preparation`.

### F3 P1: Canal 1 puede sustituir un stop pendiente mas protector

`listener.py:714` recalcula el stop comun Dubai y lo encola sin conservar una
solicitud pendiente mas fuerte. `pending_actions.py:527` fusiona la accion
cambiando el nivel anterior. En la reproduccion SELL, una solicitud pendiente
en 4210 se sustituye por 4228 antes de llegar al broker. El control ya existente
para un stop instalado no cubria el estado pendiente.

Regresion: `test_dubai_recalculation_preserves_stronger_pending_stop`.

### F4 P2: La recuperacion valida contra el payload, no contra el plan congelado

`position_lifecycle_monitor.py:101` compara direccion y magic, y las lineas
149-168 comparan la posicion con el propio payload durable. No verifican que
indice, volumen y comentario correspondan a la pata del plan congelado en la
`Signal`. Un DONE y una posicion de 0.09 pueden incorporarse como pata 1 aunque
el plan vigente de esa senal fija 0.03, tras lo cual se solicita su gestion como
si la identidad fuese valida.

Regresion: `test_restart_rejects_delayed_fill_outside_frozen_entry_plan`.

## Evidencia

La matriz dirigida conserva 121 controles aprobados y reproduce los cuatro
hallazgos:

```text
4 failed, 121 passed, 122 warnings in 8.75 s
```

Archivo nuevo:
`tests/test_e3_c_third_repair_review.py`. Entorno Windows, Python 3.14.2,
broker simulado. No es evidencia de la VM, Python 3.11, MT5 nativo ni paridad
real/simulada.

## Reparacion Acotada

La siguiente entrega debe marcar la conciliacion antes de cualquier espera que
pueda haber enviado una pata; revalidar cierre y plan inmediatamente antes del
dispatch; conservar el stop mas protector tambien en el recalculo Dubai; y
comparar el registro recuperado con la fila exacta del plan congelado antes de
mutar `Signal`. Repetir estas cuatro regresiones, la matriz E3-C y la suite
global. Despues corresponde una revision final Astra/alto y, solo si se acepta,
E4 operativo. No hay autorizacion de publicacion o activacion live.

