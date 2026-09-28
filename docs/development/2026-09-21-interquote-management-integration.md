# Lecturas Integradas Y Continuidad Del Monitor

## Estado

Checkpoint local del 21/09, posterior a `2026-09-21-sequential-management-reads.md`.
La cadena de lecturas ya esta conectada al motor suspendido y al transporte
compartido mediante el modo explicito `shared_interquote_reads_v2`. El modo
anterior sigue siendo predeterminado. Sin commit, push, reinicio ni acceso a VM.
El objetivo completo y la admision de estrategias siguen abiertos.

Admite reglas monetarias genericas, cliente terminal, stop fijo por movimiento
o ausente, sin targets, BE ni trailing en las cestas consumidoras de lecturas.
No es la gestion completa de Dubai/Gold555. Conserva las barreras existentes
de extensiones no admitidas. Las cestas sin ese consumidor pueden usar las
politicas ya admitidas por el motor.

## Integracion

- El proveedor consulta posiciones, entradas y salidas del motor en su estado
  causal actual, no resultados finales ni la rejilla diagnostica de riesgo.
- Cada respuesta conserva ordinal fuente y relojes de solicitud, concesion,
  muestreo y entrega. El payload queda congelado al muestrear.
- ACK de operaciones y lecturas pueden llegar entre quotes. La continuacion
  de respuestas no repite `process_quote`, swap, SL/TP nativo ni fills.
- La politica consume cada resumen completo una vez. Su pico observado es
  independiente del pico del mercado registrado para riesgo.
- Las lecturas son secuenciales, no una instantanea atomica. El dinero abierto
  usa `p.profit`, sin swap abierto ni slippage de una salida hipotetica. La
  historia usa precio y FX del cierre, agregando una vez el swap realizado.
  Commission/fee cero son supuestos del modelo admitido, no datos de cuenta.
- Lecturas y operaciones de distintas cestas comparten recurso con identidades
  separadas. Los limites de ciclos/bytes mantienen las cestas incompletas en
  el informe y en el denominador.

## Fallos Corregidos

1. Un pico entre lecturas podia armar la proteccion sin haber sido observado.
   Ahora figura en el diagnostico, pero no activa decisiones de la politica.
2. Encadenar lecturas mas largas que el intervalo podia impedir indefinidamente
   nuevas entradas: un control pasaba de tres entradas a una sin error. Tras
   completar o drenar, se observa una vez la ultima quote pendiente antes del
   siguiente ciclo, respetando `fresh_quote`, `snapshot_batch`, trabajo propio
   pendiente y terminales. No se repiten efectos del broker.
3. POSITIONS intentaba convertir NaN a dinero y abortaba sin informe. Una cesta
   detenida tambien podia recibir decisiones. Ahora el dato invalido permanece
   desconocido, no se inician nuevas lecturas/decisiones de esa cesta y se
   drenan las comprometidas conservando el bloqueo original.

Las regresiones de 2 y 3 fallaron antes de reparar sus causas. La revision
independiente final de ambas correcciones fue estatica, sin nuevos hallazgos
materiales; no se cuenta como otra ejecucion de tests.

## Evidencia

33 casos en `tests/test_shared_management_reads.py`: BUY/SELL, picos, relojes,
ACK interquote, cierre del proveedor, SL durante lectura, rechazo de cierre
duplicado, historias con FX posterior distinto, swap, slippage, contencion,
timeout, cutoff, limites, timestamps repetidos, entradas y quotes invalidas.

391 pruebas focalizadas aprobadas, con las referencias congeladas escalares y
controles previos de transporte/riesgo. General nueva con 387 fuentes congeladas
y runtime temporal aislado. Consultar su resultado final en
`reports/e5-interquote-management-integration-20260921/verification.json`;
la general anterior de 6.889 no certifica estos cambios. El paquete contiene
protocolo, cuatro controles sinteticos, fuentes, comando, salida y JUnit.

