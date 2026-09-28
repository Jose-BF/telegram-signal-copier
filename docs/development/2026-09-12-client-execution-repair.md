# Correccion del cliente simulado

## Alcance congelado antes de la comparacion

- Solo investigacion local. No cambia la estrategia ni el ejecutor de produccion.
- Cola FIFO de llamadas compartida por entradas, modificaciones y cierres de una
  cesta; una llamada ocupa el recurso hasta su respuesta. Es una hipotesis
  explicita, no una reconstruccion certificada del GIL ni de otras cestas.
- Escalera decidida en linea. Modo `snapshot_batch`: las patas cruzadas por una
  observacion quedan comprometidas y se envian secuencialmente conservando esa
  observacion. Modo `fresh_quote`: cada nueva pata requiere una observacion nueva.
- SL/TP instalados siguen activos mientras el cliente espera. No usar una
  proteccion deseada como si ya estuviese instalada.
- Separar reloj de precio y de ejecucion: cotizacion al procesar, por defecto;
  alternativamente cotizacion causal posterior al envio con demora declarada
  no mayor que la demora de ejecucion. Nunca introducir fills reales como entradas.
- Conservar modo anterior por defecto. Los motores sin implementacion independiente
  deben bloquear este perfil, nunca ignorarlo ni reutilizar el motor de referencia
  como si fuese un oraculo independiente. Busqueda masiva sigue cerrada.
- Comparar las mismas 19 senales: 10 del dia 10, 7 del 9 y las primeras 2 del 11.
  Son controles retrospectivos ya vistos, no OOS nuevo. No excluir casos sin entrada,
  discrepancias ni intervalos de conversion incompletos.
- Comparacion fija: perfil base anterior 250 ms ejecucion / 250 ms respuesta;
  mismo perfil con cliente snapshot_batch; cliente fresh_quote; snapshot_batch
  con precio al enviar. Sin rejilla ni seleccion de parametros por resultado.
- Exito mecanico: regresiones causales pasan, salidas nativas no se congelan,
  cola y decisiones quedan auditables, suite completa sin regresiones nuevas.
  Exito historico no se presume: informar errores por dia, cantidad, precio y
  tiempo. No prometer coincidencia exacta ni certificacion monetaria completa.

## Progreso

- [x] Preservada copia anterior de los modulos afectados.
- [x] Regresiones y modelo de referencia verificados.
- [x] Comparacion fija de los 19 casos y limites documentados.
- [x] Verificacion completa y cierre del bloque en el mapa compartido.

Resultado: [informe y limites](../audits/2026-09-12-client-execution-results.md).
5122 pruebas aprobadas. Bloque mecanico local completado; la discrepancia
historica principal no resuelta y las fases 2/3 no cerradas. No se publica.
