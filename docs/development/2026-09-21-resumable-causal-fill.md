# Motor Suspendible Y Fill Causal

## Alcance Del Bloque

Continuacion de la integracion descrita en
[transporte compartido](2026-09-21-shared-transport-implementation.md), dentro
del plan completo de ejecucion/simulacion. No es cierre de E5 ni del objetivo.
Rama `feature/gold-555-live-trial`, base
`7415941a410d18288fa4e08da76809f70b125efa`; cambios previos conservados.
Sin commit, push, VM, reinicio ni ordenes reales.

## Cambios

El motor escalar conserva su API `simulate`, pero consume un generador privado
`_simulation_steps`. Este cede el control **antes** de cada cotizacion con su
ordinal y tiempo; posiciones, cursores, protecciones, colas y acumuladores de
riesgo permanecen en el mismo frame. No duplica las reglas de la estrategia.
Se pueden intercalar dos ejecuciones sin compartir accidentalmente estado.
Cerrar un generador abandonado no lo drena ni fabrica un cierre de posiciones.

En la ruta opt-in `single_basket_serial_v1`, una entrada enviada ya no busca
`fill_index` ni calcula su precio futuro en ese mismo instante. Conserva la
solicitud y resuelve el precio/fill cuando llega una cotizacion utilizable que
cumple su reloj. Se conserva el reloj separado de precio y fill, los ordinales
para cotizaciones repetidas, la espera de confirmacion y la proteccion nativa.
La solicitud ya enviada no se cancela ficticiamente por caducidad posterior.

Al terminar una cinta completa sin fill, conserva `entry_fill_quote_missing`
y la solicitud pendiente. Si la ejecucion se interrumpe antes de consumir la
cinta, informa `entry_request_in_flight_at_strategy_exit` sin buscar en el
sufijo no procesado si habria habido una cotizacion ejecutable. No convierte
una respuesta desconocida en rechazo ni realiza reenvios.

## Evidencia Congelada Y Regresiones

Antes del cambio de motor se generaron 48 hashes de **todo** `SimulationResult`,
no solo P/L: entradas, salidas, tiempos, riesgos y eventos de proteccion,
mercado y cliente. Matriz BUY/SELL; sin modelo, proteccion, mercado y cliente;
objetivo, stop, mensaje de cierre, falta de fill, varias patas y ordinales
repetidos. Es control de no regresion sintetico, no una muestra historica ni
un oraculo independiente. Motor anterior SHA256:
`7592ac715c150bc6a64650a28718922b0bd72b6669ecff46ab9c74d6c83499cf`.

Referencia inmutable `tests/fixtures/iterative_resume_reference.json`, SHA256
`2f2ea07a139a7fa291285cded7c75d22eac96803d4b9c17dc61c634fcfdbfe4d`.
No se regenero despues de cambiar el motor para hacer pasar las pruebas.

- 99 pruebas de reanudacion: referencia completa, orden de boundaries,
  intercalado, retorno bloqueado sin consumir ticks y abandono sin finalizar.
- 14 pruebas de fill causal. BUY/SELL reprodujeron lectura de precio del tick 2
  al procesar el tick 0 antes de corregirlo. Ahora solo leen precio disponible
  en ese momento o uno anterior. Incluyen precio distinto al reloj de fill,
  cotizacion invalida, falta de fill y ordinal repetido.
- 192 focales aprobadas en 2,08 s junto con serializacion, mercado y proteccion.
  Comando: `python -m pytest -q tests/test_client_causal_fill.py
  tests/test_iterative_resume.py tests/test_iterative_client.py
  tests/test_iterative_market.py tests/test_iterative_protection.py`.
- Revision independiente de solo lectura: sin hallazgos materiales confirmados;
  192 focales propias y cuatro controles en memoria BUY/SELL sobre rechazo
  de proteccion con fill demorado y cierre del proveedor durante la espera.
  No reconstruyo el motor historico a partir del SHA ni certifica todas las
  combinaciones de latencia. Los 48 controles congelados contienen sobre todo
  fills inmediatos; los retrasos tienen ademas regresiones especificas.
- Primera suite global: 6.537 aprobados y un fallo en cancelacion del gateway,
  una advertencia previa, 473,44 s. Se conservo el informe y se reprodujo el
  mecanismo, en lugar de descartar el fallo por pasar en otra ejecucion.
  [Incidente y correccion general](2026-09-21-runtime-cancellation-races.md).
  Suite final tras corregir tambien la autorizacion del envio: **6.551 aprobados**,
  una advertencia pandas previa, 472,22 s. Fuentes finales en
  `reports/e5-resumable-causal-fill-20260921/sources-authorized.json`; JUnit
  `pytest-authorized.xml`, SHA256
  `9af544e32e6a7bc454dd6bf42bb5ec05e22645873cb278d4b43e8b5f9efb6b54`.

De los 48 controles, 40 contienen entradas y 32 salidas; 18 conservan bloqueos
esperados. Incluyen falta de fill, fin de cinta con exposicion/respuesta pendiente
y cierre de mercado no soportado en proteccion sin modelo de mercado. No se
han quitado estas filas ni convertido una prueba de bloqueo en paridad admitida.

Fuentes y entorno guardados durante la corrida en
`reports/e5-resumable-causal-fill-20260921/sources.json`. La identidad de
investigacion ya incluye `engine.py`; no se ha introducido una dependencia
nueva en el motor que falte en `IMPLEMENTATION_FILES`. No se reetiquetan
resultados archivados con los hashes nuevos.

## Lo Que Sigue Abierto

La pausa entre ticks **no conecta todavia** el coordinador global: cada cesta
conserva su `ClientBook` local. Falta entregar solicitudes reales del motor
al recurso comun y devolver respuestas antes de nuevas decisiones, ademas
de tratar las fases internas de cada tick y las lecturas compartidas.

Nota posterior: la [continuacion incremental](2026-09-21-incremental-entry-transport.md)
ya sustituye la seleccion inicial de la ruta cliente por observaciones
sucesivas; mantiene la ruta sin cliente y los gates. El texto siguiente
describe la frontera al cerrar este checkpoint anterior.

La seleccion inicial `_causal_entry_index` y algunos eventos de preparacion
siguen precalculados desde la cinta. No se presenta este generador como API
de entrada completamente incremental ni se afirma que solo lea un prefijo
de todos sus inputs. El control nuevo acota especificamente la valoracion
de fills enviados por cliente; las rutas sin cliente no se han cambiado.

Gates de Dubai basket_guard, oracle/fast, portfolio y certificacion intactos.
No es evidencia de paridad live completa, latencia calibrada o rentabilidad.
Siguiente paso: detector inicial causal y concesion/respuesta compartidas,
con identidades canal/senal/ticket/solicitud y pruebas de SL pasivo durante
espera de otra cesta. Ampliacion historica, anclas nativas, retencion de datos
y comprobacion/publicacion operativa siguen dentro del objetivo original.

La reparacion de cancelacion distingue ademas transporte adquirido de permiso
de envio: el prepare nativo y el guard pueden ocupar la conexion antes de
autorizar el commit. El evento `started` del coordinador no debe interpretarse
como fill ni como autorizacion. La integracion debe conservar esta frontera y
el drenaje del abort, sin sumar dos veces tiempos de preparacion/respuesta.
