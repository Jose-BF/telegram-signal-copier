# Validacion de confianza del simulador

## Objetivo Y Limites

Contrastar el simulador con evidencia MT5 antes de ampliar el estudio de
estrategias. Trabajo local y offline; no publicar, reiniciar, instalar servicios,
consultar MT5 ni cambiar politica, lotes o cuenta. Preservar fuentes y resultados
anteriores. Capital de inversion abierto; esta validacion no lo fija.

Separar contabilidad observada, gestion condicionada a fills reales y simulacion
independiente desde mensajes/precios. Ni igualdad de motores ni igualdad del
beneficio agregado prueban por si solas paridad integral con MT5.

## Hitos

- [x] Revisar contratos y evidencia existente: piloto independiente del 07/09,
  calibracion de aperturas del 03-07/09 y preparacion causal Gold.
- [x] Comprobar que el control Gold detecta diferencias entre tickets aunque
  el total de la cesta coincida; reproducir antes de cualquier correccion.
- [x] Reforzar la barrera afectada y verificar regresiones de identidad,
  volumen, importes por ticket y datos incompletos sin ampliar tolerancias.
- [x] Revalidar el piloto congelado, sin usar fills reales como entrada de
  la simulacion independiente ni sobrescribir resultados anteriores.
- [x] Ejecutar pruebas proporcionales y documentar coincidencias, incidencias,
  limites y siguiente evidencia necesaria.

## Criterios

La conciliacion monetaria requiere identidad y cuentas por ticket, no solo por
cesta. Una discrepancia material o un dato ausente impide la afirmacion afectada.
Los cierres parciales deben conservar su volumen y agregarse solo dentro del
ticket correcto. La comparacion temporal/causal y la ejecucion hipotetica son
barreras adicionales, no equivalentes a contabilidad exacta.

El piloto del 07/09 es retrospectivo e intradia: no se convierte en dia completo,
OOS ni evidencia posterior al ultimo despliegue. Sus supuestos y escenarios
0/250/1000 ms ya estaban fijados; reproducirlos es validacion, no una busqueda.
Los datos ya vistos no se reservan retrospectivamente como validacion intacta.

Presupuesto de esta fase: controles y regresiones, repeticion de las 24
ejecuciones fijas del piloto y sus ocho controles semanticos previos como maximo,
cero candidatas nuevas y ninguna
seleccion de estrategia. Fuente base: HEAD 08551e9a8, con cambios locales previos
en el adaptador causal Gold y sus pruebas que deben preservarse.

## Incidencia Reproducida

El control anterior aceptaba como exacta una cesta de 6 EUR con tickets reales
de 1/5 EUR y tickets simulados de 2/4 EUR. El error se reprodujo con los tres
motores de acuerdo. La correccion exige identidad, entrada, volumen cerrado y
dinero por ticket, con cierres parciales agregados solo dentro de su ticket.
La prueba no afirma que ese error haya ocurrido en una cuenta real.

El informe de conjunto tambien aceptaba el contrato antiguo y podia declarar
paridad integral sin una comparacion de la secuencia de decisiones/deals de
salida. Ambas situaciones tienen regresiones rojas. El nuevo contrato v2 no
rehabilita certificados v1; la secuencia de salida permanece explicitamente
sin verificar y bloquea esa afirmacion integral, no borra la contabilidad.
