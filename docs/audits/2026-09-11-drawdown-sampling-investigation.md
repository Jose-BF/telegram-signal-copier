# Muestreo Del Minimo Real De Gold 2774

## Conclusion

La hipotesis del usuario es correcta como mecanismo: un registro periodico
puede omitir una perdida breve entre consultas. En este caso se confirma
una omision pequena, **pero no explica la diferencia de unos 90 EUR**.

| Fuente para la misma cesta | Minimo EUR |
| --- | ---: |
| Ocho snapshots periodicos detallados | -142.83 |
| Minimo guardado en el resumen final del bot | -146.19 |
| Consultas frecuentes de la guarda de beneficio | -147.34 |
| Reconstruccion con fills reales y todos los ticks retenidos | -147.50 |
| Reconstruccion con fills hipoteticos del simulador propio | -237.88 |

Todas las filas se refieren a realizado mas flotante desde el inicio de
la cesta. No son drawdown maximo de cuenta, ni dolares, ni flotante puro.
La cifra -147.50 citada en los controles anteriores ya era una reconstruccion
historica; no era un minimo proporcionado por el terminal ni una lectura
ocasional del bot. Esta distincion no habia quedado suficientemente clara.

## De Donde Sale El Registro Live

El codigo historico `892bc33c8` y los archivos actuales coinciden exactamente
en `position_lifecycle_monitor.py`, `journal.py` y `config.py`.

- `_signal_pl_summary` suma el beneficio nativo de las posiciones de la
  senal consultando `mt5.positions_get()`, y anade beneficios realizados
  confirmados cuando la informacion esta completa.
- La guarda de beneficio consulta mas frecuentemente. Hay 11358 muestras
  de esta cesta; separacion mediana 63 ms y maxima 63.359 s. El maximo
  incluye las esperas operativas ya investigadas; no es la separacion
  entre ticks historicos del mercado ni el intervalo alrededor del minimo.
- La actualizacion del minimo del resumen esta limitada a cada 5 segundos
  cuando el bucle puede avanzar. `journal.update_extremes` solo conserva
  el mejor/peor valor que recibe; no reconstruye lo ocurrido entre consultas.
- Los snapshots detallados son otra serie, todavia menos frecuente. En
  esta ejecucion aparecen aproximadamente cada 120 segundos, con retrasos;
  no debe confundirse el antiguo comentario de 30 segundos con el valor
  configurado o con la frecuencia de las guardas.

El resumen final tiene MAE -146.19 EUR. Una observacion frecuente del bot
registra -147.34 EUR a 12:39:43.324 UTC, tres posiciones y 0.09 lotes,
flotante -154.23 y realizado +6.89. Esa observacion es mas negativa que
el minimo del resumen, prueba directa de la limitacion de su muestreo.
El timestamp de la cotizacion y la consulta de beneficio no son una
captura atomica; no se equiparan sus relojes ni se infiere latencia de red.

## Comprobacion Independiente

Se vuelven a valorar las cinco posiciones reales desde sus aperturas hasta
el ultimo cierre, sin simular ninguna entrada o salida distinta. Los diez
deals se enlazan con el archivo nativo conservado. Se verifican hashes del
segmento comprimido, contenido descomprimido, 124682 filas y manifiesto.
Los eventos seleccionados conservan numero de linea y hash.

Se examinan **14024 ticks XAUUSD** durante la vida real de la cesta, con
Bid/Ask, conversion EURUSD causal y redondeo por posicion. No hay conversion
fuera del limite en este recorrido. El minimo vuelve a ser -147.50 EUR a
12:39:42.008 UTC, Bid 4324.03 y conversion de 440 ms de antiguedad:

`-154.39 EUR flotantes + 6.89 EUR realizados = -147.50 EUR`.

Ninguno de esos ticks lleva las posiciones reales a un saldo de -200 EUR
o inferior. En los dos segundos centrados en el minimo, el mayor intervalo
entre ticks es 94 ms. Una consulta nueva, exclusivamente de lectura, obtiene
cinco velas M1 de MT5 local, 12:37-12:41 UTC normalizado. Apertura, maximo,
minimo y cierre coinciden con los ticks retenidos en las cinco velas; la
de 12:39 tambien tiene minimo Bid 4324.03.

Las velas corroboran la cinta del mismo proveedor; no son un segundo mercado
independiente. Tampoco se afirma conocer cotizaciones ausentes tanto de los
ticks como de las velas del broker. Dentro del historial disponible, no hay
evidencia del supuesto pico de -200/-237 EUR en las posiciones reales.

## Por Que Persiste La Diferencia

La correccion del minimo del resumen es de **1.31 EUR**: -146.19 pasa a
-147.50. Tras corregirla, quedan **90.38 EUR** entre posiciones reales e
hipoteticas. Por tanto, no procede atribuir esa diferencia al muestreo.

En el mismo tick minimo hay tres posiciones reales abiertas frente a cuatro
hipoteticas. El refuerzo B3 real ya habia cerrado por TP a 12:35:49.916 UTC,
realizando +5.17 EUR; el hipotetico sigue abierto y aporta -59.57 EUR.
Esa pata explica 64.74 EUR de diferencia. Los diferentes precios de entrada
de las otras tres posiciones explican los restantes 25.64 EUR.

No es que el mercado haya tenido dos minimos de precio distintos para estos
dos calculos: se aplica el mismo tick a exposiciones diferentes.

## Consecuencia Practica

Para controles historicos debe utilizarse el minimo reconstruido con deals
y ticks verificados, indicando cobertura, y conservar aparte el minimo
observado por el bot. Muestreo live mas frecuente puede reducir omisiones,
pero no garantiza extremos completos ni resuelve bloqueos del proceso.
La frecuencia de observacion y la frecuencia de escritura no tienen por
que ser iguales; cualquier cambio operativo requiere su propia reparacion
y comprobacion, no se ha implementado incidentalmente en esta investigacion.

Tres pruebas sinteticas comprueban el pico perdido entre muestras, una pata
cerrada antes del pico y la distincion entre flotante y realizado mas flotante.
Los 437 archivos de codigo previamente congelados siguen intactos. Solo
analisis local y lectura de cinco velas; sin ordenes, cambios del bot,
acceso a la VM, publicacion ni nuevas tareas programadas.

## Evidencia

- [Resultado detallado](../../runtime_data/drawdown_sampling_audit_20260911/result.json).
- [Velas nativas de contraste](../../runtime_data/drawdown_sampling_audit_20260911/native_bars.json).
- [Recorrido de las posiciones reales](../../runtime_data/drawdown_sampling_audit_20260911/actual_positions_full_tick_path.csv).
- [Eventos originales seleccionados](../../runtime_data/drawdown_sampling_audit_20260911/retained_event_excerpt.json).
- [Explicacion anterior por posiciones](2026-09-11-execution-divergence-root-cause.md).
- [Control nativo MT5](2026-09-11-native-mt5-control-results.md).
