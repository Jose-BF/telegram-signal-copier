# Secuencia De Salidas Del Simulador

## Alcance

Continuacion local autorizada de la robustez. Sin nuevas candidatas, cambio
de politica/capital/lotes, publicacion, MT5, reinicio ni ordenes. Preservar
las pruebas de la fase anterior y sus 3.256 resultados. No repetir el piloto
independiente si sus motores/inputs no cambian.

Separar tres contratos: dinero observado por ticket; hechos de cada deal de
salida frente al motor; decisiones/solicitudes/intentos/confirmaciones anteriores
al deal. La coincidencia de los dos primeros no demuestra el tercero. Tampoco
el retcode de una solicitud prueba por si solo el deal realizado ni su causa.

## Hitos

- [x] Verificar fuentes locales y contratos; inspeccionar trazas retenidas.
- [x] Reproducir cierres incompatibles que conservan el mismo total monetario.
- [x] Incorporar comparacion por deal, con precision temporal y evidencia
  monetaria explicitas, en certificador e informe de conjunto.
- [x] Auditar la vinculacion causal ya disponible sin importar modulos live
  ni completar identificadores ausentes por proximidad temporal.
- [x] Contrastar los cinco controles Gold existentes condicionados a fills
  observados, si las fuentes permiten construirlos; conservar bloqueos.
- [x] Revision, suite proporcionada al cambio y registro final de limites.

## Criterios

Conservar posicion/deal/order, entradas, parciales, dinero/costes, hora UTC y
motivo nativo. No inferir SL/TP de comentarios cuando falta el motivo del broker.
Un cierre experto no prueba una decision concreta de estrategia. No repartir
costes de apertura entre parciales sin un modelo declarado. El orden entre
operaciones simultaneas no se inventa; cantidades y multiplicidades se conservan.

El comparador de deals no ejecuta estrategias ni altera motores. La revision
causal reutiliza contratos existentes y mantiene como desconocido lo no ligado
de forma verificable. Un certificado mas exigente tiene version nueva; las
historias antiguas no se sobrescriben ni se convierten en OOS.

Presupuesto: pruebas sinteticas y controles fijos, cero busquedas de candidatas.
La comprobacion de fills observados tiene como maximo cinco cestas Gold por
los tres motores, con sus parametros originales. Los datos nuevos o una mejora
de captura que requiera despliegue necesitan su propia decision posterior.

Resultado: `../audits/2026-09-08-simulator-exit-sequence.md`. Suite final:
3.551 pruebas aprobadas, 358 fuentes estables. Los hechos de salida del piloto
siguen discrepando y la secuencia completa de decisiones sigue sin certificar.
