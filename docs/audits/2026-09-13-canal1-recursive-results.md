# Canal 1: Primera Campana Adaptativa

## Resultado Y Decision

Campana local cerrada: **2500 candidatas unicas**, cuatro familias y una
ampliacion de 500 candidatas, decidida exclusivamente con desarrollo.
No se alcanzo el techo de 10000 ni el de cuatro horas. Se detuvo por ausencia
de mejora material adicional segun las reglas congeladas.

Las dos finalistas de investigacion no quedan aprobadas para real. La entrada
con demora pierde en el escenario mas lento de desarrollo y en los cuatro
escenarios del contraste posterior disponible. La entrada tras recuperacion
es mas interesante, pero tambien pierde en el escenario mas lento del contraste
posterior. Este contraste es parcial, 50 de 55 senales, y retrospectivo.

Esto no demuestra que ninguna de las 2500 variantes pueda funcionar, ni que
se haya encontrado el optimo global. Las comprobaciones posteriores se hicieron
sobre las dos finalistas congeladas y un control fijo, no sobre toda la grilla.
No se han retocado las candidatas despues de ver el contraste.

**Siguiente bloque propuesto para revision conjunta:** una busqueda cuyo criterio
de desarrollo incluya los varios escenarios de ejecucion, no solo el resultado
del escenario primario. Antes de ejecutarla, fijar un nuevo protocolo, completar
las metricas de riesgo pendientes y reservar evidencia nueva que no haya
intervenido en esta decision. Las fechas de este informe ya estan reutilizadas.
La recuperacion queda como hipotesis de partida, no como estrategia elegida.

## Que Se Ejecuto

Autorizacion del 13/09: empezar por Dubai, canal 1, con refinamiento iterativo
y criterio para ampliar. Canal 2 queda fuera. Referencia de **0.01 lotes por
senal**, una entrada, sin refuerzos, sin cambiar de direccion y sin seguir
modificaciones del proveedor. Capital y perdida maxima tolerable no fijados.

Dominio congelado: 13104 combinaciones. Primeros presupuestos de 500 por familia,
ampliaciones de 500 y exploracion distante en aproximadamente un tercio de las
propuestas. El coordinador reutiliza el motor de busqueda existente.

| Familia | Primera ronda | Ampliacion | Total | Motivo de parada |
|---|---:|---:|---:|---|
| Demora | 500 | 0 | 500 | Sin mejora material adicional |
| Retroceso | 500 | 500 | 1000 | Mejora en primera ronda; no suficiente en segunda |
| Impulso | 500 | 0 | 500 | Sin mejora material adicional |
| Recuperacion tras excursion adversa | 500 | 0 | 500 | Sin mejora material adicional |
| Total | 2000 | 500 | 2500 | Reglas de parada, no agotamiento del techo |

Para ampliar: cobertura de desarrollo completa, resultado positivo tras
recargo, dos de tres bloques positivos, participacion >=25%, concentracion
diaria <=60%, ganancia adicional >=max(1 EUR hipotetico, 2%), riesgo aproximado
no mas de 5% peor y mejora en dos bloques sin deteriorar materialmente el peor
dia. En la primera ronda la referencia son las generaciones completas que no
superan 250 candidatas: fueron 217 en estas cuatro familias.

El riesgo usado dentro del bucle es la aproximacion existente por resultados
ordenados/cesta. Para la preseleccion posterior se reconstruyo la curva conjunta
con las cotizaciones canonicas. De diez prefinalistas examinadas de esta forma
se congelaron dos: mejor resultado/caida conjunta y mayor resultado restante.

## Datos Y Limites

| Bloque | Fechas UTC | Dias con senales | Identificadas | Con precios admitidos |
|---|---|---:|---:|---:|
| Desarrollo | 27/07-12/08/2026 | 13 | 39 | 39 |
| Contraste posterior | 13/08-02/09/2026 | 14 | 55 | 50 |

