# Relojes Y Cobertura Del Universo Anual

Actualizacion posterior: [regla temporal de entradas aplicada y probada](2026-09-13-canal1-entry-stream.md).
El universo retrospectivo de 736 grupos se conserva intacto; `entry_stream_v3`
contiene los disparadores derivados sin usar sus relaciones manuales futuras.
Este informe sigue describiendo la cobertura de los grupos originales, no
la del flujo nuevo ni una admision automatica al motor.

## Resultado

Cruce offline terminado para las **736 hipotesis de entrada de canal 1**,
del 02/01 al 11/09/2026. Se conservan todos los meses y todos los casos.
No hay simulacion de resultados, seleccion de parametros ni cambio live.

Se comprueban cuatro relojes de mensaje y dos horizontes por entrada:
**5888 filas**, siempre con identidad y motivos de bloqueo. Los recuentos de
cobertura siguientes dependen del horario del broker declarado como hipotesis.
Superar cobertura NO acredita contenido inicial, recepcion, deduplicacion,
ejecucion alternativa ni costes.

| Reloj de inicio | Hora disponible / 736 | Supera 20 min | Supera 4 h |
| --- | ---: | ---: | ---: |
| Referencia de publicacion, diagnostica | 736 | 723 | 621 |
| Primer componente direccional sin editar conservado | 240 | 235 | 204 |
| Primer componente conocido, original o revision | 736 | 724 | 621 |
| Recepcion observada del bot | 0 | 0 | 0 |

Los 496 casos sin componente sin editar siguen en el denominador, no se
rellenan con la hora de publicacion. De los 240 disponibles, 234 coinciden
con la referencia original y seis aparecen despues. De esas 234 referencias
con respaldo inicial, 229 superan el horizonte corto. Las seis posteriores
son 17551, 17567, 17737, 19961, 20289 y 22412; no se adelantan sus datos.

Una revision fechada permite estudiar esa version desde su disponibilidad,
no atribuir su contenido final a una publicacion anterior. Ademas, las
agrupaciones sticker/texto siguen siendo retrospectivas: ninguno de estos
relojes demuestra que esa misma agrupacion pudiera decidirse en tiempo real.
No se necesita una ejecucion historica del bot por cada senal para formular
una simulacion hipotetica, pero hay que declarar su disparador y sus limites.

## Distribucion Por Mes

Cobertura desde la referencia de publicacion, no admision causal:

| Mes de 2026 | Entradas | Supera 20 min | Supera 4 h |
| --- | ---: | ---: | ---: |
| Enero | 75 | 74 | 67 |
| Febrero | 73 | 70 | 63 |
| Marzo | 109 | 109 | 104 |
| Abril | 94 | 91 | 74 |
| Mayo | 91 | 91 | 75 |
| Junio | 97 | 94 | 76 |
| Julio | 73 | 71 | 56 |
| Agosto | 73 | 72 | 63 |
| Septiembre parcial | 51 | 51 | 43 |
| Total | 736 | 723 | 621 |

No se elige un tramo del ano por estos resultados ni se afirma que enero y
septiembre sean equivalentes. Cada reloj tiene sus propias ventanas moviles
recalculadas: 56 dias de desarrollo, 14 de comprobacion y avance quincenal,
con purga de cuatro horas. Por reloj hay 14 ventanas completas y una parcial,
aunque algunas carezcan de entradas con hora disponible.

Publicacion y version conocida mantienen 145 entradas de calentamiento y
591 en una unica comprobacion. El reloj sin editar tiene 55 y 185, ademas de
496 sin hora. Las ventanas no convierten datos ya consultados en OOS nuevo.
El archivo conserva 60 ventanas etiquetadas por reloj, no cuatro muestras
independientes. Al fijar el disparador causal habra que conservar o recalcular
esa pertenencia segun corresponda.

## Horario Del Broker

