# Canal 1: Candidata Para Prueba Sin Dinero Real

## Peticion Y Fronteras

El usuario pide una candidata al despertar, incluso publicada y en produccion,
con verificacion de diferencias de ejecucion aprendidas de la 555. Se acepta
avanzar autonomamente en investigacion, codigo y comprobaciones. No se promete
encontrar una ganadora ni igualdad de fills futuros. No se activa negociacion
autonoma con dinero real ni se publica en una rama que pueda activarla.
El despliegue anterior del 08/09 era demo; eso no acredita la cuenta actual.

El watcher consulta origin/main y puede reiniciar. Hasta comprobar destino,
ausencia de exposicion/entradas pendientes y aislamiento de ordenes, no push,
reinicio ni cambio de politica. Canal 2 permanece fuera del estudio y sin cambios.

## Cambio De Prioridad

La busqueda acotada utiliza las 16 familias ya verificadas en el laboratorio v3
y un maximo de 128 reglas congeladas antes de cargar precios. No se habilita la
busqueda masiva. Contexto previo/ATR/tendencia adaptativa, capital, overnight,
martingala, netting y liberacion dinamica de reservas siguen excluidos.
Se conserva la muestra anual completa, escenarios de revision y publicacion
inicial separados, jornadas bloqueadas visibles, y ventanas moviles existentes
de 56/14 dias. Todo uso de este historico sigue siendo retrospectivo, no OOS nuevo.

El objetivo operativo de este bloque es una hipotesis para demo/observacion,
si supera criterios predeclarados. selected_policy y promocion monetaria real
continuan desactivados. Una hipotesis fragil no se convierte en aprobada por plazo.

## Hitos

- [x] Recuperar instrucciones, evidencia v3 y riesgos de publicacion automatica.
- [x] Protocolo reproducible, rejilla de 106 reglas y regresiones del estudio anual.
- [x] Evaluacion anual en motor existente, dos perfiles y coste adicional
  hipotetico por volumen. Preservar todas las filas y los dias bloqueados.
- [x] Comparacion por ventanas moviles: ninguna regla supera los criterios.
  No realimentar ventanas como OOS intacto ni forzar una nominacion.
- [ ] Reevaluacion finalista por tres motores y cartera: no aplica en esta
  campana al no existir nominacion valida. No se afirma validacion anual triple.
- [x] Informe de rechazo y estado local/no publicado; demo actual sin verificar.

## Presupuesto

Maximo 128 reglas, 350000 evaluaciones rapidas y 5000000000 visitas de
cotizacion; dos horas de proceso para cribado, un control por jornada guardado
sin sobrescribir. Finalistas: maximo dos, con presupuesto separado, detenido
ante discrepancias y sin ampliar tolerancias. No ejecucion en proceso del bot.

## Verificacion

El plan inicial no preveia modificar motores. La primera campana encontro una
discrepancia real y se detuvo: la reparacion descrita abajo invalida la vigencia
del laboratorio v3 frente al codigo actual, sin modificar sus archivos.
Nuevos coordinador/CLI/pruebas enlazan contratos existentes. Protocolo y
resultados atan fuentes, implementacion y entorno, con verificador nuevo.
Pruebas causales: cambios de resultados posteriores no alteran la eleccion
de ventanas anteriores; datos ausentes no equivalen a cero; dinero no finito
rechazado; una jornada incompleta no se convierte en una cuenta anual completa.
Pruebas completas requeridas antes de cualquier entrega operativa.

## Incidente Y Reanudacion Con Codigo Nuevo

`paper_campaign_v1` queda preservada en `blocked_engine_incident`, sin ranking,
tras 93 jornadas/escenarios y 61692 evaluaciones. En el caso
`dubai_stream:revision_time:18185` del 19/03 a las 14:00:32 UTC habia cotizaciones
con la misma marca temporal. Scalar y fast bloqueaban la peticion ambigua,
mientras oracle aceptaba un indice. No se elimina el caso ni se afloja el gate.

Antes de reparar se copiaron y verificaron 65 fuentes en
`implementation_before_request_quote_binding_v1`. El contrato base
`timestamp_only` mantiene el bloqueo y ahora oracle lo respeta. El modo nuevo
explicito `timestamp_and_ordinal` vincula hora e indice de la cotizacion origen,
sin ordenar, deduplicar ni inventar tiempos. Solo admite reglas propias de
mercado representadas; no acredita recepcion observada ni equivalencia live.

El caso historico reproduce los tres motores en ambos modos y ambos perfiles.
Las 391 regresiones focalizadas y 5602 pruebas completas pasan. Laboratorio
`shared_family_lab_v4` verificado: 5472 resultados iguales a v3 salvo la huella
de comportamiento. La nueva `paper_campaign_v2`, terminada, usa el modo ordinal
explicito, mismos datos, 106 reglas, presupuestos y criterios predeclarados.
No se reanuda ni sobrescribe v1; no se cambia codigo durante los calculos.
Evidencia y condiciones de puesta en marcha:
[auditoria nocturna](../audits/2026-09-14-canal1-overnight-candidate.md).

## Cierre

209668 evaluaciones, 293 pares jornada/escenario y 237652 filas retenidas.
Verificador actual pasa; 1052 fuentes y 295 archivos. Ninguna de las 106
reglas supera el filtro, ninguna es positiva en los cuatro grupos. No se
ejecuta la fase de finalistas, no se modifica la seleccion ni se amplia el
presupuesto. Sin candidata admitida y sin publicacion o activacion. La
auditoria conserva la pista mas cercana como rechazada, no lista para probar.