Las 94 identidades por dias coinciden exactamente con la compilacion global
del corpus raw disponible. Esto no afirma cobertura de todas las publicaciones
del canal. Antes del 27/07 faltan los requisitos de evidencia raw del inventario.
Tambien se conservan 30 incidencias de reloj del 13-14/08: no se ajustaron
publicaciones o recepciones para admitirlas.

Las cinco senales sin precios utilizables permanecen en el denominador:

- 23/08: `canal1_21488`, `canal1_21497`, `canal1_21502`, sin cobertura inicial.
- 24/08: `canal1_21543`, `canal1_21544`, huecos de mercado superiores al contrato.

`inputs_v1` detecto exclusiones causadas por cortar las publicaciones por dia.
Antes de simular, `inputs_v2` separo por primera recepcion UTC despues de compilar
la identidad global; conserva las primeras recepciones tardias y documenta los
reenvios excluidos. La version anterior sigue guardada. No se relajaron
tolerancias ni se alteraron los relojes o precios.

El antiguo contraste del 10-12/08 se reutilizo expresamente como desarrollo.
Ni ese bloque ni el posterior se presentan como OOS intacto. Todos los totales
monetarios de la cohorte posterior completa permanecen nulos en los archivos.

## Finalistas Congeladas

Movimientos expresados en unidades de cotizacion XAUUSD, no en pips ni en EUR.
Los niveles dependen de las entradas hipoteticas y del perfil de ejecucion.

- **A, demora:** esperar 90 segundos; SL de 8 y TP de 12; salida temporal
  obligatoria a 5 minutos; expiracion de entrada a 3 minutos. ID `0a9fb5c4b734`.
- **B, recuperacion:** esperar un movimiento adverso de 2 y una recuperacion de
  1 desde el extremo observado; SL de 15 y TP de 8; salida obligatoria a 15
  minutos; expiracion a 3 minutos. ID `8b406291ec7a`.
- **Control propio fijo:** demora de 1 segundo, SL de 10, TP de 5, salida a 15
  minutos y expiracion a 3 minutos. ID `50fad63da958`. No es una reproduccion
  de la estrategia live ni de sus operaciones observadas.

Todos los vecinos inmediatos declarados de las finalistas se habian evaluado:
A, 24 de 24, con 16 positivos; B, 20 de 20, con 15 positivos en desarrollo tras
recargo. No se generaron vecinos nuevos despues del contraste. Hay limites
del dominio tocados: demora de 90 segundos, confirmacion de 1 y salidas de
5/15 minutos. No se certifica una meseta fuera de los valores explorados.

## Resultados Hipoteticos

**EUR simulados bajo el contrato monetario historico no certificado.** No son
beneficios observados ni una recomendacion de capital. El neto de las tablas
resta ademas un recargo de sensibilidad de 10 EUR por lote de ida y vuelta:
0.10 EUR por entrada de 0.01 lotes. No es una certificacion de comisiones reales.

Escenario primario. La caida conjunta incluye equity abierta y realizada del
escenario; no se ha recalculado incorporando ese recargo adicional.

| Regla | Neto desarrollo | Caida conjunta desarrollo | Neto posterior, solo 50/55 | Caida posterior, solo 50/55 |
|---|---:|---:|---:|---:|
| A, demora | 32.79 | 11.43 | -9.77 | 34.27 |
| B, recuperacion | 50.77 | 23.18 | 12.18 | 29.05 |
| Control fijo | 3.69 | 31.80 | -24.62 | 46.86 |

A entra en 39/39 senales de desarrollo y B en 18/39. En ese bloque ambas
alcanzan un maximo de dos senales simultaneas y 0.02 lotes conjuntos.

Sensibilidad del neto tras recargo:

