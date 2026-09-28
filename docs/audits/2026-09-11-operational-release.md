# Activacion De Reparaciones Operativas

Actualizacion posterior 07:40 Madrid: el usuario aclara que no autorizaba
repetir hoy las revisiones. Seguimiento Codex eliminado y las dos capturas
viernes v3 retiradas antes de ejecutarse, sin tocar bot/terminal. Recibo
`forward_friday_v3/cancelled_by_user.json`, SHA256
`38d710fa6553f22a454cf199892020fd9a270138f3429a598edaaa194be1dafb`.
La activacion del commit sigue completada; los estados Ready inferiores son
el checkpoint anterior, no tareas vigentes. Ver
`../development/2026-09-11-simple-progress.md` para seguimiento y limites de cierre.

## Peticion Y Alcance

El 11/09 el usuario pide activar lo que seguia sin publicar/en funcionamiento
en el bot, para medir nuevas operaciones. Se interpreta como autorizacion
para este bloque operativo pendiente, no para publicar la investigacion
local, cambiar estrategias/cuentas/lotes o iniciar una busqueda masiva.
La propuesta de estimar parametros queda separada en
`../development/2026-09-11-execution-assumptions.md`; no se calibra el control
prospectivo ni se amplian sus tolerancias durante esta actualizacion.

Habia cambios locales sin commit, no un ultimo commit ya creado e inactivo.
Dos correcciones de producto: exclusividad por instancia de senal al finalizar
el diario, con reintento tras fallo/cancelacion; y refresco de dinero del
observador cuando la cesta queda sin posiciones pero conserva entradas
elegibles. No cambian las decisiones de entrada/gestion del bot.

## Verificacion De La Entrega Minima

Copia limpia aislada en `telegram-signal-copier-operational-verify-20260911`,
base `892bc33c8f6c19be7248401ec3cf6a27ba3d0563`. Se copiaron solo los tres
archivos de producto y sus dos suites, conservando toda la investigacion en
el worktree original. La primera suite fallo al importar el auditor raw:
el commit anterior ya importaba `tools.audit_management_capture`, pero ese
archivo no estaba publicado. No era un fallo de los arreglos del bot.

Se incluyeron la dependencia existente y su suite, sin modificar su contenido
ni ampliar la publicacion al resto de investigacion. Regresion de empaquetado:
92 pruebas afectadas aprobadas tras reproducir la dependencia ausente en el
checkout limpio. Suite completa final: **3191 aprobadas, cero fallos, errores
u omisiones**, 355 fuentes estables, 212.41 segundos. Advertencias conservadas.
Es la suite de la entrega minima; no sustituye las 5093 pruebas del arbol
de investigacion completo, cuyas 425 fuentes permanecen iguales.

Evidencia: `runtime_data/operational_release_20260911/verification/` conserva
el fallo inicial; `verification_v2/start.json`, `final.json`, `suite.xml` y logs
retienen el resultado final. XML SHA256
`c29e886f9faff4e75761b1d432eef65c58524cc8f4c52c41732665f54f369cc7`.

## Commit Y Publicacion

Commit `d9037c5c1ac3b91814ec73a00278e6d6c7b52aad`, padre `892bc33c8`.
Incluye exactamente siete archivos:

- `listener.py`, `state.py`, `strategy_shadow_engine.py`.
- `tests/test_strategy_runtime_lifecycle_integration.py` y `tests/test_strategy_shadow_engine.py`.
- `tools/audit_management_capture.py` y `tests/test_audit_management_capture.py`.

Los siete archivos del indice se comprobaron contra la copia verificada y
sus hashes antes del commit. Push normal a `origin/main` confirmado, sin
force, cambios de estrategia o publicacion de datos/investigacion incidental.
El nuevo remoto fue verificado mediante lectura separada de su referencia.

## Guardas De Activacion

Dos consultas nativas propias de solo lectura, en sesion interactiva 2 y
terminal existente 6312, misma cuenta demo EUR. Cero posiciones, ordenes y
acciones pendientes. Helper basado en el ya verificado el jueves; solo cambian
fecha y prefijo de salida. No login ni ordenes, no se termina MT5.

La primera captura de 05:03:35 UTC caduco antes del gate de publicacion y
este rechazo continuar: no hubo push en ese intento. Se conserva, SHA256
`5081079b91bf20f8c26c16f20f68d355702af3d16f781de4f1aa879fe5f6ef4a`.
Segunda captura nativa de 05:05:50.555024 UTC, SHA256
`d1fa14f130919ad3973ec267732831f6d0145fae3304936ec7760153e2b24bd5`.
Gate a 05:05:55: nativa de 4.42 s, heartbeat de 8.77 s, cero exposicion,
senales y entradas pendientes, sin pausa ajena ni handlers activos y 892
limpio. El push solo se ejecuto al aprobar ese gate.

