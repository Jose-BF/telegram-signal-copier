# Resultado De Tiempos Y Transferencia

## Conclusion

La prueba pedida se ha ejecutado: **96 pares de tiempos sobre las diez
senales del 10/09**, con dos selecciones congeladas antes de contrastarlas
en las siete senales del 09/09 y las primeras dos conservadas del 11/09.
Ninguna de esas dos selecciones es una correccion general validada.

Una espera de 20 s hasta fill y 10 s adicionales hasta acuse casi iguala
el minimo de 2774: **-148.09 EUR frente a -147.50 EUR observado reconstruido**.
Sin embargo, lo consigue cerrando antes **B2**, mientras en real habia
cerrado **B3**. Ademas empeora otras senales del propio dia y el contraste.
Es una coincidencia de una estadistica, no reproduccion de la misma causa.

No se adoptan esos tiempos. No se han cambiado los motores, la estrategia,
el bot ni sus parametros de produccion. No hubo publicacion ni ordenes.

## Por Que Cerro La Posicion Real

Los deals nativos y la confirmacion de modificacion enlazados al mismo ticket
permiten reconstruir esta secuencia del 10/09, en UTC:

| Evento B3 | Hora | Precio |
| --- | --- | ---: |
| Entrada real | 12:35:34.867 | 4340.54 |
| TP exacto confirmado por el bot | 12:35:38.255 | 4342.54 |
| Primer tick retenido que cruza ese TP instalado | 12:35:49.870 | Bid 4342.63 |
| Cierre nativo, motivo TP | 12:35:49.916 | 4342.54 |

El cierre nativo sigue al primer cruce por 46 ms. Su objetivo estaba
confirmado con anterioridad. No hace falta inventar un cierre discrecional,
una posicion extra o una perdida oculta para explicar esa salida.[^1]

La B3 del control 250/250 entra a 4347.05 y tiene objetivo 4349.05; sigue
abierta durante el minimo. Cambian entrada, precio y objetivo absoluto,
aunque volumen y distancia relativa de beneficio sean iguales.

### La Espera No Determina Por Si Sola El Precio

El control condicionado toma la solicitud B3 realmente registrada y prueba
241 demoras de 0 a 60 s, cada 250 ms, manteniendo una distancia TP de 2.
97 escenarios alcanzan su objetivo antes del minimo real; los otros 144 no.
La relacion no es monotona: esperar mas puede mejorar o empeorar segun la
trayectoria. Este control suministra la solicitud observada y supone el
objetivo activo tras el fill; **no es una simulacion completa ni predictiva**.
Sus resultados no participan en la seleccion de los 96 escenarios.[^2]

Incluso usando exactamente los 56857 ms registrados entre solicitud y fill,
la regla de ejecutar al primer Ask historico posterior produce **4341.28**,
no el fill nativo **4340.54**. El ultimo Ask anterior al timestamp del deal es
4341.25, 9 ms antes; el siguiente es 4341.28, 65 ms despues. El objetivo de
esa hipotesis seria 4343.28 y el mayor Bid hasta el minimo fue 4343.05:
no habria cerrado antes de la caida. No se identifica con esto el mecanismo
interno de precios del servidor ni un desfase general de reloj.[^1][^2]

Hay que distinguir **cuando puede decidir/enviar el cliente**, **cuando se
registra una ejecucion**, **a que precio se ejecuta** y **cuando conoce el
resultado el bot**. Copiar una duracion no iguala automaticamente las otras
variables. Se mantiene el diagnostico previo de bloqueo/colas e intenciones,
sin atribuir toda la diferencia exclusivamente al broker.[^3]

## Resultado Del Ajuste

Todos los resultados de la rejilla se conservan, no solo sus ganadores.
Los criterios se fijaron antes: el ajuste de cesta prioriza completitud,
numero/volumen y error de minimo; el ajuste de jornada prioriza completitud,
numero/volumen y error medio absoluto de hora de entrada. Precio y salida
desempatan. No se eligio por beneficio.[^4]

| Criterio del 10/09 | Control | Ajuste de cesta | Ajuste de jornada |
| --- | ---: | ---: | ---: |
| Espera fill / acuse, segundos | 0.25 / 0.25 | 20 / 10 | 0 / 2 |
| Minimo cesta 2774, EUR | -237.88 | -148.09 | -231.72 |
| Refuerzo cerrado antes del minimo | Ninguno | B2 | Ninguno |
| Error medio hora de entrada del dia, s | 11.253 | 36.000 | 10.769 |
| Errores de numero/volumen del dia | 0 | 4 | 0 |
| Beneficio total simulado, EUR | 78.77 | 96.88 | 78.77 |

En real el refuerzo cerrado era B3 y el beneficio observado del dia era
78.77 EUR. La posicion inicial tambien estaba cerrada en los cuatro mundos.
El error horario de posiciones emparejadas se informa siempre junto a las
posiciones que faltan/sobran: una media menor no borra las no emparejadas.

## Contraste Sin Reajuste

Las dos selecciones y sus criterios quedaron congelados en `selection.json`
antes de ejecutar estos escenarios en los otros dias. Se incluyo tambien
el control 250/250. No se amplifico la rejilla ni se eligio otro ganador al
ver los resultados del contraste.[^5]

