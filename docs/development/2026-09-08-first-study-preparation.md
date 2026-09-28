# Preparacion del primer estudio Gold

## Objetivo aclarado por el usuario

La pregunta principal es usar las entradas publicadas en cada canal como
disparadores de una gestion propia. Copiar toda la gestion del trader y
reconstruir exactamente las ejecuciones del bot son comparaciones distintas,
no requisitos universales para esa investigacion.

Para el estudio provider-first se necesita el contenido de entrada que podia
conocerse entonces, su hora, direccion e identidad, precios Bid/Ask y supuestos
explicitos de ejecucion/costes. Solo se requieren niveles o mensajes posteriores
cuando la politica los utiliza. Cancelaciones y rectificaciones se relacionan
con su senal y hora; no se aplican retroactivamente ni implican invertir una
posicion automaticamente. La respuesta con posiciones ya abiertas debe formar
parte de cada politica declarada.

El universo hipotetico anual usa publicaciones disponibles mas retrasos
declarados, no exige que el bot haya ejecutado cada entrada. El universo de lo
que este bot pudo hacer usa sus recepciones reales y se mantiene separado.
Un texto final editado no demuestra el contenido original. Se necesita historial
de mensajes y precios del mismo periodo de 2026 disponible hasta la fecha;
ninguna ausencia se rellena ni se declara completa por tener precios anuales.

El capital de inversion queda abierto por peticion del usuario. No se vuelve
a pedir una cifra como bloqueo para diagnosticar estrategias a escala comparable.
Se declara un tamano de referencia y sus costes, sin seleccionar por aumentar
lotes. Capital, margen y viabilidad conjunta se estudian despues como escenarios,
no como dinero ilimitado. Se mantienen los requisitos de los motores y sus
controles monetarios; ningun diagnostico se presenta como seleccion certificada.

El objetivo de riesgo es beneficio frente a perdida grave, no solo porcentaje
de aciertos: perdida de cesta completa y conjunta, beneficio de semanas que
borra, caida de equity abierta, peores rachas y tiempo de recuperacion. No haber
observado una cesta completa en SL no prueba que no pueda ocurrir. No se fijan
ahora un capital objetivo ni un drawdown maximo arbitrarios. Cada investigacion
sigue teniendo presupuesto finito y validacion separada de la seleccion.

Esta aclaracion prevalece sobre las referencias posteriores a capital pendiente
del inventario inicial. No inicia una busqueda ni modifica la politica viva.

Actualizacion posterior de esta preparacion: ver
`../audits/2026-09-08-gold-foundation.md`. El catalogo diagnostico ya esta
generado y las seis diferencias EURUSD quedaron resueltas semanticamente.
La admision por horizonte y dinero sigue pendiente; los recuentos de este
documento conservan el estado del inventario inicial.

## Estado y alcance

Preparacion local al 8 de septiembre de 2026. No se ha iniciado simulacion,
busqueda de candidatas ni seleccion. Este protocolo no modifica el bot, los
lotes ni la cuenta. La mejora de telemetria se activa por separado en
`08551e9a86aa716473e43b978c13a65f21003b7a`.

Primero Gold actual, chat `-1003908582492`, universo formal BUY/SELL NOW.
Dubai `-1001642806869`, Gold antiguo y zonas son cohortes separadas. El catalogo
debe conservar cada mensaje con su clasificacion y motivo, incluidos los casos
ambiguos, bloqueados, no ejecutados y fuera del universo. No equivale a estudiar
solo operaciones que el bot llego a abrir.

## Fuentes propuestas

- Periodo UTC propuesto: 22/07/2026 a 02/09/2026, elegido solo por disponibilidad.
  No esta admitido ni certificado y no representa un historico anual completo.
- Inventario: 2159 mensajes, 9276 recepciones, 4606 estados de revision. Hay 763
  mensajes sin original no editado observado y 291 estados sin identidad canonica.
- Ticks seleccionados: 32 XAUUSD y 31 EURUSD; los 63 archivos y metadatos tienen
  SHA256 verificado. Faltan EURUSD del 24/07 y otros pares en dias naturales.
  Seis grupos EURUSD del 23 al 28/08 tienen alternativas no identicas: hay que
  resolver su diferencia o mantener bloqueado el horizonte afectado.
