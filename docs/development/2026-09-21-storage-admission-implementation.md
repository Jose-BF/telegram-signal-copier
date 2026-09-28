# Almacenamiento Y Admision: Verificacion Local

Objetivo activo: continuar hasta implementar el plan de ejecucion/simulacion,
sin convertir cada reparacion parcial en el final del trabajo. El bloque
anterior de muerte del padre produjo progreso verificado; no certifica el
resto del objetivo. No hay autorizacion nueva de publicacion o reinicio live.

## Evidencia Actual

- Journal confirmaba un append cerrado sin fsync. Tres regresiones fallaron
  antes de la reparacion; ahora hay barreras de sincronizacion en el escritor
  fuera del bucle. Confirmacion por recibo y flush no ocultan errores de sync.
- El servicio durable no aplicaba una prohibicion comun de entradas por fallo
  de evidencia. Regresion: se envio una orden ficticia pese al veto. Ahora
  compone ese veto con el guard existente inmediatamente antes del envio.
- PLACE_LIMIT se clasificaba como gestion a efectos de prioridad. Se agrupa
  con OPEN_MARKET mediante ENTRY_OPERATIONS; ambas rutas pasan el control
  de cierre prioritario y los vetos de evidencia por ambos canales.
- Main instala el guard de evidencia y muestra estado del diario en heartbeat.
  Los fallos de journal quedan bloqueantes para nuevas entradas durante la
  sesion; flush no los borra como si nunca hubiera faltado evidencia.
- Cada entrada confirma su solicitud exacta mediante fsync fuera del bucle,
  antes de esperar el transporte. Si falla o vence la espera, no se envia.
  El guard de salud y el guard original se comprueban de nuevo al despachar.
- La reserva existente de 2 GiB ahora bloquea nuevas entradas. Heartbeat
  renueva una observacion de disco caducable; no se consulta el disco en cada
  decision. Se suprimen snapshots opcionales, conservando contadores/huecos.
  Protecciones y cierres conservan su ruta por el ledger durable.
- Una pausa de captura pone las sombras activas en estado incompleto mediante
  el registro durable existente; un reinicio no las convierte en completas.
  Regresion reproducida y reparada en Dubai y Gold.
- 30 pruebas focales finales de journal, servicio, prioridad y sombra aprobadas
  en 8,94 s. Incluyen cola llena, sync lento real, timeout, error de sync,
  composicion de guards, configuracion live del servicio y batching de recibos.
  Suite general estable: **6.340 aprobadas, un warning, 460,89 s** en Windows,
  Python 3.11.9 y pytest 9.1.1. El warning de pandas ya existia. Comando:
  `python -m pytest -q --junitxml=reports/e4-storage-admission-20260921/pytest.xml -o junit_family=legacy`.
  SHA256 XML `0e63db86f3ed587ca8af36408c89bcfddb35287f13d04c5721be98d2c8fa82c0`.
  No se edito runtime Python durante esta corrida. La prueba PowerShell
  se anadio despues de recoger estos casos y se verifico por separado.

## Medicion Del Recorrido Completo

`reports/e4-storage-service-load-20260921/result.json`: 60 segundos con
40 lecturas/s y una operacion/s alternando entrada, proteccion y cierre.
Nuevo modo opt-in `--entry-evidence` del medidor existente: servicio durable,
diario aislado y fsync reales, pero broker completamente ficticio. Fuentes
congeladas por hash antes/despues. No toca originales ni conecta a MT5.

- 2.400 lecturas FOUND; 60 operaciones DONE (20 por tipo).
- 20 checkpoints de entrada confirmados; p99/max 32 ms.
- Bucle p99 16 ms/max 32 ms; operacion completa p99/max 93 ms.
- Cola maxima observada 2; diario y transporte vacios al acabar, cero fallos
  de escritura, trabajador/vigilante terminados. Diario de 21.754 bytes.
- Padre RSS 32.620.544 -> 33.492.992 bytes; incluye muestras del medidor.
- SHA256 del resultado:
  `317e06c27892b332a30cecdf65ec1305cb84b24ccbbb76f93c6cac8e28fcf91f`.

