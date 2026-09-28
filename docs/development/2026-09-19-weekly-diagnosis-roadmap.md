# Ruta: Resultados Reales Antes De Ampliar La Busqueda

Estado: propuesta basada en inspeccion preliminar del 19/09/2026.
Actualizacion: [comparacion semanal de sombras y contabilidad nativa](../audits/2026-09-19-weekly-shadow-comparison.md)
terminada. Los importes/contadores CSV preliminares de abajo son historicos,
no el cierre semanal: MT5 confirma Dubai -104.86 EUR (17 cestas) y Gold
-278.12 EUR (23). Solo 5 IDs Dubai y 9 Gold tienen sombras originales
completas; no hay sombra de las tres perdidas Gold. Fallos de confirmacion
del journal desactivaron el observador el 15 y el 17; el control mantiene
discrepancias con MT5. Antes de nuevas variantes: reparar/validar captura,
contrastar el control y estudiar reconstruccion retrospectiva de las perdidas.
Objetivo: aprovechar las senales de ambos canales con beneficio neto y riesgo
controlado; no maximizar el porcentaje de aciertos ni ajustar al ultimo SL.
Canal 1 = Dubai Investing. Canal 2 = Gold Signals.

## Evidencia Recuperada

- [x] Revisados contratos de investigacion y ultimo contraste Dubai documentado.
  Este ultimo cubre 258 IDs del 05/06 al 07/09, no la semana del 14 al 18.
- [x] Descargada solamente la copia pequena del diario desde la VM, sin
  reconstruir los 7 GB de eventos ni ejecutar analisis pesados en produccion.
  Fuente: runtime_data/weekly_review_20260919/trade_journal_vm_20260919.csv.
  SHA256: 0bfb3a5ce822cf4f5ff1d18a52d887fcdd60fece13799dc01b82e34ff2dde665.
- [x] Inspeccion por cierre UTC: [2026-09-14, 2026-09-19).
  Canal 1: 16 filas, seis positivas y diez negativas.
  Canal 2: 19 filas, 17 positivas y dos negativas, sin IDs repetidos.
  Campo heredado total_pnl_usd: canal 1 -79.86; canal 2 -233.86;
  positivos canal 2 +234.40, negativos -468.26.
  No etiquetar estas sumas como dinero neto conciliado ni asumir USD por el
  nombre de la columna. Verificar moneda, deals y costes antes de publicarlas
  como resultado contable. La cobertura completa aun no esta comprobada.
- Dos filas prioritarias: canal2_2908 (14/09, -302.38, RECONCILER) y
  canal2_3003 (16/09, -165.88, cierre mixto SL/TP_PROVISIONAL).
  Las etiquetas agregadas no acreditan por si solas el mecanismo de cada deal.
- Hay duplicados y filas sin canal en semanas anteriores: no comparar sus
  sumas CSV directamente. Los informes ledger remotos estaban desactualizados.
- Hubo reparaciones operativas esta semana. Verificar configuracion y version
  efectivas por episodio; mismas estrategias no implica ejecucion identica.

## Ruta Propuesta

0. **Recuperar las sombras existentes antes de nuevas variantes.** Catalogo:
   Dubai balanced (control), frontloaded 30m y 40m; Gold 555 (control), b210
   y c490. El 19/09 la VM conserva STRATEGY_SHADOW_ENABLED=1, pero esto no
   acredita un worker activo ni cobertura semanal. Copia del informe remoto:
   runtime_data/weekly_review_20260919/strategy_shadow_report_vm_20260919.json;
   ventana since=2026-08-27, until=2026-08-29, schema 1, winner=null.
   Es historico y no certifica resultados actuales. La auditoria del 07/09
   documento 198 registros (66 senales, tres politicas por senal), 63 con
   money_contract_missing; las restantes tampoco tenian certificacion global.
   El 10/09 se verifico arranque y recuperacion de 354 estados. Recuperar
   actividad/cierres/bloqueos posteriores por candidata y version, distinguir
   captura de generacion/publicacion de informes y resolver causas antes de
   sumar o comparar dinero. Incluir las dos grandes perdidas del 14 y 16/09
   si existe cobertura; no reconstruirlas y llamarlas prospectivas.

1. **Cerrar la semana observada.** Conciliar MT5 (deals, volumen, costes,
   moneda y posiciones) con senales y eventos del 14-18/09. Separar calendario
   de cierres, cohorte de entradas, exposicion abierta e intervenciones.
   Comparar las dos semanas previas con identidades deduplicadas y misma
   definicion de periodo. No sustituir datos ausentes por cero.
   Entrega: tabla diaria/por canal y explicacion de las mayores perdidas:
   regla de riesgo, interpretacion, entrada, gestion, o fallo operativo.

