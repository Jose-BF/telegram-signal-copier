# Por Que No Coinciden La Realidad Y La Simulacion

## Respuesta Directa

Caso investigado: Gold 2774, jueves 10/09/2026. La referencia para saber
que sucedio son las operaciones efectivamente ejecutadas y conciliadas.
El resultado simulado no reemplaza esos hechos.

Si somos capaces de reconstruir el recorrido de las posiciones reales con
los datos disponibles: vuelve a dar -147.50 EUR. Lo que aun no conseguimos
es que una simulacion independiente genere ese mismo comportamiento desde
mensajes y precios, sin proporcionarle las operaciones finales observadas.
Son pruebas distintas; esa distincion habia quedado poco clara.

La divergencia actual no es una imposibilidad misteriosa ni un residuo de
90 EUR que debamos asignar genericamente al broker: tiene causas concretas
en las entradas y en una salida. No autoriza una correccion estadistica que
oculte esas diferencias ni una busqueda de estrategias con riesgo certificado.

## Nueva Comprobacion

Se ha vuelto a ejecutar una sola vez el control anterior con el codigo local
actual, sin ajuste de parametros. Su salida completa coincide exactamente con
el resultado archivado, no solo con su beneficio o minimo.

Despues se han valorado exhaustivamente 14467 ticks del intervalo comun de
vida de ambos recorridos, mediante la misma funcion monetaria independiente.
Se usan Bid/Ask, volumen, conversion EURUSD causal y redondeo por posicion.
No hay FX invalido mientras existe exposicion en este caso; comisiones, tasas
y swap observados son cero. No se infiere eso para otros periodos o estrategias.

| En 12:39:42.008 UTC | Posiciones reales | Posiciones simuladas |
| --- | ---: | ---: |
| Abiertas | 3 | 4 |
| Flotante | -154.39 EUR | -239.60 EUR |
| Realizado | +6.89 EUR | +1.72 EUR |
| Total de cesta | -147.50 EUR | -237.88 EUR |

Por precision: -147.50 y -237.88 son realizado mas flotante de cesta,
no flotante puro ni drawdown de cuenta desde un maximo anterior. Son
reconstrucciones sobre la cinta retenida, no observaciones continuas directas.

Resultado y fuentes en
[control reproducido](../../runtime_data/2774_representation_audit_20260912/result.json)
y [recibo](../../runtime_data/2774_representation_audit_20260912/receipt.json).
Presupuesto: una evaluacion de motor, sin seleccion, menos de 300 segundos.
Calculo final 4.06 s, comprobaciones fijadas aprobadas, fuentes sin cambios.

## Que Tiempos Tenemos

No disponemos de absolutamente todos los tiempos internos del sistema.
En esta cesta se identifican 17 llamadas temporizadas: cinco aperturas y
12 modificaciones. Se conservan inicio/final del intento, entrada/retorno de
order_send, cotizacion de solicitud, respuesta e identidades. Hay decisiones,
peticiones y confirmaciones; los historicos nativos aportan fills y cierres.

El registro no cronometra individualmente cada consulta de precio/posiciones,
cada espera de despacho de hilo ni cada etapa privada de terminal, transporte
y broker. Por ejemplo, la consulta del monitor de posiciones no lleva el
mismo envoltorio temporal que order_send. Un tramo previo agregado no permite
descomponer esas consultas. Tampoco conocemos todas las cotizaciones internas
que el broker pudo usar para fijar el precio de una operacion.

Esto limita identificar cada causa interna y construir un modelo predictivo,
pero no impide reconstruir las posiciones efectivamente ejecutadas: sus
precios y horas finales si estan registrados y conciliados.

La instrumentacion tampoco entra automaticamente en el simulador. El
protocolo exacto que produjo -237.88 EUR declara:

| Elemento | Hipotesis del control |
| --- | --- |
| Retraso hasta procesamiento de entrada | 250 ms |
| Retraso posterior hasta acuse | 250 ms |
| Procesamiento/acuse de modificaciones | 250/250 ms |
| Deslizamiento adicional de entrada | 0 |
| Precio de entrada | Primera cotizacion util tras el retraso |
| Cliente compartido del control anterior | No habilitado |
| Duraciones y fills reales suministrados al motor | No |

El perfil local mas reciente anade una cola por cesta, pero su contraste
conservaba esos mismos 250/250 ms. No incorpora aun el planificador del
proceso completo ni una replica empirica de todas las llamadas.

## Donde Empieza A Diferir

La primera apertura ya difiere: real a 12:32:52.458 y 4351.13; simulada a
12:32:51.983 y 4351.50. La solicitud simulada es 189 ms anterior y el fill
475 ms anterior. El ancla de la escalera nace distinta; no solo falla una
posicion al final de la secuencia.

Luego se acumulan diferencias de observacion, entrada y disponibilidad:

| Entrada | Precio real | Precio simulado | Espera real order_send |
| --- | ---: | ---: | ---: |
| Inicial | 4351.13 | 4351.50 | 0.734 s |
| B1 | 4347.58 | 4350.05 | 31.375 s |
| B2 | 4343.45 | 4347.89 | 6.844 s |
| B3 | 4340.54 | 4347.05 | 57.125 s |
| B4 | 4340.72 | 4343.72 | 1.906 s |

Entre B1 y B2 hay una modificacion que ocupa 10.156 s, y entre B2 y B3 otra
de 3.078 s. Los registros y el diagnostico anterior del puente muestran
pausas del proceso durante llamadas largas. El mercado y las protecciones
nativas ya instaladas siguen actuando aunque Python no avance.

