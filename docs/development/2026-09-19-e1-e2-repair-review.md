# Revision De Las Reparaciones E1-E2

Estado historico: NO aprobado para E3. Revision directa de E2 tras el cambio de modelo,
con revision independiente de E1 por subagente. El cambio de modelo dentro de
la misma conversacion no convierte la revision principal en auditoria externa.
HEAD: `7415941a410d18288fa4e08da76809f70b125efa`, rama
`feature/gold-555-live-trial`, checkout con cambios locales previos preservados.

Actualizacion posterior: los contraejemplos RR1-RR5 y RI1-RI7 de este informe
se han reparado localmente y tienen regresiones en la
[segunda ronda de reparaciones](2026-09-19-e1-e2-second-repair-results.md).
Este documento conserva el hallazgo original; la aprobacion sigue pendiente de
revision independiente con Astra y no autoriza E3 ni produccion.

La suite anterior y las reparaciones son avances reales. No justifican el cierre
de las ventanas R1, R2 y R5 porque las pruebas no ejercitan todos sus caminos.
En esta revision no se edita codigo de ejecucion, no se publica ni se consulta
la VM. Los resultados siguientes usan SQLite temporal y dobles sin MT5.

## Hallazgos Reproducidos De Ejecucion

### RR1 / P1: El plazo se reinicia al conseguir un hilo

`mt5_gateway.py:171-172` entrega a `to_thread` un timeout relativo calculado antes
de esperar un hilo. `call` crea otro deadline en `:101` cuando finalmente arranca.
Con el executor por defecto ocupado, la solicitud puede enviarse despues del
plazo original. La reserva asincrona limita ocupacion, pero no evita esta espera.

Reproduccion `expired_pool`: executor de un hilo ocupado 150 ms y timeout 20 ms.
La llamada se despacha a los 187,92 ms y termina DONE. Es envio caducado, no solo
un retorno tardio. El doble recorre la admision y la cola reales del gateway.

Cierre: transportar el mismo deadline absoluto desde la entrada asincrona hasta
la admision, revalidarlo antes de encolar y comprobar saturacion del executor.
Separar ese plazo de espera del consumidor de una eventual caducidad de orden
en el trabajador. Medir tambien contencion SQLite y cancelacion antes de enviar.

### RR2 / P1: Se pierde el resultado si falla la primera lectura de SQLite

`mt5_gateway.py:205` consume el sobre y `_process_envelope` llama a `store.get`
en `:290` antes de retenerlo. Solo las excepciones de escritura en `:296` quedan
cubiertas por `_pending_envelope`. Una lectura fallida descarta el sobre conocido.

Reproduccion `lost_on_read`: sobre DONE/deal=501, lectura falla una vez. Tras
recuperarse SQLite, el registro sigue DISPATCHING, cola vacia y sobre=null. Se
ha perdido evidencia que estaba disponible; la espera posterior no la recupera.

Cierre: retener el sobre validado antes de cualquier I/O y liberarlo solamente
tras confirmacion durable y de identidad. Probar lectura/escritura/conexion
fallidas, error persistente y recuperacion sin ninguna nueva llamada al broker.

### RR3 / P1: La recuperacion descarta un resultado ya retenido

`mt5_gateway.py:230-231` transforma DISPATCHING en UNKNOWN y borra el sobre.
`start` (`:77-82`) y `close` (`:248-250`) tienen rutas equivalentes. El padre
puede seguir vivo y conservar DONE/ticket aunque el hijo ya haya terminado.

Reproduccion `lost_on_recovery`: fallo de escritura deja DONE/deal=501 en
`_pending_envelope`. Se simula muerte del trabajador; con SQLite recuperado,
`recover_after_worker_loss` guarda UNKNOWN/deal=null y elimina el sobre.

Cierre: persistir resultados conocidos antes de degradar los intentos restantes;
si el disco sigue caido, conservar esa evidencia y el estado de degradacion.
Probar recuperar/reiniciar/cerrar con resultado retenido y aviso en transito.
No exigir recuperar informacion que nunca llego al padre ni fue persistida.

### RR4 / P1: Consumir el aviso tardio deja bloqueada la admision

`mt5_gateway.py:338` ignora el booleano `resolved` al drenar respuestas. Si una
peticion sigue pendiente tras timeout y llega su resultado antes del siguiente
`call`, se guarda DONE, se consume el sobre, pero no se borra la peticion. El
chequeo de `:111` sigue lanzando UnresolvedRequestError en llamadas sucesivas.

Reproduccion `drained_pending`: dos llamadas posteriores rechazadas, registro
DONE, pending=true y cola vacia. `await_late` explicito puede recuperar la ruta
tras esperar su timeout y releer disco; por tanto no es bloqueo irreversible.
La prueba anterior cubria un caso distinto: pendiente ya borrado antes del aviso.

Cierre: liberar unicamente el pendiente cuya identidad corresponde al sobre
resuelto. Probar ambos ordenes de carrera y que un aviso antiguo no borre una
solicitud distinta actualmente en vuelo.

### RR5 / P2: La respuesta no se coteja con la peticion durable completa

`mt5_gateway.py:284-289` coteja los campos del sobre con su propia copia de la
peticion, pero no con lo admitido. `execution_intents.py:280` solo verifica
attempt_id al completar. Los request_id/action_id/payload guardados no se usan
para validar la respuesta; tampoco se transporta el epoch del trabajador.

