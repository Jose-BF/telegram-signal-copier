# Cancelaciones Simultaneas A Confirmaciones

## Incidente Y Causa

Durante la suite del motor suspendible aparecio un fallo real fuera del motor:
`test_async_cancellation_keeps_capacity_reserved_until_thread_finishes` no
recibio `CancelledError`. Resultado original preservado: 6.537 aprobados,
un fallo, una advertencia pandas previa, 473,44 s. JUnit
`reports/e5-resumable-causal-fill-20260921/pytest.xml`, SHA256
`f4c8328b63634603434be8f8a26bf06774d3df6e03c835f0815c754cd7846124`.
Repetir solo ese test paso en 0,24 s, pero no se descarto el incidente.

Lectura de la implementacion instalada de `asyncio.wait_for` en Python 3.11.9:
si llega una cancelacion y el future interno ya termino, puede devolver su
resultado en vez de propagar la cancelacion. Se reprodujo deterministamente
sin MT5: confirmacion `started`, callback de shield y cancelacion del solicitante
ordenados en el mismo turno del bucle. La tarea seguia esperando el hilo tras
cancelarla. El doble inicial necesitaba inicializar `_async_admission`; ese
error de preparacion se corrigio antes de demostrar la carrera efectiva.

La misma frontera se reprodujo en el cliente actual:

- Notificacion de transporte y cancelacion simultaneas: devolvia `True` y
  adquiria la conexion para trade, management y read.
- Respuesta y cancelacion simultaneas: devolvia `ReadResponse` en vez de
  propagar la cancelacion.
- Confirmacion de evidencia y cancelacion simultaneas: el servicio de entrada
  llegaba a `client.execute` en el doble, pese a la cancelacion pendiente.

No se atribuye este fallo a una operacion historica concreta sin trazas.
Su reproduccion basta para corregir el mecanismo general de ejecucion.

## Correccion Acotada

`mt5_gateway.py`, `mt5_client.py` y `durable_execution.py` usan ahora
`asyncio.timeout` con await directo. Se conservan los shields de trabajo ya
lanzado y los callbacks propietarios de la liberacion. Semaforo, condicion y
probe de evidencia no crean una tarea hija solo para medir el plazo.

Se mantienen deadlines, guards, estado desconocido, conciliacion y politicas
de reenvio. Cancelar al solicitante no interrumpe una llamada nativa iniciada
ni devuelve su capacidad antes de drenar. No se usa `uncancel` ni se aumenta
ningun limite temporal para hacer pasar las pruebas.

Esta API requiere Python >= 3.11. Se verifica en los dos interpretes actuales:
3.11.9 offline y 3.14.2 local. No se promete compatibilidad con 3.10.
`timeout(0)` no es identico a `wait_for(..., 0)` para un await que no suspende;
se conservan las comprobaciones de deadline de admision, transporte, arranque
y envio. La revision posterior y el cierre acotado del parche final se
documentan mas abajo; no equivalen a certificacion de todo el runtime.

## Verificacion

`tests/test_gateway_cancellation_race.py`: siete controles deterministas,
incluyendo admision concedida/cancelada sin trabajo de broker, la carrera de
arranque, tres clases de transporte, respuesta y evidencia previa a entrada.
Los casos nuevos verifican tambien devolucion de capacidad y ausencia de
despacho. Ninguno crea terminal ni envia operaciones reales.

- Python 3.11.9: 97 focales aprobadas en 39,59 s, incluyendo pruebas existentes
  de gateway, lectura, mutaciones, durabilidad y disco cancelado/lento.
- Python 3.14.2 / pytest 9.0.3: las siete regresiones pasan en 0,29 s.
- Carga de 60 s aprobada (61,047 s total): 2.400 lecturas FOUND, 60 mutaciones
  ficticias DONE (20 entradas/20 modificaciones/20 cierres), 20 checkpoints
  confirmados max 31 ms; bucle p99 16 ms/max 32 ms; mutaciones max 78 ms.
  Maximo de pendientes 3, drenado; cero errores de diario; trabajador y
  vigilante terminados, fuentes sin cambios. No es pico real ni prueba de VM.
  `reports/e4-cancellation-race-load-20260921/result.json`, SHA256
  `0de7f338613cff53c77a31d6d760f110b418ee0f1cdac10219938e0c15e1494a`.
- La corrida intermedia de 6.545 casos se detuvo deliberadamente alrededor
  del 42% al confirmar la ventana del guard, antes de editar Python. Se
  identifico por comando/PID exclusivamente el pytest propio; sesion recogida,
  sin declarar aprobado ni reutilizar un JUnit parcial. Sus fuentes quedan en
  `reports/e5-resumable-causal-fill-20260921/sources-final.json`.

Revision independiente del cambio: sin regresion material identificada en
las nueve sustituciones. Detecto una ventana preexistente adicional y la
reprodujo con FakeTradeMT5 y barrera: PREPARED -> caller cancelado -> liberar
guard -> una orden ficticia enviada -> DONE. Capacidad retenida correctamente,
pero ausencia de comprobacion posterior al guard. El padre reprodujo tambien
el envio indebido con un test antes de corregirlo.

