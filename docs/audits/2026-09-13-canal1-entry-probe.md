# Canal 1: Control Mensual Del Puente Al Motor

## Resultado

Control local terminado sobre `entry_stream_v3`: 61 casos hipoteticos en nueve
dias completos, repartidos entre enero y septiembre. Se simulan 60 casos con
coincidencia scalar/fast/oracle; uno permanece bloqueado por cobertura.
180 evaluaciones, cero discrepancias, cero busquedas y ninguna seleccion.
Es una comprobacion de integracion, no una estimacion de rentabilidad anual.

El [flujo anual de entradas](2026-09-13-canal1-entry-stream.md) mantiene sus
881 activaciones desde primera version conocida y 240 desde versiones sin
editar. Este control no sustituye ese universo ni cambia sus agrupaciones.

## Muestra Congelada

Regla: primer dia UTC completo con activaciones `revision_time` de cada mes,
incluyendo todas las activaciones de ambos escenarios en esos mismos dias.
Seleccion por mensajes y calendario, antes de decodificar precios o ejecutar
motores. No se sustituyen dias ni casos por su cobertura o resultado.

| Dia UTC | Version conocida | Simulados | Bloqueados | Sin editar, simulados/total |
| --- | ---: | ---: | ---: | ---: |
| 2026-01-02 | 1 | 1 | 0 | 1/1 |
| 2026-02-02 | 8 | 8 | 0 | 0/0 |
| 2026-03-02 | 9 | 9 | 0 | 6/6 |
| 2026-04-01 | 8 | 8 | 0 | 5/5 |
| 2026-05-01 | 4 | 4 | 0 | 0/0 |
| 2026-06-01 | 3 | 2 | 1 | 0/0 |
| 2026-07-01 | 4 | 4 | 0 | 3/3 |
| 2026-08-03 | 5 | 5 | 0 | 1/1 |
| 2026-09-01 | 3 | 3 | 0 | 0/0 |
| Total | 45 | 44 | 1 | 16/16 |

Los 61 son casos de dos escenarios, no 61 senales originales independientes.
El contraste sin editar no es una comparacion de operaciones emparejadas;
sus meses vacios no se rellenan. 29 de las 45 activaciones principales usan
una version editada; el mayor retraso publicacion/activacion de esta muestra
es 6652 segundos. El control no cubre las 24 ediciones de mas de un dia del
universo completo y no prueba recepciones originales.

La distribucion mensual sirve para ejercitar distintas fechas del sistema,
no garantiza una muestra estadisticamente representativa. Para el estudio
posterior sigue vigente el esquema movil 56/14 dias, avance de 14 y purga de
cuatro horas, no una particion rigida enero frente a actualidad.

## Reglas Fijas

Se copian sin ajustar la estrategia, ejecucion y dinero del control anterior
`small_search_readiness_20260912/short_inputs_v1/2026-07-27/canal1_config.json`,
SHA256 `e509b4bf3299c16f47129de0463f92ce6fc51edf6b3e3e26ad16fe67a13e383f`.

- Una posicion de referencia de 0.01 lotes por caso, entrada a mercado;
  caducidad de entrada de tres minutos. No es capital asignado por el usuario.
- Stop de 10 y objetivo de 5 unidades de precio XAUUSD, no pips ni euros.
  Salida por tiempo a los 15 minutos y horizonte completo de 20 minutos.
- Sin BE, parciales ni gestion, SL, TP o resultados del proveedor.
- Cada caso reinicia el estado de posiciones. No comparte cuenta con los
  demas; no se suman beneficios ni se presenta cartera, margen o capital suficiente.
- Bid/Ask historico y spread real del archivo. Latencia de fill declarada
  250 ms; solicitudes/respuestas del perfil integrado procesadas en el reloj
  de cotizaciones. No se promete ejecucion exactamente a los 250 ms.
- Volumen minimo/paso 0.01, maximo 1.0; punto 0.01, dos decimales, stops 20
  puntos y freeze 0. Son parametros declarados del control, no metadata anual certificada.
- EUR, contrato declarado 100, conversion EURUSD previa con el criterio
  historico de intervalo ya congelado. Comision, swap y deslizamiento adicional
  cero son hipotesis optimistas; no equivalen a costes reales nulos.
- Se conservan los limites de cobertura: hueco XAUUSD 5000 ms, edad FX
  5000 ms e intervalo historico FX 60000 ms. El siguiente precio FX no se usa.
- Horario del broker declarado +2/+3: 25 casos de invierno y 36 de verano.
  No se convierte todo el ano con el desfase de verano.

