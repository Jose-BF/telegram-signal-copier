# Primera Comparacion Acotada De Reglas Propias

Peticion: preparar una primera busqueda pequena por canal y comprobar las
posibles candidatas antes de plantear ampliar el estudio. Trabajo local del
12/09/2026. No autoriza operativa, despliegue ni busqueda masiva.

Estado: primera comparacion y comprobaciones terminadas. No se propone aprobar
ninguna de estas reglas. `selected_policy=null`, `promotion_eligible=false`.

## Reparaciones Y Controles

- Dinero desconocido durante una posicion abierta ya no se presenta como
  maxima perdida completa. Las reglas dependientes de dinero se bloquean si
  la conversion necesaria es desconocida, incluso antes de armar profit-lock.
  Una salida basada solo en precio/tiempo conserva el dinero cerrado conocido,
  pero no convierte el riesgo parcial en completo. Tres motores y agregacion.
- Cierre temporal incondicional opt-in en schema2, sin modificar los valores
  por defecto. Es una solicitud con espera y posible cierre previo por SL/TP,
  no un cierre inventado al terminar los datos.
- 96 regresiones monetarias nuevas y 45 temporales. Suite final: 5263 pruebas,
  cero fallos, errores u omisiones; fuentes estables. XML SHA-256:
  `9a79c24a24aa953dfbba9d62a9ed8e6f795550b801541790cf39a6c3d9fdb1cd`.
- Cohorte anterior reejecutada: 19 senales, 57 evaluaciones, tres motores
  concordantes, mismas entradas y salidas. 2801 conserva sus once ticks FX
  desconocidos, ahora expresamente bloqueados; las otras 18 no cambian.
  Esto no resuelve ni certifica la ejecucion independiente del caso 2774.

## Protocolo Congelado

Una entrada de 0.01 lotes por disparador del canal, a mercado; sin refuerzos,
gestion del proveedor, trailing ni reglas monetarias de recuperacion. Entrada
expira a los 3 minutos. SL 5/10/15 x TP 5/10 x cierre 15/60 minutos: 12 reglas
por canal, 24 en total. SL/TP se expresan en movimiento de precio XAUUSD, no
en euros ni en pips del proveedor. Mismo volumen para todas las reglas.

Referencia monetaria EUR con contrato 100; spread Bid/Ask y conversion causal.
Comision cero es la hipotesis historica declarada, no una certificacion de la
cuenta. Se informa ademas el efecto neto de 10 EUR por lote y vuelta completa:
0.10 EUR por cada entrada de 0.01 lotes. Ese coste extra no se integra en el
drawdown base ni se trata como tarifa garantizada. Sin capital, margen,
stop-out ni limite de perdida elegido por el usuario.

Desarrollo: diez dias, 27/07-07/08. Contraste cronologico: tres dias,
10/08-12/08. Fechas ya utilizadas en investigacion anterior, NO validacion
intacta ni OOS nuevo. Se fijan antes de evaluar candidatos. Ninguna ampliacion
de parametros despues de ver resultados.

Fuente: recibos raw originales de los chats -1001642806869 y -1003908582492,
con identidad de mensaje/revision y primera disponibilidad comprobadas. Dubai
usa los stickers direccionales declarados; Gold usa NOW del chat actual.
El chat Gold antiguo y las hipotesis de exportaciones editadas no se mezclan.
Un disparador por mensaje distinto, sin introducir el filtro operativo de
duplicados de cinco segundos. No se afirma capturar todas las publicaciones
que el colector no recibio.

Cuatro mundos de ejecucion fijados: primario 250 ms; 1 s; 10 s mas 0.10 de
deslizamiento de entrada/salida; 60 s mas 0.20. Los tres ultimos incorporan
1 s de latencia de observacion. Acuses/modificaciones/cierres estan separados
en el protocolo. Son hipotesis de sensibilidad, no probabilidades calibradas
ni una cota que siempre empeore el resultado. La contencion entre cestas y
el bloqueo global del puente siguen sin validacion independiente.

