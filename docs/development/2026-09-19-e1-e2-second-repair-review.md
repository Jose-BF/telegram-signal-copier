# Revision De La Segunda Ronda E1-E2

Estado: NO aprobado para E3. Revision directa con Astra y dos revisores
auxiliares independientes (transporte e inventario). El revisor principal
reprodujo los nueve hallazgos siguientes. Ningun cambio de codigo operativo,
configuracion, commit, push, VM ni orden real durante esta revision.

Actualizacion: los nueve hallazgos fueron reparados localmente y cuentan con
regresiones permanentes en la
[tercera ronda de reparaciones](2026-09-19-e1-e2-third-repair-results.md).
Este documento conserva la evidencia de la revision que los descubrio; E3
sigue sin aprobar hasta una nueva revision independiente.

Base: `7415941a410d18288fa4e08da76809f70b125efa`, rama
`feature/gold-555-live-trial`, checkout con cambios locales preservados.
Los hashes de las fuentes revisadas estan en
`runtime_data/e1_e2_second_repair_review_20260919/results.json`.

## Transporte

### SR1 / P1: Un error anterior al envio bloquea la recuperacion

`mt5_gateway.py:385` exige siempre un intento DISPATCHING antes de interpretar
el sobre, incluso para el error que `_worker_main` emite en `:553` si falla
`begin_dispatch`. Ese error deja PREPARED sin fila de intento.

Reproduccion: SQLite falla en begin_dispatch; cero llamadas al broker. Tras
restablecer SQLite, procesar el sobre, await_late, recover y close lanzan
`dispatched attempt was not found`; pending=true y sobre retenido. El fallo
transitorio se convierte en bloqueo persistente del objeto gateway.

Cierre: modelar y autenticar el resultado anterior al despacho contra la
admision durable y su sesion; permitir terminar/reintentar conforme a politica
solo cuando se ha demostrado que no se envio. No omitir toda validacion para
aceptar cualquier error sin intento.

### SR2 / P1: La recuperacion automatica no consume el resultado recien llegado

`mt5_gateway.py:154` y `:249` convierten DISPATCHING en UNKNOWN al detectar
muerte del trabajador despues de vencer la espera. Estas dos rutas no usan
el drenaje/reintento incorporado a start, close y recuperacion explicita.

Reproduccion: el broker doble devuelve DONE/deal=772 una vez; falla persistirlo.
El sobre llega entre la expiracion de `_wait_response` y la comprobacion de
muerte. La llamada devuelve UNKNOWN con DONE en cola. La siguiente llamada y
recover fallan con `UNKNOWN conflicts with worker outcome DONE`.

Cierre: compartir el procedimiento de recuperacion entre todas las rutas;
preservar y conciliar evidencia tardia que llegue despues de declarar UNKNOWN.
Una gracia fija de 100 ms reduce una carrera, pero no demuestra que ya no
pueda llegar un resultado. Nunca reenviar para resolver esta ambiguedad.

### SR3 / P1: Arranque y cierre no comparten exclusion

`mt5_gateway.py:76` no toma el lock usado por call, recover y close. Al pausar
start antes de crear el proceso, close termina y marca `_closed=True`; despues
start crea un trabajador vivo. Repetir close no lo termina por su early return.
Dos start concurrentes tambien pueden crear dos procesos y perder seguimiento
de uno (reproduccion independiente del revisor auxiliar).

Cierre: serializar las transiciones del mismo gateway y revalidar el estado
antes de arrancar. Este fallo local es distinto del cerrojo entre procesos y
propiedad unica entre reinicios ya reservado para E4.

### SR4 / P2: Un request_id reutilizado entrega el resultado de otra intencion

`mt5_gateway.py:337` espera solo request_id; `:159` borra el pendiente cuando se
resuelve cualquier sobre devuelto. La validacion durable en `:385` comprueba
que el sobre corresponde a algun intento, pero no al que espera esta llamada.
La base no impone unicidad a request_id (`execution_intents.py:78`).

Reproduccion: segunda intencion con igual request_id y distinto attempt/action.
Su envio es admitido; llega un aviso tardio valido de la primera. La segunda
llamada devuelve DONE/deal=42 de la primera, deja la segunda DISPATCHING y
pending=false. Requiere reutilizacion del identificador; no es una colision
aleatoria demostrada en produccion.

