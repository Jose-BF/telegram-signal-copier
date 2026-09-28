# Revision De La Tercera Ronda E1-E2

Estado: E1-E2 no aprobados para integracion E3. Revision directa del transporte y
registro de intenciones, con revision auxiliar independiente del inventario y
latencia. Solo reproducciones offline y documentacion; sin cambios operativos,
commit, push, VM ni ordenes. Base `7415941a410d18288fa4e08da76809f70b125efa`, rama
`feature/gold-555-live-trial`; checkout sucio preservado.

Actualizacion 20/09/2026: los hallazgos de esta revision tienen una
[reparacion local verificada](2026-09-20-e1-e2-fourth-repair-results.md).
Este documento se conserva como evidencia de los contraejemplos previos; E1-E2
sigue pendiente de la nueva revision independiente indicada en esa entrega.

Las regresiones de la ronda anterior pasan. Esto confirma sus casos concretos,
pero no cierra las transiciones combinadas que se reproducen a continuacion.
Los hashes actuales de los cuatro modulos coinciden con los de la entrega.

## Transporte Y Aplicacion

### TR1 / P1: La recuperacion automatica abandona una confirmacion retenida

`mt5_gateway.py:548` reintenta el sobre, pero `:551` no comprueba si sigue
retenido antes de recuperar DISPATCHING como UNKNOWN y borrar el pendiente.
En contraste, start/close/recuperacion explicita comprueban el backlog.

Reproduccion: DONE/deal=902 en cola, trabajador muerto y fallos de escritura al
guardar el resultado. La escritura posterior de recuperacion si tiene exito.
Devuelve UNKNOWN, pending=false y conserva DONE solo en el backlog. Incluso
tras recuperar el almacenamiento, una llamada ordinaria con la misma intencion
devuelve UNKNOWN porque solo reintenta el sobre del pendiente, ya eliminado.

Cierre: compartir la misma condicion de recuperacion en todas las rutas;
evidencia confirmada retenida impide degradar y liberar. Restaurado SQLite,
reintentar toda confirmacion retenida antes de retornar/admitir mas trabajo.

### TR2 / P1: UNKNOWN tardio del broker bloquea la recuperacion

`mt5_gateway.py:429` solo concilia si el estado tardio difiere de UNKNOWN.
Un timeout del broker tambien es UNKNOWN, pero tiene evidencia/error distintos
del UNKNOWN provisional creado por el gateway. La comparacion de `:444` lo
trata como contradiccion terminal y retiene un sobre imposible de resolver.

Reproduccion: recuperacion por muerte, despues respuesta autenticada del mismo
intento/sesion con retcode=10012. Procesar el sobre y recuperar explicitamente
lanzan `worker outcome does not match the durable terminal outcome`.

Cierre: distinguir incertidumbre provisional de evidencia tardia del broker.
Conservar esa evidencia aunque siga sin confirmar ejecucion; mantener la
operacion sin reenviar y permitir recuperar el transporte. No convertir un
timeout del broker en exito o rechazo probado.

### TR3 / P1: El marcador de aplicacion oculta la confirmacion posterior

`execution_intents.py:601` sustituye el resultado recuperado, pero conserva
`applied_utc`. `mark_applied` en `:720` devuelve False siempre que ese marcador
existe y permite marcar UNKNOWN como aplicado.

Reproduccion: UNKNOWN de recuperacion, mark_applied=True, resultado tardio
DONE/deal=903. El nuevo estado es DONE con applied_utc previo; mark_applied=False
y list_unresolved vacio. Un consumidor que use este contrato para aplicar una
vez puede omitir el fill confirmado. Es un defecto del contrato local, no una
perdida de fill demostrada en produccion; E3 aun no consume este modulo.

Cierre: definir entrega/aplicacion por revision de resultado, o impedir consumir
incertidumbre como confirmacion terminal. La entrega posterior debe ser visible
exactamente una vez sin ejecutar otra orden ni duplicar efectos parciales.

### TR4 / P1: Admision lenta permite encolar despues del plazo

`mt5_gateway.py:141` comprueba el plazo antes de `store.admit`, que puede esperar
al disco. Despues `:150` encola con timeout=0; una cola libre lo acepta aunque
el plazo absoluto ya haya vencido.

Reproduccion controlada: plazo de 20 ms, admision retrasada 60 ms, cola vacia.
La llamada llega al broker doble despues del plazo y devuelve DONE. El tiempo
total concreto depende de SQLite; esta prueba demuestra envio posterior al
plazo, no solamente una respuesta tardia. No modifica la politica de caducidad
de una orden que ya hubiese sido admitida y enviada a tiempo.

Cierre: revalidar al terminar la admision durable y antes de encolar; cerrar
la admision no enviada y conservar una via de reintento con identidad nueva.

### TR5 / P2: Schema 2 pierde validacion al abrirlo como schema 3

`execution_intents.py:101` crea admissions vacia y `:129` cambia version a 3
sin migrar las identidades de los intentos existentes. La nueva validacion
requiere admission incluso para un intento antiguo que ya conserva peticion
completa y sesion.

Reproduccion: base con tablas/campos de schema 2 y un intento DISPATCHING.
Al abrirla con el nuevo store pasa a version 3, pero validar su respuesta falla
con `durable request admission was not found`.

Cierre: migracion transaccional y comprobada desde evidencia durable disponible,
o rechazo explicito del esquema incompatible antes de actualizarlo. No inventar
identidades de schema 1 o registros incompletos. Probar reapertura y resultados
tardios con bases antiguas, no solo bases vacias. Afecta compatibilidad del
prototipo; no se ha constatado una base de este esquema en la VM.

