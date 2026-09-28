# Preparacion Y Primera Busqueda Acotada

Peticion del 12/09: ejecutar el plan acordado para preparar una primera
comparacion pequena de reglas propias para Dubai y Gold. No hay autorizacion
para publicar, reiniciar el bot, operar ni iniciar una busqueda masiva.
Una candidata positiva en el historico no es una estrategia aprobada en real.

## Hitos

1. CERRADO LOCAL: reparar la admision de dinero desconocido en los tres motores.
   Regresiones: maximo perdido antes de armar profit-lock, salida temporal
   condicional y riesgo monetario incompleto. Preservar decisiones por precio,
   senales bloqueadas y contabilidad que si sea conocida. Verificar vecinos,
   cartera, seleccion y suite completa con identidad de codigo nueva.
   Evidencia inicial: 96 regresiones nuevas; 5218 pruebas completas sin fallos,
   errores u omisiones; identidad de fuentes estable. Recibo inmutable:
   `../../runtime_data/small_search_readiness_20260912/money_full_v1/receipt.json`.
   Tras anadir el cierre temporal explicito: 5263 pruebas completas sin fallos,
   errores u omisiones; 45 regresiones temporales nuevas. Recibo final:
   `../../runtime_data/small_search_readiness_20260912/timed_exit_full_v1/receipt.json`.
2. INVENTARIO TERMINADO, ADMISION PARCIAL: delimitar ejecucion y admitir datos
   de cada canal. Inventario de
   senales, fechas, Bid/Ask, conversion, costes, horizontes y uso previo.
   No ocultar los defectos de cola/concurrencia tras una latencia constante ni
   exigir nuevas operaciones reales para todo contrafactual historico.
   Alcance propuesto: una sola entrada de 0.01 lotes por senal, sin refuerzos,
   SL/TP propios por precio y un cierre temporal explicito. Se implementa la
   opcion temporal incondicional schema2 antes de congelar el experimento;
   no cerrar posiciones artificialmente al final del archivo de precios.
   Trece dias, 39 disparadores Dubai y 94 Gold del chat actual, recibos raw con
   chat/revision comprobados. Cobertura 15 min: 39/39 y 93/94; 60 min: 38/39 y
   89/94. Huecos y una extension nocturna permanecen bloqueados, no eliminados.
3. CERRADO: congelar presupuesto pequeno, reglas simples, periodos y criterios
   antes de evaluar. Capital y perdida abierta tolerable preguntados al usuario;
   sin respuesta solo cabe un tamano de referencia explicitamente diagnostico.
   Separar cuentas compartidas/separadas y riesgo conjunto cuando corresponda.
   12 combinaciones por canal, 24 en total, sin mutaciones posteriores. Regla
   propia de una entrada 0.01 lotes, SL 5/10/15, TP 5/10 (unidades de precio
   XAUUSD, no EUR), cierre temporal 15/60 minutos. Desarrollo 27/07-07/08;
   contraste 10/08-12/08. Ambos periodos son historico reutilizado, no OOS nuevo.
   Seis variantes de 15 min usan 20 min de datos suficientes; las de 60 min
   conservan 70 min. Addendum previo a resultados, mismas senales y tolerancias.
4. CERRADO EN EL ALCANCE DIAGNOSTICO: ejecutar solo sobre el alcance admitido,
   conservar cada intento,
   revisar beneficio neto, peor perdida abierta, recuperacion y concentracion;
   contrastar escenarios de ejecucion y periodos reservados si los hay.
   1146 evaluaciones del motor rapido, 24 variantes conservadas. Una unica
   prefinalista diagnostica Dubai (SL10/TP5/15min), +3.77 EUR hipoteticos;
   +0.77 con coste adicional, drawdown de cuenta 31.36. Ninguna Gold. Datos y
   codigo estables. Contraste terminado: 664 casos coinciden entre tres motores
   y con los hechos economicos del cliente serial por cesta. 2656 evaluaciones,
   124 resultados iniciales reproducidos exactamente. La prefinalista Dubai
   pierde en desarrollo con los escenarios de 10 y 60 segundos; no se aprueba.
   Contraste cronologico positivo pero pequeno y reutilizado, no OOS intacto.
5. CERRADO, INFORME LISTO: entregar resultados y decision fundada, incluida la posibilidad de
   ninguna candidata defendible. Revision conjunta antes de ampliar la busqueda;
   cualquier prueba demo que envie ordenes requiere autorizacion propia.
   Decision: ninguna propuesta para aprobar. No se ha iniciado otra familia.
   Capital/riesgo, dinero historico certificado, contencion global aplicable y
   periodo intacto siguen pendientes; no se confunden con esta primera busqueda
   ya terminada. Recibo final con presupuesto, fuentes y denominadores:
   `../../runtime_data/small_search_readiness_20260912/final_verification_v1.json`.

## Limites Y Evidencia

- Motor compartido: `research/dubai_iterative/{engine,fast_engine,oracle}.py`.
- No reutilizar checkpoints anteriores tras cambios de implementacion.
- Una perdida maxima parcial no se publica como metrica completa; desconocido
  no equivale a cero, a una decision falsa ni a una operacion no ejecutada.
- Los controles usados para diagnosticar son retrospectivos, no validacion
  intacta. Si no hay periodo intacto disponible, declararlo sin inventarlo.
- Controles anteriores: `../audits/2026-09-11-execution-divergence-root-cause.md`
  y `../audits/2026-09-12-execution-uncertainty-results.md`.
- Registrar aqui los cierres de hitos y referencias de verificacion. El mapa
  compartido sigue siendo `2026-09-08-simulation-foundation-readiness.md`.

Estado inicial: cambios previos preservados; sin cambios live ni publicacion.

Resultado de esta primera familia y evidencia:
[informe de busqueda](../audits/2026-09-12-small-search-results.md).
