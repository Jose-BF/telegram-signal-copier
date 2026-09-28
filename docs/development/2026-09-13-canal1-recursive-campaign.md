# Campana Iterativa De Canal 1

Autorizacion del 13/09: aplicar el plan aceptado empezando por Dubai, con
exploracion/refinamiento iterativos y criterio para ampliar la primera ronda.
Canal 2 queda fuera de esta ejecucion. Solo trabajo local offline; no implica
ordenes, despliegue, reinicio, automatizacion futura ni aprobacion en real.

## Presupuesto Y Metodo

- Primera ronda: hasta 2000 candidatas unicas en las cuatro familias de entrada
  declaradas. Controles, piloto y vecinos nuevos cuentan dentro del presupuesto.
- Techo de esta campana: 10000 candidatas unicas y cuatro horas de calculo de
  busqueda/contraste. Es un limite, no un objetivo de consumo ni plazo para
  implementar y certificar una estrategia.
- Refinamiento por generaciones sobre el motor de busqueda existente. Reservar
  aproximadamente un tercio de las propuestas para explorar otras zonas.
- Ampliar solo con evidencia de mejora material y estabilidad en desarrollo;
  fijar indicadores, umbrales, cuotas y parada por estancamiento en el protocolo
  antes de evaluar las nuevas candidatas. No extender porque un resultado de
  validacion guste o disguste. Toda regla nueva y todo intento quedan registrados.
- Parametros validos y operadores cerrados sobre un dominio explicito. El
  limite de una grilla no demuestra haber encontrado el optimo global. Una
  mejora aislada exige contraste, no una nueva promesa de rentabilidad.
- Los periodos de contraste/reserva no se entregan al bucle de refinamiento.
  Historico ya usado sigue siendo retrospectivo aunque el bucle no lo vea.
- Tamano de referencia: 0.01 lotes, una entrada, sin refuerzos. Capital y
  perdida conjunta tolerable siguen pendientes; no se inventan para lanzar
  diagnosticos ni se ignoran para aprobar riesgo/cartera en real.

## Hitos De Ejecucion

1. HECHO: coordinador de rondas sobre run_search; pruebas de limites,
   reanudacion, separacion del contraste, candidatos desconocidos y truncado.
   164 pruebas focalizadas y 5278 de suite completa pasan bajo nueva identidad.
2. HECHO: 39/39 senales de desarrollo (27/07-12/08) y 50/55 cargadas para
   contraste posterior (13/08-02/09). Las cinco bloqueadas no se eliminan.
   El contraste antiguo del 10-12/08 pasa expresamente a desarrollo; no es OOS.
3. HECHO: 2500 candidatas; retroceso amplio de 500 a 1000 y despues paro.
   Las otras tres familias pararon en 500; decisiones reproducidas sin contraste.
4. CONTRASTE HECHO, RIESGO COMPLETO PENDIENTE: dos finalistas y control fijo,
   1068 casos coincidentes en tres motores y en hechos economicos seriales,
   cuatro escenarios, curva conjunta y contraste posterior parcial. Siguen
   pendientes flotante conjunto puro, recuperacion intradiaria, capital/margen,
   cliente global y nueva evidencia intacta. No hay aprobacion para real.
5. HECHO: [informe y siguiente decision](../audits/2026-09-13-canal1-recursive-results.md).
   La campana se cierra sin promocion automatica ni ampliacion a canal 2.

## Protocolo Congelado

Piloto completado: cuatro semillas sobre tres senales predeclaradas de
desarrollo, 48 calculos entre rapido, escalar, oraculo y cliente serial.
Cero discrepancias en tres motores y cero diferencias economicas seriales.
Las cuatro semillas cuentan dentro del presupuesto de candidatas de la ronda.
Busqueda y contraste terminados en 2404.71 segundos registrados. Las dos
finalistas estan congeladas; ninguna queda aprobada para operar. No se amplian
las rondas porque un resultado del contraste guste o disguste.

Artefactos locales: `runtime_data/canal1_recursive_20260913/frozen_v1/`.
Cuatro familias: demora, retroceso, impulso y recuperacion tras excursion
adversa. Dominio: 3072 + 3072 + 3072 + 3888 = 13104 combinaciones.
Primera ronda: 500 por familia; ampliaciones de 500, turnandose entre familias.
Un tercio aproximado de cada generacion conserva la exploracion distante.

Para ampliar, exigir desarrollo completo, resultado positivo tras coste extra,
al menos dos de tres bloques temporales positivos, participacion >=25% y
concentracion diaria <=60%. Frente al punto de comparacion, mejora minima
de max(1 EUR hipotetico, 2%), sin aumentar la aproximacion de drawdown mas
del 5%, sin deteriorar materialmente el peor dia y mejorando dos bloques.
Una nueva ganadora aislada no basta si antes no habia candidatas estables.
La primera ronda compara su segunda mitad con la primera; las siguientes
comparan con el estado al cierre de la ronda anterior. El contraste no decide
ampliaciones. El drawdown conjunto exacto se reconstruye despues para finalistas.

La admision `inputs_v1` detecto que cortar tambien la publicacion por dia
excluia primeras recepciones tardias. `inputs_v2` compila primero la identidad
global y luego separa las primeras recepciones por dia UTC. Preserva los
reenvios excluidos, relojes originales y todas las incidencias; verifica que
el conjunto por dias coincide exactamente con el global. No hubo simulaciones
de estrategias durante esta correccion. La version anterior queda conservada.

Los horizontes son 20 minutos, con expiracion de entrada hasta 3 y salida
temporal obligatoria hasta 15. No se alteran tolerancias de precios/FX.
Las cinco ausencias posteriores son tres del 23/08 y dos del 24/08. Tambien
se preservan las incidencias de publicacion posterior a recepcion del 13-14/08.

## Verificacion

Reutilizar el calculo y los contratos existentes. Pruebas de resultado conocido
para las entradas, datos desconocidos y limites; integracion del coordinador,
presupuesto y checkpoints; suite completa al modificar comportamiento compartido.
Nuevas identidades tras cambios de codigo, sin reutilizar checkpoints historicos
bajo otra implementacion. Conservar las barreras monetarias y de ejecucion.

Punto de partida: [primera ronda cerrada](../audits/2026-09-12-small-search-results.md),
5263 pruebas verificadas bajo su identidad, sin que ello admita automaticamente
nuevas capacidades. Las ediciones posteriores requieren verificacion aplicable.
