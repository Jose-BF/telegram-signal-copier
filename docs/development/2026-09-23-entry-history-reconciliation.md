# Recuperacion De Entradas Con Respuesta Perdida

Estado: implementacion y verificacion local. No se ha publicado, reiniciado el
bot ni consultado la VM. Este bloque no certifica el simulador completo.

## Frontera Recuperada

La preparacion nativa anterior al envio ya se conservaba atomicamente. Ahora
`DurableExecutionService.reinspect_entry` consulta TICK, deals y ordenes
historicas mediante el cliente y trabajador tipados. El matching exige cuenta,
simbolo, magic, comentario, direccion, orden, posicion, reloj, volumen completo
y precio de los fills. El ledger comprueba intentos competidores y atribuye
cuenta/orden/posicion/deals en la misma transaccion que outcome y proyeccion.
No inventa un retcode ni deduce rechazo de un historial vacio.

`DurableEntryExecutor.open_market` intenta esta lectura cuando se reentrega una
intencion que ya estaba UNKNOWN, PLACED o DONE_PARTIAL. El primer envio incierto
no espera una lectura historica inmediata. Las nuevas inspecciones de una misma
intencion se espacian al menos 30 segundos por adaptador; si siguen bloqueadas,
el resultado permanece RECONCILE y no se vuelve a enviar la orden.

## Evidencia

- `A:/cp-hist-test/red`: dos pruebas fallaban antes de conectar la reentrega.
- `A:/cp-hist-test/green2.xml`: 190 pruebas enfocadas aprobadas, incluidas la
  recuperacion con cliente y trabajador reales del proyecto, BUY/SELL,
  reinicio del trabajador, historial vacio/desconocido, no duplicacion y limite
  de frecuencia de lectura. El backend MT5 es simulado, no el broker nativo.
- `A:/cr-3rzw4esq/e/focused.xml`: 727 aprobadas.
- `A:/cr-3rzw4esq/e/full.xml`: 7604 aprobadas, cero fallos/errores/omisiones.
  SHA256: `3dee66903a3afb0d7e0d4f69eac03b0d114c6cc55bb4e141736e6a614b9888bf`.
- `A:/cr-3rzw4esq/e/verification.json`: fuentes e identidad de implementacion
  sin cambios durante las pruebas; 86 controles sinteticos conservados.

## Pendiente Para El Objetivo

La consulta actual bloquea si el intervalo entre tick de preparacion y tick
actual supera 24 horas. Exige comentario exacto y que orden y posicion tengan
el mismo ID, pues el consumidor actual usa la orden como ticket. Esos bloqueos
son deliberados, no pruebas de que la orden no se ejecutara. La inspeccion no
recorre aun todos los UNKNOWN al arrancar; necesita reentrega por su flujo.

Faltan cortes de proceso completo y journal, entradas cerradas antes de la
recuperacion con dinero conciliado, historial temporal equivalente en el broker
virtual, comparacion de trayectorias de exposicion/flotante/drawdown en varias
cohortes independientes, contencion y latencia real de la VM. Esta evidencia
no valida fills contrafactuales, rentabilidad, produccion ni admision de una
busqueda masiva de estrategias.
