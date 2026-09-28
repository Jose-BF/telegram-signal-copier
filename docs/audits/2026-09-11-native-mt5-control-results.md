# Control Nativo MT5 De La Estrategia 555

## Resultado

El control del **10/09 se ha ejecutado dentro del Strategy Tester de MT5**,
con operaciones, protecciones, cierres y calculo monetario nativos. El de
las dos primeras senales del 11/09 queda pendiente: el probador no incluye
la jornada actual. No se han cambiado sus fechas ni el reloj del ordenador.

| Recorrido del 10/09 | Posiciones | Neto EUR | Minimo de la cesta 2774 EUR |
| --- | ---: | ---: | ---: |
| Operaciones realmente observadas | 22 | 78.77 | -147.50 |
| Simulador propio, caso base conservado | 22 | 78.77 | -237.88 |
| Probador MT5, retraso 0 ms | 22 | 83.57 | -234.62 |
| Probador MT5, retraso 250 ms | 22 | 85.21 | -237.77 |

El minimo es **realizado mas flotante desde el inicio de la cesta**, no
drawdown de cuenta ni flotante puro. Se reconstruye con todos los ticks
retenidos y los fills de cada recorrido, no solo con muestras del robot.
En los cuatro recorridos el minimo de 2774 esta a 12:39:42.008 UTC, con
conversion de 440 ms de antiguedad. En el probador a 250 ms hay -239.66 EUR
flotantes y +1.89 EUR ya realizados: suma -237.77 EUR.

La diferencia de 0.11 EUR respecto al simulador propio apoya que aquella
caida hipotetica no era una cifra arbitraria de nuestra calculadora.
**No valida toda la ejecucion ni reproduce el bot real.** El probador no
recrea las esperas observadas de 31/57 segundos ni el bloqueo del puente
Python-MT5. La explicacion anterior por entradas y exposicion diferentes
sigue siendo compatible con este control, sin calibrar latencias a 2774.

## Universo Y Reglas

Diez Gold NOW del 10/09: las cuatro sin entrada se conservan y los seis
casos con entrada dan 2, 5, 5, 1, 4 y 5 posiciones en ambos ensayos nativos.
La estrategia recibe mensajes fechados y precios, nunca las operaciones
observadas ni los resultados anteriores como entradas.

555: espera adversa 1.0, rebote 1.5, caducidad desde publicacion 30 minutos,
lotes 0.04 + cuatro de 0.03, escalera adversa 1.5 desde primer fill, objetivos
0.5/1/1.5/2/2.5 desde cada fill, SL y trailing 30, proteccion de beneficio
30 EUR con retroceso 1 EUR, salida no negativa a 180 minutos y solo cierres
explicitos del proveedor. Una cesta temporalmente sin posiciones conserva
los refuerzos pendientes hasta su caducidad.

MT5 build 6182, XAUUSD del servidor de la cuenta demo EUR, modo hedging,
contrato 100, dos decimales, stops 20 puntos y freeze 0. Modelo 4, ticks reales,
agente local, sin optimizacion, nube ni agentes remotos. Saldo de prueba
10000 EUR y apalancamiento 1:500 son referencias tecnicas declaradas,
no capital propuesto ni reconstruccion de la cuenta historica.

El historial local del 10/09 coincide exactamente con las ocho columnas
de los 380703 ticks XAUUSD y 82036 EURUSD conservados. El desfase de
10800000 ms se elimina una sola vez. Las doce identidades de los dos
controles quedan congeladas; del 11/09 solo se incluyen 2816 y 2824.

## Dinero Y Diferencias

| Senal | Neto observado / propio EUR | MT5 0 ms EUR | MT5 250 ms EUR |
| --- | ---: | ---: | ---: |
| 2723, 2729, 2738, 2747 | 0.00 | 0.00 | 0.00 |
| 2756 | 4.30 | 4.45 | 4.45 |
| 2774 | 19.81 | 21.18 | 20.83 |
| 2777 | 19.82 | 20.27 | 21.51 |
| 2786 | 1.72 | 2.17 | 2.31 |
| 2795 | 13.33 | 14.95 | 15.32 |
| 2801 | 19.79 | 20.55 | 20.79 |

Los 88 deals de ambos ensayos, 44 posiciones sumadas, quedan enlazados
a cotizaciones ejecutables conservadas. Las 44 salidas se recalculan en
EUR y concuerdan al centimo. Comision, fee y swap nativos son cero en
estos ensayos intradia, no una presuncion general de costes gratuitos.

Todos los cierres son TP nativos. MT5 ejecuta 18 de 22 cierres del escenario
0 ms y 19 de 22 del escenario 250 ms a precio mas favorable que el objetivo;
los restantes coinciden con el objetivo. El control anterior usaba precio
de TP exacto. Esta diferencia de ejecucion de salida queda explicitada,
no corregida restando beneficio ni usada para elegir el mejor resultado.
La descomposicion contable a igual hora de salida figura en la verificacion.

