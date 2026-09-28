# Precios Anuales Extraidos De MT5

Actualizacion posterior: [relojes y cobertura por entrada terminados](2026-09-13-canal1-annual-coverage.md).
723/736 referencias superan 20 minutos y 621/736 cuatro horas, bajo reloj
declarado +2/+3; esto no admite datos al motor ni acredita todo el reloj anual.
Los pendientes y pruebas de esta extraccion se conservan abajo como su estado
al cierre original. La limpieza temporal y el impacto sobre estudios antiguos
siguen abiertos.

## Resultado

El usuario autorizo abrir una instancia aislada de MT5, sin asesores y con
trading desactivado, exclusivamente para obtener el historico. Extraccion
terminada desde la conexion **VantageMarkets-Demo**, sin mezclar manualmente
los historicos de los dos servidores anteriores.

Se consultaron los dias de epoch fuente del 01/01/2026 al 13/09/2026 inclusive
para XAUUSD y EURUSD. Septiembre es parcial; no se afirma cobertura futura del
resto del dia de descarga. Los ultimos dias con cotizaciones son del 11/09.

| Concepto | XAUUSD | EURUSD |
| --- | ---: | ---: |
| Dias calendario consultados | 256 | 256 |
| Dias con cotizaciones | 180 | 181 |
| Dias sin cotizaciones devueltas | 76 | 75 |
| Cotizaciones conservadas | 125156798 | 25320325 |
| Archivos diarios con tres lecturas coincidentes | 180 | 181 |
| Filas Bid/Ask invalidas detectadas | 0 | 0 |
| Inversiones temporales detectadas | 0 | 0 |
| Discrepancias segundos/milisegundos en datos nuevos | 0 | 0 |

Total: **150477123 cotizaciones**, 361 archivos de precios y 512 registros
diarios de estado; 970767179 bytes de precios comprimidos, aproximadamente
0.97 GB. Cada dia con datos coincide entre una lectura completa y la union de
sus dos mitades, comparando todos los campos raw y preservando duplicados.
Se releen los Parquet para comprobar hashes, filas, cotizaciones y tiempos.

Entre lunes y viernes, XAUUSD no devuelve cotizaciones el 01/01 y el 03/04;
EURUSD no las devuelve el 01/01. Se conservan como resultados vacios, no como
prueba automatica de cierre de mercado. La coincidencia de lecturas no prueba
por si sola que el broker conserve cada tick historico ni que no haya huecos.

## Representacion Del Ano

| Mes | Dias XAUUSD | Dias EURUSD | Ticks XAUUSD | Ticks EURUSD |
| --- | ---: | ---: | ---: | ---: |
| Enero | 21 | 21 | 15879903 | 2676456 |
| Febrero | 20 | 20 | 14678319 | 2661665 |
| Marzo | 22 | 22 | 17390662 | 4337379 |
| Abril | 21 | 22 | 13910514 | 3241244 |
| Mayo | 21 | 21 | 13064148 | 2874083 |
| Junio | 22 | 22 | 15022676 | 2927526 |
| Julio | 23 | 23 | 15295140 | 2973312 |
| Agosto | 21 | 21 | 13909591 | 2496180 |
| Septiembre parcial | 9 | 9 | 6005845 | 1132480 |

Ya no dependemos solo de los caches de julio-septiembre para preparar el
estudio anual. Tambien se recupero EURUSD de mayo, que no aparecia en las
carpetas nativas inventariadas. Las ventanas del universo de 736 entradas
siguen congeladas; no se han ajustado a resultados de rentabilidad.

## Incidencia En Tres Caches Anteriores

Se compararon 63 dias/simbolo y **23785657 cotizaciones** del inventario anterior.
60 dias coinciden exactamente en secuencia, tiempo fuente, Bid y Ask.
Los otros tres son XAUUSD:

| Dia UTC segun referencia previa | Filas antiguas | Filas nuevas | Filas adicionales |
| --- | ---: | ---: | ---: |
| 20/08/2026 | 597125 | 692300 | 95175 |
| 21/08/2026 | 317441 | 649318 | 331877 |
| 01/09/2026 | 688636 | 717350 | 28714 |

En esos tres archivos antiguos `time_msc` esta en segundos, aunque el nombre
indica milisegundos. Sus columnas `source_time_msc` y `time_utc` SI conservan
los milisegundos compatibles con el desfase de 10800 segundos de sus metadatos.
No se atribuye por tanto el fallo a todas las representaciones temporales.

Todas las cotizaciones antiguas estan presentes en los nuevos datos, incluidas
sus multiplicidades: cero cotizaciones antiguas ausentes o con precio cambiado
en la comparacion multiconjunto. Hay **455766 adicionales**. El cache antiguo
del 21/08 acaba a las 11:08:42.951 UTC, frente a 20:56:59.930 en la nueva lectura;
el del 01/09 acaba a las 20:57:59.201, frente a 23:59:59.919. El 20/08 conserva
extremos completos, pero no todas las cotizaciones intermedias.

Se preservan ambas versiones: no se sobrescribieron originales ni se borraron
dias del denominador. Es una incidencia reproducible por rutas, hashes y
comparacion; no una diferencia que se deba ocultar ampliando tolerancias.
El efecto sobre estudios anteriores **no esta evaluado**: depende de que
archivo, columna temporal y tramo utilizara cada estudio. No se declara
invalida toda la investigacion ni se reutilizan esos archivos como equivalentes
sin revision. En el catalogo anual hay diez entradas de canal 1 en esas fechas:
21465, 21470, 21474, 21478, 21488, 21497, 21502, 21959, 21968 y 21977.

## Aislamiento Y Seguridad

