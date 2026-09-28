# Continuacion Nocturna: Ejecucion Y Recuperacion

Continuacion posterior: [recuperacion automatica del trabajador huerfano y
compatibilidad venv](2026-09-21-parent-death-recovery.md). El bloqueo de
disponibilidad indicado abajo queda reparado localmente con evidencia nueva;
los snapshots, resultados y hashes de este informe siguen siendo historicos.

## Alcance Y Estado

Continuacion autorizada del 20/09 UTC al 21/09 Europe/Madrid. Base local
`7415941a410d18288fa4e08da76809f70b125efa`, rama
`feature/gold-555-live-trial`. Se preserva el checkout sucio. No hay commit,
push, despliegue, reinicio remoto ni orden real. Revision directa de esta
conversacion, no revision independiente. No se seleccionan estrategias.

Este informe continua el [informe anterior](2026-09-20-night-recovery-validation.md).
Sus resultados historicos y hashes no se reescriben como si fueran actuales.
La revision conjunta y autorizacion de publicacion siguen pendientes.

## Defectos Y Correcciones

- Arranque: `_run_main` utilizaba un modulo no importado en ese ambito.
  Se importa explicitamente. La prueba ejecuta el arranque real con fronteras
  externas ficticias; tambien reprodujo monitores actuando antes de recuperar.
- Barrera de recuperacion: primero se reconstruyen estado, cola y peticiones
  de cierre; despues se recuperan entradas tardias y se activan monitores.
  Un error de recuperacion no libera la cola. Un spool corrupto no se descarta
  ni se reemplaza silenciosamente durante este arranque protegido.
- Propiedad nativa: lock de SO retenido por el trabajador durante toda su
  vida. El backend nativo impone una ruta comun por usuario de Windows,
  incluso entre checkouts/sesiones. Una segunda inicializacion queda excluida.
  Esta restriccion es deliberadamente mas conservadora que un lock por cuenta.
- Padre muerto: el hijo puede seguir dentro de una llamada nativa. La prueba
  mata un padre ficticio y comprueba que otro trabajador no inicializa mientras
  vive el anterior. Tras terminar ese hijo de prueba, la recuperacion convierte
  sus DISPATCHING en UNKNOWN, filtrando por cuenta; redelivery no repite envio.
  **La prueba termina explicitamente el huerfano: no certifica su limpieza
  automatica por el watcher en una muerte forzada del padre.**
- Disco: consultas de entradas congeladas, liberacion de reservas, proyeccion
  y recuperacion de acciones pasan fuera del bucle principal. Cancelar una
  preparacion no libera capacidad hasta que su escritura real termina;
  antes permitia acumular trabajo pese al limite configurado.
- Cola persistente: las mutaciones permanecen en el bucle propietario y la
  escritura de una copia de datos va al ejecutor de almacenamiento. Se esperan
  flush/fsync y reemplazo antes de acusar recibo. Cancelaciones repetidas no
  permiten que otra escritura adelante a la anterior. No se presenta esto
  como garantia frente a cualquier fallo fisico del disco/controlador.
- Cada accion/revision de cola debe estar persistida antes de enviarse, tambien
  en la comprobacion inmediata al dispatch. Una escritura fallida conserva
  el archivo anterior y no autoriza la revision nueva. Se mantienen SL/TP
  conjuntos de Gold y el retorno del stop efectivo mas protector.
- Una escritura fallida al terminar una accion ya no mata el runner. Se
  reintenta con espera de cinco segundos mientras hay trabajo; el heartbeat
  expone dirty/error/pendientes sin persistir. Si la cola queda vacia y falla
  su ultimo checkpoint, queda el spool anterior y el error visible; otra
  escritura/reinicio debe conciliarlo. No hay un escritor infinito separado.
- Respuesta tardia: la ruta durable podia consumir como DONE una revision
  nueva al recibir la respuesta de la anterior. Se congela el intento y solo
  se confirman sus niveles. La revision nueva permanece pendiente. Incluye
  el caso donde solo se aplico TP y el SL sigue esperando, sin inventar un SL
  confirmado. Control adicional con proceso real y broker ficticio: exactamente
  dos envios, primero niveles antiguos y despues los nuevos.
- Prioridad: cierres/protecciones adelantan a entradas en espera, con una
  plaza adicional reservada y una lectura despues de cuatro operaciones si
  hay consultas esperando. No se interrumpe una llamada nativa en curso.
  Politica operativa: `management-first-read-burst4-reserve1-v1`; se registra
  al arrancar y en el snapshot. La simulacion debe representar este arbitraje.
