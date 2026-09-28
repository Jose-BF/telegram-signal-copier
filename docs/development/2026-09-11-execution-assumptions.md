# Senales Historicas Y Supuestos De Ejecucion

Trabajo posterior del11/09: `2026-09-11-execution-review-contract.md` contiene
criterios, resumen descriptivo de mediciones y sensibilidad ejecutada con
perfiles hipoteticos. No se implemento un relleno automatico ni se cambiaron
reglas o perfiles congelados. La propuesta inferior conserva su alcance.

## Aclaracion Del Usuario

El usuario reitera que el estudio usa el JSON del canal como disparador
fechado de compra/venta. Las entradas y la gestion las genera la estrategia
propia con precios historicos; no se exige que el bot operase entonces ni
conocer los fills, cierres o beneficios reales del proveedor. El ejemplo de
mayo explica el uso deseado, no confirma que ese periodo este disponible.

Una senal no se descarta por desconocer su ejecucion real. El resultado de
una politica puede ser entrar, esperar o no entrar, y debe explicar esa
decision sin confundirla con una senal omitida por falta de datos.

## Estimaciones Propuestas

El usuario propone estimar retrasos, spread u otros costes a partir de
mediciones disponibles. Es viable como hipotesis de simulacion explicita:
media/mediana y escenarios de sensibilidad, o una distribucion reproducible,
cuando exista evidencia suficiente para el uso. Una media no garantiza
cercania para cada operacion ni representa por si sola los extremos.

No reemplazar Bid/Ask observado por un spread medio. Si se dispone de una
serie de referencia pero no del spread, un estudio puede definir un escenario
de spread estimado y conservar todas las senales, identificando el precio
construido como hipotetico. Estimar spread o retraso no recupera un recorrido
de precios ausente. Los supuestos recientes tampoco prueban las condiciones
de meses anteriores: su transferencia debe declararse y someterse a sensibilidad.

Los datos para estimar parametros y sus condiciones se fijan antes de evaluar
las estrategias. No elegir una media o tolerancia por mejorar el resultado.
Conservar parametros, fuentes, periodo, semillas cuando proceda y denominador
completo en cada escenario. No presentar las hipotesis como costes observados,
precision exacta de MT5 ni garantia de rentabilidad.

## Separacion Del Contraste Natural

Las operaciones nuevas contrastan mecanismos generales y supuestos, no
constituyen un requisito de registrar en vivo cada senal historica. El
contraste exacto con evidencia nativa y los estudios hipoteticos tienen
contratos distintos. No debilitar el primero para admitir silenciosamente
una imputacion del segundo.

Esta nota registra el alcance y la propuesta, no implementa un nuevo fallback,
calibra valores, admite todo el historico ni autoriza busqueda masiva. La
publicacion operativa del 11/09 no cambia reglas, escenarios o tolerancias
del modelo congelado. El primer experimento sigue sujeto a revision conjunta.
