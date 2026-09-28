# Resultado Local E1-E2: Evidencia Y Frontera MT5

Fecha: 19/09/2026. Este documento conserva los resultados del prototipo inicial.
La [revision posterior](2026-09-19-e1-e2-review.md) retiro su cierre y documento
once defectos reproducidos. Las [reparaciones posteriores](2026-09-19-e1-e2-repair-results.md)
incluyen el inventario schema 3 y la nueva evidencia local; no reinterpretan
retroactivamente las cifras iniciales de este documento.
No hubo
push, despliegue, reinicio de VM, cambio de configuracion ni orden real.

## E1: Inventario Schema 2

El generador conserva eventos anteriores al primer evento raiz, vincula el
contrato solo dentro de la misma sesion, usa los limites explicitos de broker
en vez del instante de escritura del evento y no transforma el calendario
nativo en UTC sin una conversion acreditada. `sl_updated` y otros proxies de
interpretacion dejaron de contar como proteccion confirmada.

- Salida original schema 1, intacta:
  `runtime_data/runtime_simulation_inventory_20260919/inventory.json`.
  SHA256 `c1aef53b3accfff46b051b1172482797ab075332b5ca8ffcb4bf84ebbcb0cb85`.
- Nueva salida schema 2:
  `runtime_data/runtime_simulation_inventory_20260919_v2/inventory.json`.
  SHA256 `9e57d0585a16193ae03616ab52b835a5b7f3e57b284bf5ca80fbf61500e69e42`.
- 192.373 lineas de eventos, sin lineas malformadas, IDs duplicados ni
  conflictos detectados en estas fuentes.
- 267 senales colocables: 253 con recepcion detallada y 14 incorporadas desde
  fuentes auxiliares con dia UTC declarado.
- 30 identidades auxiliares sin dia UTC justificable quedan separadas, no
  desaparecen: 90 filas de sombra sin fecha y 28 cestas con calendario nativo
  se registran en `auxiliary_quality.unplaced`.
- 5.937 intentos: 5.927 enviados, 10 confirmados como no enviados y ninguno
  con los nuevos limites UTC de respuesta. Este corpus no certifica latencia
  completa ni permite inventar el inicio de las llamadas antiguas.
- 117 filas no tienen un contrato precedente de su misma sesion. Se conserva
  el hueco en vez de heredar una politica de otro arranque.

## E2: Protocolo Y Ejecucion Aislada

Se anadieron tres piezas sin conectarlas aun al runtime productivo:

- `mt5_protocol.py`: identidad estable por cuenta, canal, raiz/generacion,
  pata, operacion y revision; protocolo serializable y estados explicitos.
- `execution_intents.py`: registro SQLite en WAL con `synchronous=FULL`,
  admision idempotente, `PREPARED`/`DISPATCHING`, resultado durable y marca de
  aplicacion idempotente.
- `mt5_gateway.py`: trabajador de proceso unico y cola acotada. El hijo marca
  el despacho antes de llamar al doble de broker y guarda el resultado antes
  de contestar al padre.

Los codigos 10008 y 10010 se conservan como `PLACED` y `DONE_PARTIAL`; 10011,
10012, ausencia de respuesta, excepcion posterior al despacho o muerte del
trabajador quedan `UNKNOWN`. Ninguno de esos estados autoriza un reenvio. Una
consulta `None` se distingue de una lista vacia conocida.

Las pruebas cubren redelivery concurrente, cambio conflictivo de payload,
caida antes del despacho, resultado guardado con el padre ausente, timeout con
respuesta tardia, parcial, aceptacion pendiente, excepcion y muerte durante la
llamada. El doble inicial ejecuta un bucle Python cinco segundos; su control
negativo bloquea directamente asyncio. La revision posterior identifica que
esto no demuestra retencion nativa del GIL y aporta el control nativo correcto.

## Verificacion

- 32 pruebas focalizadas superadas.
- Suite completa: 5.927 pruebas superadas, 638 avisos existentes, 417,15 s.
- Compilacion de los modulos nuevos y pruebas: correcta.
- Entorno local ejecutado: Windows y Python 3.14.2.

## Limites Antes De Produccion

E2 prueba parte del contrato con dobles; quedan fallos de transporte y
persistencia reproducidos en la revision. No sustituye todavia ningun acceso MT5
del bot. Faltan revision independiente, prueba en Python 3.11, prueba bloqueante
de 60 s y carga sostenida, propietario unico tras reinicios, conciliacion real
de ordenes/deals/posiciones y la migracion completa de lecturas y escrituras
de E3. No existe fallback permitido hacia el envio directo si falla la nueva
frontera.