## Admision Sin Eliminar Fallos

| Canal y horizonte | Desarrollo | Contraste | Total |
| --- | --- | --- | --- |
| Dubai, 15 min | 30/30 | 9/9 | 39/39 |
| Dubai, 60 min | 30/30 | 8/9 | 38/39 |
| Gold, 15 min | 67/67 | 26/27 | 93/94 |
| Gold, 60 min | 64/67 | 25/27 | 89/94 |

Los cocientes son recorridos cargados/disparadores elegibles. Reglas de 15 min
requieren 20 min de datos, suficientes para su entrada/cierre en los mundos
declarados; las de 60 min conservan 70. Addendum previo a resultados, sin
cambiar reglas, senales ni tolerancias. Maximo hueco de mercado 5 s; FX con
el contrato historico previo de edad 5 s o intervalo acotado 60 s. No se
relajan los incidentes estrictos FX del 09-11/09.

Se conservan seis incidencias de horizonte largo: Gold 632 (extension hasta
el cierre diario/rollover), 968, 998, 1362, 1416 y Dubai 21368 (huecos entre
ticks superiores a 5 s). En horizonte corto solo sigue afectada Gold 1362.
Un resultado sobre la parte cargada no es el resultado de toda la cohorte.

## Desarrollo

1146 evaluaciones de senal/regla en el motor rapido; cinco pruebas del
auxiliar comprueban denominadores, veto a candidatos incompletos, criterios
de preseleccion y recuperacion. Cada variante y resultado retenidos.

| Canal | Resultado de la familia |
| --- | --- |
| Dubai | 11/12 reglas negativas; una apenas positiva |
| Gold | Las seis reglas con desarrollo completo son negativas; las otras seis conservan tres senales bloqueadas cada una y no son elegibles |

Unica prefinalista diagnostica Dubai: `f9010ed7588f`, SL10/TP5/15min.
Neto hipotetico +3.77 EUR; +0.77 tras el coste adicional; drawdown de cuenta
31.36 EUR, reconstruido con posiciones abiertas y simultaneidad. Maximo dos
senales/0.02 lotes simultaneos. Peor marcado individual -8.83 EUR; no es el
drawdown conjunto. Peor dia -10.58 EUR; 44.59% del beneficio de los dias
positivos se concentra en el mejor dia. Recuperacion diaria realizada maxima
completada: tres jornadas; al final seguia dos jornadas por debajo del pico.
La recuperacion diaria no mide tiempo bajo agua de la curva tick a tick.

Las seis reglas completas Gold quedan entre -65.39 y -75.29 EUR hipoteticos,
antes del coste adicional. No hay candidata Gold para combinar con Dubai.
No se escoge la regla menos negativa como si fuera una ganadora.

## Contraste Y Decision

Se ha evaluado la prefinalista fijada y la referencia SL10/TP5/60min de cada
canal en los cuatro mundos: 664 casos por cada uno de los tres motores y otros
664 con el cliente serial por cesta, 2656 evaluaciones. Cero diferencias en
el resultado completo entre los tres motores, salvo el digest especifico del
backend. Cero diferencias economicas con el cliente serial, incluidas fechas,
precios y volumen de entrada, salidas, dinero y extremos. Ese cuarto control
no certifica concurrencia de la cuenta ni sustituye el modelo primario.

Dubai SL10/TP5/15min, EUR hipoteticos con 0.01 lotes por senal:

| Mundo | Desarrollo neto | Tras coste extra | Drawdown de cuenta | Contraste neto | Tras coste extra | Drawdown de cuenta |
| --- | --- | --- | --- | --- | --- | --- |
| Primario | +3.77 | +0.77 | 31.36 | +13.01 | +12.11 | 7.37 |
| 1 segundo | +4.27 | +1.27 | 31.94 | +3.87 | +2.97 | 13.44 |
| 10 segundos | -1.91 | -4.91 | 36.67 | +11.19 | +10.29 | 6.65 |
| 60 segundos | -15.37 | -18.37 | 38.01 | +6.05 | +5.15 | 12.48 |

