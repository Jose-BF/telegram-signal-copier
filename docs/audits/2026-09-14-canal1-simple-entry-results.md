# Canal 1: Resultados De Entradas Y Candidata De Investigacion

## Conclusion

Ya hay una comparacion economica terminada y una hipotesis concreta para
prueba en papel: **R05-60**, entrada al recibir los niveles, solo cuando la
distancia hasta TP1 sea al menos la mitad de la distancia hasta SL. Una sola
posicion, SL/TP1 iniciales, sin DCA ni break-even, cierre temporal a 60 minutos.

No es una estrategia certificada ni esta lista para operar dinero real.
La muestra seleccionada es pequena: 12 operaciones, ocho ganancias y cuatro
perdidas. No se elige porque cada mes sea positivo: junio pierde.

Con ejecucion desfavorable y costes, R05-60 suma **+911.63 EUR hipoteticos**;
la referencia suma +995.30. La entrada inmediata sin ese filtro, sobre las
mismas 158 senales con datos de una hora, suma -561.99 EUR desfavorable.
El filtro se decide con la cotizacion previa a solicitar la orden, no con el
precio final de ejecucion ni con el resultado posterior.

**Limitacion decisiva:** una operacion aporta +899.48 EUR. Sin su mejor dia,
R05-60 queda en +12.15 EUR. Es una pista para investigar y observar en papel,
no evidencia suficiente de un filon estable. No se han ocultado las perdidas
ni ajustado el filtro despues de ver sus resultados.

## Que Se Ha Probado

Universo conservado: 258 identidades del 05/06 al 07/09/2026; 240 recepciones
iniciales respaldadas, 232 primeros mensajes con precio, 168 rangos y 64
precios unicos. Los datos de enero-mayo del export no sustituyen recepciones
originales. No se afirma cobertura economica de todo el ano.

Primer estudio: 24 politicas, dos perfiles, 12384 filas contando ausencias,
formatos no aplicables, bloqueos y no ejecutadas. Cuatro entradas para rangos:
al recibir niveles, dentro del rango, punto medio, principal con dos anadidos.
TP1 completo o salida dividida a TP2; 60/240 minutos. Precio unico separado.
No se ejecuto el catalogo antiguo de 4217 reglas.

Despues, hipotesis adicional fijada antes de calcularla: cuatro filtros de
recorrido/riesgo (0.25, 0.50, 0.75, 1.00), solo entrada inmediata y TP1, con
los dos horizontes. Ocho combinaciones y 4128 filas adicionales. No cambian
fills ni protecciones de operaciones aceptadas: se conserva la simulacion
base o se abstiene de abrir. No se ha implementado un filtro live.

Escala: hasta 200 USD de perdida nominal por senal aislada. No es capital
del usuario ni una perdida maxima garantizada. Lotes redondeados hacia abajo
a 0.01, maximo 1.00 por tramo, sin inventar lotes inferiores al minimo.
Spread y conversion historicos, latencias declaradas y 10 EUR adicionales
por lote ejecutado de coste completo. Dinero hipotetico, no contabilidad
observada certificada. Sin swaps inferidos, margen, stop-out ni cartera real.

## Comparacion Sobre Las Mismas Senales

126 senales con rango comunes a todas las reglas, perfiles y horizontes.
EUR netos hipoteticos con ejecucion desfavorable:

| Entrada | TP1, 60 min | TP1, 240 min | Dividida, 60 min | Dividida, 240 min |
| --- | ---: | ---: | ---: | ---: |
| Al recibir niveles | +236.20 | +463.81 | +52.16 | +442.45 |
| Dentro del rango | -516.20 | -279.70 | -586.29 | -391.32 |
| Punto medio | -991.90 | -855.20 | -1202.15 | -1141.41 |
| Principal y dos anadidos | -645.09 | -451.87 | -740.72 | -639.68 |

