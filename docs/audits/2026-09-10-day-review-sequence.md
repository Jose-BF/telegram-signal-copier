# Secuencia Del Jueves 10/09

Actualizacion a las 10:22 Madrid: el usuario pide avanzar en paralelo y delega
la coordinacion. Seguir tambien
`../development/2026-09-10-parallel-simulator-delivery.md`. Historicos,
inventario real de pendientes y control integral avanzan ya; las 14:00 y
19:00 no son condiciones para empezar esos trabajos offline.

## Autorizacion Y Objetivo

El usuario autoriza publicar las correcciones pendientes, recuperar el bot,
recoger operaciones hasta las 14:00, revisar y corregir fallos identificados,
publicar los arreglos necesarios y comprobar el resultado a las 19:00 Madrid.
No autoriza cambiar estrategias para forzar coincidencias ni iniciar la busqueda
masiva. Se mantiene la comprobacion de exposicion antes de cualquier reinicio.

Automatizacion activa: `revisar-bot-y-simulador-a-las-14-y-19`, ligada a esta
tarea, solo 10/09 a las 14:00 y 19:00. El seguimiento se eliminara al terminar
la revision de las 19:00; el bot sano debe continuar funcionando.

El objetivo es comprobar un recorrido completo y acotado desde mensajes y
precios hasta entradas, gestion, cierres y dinero calculado, sin usar las
operaciones reales como entradas del simulador. No se exige que coincida cada
milisegundo ni que la estrategia gane dinero. Debe quedar claro que esta
comprobado y que falta; no abrir una cadena indefinida de pruebas.

## Diagnostico Inicial

La VM conserva `ed9ede1f1f8d228791e09d5b10bbdd2645c1793c`, arbol limpio.
Windows Update solicito reinicios a las 00:17 y 00:43 del 10/09 Madrid, segun
eventos User32 1074 (MoUsoCoreWorker y TrustedInstaller, SYSTEM, planificados).
MT5 registro cierre por apagado del sistema. Esta manana se actualizo y dejo
autorizacion demo correcta a las 08:31; no se ha hecho login durante el diagnostico.

La tarea del supervisor se inicia al entrar en Windows y arranco a las 08:19.
A las 09:07 habia supervisor 5528 y publicador 3460, no un proceso principal
identificado ni heartbeat. El publicador conserva un timeout de commit a las
08:30, tres fragmentos y seis ficheros pendientes; existe index.lock desde las
08:28. Ultima publicacion correcta: 00:13 del 10/09 Madrid. La causa exacta
del bloqueo actual de recuperacion aun no esta cerrada.

El bloque local del 09/09 modifica `main.py` y su prueba de observacion, y anade
`tools/audit_raw_observations.py` con pruebas. Los cuatro hashes siguen iguales
a los verificados con 4465 pruebas y dos incidentes nativos. Sigue sin commit ni
push al iniciar esta secuencia. El resto de investigacion local debe preservarse
y no publicarse incidentalmente.

## Recuperacion Y Publicacion De La Manana

Diagnostico confirmado a las 09:25 Madrid con lectura de las pilas Python:
el supervisor 5528 estaba detenido en `run_bot_watch.py:316`, escribiendo el
mensaje de checkpoint, y el publicador 3460 en `runtime_telemetry.py:1674`,
escribiendo el error de publicacion. Ambos estaban bloqueados en salida de
consola; no dentro del checkpoint ni ejecutando Git. No se afirma que una
seleccion de texto fuera la causa exacta de la consola, que no se observo.
La herramienta diagnostica py-spy 0.4.2 se instalo aislada bajo
`C:/Users/bot/codex-control/diagnostic_pyspy_20260910`, sin modificar el entorno
Python del bot. No se tocaron Windows Update ni las protecciones del sistema.