General terminada: **6.922 aprobadas**, 477,13 segundos, cero fallos, errores o
casos omitidos. Diez advertencias ya presentes (nueve de record_property/xunit2
y una de pandas). Las 387 fuentes y la identidad de implementacion permanecen
sin cambios durante el ensayo. SHA256 del JUnit:
`2f1d58e09ebcd7114cbdc4ce3810e050ac5ad131d7bd181a06f1880ef719e7b4`.
Comando y entorno aislado estan registrados en `sources.json`; el proceso
termino con codigo 0 y no queda una general propia ejecutandose.

## Pendiente

- Fills y protecciones siguen procesandose sobre quotes: no hay modelo completo
  de broker, autorizacion ni preparacion nativa.
- Este perfil deduplica entradas por ordinal; live filtra `time_msc`. No se
  afirma paridad del ciclo completo del monitor ni de timestamps repetidos.
- Live tambien hace lectura inicial de tick, entradas, verificaciones de stop
  y BE, trailing y otras lecturas. No todo esta en el ciclo monetario actual.
- Falta gestion completa de ambos canales, contraste historico amplio,
  validacion posterior a reparaciones y verificacion operativa de la VM.
- Margen, stopout, equity de cuenta y rentabilidad no estan certificados.

## Siguiente Bloque

1. Conectar las decisiones reales mediante funciones canonicas existentes,
   no otra aproximacion de profit-lock. Revisar guards de candidatos Dubai y
   Gold, hard stops y trailing 555, con sus estados y relojes propios.
2. Extender el ciclo observado: tick inicial, deduplicacion real, entradas,
   protecciones, resumen y gestion posterior. Separar reloj de decision,
   cotizacion fuente y envio de cada peticion que espera en cola.
3. Contrastar varias ventanas completas de ambos canales. Buscar primero la
   primera divergencia de decisiones/exposicion y despues comparar P/L,
   flotante, MAE/MFE y drawdown. Conservar casos incompletos y discrepancias.
4. Solo tras admision por capacidad y datos, comparar mejoras con riesgo
   comparable. Publicar requiere autorizacion actual y seguridad de la VM.

### Fronteras Canonicas Revisadas

- Dubai: `position_lifecycle_monitor._apply_candidate_basket_guard_unrecorded`
  invoca `dubai_live_candidate.evaluate_guard`. El stop monetario precede al
  profit-lock; la salida temporal requiere resultado no positivo. En el primer
  armado devuelve `arm`, no evalua una salida temporal adicional en esa llamada.
- Gold 555: `_apply_gold_555_basket_guard_unrecorded` invoca
  `gold_555_live_candidate.evaluate_guard`, no el candidato Gold anterior.
  Valida fingerprint, usa profit-lock y salida temporal no negativa. Ese guard
  no incorpora el stop monetario de Dubai; no deben intercambiarse.
- Ambas rutas conservan `triggered`, `recovery_pending`, peak y armado. La
  evidencia monetaria incompleta no actualiza el pico; una recuperacion ya
  comprometida se trata antes de esa barrera. No sustituir estos estados por
  un booleano `armed` generico.
- El loop live usa el tick inicial para las entradas; verifica stops antes del
  resumen. El trailing 555 posterior usa ese tick inicial y los tickets del
  resumen, no una supuesta instantanea nueva que combine precios futuros.
- Dubai verifica stops en `listener._ensure_dubai_candidate_hard_stops`.
  `_ensure_gold_candidate_hard_stops` corresponde al candidato Gold anterior,
  no a 555. Este usa `_queue_gold_555_first_leg_protection` en listener y
  `_queue_gold_555_leg_protection_unrecorded` en el monitor; su trailing esta
  en `_apply_gold_555_trailing_stops_unrecorded`.

Estas referencias proceden del codigo local, no certifican su version en VM.
Son el punto de partida del siguiente bloque, no capacidades ya integradas.