| Ejecucion | A desarrollo | B desarrollo | A posterior parcial | B posterior parcial |
|---|---:|---:|---:|---:|
| Primaria declarada | 32.79 | 50.77 | -9.77 | 12.18 |
| Esperas de 1 s | 37.86 | 42.71 | -9.92 | 20.11 |
| Esperas de 10 s y deslizamiento de 0.1 | 15.23 | 38.51 | -11.98 | 7.51 |
| Esperas de 60 s y deslizamiento de 0.2 | -11.62 | 33.49 | -40.72 | -18.28 |

Los perfiles completos fijan por separado entrada, procesamiento de cambios,
cierre y confirmaciones. Son hipotesis de sensibilidad congeladas, no
probabilidades calibradas ni una cota garantizada del peor caso real.

## Verificacion

- **5278 pruebas completas aprobadas**, 634 avisos, con identidad de codigo,
  entorno y fuentes de pruebas conservada. Ademas, 164 pruebas focalizadas.
- Regresion reproducida y reparada: al cortar una generacion por presupuesto,
  las propuestas aun no evaluadas quedaban en el conjunto de vistas. Ahora
  permanecen pendientes para la siguiente ronda; no se pierden al reanudar.
- Piloto: 48 calculos, cuatro semillas incluidas en las 2500 candidatas.
- Busqueda y revisiones de desarrollo: 99411 calculos candidata-senal.
- Finalistas y control: 4272 calculos; **1068 casos coinciden en rapido,
  escalar y oraculo**, y en hechos economicos con la ejecucion serial por cesta.
- 117 resultados de desarrollo de esas tres reglas reproducidos; cinco
  decisiones de ronda recalculadas; identidades, denominadores y todos los
  escenarios verificados contra los archivos retenidos.
- Tiempo registrado de busqueda/piloto/contraste: **2404.71 segundos**, unos
  40 minutos. Preparacion, implementacion y suite de pruebas son trabajos aparte.

La concordancia comprueba consistencia bajo los mismos supuestos. No demuestra
que una operacion alternativa se hubiese ejecutado asi en el broker.

## Que Sigue Abierto

- Cobertura posterior 50/55, incidencias de reloj y certificacion monetaria.
- Evidencia nueva no utilizada para afinar o elegir la estrategia; no se ha
  calculado una probabilidad de sobreajuste corregida por los 2500 intentos.
- Capital, margen, perdida conjunta tolerable y contencion del cliente entre
  varias cestas. La comprobacion serial es por cesta, no una simulacion global
  validada del puente real.
- Flotante conjunto puro y recuperacion intradiaria. Se ha medido caida de
  equity conjunta y recuperacion a cierre diario, no esas dos metricas completas.
  En desarrollo, la recuperacion diaria medida fue de cinco dias para A y dos
  para B, dentro de la cohorte estudiada.

El siguiente bloque debe cerrar el criterio multi-escenario de desarrollo y
esas metricas antes de otra campana amplia. Despues, congelar reglas y probarlas
en una cohorte nueva completa. No basta con aumentar el numero de variantes ni
con volver a usar estas fechas como si fueran validacion independiente.

## Evidencia Local

- [Protocolo congelado](../../runtime_data/canal1_recursive_20260913/frozen_v1/protocol.json).
- [Recibo de pruebas](../../runtime_data/canal1_recursive_20260913/tests_v1.json).
- [Finalistas y vecindarios](../../runtime_data/canal1_recursive_20260913/search_v1/finalists.json).
- [Resultados completos del contraste](../../runtime_data/canal1_recursive_20260913/validation_v1/validation.json).
- [Verificacion final](../../runtime_data/canal1_recursive_20260913/final_verification_v1.json).
- [Mapa de esta campana](../development/2026-09-13-canal1-recursive-campaign.md).

Archivos de ejecucion locales en `runtime_data/`, no publicados. Cambios locales
en coordinador, pruebas y documentacion; sin commit ni push en esta tarea, sin
ordenes, sin reinicio ni despliegue. No se ha consultado la version actual de la
VM. `selected_policy` sigue siendo `null`.
