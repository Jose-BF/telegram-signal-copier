# Comparacion Reutilizable Del Recorrido De Riesgo

## Estado Y Alcance

Continuacion del objetivo activo tras almacenamiento/admision. La vuelta anterior
produjo progreso verificado, no bloqueo ni finalizacion. Trabajo local sobre
`feature/gold-555-live-trial`, base `7415941a410d18288fa4e08da76809f70b125efa`.
Se conservan todos los cambios y archivos historicos. Sin commit, push, VM,
ordenes, cambio de estrategia ni busqueda de candidatas. Revision directa.

Se implementa una parte concreta de E5: comparar la trayectoria de exposicion
y dinero, no solo el P/L final. No certifica aun paridad completa de decisiones,
el arbitraje compartido entre cestas, ejecuciones alternativas ni rentabilidad.
La politica de colas del runtime sigue siendo distinta del modelo de cliente
aislado por cesta; no se ha habilitado silenciosamente ese uso en el motor.

## Implementacion

- `research/risk_trajectory.py`: consume secuencias de entradas/salidas con
  identidades logicas ya vinculadas por los adaptadores existentes. Conserva
  precios, volumen, tiempos y dinero de cada fill; no vuelve a simular fills.
- Rejilla comun para ambas trayectorias: observaciones XAUUSD retenidas y
  fronteras de los deals. BUY se valora a Bid y SELL a Ask. FX previo causal,
  lado segun signo y redondeo por posicion con el helper monetario del proyecto.
- `broker_money.convert_profit_amount` extrae la conversion aritmetica ya
  usada por BrokerMoneyConverter; la seleccion temporal de la cotizacion y
  todos sus gates permanecen en su llamador. No cambia formulas ni costes.
- Registra realizado, flotante, total, volumen largo/corto, numero de posiciones
  y contribucion por pata. Compara cada pata para detectar errores compensados
  aunque el saldo agregado sea identico. Conserva cotizaciones repetidas en un
  mismo instante, en su orden de origen; no ordena una cinta desordenada.
- Metricas: minimo/maximo desde origen cero y drawdown desde maximo previo.
  No confunde minimo, flotante puro y drawdown. Cierres parciales reducen la
  exposicion en su momento; costes de apertura se contabilizan al abrir.
- Cualquier hueco aplicable deja `metrics=null`, conserva muestras desconocidas
  y sus motivos, y ofrece aparte `known_sample_metrics`, que solo describe las
  muestras conocidas. La primera divergencia es la primera comparable; no
  demuestra que no haya otra dentro de un intervalo desconocido anterior.
- `tools/compare_risk_trajectories.py`: adaptador acotado al contrato existente
  `raw_message_control_diagnostic_v2`. Verifica hashes, protocolo, universo,
  moneda, reloj de las cintas, ledger y discrepancias archivadas de motores.
  Conserva toda la matriz esperada y las filas faltantes/inesperadas. Salida
  exclusiva, sin sobrescribir archivos, hashes de fuentes antes/despues.
- Presupuesto: 200 casos, 128 MB por entrada, tres millones de filas por cinta,
  200.000 cotizaciones por caso, cinco millones de valoraciones posicion/punto
  y comprobacion de plazo de 600 s entre casos. No es un limite duro capaz de
  interrumpir una unica operacion del SO bloqueada.

La rejilla no contiene cada cambio EURUSD entre ticks XAUUSD; no se presenta
como extremos continuos ni como todas las cotizaciones del mercado. Se declara
la convencion de aplicar deals confirmados antes de valorar cotizaciones con
el mismo timestamp. El orden submilisegundo del broker sigue desconocido.
Swap devengado no nulo sin trayectoria queda bloqueado. Este comparador es
por cesta en EUR, no drawdown de cuenta/margen ni suma de minimos de cestas.
La fuente simulada conserva su version historica; revalorar sus fills ahora
no la convierte en una ejecucion del motor actual ni en evidencia OOS.

## Ensayo Historico Sin Reajuste

Comando ejecutado en Python 3.11.9:

```text
python tools/compare_risk_trajectories.py --study runtime_data/simulation_foundation_20260909/independent_v3 --analysis runtime_data/causal_capture_today_20260908/end_analysis_v4 --output reports/e5-risk-comparison-20260921/retrospective-20260908.json --max-market-gap-ms 5000
```

18 senales del 08/09: 7 Dubai y 11 Gold, cinco escenarios de ejecucion ya
congelados, **90 controles conservados**, cero estrategias nuevas. Tiempo:
128,62 s; archivo 362.755 bytes. Limite de antiguedad FX de 5 s heredado del
protocolo; limite de hueco de mercado de 5 s declarado antes del ensayo. No se
modifico ninguno tras conocer el resultado. Estos limites de disponibilidad
no son tolerancias de coincidencia monetaria ni certificacion de toda la cinta.

| Canal | Diferencias observables sin huecos aplicables | Bloqueados | Total |
| --- | ---: | ---: | ---: |
| Dubai | 19 | 16 | 35 |
| Gold | 30 | 25 | 55 |

40 filas tienen conversion desactualizada; otra conserva el bloqueo previo
`entry_request_in_flight_at_strategy_exit`. Las 49 filas restantes muestran
alguna diferencia de recorrido. **Diferencia no implica automaticamente bug**:
estamos comparando fills hipoteticos con fills observados; algunas diferencias
son pequenas. No se selecciona escenario ni se ajusta demora para hacerlas cero.
En la referencia sin demora son comparables 4/7 Dubai y 6/11 Gold, con las ocho
ausencias restantes conservadas. Esto no cierra la matriz historica amplia.

