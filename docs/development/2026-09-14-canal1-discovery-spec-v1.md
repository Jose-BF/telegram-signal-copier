# Canal 1: Especificacion De Descubrimiento V1

Especificacion de hipotesis y limites fijada el 14/09, antes de consultar
resultados del contraste ampliado o calcular variantes nuevas. Es el siguiente
bloque autorizado del [plan](2026-09-14-canal1-causal-discovery-plan.md), no una
campana ejecutada. Antes del primer motor de busqueda, el ejecutor debe sellar
en un archivo inmutable esta especificacion, los genomas exactos, cohortes,
presupuestos, fuentes, codigo y entorno. Cualquier incompatibilidad se registra
y se resuelve con version nueva antes de ejecutar; nunca se cambia tras ver P/L.

## Fuentes Y Universo

- Solo canal 1. `receipt_inventory_v2`, identidad
  `52ea1c567b416cc256c4b3403c3e2c03f108b014a3fe8cf00b11d1fd4a58297b`.
- Cobertura `receipt_coverage_v1`, identidad
  `2e2905debf0b0bce99f852a680a6667f4023ae8fc586bb3a73254030154af2a5`.
- Periodo de recepcion 05/06 inclusive a 08/09 exclusive: 258 IDs retenidos;
  240 primeras recepciones respaldadas. Los otros 18 siguen como desconocidos.
- No filtrar entradas por exportaciones posteriores, futuras ediciones, P/L,
  extremos de precio ni coincidencia con operaciones observadas del bot.
- Tres niveles de evidencia separados. No sintetizar revision canonica o chat
  observado para el legado. La recepcion, no la publicacion del JSON final,
  inicia el reloj de la regla propia. Ninguna instruccion futura se anticipa.
- Frescura de cinco segundos solo como estrato descriptivo. Mantener tambien
  el conjunto respaldado completo, con las recepciones tardias observadas.
- Enero-mayo exportado queda como sensibilidad posterior separada. No exigir
  rentabilidad simultanea en los cuatro escenarios antiguos como filtro universal.

## Politicas Y Riesgo

Exploracion de entradas propias y gestion intradia; sin seguir instrucciones
del proveedor, repetir fills observados, ampliar posiciones sin limite ni
operar sin stop. El catalogo no pretende agotar estrategias posibles.

Volumen comun de variantes nuevas: 0.02 lotes totales, una posicion de 0.02 o
dos de 0.01. No optimizar lotaje ni usar mas apalancamiento como mejora. Las
cuatro referencias antiguas se mantienen exactamente a 0.01 lotes y se marcan
como referencias, no se comparan sumas brutas de distinto volumen como ventaja.
Conservar sus huellas y resultados de ejecucion sin sustituir parametros.

Stop fijo en precio XAUUSD. Mostrar tambien perdida configurada en USD:
100 x lotes totales x distancia del stop. No es limite garantizado ante gaps.
No convertir esa cantidad en euros sin el contrato de conversion. El ranking
considera por separado dinero hipotetico, caida y perdida configurada; ningun
capital o tolerancia del usuario se presupone.

Primera exploracion con posiciones aisladas. Toda conclusion de cuenta queda
pendiente de reconstruccion conjunta, disponibilidad de datos, concurrencia,
margen y capital. No interpretar su suma como rentabilidad de una cuenta.

## Dominio De Entradas Y Salidas

| Eje | Valores fijados |
| --- | --- |
| Mercado | Entrada al primer precio disponible segun perfil |
| Demora | 5, 15, 30, 60, 90, 120 segundos |
| Retroceso | 0.5, 1, 2, 3, 5, 8 unidades de precio |
| Impulso | 0.5, 1, 2, 3, 5, 8 unidades de precio |
| Recuperacion | Retroceso 1, 2, 3, 5, 8; confirmacion 0.5, 1, 2 |
| Caducidad de entrada | 1 o 3 minutos; mercado siempre 3 |
| Stop | 3, 5, 8, 10, 15, 20, 30 unidades de precio |
| Objetivo simple | 2, 3, 5, 8, 12, 20, 30 unidades de precio |
| Salida temporal incondicional | 5, 15, 45, 105, 225 minutos |
| Horizonte de datos | 20 min para salidas 5/15; 60 para 45; 120 para 105; 240 para 225 |