| Periodo / metrica | Control | Ajuste de cesta | Ajuste de jornada |
| --- | ---: | ---: | ---: |
| 09/09: senales conservadas | 7 | 7 | 7 |
| 09/09: error medio hora de entrada, s | 2.528 | 24.406 | 2.824 |
| 09/09: errores de numero/volumen | 0 | 3 | 0 |
| 09/09: beneficio simulado, EUR | 48.96 | 48.96 | 48.96 |
| 11/09, primeras dos: error hora de entrada, s | 0.557 | 19.244 | 0.800 |
| 11/09, primeras dos: beneficio simulado, EUR | 3.44 | 31.10 | 3.44 |

Los importes observados son 48.96 EUR el 09/09 y 3.44 EUR en esas dos senales
del 11/09. **La igualdad agregada del 09/09 oculta errores que se compensan**:

| Senal / ajuste de cesta | Posiciones reales | Simuladas | Diferencia EUR |
| --- | ---: | ---: | ---: |
| 2674 | 4 | 5 | +6.44 |
| 2682 | 3 | 2 | -3.86 |
| 2693 | 2 | 1 | -2.58 |

Su suma es cero, pero no son las mismas operaciones. El ajuste elegido por
la jornada completa tampoco mejora los errores de entrada del 09/09 ni del
11/09. Esto no prueba que ningun modelo de latencia pueda generalizar; si
impide presentar estos dos pares constantes como solucion validada.

## Muestra Y Cobertura

- Diez senales del 10/09, siete del 09/09 y dos de la ventana ya conservada
  del 11/09: **19 identidades**, incluidas cinco sin entrada en el control.
  El 11/09 no se presenta como jornada completa.
- Para el 09/09 se verificaron ocho segmentos contiguos: 381089 eventos y
  475 observaciones raw. Las siete NOW se conservan con su hora publicada
  y su disponibilidad registrada, incluidas recepciones tardias. El diario
  no acredita mensajes publicados que nunca llegaran a registrarse.
- Los nuevos historicos del 09/09 contienen 647273 ticks XAUUSD y 120353
  EURUSD. Su tramo comun con las capturas del dia coincide exactamente en
  tiempo y precios. En EURUSD cambia el bit 128 de `flags` en parte de las
  filas; se retienen ambos originales. Ese campo no se consume en este
  modelo de Bid/Ask y tiempos. No se declara igualdad de todos los campos.
- Las 16 posiciones nativas del 09/09 se enlazan a sus ordenes y 32 deals,
  con netos recalculados al centimo y costes observados cero. Los seis
  snapshots de las senales que entraron coinciden en los diez campos de
  estrategia contrastados. Eso no equivale a igualdad de codigo operativo.
- No se ocultan huecos FX: 2801 conserva once ticks invalidos; 2668 tiene
  tres en su recorrido real y cero/uno en los escenarios; el ajuste de
  cesta genera 118 ticks invalidos en 2674 y 112 en 2816. Sus minimos son
  parciales, aunque el motor anterior no lo advierta. Los importes y casos
  permanecen; no se certifica su riesgo completo ni se amplian los 5 s.
- Las cifras de riesgo son minimo de realizado mas flotante por cesta en
  EUR, no drawdown conjunto de cuenta ni comprobacion de margen/stop-out.

El contraste es retrospectivo y sin reajuste en este experimento. Parte de
estos dias se habia investigado anteriormente: **no es OOS totalmente intacto**.

## Verificacion Y Siguiente Paso

Las 960 evaluaciones de descubrimiento y las 27 de contraste se reprodujeron.
Los tres motores concuerdan en las 57 combinaciones senal/escenario
seleccionadas para comprobacion independiente; se recalcularon sus 57
recorridos monetarios con los precios y conversion causal. Las 146 pruebas
focales pasan. La version inicial de verificaciones y las salidas de los
intentos interrumpidos se conservan; no se retocaron los resultados.[^6]

El cierre real ya queda explicado. Lo pendiente es reproducir la formacion
de esa entrada con un modelo mas completo de cliente y ejecucion. La
reparacion siguiente debe tratar la cola compartida, las decisiones retenidas
durante esperas y la incertidumbre conjunta de tiempo/precio; no sustituir
el control por 20/10 ni por 0/2. Sus reglas se deberan fijar antes de otra
validacion. Los casos de este experimento quedan como regresiones, no como
una reserva intacta para seguir ajustando y llamarlo validacion nueva.

Estado: investigacion y artefactos locales, sin commit, push, acceso a la VM,
reinicio del bot, cambios de estrategia, ordenes ni automatizacion. Se abrio
MT5 local para lectura historica, con operativa automatica desactivada.

## Referencias

[^1]: [TP real, cruce y cotizaciones adyacentes](../../runtime_data/timing_transfer_20260912/early_close_evidence.json).
[^2]: [241 demoras de una solicitud condicionada](../../runtime_data/timing_transfer_20260912/conditioned_target.json).
[^3]: [Diagnostico previo de bloqueos y ejecucion](2026-09-11-execution-divergence-root-cause.md).
[^4]: [Protocolo congelado](../../runtime_data/timing_transfer_20260912/protocol.json), [rejilla completa resumida](../../runtime_data/timing_transfer_20260912/discovery_summary.json).
[^5]: [Selecciones previas al contraste](../../runtime_data/timing_transfer_20260912/selection.json), [resultados del contraste](../../runtime_data/timing_transfer_20260912/challenge_summary.json).
[^6]: [Verificacion reproducida](../../runtime_data/timing_transfer_20260912/verification_v2.json), [todos los recorridos y cobertura](../../runtime_data/timing_transfer_20260912/risk_and_path_comparison_v2.json), [pruebas focales](../../runtime_data/timing_transfer_20260912/tests.xml).