## Autorizacion Frente A Cancelacion

`_DispatchDecision` comparte un lock breve entre abandono y autorizacion.
La autorizacion ocurre despues del guard y antes de `begin_dispatch`.
Cancelacion, stop o plazo agotado que ganan esa decision abortan la preparacion
y dejan fallo predispatch durable, sin commit. Si gana autorizacion, una
cancelacion posterior abandona la espera, no revoca el permiso ya concedido:
se permite persistir y completar/conciliar la operacion. Nunca reenviar por
haber recibido CancelledError. El worker mantiene su guard de plazo/cuenta.

El lock no envuelve guard, SQLite, IPC ni broker. No se promete revocacion
instantanea de trabajo ya autorizado. Una autorizacion local no equivale a un
fill ni garantiza que la persistencia o el envio tengan exito.

Seis pruebas adicionales: barreras antes y despues de autorizacion, rechazo
de una segunda autorizacion, cancelacion previa, stop y plazo agotado. La
prueba posterior a autorizacion obtiene exactamente una operacion ficticia
DONE y drena la capacidad. Se espera el callback de liberacion de transporte,
que es posterior al retiro de la admision; no se fuerza una identidad temporal
que el runtime no garantiza. Trece regresiones nuevas pasan en Python 3.11.9
y Python 3.14.2 (1,19 s en este ultimo); 33 focales de integracion pasan en
3.11.9 / 15,23 s.

La revision independiente propuso esta frontera, pero **la revision adicional
del codigo final no pudo completarse por limite de uso del agente**. No se
presenta la propuesta revisada como revision del parche final. Revision
directa del padre y verificacion automatica siguen disponibles. No se ha
consumido ningun credito ni cambiado el modelo automaticamente.

Actualizacion tras continuacion explicita del usuario: completada la revision
independiente final de esta frontera, sin hallazgos materiales. Se comprobaron
cliente/pruebas contra `sources-authorized.json` y hashes de carga/JUnit
archivados. Seis probes en memoria: cancelacion, expiracion, stop y excepcion
ordinaria del guard abortan sin commit; cancelacion despues de autorizacion
produce un unico commit; respuesta perdida deja UNKNOWN y repetir la ejecucion
no anade envio. Dobles de almacenamiento/conexion, no prueba de persistencia
fisica ni de muerte del proceso. El punto lineal de abandono es `abandoned.set()`
al atender la cancelacion, no la mera invocacion anterior a `Task.cancel()`.
La revision no cambia las fuentes ni sustituye la comprobacion operativa en VM.

La carga anterior corresponde al codigo antes de `_DispatchDecision`.
Nueva carga con autorizacion aprobada: 60,125 s, 2.400 lecturas, 60 operaciones
ficticias DONE, 20 checkpoints confirmados max 31 ms; bucle p99 16 ms/max 32 ms,
operaciones p99/max 93 ms; max pendientes 3, drenado, cero errores de diario,
worker y guardian terminados. Fuentes sin cambios. Informe
`reports/e4-cancellation-authorization-load-20260921/result.json`, SHA256
`cd74a4087b4de90385a28cf0404712e4011db229e240c6f5e121939343effc8d`.
No acredita pico de produccion, VM ni broker. Fuentes congeladas en
`reports/e5-resumable-causal-fill-20260921/sources-authorized.json`.
Suite completa final: **6.551 aprobados**, una advertencia previa de pandas,
472,22 s, Python 3.11.9 / pytest 9.1.1. Comando:
`python -m pytest -q --junitxml=reports/e5-resumable-causal-fill-20260921/pytest-authorized.xml -o junit_family=legacy`.
SHA256 JUnit:
`9af544e32e6a7bc454dd6bf42bb5ec05e22645873cb278d4b43e8b5f9efb6b54`.
Comparacion posterior de todos los hashes de la identidad de investigacion,
los nueve archivos del bloque y las fuentes del medidor: sin cambios.
No quedan procesos pytest, medidor, trabajador o guardian de estos ensayos.
`git -c core.safecrlf=false diff --check` aprobado.

Esto cierra la regresion local, no el objetivo completo: faltan integracion
del coordinador con politicas, contraste amplio de riesgo/ejecucion, retencion
compatible y comprobaciones/autorizacion de
publicacion. Una reparacion operativa no necesita esperar universalmente a
la certificacion entera del simulador; si se publica, requiere su propia
revision de exposicion, recuperacion, Python efectivo >= 3.11 y rollback.

Este arreglo invalida para la version nueva los hashes de cargas previas;
esas evidencias se conservan como historicas. El generador y el fill causal
siguen sin conectar el transporte entre cestas. El objetivo completo continua,
incluyendo integracion, cohortes y entrega operativa. Sin commit, push, VM,
reinicio, automatizacion ni cambio de politica live.