Caducidad, espera de ejecucion y mantenimiento deben caber en el horizonte,
con margen de confirmacion. No cerrar artificialmente al terminar los datos.
Conservar los bloqueos de rollover, dinero, FX y cobertura actuales.

## Catalogo Inicial Determinista

No utilizar un unico stop, solo vecinos de recuperacion ni un producto
cartesiano ilimitado. Seleccion determinista sin consultar cotizaciones:
ordenar combinaciones validas de cada estrato por SHA-256 de su representacion
canonica con etiqueta `canal1-discovery-v1`; desempate por genoma canonico.
Enumerar todas las combinaciones del dominio del estrato, no tomar primero las
de un parametro por orden de bucles. Preservar descartes de gramatica y duplicados.

1. Mercado: todas las 49 combinaciones SL/TP por cada duracion, 245 reglas.
2. Demora, retroceso, impulso y recuperacion: 144 reglas por familia/duracion,
   2880 en total, elegidas por el orden anterior. Confirmacion solo para
   recuperacion; sin gestores adicionales en este bloque base.
3. Las cuatro referencias exactas de `dubai_paired_clocks.fixed_controls`.
4. Siete familias de gestion: hasta 128 por familia, repartidas por duracion
   26/26/26/25/25. Entradas, stops y caducidad usan el mismo dominio anterior;
   mercado no varia su caducidad. Objetivos simples usan el dominio anterior
   cuando la familia los admite. Cada regla tiene un solo gestor de esta tabla.

| Familia | Dominio adicional |
| --- | --- |
| Break-even de precio | Activacion 0.5, 1, 2, 3, 5, 8; objetivo simple |
| Trailing | Distancia 0.5, 1, 2, 3, 5, 8; sin objetivo simple |
| Proteccion de beneficio | Armado 2, 4, 8, 12 EUR; devolucion 0.5, 1, 2, 4 EUR, menor que armado |
| Parcial y remanente | Mitad de 0.02 lotes; primer umbral 2, 4, 8, 12 EUR; objetivo total 4, 8, 12, 20 EUR, mayor que primer umbral |
| Dos objetivos | Dos entradas simultaneas de 0.01; pares 2/5, 3/8, 5/12, 8/20, 12/30 en precio |
| Escalon adverso | Dos entradas de 0.01; distancia 0.5, 1, 2, 3, 5; mismos pares de objetivos |
| Escalon favorable | Dos entradas de 0.01; distancia 0.5, 1, 2, 3, 5; mismos pares de objetivos |

5. Salida solo por tiempo y stop, sin objetivo ni gestor: 64 reglas, duraciones
   13/13/13/13/12, mismo dominio de entrada/SL.
6. Filtro de spread maximo: 64 reglas con limites 0.3/0.6/0.9/1.2 en precio.
   Corte horario UTC: 64 reglas con horas 10/12/15/18. Son familias distintas,
   no un filtro de tendencia ni una franja horaria completa. Mismo reparto
   de duraciones 13/13/13/13/12 y dominio de entrada/SL/TP, sin otro gestor.

Objetivo de enumeracion inicial: 4217 reglas contando las cuatro referencias,
antes de colapsar equivalencias de comportamiento. El generador debe demostrar
gramatica, unicidad y esos recuentos sin evaluar precios. Si alguna combinacion
es incompatible con el motor/perfil, declarar el motivo; no sustituirla con
otra elegida por resultado ni afirmar que toda la familia esta cubierta.

## Ejecucion Y Costes