Comprobacion nativa mediante helper de solo lectura, sin login ni ordenes:
terminal existente 6312, sesion 2, misma cuenta demo EUR identificada por hash.
Las capturas de 07:26:46, 07:28:07, 07:29:02 y 07:30:26 UTC confirmaron cero
posiciones, cero ordenes y cola persistida de acciones vacia. Antes de parar
los procesos bloqueados o iniciar el supervisor se exigio una captura nueva
de menos de 35 segundos y ausencia de otro proceso Python inesperado.
No habia proceso principal: no se sustituyo su estado vivo por una presuncion
de ausencia de exposicion. El estado nativo y la cola se comprobaron aparte.

Se conservo la tarea anterior en
`C:/Users/bot/codex-control/watcher_task_before_20260910.xml`. La tarea existente
`Signal Copier Bot Watcher` usa ahora el lanzador
`C:/Users/bot/codex-control/start_logged_bot.py`, con salida y errores en
`runtime_data/watcher_20260910*.out.log` y `*.err.log`, sin depender de una
consola visible. El primer intento de lanzador PowerShell termino con codigo 1
y no arranco Python; quedo sin uso. Se empleo Python para la tarea, sin cambiar
la politica de ejecucion de Windows. Las fuentes operativas locales estan en
`runtime_data/day_review_20260910/operations/`; no forman parte del codigo live.

Commit publicado en `origin/main` y descargado limpio por la VM:
`892bc33c8f6c19be7248401ec3cf6a27ba3d0563`, padre `ed9ede1f1`.
Incluye exclusivamente `main.py`, `tests/test_strategy_shadow_main.py`,
`tools/audit_raw_observations.py` y `tests/test_audit_raw_observations.py`.
El resto de cambios de investigacion permanece local y conservado.
Los nueve hashes relevantes coinciden con la verificacion del 09/09:
4465 pruebas completas aprobadas. Hoy se repitieron las dos suites afectadas:
72 aprobadas, una advertencia, 7.48 segundos, Python 3.14.2 / pytest 9.0.3.

El supervisor nuevo arranco a las 07:30:31 UTC (09:30:31 Madrid), con lanzador
6372 y supervisor 8144. A las 07:33 estaba comprobando los registros preservados
(`runtime_paths._inspect_stream_prefix`); habia leido aproximadamente 1.8 GB.
La salida de consola anterior ya no bloquea. La conexion y el latido del bot
principal quedan pendientes de la comprobacion final de este arranque.

A las 07:35:29 UTC el proceso principal 6180 conecto con MT5 y confirmo que no
habia posiciones huerfanas. Declaro las mismas reglas Dubai y Gold 555, sin
alteracion de estrategias. A las 07:36:17 UTC se publicaron los seis archivos
pendientes, commit de telemetria `30e1d016978ee197f4dd8947e7dc77828a7d4d3b`,
estado correcto y cola de publicacion vacia. El bloqueo antiguo de `index.lock`
se resolvio por la recuperacion normal existente; no se borro manualmente.
El principal estaba reconstruyendo las entradas vigiladas desde el diario,
una lectura de arranque necesaria para conservar el estado previo.

El primer principal 6180 no llego a Telegram: el supervisor agoto sus 600
segundos de arranque y lo reinicio mientras releia el diario de unos 2 GB.
Las pilas sucesivas demostraron avance por las restauraciones de esperas y
planes Gold, no bloqueo de consola. No se omitieron esas restauraciones ni
se ampliaron los 600 segundos. Una prueba de lectura de 64 MiB por SSH dio
0.253 s con buffer de 8 KiB y 0.238 s con 1 MiB, misma huella y 126450 filas;
no justificaba modificar los lectores del codigo.

La tarea tenia prioridad 7, con procesos BelowNormal. Windows asigna ademas
prioridad baja a disco y memoria con ese valor; el valor 4 utiliza prioridades
normales. Ver [contrato de prioridad de Microsoft](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-priority-settingstype-element).
Cambiar solo la prioridad CPU de un proceso existente no aplicaba la nueva
configuracion de lanzamiento. Se conservo el resto de ajustes de la tarea y
se cambio exclusivamente su prioridad a 4, sin elevarla a alta/tiempo real.