Reproduccion `mismatched_request`: conservando intent_id/attempt_id pero cambiando
request_id, action_id y volumen de 0.01 a 1.0 en el sobre, se acepta DONE. Esto
prueba una falta de validacion de frontera, no demuestra que el trabajador actual
fabrique esa respuesta ni que se haya ejecutado un volumen incorrecto en vivo.

Cierre: cotejar identidad completa y origen con el intento persistido, incluidos
avisos tardios y resultados ya guardados; conservar y aislar conflictos en lugar
de aceptarlos por igualdad del estado final.

## Hallazgos Reproducidos Del Inventario

Revision independiente del subagente Franklin y reproduccion posterior del
revisor principal. Todos los siguientes son P2 y se refieren a
`research/runtime_simulation_inventory.py`. El resultado schema 3 sigue siendo
un inventario diagnostico; no prueba admision ni fidelidad completa.

| ID / linea | Contraejemplo confirmado | Correccion exigida |
| --- | --- | --- |
| RI1 / 491 | Dos recepciones de una senal con mismo event_id y direcciones distintas dejan signals=[]; solo persiste el contador global de conflictos | Conservar identidad, procedencias y motivo en cuarentena/denominador sin agregar hechos ambiguos |
| RI2 / 391 | Tras contrato A a las 09:00, un evento contractual conflictivo B/C a las 11:00 se elimina; recepcion 12:00 hereda A sin hueco | El conflicto debe invalidar la atribucion desde su instante hasta evidencia posterior suficiente, no restaurar A por omision |
| RI3 / 393 | Contratos A/B distintos con misma sesion e instante seleccionan B o A segun el orden de los archivos | Marcar ambiguedad contractual; ninguna politica debe depender del orden de lectura |
| RI4 / 512, 560 | Intento sin session_id queda sin hueco contractual; primera recepcion con session_id vacio aborta con UnboundLocalError | Inicializar atribucion por evento y conservar eventos de sesion desconocida como huecos explicitos |
| RI5 / 571 | Timing schema 1 con componentes monotonos coherentes pero duration_ns negativo produce timed_attempts=1 e invalid_timing_attempts=0 | Validar coherencia y signo de la duracion total junto a los componentes; comprobar limites ausentes/invalidos |
| RI6 / 560, 679 | Leer recepcion A/10:00 y luego B/08:00 cambia recepcion principal a las 08:00 pero conserva contrato A/09:00 | Recalcular/borrar atribucion al cambiar recepcion; no usar primer contrato posterior como sustituto de contrato causal ausente |
| RI7 / 634, 657 | Dos filas de paridad de candidatas distintas y dos cestas nativas de igual senal se sobrescriben: solo queda ultima candidata y ultimo importe, sin conflicto | Preservar multiplicidad por identidad de candidata/cesta y diagnosticar contradicciones, sin seleccionar ultima fila |

RI1-RI6 son ventanas no cubiertas de las reparaciones I1/I4/I5. RI7 es un defecto
adicional confirmado en el alcance de inventario: afecta comparar varias sombras
para una misma senal. Estas reproducciones no prueban que cada conflicto exista
en el corpus real; prueban que el generador no cumple todavia los contratos.

Correccion documental P3: el schema 3 verificado conserva 267 senales,
**29 fechas UTC unicas y 53 grupos dia/canal** (27 canal1 y 26 canal2). El informe
de implementacion confundia grupos con dias; se corrige la frase y se conserva
el JSON original, cuyo hash no cambia.

## Evidencia Y Alcance

- Reproducciones: `runtime_data/e1_e2_repair_review_20260919/probes.py`.
- Resultado y hashes de los cinco modulos revisados:
  `runtime_data/e1_e2_repair_review_20260919/results.json`.
- Reproducciones RI1-RI7 y comprobacion de los dias del corpus:
  `runtime_data/e1_e2_repair_review_20260919/inventory_probes.py` y
  `runtime_data/e1_e2_repair_review_20260919/inventory_results.json`.
- Las cinco reproducciones terminan correctamente y verifican los defectos
  mediante assertions. No son pruebas verdes de una reparacion.
- `python -m pytest -q tests/test_runtime_simulation_inventory.py
  tests/test_execution_latency.py tests/test_mt5_protocol.py
  tests/test_execution_intents.py tests/test_mt5_gateway.py`:
  59 passed en 11,23 s, Windows/Python 3.14.2.
- Se conserva el resultado anterior de 5.949 pruebas; no se repite la suite
  completa para esta revision sin cambios de codigo productivo.
- Python 3.11, bloqueo nativo de 60 s, carga sostenida, propiedad unica,
  conciliacion real e integracion E3 continuan pendientes como ya estaba
  declarado. No se cuentan como defectos nuevos de estas reparaciones.

El siguiente bloque debe cubrir estas transiciones con regresiones: primero
RR1-RR5 en transporte/persistencia, despues RI1-RI7 en inventario. Debe probar
tambien permutaciones de archivos, ausencia/conflicto de identidades y las
transiciones recuperacion/cancelacion, no solamente el ejemplo que fallo.
Revisar los cambios afectados y cerrar los pendientes antes de integrar E3. Esta
revision no concede permiso de publicacion ni certifica simulacion o estrategia.