Presupuesto previo: 64 casos, 192 evaluaciones, 1000000 cotizaciones de
trayectorias, 20000000 filas fuente decodificadas y 600 segundos. Se mantiene
el denominador completo si se excede; no se trunca. El proceso tiene timeout.
Consumo: 813074 cotizaciones en horizontes (pueden repetirse entre casos),
7667530 filas fuente decodificadas y 180 evaluaciones.

## Incidencia Conservada

`dubai_stream:revision_time:20030`, SELL del 01/06, publicado 17:59:57 UTC,
activacion hipotetica en la edicion 18:00:38 UTC; fin 18:20:38 UTC.
3394 cotizaciones XAUUSD en el horizonte. Tres pausas exceden 5000 ms,
maximo 11050 ms. La primera va de 18:06:14.157 a 18:06:19.165 UTC (5008 ms).

Motivo `market_gap_exceeds_contract`: cero evaluaciones de este caso,
conservado entre los 45 principales. Conversion valida bajo el contrato y
ningun archivo diario ausente. Esto no demuestra que el broker haya perdido
ticks: demuestra incumplimiento del limite de cobertura elegido. No se
amplia el limite ni se interpreta el bloqueo como una operacion perdida.

## Verificacion

Herramienta nueva y aislada: `research/dubai_entry_probe.py` y
`tools/run_dubai_entry_probe.py`. Reutiliza el puente causal, normalizacion
raw, cobertura, conversion, perfiles y tres motores existentes, sin editar
sus implementaciones ni el runner de estudios anterior.

21 pruebas nuevas: seleccion estable frente a resultados, dias completos,
denominadores, presupuestos, integracion raw/UTC/tres motores, bloqueo antes
de simular, congelacion antes de decodificar y alteracion de fuentes/resultados.
Revision ampliada: 470 pruebas pasan en 52.64 segundos con Python 3.14.2.

```powershell
py -3.14 -m pytest -q tests/test_dubai_entry_probe.py tests/test_dubai_entry_stream.py tests/test_dubai_annual_coverage.py tests/test_dubai_annual_universe.py tests/test_dubai_export_catalog.py tests/test_telegram_exports.py tests/test_strategy_study.py tests/test_study_fx_interval_contract.py tests/test_causal_replay.py tests/test_execution_profile.py tests/test_own_rule_profile_integration.py tests/test_market_parity.py tests/test_protection_parity.py
py -3.14 tools/run_dubai_entry_probe.py run --stream runtime_data/canal1_history_20260913/entry_stream_v3 --coverage runtime_data/canal1_history_20260913/coverage_v1 --raw-audit runtime_data/canal1_history_20260913/raw_mt5_v2_audit.json --output runtime_data/canal1_history_20260913/entry_probe_v1
py -3.14 tools/run_dubai_entry_probe.py verify --output runtime_data/canal1_history_20260913/entry_probe_v1
```

Archivo verificado contra codigo/entorno vigentes, 1050 fuentes y los hashes
de protocolo/resultados; identidad
`35d1acdd171da152e897720ad4c8e758b536cacdf609f4dcae25841a9ab5797d`.
Protocolo SHA256 `870b6acff52544c4449c5a3e1b084db20782966edf26c69e32a07691f8923289`;
resultados `b49be715e825d47ab3e124d8a01f0dea6555df3bed8868ea9f738fe74a5e6c53`.

Ademas, las 180 salidas de motores se revisan: una entrada y cierre por caso,
0.01 lotes, entrada nunca anterior al trigger, cierre dentro de los 20 minutos,
sin blockers y confianza `counterfactual_entry`. Ramas ejercitadas: 32 objetivos,
15 stops y 13 salidas por tiempo; no es un ranking ni una tasa de acierto anual.
Maxima permanencia 901.105 segundos, incluyendo la ejecucion de la salida.

## Siguiente Paso

El puente JSON/version disponible -> precios -> gestion propia -> resultado
coincidente entre motores queda comprobado para este control. No sigue
pendiente reconstruir desde cero el catalogo o la agrupacion.

Sigue pendiente extender el contrato del dataset anual a las ventanas moviles
y declarar la politica de exposicion compartida, costes/sensibilidades y
escenarios de capital antes de evaluar carteras o comparar estrategias.
Las hipotesis de recepcion, edicion y horario mantienen sus limites. Los gates
de dinero, admision y seleccion continuan falsos; beneficio agregado no calculado,
cartera no calculada y `selected_policy=null`.

Todo local y sin publicar. Sin MT5, ordenes, acceso a VM, reinicio del bot,
commit o push. Permanece pendiente, sin reintento, la limpieza temporal de
autenticacion bloqueada durante la extraccion anterior.