El reinicio automatico habia creado principal 8016 a las 07:44:31 UTC. Antes
de relanzar la tarea con su configuracion normal se hicieron nuevas consultas
nativas (posiciones, ordenes y acciones pendientes vacias). Se rechazo un
intento con captura caducada; otro espero a que realmente desaparecieran todos
los hijos. `Stop-ScheduledTask` detuvo el lanzador, pero no todos sus hijos,
por lo que se termino exclusivamente el arbol conocido del supervisor 8144,
con cuenta flat comprobada y sin heartbeat de inicio completado. No se termino
MT5. Estos detalles son importantes para una futura parada operativa: no dar
por cerrado el arbol Python solo porque la tarea de Windows haya terminado.

La tarea con prioridad 4 arranco a las 07:48:25 UTC, con lanzador 4824 y
supervisor 4468, ambos en prioridad Normal comprobada. La guarda fue
`native_health_20260910T074817_3260.json`, con 3.68 s de antiguedad al actuar.
La configuracion posterior se conserva en
`C:/Users/bot/codex-control/watcher_task_after_20260910.xml`.
En la primera comprobacion el supervisor ya habia leido aproximadamente
1.7 GB.

## Arranque Confirmado

El ultimo supervisor lanzo el principal 5216 a las 07:49:37 UTC. MT5 conecto
a las 07:49:57 y Telegram a las 07:51:35. Se confirmaron ambos canales,
monitores y auditor activos. Primer heartbeat de esquema 3 a las
07:51:35.277 UTC: cero posiciones, cero senales abiertas, cero entradas
pendientes. Seguian en cero a las 07:52:05.497, con heartbeat avanzando.
La VM conserva limpio `892bc33c8f6c19be7248401ec3cf6a27ba3d0563`.
El arranque completo de la tarea duro aproximadamente 190 segundos, dentro
de los limites originales; no se modificaron los timeouts ni las estrategias.

El inicio util de recogida de operaciones nuevas es 07:51:35 UTC
(09:51:35 Madrid), no el momento del push ni los intentos anteriores. Los
mensajes recuperados de la parada conservan sus horas de publicacion y
recepcion; no etiquetarlos como recibidos en directo a su hora original.
Las comprobaciones de las 14:00 y 19:00 deben separar tambien este cambio
operativo de prioridad de Windows cuando comparen retrasos de ejecucion.

Comprobacion final guardada localmente en
`runtime_data/day_review_20260910/recovery_verified_20260910T075401.json`,
SHA256 `18b9b82c2272ffdfe185c319bc0c315b074b8568aa7bfc407b84759a1109c6f2`.
Incluye estado limpio del codigo, heartbeat 07:53:51, prioridad 4 y publicacion
correcta de cuatro archivos nuevos a las 07:53:56 UTC, commit de telemetria
`66f58b9992a27b81815d1c286133363361de5485`, sin ficheros pendientes.
El cursor anterior al ultimo arranque es byte 2024023315, prefix SHA256
`23df6895a7d4cd43d329b4ccf045188f28d99e7bae18966630a91a565ad6c3fd`.
El posterior publicado alcanza byte 2025445061, prefix SHA256
`e2e96e315c8fb8e2c23410909c74181701e2f6a2ca45c6b8072e023e4a8f1a18`.
Usar esos cursores y los fragmentos originales para localizar el tramo nuevo;
no confundir un cursor con una descarga local de todo el diario.