Vantage publica servidores GMT+2 en invierno y GMT+3 durante el horario de
verano, con medianoche alineada a las 17:00 de Nueva York. Su calendario de
2026 situa el inicio estadounidense el 8 de marzo:
[referencia oficial de Vantage](https://www.vantagemarkets.com/en/academy/forex-trading-time/).

Para este diagnostico se congela +7200 segundos antes del 08/03 a las 07:00 UTC
y +10800 despues. Ese instante se toma de la transicion de Nueva York, no de
una observacion del servidor. Aplicar la regla de reloj del servidor a estos
epochs raw de la API es una **hipotesis declarada**, no una prueba anual.
Se contrasta el calendario contra `America/New_York`: 256 dias, cero diferencias.
No se ha usado la hora de Madrid, el cambio europeo del 29/03, precios del
proveedor ni resultados de estrategias para decidir el desfase.

Las 63 referencias previas de julio-septiembre declaran +10800 y coinciden
con la regla. De ellas, 28 contienen una referencia directa a un fill: se
reproduce en los precios nuevos su epoch, lado Bid/Ask y diferencias de precio
y tiempo almacenadas, sin discrepancias. No se han vuelto a validar los logs
originales de esos fills, por lo que esto es corroboracion de la referencia
retenida, no una nueva certificacion independiente del fill.

Las otras 35 referencias quedan como metadatos conservados: tres heredan un
dia adyacente, una resume ticks observados y 31 EURUSD heredan el contrato de
XAUUSD. No se presentan como 63 pruebas independientes, ni se extrapola esa
evidencia empirica a enero-junio. Los datos raw permanecen intactos y no se
generan caches con `semantic_time_valid=true` ni se elude el cargador estricto.
La normalizacion es solo en memoria, conserva duplicados y milisegundos,
y rechaza epochs ambiguos, sin correspondencia o con inversion temporal.

## Continuidad Y Limites

Se reutiliza la comprobacion existente de `research/strategy_study.py`, sin
modificar el motor ni ampliar tolerancias. El protocolo queda vinculado por
hash al diagnostico corto anterior: maximo cinco segundos entre cotizaciones
de oro, antiguedad FX de cinco segundos y convencion historica existente de
intervalo FX de hasta 60 segundos. Esta ultima usa el precio anterior, nunca
el precio de la cotizacion futura; sigue siendo una hipotesis de validacion.
Los tramos que cruzan medianoche del servidor quedan fuera del supuesto
intradia sin swap. Pasar esa comprobacion no valida costes historicos.

Los 13 casos que no superan 20 minutos desde publicacion tienen algun intervalo
de oro superior a cinco segundos. Sus maximos van de 5015 a 11050 ms. IDs:
16902, 17638, 17699, 17803, 18852, 18917, 18986, 20030, 20341, 20447, 20693,
20700 y 21543. Se guarda el inicio y final del primer intervalo que incumple,
su duracion y el maximo del horizonte. No son automaticamente descargas
corruptas: un intervalo sin nuevas cotizaciones tambien puede ser autentico.
No se ha ampliado el limite para hacerlos pasar.

En cuatro horas fallan 115 referencias de publicacion. Motivos no excluyentes:
112 superan el intervalo de oro, 48 cruzan la frontera intradia, 31 tienen
distancia excesiva al final, 11 no alcanzan el horizonte con la cinta cargada
y siete incumplen FX. No hay archivos diarios ausentes en las ventanas leidas.
Los dias vacios de la extraccion no se convierten en cierres probados de mercado.
Cuatro horas son el sobre de datos para espera, ejecucion y desenlace eventual,
no una duracion elegida por rentabilidad ni permiso para omitir la cola de salida.

Los diez casos del 20/08, 21/08 y 01/09 superan ambos horizontes desde
publicacion con los precios nuevos. Esto resuelve su cobertura actual bajo
esta hipotesis; **no cierra el impacto sobre estudios antiguos** que pudieran
haber usado caches incompletas o la columna en segundos. Las versiones previas
y su incidencia siguen conservadas.

## Evidencia Y Verificacion

- [Protocolo congelado](../../runtime_data/canal1_history_20260913/coverage_v1/protocol.json).
- [Resumen y recuentos por mes y ventana](../../runtime_data/canal1_history_20260913/coverage_v1/summary.json).
- [5888 comprobaciones por entrada](../../runtime_data/canal1_history_20260913/coverage_v1/coverage.jsonl).
- [Ventanas recalculadas por reloj](../../runtime_data/canal1_history_20260913/coverage_v1/rolling_folds.jsonl).
- [63 referencias horarias y su alcance](../../runtime_data/canal1_history_20260913/coverage_v1/clock_references.jsonl).
- [Manifiesto de fuentes y resultados](../../runtime_data/canal1_history_20260913/coverage_v1/manifest.json).

Identidad de auditoria:
`c6b19beaa51888f0065fe2b11368bdbb6fe4bda51e25367d266a103ae0ac980b`.
Universo de entrada inalterado:
`db038d3597ecfbba177ef723ea0eaee0c9e814dca6d8fddc83d2c8d1c549dbb6`.

Relectura final independiente: **1021 archivos vinculados y cinco resultados,
cero diferencias de hash**. Conciliacion de las 5888 filas con el universo:
cero diferencias de identidad, direccion, canal, reloj y duracion del horizonte.
Los archivos fuente raw y los 63 pares de precios/metadatos antiguos permanecen
sin cambios. El catalogo y sus fuentes JSON/media se vuelven a validar al terminar.

`py -3.14 -m pytest -q tests/test_dubai_annual_coverage.py tests/test_dubai_annual_universe.py tests/test_dubai_export_catalog.py tests/test_telegram_exports.py tests/test_isolated_mt5_history.py tests/test_audit_isolated_mt5_extract.py tests/test_strategy_study.py tests/test_study_fx_interval_contract.py`:
**228 pruebas aprobadas**, incluidas 19 nuevas. Cubren cambio horario, ambiguos,
segundos frente a milisegundos, duplicados, medianoche, FX sin precio futuro,
huecos, colas, hashes, archivo inmutable y pertenencia temporal de las ediciones.
Python 3.14.2. La importacion del auditor no carga MetaTrader5 ni `main`.
No se modifica logica compartida de ejecucion o dinero.

## Que Queda

Esta fase cierra el inventario de cobertura por entrada bajo un reloj declarado.
No obliga a obtener nueva telemetria para cada senal historica ni acredita por
si sola el dataset ejecutable. El siguiente trabajo es fijar y probar la regla
causal que une stickers y textos, decide que hacer con ediciones y dispara
nuestras entradas; despues, enlazarla al motor con ejecucion y dinero explicitos.
El escenario desde la primera version conocida conserva el ano completo; el
subconjunto sin editar sirve como contraste separado, no como muestra anual
automaticamente representativa. Ninguna comparacion debe seleccionar solo los
casos que pasan y esconder los restantes.

Todo queda local y sin publicar: cero evaluaciones de estrategias, sin ordenes,
arranque de MT5, acceso a VM, reinicio del bot, commit ni push en este bloque.
La limpieza de las dos copias temporales de autenticacion fuera de OneDrive
sigue pendiente del bloqueo del entorno documentado en la fase anterior;
no se vuelve a intentar ni se elude.