Ejemplos de la referencia, solo en rejilla retenida sin huecos aplicables:

| Senal | Minimo observado reconstruido / hipotetico EUR | Drawdown observado reconstruido / hipotetico EUR |
| --- | --- | --- |
| canal1_22284 | -1,75 / -1,75 | 2,21 / 2,21 |
| canal1_22312 | -22,20 / -23,02 | 28,21 / 28,52 |
| canal2_2615 | -66,58 / -111,79 | 75,83 / 116,09 |
| canal2_2628 | -9,88 / -19,55 | 12,81 / 26,90 |

Igualdad de extremos tampoco basta: canal1_22284 conserva diferencias de
trayectoria de hasta 0,18 EUR y de instantes de exposicion.

## Explicacion Que Orienta La Siguiente Reparacion

Gold 2615 termina en **+19,79 EUR en realidad y en los cinco escenarios**.
Sin embargo, sus drawdowns hipoteticos son 116,09 EUR en referencia,
75,83 EUR con fill de 250 ms y 189,53 EUR con fill de 1.000 ms. Ni el saldo
igual ni acertar un drawdown aislado prueban paridad: el escenario de 250 ms
aun tiene una diferencia temporal maxima de 4,23 EUR.

La primera entrada real fue 4405,05 y la hipotetica de referencia 4404,90.
Ambas politicas anclan la escalera al primer fill, con paso 1,5. El segundo
refuerzo usa por tanto umbral 4402,05 real frente a 4401,90 hipotetico.
Consulta independiente de la cinta conservada, con SHA256 verificado:

- Primer cruce Ask <=4402,05: 12:05:02.357 UTC, Ask 4402,04.
  Fill observado 12:05:02.664, 4402,02.
- Primer cruce Ask <=4401,90: 12:10:48.846 UTC, Ask 4401,78.
  Es el instante/precio hipotetico archivado de ese refuerzo.
- Entre 12:05:02 y 12:10:48 UTC se conservan 2.943 ticks; minimo Ask 4401,95.
  El precio llega al umbral real pero no al hipotetico durante ese tramo.
- El refuerzo real cierra a 12:05:50.385; el hipotetico a 12:30:01.713.
  Ambos realizan +3,87 EUR, pero mantienen riesgo durante tiempos distintos.

Es sensibilidad a la ejecucion inicial propagada por la regla de entrada,
no evidencia de que una simple demora constante resuelva el problema. No se
ha cambiado la politica para encajar este caso. La prueba general pendiente
debe separar: replay condicionado a fills observados, mismas decisiones con
mismos inputs, y error esperable de fills hipoteticos bajo perfiles empiricos.

## Verificacion Y Continuacion

72 pruebas focales aprobadas: 23 de trayectoria, siete de adaptadores/CLI y
42 monetarias existentes. El primer fallo de recogida fue ausencia de la API
nueva, no un defecto demostrado del motor previo. Un test comparaba hashes
de representaciones Decimal distintas pese a valores iguales; se corrigio la
expectativa del test, manteniendo la comparacion exacta de cada muestra/pata.
Hashes de muestras describen bytes reproducibles, no normalizacion semantica.

Suite completa: **6.371 aprobados, una advertencia, 459,06 s**, Python 3.11.9.
Fuentes estables durante la ejecucion. Advertencia ya existente de pandas en
`test_study_dataset_review`, sin fallos. Evidencia:
`reports/e5-risk-comparison-20260921/pytest.xml`, SHA256
`8959ec3fcbcfcd8cece154f69f1326e026ff5b2e35fe6a7c5d0eb2a7cd921bef`.

Siguiente trabajo util, sin reiniciar auditorias ya resueltas:

1. Integrar/verificar el control de trayectoria condicionada contra anclas
   nativas y ampliar los controles historicos admisibles, manteniendo los huecos.
2. Contrastar decisiones de las dos politicas con entradas/respuestas iguales;
   representar el arbitraje compartido del runtime donde sea necesario. El
   modelo `single_basket_serial_v1` no lo cubre; no levantar su gate de portfolio.
3. Estimar perfiles de ejecucion sin elegir por senal la demora que mejor encaja.
   Separar calibracion/contraste; los archivos examinados siguen retrospectivos.
4. Completar condiciones operativas comparables a VM, retencion compatible y
   revision/autorizacion actual antes de publicar. No convertir E5 completo
   en un requisito nuevo universal para desplegar una reparacion operativa.

## Identidad

Informe SHA256: `5cda11785415bb540e7ab9045ae412163d27ea2de87c6c7b0238aeaba2eebe58`.
Fuentes/entradas completas y sus hashes estan dentro del informe; originales
sin cambios. El contraste de secuencias anterior usado para explicar 2615 es
`runtime_data/simulation_foundation_20260909/independent_v3/first_divergences.json`,
SHA256 `10b343555e8361ff293d38d155ceed00cc91836a6f9f189888da03272df98607`.

| Fuente | SHA256 |
| --- | --- |
| broker_money.py | 0a868a7c26ff32064b1beabe5ba046d7ad2a4ac3ecea3b87813a7ff183074a1f |
| research/risk_trajectory.py | 558682a5e64346e8788e4d0c622868fff7bac9918e9cba230e3ded6133df3019 |
| tools/compare_risk_trajectories.py | 82f6f7a1573e2819f0914be71aa73def124a3d255717051be4ff260de7e6ef7c |
