# Tiempos, Precios Y Riesgo De Error

## Resultado Principal

Si podemos medir buena parte de lo que faltaba con las capturas existentes.
Eso no permite atribuir cada milisegundo a red, terminal, broker o bloqueo
del proceso, ni conocer exactamente el fill de una orden alternativa.

La hipotesis de que el simulador es siempre mas pesimista queda refutada en
la muestra completa comparable: seis cestas subestiman riesgo, cuatro lo
sobrestiman y dos coinciden. Algunas diferencias son centimos; la mayor
subestimacion del escenario primario es 23.23 EUR, no un limite futuro.

Se ha completado el diagnostico local, no una reparacion nueva del motor.
Fases 2 y 3 siguen abiertas. No se publican cambios, no se accede a la VM,
no se generan ordenes ni se abre la busqueda masiva.

## Alcance Y Evidencia

Protocolo fijado antes del calculo:
[plan del estudio](../development/2026-09-12-execution-uncertainty-study.md).
Se conserva sin editar como entrada congelada; el progreso final se recoge
aqui. Datos del 09/09, 10/09 y primeras dos senales del 11/09, retrospectivos.

- 14 fuentes, 845406 filas verificadas por hash y recuento, sin duplicados
  de event_id. Extraccion de 832524 eventos relevantes, principalmente
  inicios/finales de decisiones. No representan otras tantas operaciones.
- 533 intentos registrados entre Dubai y Gold; 530 enviaron una llamada:
  58 aperturas, 461 modificaciones y 11 cierres. Tres cierres no enviados
  permanecen separados. Los envios incluyen 494 respuestas 10009,
  29 respuestas 10036, seis 10029 y una 10016; no se descartan rechazos.
- Cuatro versiones historicas, conservadas en las tablas: f3ab236, ed9ede1,
  892bc33 y d9037c5. Los resumenes agregados siguientes son descriptivos,
  no una distribucion unica certificada para cualquier version.
- Precios: las mismas 40 entradas observadas Gold, enlazadas por order/deal.
- Riesgo: mismas 19 senales y cuatro escenarios ya fijados, 76 comparaciones.
  Cinco senales sin exposicion y dos con cobertura FX parcial se conservan.
  Quedan 12 parejas con exposicion y cobertura completa de ticks retenidos.

Artefactos bajo `runtime_data/execution_uncertainty_20260912/`:
`protocol.json`, `source_verification.json`, `calls.json`, `call_summary.json`;
resultados finales en `completion_v2/prices.json`, `interference.json`,
`risk_summary.json` y `analysis_receipt.json`. El recibo final enlaza pruebas,
fuentes, resultados y este informe.

## Lo Que Duran Las Llamadas

Duracion desde entrada en order_send hasta retorno al codigo Python,
medida por el reloj monotonico registrado. No equivale solo a tiempo del
broker. Percentiles empiricos, sin garantia de frecuencia futura:

| Operacion | Envios | Mediana | P95 muestral | Maximo |
| --- | ---: | ---: | ---: | ---: |
| Apertura | 58 | 0.711 s | 12.166 s | 57.125 s |
| Modificar SL/TP | 461 | 0.187 s | 1.312 s | 10.156 s |
| Cierre | 11 | 0.406 s | 0.813 s | 0.953 s |

24 aperturas superan un segundo, siete superan cinco y cuatro superan diez.
En modificaciones son 36, dos y una, respectivamente. Poner 250 ms a todo
no representa esta muestra, pero poner el maximo a todo tampoco es una
garantia conservadora: retrasar una entrada puede mejorar su precio.

Los componentes previo/llamada/posterior suman exactamente el total en los
530 envios. El tramo previo maximo registrado es 187 ms; muchos previos y
posteriores figuran como cero. El reloj historico presenta cuantizacion
compatible con pasos de unos 15-16 ms y diferencias de hasta 16 ms frente a
los intervalos UTC. Cero registrado no demuestra duracion fisica cero.
No se transforma la unidad nanosegundos en una promesa de precision.

Ejemplo de la llamada larga de 2774, order 1980744545:

| Hito | Intervalo observado |
| --- | ---: |
| Entrada Python -> alta de orden nativa | 46.497 s |
| Alta nativa -> deal nativo | 10.360 s |
| Deal -> retorno Python | 0.265 s |
| Total por reloj UTC | 57.122 s |

El total monotonico es 57.125 s. El terminal retenido ya corroboraba estas
llamadas largas en el diagnostico anterior. Estas etiquetas son hitos,
no una identificacion exclusiva de la causa: el primer tramo puede contener
esperas de puente, terminal, transporte o procesamiento previo al alta.
Una diferencia cruzada de -7 ms en otra entrada se conserva como limite
de alineacion de relojes, no como una ejecucion anterior a su causa.

## Interferencia Y Peticiones Pendientes

No se observan dos order_send solapados dentro de una misma sesion. Esto
no demuestra ausencia de interferencia: una tarea puede esperar mientras
otra ocupa el proceso o la via de acceso a MT5. Las consultas de solo lectura
no tienen aqui el mismo detalle por llamada, y tampoco se ve el interior
del puente o del servidor.