Bid/Ask y FX causales del archivo vinculado. Base de ejecucion de los controles
previos; perfiles `reference` y `adverse_execution` con la extension opt-in
`own_rule_be_partial_v1` y `request_quote_binding=timestamp_and_ordinal`.
Antes de buscar, comprobar que activar esa extension no cambia las referencias
que no la necesitan, y verificar cada familia/horizonte en tres motores.
No desactivar barreras de capacidad para admitir combinaciones.

Coste adicional principal: 10 EUR/lote round-trip. Comision/swap historicos
no se inventan como observados. Finalistas: mostrar tambien 0 y 20 EUR/lote,
sin elegir una regla distinta para cada recargo. Referencia y ejecucion
desfavorable no son dos muestras independientes.

Dos sensibilidades adicionales para las finalistas, sin reajustarlas:
latencia de entrada 2000 ms; reconocimientos de entrada/cierre, procesamiento
de cierre y procesamiento/reconocimiento de protecciones de 1000 ms. La
segunda usa ese perfil y slippage de entrada/salida 0.3 y spread adicional
0.3, todos en precio XAUUSD. Son escenarios hipoteticos, no mediciones live.

## Cohortes Y Cronologia

Exploracion inicial sobre jornadas completas del conjunto respaldado. Elegir
el primer dia UTC con entradas y cobertura conjunta de los cuatro horizontes
en cada bloque consecutivo de 14 dias desde 05/06 hasta 08/09 exclusive.
No sustituir por beneficio. Si un bloque no tiene dia disponible, conservarlo
como ausente; no usar uno de otro bloque. Congelar IDs y fechas exactos antes
de motores. No partir jornadas para alcanzar un numero de senales.

Contraste posterior sobre todas las jornadas disponibles, con ambos informes:
interseccion de identidades/horizontes para comparaciones pareadas, y todas
las identidades del horizonte propio con bloqueos visibles. No escoger la
duracion por una suma mayor calculada sobre otras oportunidades.

Ventanas de 28 dias de desarrollo y 14 de contraste, desplazadas siete dias:
primer contraste 03/07, ultimos inicios 14/08 y 21/08. Solo ventanas completas
antes de 08/09; 04/09-07/09 queda como cola descriptiva adicional, no se fuerza
una ventana incompleta. Jornadas indivisibles y entradas anteriores al limite
asignadas por recepcion; evitar posiciones que crucen la frontera entre fases.

El catalogo, semillas y datos son retrospectivos y reutilizados. Las ventanas
describen estabilidad de reglas investigadas hoy; no prueban que hubiesemos
podido descubrirlas entonces. Vecinos creados con resultados globales no se
presentan como seleccion pasada causal ni como OOS nuevo. Registrar cada
cohorte consultada por el generador. Un futuro intacto requiere congelacion
separada antes de nuevas senales.

## Presupuestos Y Rondas

Limites globales: 10000 genomas distintos, 14400 segundos de calculo de
campana y 24000000000 visitas de motor, contando evaluaciones repetidas,
control de capacidades, refinamientos y verificaciones de finalistas. Filas
fuente decodificadas: 500 millones en toda la campana, incluidas recargas;
memoria de cache acotada, no cargar todo el ano por candidato.

| Ronda | Genomas nuevos maximos | Tiempo maximo | Visitas maximas |
| --- | ---: | ---: | ---: |
| Integracion y catalogo inicial | 4217 | 6600 s | 12 mil millones |
| Vecinos de frontera | 2000 | 2400 s | 4 mil millones |
| Interacciones entre entrada y gestores | 2000 | 1800 s | 3 mil millones |
| Robustez y verificacion | 1783 | 3600 s | 5 mil millones |

