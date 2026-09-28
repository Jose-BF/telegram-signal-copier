# Recuperacion Tras Muerte Del Proceso Principal

Continuacion local del bloque de disponibilidad E4. No modifica estrategias,
no publica cambios y no certifica equivalencia economica con el simulador.
Base Git `7415941a410d18288fa4e08da76809f70b125efa`, rama
`feature/gold-555-live-trial`, con los cambios anteriores conservados.

## Defecto Reproducido

El lock exclusivo impedia dos propietarios MT5, pero no recuperaba por si solo
la disponibilidad: al matar el padre durante un envio ficticio con bloqueo
nativo de 60 segundos, el trabajador seguia vivo. La regresion que exige su
salida automatica fallo por timeout de cinco segundos antes de la reparacion.

No se resuelve borrando el lock, repitiendo una orden incierta o matando un
arbol entero de procesos. El terminal puede tener su propio ciclo de vida.

## Mecanismo Local

- El trabajador inicia un vigilante independiente antes de importar el backend
  o inicializar MT5 y espera su confirmacion. Un hilo dentro del trabajador no
  serviria frente a una extension nativa reteniendo el GIL.
- El vigilante comprueba identidad de creacion y parentesco. En Windows abre
  referencias persistentes del SO: la terminacion actua sobre ese objeto, no
  sobre un numero PID que podria pertenecer despues a otro proceso. Solo tiene
  permiso de terminacion sobre el trabajador, nunca sobre el padre.
- Si muere el padre, termina exclusivamente el trabajador. Si muere el
  trabajador, sale el vigilante. El terminal y otros descendientes se conservan.
- Su grupo de procesos esta separado del Ctrl-Break que emplea el watcher.
  El intervalo de comprobacion es 100 ms; no es una garantia de latencia bajo
  suspension de VM, saturacion del SO o terminacion bloqueada por E/S.
- No se crea una tarea periodica, servicio persistente ni vigilancia de mercado.
  Hay un proceso ligero adicional por trabajador, sin mensajes ni log por tick.
- El lock sigue siendo la barrera final hasta que el propietario termina.
  Los intentos DISPATCHING recuperados siguen pasando a UNKNOWN, sin reenvio.
- Si el vigilante desaparece, el trabajador rechaza la siguiente solicitud.
  El watchdog anterior sigue cubriendo una llamada nativa activa atascada
  mientras el padre continua vivo.