Los anadidos no mejoran esta comparacion. Esperar un precio mas profundo
deja pasar algunas recuperaciones y concentra entradas en movimientos que
siguen contra la senal. Ademas, el precio ya puede ser mejor que el rango
cuando llegan los niveles: exigir regresar al rango puede empeorar la entrada.
Esta explicacion es una interpretacion de los resultados, no una ley general.

En el conjunto disponible de 130 rangos a cuatro horas, la entrada inmediata
TP1 suma +476.91 EUR desfavorable (+1008.18 referencia), 125 operaciones,
99 ganadoras, ganancia media 47.19 y perdida media 161.34. Su margen es fino.
Los ocho controles de precio unico son negativos en ambos perfiles; no se
traslada la candidata de rangos a ese formato ni al canal 2.

## Cobertura Importa

El total de una hora tiene 158 rangos conocidos y el de cuatro horas 130.
Comparar sus totales directamente confunde gestion con cambio de muestra.
En las 28 senales conocidas a una hora pero sin cuatro horas completas, la
regla inmediata TP1/60 suma -797.17 EUR. Veintiuna terminan naturalmente antes
del limite o no abren y suman -543.03. El saldo positivo de cuatro horas no
se puede extrapolar ignorando esos casos. Se conservan sus bloqueos, incluidos
huecos de cotizacion y ventanas que cruzarian la noche aunque el cierre
conocido hubiese ocurrido antes. No se han relajado esas puertas de admision.

Para R05, las 28 adicionales quedan resueltas en la ventana corta como
abstenciones, no entradas o cierres naturales. Aportan -508.05 EUR desfavorable.
Sumarlas como diagnostico a +1470.79 de cuatro horas deja +962.74, todavia
con diez rangos de resultado desconocido. Esto es sensibilidad de cobertura,
no un nuevo total admitido ni prueba completa de una estrategia de cuatro horas.

## Candidata R05-60

Regla congelada para futura prueba en papel, no seleccion certificada:

1. Solo primer mensaje operativo con rango asociado a una senal del canal 1.
2. Usar el primer Bid/Ask ejecutable tras recibir esos niveles. No entrar al
   sticker suponiendo que ya se conocian el stop y los objetivos.
3. BUY: (TP1 - Ask) / (Ask - SL) >= 0.50. SELL: (Bid - TP1) / (SL - Bid) >= 0.50.
   Denominador positivo; mantener restricciones de stop, TP y caducidad.
4. Una posicion. Lote = 200 / (100 * distancia al SL), redondeado hacia abajo
   a paso 0.01, maximo 1.00. Esta es solo la escala historica de investigacion.
5. Enviar SL y TP1 absolutos con la solicitud. No corregir precios con mensajes
   posteriores ni desplazar el objetivo por deslizamiento del fill.
6. Sin anadidos ni movimiento a break-even en esta version. Cerrar tras 60
   minutos desde el fill si no se ha alcanzado TP1/SL; modelar procesamiento y acuse.

| Medida R05-60 | Referencia | Desfavorable |
| --- | ---: | ---: |
| Senales con cobertura | 158 | 158 |
| Operaciones ejecutadas | 12 | 12 |
| Beneficio neto hipotetico | +995.30 | +911.63 |
| Factor de beneficio | 2.40 | 2.30 |
| Peor operacion neta | -202.27 | -193.01 |
| Caida maxima por cierres diarios | 358.86 | 328.57 |
| Neto sin mejor dia | +47.98 | +12.15 |

Meses desfavorables: junio -168.52, julio +851.41, agosto +140.17 y septiembre
hasta el 07/09 +88.57 EUR. No se filtraron meses ni direcciones para obtenerlo.
El mayor retroceso desde un pico de beneficio dentro de una operacion es
502.02 EUR; NO equivale a perder 502.02 desde el saldo inicial. La peor equity
abierta de una senal es -191.71 EUR antes del recargo externo.
Ninguna de estas medidas es drawdown de una cuenta con senales superpuestas.