## Inventario Y Latencia

El revisor auxiliar entrego siete contraejemplos y un conjunto de controles
positivos. El revisor principal inspecciono y ejecuto sus probes sin cambiar
los algoritmos. Los cinco P2 siguientes requieren correccion; el P3 es una
limitacion condicionada de tipado. No se afirma que esten presentes en el
corpus real ni que todos hayan sido introducidos en esta ronda.

| ID | Prioridad y ubicacion | Resultado reproducido | Cierre |
| --- | --- | --- | --- |
| TL1 | P2, execution_latency.py:239 | 30 copias sin attempt_id/event_id dan ready; un mismo event_id con 30 attempt_id distintos tambien da ready sin conflicto | Identidades ausentes solo diagnosticas; comprobar coherencia entre todas las identidades disponibles antes del gate |
| TL2 | P2, execution_latency.py:183 | adverse_slippage_xau=NaN aborta todo el resumen con ValueError antes del filtrado numerico | Rechazar/contabilizar datos invalidos sin abortar otras muestras ni equiparar invalidos con valores validos |
| TI1 | P2, research/runtime_simulation_inventory.py:834 | Recepciones simultaneas con canales distintos cambian el grupo diario al invertir lectura, sin cuarentena | Incluir canal en consistencia de identidad y resolver el conflicto explicitamente; no elegir el primer canal leido |
| TI2 | P2, research/runtime_simulation_inventory.py:545 | Cesta nativa sin fecha -20 antes de cesta fechada +10 produce una fila; orden inverso produce dos | Descubrir primero todas las raices nativas del periodo, despues vincular las filas sin inventarles fecha |
| TI3 | P2, research/runtime_simulation_inventory.py:773 | Cambiar el dia del tercer importe shadow semanal +30 deja identica la salida signals | Conservar dia y fila de origen tambien en shadow.diagnostic_rows; SI4 reparo nativo/paridad pero esta ruta seguia perdiendo esa asociacion |
| TI4 | P3 condicionado, research/runtime_simulation_inventory.py:399 | code_commit mal tipado como objeto copia un marcador raw_text al inventario sanitizado | Validar tipos escalares de metadatos exportados; se uso solo un marcador ficticio, sin contenido sensible real |

TI2 conserva en unplaced el identificador y motivo del caso apartado, pero no
su importe. TI3 se refiere a la salida de senales: las fuentes y sus hashes
siguen permitiendo detectar que los documentos de entrada eran distintos.
TL1 no permite certificar independencia solo por tener 30 filas. El arreglo
puede mantener estadisticas diagnosticas de legado, con un gate separado para
muestras identificadas y no contradictorias.

## Evidencia De Reproduccion

`runtime_data/e1_e2_third_repair_review_20260919/transport_probes.py` y
`transport_results.json` conservan cinco reproducciones con SQLite temporal y
dobles. Sus assertions comprueban los defectos actuales, no certifican un
arreglo. `verify_sources.py` y `source_verification.json` cotejan procedencia.
`evidence/inventory_latency_probes.py` y `inventory_latency_results.json`
conservan la revision auxiliar, ejecutada tambien por el revisor principal.

- 91 pruebas focales aprobadas en 13,18 s, incluyendo protocolo, intenciones,
  gateway, latencia e inventario. Windows/Python 3.14.2.
- No se repite la suite global: los modulos mantienen los hashes de la entrega
  con 5.980 pruebas aprobadas. Esa pasada no cubre estos contraejemplos.
- Los JSON v4/v5 conservan sus hashes. Cinco fuentes coinciden integramente.
- trade_events.jsonl tiene 38.556 bytes adicionales y 37 eventos posteriores
  al corte. Los primeros 127.539.360 bytes coinciden exactamente con el hash
  congelado `46772e3d1bd5764aca268b70758edd78e7ea9f76137ddc52cbe729c78274dda8`.
  No se borra el sufijo ni se atribuye su origen sin evidencia. Reproducir el v5
  exige usar ese prefijo verificado o una copia congelada, no el archivo entero
  actual como si conservara su hash original.

## Reparacion Acotada Siguiente

Antes de integrar, usar una matriz unica de transiciones: admision, despacho,
resultado retenido, resultado durable y aplicacion. Cruzarla con respuesta
ausente/UNKNOWN/DONE/PLACED/parcial/rechazo, disco fallando/restaurado y las
entradas timeout/await_late/recover/start/close. Mantener estas invariantes:

- Una confirmacion conocida no se degrada por recuperar el transporte.
- Resolver incertidumbre no suprime la entrega de la confirmacion posterior.
- Fallo de transporte no autoriza reenviar una operacion ambigua.
- Plazo vencido antes de encolar implica cero envios.
- Reabrir una base conserva identidad y validacion, o falla explicitamente.
- Permutar fuentes no cambia canal, pertenencia o asociacion fecha/importe.
- Replicar filas o aportar identidades contradictorias no aumenta la muestra
  certificable; un valor mal formado queda visible sin inutilizar todo el lote.

Reutilizar las 91 pruebas existentes y convertir las reproducciones en
regresiones de aceptacion. Python 3.11, carga nativa, propiedad entre procesos,
conciliacion MT5 e integracion E3 siguen siendo trabajo posterior declarado.
La siguiente implementacion puede usar Sol/alto siguiendo el reparto acordado;
su alcance es corregir estas fronteras y ejecutar la matriz, sin nuevas familias
de estrategias ni ajustes de rentabilidad. TI4 puede tratarse en el mismo
validador de entrada o quedar explicitamente separado del gate operativo.