Referencias de semantica Windows:
[objetos de proceso](https://learn.microsoft.com/en-us/windows/win32/procthread/process-handles-and-identifiers)
y [TerminateProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess).
La rama Linux utiliza pidfd, no kill por PID; no se ha validado en Linux en
este equipo. Las plataformas sin referencia segura fallan antes del backend.

## Entornos Virtuales Windows

La revision directa tambien reprodujo un proceso intermedio del lanzador venv:
el PID de Popen no era el PID real del interprete. La comprobacion estricta
del parentesco rechazo ese arranque en dos regresiones nuevas. La correccion
conserva el entorno virtual y da propiedad directa sobre el trabajador y su
vigilante. Usa el mismo mecanismo de `multiprocessing/popen_spawn_win32.py`
de CPython 3.11: ejecutable base y `__PYVENV_LAUNCHER__`, sin reimportar main.
Dos pruebas en un venv real verifican arranque, prefijo del trabajador, cierre
normal y muerte forzada del padre durante un envio nativo ficticio.

## Verificacion

- Regresion inicial: fallo por timeout al esperar la terminacion automatica
  del trabajador tras matar el padre. Tras la reparacion, reinicia un nuevo
  propietario y el envio incierto queda UNKNOWN: un unico `send` registrado.
- Pruebas focales: 69 aprobadas en 40,21 s sobre Python 3.11.9/Windows.
  Incluyen muerte del padre en inicializacion nativa, reposo y envio; rechazo
  de identidad incorrecta; guardian perdido antes de leer o enviar; cierre
  normal; segundo propietario bloqueado; terminal ficticio descendiente
  conservado; venv real y regresiones anteriores del transporte/supervisor.
  La orden no enviada al perder el vigilante conserva PREPARED, no se
  etiqueta falsamente como DONE ni como envio incierto.
- Primera suite general: 6.310 aprobadas, 448,98 s. Es evidencia intermedia
  anterior a la reparacion del lanzador, no la version final. El XML conserva
  nueve avisos del formato JUnit y el aviso pandas ya existente.
- Suite final: 6.313 aprobadas, un aviso pandas preexistente, 448,73 s.
  XML separado en `reports/e4-parent-guard-final-20260921/pytest.xml`.
  Once hashes de fuentes y pruebas coinciden antes/despues de la suite.
  El formato JUnit legacy admite las propiedades registradas por las pruebas,
  sin filtrar avisos de ejecucion.
- Despues de la suite se reforzo solo la limpieza de dos fixtures: un PID no
  se incorpora a la lista de terminacion hasta validar su fecha de creacion.
  Sus 14 regresiones pasan en 11,07 s, con XML separado
  `reports/e4-parent-guard-final-20260921/cleanup-regressions.xml`.
  No cambio el codigo de ejecucion ni se relajaron aserciones de negocio.
- Auditoria nativa: baseline coincidente, cero modulos live pendientes de
  migracion. El vigilante no importa MT5 ni ordena operaciones.
- Consulta final de procesos de este checkout: ningun trabajador, vigilante
  o padre de pruebas restante. `git diff --check` correcto con la configuracion
  de finales de linea del repositorio. Sin jobs recurrentes nuevos.

Comandos focales: `python -m pytest -q tests/test_e4_worker_venv.py
tests/test_e4_worker_lifetime.py tests/test_e4_owner_lock.py
tests/test_e4_owner_supervision.py tests/test_mt5_read_client.py
tests/test_mt5_trade_client.py tests/test_e3_c_runtime_startup.py`.
Suite final: `python -m pytest -q -o junit_family=legacy
--junitxml=reports/e4-parent-guard-final-20260921/pytest.xml`.

## Carga Y Coste Del Vigilante

`python tools/probe_mt5_mixed_load.py --seconds 60 --read-rate 40
--directory reports/e4-parent-guard-mixed-20260921`.

- 60 segundos, 2.400 lecturas FOUND, 60 operaciones DONE: veinte aperturas,
  veinte cambios SL/TP y veinte cierres ficticios.
- Heartbeat p99 16 ms/max 32 ms; lecturas p99 16 ms/max 31 ms; operaciones
  p99 y max 78 ms. Reloj declarado con resolucion de 15,625 ms.
- Cola observada maxima dos, drenada al terminar. Trabajador y vigilante
  terminados, sin pruebas periodicas ni procesos de carga pendientes.
- Vigilante: 16.678.912 bytes RSS inicial/final/pico (15,91 MiB), 0,046875
  segundos de CPU durante los 59,016 s entre primera y ultima muestra.
  No incluye consumo global de todos los procesos del sistema o del broker.
- Identidad de fuentes antes/despues coincidente; el informe incluye tambien
  `mt5_worker_lifetime.py`. JSON conservado en
  `reports/e4-parent-guard-mixed-20260921/result.json`.

SHA256 de evidencias conservadas:

- Carga: `819c2011ca0598ccf0812466669b8c17800c341ce2f1a1125959d4d2c37b3648`.
- XML global final: `af8f08a3aabcd807b005302bf86bc3091bb4dd53077a18a742f6a87d21ba7ee5`.
- XML limpieza: `ab094f3a544170813f7eacd2046a9a261d1d7e465f397de54a4051e88233f512`.

No es una prueba de una hora de esta nueva version, ni del pico real duplicado,
ni de hardware equivalente a VM. Los ensayos previos de una hora y diez
minutos siguen intactos y pertenecen a sus snapshots anteriores.

## Identidad De Ejecucion

SHA256 del codigo durante el ensayo final de carga y al comenzar la suite:

| Archivo | SHA256 |
| --- | --- |
| `mt5_client.py` | `b170fcccd76ca8348e366b95c69eb3d7a4b08c128fea2bd57fe48c9cf95fb585` |
| `mt5_worker.py` | `d362234cf4b6d2a2cf3e517a91da5c935b877332fbe2e20acd517de296ce2324` |
| `mt5_worker_entry.py` | `7f8453d3ef9f00cfdc2e41fb9583d81d3703f737f65d6f62c99f7200128637e0` |
| `mt5_worker_lifetime.py` | `458383e73f3ab46152afed976cda53c62c7cdefd46ca6ea0bee3195ad306b4b0` |
| `tools/probe_mt5_mixed_load.py` | `6b40f3a1821078e2893df77b9f5e8d504ff934a5b6578b3cc71521e90ef2ee61` |

## Limites Y Siguiente Paso

Esta reparacion libera el propietario huerfano; no sustituye la politica de
relanzamiento del watcher ante errores que requieren intervencion. No se
modifican sus codigos de parada deliberada ni se fuerza un reinicio en vivo.

No garantiza recuperacion si mueren a la vez padre y vigilante dejando vivo
al trabajador, ni soluciona todos los posibles bloqueos de disco o del SO.
El lock mantiene la exclusion en esos casos. Sigue pendiente la verificacion
operativa comparable a VM, la retencion de telemetria y el contraste amplio
de decisiones y riesgo entre realidad y simulacion de ambos canales.

La revision de este bloque es directa, no independiente. No se ha consultado
de nuevo la VM ni cambiado su codigo, configuracion o procesos en este bloque.
Antes de publicar hacen falta revision conjunta, autorizacion actual y
exposicion/pendientes frescos. No hay commit, push ni ordenes reales.
