# Activacion controlada de la reparacion en demo

Estado: commit y push realizados; version activa verificada en la demo.
Commit: `9765758ed84bcdcb5e2ca690ec954a2dce154bc6`.
Validacion prospectiva pendiente. Incidencia previa de publicacion de telemetria
registrada al final; se resolvio despues, en el mantenimiento separado descrito
en `2026-09-08-operational-cleanup.md`, no mediante este despliegue.
El usuario autorizo implementar la puesta en funcionamiento despues de revisar
el alcance y las limitaciones de la reparacion local.

## Fronteras

- No cambiar estrategias, parametros, lotaje ni cuenta.
- No activar una actualizacion con posiciones, senales o entradas pendientes.
- Una incertidumbre impide reiniciar; nunca equivale a una cuenta plana.
- Conservar evidencia antigua y nueva por separado. Arrancar no certifica
  resultados prospectivos ni rentabilidad.

## Evidencia previa

- Rama local: `feature/gold-555-live-trial`; base `b4426e6`.
- VM: rama `main`, misma base, checkout limpio en la primera comprobacion.
- Python VM: 3.11.9; MT5 5.0.5735, Telethon 1.43.2, google-genai 1.73.1,
  pandas 3.0.2 y pyarrow 24.0.0. La verificacion local anterior uso Python 3.14.2.
- Cuenta observada a 2026-09-07T22:18:05Z: demo, EUR, cero posiciones y cero
  ordenes pendientes, terminal conectado. No sustituye la comprobacion final
  inmediatamente anterior a publicar.
- Preflight en clon separado bajo `codex-control`, sin `.env` ni sesiones del
  bot; dependencias de pruebas instaladas solo en su entorno virtual aislado.
  Mutaciones e inicializacion MT5 bloqueadas, lecturas nativas sustituidas por
  respuesta no inicializada; conexiones externas bloqueadas. Solo se admite
  loopback para el event loop de Windows y el bloqueo de instancia de pruebas.
- La primera recogida de tests detecto dos dependencias de investigacion ausentes
  (`numba`, `matplotlib`); se anadieron al entorno de pruebas, no al bot activo.
  La DLL nativa `_typeconv` de Numba siguio sin cargar: por eso la suite completa
  de investigacion se verifico localmente, no se declara pasada en la VM.

## Defecto adicional de activacion

La revision encontro que el heartbeat v2 no contaba esperas preentrada Gold 555
ni planes de zona ejecutables. Sus bucles tampoco participaban en la pausa y
el drenaje de handlers. Cero posiciones no bastaba para autorizar el reinicio.

Se anade el contrato v3 con `pending_entry_count` y validacion estricta;
un contador ausente, invalido o desconocido bloquea la activacion. Se protegen
los procesadores de entradas autonomas con la puerta de actividad existente.
Las regresiones iniciales reprodujeron diez fallos. La revision tambien detecto
que cancelar un consumidor podia publicar inactividad antes de terminar el hilo
nativo MT5. El procesador interno retenido y protegido con `asyncio.shield`
conserva la actividad hasta terminar tanto el hilo como el procesamiento de
estado. Un plan `triggered` no consumido y ejecutable sigue contando.
Regresiones y revision independiente cerradas antes de publicar.

## Verificacion final previa

- Suite local final: 3025 tests, 624 warnings, cero fallos y errores, 111.36 s.
- VM aislada, Python 3.11.9: 1256 tests operativos en 63 archivos, cero fallos y
  errores, 47.95 s. Incluye conexiones, ejecucion, recuperacion, sombras,
  persistencia, clasificador y supervisor; no equivale a la suite de research.
- Los 33 archivos del manifiesto de fuente coinciden con los probados. El
  checkout activo se comparo normalizando solo finales de linea CRLF/LF.
- Migracion local del prefijo congelado: conserva los 24 pares, sus posiciones,
  dinero e identidades; recuperacion repetible. Los bloqueos antiguos siguen
  presentes y no se convierten en certificacion prospectiva.
- Evidencia: `runtime_data/foundation_deploy_20260908_2204/`, incluidos
  `pytest_local_final_v3.xml`, `pytest_vm_v7.xml`,
  `preflight_environment_v7.json`, `source_manifest_v3.json` y
  `result_migration_final.json`.

## Criterios de salida

- [x] Regresiones del listener y revision del defecto de activacion cerradas.
- [x] Suite local completa sobre el codigo final.
- [x] Preflight operativo aislado con Python y dependencias efectivas de la VM.
- [x] Pausa inicial, drenaje y reconstruccion de entradas pendientes sin saltar
  registros malformados; evento durable del journal posterior al drenaje.