Se emparejan 415386 inicios/finales de decision por sesion e identidad, sin
faltantes. Las 25 decisiones de mas de un segundo coinciden temporalmente
con llamadas de orden; varias son la propia decision que espera su apertura,
no 25 pruebas independientes de bloqueo de otra tarea.

Hay 512 enlaces solicitud-intento; 47 intervalos intersectan otras llamadas.
Ejemplo: una peticion de 2693 espera 3.522 s antes del intento, y 3.502 s de
ese intervalo coinciden con otra llamada registrada. Es evidencia de espera
concurrente, no una separacion completa entre cola, elegibilidad y reintento.
Los 21 intentos sin solicitud enlazada se mantienen, no reciben espera cero.

Se registran 168 eventos de fusion de acciones pendientes (65/96/7 por dia).
Precision respecto al informe anterior: SL y TP pueden ser intenciones
distintas, pero fusionarse en una sola llamada MODIFY_SLTP. El modelo debe
representar revisiones, sustituciones y fusiones; no imponer dos envios
por cada par SL/TP. El codigo de cola tambien contempla esperar a que el
mercado permita el stop; esa espera no se etiqueta como order_send lento.

## Precio Ejecutado

Las 40 entradas quedan conciliadas: precio y volumen del deal, precio de
la orden y respuesta Python, identidad de posicion y hora de ejecucion.
Las 40 cotizaciones utilizadas para solicitar la orden aparecen exactamente
en la cinta historica retenida. Esto valida hechos observados, no fills
hipoteticos ni toda la ejecucion de salidas de cualquier estrategia.

Para compra se compara Ask; para venta Bid. El deslizamiento con signo
adverso se descompone sin duplicarlo:

`fill - solicitud = movimiento hasta el tick previo + residuo de ejecucion`

En venta se invierte el signo. Las igualdades se verifican con Decimal.
El residuo tambien recoge diferencias de muestreo/reloj, no es solo un
recargo del broker. La primera cotizacion posterior se conserva unicamente
como diagnostico no causal; no alimenta el precio simulado.

Mediana del deslizamiento adverso: -0.02 unidades de precio XAUUSD; rango
-4.77 a +1.13. Frente al ultimo tick previo, la mediana residual es cero,
pero el rango va de -0.71 a +1.11. Solo 18 de 40 fills igualan ese tick.
Su antiguedad mediana es 32.5 ms, maxima 232 ms. No hay empates en el
milisegundo de estos 40 fills; el analizador los marca si aparecen.
Estas unidades de cotizacion no son EUR de cuenta ni pips del proveedor.

Ejemplo: en la tercera entrada de 2774 se solicitan 4348.22 y se ejecutan
4343.45. El movimiento hasta el Ask previo es -4.89 y el residuo +0.12:
total -4.77. Fue una espera favorable a esa compra. No se puede convertir
ese beneficio accidental en un supuesto favorable para todas las entradas.

La distincion solicitud/resultado coincide con el contrato de
[order_send de MetaQuotes](https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py).
El significado de precio solicitado depende del modo de ejecucion del simbolo;
no se presupone que todos los modos lo traten como precio garantizado.

## Es Conservadora La Simulacion

Se calcula perdida maxima desde cero de cada cesta, no drawdown de cuenta
desde un maximo de equity. Se reutiliza el marcado monetario independiente
anterior, no el minimo resumido cada cinco segundos por el bot.

`L = max(0, -minimo_cesta)`; `E = L_observado - L_simulado`.
E positivo es el error peligroso: la simulacion subestima la perdida.

| Escenario fijado | Subestima | Sobrestima | Igual | Maxima subestimacion |
| --- | ---: | ---: | ---: | ---: |
| Anterior, primario | 6 | 4 | 2 | 23.23 EUR |
| Cola y tanda fijada | 6 | 4 | 2 | 22.88 EUR |
| Cola y decision renovada | 6 | 4 | 2 | 24.44 EUR |
| Cola y precio de solicitud | 6 | 5 | 1 | 21.93 EUR |

Cada fila mantiene ademas cinco senales sin exposicion y dos parejas
parciales, 2668 y 2801, cuyo signo completo es desconocido. No se elige un
escenario ganador. La mediana de E es +0.02 EUR en los cuatro: puede ser
practicamente cero y coexistir con una discrepancia material.

Dos contraejemplos de signo contrario, escenario primario:

| Cesta | Minimo observado reconstruido | Minimo simulado | Lectura |
| --- | ---: | ---: | --- |
| 2774 | -147.50 EUR | -237.88 EUR | Simulacion peor en 90.38 EUR |
| 2777 | -121.57 EUR | -98.34 EUR | Simulacion mejor en 23.23 EUR |

Entre las 12 parejas completas, una simulada cruza -200 EUR y ninguna real
lo hace. Es compatible con el ejemplo del usuario en esta muestra, pero no
demuestra dominancia estadistica ni que toda perdida real de 200 EUR seria
todavia peor en simulacion: no hay un caso real completo asi para comprobarlo.
Dos cestas parciales tampoco se clasifican como seguras frente al umbral.

