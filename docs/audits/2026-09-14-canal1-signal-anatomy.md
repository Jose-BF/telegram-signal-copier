# Canal 1: Que Oportunidad Trae Cada Senal

## Conclusion

La hipotesis que merece prioridad es **esperar el rango publicado, tomar una
entrada principal y comparar si uno o dos anadidos pequenos aportan algo**.
No hemos demostrado rentabilidad, pero ahora hay una oportunidad concreta
que investigar, no un catalogo arbitrario de parametros.

La espera mejora a menudo el precio, aunque pierde movimientos directos.
El DCA profundo tiene una contrapartida visible: aumenta exposicion en todas
las perdedoras observadas, pero solo en parte de las ganadoras. No conviene
confundir recuperar a menudo con recuperar sin una perdida intermedia grande.

## Que Publica El Canal

Periodo de recepciones conservadas: 5 de junio a 7 de septiembre de 2026.
No es una afirmacion sobre todo enero-septiembre: enero-mayo permanece en
los JSON exportados, sin el mismo respaldo de recepciones originales.

- 258 identidades direccionales, 240 con primera recepcion respaldada.
- 232 de esas 240 tienen un primer mensaje de entrada con precio asociado:
  168 con rango y 64 con precio unico. Ocho quedan sin asociacion operativa.
- Las otras 18 identidades no tienen primera recepcion utilizable y siguen
  en el inventario. No se inventa una version inicial para recuperarlas.
- Mediana desde aviso hasta primer mensaje con precio: **48.15 segundos**;
  mitad central entre 35.13 y 63.87 segundos; percentil 90, 80.65 segundos.
- Hay casos tardios, incluido uno a 5 h 42 min con asociacion inferida; no se
  utiliza dentro del horizonte de cuatro horas ni se presenta como normal.
- La secuencia recurrente es aviso BUY/SELL, precio/rango, TP/SL y gestion.
  Se detecta una instruccion directa de mover SL a BE en 143 identidades
  dentro de cuatro horas, mediana 11.45 minutos desde el aviso. No significa
  que el broker la ejecutase ni que todos los mensajes fuesen inequivocos.
- Comentarios de mercado con zonas no se convierten en entradas. Se conservan
  28 mensajes distintos cuyo primer contenido retenido se clasifica como
  comentario con rango, separados de las ordenes NOW.

Los rangos no suelen responder explicitamente al sticker. La asociacion usa
el aviso precedente compatible y queda etiquetada como inferida. Las respuestas
explicitas y las revisiones conservan su relacion. Hay seis grupos con mas de
un mensaje de entrada distinto: el analisis principal usa solo el primero,
sin fusionar una reentrada posterior con la entrada original.

## Esperar El Rango

De 168 primeras entradas con rango, a una hora hay 158 ventanas completas,
7 parciales, 2 sin precios y 1 con TP1 incompatible. Las 10 no completas o
incompatibles no se incluyen en los porcentajes siguientes ni se ocultan.
Las siete parciales muestran TP1 primero en el orden retenido, cinco despues
de una visita; se conservan como observaciones, sin suponer que nada distinto
ocurrio en sus huecos.

| Situacion en las 158 ventanas completas | Casos |
| --- | ---: |
| Precio dentro del rango cuando se recibe | 69 |
| Precio ya fuera, por el lado favorable | 80 |
| Precio fuera, por el lado adverso | 9 |
| Visita al rango antes de TP1/SL | **131 (82.9%)** |
| TP1 antes de ofrecer visita al rango | 26 |
| SL antes de ofrecer visita al rango | 1 |

De las 80 que ya iban por delante, **54 regresan al rango antes de TP1/SL**.
Por tanto, no perseguir el precio no equivale a quedarse siempre fuera.

Despues de la visita en esos 131 casos: **83 TP1 primero, 24 SL primero y
24 sin resolver a una hora**. La proporcion 83/107 entre los resueltos no
debe presentarse como tasa de exito sobre todas las senales: deja fuera
esperas no ejecutadas, casos abiertos y falta de datos.