No es el ensayo de una hora al doble del pico real, ni mide red/broker o
recursos de la VM. Resolucion del reloj Windows: 15,625 ms. El timeout del
checkpoint y el del transporte son limites por fase, no un SLA combinado.

## Prioridad De Recuperacion

El configurador `tools/configure_vm_recovery.ps1` seguia omitiendo Priority,
reproduciendo la vuelta al valor 7 documentada en R05. Ahora fija 4.
Prueba ejecutando el script real con todas las operaciones de tareas y la
exportacion sustituidas por dobles, dos veces: fallo previo 7 != 4 y aprobado
tras reparacion. Conserva S4U, tres triggers e IgnoreNew. Evidencia separada:
`reports/e4-storage-admission-20260921/task-priority.xml`, 1 passed / 1,18 s.
SHA256 `151bde1bd29173f1fdaccaa3b082ed2a46b5bd4fcd0340fd62a41d692a2d31b7`.
La primera version del doble perdia la captura por ambito PowerShell; se
corrigio el doble antes de demostrar el fallo de prioridad. No se ejecuto el
configurador contra el Programador de tareas local ni remoto. No acredita
todavia prioridad Normal de procesos reales despues de reiniciar la VM.

## Trabajo En Curso

- [x] Reproducir y corregir la confirmacion sin sincronizacion durable.
- [x] Frontera de veto de entradas sin desactivar cierres ni SL/TP.
- [x] Confirmacion durable del intento de entrada antes de enviarlo, con espera
  asincrona y sin ocupar el transporte de protecciones durante E/S lenta.
- [x] Regresiones de cola llena, escritura lenta, error de sync, limite de
  espera, sticky failure, PLACE_LIMIT y composicion con guards anteriores.
- [x] Reserva de almacenamiento y politica de telemetria de alta frecuencia;
  no borrar/rotar originales ni romper cursores, checkpoints o lectores.
- [x] Medir checkpoint/transporte e integracion; suite general estable.
- [ ] Continuar E4 operativo y E5 amplio segun el plan original, conservando
  datos faltantes y capacidades no admitidas. No reemplazarlo con una muestra.
- [ ] Revision de integracion, autorizacion y comprobaciones live antes de
  publicar. No promover estrategias ni reactivar automaciones.

El objetivo sigue activo. Las 6.313 pruebas y la carga previas pertenecen a la
version anterior al bloque de almacenamiento; no validan retroactivamente
estos cambios. La retencion/rotacion compatible con lectores sigue abierta;
reservar disco y pausar no equivale a almacenar indefinidamente sin limites.
Si falla el ledger esencial tampoco se garantiza la gestion de abiertas.
Las pruebas de timeout de diario no certifican recuperacion ante cualquier
bloqueo del sistema operativo. Revision directa, no independiente.

## Fuentes De Runtime

Base `7415941a410d18288fa4e08da76809f70b125efa`, rama
`feature/gold-555-live-trial`, con cambios anteriores conservados.
Sin commit, push, configuracion remota, reinicio ni ordenes reales.
Tras las pruebas no quedan procesos de los ensayos/pytest/worker/guardian.
`git -c core.safecrlf=false diff --check` aprobado.

| Archivo | SHA256 |
| --- | --- |
| journal.py | fc8cc0e4edc34364a9117b8825e0f75e4c0c8e79e05540a0282774f43cd07e3c |
| durable_execution.py | 52b77cfef7de47aa737e85fb4475613378c14a6bbc1db1d5b57c971dd0ee452c |
| mt5_client.py | 009c6b689c892427d6788159533caef77e3b57a1e9676b538dca3bc73e4eac92 |
| mt5_trade_protocol.py | 20f2dc429b7a288b537c9f3877f3f5df824eb0c3219104b4102508e3048eb1d2 |
| main.py | e32e72240bee07fc49c6e307d1c8133970917f9cb4b437090d23f46f125c72c9 |
| strategy_shadow_runtime.py | 060d02f568936fc21ca243f0a2212152b230e214298fdcdc887616da002652aa |
| tools/configure_vm_recovery.ps1 | 5495ad61734c205cc3464bbbeb670a8a5e9fafc59efadec1c670cb8e5ea8866f |