MT5 confirmo de nuevo cero posiciones, ordenes y acciones pendientes a las
07:54:33.420 UTC. Evidencia local:
`runtime_data/day_review_20260910/native_health_20260910T075430_8092.json`,
SHA256 `1e71f74670566903ce0e5dc34b41e9e2d848c3c72698130af3cb7930d24332d1`.
Se conservaron tambien las ocho capturas nativas, ambas configuraciones de la
tarea y los dos pares de logs de arranque; ambos logs de errores estan vacios.
La tarea puntual de comprobacion nativa se retiro tras terminar correctamente.
La tarea permanente del bot queda funcionando y la automatizacion local
mantiene unicamente las revisiones autorizadas de las 14:00 y las 19:00.

Tambien se verifico `strategy_shadow_runtime_started` a las 07:52:34.120 UTC
con `892bc33c8`: tres observaciones Dubai y tres Gold activas, 354 estados
recuperados, sin ordenes MT5 del observador. El sondeo de ambos canales quedo
activo a las 07:52:56.217. El bloque con todos los componentes listos empieza
ahi; conservar por separado cualquier senal entre conexion e inicio del
observador. La recuperacion del canal 2 encontro 32 mensajes y dos ediciones;
canal 1, un mensaje. Las senales antiguas 2723, 2729, 2738 y 2747 se ignoraron
por superar los 120 segundos permitidos. Conservarlas y explicar la parada;
no contarlas como operaciones nuevas ejecutadas ni excluirlas sin motivo.

## Estado De La Ejecucion

- Revisiones 14:00 y 19:00: programadas correctamente.
- Recuperacion del bot: confirmada; Telegram y MT5 conectados, heartbeat fresco.
- Publicacion del bloque pendiente: `892bc33c8` publicado y activo en VM.
- Pruebas del recorrido independiente: control retrospectivo de 11 Gold y
  comparacion factual realizados en el bloque paralelo de las 10:22;
  la secuencia integral y los datos nuevos siguen pendientes.

Las revisiones posteriores deben continuar desde este resultado, no repetir
la preparacion ni activar codigo sin una comprobacion nueva. El motor y sus
pruebas locales existen, pero sigue pendiente cerrar el recorrido completo
desde mensajes a cierres y dinero, comprobarlo con operaciones nuevas y
terminar de admitir el historico que realmente se vaya a utilizar.

## Entrega Paralela De La Manana

Se recibio y reviso el adaptador de los cuatro JSON: 22.656 filas originales,
21.977 IDs sin fusionar canales; 5.731 IDs en 2026. Preparados 36 disparadores
sin marca de edicion y 894 bajo el escenario separado de hora de revision.
No son operaciones ni recepciones demostradas. Precios, continuidad y admision
de los horizontes siguen abiertos.

Control propio de 11 Gold, tres motores: 33 evaluaciones sin desacuerdos ni
bloqueos. La comparacion MT5 posterior conserva 45 aperturas reales frente
a 46 simuladas, once diferencias y siete Dubai fuera del control. Se explican
las familias de referencia inicial, fill que ancla el ladder e interpretacion
de gestion; no se modifican tolerancias ni reglas live para igualarlas.

Dos defectos de las herramientas de comparacion se reprodujeron y corrigieron
localmente. Evidencia: 4511 tests de la suite completa previa a esos ajustes,
mas 101 pruebas afectadas despues. Productor nuevo con protocolo vinculado,
`runtime_data/simulator_delivery_20260910/market_independent_v3_bound/`, y
comparacion vigente `market_factual_comparison_v2.json`. No utilizar la
comparacion v1 como resultado de la version final.

Continuar desde `../development/2026-09-10-parallel-simulator-delivery.md` y
`2026-09-10-simulator-delivery-inventory.md`: D1 cerrado y 15 requisitos de
entrega aun abiertos, no 15 errores. M7 (ejecucion parametrizada de reglas
propias) y S1 (conexion del perfil al buscador) son codigo offline autorizado;
solo ejecutar la busqueda masiva requiere todavia revision conjunta.
No se ha hecho commit ni push de esta investigacion ni otro reinicio de VM.
