# M7: Runner Local De Reglas Propias

Trabajo exclusivo offline sobre `feature/gold-555-live-trial`, HEAD inicial
`892bc33c8f6c19be7248401ec3cf6a27ba3d0563`. Cambios previos preservados.
No modifica engines, contratos compartidos, export adapter, buscadores,
plan/inventario global ni politica live. Sin commit, push, red, SSH/VM,
ordenes, emails ni nuevas tareas.

## Interfaz Entregada

- `research/strategy_study.py`: `load_config(path)`,
  `run_study(config_path, output_dir)`, `verify_study(output_dir)` y
  `current_identity()`.
- `tools/run_strategy_study.py`: `run --config JSON --output-dir DIR` y
  `verify --output-dir DIR`.
- `tests/test_strategy_study.py`: contratos publicos con fuentes sinteticas.
- Este informe y evidencias bajo
  `runtime_data/calculator_delivery_20260910/runner_tests/`.

Una configuracion contiene UN genoma fijo, UN perfil y UNA cohorte.
No hay optimizacion, ranking, busqueda, admision automatica ni seleccion.
La salida siempre dice `hypothetical_only_diagnostic`, mantiene
`search_candidates=0`, ambos gates monetarios `false`,
`selection.selected_policy=null` y `promotion_eligible=false`.

## Dos Entradas Separadas

`input_kind="telegram_export"` exige `admission={path, sha256}`; SHA corresponde
a `manifest.json`. Reutiliza `load_admission` y `to_causal_signals`.
El parser y adapter del archivo deben coincidir con los bytes actuales.
Escenario obligatorio: `receipt`, `publication_initial` o `revision_time`.
Cohorte exacta `{chat_id:1642806869,name:"dubai_historical"}` o
`{chat_id:3828356530,name:"gold_historical"}`. No fusiona Gold actual.
Los relojes hipoteticos siguen etiquetados; `receipt` no inventa recepciones.

`input_kind="causal_control"` exige `source_study={path, sha256}`; SHA corresponde
a `protocol.json`, de contrato `raw_message_control_diagnostic_v2`.
Escenario obligatorio: `observed_raw_receipt`. Cohorte exacta:

- Gold actual: `{chat_id:3908582492,name:"gold_current_control",channel:"canal2"}`.
- Dubai control: `{chat_id:1642806869,name:"dubai_control",channel:"canal1"}`.

Este segundo recorrido permite reutilizar los INPUTS de
`runtime_data/simulation_foundation_20260909/independent_v3`, no sus resultados.
Verifica SHA de protocolo/raw, recompila con `compile_signals` y exige igualdad
de TODOS los `expected_signal_ids`, incluido orden, antes de seleccionar canal.
Exige coincidencia de fuentes Parquet, reloj y unidades monetarias del protocolo.
Ignora sus genomas/escenarios legacy: ejecuta solamente los de la nueva config.
La implementacion historica del protocolo no se presenta como actual; el nuevo
archivo identifica la implementacion actual y conserva los blockers historicos.

Los raw no contienen chat numerico. El mapping anterior se identifica como
`declared_channel_mapping_not_raw_chat_evidence`. Las otras filas de canal
tienen `chat_id=null`: no se inventa su chat. El ID de motor es
`causal_control:<protocol_SHA>:<source_signal_id>`; se conserva ademas el
`source_signal_id` original. No puede colisionar con `telegram_export:<chat>:<id>`.
Se conserva cada identidad raw, sus indices de ocurrencia, revisiones y todos
los diagnosticos del compilador. Gestion proveedor compilada se descarta mediante
`provider_events=()` ANTES de `make_path`. No lee deals ni estado MT5.

## Configuracion Comun Exacta

JSON estricto, maximo 128 KiB: rechaza claves duplicadas, campos desconocidos,
constantes no finitas y opciones de busqueda/OOS. Paths relativos se resuelven
contra el directorio de la configuracion, no contra el directorio de trabajo.
No usa URLs, rutas UNC ni descubrimiento de fuentes. Campos requeridos:

```json
{
  "schema_version": "own_rule_study_v1",
  "input_kind": "causal_control",
  "data_use": "retrospective_development_only",
  "search_candidates": 0,
  "source_study": {"path": "<directorio-control>", "sha256": "<SHA256-protocol.json>"},
  "cohort": {"chat_id": 3908582492, "name": "gold_current_control", "channel": "canal2"},
  "scenario": "observed_raw_receipt",
  "period": {"start_utc": "2026-09-08T06:15:39.315000Z", "end_exclusive_utc": "2026-09-08T19:19:52.945592Z"},
  "horizon_seconds": 120,
  "max_market_gap_ms": 5000,
  "max_fx_age_ms": 5000,
  "broker_clock": {"utc_offset_seconds": 10800, "status": "declared_hypothesis"},
  "sources": {
    "market": [{"path": "<XAUUSD.parquet>", "sha256": "<SHA256>", "symbol": "XAUUSD"}],
    "conversion": [{"path": "<EURUSD.parquet>", "sha256": "<SHA256>", "symbol": "EURUSD"}]
  },
  "strategy": {
    "schema_version": 2, "entry_mode": "signal_market", "entry_expiry_min": 1,
    "leg_count": 1, "volume_weights": [0.01],
    "target_mode": "per_leg_steps", "target_steps": [1.0],
    "stop_mode": "fixed_move", "stop_value": 5.0, "be_mode": "none",
    "time_exit_min": 1, "provider_management_mode": "ignore"
  },
  "execution": {
    "entry_slippage": 0, "exit_slippage": 0, "spread_addition": 0,
    "latency_ms": 0, "entry_fill_latency_ms": 250,
    "protection": {
      "point": 0.01, "digits": 2, "stops_level_points": 0, "freeze_level_points": 0,
      "processing_delay_ms": 250, "acknowledgement_delay_ms": 250, "retry_delay_ms": 1000
    },
    "market": {
      "entry_acknowledgement_delay_ms": 250, "close_processing_delay_ms": 250,
      "close_acknowledgement_delay_ms": 250, "volume_min": 0.01,
      "volume_max": 1.0, "volume_step": 0.01
    }
  },
  "money": {
    "mode": "hypothetical_intraday_zero_cost", "account_currency": "EUR",
    "profit_currency": "USD", "currency_digits": 2, "contract_size": 100,
    "conversion_symbol": "EURUSD", "conversion_orientation": "account_base_profit_quote",
    "commission_per_lot": 0, "swap_per_lot": 0,
    "rollover_hour_server": 0, "capital_eur": null
  },
  "budget": {"max_signals": 40, "max_evaluations": 120, "max_wall_seconds": 600, "max_quotes": 2000000}
}
```

Es plantilla de sintaxis, NO perfil broker certificado ni config de un estudio
real ya ejecutado. El coordinador aporta hashes, reglas y perfil concretos.
Para exportaciones sustituir `input_kind`, `source_study` por `admission`,
`cohort`, `scenario` y periodo; conservar los demas campos.

`execution` se carga SOLO mediante `execution_from_mapping`; el oracle recibe
`execution_to_scenario`, preservando los objetos nested. No hay decoder alterno.
`StrategyGenome.from_dict` y `validation_errors` son la gramatica existente.
Se rechazan dependencias proveedor, `actual_mt5`, `min_reward_risk`, schema1 e
inyeccion `initial_protections`. Otras capacidades no soportadas, como
`partial_runner` integrado, quedan como blockers de los tres motores, sin
redireccionarlas a legacy.

## Datos, Horizonte Y Presupuesto

Periodo de disparadores semiabierto, explicitamente UTC. Debe estar contenido
en el periodo del archivo de entrada. Horizonte independiente por senal,
desde su reloj causal, de 1 a 14400 segundos. No se recorta silenciosamente al
ultimo tick ni se extiende con precios inventados. Los anchors de caducidad
del compilador/motor original se conservan.

Parquet: `time_utc` con dtype timestamp UTC, `source_time_msc` entero,
`bid`, `ask`. Comprueba `source_time_msc - offset = UTC` mediante el contrato de
reloj existente. Todas las filas deben ser finitas, positivas, ordenadas y
Ask>=Bid. Un shard puede conservar timestamps repetidos; shards distintos no
pueden solaparse. Rechaza ambiguedad, no deduplica ni rellena precios.
Los bytes decodificados se cotejan con el SHA configurado.

El archivo debe llegar hasta el fin del horizonte. El retraso de primera
cotizacion, los huecos internos y la distancia al final se limitan con
`max_market_gap_ms`; FX usa exclusivamente as-of causal y `max_fx_age_ms`.
Ambos limites son explicitamente 1..60000 ms, no una calibracion automatica.
Hueco, FX ausente/obsoleto o cruce del rollover broker producen `data_blocked`,
incluso si una hipotetica salida temprana habria evitado ese hueco. Es una
exigencia conservadora del horizonte completo, no prueba de cinta broker total.

No se impone capital. `capital_eur` admite `null` o referencia positiva; no
calcula retornos porcentuales, margen, stop-out ni viabilidad de capital.
El contrato actual soporta SOLO EUR 2 digitos, profit USD, conversion EURUSD
account-base y comision/swap cero declarados como hipotesis intradia. Los campos
`pnl_eur` y `net_eur` siguen siendo resultados hipoteticos, nunca dinero observado.

Techos inclusivos: 40 senales, 120 evaluaciones, 600 s, 2M filas de cotizaciones.
Cada presupuesto puede reducirse. El conteo Parquet de TODAS las fuentes se
comprueba antes de decodificar filas. Adicionalmente, la suma de cotizaciones
de paths previstos no puede exceder `max_quotes`. No hay truncado a primeras N:
si cohorte/evaluaciones/paths exceden presupuesto, todas las senales previstas
quedan `budget_blocked` y se conserva el denominador. La CLI impone timeout de
proceso, incluido codigo nativo; la API directa comprueba el reloj entre
operaciones. Un timeout no publica un manifiesto completo nuevo.

## Informes E Inmutabilidad