- Copias en `C:/Users/josea/AppData/Local/Codex/MT5History/20260913-v1` y `20260913-v2`,
  fuera de OneDrive. Ningun perfil, asesor, script, servicio ni historial de
  operaciones se copio del terminal habitual. Los caches de ambos servidores
  se conservaron en carpetas separadas; la extraccion API se vincula al servidor actual.
- Arranque portable con configuracion propia, sin programas de inicio, asesores,
  DLL, correo ni notificaciones. La configuracion de inicio independiente esta
  documentada por [MetaTrader](https://www.metatrader5.com/en/terminal/help/start_advanced/start).
- Se verificaron por API ruta del ejecutable y datos, cuenta demo, servidor,
  `trade_allowed=false`, `tradeapi_disabled=true` y `dlls_allowed=false` antes
  de leer precios y antes de cada dia. MT5 expone estas comprobaciones mediante
  [terminal_info](https://www.mql5.com/en/docs/python_metatrader5/mt5terminalinfo_py).
- Build observado 6182; paquete Python MT5 5.0.5640. La cuenta de conexion usa
  EUR; esto es metadato actual, no prueba de costes o condiciones historicas.
  No se exportaron contrasenas, numero de cuenta, saldos ni operaciones.
- La primera copia funciono en la prueba de seis muestras. Al reabrirse inicio
  un actualizador y salio; se detuvo solo ese actualizador, identificado por
  su ruta de destino y configuracion aisladas. Se corrigio la limpieza de
  procesos auxiliares con una prueba de regresion. La segunda copia completo
  la extraccion en un solo arranque, sin desactivar las actualizaciones del sistema.
- Verificacion final: cero procesos MT5, cero programas compilados `.ex5` y
  cero perfiles de graficos en ambas copias. Los **22 archivos originales**
  contrastados siguen intactos: ejecutable, configuracion, bases de acceso y
  los 18 archivos nativos de precios seleccionados.
- Se intento eliminar las copias temporales `config/accounts.dat` de las dos
  instancias al terminar. El entorno rechazo esa limpieza por politica y no
  se intento eludirla. **Esas dos copias de autenticacion siguen presentes**
  en las carpetas aisladas, fuera de OneDrive; los terminales estan cerrados.
  La limpieza de esos archivos queda pendiente de intervencion autorizada.

No se accedio a VM, no se reinicio el bot, no se enviaron ordenes y no hay
commit ni push. Los cambios de herramientas e informes permanecen locales.

## Reloj Y Admision Pendientes

Los precios nuevos conservan el epoch original de MT5. No se resta
silenciosamente el desfase de verano a todo el ano. Vantage describe GMT+2
en invierno y GMT+3 con horario de verano, alineado al cierre de Nueva York:
[horario de servidor](https://www.vantagemarkets.com/media/vantage-xauusd-trading-conditions-geo/).
Esta referencia orienta el contraste, pero no sustituye por si sola la prueba
del reloj de cada tramo exigida por los contratos existentes.

La comparacion con caches previos usa solo el desfase documentado por cada
referencia; no lo extrapola a enero-junio. Los datos nuevos siguen separados
de los caches admitidos al motor y marcados `engine_dataset_ready=false`.
Falta validar reloj historico/cambios de horario, huecos y cobertura por senal
y horizonte, costes/contratos historicos y politica causal de mensajes editados.
La purga y las ventanas se recalcularan con el reloj finalmente admitido.
No se ejecuto simulacion ni seleccion de estrategias en este bloque.

## Archivos Y Pruebas

- [Contrato raw](../../runtime_data/canal1_history_20260913/raw_mt5_v2/contract.json),
  [conexion verificada](../../runtime_data/canal1_history_20260913/raw_mt5_v2/binding.json).
- Precios y metadatos diarios en `runtime_data/canal1_history_20260913/raw_mt5_v2/XAUUSD/`
  y `runtime_data/canal1_history_20260913/raw_mt5_v2/EURUSD/`.
- [Auditoria integral e inventario con hashes](../../runtime_data/canal1_history_20260913/raw_mt5_v2_audit.json).
- [Incidencias de los tres archivos anteriores](../../runtime_data/canal1_history_20260913/raw_mt5_v2_reference_incidents.json).
- [Prueba inicial de seis muestras](../../runtime_data/canal1_history_20260913/isolated_probe_v1.json).

`raw_mt5_v1` conserva solamente el contrato del intento interrumpido por la
actualizacion, sin archivos de cotizaciones. La version raw vigente es `raw_mt5_v2`.

Hashes: contrato `885f642c06ec7a819005efb9a2dd2efcc32f0e5c6eecf497b07d23632aee5511`;
auditoria `10ec1c8bdb82358dc999477c9469fd3127ab6f251fefce47fb8c80ac629141e9`;
incidencias `1173f84603502fb031b93ad7c24c31b1a2ca89bdaba2b5e94ebb13edc74e1b4b`.

Python 3.14.2, pandas 3.0.2, pyarrow 23.0.1, psutil 7.2.2 y pytest 9.0.3.
`py -3.14 -m pytest -q tests/test_isolated_mt5_history.py tests/test_audit_isolated_mt5_extract.py tests/test_dubai_annual_universe.py tests/test_dubai_export_catalog.py tests/test_telegram_exports.py`:
**94 pruebas aprobadas**. Verificados aislamiento, bloqueo de trading,
propiedad del actualizador, limites de intervalos, duplicados, integridad,
comparacion exacta y las 76 pruebas previas de mensajes/ventanas. La auditoria
real relee los 361 Parquet y verifica los 512 registros diarios.
No se modificaron motores, parser ni rutas compartidas live/dinero.

Siguiente paso: admitir el reloj y los horizontes de precios para las 736
entradas de trabajo, manteniendo las limitaciones de edicion y estas incidencias
visibles. Revision del impacto historico antes de reutilizar los tres caches afectados.
