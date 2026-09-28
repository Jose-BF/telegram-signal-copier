# Canal 1: Dubai frente a R05

Comparacion economica terminada el 14/09/2026, solo local y retrospectiva.
Periodo de los 258 avisos: 05/06/2026 12:33:34.054 UTC a
07/09/2026 15:18:22.545 UTC. Las doce entradas R05 empiezan el 11/06.
No hay resultados de enero en esta comparacion.

## Resultado principal

Mismas 153 identidades con resultado conocido para ambas politicas bajo
ejecucion adversa. Presupuesto nominal 25 EUR: en Dubai es el disparador
del stop de cesta; en R05 es el presupuesto previo a la orden, con lotes
recalculados y redondeados hacia abajo. No son perdidas maximas garantizadas.

| Politica | Senales comparables | Operaciones | Neto modelado EUR | Sin mejor dia EUR |
| --- | ---: | ---: | ---: | ---: |
| Dubai balanced actual, referencia local | 153 | 153 | -290.27 | -352.22 |
| R05, presupuesto nominal 25 EUR | 153 | 12 | +122.01 | -4.68 |

R05 gana esta comparacion, pero no es una candidata robusta acreditada:
el 10/07, una sola operacion aporta 126.69 EUR. Las otras once suman -4.68.
La operacion dominante es `canal1_20807`, SELL, con 0.10 lotes recalculados.
No se ha elegido ni publicado una politica nueva.

R05 espera el primer mensaje con rango, entra solo si la distancia cotizada
hasta TP1 es al menos la mitad de la distancia hasta SL, y usa SL/TP1 absolutos,
sin BE ni DCA y con cierre a 60 minutos. En el universo propio hay 158 casos
conocidos: 12 entradas, 136 abstenciones por calidad y 10 casos sin orden.
Las trazas de las bases abstencionadas son contrafactuales, no operaciones R05.

## Universos propios

Son conjuntos distintos; esta tabla no sustituye a la comparacion emparejada.
Neto despues de spread/FX historicos y 10 EUR por lote ejecutado de coste
adicional de ida y vuelta. Son sumas de senales aisladas, no saldo de una cuenta.

| Politica | Conocidos ref./adverso | Entradas ref./adverso | Neto referencia EUR | Neto adverso EUR |
| --- | ---: | ---: | ---: | ---: |
| Dubai balanced | 218 / 217 | 218 / 217 | +50.99 | -405.05 |
| Rango inmediato, escala nativa 200 USD | 158 / 158 | 153 / 152 | +25.53 | -561.99 |
| Rango inmediato, presupuesto 25 EUR | 158 / 158 | 147 / 146 | +0.06 | -61.90 |
| R05, escala nativa 200 USD | 158 / 158 | 12 / 12 | +995.30 | +911.63 |
| R05, presupuesto 25 EUR | 158 / 158 | 12 / 12 | +131.60 | +122.01 |

Los +911.63 EUR anteriores de R05 quedan reproducidos, pero no son comparables
directamente con un stop Dubai de 25 EUR. El cambio de escala vuelve a ejecutar
las ordenes; no multiplica el beneficio viejo por un factor.

Sobre las mismas 217 identidades de Dubai conocidas en los dos escenarios:
referencia +51.18 EUR, adverso -405.05 EUR. La diferencia es -456.23 EUR,
incluyendo cambios de decisiones y recorridos por ejecucion, no solo una
comision descontada al final. La politica es sensible a demoras y precios de fill.

| Mes 2026 | Dubai adverso, universo propio EUR | R05 25 EUR adverso, universo propio EUR |
| --- | ---: | ---: |
| Junio | -346.94 | -28.13 |
| Julio | -55.44 | +127.10 |
| Agosto | +121.33 | +13.95 |
| Septiembre, hasta el dia 7 | -124.00 | +9.09 |

Meses descriptivos, no filtros aprendidos ni conjuntos de validacion nuevos.

## Patron de las entradas adicionales

Desglose retrospectivo de las 217 cestas Dubai conocidas en escenario adverso.
El numero final de entradas solo se sabe despues: NO es un filtro operable.

| Entradas ejecutadas | Cestas | Cestas con neto positivo | Neto EUR |
| --- | ---: | ---: | ---: |
| Una | 85 | 41 | +229.21 |
| Dos | 88 | 75 | +509.16 |
| Tres | 44 | 4 | -1143.42 |

En referencia se repite la concentracion: una entrada +255.21 EUR (94 cestas),
dos +632.35 EUR (82), tres -836.57 EUR (42). Esto identifica una hipotesis,
no demuestra que suprimir el tercer fill convierta -405.05 en +738.37 EUR.
Las mismas senales que llegaron a tres pueden seguir perdiendo con dos.

En adverso, por salida: profit lock 118 cestas / +1225.61 EUR; basket stop
50 / -1407.31; time exit 49 / -223.35. Algunas ordenes pedidas por perdida
acaban positivas al ejecutarse; la razon de solicitud no impone el signo final.
Por direccion: BUY 70 / -113.11 EUR; SELL 147 / -291.94 EUR. No se justifica
un filtro de direccion a partir de esta muestra reutilizada.

### El stop de 25 EUR no es un techo

Peor caso adverso: `canal1_20380`, BUY del 17/06 a las 18:07:40.143 UTC,
neto -57.77 EUR, tres entradas. En la traza conservada, la tercera orden se
solicita a 4297.94, se ejecuta a 4296.38 y se confirma a las 18:08:34.774.
En ese tick se solicitan los cierres de cesta a cotizacion 4295.11; se ejecutan
a 4293.02 a las 18:08:35.400. Los tres motores reproducen ese recorrido.
La espera de confirmacion de entrada y el desplazamiento durante el cierre
importan. Esto es una hipotesis de ejecucion declarada, no un fill real verificado.
El peor R05 de 25 EUR es -27.37 EUR en adverso y -29.60 EUR en referencia:
un escenario adverso agregado tampoco tiene que empeorar cada operacion.