Cierre: rechazar reutilizacion incompatible antes de enviar y correlacionar
la identidad completa pendiente en la espera y liberacion de capacidad.

## Inventario

Los cuatro hallazgos siguientes son P2 y pertenecen a
`research/runtime_simulation_inventory.py`.

| ID / linea | Reproduccion | Cierre |
| --- | --- | --- |
| SI1 / 224 | B/C conflictivos a las 11:00, D independiente tambien a las 11:00 y recepcion 12:00: selecciona D sin hueco | Una invalidacion debe bloquear tambien contratos simultaneos salvo orden causal probado; exigir evidencia posterior suficiente |
| SI2 / 423 | Recepcion valida mas dos protection_confirmed de mismo event_id y SL distinto: contador global=1, cuarentena de senal vacia | Vincular conflictos de cualquier evento de gestion/proteccion a las senales existentes, aunque no sea evento raiz |
| SI3 / 687 | Dos recepciones simultaneas BUY/A y SELL/B, ids distintos: invertir archivos cambia direccion y contrato principal | Empates contradictorios deben ser ambiguos y conservar evidencia; no resolver por orden de lectura |
| SI4 / 792 | Tres cestas, importes +10/-20/+30, dias 15/16/15; mover la tercera al 16 conserva identica salida de senal | Guardar fecha e identidad/procedencia por fila nativa y de paridad, ademas de los conjuntos agregados |

En SI4 cambian los hashes de la fuente: la perdida esta en la asociacion de
importe/fecha dentro del inventario, no se afirma que el documento completo
con su procedencia sea identico. Estas pruebas sinteticas no demuestran que
los conflictos existan en las 267 senales del v4.

## Latencia

SL1 / P2, `execution_latency.py:212`: summarize_events cuenta copias del mismo
intento como observaciones independientes. Una decision y 30 copias de un unico
intento valido producen sample_count=30 y simulation_latency_scenarios=ready.
Deduplicar por identidad y excluir contradicciones antes de percentiles y gates.
La seleccion del minimo entre inicios de decision contradictorios tambien
merece cobertura en esa reparacion. El defecto de deduplicacion ya estaba en
el agregador anterior; no fue introducido por validar duration_ns esta ronda.
Bloquea usar ese estado ready como evidencia muestral suficiente, no invalida
por si mismo las duraciones individuales ni el inventario v4.

## Verificacion Y Siguiente Bloque

- 78 pruebas focalizadas aprobadas en 12,31 s sobre las fuentes revisadas.
- Se conserva la evidencia anterior de 5.968 pruebas; no se repite la suite
  global al no haber cambios operativos en esta revision.
- Inventario v4, generador y seis fuentes cotejados por hash. Siguen siendo
  267 senales, 29 fechas UTC y 53 grupos dia/canal.
- Reproducciones permanentes: `runtime_data/e1_e2_second_repair_review_20260919/`
  contiene probes.py/results.json e inventory_probes.py/inventory_results.json.
  Sus assertions confirman defectos; no son pruebas verdes de reparacion.
- SQLite temporal, colas/dobles y limites de transporte controlados; sin MT5.

Implementacion siguiente con Sol/alto: un bloque acotado de transporte para
SR1-SR4 y otro de evidencia para SI1-SI4/SL1. Evitar sumar esperas en cada ruta;
definir estados compartidos de admision, despacho, confirmacion, recuperacion
y cierre. Probar disco caido antes/despues del envio y persistencia, respuesta
antes/despues del timeout, todas las entradas a recuperacion, cancelacion,
start/close concurrentes e identidades incompatibles.

En evidencia, probar permutaciones, duplicados exactos, contradicciones, empates
temporales y varias cestas por senal. Conservar fechas y metadatos de cada fila.
Convertir las reproducciones en regresiones que exijan el comportamiento
correcto; despues suite focalizada/global y revision de estas fronteras.

No cambia el objetivo: reducir bloqueos y disponer de evidencia fiable para
comparar trayectoria, exposicion y drawdown. Python 3.11, carga nativa sostenida,
propiedad unica entre procesos e integracion real siguen pendientes segun el
plan existente. No se abre busqueda masiva ni se aprueba produccion.