- No descartar fines de semana solo por calendario: el catalogo debe probar que
  no contienen senales elegibles y la cobertura debe incluir todo su horizonte.
- Hay solapamiento con investigacion anterior en 27 dias. El resto tiene
  reutilizacion desconocida. Ningun dia constituye validacion intacta OOS.

Manifiesto inmutable: `runtime_data/study_preparation_20260908_0044/data_inventory/`
`source_readiness_manifest.json`, identidad
`2c30f5c51a41318b955fb621db78843596b24cf2b4868486d2b67809f996af52`.
El informe del inventario conserva todas las rutas, alternativas e incidencias.

## Reglas de ejecucion y dinero

1. Fijar codigo y hashes del parser/catalogo y contratos existentes antes del
   experimento. Cada revision se habilita cuando realmente se recibio; la hora de
   publicacion o edicion no permite anticipar un contenido recibido mas tarde.
   Un original ausente no se reconstruye usando el texto final editado.
2. Usar el motor Gold existente para entradas alternativas, nunca el contrato de
   entradas MT5 inmutables. Mantener Gold 555 como control con interpretacion
   explicita, sin sustituir implicitamente la politica viva por otra simulada.
3. Comprobar Bid/Ask, reloj v3 y cobertura continua por senal y horizonte completo.
   No inventar ticks ni convertir una captura parcial en completa. Conversion
   causal a EUR y metadatos historicos del broker son requisitos independientes.
4. Reconciliar primero operaciones y costes observados. Mantener separados
   `money_contract_verified` y `account_currency_money_verified`; ambos deben
   pasar para conclusiones monetarias. No equiparar pips, movimiento XAU y EUR.
5. Las 74 aperturas historicas contrastadas son evidencia descriptiva, no un
   modelo certificado de fills. Para Gold, decision-respuesta: p50 266 ms,
   p90 648 ms y p99 2399.75 ms, sobre 46 aperturas en dos dias. Mantener escenarios
   por canal; no importar silenciosamente el promedio combinado de Dubai/Gold.
6. El tiempo local de order_send incluye terminal y respuesta del broker, no
   prueba el instante exacto de fill. La entrega de Telegram y la incertidumbre
   de sus segundos de publicacion se modelan separadamente. No sumar de nuevo el
   slippage peticion-fill despues de ejecutar sobre un tick desplazado en tiempo.
7. Las comisiones, swap y tasas de estas entradas son cero en el historial
   observado. Eso NO fija costes nulos para cierres, overnight u otras fechas.
   Faltan la validacion historica completa del dinero y las colas de ejecucion.

Calibracion vigente: `runtime_data/study_preparation_20260908_0044/`
`execution_calibration_v2.json`. Conserva 104 intentos del 3 al 7/09: 74 aperturas
con timing versionado y deal independiente, dos rechazos y 28 intentos antiguos
sin timing/campos suficientes. No se han eliminado del denominador. La version
anterior queda retenida; v2 solo diferencia campos ausentes de discrepancias.

## Condiciones antes de ejecutar

El catalogo causal NOW y la admision por horizonte siguen pendientes. Tambien
deben quedar fijados capital en EUR, volumen por pata, exposicion simultanea,
reglas de margin/stop-out, horizonte y costes, semilla, presupuesto finito de
candidatas/tiempo y condiciones de parada. No se deducen de una cuenta anterior
ni del volumen observado. El presupuesto de busqueda de esta preparacion es cero.

Congelar descubrimiento y evaluacion por dias completos, con todas sus cestas.
Los resultados de una evaluacion usada para volver a ajustar dejan de ser OOS.
Ninguna candidata puede promocionarse automaticamente; la seleccion queda nula
mientras algun control monetario, de matriz completa o validacion aplicable falle.

## Lo que requiere nuevas operaciones

Las nuevas senales demo permitiran verificar captura, ejecucion, shadow,
reintentos, persistencia y contabilidad de la version activa. No se provocan
ordenes de prueba ni se fuerza un reinicio con exposicion abierta.

Esa validacion operativa no certifica una estrategia futura. La validacion
prospectiva de una candidata empezara solo despues de fijar su hipotesis,
parametros y protocolo, sin reutilizar esos resultados para seleccionarla.

No se ha creado una automatizacion de seguimiento. Este documento y los datos
de preparacion siguen locales y no se han publicado en la VM.