La gran ganadora es `canal1_20807`, 10/07: SELL, rango 4104-4108, SL 4118,
TP1 4100. Al llegar los niveles el Bid era 4115.20: distancia al stop 2.80,
recorrido a TP1 15.20 y relacion 5.43. Lote de referencia 0.71. Se contrastaron
texto original, recepcion y niveles; no se sustituyo una revision posterior.
El precio ya estaba fuera del rango hacia el stop, no cerca del objetivo.
Ese caso explica por que una entrada mejor situada puede valer mas que muchos
aciertos pequenos, pero tambien la fragilidad de la muestra seleccionada.
Las doce asociaciones de niveles con su sticker se infieren por precedencia
compatible; no son enlaces de respuesta observados. Se conserva esa limitacion
y el texto/recepcion originales, sin inventar revisiones canonicas.

Los umbrales 0.75 y 1.00 dejan solo cinco y tres operaciones a una hora.
No se eligen por tener una tabla aparentemente mejor: reducen demasiado la
evidencia y siguen dependiendo del mismo gran resultado.

## Verificacion Y Archivos

Estudio base completo: `runtime_data/canal1_history_20260913/simple_range_study_v3`.
Identidad: `bb1e7fd0e58234f4a0562ecf932e4c91da8764fe41d0d40502bf4fd38e95fc73`.
5382 nuevas evaluaciones rapidas, 483941020 visitas de cotizacion, 51284504
cotizaciones fuente y 328 casos con los tres motores coincidentes.

v1 completo en sus 12384 filas fallo solo al resumir seis casos sin entrada
cuya excursion abierta era nula/no aplicable. Se preservaron matriz y codigo.
v2 se detuvo al comparar tuplas en memoria con listas JSON; se anadio regresion.
v3 reconstruyo datos, cobertura, estrategias y todos los resultados rapidos;
reutilizo solo las 328 pruebas escalar/oraculo con identidad y resultados
exactos coincidentes. Las 12384 filas finales son identicas byte a byte a v1:
`bf59b0c15ad4cd6317d416fa7b160db7a524e972ff8493b5616b2fb71a812bba`.
No se han cambiado operaciones para arreglar un resumen.

Filtros: `runtime_data/canal1_history_20260913/range_entry_quality_v1`.
Identidad: `3ed6a074e32de1eb8ccb2a591ca7285570fb8ce8dc58ad7769fe6e8c26cdf585`.
Verificados hashes de fuentes/archivos y recalculadas las 4128 decisiones y
los 16 resumenes desde la matriz base. Cero llamadas nuevas al motor en el
filtro de abstencion, cero cambio de fills de operaciones conservadas.

Pruebas: `python -m pytest -q --tb=short`: 5765 pasan, 634 avisos, 346.27 s.
El motor no cambio despues. `tests/test_dubai_range_study.py` y
`tests/test_dubai_range_entry_quality.py`: 38 pasan, incluidas las regresiones
de resumen/serializacion. `git diff --check` sin errores; avisos CRLF existentes.
El verificador del estudio comprobo fuentes, matriz, resumen y paridad retenida;
no afirma volver a ejecutar los tres motores en cada invocacion de verificacion.

## Siguiente Pregunta

Proteger parte del beneficio puede ser mas util que anadir posiciones.
Siguiente bloque local: comprobar gestion sencilla de break-even, con reloj
causal, aceptacion realista de SL y el mismo riesgo. Comparar un maximo de tres
gestiones fijadas previamente, incluyendo el control sin BE; no optimizar
meses, perseguir miles de variantes ni dar por robusta una muestra de doce.
Preservar fuentes versionadas antes de ampliar el contrato de proteccion.

Todo permanece local: sin commit, push, cambios de configuracion, MT5, VM,
reinicios ni ordenes. No se ha verificado una nueva version live. Las puertas
de seleccion monetaria y OOS siguen abiertas; seleccion certificada nula.
Un backtest no garantiza ejecucion ni rentabilidad futuras, una limitacion
general de resultados hipoteticos descrita tambien por la
[NFA](https://www.nfa.futures.org/rulebooksql/rules.aspx?RuleID=9025&Section=9).