El retraso nativo fijo no equivale al modelo anterior de fill y acuse
separados 250/250 ms. El EA procesa secuencialmente y conserva intenciones
de escalera de un mismo tick, pero no replica la concurrencia Python.
Los campos de solicitud/acuse del CSV registran la ultima cotizacion
conocida, no un reloj independiente de respuesta. En un fill de 2801,
MT5 procesa la ejecucion antes de la nueva cotizacion del mismo milisegundo:
deal 14:20:06.154 UTC a 4364.07, precio anterior de 14:20:06.088; el tick
de 14:20:06.154 es 4364.21. Se conserva esta frontera, sin tolerancia de precio.

Se registran 14 rechazos nativos de modificaciones en el ensayo de 250 ms,
incluidas protecciones de posiciones ya cerradas y distancias no validas.
Cero errores internos del EA, cero posiciones abiertas al corte y cero
aperturas rechazadas. Los rechazos y reintentos no se eliminan del historial.

## Limites De Evidencia

- 2801 mantiene once ticks con conversion de mas de cinco segundos mientras
  tiene exposicion. Su minimo reconstruido es parcial en ambos ensayos;
  no se declara completa la cobertura monetaria de toda la jornada.
- El muestreo OnTick puede omitir extremos durante llamadas. Por ejemplo,
  2786 a 250 ms marca -0.86 EUR en el EA, pero la reconstruccion completa
  da -0.90 EUR. Para 2774, reconstruccion y minimo observado coinciden.
- La comprobacion de precios/cuentas y reglas es retrospectiva, no OOS,
  certificacion del simulador, prueba de rentabilidad ni busqueda masiva.
- El 11/09 nativo incluye cuatro ticks de oro adicionales respecto a la
  captura anterior, antes de la primera entrada, y cambios de flags FX.
  No se borran ni se afirma igualdad exacta de todo ese historial.
- Dos intentos del 11/09 fueron rechazados por fechas antes de simular.
  No existe resultado financiero nativo de ese control. La documentacion
  oficial confirma que la fecha final es exclusiva y no puede superar
  la fecha actual.[^1] Ambos escenarios quedan preparados para una jornada
  ya cerrada, sin nueva tarea programada.

## Reparacion Del Robot De Control

La primera implementacion cerraba la senal al quedar sin posiciones y
omitia los refuerzos futuros. Sus dos ensayos del 10/09 quedan conservados
como **invalidos para comparar la 555**. Una regresion matematica dentro
de MT5 fallo con esa regla y paso tras aplicar el contrato existente de
`signal_lifecycle.py`: mantener la senal si hay patas elegibles y no ha
caducado. No se cambio produccion ni el simulador propio.

La version corregida se compilo sin errores/avisos y se congelo antes de
repetir los dos ensayos del dia 10. Se conservan por separado fuente y
binario inicial, regresion y version corregida. Tambien se conservan dos
intentos de auditoria que exigian incorrectamente tick exacto o prioridad
del tick sobre la ejecucion al coincidir el milisegundo; no se retocaron fills.

36 pruebas Python focales aprobadas: preparacion y contrato de ciclo de vida.
El control nativo comprueba BUY/SELL, caducidad, ticks repetidos, objetivos,
trailing, guardas y finalizacion con patas pendientes. Los 437 archivos de
codigo previamente congelados siguen intactos. No se necesita repetir la
suite de produccion completa para estos artefactos aislados de investigacion.

## Estado Y Archivos

Todo permanece local y sin publicar. No hay commit, push, despliegue, orden
a la cuenta ni acceso a la VM en este trabajo. El MT5 habitual queda abierto
con su perfil anterior y operativa automatica desactivada: demo EUR, mismo
saldo 9531.96, cero posiciones y ordenes pendientes en la comprobacion final.
No quedan ensayos ni procesos auxiliares del probador en ejecucion.

- [Comparacion y operaciones nativas](../../runtime_data/mt5_native_control_20260911/analysis_v4/comparison.json).
- [Plan corregido congelado](../../runtime_data/mt5_native_control_20260911/native_runs_v2/plan.json).
- [Historial nativo y contraste de fuentes](../../runtime_data/mt5_native_control_20260911/native_history_v1/manifest.json).
- [Preparacion y primer obstaculo, antecedentes](2026-09-11-native-mt5-control-preparation.md).
- [Contraste propio del dia 10](2026-09-11-yesterday-gold-retrospective.md).
- [Explicacion de la divergencia real](2026-09-11-execution-divergence-root-cause.md).

[^1]: [MetaTrader 5, Strategy Testing](https://www.metatrader5.com/en/terminal/help/algotrading/testing), apartado de fechas y modalidades de ticks, consultado el 11/09/2026.