Frente al primer precio ejecutable del aviso, la primera cotizacion dentro
del rango mejora la entrada una mediana de **0.71 unidades de precio XAUUSD**
en esas mismas 131 parejas. La mejora media es 1.20; el percentil 10 es -0.59,
por lo que esperar tambien puede empeorar el precio. No son pips ni euros.

La mediana de anchura del rango es 5; desde su centro, TP1 esta a 7.5 y SL a
16.5 unidades. **Una alta frecuencia de TP1 no basta para demostrar ventaja**:
el beneficio, la perdida, los costes y el precio real de entrada son distintos.
Tambien hay cuatro primeros stops a mas de 100 unidades del centro. Dos de
esos casos quedan pendientes a una hora, uno llega a TP1 tras visitar y otro
antes de visitar. Se mantienen literalmente, incluso cuando luego se corrigen;
una politica de riesgo no puede reemplazarlos por el precio que parezca logico.

## Cuanto Tarda

Para comparar duraciones sin cambiar de muestra, las siguientes filas usan
exactamente las mismas 132 senales con rango y cobertura completa en todos
los horizontes. El tiempo se cuenta desde el aviso, no desde una entrada.

| Horizonte | Visita antes de TP1/SL | Luego TP1 primero | Luego SL primero | Sin resolver |
| --- | ---: | ---: | ---: | ---: |
| 5 minutos | 108 | 22 | 2 | 84 |
| 15 minutos | 109 | 50 | 5 | 54 |
| 60 minutos | 109 | 73 | 19 | 17 |
| 240 minutos | 109 | 84 | 24 | 1 |

En esta muestra, casi todas las oportunidades de visita aparecen pronto.
Lo que madura despues es el desenlace. Esto justifica separar **tiempo para
entrar** de **tiempo para gestionar**, en lugar de cerrar todo muy pronto.
No prueba que cuatro horas sea la duracion optima.

## Lo Que Implica Para DCA

En las 131 visitas con una hora completa:

| Profundidad observada antes del desenlace | TP1 primero | SL primero | Sin resolver |
| --- | ---: | ---: | ---: |
| Primera visita al rango | 83 | 24 | 24 |
| Alcanza tambien el punto medio | 56 | 24 | 24 |
| Alcanza tambien el extremo lejano | 34 | 24 | 22 |

Las 24 perdedoras atraviesan todo el rango; solo 34 de las 83 que alcanzan TP1
llegan tambien al extremo. Dar el mayor peso a la ultima entrada carga mas
las perdidas. Puede mejorar el precio medio, pero eso hay que compararlo con
el incremento de riesgo, no con el numero de operaciones recuperadas.

En otra comparacion de **las mismas 186 identidades** con cuatro horizontes
completos, 159 retroceden al menos 5 unidades desde el precio ejecutable
inicial; 127 vuelven a cubrir ese precio dentro de cuatro horas y 32 no.
La peor excursion observada hasta recuperar o terminar llega a 127.82
unidades. Estas trayectorias no tienen stop simulado: una recuperacion tardia
no implica que una cuenta hubiera podido mantenerse abierta hasta entonces.

## Precio Unico Y Contexto

Las 64 primeras entradas con precio unico no se fuerzan a un rango ficticio.
A una hora, 61 tienen ventana completa. En 36 se observa un precio igual o
mejor que el publicado antes de TP1/SL; luego 20 alcanzan TP1 primero, 8 SL
primero y 8 quedan sin resolver. El TP1 mediano esta a 5 y el SL a 15 unidades
del precio publicado. Merecen su propio control, no extrapolarles el rango.

La relacion con el movimiento previo tambien merece contraste, sin convertirla
ya en filtro. Entre los casos de rango con contexto previo completo de 15 min,
las visitas en senales a favor del movimiento previo dan 34 TP1/15 SL/9
pendientes; las contrarias, 49 TP1/9 SL/14 pendientes. Es una particion
exploratoria simple, no evidencia independiente ni un indicador optimizado.

El patron de rango aparece tanto en BUY como SELL y en todos los meses
observados, pero no con identica fuerza. A una hora, despues de visitar:

| Mes | TP1 primero | SL primero | Sin resolver |
| --- | ---: | ---: | ---: |
| Junio | 22 | 9 | 7 |
| Julio | 25 | 5 | 5 |
| Agosto | 30 | 6 | 10 |
| Septiembre, hasta el 7 | 6 | 4 | 2 |

No se usa esta tabla para exigir meses positivos ni eliminar septiembre.

## Ejemplos Completos

Primer caso cronologico de cada categoria, sin elegir el mayor beneficio.
Horas UTC; son cotizaciones observadas, no operaciones realizadas.

- **20097, BUY, 5 de junio:** aviso 13:07:37.680; rango 4410-4415 disponible
  13:08:12.038. Ask 4414.59 dentro del rango; TP1 4420 observado 13:12:05.430.
  Antes de TP1, excursion adversa 0.82; no llego al punto medio. SL 4400
  aparece mas tarde, 13:30:59.111. Conservar la secuencia importa para la salida.
- **20200, SELL, 10 de junio:** rango 4168-4170 recibido 09:43:39.299;
  Bid 4168.71. Extremo lejano observado 09:43:46.183; TP1 4165 a
  09:53:30.334. Excursion adversa antes de TP1, 6.39. Ejemplo de retroceso y regreso.
- **20093, BUY, 5 de junio:** rango 4450-4454 recibido 12:34:13.094;
  visita 12:34:33.794, extremo lejano 12:35:57.256, SL 4440 a 12:36:43.957.
  No alcanza TP1 en esa hora. Ejemplo en el que anadir habria aumentado exposicion.
- **20170, SELL, 9 de junio:** rango 4318-4322 recibido 14:30:45.075;
  TP1 4316 observado 0.515 segundos despues; visita al rango 103.948 segundos
  despues. No se cuenta esa visita tardia como oportunidad anterior al objetivo.

## Alcance Y Verificacion

Archivo vigente: `runtime_data/canal1_history_20260913/signal_anatomy_v2`.
Identidad: `ac0bb365e04b23b76756cafca8bc5e85b70b11919f554d001f83a69e70328e71`.
243205 lineas originales, 43099158 cotizaciones decodificadas y 34649072
puntos seleccionados; 12.35 segundos de calculo. Cero estrategias evaluadas,
cero ordenes, cero seleccion economica.

Se conserva v1 y su codigo en `signal_anatomy_sources_v1`: identificaba precios
unicos como avisos sin rango y bloqueaba TP1 por errores en objetivos lejanos.
v2 separa precio/rango y solo bloquea campos necesarios para describir TP1/SL.
Las advertencias originales y correcciones posteriores permanecen visibles.
No corrige por intuicion stops como 4360 frente a 4260 ni usa una revision
posterior para reescribir la primera. Las 258 trayectorias/contextos de precio
son identicos entre v1 y v2; cambian la clasificacion y las observaciones de niveles.

74 pruebas focalizadas pasan: 22 nuevas, 33 de inventario y 19 de cobertura.
Verificador nuevo confirma hashes de fuentes/resultados y recalcula resumen;
no afirma haber repetido las trayectorias de precios. La suite completa previa
de 5713 pruebas corresponde a motores compartidos que no se han modificado.

Bid/Ask y lados de activacion siguen la
[documentacion oficial de MetaTrader 5](https://www.metatrader5.com/en/terminal/help/trading/general_concept):
compra con Ask, venta con Bid; TP/SL de largos observados con Bid y de cortos
con Ask. Ver una cotizacion no equivale a una ejecucion confirmada.

Hay spread historico, pero no latencia, comision, slippage, tamanos, margen ni
ejecucion de protecciones en este informe. Los precios usan el reloj de broker
declarado ya retenido. Completo significa sin huecos mayores de 5 segundos,
no certificacion de exhaustividad del broker. La siguiente comparacion
economica usara los motores existentes y sus condiciones, sin alterar sus puertas.

Trabajo exclusivamente local, sin commit, push, VM ni cambios de produccion.
Siguiente bloque: `docs/development/2026-09-14-canal1-simple-range-study.md`.