- Si recuperar/instalar el servicio falla despues de crear el trabajador,
  el arranque lo cierra aunque todavia no este asignado al propietario global.
  Incluye cancelacion y fallo al registrar evidencia de arranque.
- Vigilancia nativa independiente de Telegram: trabajador muerto o transporte
  activo atascado cierra el propietario y termina la sesion por la via
  recuperable del watcher. `BOT_MT5_OWNER_STALL_SEC=30` por defecto, comprobacion
  cada 0,25 s. Es un umbral de emergencia, no un objetivo aceptable de latencia
  de trading. No confunde un mercado quieto con una llamada bloqueada.
  La orden interrumpida queda UNKNOWN, no se interpreta como no enviada.

La migracion asincrona modifica fronteras de intercalado. No cambia lotajes,
niveles economicos ni seleccion de estrategia, pero exige contraste de orden
de decisiones/ejecucion en E5. No se da por demostrada la paridad por compartir
codigo ni porque la bateria unitaria pase.

## Verificacion

Python local aislado 3.11.9, pytest 9.1.1. No es una replica exacta de todas
las dependencias ni de los recursos de la VM.

- Primera suite de esta continuacion: 6.282 aprobadas, un warning, 435,86 s.
- Tras ampliar persistencia: 6.295 aprobadas y un fallo, 448,94 s. El test
  `test_main_connects_telegram_before_background_shadow_recovery` lee el
  archivo con `inspect.getsource`; el archivo cambio mientras esa ejecucion
  retenia posiciones de linea antiguas. El test y su modulo pasan al releer
  la version estable. No se altero la prueba para ocultar el fallo.
- Controles recientes: 79 pruebas de spool/cola/heartbeat/arranque; 68 de
  supervision, revisiones tardias, spool y sombras, aprobadas. Las pruebas
  nuevas de API inicialmente fallaron por ausencia de esa API; se distinguen
  de los contraejemplos que reprodujeron el comportamiento defectuoso anterior.
- Suite final sobre codigo estable: **6.302 aprobadas**, un warning,
  442,92 s. Comando: Python 3.11 aislado, `-m pytest -q --tb=short
  --disable-warnings`. Sin editar codigo durante esta ejecucion.
- Auditoria nativa: baseline coincidente, cero modulos live con acceso MT5
  nativo directo. Analisis AST: 50 fronteras `persist_async` esperadas,
  ninguna llamada directa de enqueue sincronico dentro de funciones async
  de main/listener/monitor. Los helpers sincronicos conservados se invocan
  desde esas fronteras. Sintaxis Python 3.11 y `git diff --check` aprobados.
- Ensayo de 60 minutos sobre snapshot inicial: aprobado, 144.000 lecturas
  FOUND y 3.600 operaciones DONE (1.200 por tipo). Heartbeat p99 16 ms/max
  32 ms; lecturas p99 31 ms/max 250 ms; operaciones p99 94 ms/max 531 ms.
  Maximo observado en muestras de cola: tres; drenada al final y trabajador
  terminado. Informe `reports/e4-mixed-hour-20260920/result.json`.
  RSS padre 32.964.608 -> 62.230.528 bytes; hijo 20.500.480 -> 20.303.872.
  El padre incluye 554.016 muestras de heartbeat y 144.000 de lectura.
  Hashes del snapshot inicial sin cambios durante sus 3.600 segundos.
  **No es una hora de la version final**: el cliente inicial precede la
  reparacion de capacidad tras cancelacion y la medida de edad activa del
  transporte. El backend de pruebas tambien recibio despues otra clase de
  regresion. La version final tiene el ensayo adicional siguiente.
- Ensayo adicional de 600 segundos sobre snapshot final del transporte:
  aprobado, 24.001 lecturas FOUND y 600 operaciones DONE (200 aperturas,
  200 modificaciones y 200 cierres ficticios). Heartbeat p99 16 ms/max 32 ms;
  lecturas p99 16 ms/max 94 ms; operaciones p99 93 ms/max 140 ms. Maximo
  observado en las muestras de cola: dos solicitudes; drenada al final y
  trabajador terminado. Informe `reports/e4-mixed-final-20260920/result.json`.
  Hashes antes/despues coincidentes, incluida la version final del cliente.
  Memoria RSS del padre: 33.460.224 -> 38.187.008 bytes; hijo:
  20.611.072 -> 20.320.256. El medidor retiene muestras, por lo que este
  crecimiento del padre no demuestra por si solo una fuga del cliente.
  Resolucion declarada del reloj monotono: 15,625 ms.