## Logica Propuesta

1. Reparar primero los defectos deterministas pendientes: dinero, conversion,
   estados y semantica de acciones. Un margen estadistico no los soluciona.
2. Representar una via compartida de ejecucion, esperas de consulta/envio,
   estado de cada tarea y reglas de fusion/revision. Mantener separados el
   instante de ejecucion del broker y el acuse que conoce el cliente.
3. Usar escenarios conjuntos de tiempos y residuo de precio, condicionados
   por operacion y condiciones conocidas: version, spread, volatilidad,
   actividad y antiguedad del tick. No sortear independientemente cada
   componente ni sumar dos veces el movimiento ya causado por la latencia.
   Con esta muestra solo hay descripciones y sensibilidades, no un ajuste
   multivariable fiable ni una frecuencia validada para las colas.
4. Recalcular toda la estrategia en cada escenario. Cambiar un fill puede
   activar otra entrada o salida; no basta restar euros al resultado final.
5. Para un modelo y dominio fijados, calibrar despues un margen unilateral
   del error peligroso: `L_guardado = L_simulado + max(0, q_adverso(E))`.
   Si el modelo usa escenarios, E se calcula frente a esa salida de escenarios
   ya fijada, no mezclando errores del modelo anterior. Una mediana favorable
   nunca reduce el riesgo admitido. Hoy no se asigna un margen numerico.
6. Separar desarrollo, calibracion y validacion posterior; conservar juntas
   las cestas de un dia y las dependencias entre dias. Los tres bloques
   actuales no se convierten en dias independientes por remuestrearlos.
   Estimar incumplimientos y su incertidumbre por bloque y condicion, no
   presentar un percentil empirico como garantia probabilistica.
7. Admitir solo las capacidades y condiciones contrastadas. Una estrategia
   con distinta frecuencia, volumen o carga puede cambiar el error. La
   correccion obtenida con 555 no se transfiere automaticamente a miles de
   politicas. Si la candidata depende de un caso no cubierto, queda bloqueada
   o limitada expresamente, conservandola en el denominador.
8. Antes de buscar, fijar presupuesto, criterios de beneficio/riesgo/costes
   y prueba reservada, y revisar el alcance con el usuario. Una candidata
   cuyo atractivo desaparece bajo escenarios plausibles no queda validada
   por tener buena media. Riesgo conjunto de cuenta se valida por separado.

El margen propuesto es una posible capa final sobre un modelo correcto,
no la alternativa inmediata a corregirlo. Una cota prudente para perdida
tampoco prueba rentabilidad ni corrige un beneficio sobreestimado.

Como control matematico se calcula el rango `ceil((n+1)*0.95)` de un margen
unilateral por estadistico de orden: con 12 parejas exige la posicion 13,
y con tres bloques la cuarta. No hay cota finita disponible por ese metodo
en esta muestra; ademas no se han satisfecho sus hipotesis de intercambio.
No se confunde cobertura marginal, confianza de una cota y garantia para
cada condicion. Fundamentacion:
[Angelopoulos y Bates](https://arxiv.org/html/2107.07511v6),
[NIST, limites de tolerancia](https://www.itl.nist.gov/div898/software/dataplot/refman1/auxillar/tolelimi.htm).
El 95% es una ilustracion metodologica, no un nivel de riesgo autorizado.

## Siguiente Paso Y Cierre

Prioridad: cerrar los defectos monetarios identificados y ampliar el modelo
del cliente con la semantica y los tiempos ahora medidos. La aceptacion exige
regresiones independientes y despues contraste fuera de los casos usados
para reparar; no igualar solo 2774 ni escoger la latencia que mejor funcione.

Si al implementar falta distinguir una espera de consulta, despacho o puente,
la instrumentacion adicional minima mediria programacion/inicio/final por
API, tarea e intento, reloj de alta resolucion y llegada del acuse. Se puede
preparar offline; instalarla o reiniciar el bot requiere autorizacion separada.
No se exige operar en real cada estrategia alternativa ni descartar el
historico previo que tenga los datos causales necesarios para su uso.

El primer analisis se detuvo al comparar capturas que anadian campos UTC
derivados y otras que no los incluian. La continuacion verifica cada anotacion
contra el reloj nativo y elimina solo esa diferencia de formato; todos los
campos nativos duplicados deben coincidir exactamente. Se conservan script,
protocolo y salida parcial originales. No se ajustaron precios ni tolerancias.

Verificacion local: pruebas focales y comprobacion independiente de identidades,
duraciones, precios, signo del error y fuentes en el recibo final. La evidencia
previa de 5122 pruebas del motor se reutiliza porque su identidad no cambio.
No hay commit, push, nueva verificacion live ni modelo probabilistico admitido.

- [x] Tiempos y precios extraidos y conciliados dentro del alcance.
- [x] Hipotesis conservadora contrastada sin eliminar discrepancias.
- [x] Logica estadistica, limitaciones y siguiente paso definidos.
- [ ] Reparacion integral del motor y validacion posterior.
- [ ] Admision para buscar estrategias, pendiente de revision conjunta.