Ademas, el codigo live conserva el tick que decidio varias entradas mientras
espera; el precio de solicitud se refresca en el ejecutor. Intencion y precio
no son lo mismo. El cliente simulado anterior no reproducia todas esas
reglas. La nueva cola opcional cubre una parte, no cierra el modelo completo.

## La Posicion Que Explica El Cambio Grande

B3 real entra a 4340.54 y recibe TP en 4342.54. El bot confirma ese TP a
12:35:38.255 UTC. La cinta lo cruza a 12:35:49.870; el deal nativo cierra
46 ms despues, realizando +5.17 EUR. No fue un cierre ficticio ni una
correccion posterior de la contabilidad.

B3 simulada habia entrado a 4347.05, con TP 4349.05. No alcanza su objetivo
antes del minimo y sigue abierta con -59.57 EUR. La diferencia entre esa
ganancia ya realizada y esa perdida abierta es 64.74 EUR. Los precios de
entrada de B1, B2 y B4 explican otros 6.39 + 11.49 + 7.76 = 25.64 EUR.
Total: 64.74 + 25.64 = 90.38 EUR, verificado al centimo en el mismo tick.

## Por Que No Basta Copiar La Demora

Se vuelve a comprobar el ejemplo B3 con su solicitud real y sus 56857 ms
observados hasta el fill. La regla de primera cotizacion posterior daria
4341.28, no 4340.54. Su TP relativo pasaria a 4343.28; el mayor Bid hasta
el minimo es 4343.05. Ese objetivo no se cruzaria antes de la caida.

Este es un control condicionado de precio, no una simulacion completa de
politica. Aisla una segunda carencia: acertar el tiempo no identifica el
precio nativo. Ese precio nativo es conocido a posteriori; su mecanismo
interno de formacion no queda identificado por la cinta de ticks adyacentes.
La API distingue solicitud y resultado de ejecucion:
[MetaQuotes, order_send](https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py).

Copiar los precios y cierres reales serviria para una reconstruccion
observada, que es una prueba util y legitima. No serviria como prueba de
que el motor puede generarlos de forma independiente para otra estrategia.

## Que Falla En Cada Lado

**Simulacion:** el modelo de ejecucion y observacion no representa el episodio.
El calculo monetario de estas posiciones hipoteticas se reproduce de forma
independiente; la discrepancia nace antes, al generar entradas y salidas.
Los defectos monetarios abiertos en otras capacidades siguen vigentes, pero
no explican los 90.38 EUR de esta cesta con FX completo.

**Ejecucion real:** hubo esperas muy largas y falta de aislamiento de la
espera Python-MT5 respecto al resto del bot. Que aqui redujeran el riesgo por
comprar mas barato no las convierte en un funcionamiento deseable. El
[diagnostico del puente](2026-09-11-execution-divergence-root-cause.md)
incluye evidencia del terminal, inspeccion del binario y controles de bloqueo.
La documentacion de [Python 3.11](https://docs.python.org/3.11/library/asyncio-task.html#asyncio.to_thread)
no garantiza aislamiento del GIL por enviar una extension a otro hilo.
No se demuestra que el broker incumpliera sus condiciones ni se atribuye
exclusivamente a su servidor toda la espera previa al alta de la orden.

**Registro del minimo:** el resumen periodico guardo -146.19, mientras el
recorrido reconstruido llega a -147.50. Esa omision de 1.31 EUR ya esta
separada; no explica el desajuste principal. La realidad operada no se
confunde con un resumen de vigilancia que puede omitir extremos breves.

## Que Es Mas Fiable

Para describir lo sucedido: deals nativos conciliados y reconstruccion con
precios y conversion verificados, con sus limites de cobertura. La simulacion
no puede desmentir unas posiciones realmente ejecutadas por usar un escenario
distinto. Un registro real tampoco certifica por si solo ausencia de bugs.

Para predecir otra estrategia o sesion: ninguno de los dos constituye una
garantia. Esta muestra real es una realizacion concreta; el simulador aun
tiene limitaciones de ejecucion. No se admite su riesgo para seleccion masiva.

La siguiente comprobacion debe separar: (1) cliente/politica bajo las mismas
observaciones y respuestas externas, donde las decisiones deben coincidir;
(2) generacion independiente de tiempos/precios, donde procede un modelo
de incertidumbre validado. Si falla (1), hay una diferencia de implementacion,
estado, orden o cobertura de inputs que investigar; no se cubre con un margen.
Si (1) pasa y difiere (2), se aisla la aproximacion de ejecucion del broker.
La reconstruccion monetaria de hechos observados ya pasa; (1) integral aun
no se declara cerrada. Registrar mas no reemplaza conectar datos y mecanismos.

## Codigo Y Estado

Puntos comprobados: `executor.py:735` cronometra order_send;
`position_lifecycle_monitor.py:2288` consulta ticks sin ese envoltorio;
`position_lifecycle_monitor.py:1383` conserva la observacion de una tanda;
`research/dubai_iterative/engine.py:1563` resuelve la hipotesis de fill por
cotizacion posterior; `runtime_data/client_execution_20260912/compare.py:32`
fija 250/250. Protocolos y fuentes enlazados en el recibo del nuevo control.

Los cinco modulos operativos inspeccionados no difieren de `892bc33c8`,
version registrada durante el episodio. El motor local conserva su identidad
verificada anterior; no se ha modificado codigo de producto en esta tarea.
No se ha repetido toda la suite para este diagnostico: se ha reproducido el
caso fijo, su salida completa, dos recorridos monetarios exhaustivos y el
control de precio/TP. Sin commit, push, acceso a la VM, reinicio ni ordenes.
