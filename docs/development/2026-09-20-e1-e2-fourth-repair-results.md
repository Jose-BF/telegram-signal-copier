# Cuarta Ronda De Reparaciones E1-E2

Fecha: 20/09/2026. Estado: todos los contraejemplos de la revision anterior
estan reparados y verificados localmente; pendiente de una nueva revision
independiente con Astra antes de aprobar E1-E2 o disenar E3. Sin commit, push,
VM, reinicio, configuracion ni orden real. La estrategia 5-5-5 no se modifica.

Revision posterior: la [revision independiente](2026-09-20-e1-e2-fourth-repair-review.md)
confirma los controles originales y la reconstruccion exacta del inventario,
pero reproduce dos transiciones de transporte/aplicacion y huecos de identidad
y numeros malformados en latencia. La afirmacion de cierre de abajo describe
los casos de implementacion; E1-E2 siguen sin aprobar por esos hallazgos.
F1-F4 se reparan despues en la
[quinta ronda](2026-09-20-e1-e2-fifth-repair-results.md), pendiente de revision
independiente con Astra.

## Transporte Y Aplicacion

- Una confirmacion retenida impide recuperar DISPATCHING como UNKNOWN. El
  pendiente y el sobre se conservan hasta que el resultado pueda persistirse.
- Una respuesta UNKNOWN autenticada del broker puede sustituir el UNKNOWN
  provisional creado por recuperacion, sin convertir incertidumbre en exito ni
  autorizar un reenvio.
- Sustituir un resultado provisional borra su marcador de aplicacion. El nuevo
  resultado queda disponible exactamente una vez para el consumidor.
- El plazo absoluto se comprueba tambien despues de la admision durable. Si ya
  vencio, la admision termina como FAILED_PRE_DISPATCH y nada entra en la cola.
- Abrir schema 2 reconstruye admissions desde la identidad durable completa en
  una transaccion. Registros incompletos o contradictorios impiden avanzar la
  version en vez de inventar identidad.

Estas reglas son comunes a cualquier peticion y no ajustes para 3086 o una
fecha concreta. Las cinco familias TR1-TR5 tienen regresiones permanentes.

## Latencia Schema 3

- Las filas sin attempt_id y event_id siguen disponibles para diagnostico,
  pero no cuentan para declarar listo un escenario de simulacion.
- Todas las identidades disponibles se conectan antes de deduplicar. Un mismo
  event_id unido a attempt_id incompatibles es conflicto y queda fuera.
- Los valores numericos no finitos se contabilizan y excluyen de esa metrica
  sin abortar el resumen completo.
- El informe separa `sample_count` identificado de
  `diagnostic_sample_count`.

## Inventario Schema 6

Nueva salida, sin reemplazar schemas 1-5:
`runtime_data/runtime_simulation_inventory_20260920_v6/inventory.json`.

- SHA256: `06c79ed4f8251c0445c44b5bccb965c4bf11554f5b7975d996181ba7b1380b01`.
- 1.190.442 bytes; generador SHA256
  `a09ef960cb647f1eafcd54cb00a55900e65cc80b2877bcb45c485b04b85e101e`.
- 267 senales, 253 recepciones, 53 grupos dia/canal y 5.937 intentos:
  5.927 enviados y 10 conocidos como no enviados.
- Las recepciones empatadas incluyen canal en su semantica. Un empate
  contradictorio queda con canal desconocido y en cuarentena, sin depender del
  orden de los archivos.
- Las cestas nativas descubren primero todas las raices fechadas del periodo.
  Las filas sin fecha ya no se incluyen o excluyen segun su posicion.
- Cada diagnostico shadow semanal conserva `day_utc` y `source_row_index`.
- Metadatos de contrato no escalares se sustituyen por null, se identifican en
  `invalid_metadata_fields` y se contabilizan sin copiar el valor anidado.

Los JSON v4 y v5 conservan sus hashes publicados. Para v6 se uso el diario
actual de 127.577.916 bytes y SHA256
`17ecdba7eb6d2f13d73482d0e3f35aa0e16e4f80b773cdce8dbd67b8682482e1`.
Contiene las 37 lineas ya verificadas posteriores al corte; por ello la fuente
y el contador global pasan a 192.521 lineas. La cohorte sigue terminando en
`2026-09-19T00:00:00+00:00`, y sus cifras sustantivas no cambian.

## Verificacion

- 83 pruebas focalizadas: superadas en 13,00 s.
- Suite completa: 5.993 pruebas superadas, 638 avisos existentes, 509,71 s.
- Compilacion de los cinco modulos afectados: correcta.
- `git diff --check`: sin errores; solo avisos CRLF existentes del checkout.
- Entorno local: Windows, Python 3.14.2.

## Limites Y Siguiente Gate

La evidencia cierra los once hallazgos TR1-TR5, TL1-TL2 y TI1-TI4 en el
entorno local. No certifica aun Python 3.11, MT5 real, carga nativa de 60 s,
propiedad unica tras reinicios, conciliacion real ni integracion E3. Tampoco
demuestra rentabilidad de una estrategia.

El siguiente gate es una revision independiente con Astra de codigo, pruebas,
migracion y artefacto v6. Solo si esa revision no deja defectos abiertos se
podra aprobar E1-E2 y plantear E3. Publicar o activar sigue requiriendo
autorizacion separada.