## Ausencias y alcance

- Dubai: 35 casos bloqueados por recibo/datos, incluidos 18 avisos sin recibo
  inicial admisible y 17 con problemas de precios/cobertura. Ademas hay 5 cestas
  sin cierre en referencia y 6 en adverso. No se les asigna cero.
- Dubai tiene 186 ventanas admitidas de cuatro horas, todas con cierre natural
  en ambos escenarios. La alternativa de 3910 segundos anade 32 cierres en
  referencia y 31 en adverso; quedan los casos pendientes mencionados.
- Con solo las ventanas de cuatro horas, Dubai da +135.73 EUR en referencia
  y -217.22 EUR en adverso. El signo adverso no depende del complemento corto.
- Rangos: 158 casos conocidos, 36 bloqueados y 64 fuera de esta familia de
  primera senal con rango. No borrar esos 100 casos del inventario de 258.
- Los recibos de inicio y de primer rango son relojes diferentes. Se conserva
  el nivel de evidencia y la asociacion inferida cuando no hay respuesta explicita.
- Solo una de las 240 senales con recibo inicial valido tiene cierres directos
  asociados dentro de cuatro horas (dos mensajes). Los anuncios de TP, BE,
  mensajes condicionales u opcionales no se convierten en cierres directos.
- La referencia es el contrato local `dubai_balanced_v1`, no toda la gestion
  discrecional del trader ni una reconstruccion del interprete real del bot.
  La identidad remota del 07/09 es evidencia de esa fecha, no verificacion de VM hoy.
- Reloj de broker, fills, FX y costes siguen siendo supuestos del estudio.
  No hay certificacion monetaria/live, cartera simultanea, margen, swaps
  nocturnos, capital supuesto ni datos OOS nuevos.

## Evidencia y cambios locales

Archivo: `runtime_data/canal1_history_20260913/current_strategy_study_v1`.
Identidad: `0bc8919818174fe8cce320d39cbaa13a3ff0b7e9f2dfb682cd99265b15347581`.
2580 filas; 3186 evaluaciones; 1062 comparaciones scalar/fast/oracle sin
discrepancias; 316 controles nativos identicos al no-BE anterior salvo el
digest de implementacion. 51284504 cotizaciones fuente decodificadas,
248013384 visitas presupuestadas, 2203.69 segundos. No se repitio la matriz
de 24 reglas ni se optimizaron parametros con esta comparacion.

La extension opt-in `basket_guard_v1` incorpora el stop monetario con solicitud,
procesamiento y acuse de cierre en los tres motores. Mantiene los rechazos de
los perfiles anteriores; requiere mercado hipotetico, sin cliente live ni fills
MT5 observados. No se cambiaron los parametros del bot ni sus rutas de ordenes.
125 fuentes anteriores se conservaron en `sources_before_basket_guard_v1`;
los estudios viejos no se reescribieron para aparentar validacion nueva.

Verificaciones ejecutadas:

- `python -m pytest -q --tb=short`: 5857 passed, 634 warnings, 1045.11 s.
  La bateria se recogio antes de los nueve tests del nuevo driver y dos
  bordes adicionales, que se comprobaron por separado.
- Pruebas enfocadas de BE absoluto, mercado y cesta: 137 passed, 35.30 s.
- Driver de comparacion, contrato actual y cesta: 37 passed, 4.46 s.
- Cesta final (incluye conversion desconocida y cesta abierta) y driver:
  32 passed, 4.24 s. Los fallos intermedios de fixtures quedaron corregidos;
  no se ampliaron tolerancias ni cambiaron resultados esperados para aprobar.
- Verificador del archivo: fuentes, artefactos, matriz, resumen, overlays R05
  y paridad registrada verificados; no ejecuta otra vez los motores.
- Comprobacion aritmetica adicional sobre los 1051 resultados base completos:
  suma de P/L de cierres, volumen abierto/cerrado y coste extra reconciliados.
- Revision directa del diff frente a las fuentes preservadas y comprobacion
  de whitespace; no revision independiente por otro agente en este bloque.

No commit, push, despliegue, reinicio, cambio de configuracion ni ordenes reales.
La automatizacion de 30 minutos permanece pausada; no se ha creado otra.

## Siguiente contraste concreto

Prioridad: aislar el efecto de los anadidos de Dubai, conservando todas las
senales, tambien las 44 que acabaron con tres entradas. No seleccionar a
posteriori las cestas que terminaron con una o dos.

1. Control original 0.01/0.04/0.04, ya medido.
2. Dos entradas 0.01/0.04, sin tercera, manteniendo paso, ventana, stop de
   cesta, profit lock y salida temporal originales.
3. Una entrada 0.01, sin anadidos, para separar el valor del primer anadido.

Congelar el experimento antes de calcularlo: dos escenarios, mismas ventanas
de datos por identidad, costes originales, tres motores y cestas aun abiertas
visibles. Hasta 300 senales, 5000 evaluaciones nuevas y cuatro horas. Separar
universos propios de intersecciones conocidas. Este contraste esta pendiente,
no ejecutado ni convertido en una candidata de produccion.

Otra rama no cubierta por las 24 reglas anteriores es destinar toda una
entrada a TP2/TP3/TP4, frente a TP1 o la division 75/25 ya probada. Queda
secundaria al contraste de los anadidos; no lanzar otro barrido por inercia.
