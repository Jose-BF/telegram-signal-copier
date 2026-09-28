# Evidencia Previa Al Envio

Estado: 80 pruebas focalizadas aprobadas, incluidas 25 nuevas; 727 controles
integrados y 7472 pruebas globales aprobados en `A:/cr-ux8i338p/e`, sin fallos,
errores ni omisiones. Fuentes e identidad sin cambios durante la ejecucion.
Todo local, sin commit/push/despliegue ni lectura nueva de VM.
Este bloque prepara la conciliacion de respuestas perdidas, no la implementa.

## Causa Y Cambio

`TradeWorker.prepare` ya produce cotizacion nativa (`source_tick`, incluido
`time_msc`), contrato de simbolo y niveles de stop solicitados/efectivos.
`MT5ReadClient` recibia esa evidencia pero solo persistia el pedido nativo.
Ante respuesta perdida faltaba esa referencia previa al efecto.

El cliente pasa ahora el `TradePreparation` completo a `IntentStore.begin_dispatch`.
La nueva columna nullable `execution_attempts.preparation_json` se escribe
en la misma transaccion que el intento, antes del mensaje de commit al worker.
No introduce llamadas MT5 ni registros por tick; almacena una preparacion por
intento enviado. No se afirma latencia neutral: hay serializacion y bytes
adicionales en la escritura durable, pendientes de medicion operativa.

Se validan request, intent, attempt, action, sesion/PID del trabajador y pedido
nativo exacto antes de cambiar el ledger. Una preparacion fallida o de otro
intento no es despachable. Si falla esa transaccion, no se manda commit.

`get_current_dispatch` devuelve un snapshot inmutable por JOIN del intento
actual. Conserva por separado reloj local de despacho y reloj nativo del tick:
ninguno se convierte implicitamente en el otro. Contrasta identidad y contenido
persistidos; una corrupcion no se interpreta como evidencia ausente.
Legacy sin preparacion o sin payload preparado devuelve `None` para ese dato,
sin fabricar una cotizacion ni rellenar un payload ausente con el pedido logico.
Un payload legacy presente no acredita por si solo preparacion nativa completa.

## Evidencia

- `A:/cp-p01/red.xml`: tres fallos reproducidos por evidencia descartada antes
  del efecto, con respuesta DONE, respuesta None y muerte del worker.
- `A:/cp-p02/green.xml`: 74 aprobadas, primera reparacion.
- `A:/cp-p03/result.xml`: 24 aprobadas y un fallo legacy por payload nativo SQL
  NULL. Corregido sin rellenar datos inexistentes; tambien detectado por revision
  independiente, que no modifico archivos.
- `A:/cp-p04/green.xml`: 80 aprobadas. `A:/cp-p05/green.xml`: 80 aprobadas tras
  reproducir la migracion eliminando ambas columnas de preparacion antes de abrir.
- Se reutiliza sin editar el runner de recuperacion
  `reports/e5-runtime-recovery-control-20260921/run_verification.py` sobre las
  fuentes nuevas: `A:/cr-ux8i338p/e/focused.xml`, 727 aprobadas (121 s);
  `full.xml`, 7472 aprobadas (668.688 s de proceso). SHA256 del XML global:
  `8be4057ca7b15a574d52f1e3b06ff433675e2cf9edbc35b328d20a5ff1bb9eff`.
  `verification.json` confirma fuentes e identidad estables. Se conservan 86
  trazas sinteticas con hashes; la global incluye las 25 regresiones nuevas.

El doble spawn-safe inspecciona SQLite dentro del proceso worker antes de su
`order_send`: coteja cotizacion, pedido, identidad y estado DISPATCHING. Despues
reinicia el cliente/worker y reentrega la misma intencion con otro ID de intento:
el envio total sigue siendo uno y se conserva la preparacion original.
No es un reinicio de todo el bot ni una ejecucion nativa real.

Se comprueban rollback antes y despues de insertar el intento, identidad cruzada,
JSON corrupto, ausencia legacy, inmutabilidad, UNKNOWN persistido y seleccion
del intento vigente tras retry explicitamente autorizado de un rechazo.
El retry no modifica la evidencia del intento anterior.

## Siguiente Frontera

La conciliacion debe partir de este request original, no crear IDs nuevos.
Debe usar lecturas tipadas por worker con cuenta verificada y presupuestos
de tiempo/volumen. El tick de preparacion es un ancla, no prueba por si solo
el instante exacto del fill, ni el deadline local prueba cuando dejo de ser
posible un efecto. Las ventanas y coherencia temporal deben ser explicitas.

El matching debe contrastar simbolo, magic, comentario nativo y direccion;
agrupar deals por orden/posicion, validar volumen y precio real, y rechazar
ambiguedad. No exigir el precio solicitado como fill: puede haber deslizamiento.
Una lectura vacia o parcial no prueba rechazo y no autoriza reenvio.

Una unica coincidencia en una consulta no basta: los comentarios actuales
pueden repetirse entre generaciones y estan truncados a 31 caracteres.
Hay que contrastar intentos competidores, incluidos los ya confirmados, y
persistir atomicamente la atribucion exclusiva cuenta/orden/deals. La API
actual de conciliacion recibe outcome/evidencia ya construidos y no prueba
esa exclusividad. No se sintetizara un retcode nativo perdido.
`list_current_intents` excluye reservas superseded; por si sola no es el
universo de competidores historicos. La comprobacion de atribucion debe
considerar los intentos enviados relevantes, no solo la lista operativa actual.

El harness soporta ahora historial por posicion conocida, no descubrimiento
temporal. Ampliarlo debe consultar el mismo libro causal por ventana, sin sacar
el ticket de `entry_receipts` para inyectar una confirmacion. Despues conectar
conciliacion y recuperacion existentes, verificando entradas abiertas y cerradas,
un solo efecto y recorrido completo de dinero/exposicion.

`MarketBook.pending` se resuelve actualmente con `acknowledged`, y
`observe_entry_response` emite una confirmacion externa y modifica su reloj.
No usar ese callback sin distinguir el origen para fingir un ACK que nunca
llego: la observacion por conciliacion necesita evidencia, instante y evento
propios. El fill y el riesgo anteriores a esa observacion no se desplazan.

Siguen abiertos cortes de proceso completo, contencion, valoracion nativa y
contraste historico independiente. Esta evidencia no certifica la estrategia
completa, rentabilidad ni admision de busqueda masiva. Objetivo general activo.