2. **Medir oportunidades y riesgo.** Para las perdedoras y tambien las
   ganadoras: recorrido favorable/adverso, secuencia de precios y mensajes,
   lotes simultaneos y coste. Determinar si hubo beneficio protegible y
   cuanto retroceso necesitaron las ganadoras. No suponer que BE o un stop
   menor mejoran el conjunto solo porque evitan los dos peores cierres.
   Reconstruir por separado el scorecard del trader con sus convenciones
   de pips, parciales, BE y SL; compararlo con una gestion ejecutable.

3. **Mejoras sencillas antes de fuerza bruta.** Conservar estrategia actual
   como control. Proponer un primer presupuesto de hasta 30 variantes Gold,
   no ejecutado: limite de perdida por cesta, carga/numero de entradas,
   proteccion de beneficio/BE, salida temporal y gestion del proveedor.
   Elegir niveles y capacidades tras el diagnostico, congelarlos antes del
   calculo, y variar primero un mecanismo cada vez. Comparar beneficio
   conservado y perdido, peor cesta/dia, equity y exposicion simultanea.
   Distinguir lotaje actual de comparaciones a riesgo equivalente.
   Para Dubai retomar la ablacion pendiente de tres/dos/una entrada con las
   mismas reglas de cesta, sin eliminar a posteriori las cestas perdedoras.

4. **Ampliar evidencia, no reiniciar el proyecto.** Ventana propuesta:
   27/07-18/09, condicionada a cobertura real por mensaje, precios y politica.
   Aprovechar datos anteriores cuando tengan evidencia suficiente. Mantener
   dias y cestas juntos; evaluar ventanas cronologicas semanales sucesivas,
   sin mezclar aleatoriamente futuro y pasado. Cada alternativa debe usar
   solo informacion disponible al decidir, incluidos mensajes editados.
   La ultima semana ya inspeccionada no es validacion intacta de los ajustes
   inspirados en ella; congelar finalistas y evaluar datos posteriores.

5. **Decidir con resultados.** Ampliar a miles de reglas solo si la ronda
   sencilla no resuelve el problema y existe una pregunta nueva concreta.
   Fijar entonces familias, capacidades admitidas, limite de candidatos y
   tiempo; conservar resultados negativos y detener busquedas redundantes.
   Una candidata requiere estabilidad entre ventanas y parametros cercanos,
   costes/ejecucion adversos y riesgo explicito, no una gran ganadora aislada.
   Verificar correspondencia con el comportamiento live antes de proponer
   despliegue. Ninguna promocion automatica ni rentabilidad presupuesta.

## Reutilizacion Y Limites

- Contabilidad: reconcile_mt5_ledger.py y analysis/daily_report.py, con inputs
  actualizados y acotados. El CSV preliminar no sustituye esa conciliacion.
- Gestion sobre entradas inmutables: strategy_simulator.py y su contrato.
  Cambiar entradas, volumen, ocupacion o reentradas exige el motor apropiado
  y estado propio; una comparacion condicionada no certifica todo el bot.
- Reutilizar research/gold_iterative, research/dubai_iterative y evidencia
  causal existente. No reparar universalmente todo el simulador para contestar
  una pregunta que solo depende de capacidades ya verificadas.
- JSON es formato, no garantia de cronologia: preferir recepciones/revisiones
  guardadas y declarar limitaciones de exportaciones finales.
- No descartar senales bloqueadas/no ejecutadas del universo general. Solo
  las politicas que necesitan un dato ausente quedan bloqueadas por ese dato.
- Analisis pesado local, no en la VM. Mantener originales y trabajos previos.
- Este bloque no cambia codigo de trading, configuracion, automatizaciones,
  ordenes, publicaciones ni servicios. La ruta requiere revision conjunta
  antes de busqueda masiva y autorizacion separada antes de cambiar produccion.

## Uso De IA

Propuesta: Astra para diagnostico dificil/diseno y revision final; Sol para
analisis e implementacion delicada; Terra para tareas acotadas; Luna solo para
transformaciones mecanicas con validadores. Usar esfuerzo medio por defecto
y elevarlo en discrepancias, no mantener Max/Ultra para todo.
Las simulaciones las ejecutan programas locales, no una llamada IA por variante.
Guardar tablas y checkpoints compactos para no releer todos los logs en cada
conversacion. Modelos no cambiados por este documento.

Pendiente antes de adopcion: capital de referencia y perdida maxima aceptable
por cesta/dia/cuenta. No bloquean el diagnostico observado ni se inventan para
declarar que una candidata es apta para la cuenta del usuario.