El supervisor existente vuelve a comprobar exposicion despues de pausar la
recepcion de nuevas entradas y esperar los handlers. A 05:07:14 UTC el log
confirmaba deteccion del commit, parada limpia del bot y conservacion de los
registros antes de sincronizar. Terminal y supervisor originales conservados.
Ese checkpoint inicial no probaba aun el arranque; se verifico despues.

Nuevo bot PID3068, padre4468, sesion2 y creacion 05:07:28.315 UTC. MT5
conectado a 05:07:49 y ambos canales Telegram escuchando a 05:10:14;
monitor conectado a 05:10:15. Supervisor4468/launcher4824 y terminal6312
conservan su identidad anterior. Consulta nativa posterior a 05:12:32.323705
UTC: misma cuenta/terminal, cero posiciones, ordenes y acciones pendientes.
SHA256 `ea97ee7a4ce8dc33b9b6e87e1bb265ef68c06c2c8d09ca9c1beeff9537b190d2`.
Los tres auxiliares nativos propios terminaron con cero y se retiraron a
05:21:37 UTC, tras cotejar XML y hashes. Archivos y recibos conservados en
`completed_native_tasks_cleanup.json`; no se termino ningun proceso del bot.

Lectura independiente a 05:17:28 UTC: d903 limpio, sin pausa, heartbeat
05:17:15 con cero posiciones/senales/entradas pendientes. Los siete blobs
instalados coinciden con el commit. Los hashes de bytes Windows CRLF se
conservan aparte; no se confunden con hashes locales LF.

## Captura Futura

La activacion cambia la identidad live; no sirve mantener silenciosamente el
protocolo de viernes v2 ligado a 892 y al bot anterior. Se preparo un protocolo
NUEVO v3 antes de la ventana, conservando el v2 y sus pruebas:
`runtime_data/calculator_delivery_20260910/forward_friday_v3/frozen/protocol.json`.
SHA256 `2b41be5f91d3680c0bc5a5df6a69b6ad01c2c97dd96f1af88fd2ceabd6f08208`.
Ventana 13-15 UTC / 15-17 Madrid, mismas reglas, presupuestos y tolerancias,
doce productores comprobados, 425 fuentes iguales a las 5093 pruebas.
Copia ejecutable SHA256 `0cf04a098a00111702f1cccc1b350b82f4285c9e75fda3a761ae7ae9931f5ca8`.

Las dos tareas v2 se retiraron ANTES de ejecutarse, con XML y motivo en
`friday_v2_retired.json`; no fallaron ni se borraron sus fuentes. Registradas
y comprobadas en Windows las nuevas tareas:

- `Codex-Simulator-20260911-1300-pre_window-2b41be5f91d3`: 12 UTC / 14 Madrid.
- `Codex-Simulator-20260911-1300-final-2b41be5f91d3`: 15 UTC / 17 Madrid.

Ambas Ready, Interactive/Limited, sin repeticion ni ejecucion atrasada,
caducidad a los dos minutos y limite de doce minutos de ejecucion. Mismo
ancla final de jueves f9cf, precaptura completa y cierre condicionado al
recibo exitoso de pre; no fallback ni omision de bytes. Coordinador nuevo
ligado a PID3068/creacion1789103248315. Solo cambian identidades, no reglas:
`capture_binding_verified.json` y 60/60 comprobaciones nuevas lo verifican.
Helpers fuera del checkout live, en
`C:/Users/bot/codex-control/simulator_friday_20260911_2b41be5f91d3`.
Recibos locales en `forward_friday_v3/registered_tasks.json` y
`registration_readback.json`. SHA del primero:
`885d6de70326ae4af2503b5b5c608278ab681f2c9e4ed022f811249ea4c569e7`.

## Cierre Y Limites

`runtime_data/operational_release_20260911/closeout_verified.json` aprobado
a 05:22:48 UTC: HEAD local/remoto/instalado concordante, siete fuentes
instaladas, suite minima, recibos y tareas verificados. Los 437 archivos
preservados (425 fuentes/wrappers y doce productores) siguen iguales.
Investigacion y documentacion siguen locales, sin publicar. El recolector
corregido se usa como auxiliar de captura, no se incorpora al bot live.

La activacion operativa esta completada. Las capturas de hoy AUN NO se han
ejecutado y no se declaran cerradas las comprobaciones naturales. Preparar
primero la simulacion desde mensajes/precios y comparar despues con MT5.
Si no hay casos suficientes, conservar el resultado incompleto sin inventar
operaciones ni ampliar retrospectivamente el protocolo. No se calibraron
medias, no hubo busqueda de estrategias ni cambio de cuenta/lotes/politica.