Los limites de tiempo suman cuatro horas y los de genomas 10000. No se
transfiere un presupuesto agotado ni se reinicia el contador cambiando el
nombre del archivo. Interrumpir al alcanzar cualquier limite; guardar matrices
incompletas y no asignarles puntuacion. Procesar familias/duraciones por turnos
para que el agotamiento no elimine siempre las mismas del final del catalogo.
Preservar resultados negativos y equivalencias; no contar ticks como reglas.

Vecinos: hasta 40 semillas de la frontera de desarrollo, maximo cinco por
familia. Cambiar un parametro cada vez al valor inmediato anterior/siguiente
del dominio; segundo anillo solo si hay al menos dos vecinos completos que
mantengan resultado neto positivo en ambos perfiles. Sin ampliar los extremos
del dominio. Desempates por huella, deduplicacion global antes de evaluar.

Interacciones: combinar entradas de hasta 20 semillas con gestores del catalogo
de hasta 20 semillas de familias distintas. No combinar dos gestores nuevos
entre si ni activar supuestos no admitidos. Elegir semillas en desarrollo,
no por contraste posterior. Mantener una cuota de al menos la mitad de las
interacciones fuera de la familia de entrada mas rentable en desarrollo.

Robustez: como maximo 12 finalistas de investigacion, tres por familia.
Vecinos inmediatos, escenarios de ejecucion/costes fijados, concentracion,
peor dia, peor periodo, participacion y exposicion. No recalibrar despues del
contraste. Un fallo de contrato abre incidente, no una excepcion favorable.

## Seleccion Y Parada

Primero completitud y acuerdo de motores. Ninguna fila con error monetario,
identidad incierta o discrepancia entra como cero. Mantener tasa de cobertura
y frecuencia de entrada junto a cada estadistica. Informes por mes/nivel de
evidencia/frescura; no exigir positividad a subgrupos con pocas observaciones.

Cribado de desarrollo para pasar a contraste: al menos 12 entradas ejecutadas
y seis jornadas con ejecucion, resultado neto despues del recargo positivo
en ambos perfiles. No usar requisitos anuales de seis meses en una muestra
de tres meses. Cobertura y participacion bajas impiden conclusiones fuertes.

Conservar frontera no dominada: mayor neto, menor caida de cierres diarios,
mejor peor-dia y menor perdida configurada, junto a complejidad y participacion.
Dentro de la frontera, ordenar por el menor ratio neto/max(1 EUR, caida diaria)
entre perfiles, despues menor complejidad, mayor participacion y huella.
La caida diaria no sustituye la flotante ni la exposicion simultanea.

Para una candidata de observacion: al menos 30 ejecuciones en diez jornadas
del contraste ampliado, ambos perfiles principales positivos tras costes,
resultado reciente de 28 dias no negativo, al menos cuatro ventanas con tres
ejecuciones y 50% de esas ventanas positivas. Ningun dia puede aportar mas
del 40% de la suma de dias positivos. Mostrar siempre los valores, no solo
aprobado/rechazado. Revisar vecindario y colapso de equivalencias antes de
nominar; no exigir una curva perfecta ni garantizar repeticion futura.

La nominacion de observacion no habilita produccion. Politica seleccionada
permanece nula mientras las barreras de dinero/cuenta y futuro requerido
esten abiertas. Concurrencia y adaptacion al bot necesitan su contraste propio;
no confundir aprobar los tres motores con equivalencia operativa.

Terminar con frontera y candidatas para investigacion, o rechazo motivado.
Parar por presupuesto, matrices insuficientes, incidencias o ausencia de
mejora discriminante; no encadenar rondas hasta forzar rentabilidad.

## Fuera De Esta Version

Contexto previo e indicadores adaptativos, clasificacion de regimen causal,
noticias, reentradas tras cierre, inversion de direccion, overnight, cuenta
continua, liberacion al reconocer cierres, margen y stop-out requieren contrato
o fuentes propios. Se mantienen como preguntas abiertas, no como vias
descartadas por estos resultados. Canal 2 queda fuera. Cero trading real,
publicacion, reinicios o cambios del bot en este bloque.
