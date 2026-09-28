# E3-A: Cliente Y Consultas Aisladas

Entrega local del 20/09/2026, rama `feature/gold-555-live-trial`, HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`. Sigue el
[plan E3](2026-09-20-e3-integration-plan.md). No activa ninguna ruta live.
Revision independiente pendiente; no es aprobacion de produccion.

Estado posterior: la primera revision encontro F1-F4. La
[reparacion y su evidencia](2026-09-20-e3-a-repair-results.md) sustituyen la
identidad de codigo y las metricas finales de esta entrega original. E3-B sigue
pendiente de una nueva revision independiente.

## Implementacion

- `mt5_read_protocol.py`: contrato JSON de consultas, versionado y tipado.
  Se mantiene separado del protocolo durable de operaciones: una consulta
  repetible no crea una intencion de compra/cierre ni accede a SQLite.
  Reutiliza `LookupState` e `ImmutableJsonMapping` de E1-E2 sin cambiarlos.
- `mt5_worker.py`: propietario del backend dentro de un proceso `spawn`.
  Importacion nativa diferida, fabrica obligatoria e inyectable, inicializacion,
  cuenta/servidor y simbolos esperados, shutdown y captura inmediata de
  `last_error` por cada llamada. Sin imports de executor/listener/main/state/journal.
- `mt5_client.py`: interfaz async y un hilo propio exclusivamente para IPC.
  Cola acotada antes del executor; sin fallback nativo ni reintentos implicitos.
  El hijo es quien puede retener el GIL con una llamada nativa, no ese hilo.
- `tools/audit_mt5_native_access.py`: inventario AST reproducible de imports,
  funciones/aliases y referencias como `executor.mt5`, tambien callbacks y
  `getattr`. Baseline temporal en `mt5-native-access-baseline.json`.
- `tools/probe_mt5_read_isolation.py`: ensayo offline reproducible de bloqueo
  nativo, con control negativo en el proceso padre y salida identificada por hashes.

E1-E2 no se modifican: `mt5_protocol.py`, `mt5_gateway.py`,
`execution_intents.py`, `execution_latency.py` e inventario v6 conservan sus hashes.
El transporte de lecturas no se conecta en paralelo al gateway de ordenes en
produccion. E3-B debe integrarlos bajo el propietario operativo previsto.

## Contratos Y Limites

Operaciones admitidas: cuenta, terminal, simbolo, tick, posiciones, ordenes,
historial de deals/ordenes por intervalo, barras por posicion/cantidad, ticks
desde fecha/cantidad y calculos de margen/beneficio. Inicializacion y shutdown
son operaciones de ciclo de vida, no consultas publicas repetibles del cliente.
`order_send`, login arbitrario, nombres dinamicos, `eval`, `last_error` remoto
independiente y peticiones con parametros desconocidos se rechazan.

`None`, error nativo, cuenta equivocada/desconectada, timeout, fallo de transporte,
exceso de tamano o shape invalido producen UNKNOWN sin datos utilizables. Solo
una coleccion vacia obtenida correctamente produce EMPTY. Un beneficio calculado
igual a cero es FOUND, no EMPTY. No truncar historial para simular completitud.

- Por defecto 8 plazas, incluyendo la consulta que esta en curso; configurable
  entre 1 y 128. La saturacion retorna UNKNOWN con motivo propio inmediatamente.
- Cancelacion y timeout del solicitante no liberan su plaza hasta drenar la
  respuesta o cerrar el trabajador. Una lectura en cola caducada no se envia.
- Una respuesta tardia no se reaprovecha como snapshot actual ni para otra
  peticion. Se verifica peticion, operacion, sesion, PID y orden del reloj local.
- El timeout incluye cola y arranque. Cerrar conserva la propiedad de un spawn
  en curso; no abandona un hijo porque el solicitante haya cancelado la espera.
- Cada cuenta se comprueba antes y despues de la lectura. Un cambio de cuenta
  invalida el resultado y deja al trabajador sin estado ready.
- Requests <=16 KiB; respuestas <=512 KiB; <=10.000 registros; historial <=24 h
  por peticion. Limites menores configurables para respuestas y registros.
  Conversion recursiva con presupuesto; records y arrays numpy pasan a JSON
  sin importar numpy en el proceso padre. Los datos retornados son copias.
- La frescura se calcula conservadoramente desde el inicio de consulta, no
  desde su finalizacion; incluye el tiempo nativo y de las comprobaciones.
  Se preservan tiempo UTC de finalizacion y tiempos nativos como `time_msc`.
  La edad del snapshot no acredita que el ultimo tick del broker sea reciente.
  Sesiones/relojes son locales: no reutilizar frescura tras reinicios.

La API nativa de historial devuelve todos los registros del intervalo antes
de que podamos limitar su serializacion. El limite temporal y la respuesta
acotada NO garantizan memoria nativa acotada: medirla y supervisarla en E4.
Referencia del contrato de retorno: [MetaQuotes history_deals_get](https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py)
y [copy_ticks_from](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksfrom_py).
`copy_ticks_range` y lecturas sin limite temporal/cantidad quedan fuera de esta
interfaz; sus consumidores legacy siguen pendientes de migracion explicita.

## Verificacion

Artefactos locales: `runtime_data/e3_a_20260920/`.

- Primer bloque: 56 pruebas nuevas aprobadas. Luego se reprodujo un fallo real
  con spawn lento: el plazo de arranque solo limitaba initialize. Se corrigio
  incluyendo la espera de spawn, reteniendo ownership hasta el cierre, y se
  agrego el control de plazos distintos de solicitantes concurrentes.
- Bloque intermedio con E1-E2: 128 aprobadas en 21,10 s, `focused_initial.xml`.
  Es evidencia intermedia; controles agregados despues se verifican en el cierre.
- Ensayo intermedio nativo 60 s: 60,010 s de llamada; p99 16,57 ms y maximo
  17,33 ms del bucle padre. Control negativo: 401,87 ms. Artefacto
  `native60_python314.json`. Cambios posteriores de plazos/serializacion/frescura
  requieren el ensayo final con sus hashes; no reutilizarlo como identidad final.
- Suite final: `python -m pytest -q --junitxml=runtime_data/e3_a_20260920/full_suite.xml`:
  6.080 aprobadas, cero fallos/errores/omitidas, 647 advertencias, 359,18 s.
  XML contrastado por sus 6.080 nodos testcase, incluidos 76 nuevos de E3-A.
  No se declara que todas las advertencias sean preexistentes por comparacion
  de su numero: la referencia previa tenia 638. No hubo fallos que tolerar.
- Inventario: `python tools/audit_mt5_native_access.py` coincide con baseline;
  diez modulos live siguen pendientes. `git diff --check` pasa para cambios
  tracked; los archivos nuevos ademas pasan compilacion/importacion en las pruebas.
- Ensayo final: `python tools/probe_mt5_read_isolation.py --seconds 60 --output
  runtime_data/e3_a_20260920/native60_final_python314.json`. Aprobado con los
  hashes finales: llamada de 60,018 s, 3.863 intervalos, p99 16,39 ms y maximo
  19,84 ms del bucle. Control negativo: 405,18 ms. PID hijo distinto del padre,
  respuesta FOUND y trabajador terminado al cerrar. Ejecucion separada de la
  suite. Prueba PyDLL real que retiene GIL; no `time.sleep`, no MT5 real ni VM.

Identidad final: `mt5_client.py` SHA256
`b80216c4b105c65f72b5cfebce524fc0be4fcb27f38abb338031c62e81b01e89`,
`mt5_worker.py` SHA256
`07b53da4d5c8e643a893d40fd4d802b789810099914556e5010530034eae1a21`,
`mt5_read_protocol.py` SHA256
`f7f772fc8e09fa2f057113920f2f146d922810aea691b59da218d5fe9fce5640`.

Entorno local: Windows 11, Python 3.14.2. El launcher solo lista 3.14; el runtime
incluido en Codex es 3.12.14. No hay ejecucion Python 3.11 disponible en esos
entornos. Se comprueba sintaxis 3.11 con AST, lo cual NO prueba compatibilidad
runtime 3.11 ni compatibilidad con la extension nativa instalada en la VM.

## Lo Que Sigue Abierto

El inventario registra 23 modulos, de los que 10 son legacy live pendientes.
Su baseline hace visibles los accesos existentes; no los autoriza ni certifica
un call graph exhaustivo. Herramientas/analysis requieren revision de alcance;
E3-C debe ampliar el control con importacion y ejecucion del arranque real.
La interfaz no es aun un reemplazo transparente de todos los namedtuples usados
por executor: sus consumidores se adaptaran expresamente en E3-B/E3-C.

No hay operaciones de trading, proyeccion durable nueva, monitor/heartbeat
migrado, singleton entre procesos, watcher, politica de recuperacion nativa,
ensayo de carga 60 min, prueba en capacidad VM ni paridad de estrategias. Una
llamada bloqueada sigue ocupando el trabajador; que Telegram siga vivo no
significa que MT5 pueda ejecutar mientras esa llamada sigue bloqueada.

Siguiente paso del flujo acordado: Astra/alto revisa E3-A; despues Sol/alto
implementa E3-B sobre las conclusiones aceptadas. No ampliar esta entrega a
ordenes o despliegue sin completar esa revision.

Todo permanece local. Sin commit, push, despliegue, reinicio, consulta remota
de estado de VM ni orden real. La ultima observacion historica de la VM no
se convierte en una comprobacion actual por pasar estas pruebas.