Desarrollo: 30 senales en diez dias; contraste: nueve senales en tres dias,
todos los recorridos completos para esta regla. Los drawdowns no incluyen el
coste extra; no son limites de perdida futura. No se elige el mejor mundo.
La mejora en los tres dias siguientes no oculta el margen de desarrollo minimo
ni las perdidas de desarrollo con otras esperas. Ademas, esos dias ya estaban
usados: no permiten afirmar validacion fuera de muestra intacta.

La referencia larga Dubai es negativa en desarrollo en los cuatro mundos.
La referencia larga Gold es negativa sobre la parte cargada en ambos periodos
y cuatro mundos; mantiene 3/67 senales bloqueadas en desarrollo y 2/27 en
contraste, por lo que no se publica como resultado completo. Sin prefinalista
Gold no hay pareja candidata cuya suma pueda presentarse como cartera ganadora.

Decision de investigacion: no proponer ninguna para aprobar ahora. Se cierra
esta familia sin nuevas mutaciones. Un estudio pequeno negativo no demuestra
que todas las estrategias posibles carezcan de ventaja; tampoco autoriza a
ampliar la busqueda hasta obtener un positivo. Corresponde revisar juntos la
siguiente hipotesis y el presupuesto antes de otra familia o busqueda masiva.

Verificacion final: 24 variantes, 1164 pares elegibles senal/regla en desarrollo,
1146 calculados y 18 bloqueados retenidos; 3802 evaluaciones en total contando
el contraste de los cuatro motores/perfiles. Se han reproducido exactamente
124 resultados primarios de desarrollo. Todos los archivos retenidos y sus
fuentes siguen iguales; la identidad actual de codigo/pruebas coincide con la
suite completa de 5263. Recibo: `final_verification_v1.json`.

En ningun caso esta preseleccion desbloquea dinero certificado, capital/margen,
validacion intacta o promocion. No hay busqueda masiva autorizada. La siguiente
familia, si se propone, requiere revision conjunta y protocolo nuevo.

Pendiente, separado de esta primera comparacion ya terminada: fijar capital y
perdida conjunta tolerable, verificar el contrato monetario historico aplicable,
cerrar la representacion/validacion de contencion global cuando el alcance la
requiera y disponer de un periodo realmente intacto. No hay prueba demo ni
captura futura programadas como parte de este bloque.

## Evidencia Retenida

Base local: `runtime_data/small_search_readiness_20260912/`.

- `timed_exit_full_v1/receipt.json`: suite final y fuentes verificadas.
- `repaired_controls_v1/receipt.json`: cohorte original sin sustituir archivos.
- `inputs_v2/comparison_protocol.json`: reglas y presupuesto previos a resultados.
- `gap_audit_v1.json`, `short_inputs_v1/addendum.json`: limites de datos por regla.
- `development_v1/receipt.json`: 24 variantes y preseleccion congelada.
- `development_v1/canal*/development.json`, `results.jsonl.gz`: todos los intentos.
- `validation_v1/receipt.json`: 2656 evaluaciones, coincidencias y fuentes.
- `final_verification_v1.json`: reproduccion exacta, presupuesto y denominadores.

Recibos SHA-256:

- Desarrollo: `e91b513d99ed623de5066bb51d130d18680eb33b5596aeab2e8923d3e339b1af`.
- Contraste: `963670f0d12e749773af9ec8f9b90447cbd28d96616559d7e6935edeb5a74b85`.

`inputs_v1` es una preparacion fallida por normalizacion de chat; no contiene
evaluaciones de estrategias y se conserva. `inputs_v2` corrige la identidad
de control manteniendo y verificando los identificadores raw originales.

Cambios locales sin commit ni push en este bloque. Bot y VM no consultados ni
modificados; ninguna orden, despliegue, reinicio o automatizacion nuevos.