`rows` conserva TODOS los IDs del archivo, no solo los ejecutados:
`outside_universe`, `admission_blocked`, `budget_blocked`, `data_blocked`,
`engine_blocked`, `engine_disagreement`, `unfilled` o `simulated`.
Cada evaluacion conserva resultados, solicitudes, fills/acks hipoteticos,
SL/TP/eventos, salidas y blockers completos de escalar, rapido y oracle.
Se comparan todos sus campos salvo `behavior_digest`, contrato diagnostico
existente. Posiciones abiertas al final no se convierten en cierres ficticios.

La cartera usa `build_portfolio_tape` + `reconstruct_portfolio` sobre las fuentes
canonica completas, no una suma de curvas. No agrega subconjuntos si alguna
senal textual seleccionada queda sin evaluar, bloqueada o discrepante. La cartera
se refiere al subconjunto de disparadores textuales seleccionado, NO a mensajes
no reconocidos ni a todo el universo historico; estos siguen en el denominador.
No certifica capital/margen ni aplica gestion global no modelada a los fills.

`protocol.json` se publica ANTES de evaluar. Vincula configuracion, hashes de
todos los inputs retenidos, implementacion actual (incluida provenance comun),
runtime, identidad fuente y limites. `results.json` referencia su hash;
`manifest.json` se publica ultimo. Los archivos completos son byte-idempotentes.
Mismo directorio con otros bytes, archivos extra, fuentes modificadas o codigo/
runtime cambiado falla cerrado; archivos historicos nunca se reescriben.
Un protocolo identico incompleto puede reintentarse; bytes parciales/conflictivos
requieren otro destino. No hay resume granular de evaluaciones ya realizadas.
`verify` verifica tambien fuentes actuales y runtime/codigo actuales, no solo
integridad de bytes. Tras editar codigo vinculado se exige un interprete nuevo.
Hashes no sustituyen retencion: borrar/mover los inputs impide verificar el run.

## Verificacion Y Handoff

TDD inicial: tras corregir dos fallos de montaje de pruebas, 19 fallos de contrato
nuevo por `NotImplementedError`, 1 prueba de aislamiento aprobada. Despues 20/20.
Extension causal: prueba positiva falla por input no soportado antes del cambio;
tras conectar, 31/31 (incluida CLI). Cierre final: **53/53 PASS**, Python 3.14.2,
14.61 s de pytest; XML inspeccionado: 53 tests, 0 failures, 0 errors, 0 skipped,
14.598 s. SHA256 `final.xml`:
`1adebded7b4cc28e0810562cd2042060123bb5d8885b75568d5e83699b8f7d41`.

SHA256 finales:

- Runner: `064cfb9012c6ea2834a9687c84ad39693882a558b412c0b2f8e0f82fda119ba4`.
- CLI: `923b53935e875a20ce83d4babc9241e834a2f42f4a2d737e2af86c6a52a5bb1b`.
- Tests: `98061f3ec85d419f22c02a89f2f3e043ffca5595f13258c943fa9e3eb2536ef9`.
- Identidad iterative comun: `3934e7b57799df7df614113ee2f67956fe8cc3d9a20c2aa7ec2d3cf76d16c307`.

El manifiesto `runner_tests/verification.json` conserva el mapa completo de
implementacion/runtime y evidencia. Pruebas de resultado conocido BUY/SELL,
SL/TP proveedor ignorados, ambas entradas, raw/hash/IDs/ticks incompatibles,
parcial de escalera con rechazo de pata, politica parcial no soportada,
posicion abierta, no-entry, cartera solapada, agujeros/FX/overnight,
dinero no soportado, presupuestos, UTC/JSON estrictos, inyeccion de estado,
mutacion durante engine, archivo inmutable, identidad actual e importacion offline.
Todas las fuentes de prueba son sinteticas; ninguna prueba consulta el historico
real, MT5, red o reserva OOS. Revision propia directa; no revision independiente.

```powershell
py -3.14 -m pytest -q tests/test_strategy_study.py `
  --basetemp runtime_data/calculator_delivery_20260910/runner_tests/final_tmp `
  --junitxml runtime_data/calculator_delivery_20260910/runner_tests/final.xml

py -3.14 tools/run_strategy_study.py run --config <JSON> --output-dir <DIR-NUEVO>
py -3.14 tools/run_strategy_study.py verify --output-dir <DIR-NUEVO>
```

CLI retorna 0 para informe diagnostico terminado (puede contener blockers),
2 para integridad/configuracion/deadline fallidos. Revisar `status_counts`,
`rows`, `mismatches` y `portfolio.blockers`, no interpretar exit 0 como admision.

Quedan a coordinacion: configuracion/control real fijo Sep8, admision de precios
por cohorte, revision independiente e integracion unica. No se ejecuta aqui un
estudio anual ni las 33 evaluaciones reales. Las cuatro exportaciones terminan
en julio y no se admiten con ticks posteriores sin solapamiento; la alternativa
de control actual no resuelve esa falta ni fusiona sus chats. No se declara
cerrada la entrega integral, el realismo broker ni autorizada la busqueda masiva.