Los ensayos usan exclusivamente procesos y cuentas ficticias, 40 lecturas/s
con cuatro productores y una operacion/s alternando apertura, SL/TP y cierre.
No procesan estrategia ni simulan una cuenta completa. No hay un pico real
medido que permita llamarlos pruebas al doble de produccion. Los informes
JSON conservan versiones, tasas, hashes, memoria, colas y latencias.
Ambos procesos de ensayo han terminado. No queda una prueba periodica,
automatizacion ni operacion ficticia ejecutandose en segundo plano.

## VM: Observacion Separada

20/09/2026 22:00:46 UTC, SSH solo lectura, sin importar la extension MT5:

- HEAD remoto `7415941a410d18288fa4e08da76809f70b125efa`.
- Bot PID 4712; launcher 4448, watcher 5260, terminal 5588.
- Heartbeat 22:00:41 UTC, reporta flat, cero posiciones/senales/entradas
  pendientes y profundidad de diario cero. No es consulta independiente al
  broker ni certificacion de ejecucion de ordenes.
- Memoria disponible 3.381.840 KiB, disco libre 19.613.249.536 bytes.
- No contiene estas correcciones locales. No se modificaron configuracion,
  procesos, originales, diarios ni retencion remotos.

## Condiciones Aun Abiertas

1. Revision de esta integracion y de sus nuevas fronteras asincronas.
   En particular, muerte forzada del padre con hijo huerfano: exclusion segura
   probada, recuperacion automatica de disponibilidad no cerrada. El lock no
   debe confundirse con un mecanismo de relanzamiento completo.
2. Ensayo operativo comparable a VM y pico de produccion medido. Medir tambien
   espera de almacenamiento antes de obtener transporte; el watchdog de 30 s
   cubre transporte activo, no todos los posibles bloqueos de SO/disco.
3. Telemetria/retencion y fallo de almacenamiento de extremo a extremo. No se
   han borrado ni rotado los originales. La evidencia de cola no equivale a
   una politica global de nuevas entradas frente a todo fallo de evidencia.
4. Contraste E5 amplio en ambos canales: mismas decisiones e inputs causales,
   entradas, volumen, flotante, MAE/MFE, drawdown y costes. Primero todos los
   controles historicos admisibles, despues seguimiento de los mecanismos
   sin cobertura suficiente. No solo 3086 o 28/08; no reabrir una busqueda
   masiva ni seleccionar ventanas por resultados para salvar diferencias.
5. Antes de publicar: revision conjunta, autorizacion actual, exposicion y
   pendientes frescos, transicion controlada y comprobacion del unico
   propietario/version/recepcion/gestion realmente activos.

## Identidad Local Estable

Hashes SHA256 capturados al inicio de la suite final, sin cambios posteriores:

| Archivo | SHA256 |
| --- | --- |
| `main.py` | `3d867d7b426949612b9ead1a439f9fd08f719989185f292e06e7259e138180f3` |
| `listener.py` | `6c85c68779dbb451d02a0060cba325fa1b10bbcb818a29589af7f5494d135216` |
| `pending_actions.py` | `bc498e68da068949a51de6fd3c82ca5a1a8ebcb83ff1717975a28df5b731701d` |
| `position_lifecycle_monitor.py` | `09f87a62be95857e0a9e71ea819efdd20000cea517cd064e79b9874ab0f52117` |
| `mt5_client.py` | `e0d0f8ec2bf9e0776a29a0dbb95249f192fe1c884457db62681ada6722b4a677` |
| `mt5_worker.py` | `4502193f144b33b033d9c2a7d94867fd6d51649a176d6bf7a6160166b377d4b6` |
| `mt5_owner_lock.py` | `6a2c129edf0f1b53c0cdf32bd48a504d8e423ee8a5c2ba57932580fd8e06c403` |
| `execution_intents.py` | `7c0e813d49d759ba2a7c5e86c41b7b0830b5b153d81abdb281fbbc4c4ac3f282` |
| `durable_execution.py` | `9658f355857502fb33865de9125865f8828fbf62f316c930899d29e223d4c229` |
| `durable_entry_execution.py` | `88e9d5963b99acd4fe5ce561110923e2eedbfaf18eca467353092db0df3a7d08` |
| `config.py` | `df07c140e56302b322429d04c1de51adaedb8074af033a5200ff165cfe7d7629` |