- [x] Cuenta demo/EUR verificada, cero posiciones y cero ordenes pendientes.
- [x] Commit delimitado, push autorizado y revision remota confirmada.
- [x] Nueva version en VM, heartbeat v3 fresco, MT5 y Telegram conectados,
  pausa retirada y diarios de sombra conservados.

La primera activacion necesita una comprobacion adicional bajo pausa porque
el supervisor antiguo todavia no incluye el contador nuevo. Ante un fallo se
retira la pausa y se conserva el bot anterior. No se fuerzan cierres ni se
descartan planes para permitir el despliegue.

## Activacion observada

Tiempos UTC; en Madrid corresponden al 8 de septiembre, dos horas despues.

- 22:53:46: pausa inicial y handlers drenados. El puente del supervisor antiguo
  reconstruyo los planes del journal completo, espero una barrera durable FIFO
  de la misma sesion y verifico cero intentos de apertura de zona en esa sesion.
  La consola retenida completa no contenia errores de escritura del journal.
- 22:54:13: puente listo, cero entradas pendientes. Evidencia inmutable:
  `activation_ready_ready2.json`.
- 22:54:47: lectura del terminal ya existente, misma identidad de cuenta demo,
  EUR, cero posiciones y ordenes pendientes; `demo_final.json`.
- 22:55:37: comprobacion final de pausa propia, handlers, proceso, heartbeat,
  cuenta y ramas. Push ordinario `HEAD:main`, sin forzar ni cambiar parametros.
- 22:56:56: nueva sesion `session_8ad7e01f99464ef1b8a6d1f294d6b9a1`.
- 22:57:34: `startup_version_confirmed`, Telegram y MT5 conectados.
- 22:59:24: heartbeat v3 fresco, bot PID 6796, supervisor PID 6044, mismo
  terminal PID 8968, una sola sesion nueva, pausa retirada. Contadores de
  posiciones, senales abiertas y entradas pendientes: todos cero.
- Recuperados 276 estados de sombra sin desactivar el runtime. Esto no
  certifica sus cohortes antiguas ni constituye una senal prospectiva nueva.
- Se verificaron por SHA256 los prefijos anteriores de eventos (187799703
  bytes) y consola (164676870 bytes): ambos conservados exactamente.
- Gold conserva politica `555`; sombra habilitada. No se modifico `.env`,
  cuenta, lotaje, parametros ni estrategias. Las tareas interactivas temporales
  de lectura de cuenta se retiraron; el bot usa su supervisor existente.
- Evidencia local y en la VM: `activation_verified_final1.json`.
- Segunda comprobacion a 23:02:34 UTC: misma sesion y procesos, heartbeat v3
  fresco y plano, 33 fuentes coincidentes y prefijos intactos. Remoto `main`
  confirmado en el mismo commit; `activation_verified_final2.json`.

## Incidencia operativa previa

La publicacion remota de telemetria sigue bloqueada por
`runtime_data/.telemetry/publisher-repo/.git/index.lock`. El error ya aparecia
antes del push y vuelve a aparecer despues. El archivo tiene cero bytes y una
fecha anterior al despliegue; no habia procesos `git.exe` en la inspeccion
puntual. Esa observacion no prueba por si sola propiedad o abandono del lock.
No se elimino ni se altero este checkout auxiliar. Los checkpoints locales
continuan, y los prefijos de eventos y consola estan preservados. Debe repararse
la publicacion con comprobacion de concurrencia antes de depender de la copia
remota para el seguimiento prospectivo.

Actualizacion posterior: recuperacion completada a 23:30 UTC; envios automaticos
posteriores correctos y reconstruccion remota de los registros operativos
comprobada contra la VM. Ver `2026-09-08-operational-cleanup.md`.

## Trabajo posterior

El bloqueo de publicacion se cerro en el mantenimiento posterior. Validacion
prospectiva con senales nuevas; cobertura de mensajes/ediciones y
ticks de 2026; calibracion ampliada de latencia, fills y costes; presupuesto y
validacion no usados para seleccionar parametros. Despues, investigacion de
Canal 2 (Gold) y finalmente Canal 1 (Dubai), sin promocion automatica.
El usuario aplazo expresamente las simulaciones y la busqueda de rentabilidad
para una fase aparte: no iniciar esos pasos automaticamente.
