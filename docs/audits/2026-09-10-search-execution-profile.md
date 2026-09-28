# S1: Perfil Explicito De Busqueda Offline

Fecha: 10/09/2026. Rama `feature/gold-555-live-trial`, HEAD
`892bc33c8f6c19be7248401ec3cf6a27ba3d0563`. Cambios locales sobre el arbol
sucio historico, preservado. Sin commit, push, red, SSH, VM ni ordenes.

## Entrega

Las dos CLI aceptan `--execution-profile <execution.json>` y
`--own-rules-config <rules.json>`. No usan las semillas legacy en esa ruta.
La configuracion de reglas contiene semillas explicitas y ejes de mutacion;
se valida todo su dominio finito antes de evaluar o abrir el spool. La ruta
legacy de perfil tambien lo valida antes de cargar los datos; M7 carga primero
su bundle acotado para comprobar la igualdad de los contratos.
Existe un camino positivo comprobado, no solo un bloqueo de las semillas
anteriores. No se ha buscado ninguna estrategia sobre datos reales.

Sin esas opciones se mantiene el recorrido legacy, incluidos sus costes,
mundos y operadores. Los flags `--search-latency-ms`, `--search-entry-slippage`,
`--search-exit-slippage` y `--search-spread-addition` no se pueden combinar con
un perfil, tampoco pasandoles cero explicitamente. El JSON de ejecucion usa
`research.execution_profile.load_execution_profile`: limite de 128 KiB,
campos desconocidos, duplicados y valores no finitos rechazados.

## Configuracion Compatible

Estos son dos JSON independientes. Son supuestos diagnosticos de control,
no valores medidos/admitidos para un periodo historico ni una recomendacion
de estrategia, capital o ejecucion. El coordinador puede utilizar la semilla
en su control fijo M7 sin iniciar una busqueda.

`execution.json`:

```json
{
  "latency_ms": 125,
  "entry_fill_latency_ms": 1000,
  "entry_slippage": 0.02,
  "exit_slippage": 0.03,
  "spread_addition": 0.02,
  "protection": {
    "point": 0.01,
    "digits": 2,
    "stops_level_points": 20,
    "freeze_level_points": 0,
    "processing_delay_ms": 1000,
    "acknowledgement_delay_ms": 1000,
    "retry_delay_ms": 1000
  },
  "market": {
    "entry_acknowledgement_delay_ms": 2000,
    "close_processing_delay_ms": 1000,
    "close_acknowledgement_delay_ms": 1000,
    "volume_min": 0.01,
    "volume_max": 1.0,
    "volume_step": 0.01
  }
}
```

`rules.json`:

```json
{
  "schema_version": 1,
  "seeds": [{
    "schema_version": 2,
    "entry_mode": "signal_market",
    "entry_expiry_min": 1,
    "entry_ladder_mode": "simultaneous",
    "leg_count": 1,
    "volume_weights": [0.04],
    "target_mode": "per_leg_steps",
    "target_steps": [0.5],
    "be_mode": "none",
    "stop_mode": "fixed_move",
    "stop_value": 5.0,
    "trailing_distance": 30.0,
    "hard_stop_eur_per_leg": 20.0,
    "time_exit_min": 180,
    "time_exit_mode": "none",
    "provider_management_mode": "ignore",
    "pending_entry_policy": "until_expiry"
  }],
  "mutations": {
    "target_steps": [[0.6], [0.7]],
    "stop_value": [4.0]
  }
}
```

La semilla aporta TP 0.5 y SL 5.0; con los valores declarados hay seis
combinaciones, todas validadas. El SL y TP se expresan en distancia de precio;
los volumenes son lotes y el hard stop es dinero de cuenta. No se equiparan
esas unidades. `time_exit_mode=none` NO garantiza un cierre a los 180 minutos;
un final de datos con posiciones pendientes conserva el bloqueo del motor.

La sintaxis de integracion es `python -m research.gold_iterative search
--execution-profile <execution.json> --own-rules-config <rules.json>` o
`python -m research.dubai_iterative --execution-profile <execution.json>
--own-rules-config <rules.json>`, mas los datos, limites y presupuesto
explicitos correspondientes. Es una referencia de interfaz, no un comando
ejecutado ni una autorizacion para investigar el historico. S4 permanece cerrado.

## Dominio Y Rechazos

- Entradas hipoteticas schema 2, gestion proveedor `ignore`, sin niveles
  proveedor ni `min_reward_risk`. Se reutilizan los bloqueos de protecciones:
  stop `none|fixed_move`, target `none|per_leg_steps`, BE `none` cuando hay
  ProtectionProfile. Freeze no cero y protecciones observadas no son admitidos
  por esta ruta propia. Los numeros no aceptan booleanos ni strings coercibles.
- Entre 1 y `min(64, population_size)` semillas, todas de una misma familia
  estructural. Las diferencias entre semillas deben aparecer en ejes declarados.
  Una familia estructural diferente requiere otra configuracion explicita;
  no se mezclan automaticamente las semillas 555/c490/provider.
- Ejes permitidos: `entry_value`, `entry_confirmation_value`, `entry_expiry_min`,
  `entry_ladder_step`, `target_steps`, `stop_value`, `trailing_distance`,
  `hard_stop_eur_per_leg`, `profit_lock_arm`, `profit_lock_giveback`,
  `time_exit_min`. Cada eje necesita un parametro ya presente en la semilla,
  de 1 a 32 valores; todos sus productos deben ser validos.
- Maximo 4096 variantes declaradas. Se comprueban sin ejecutar el motor.
  Duplicados, familias incompatibles, volumen/horizonte fuera del sobre o una
  combinacion invalida abortan explicitamente el run completo antes de evaluar;
  no se eliminan semillas ni mutaciones para fingir un dominio valido.
- Semillas, vecinos, mutador y scouts usan ese dominio. El crossover compartido
  intercambia bloques dentro de la misma familia y grilla validada. Presupuesto,
  linaje, Pareto, folds y challenge siguen en los buscadores existentes.
- `--parent-parquet` no se mezcla con la configuracion propia. Dubai `--fixture
  tiny` sigue siendo una funcion artificial de puntuacion y rechaza perfiles;
  los tests positivos usan tapes sinteticas a traves de los motores reales.

## Propagacion Y Evidencia

Los mundos usan `dataclasses.replace`, nunca nuevas ejecuciones que borren
market/protection/fill latency. Las latencias alternativas conservan los
costes base con perfil explicito; los mundos de costes cambian los costes
declarados, no los ciclos de solicitudes/fills/acks. Sin perfil mantienen sus
costes legacy. En Dubai `zero_cost_zero_latency` significa cero coste adicional
y latencia de observacion cero, NO borrar la latencia de fill ni los acuses.

`execution_to_scenario` conserva objetos anidados. Gold conecta la cartera
en su certificacion con perfil; Dubai usa el mismo perfil en cross-validation,
certificacion y cartera. Su adaptador de stress pasa un baseline explicito al
oracle, porque el stresser legacy fabrica un baseline idealizado. No se han
editado los motores ni el oracle compartido. Los informes de stress muestran
el baseline real y los perfiles anidados.

Perfil normalizado y dominio normalizado quedan en el contexto/checkpoint y
run card. Gold tambien los liga a su clave de experimento y fragmentos.
Cambiar fill latency, protecciones, mercado o ejes invalida la reanudacion;
el archivo previo no se reescribe. La incorporacion de helper/CLI a provenance
es trabajo del coordinador, no una edicion de este frente.

## Verificacion Y Pendientes

Regresion inicial reproducida: 2 FAIL reales, Gold por convertir protecciones
a dict y Dubai por perder fill latency/perfiles en mundos alternativos.
Tras la reparacion, controles BUY/SELL prueban en los seis mundos los stops
durante espera de ack y cierre a mercado pendiente, oracle independiente,
cartera, resultados incompletos conservados e identidad/resume.

Los controles positivos de ambas CLI usan una sola evaluacion de una semilla
fijada sobre tres dias sinteticos; los operadores, gates, certificacion y
archivo son reales. Las seis variantes solo se validan estructuralmente y se
prueban sus operadores; no se buscan candidatas sobre historico. Gold reanuda
el mismo control y rechaza cambios de perfil y dominio sin tocar su archivo.

Comando focalizado final (Python 3.14.2):

```text
py -3.14 -m pytest -q tests/test_search_execution_profiles.py tests/test_gold_iterative_cli.py tests/test_dubai_iterative_cli.py tests/test_search_profile_review_regressions.py
```

Resultado previo a la continuacion M7, tras revision: **80 PASS en 13.73 s**,
cero fallos/errores/omisiones.
`git diff --check` en los cuatro archivos tracked del frente tambien pasa.
No se ejecuta la suite completa: la ejecutara una sola vez el coordinador
tras estabilizar los frentes y revisar S1 independientemente.

P2 independiente de Lorentz reproducido antes del arreglo: Gold con fixture,
perfil y semilla no_entry intentaba leer el contrato monetario real al construir
la cartera. Corregido dentro de la CLI: la cartera de fixture se reconstruye
con la union de sus propias paths sinteticas y la funcion real
`reconstruct_portfolio`, sin fuentes externas. El control positivo Gold ya no
sustituye la cartera; falla expresamente si se llama al cargador de fuentes
reales. La reproduccion independiente pasa sin editar su test.

Los dos JSON documentados tambien se han decodificado y comparado con el
perfil y semilla del control positivo: identicos.

Freeze local anterior de las CLI tras P2, sustituido por M7 (SHA-256):

- Gold: `f993e03d26bb598e2d0ba5853057009e57bde7dcbb472f0835b05411d073f2bc`.
- Dubai: `6211c7f882faf5e33a0a90da27ae814edcd776b7de643a554a011f0880b5ddb6`.

El enlace M7 antes pendiente se implementa en la continuacion siguiente.
`not_connected_to_m7` permanece unicamente en el recorrido de perfil sin
`--study-config`. Siguen pendientes la admision real de datos/reglas, control
real del coordinador, revision independiente, S2/S3 y revision conjunta S4
antes de busqueda masiva.
No se afirma fidelidad MT5 universal, margen/stop-out, overnight ni costes
reales verificados por pasar controles sinteticos.

Archivos de este frente: `research/gold_iterative/__main__.py`,
`research/dubai_iterative/__main__.py`, `tests/test_gold_iterative_cli.py`,
`tests/test_dubai_iterative_cli.py`, `tests/test_search_execution_profiles.py`
y esta auditoria. Todo local y sin publicar; no se ha cambiado ni comprobado
la version live de la VM.

## Continuacion M7 A S1

Las dos CLI admiten opt-in `--study-config <config.json>` junto con
`--execution-profile <execution.json>`, `--own-rules-config <rules.json>` y
`--max-hold-minutes <ceil(horizon_seconds / 60)>` obligatorio y explicito.
No se inicia una busqueda real en esta entrega; los controles automatizados
usan exclusivamente una semilla y una evaluacion sobre tapes sinteticas.

La CLI consume `research.strategy_study_dataset.load_study_dataset`, helper
escrito y registrado en provenance por el coordinador, sin modificarlo.
Antes de evaluar exige:

- Perfil tipado y normalizado exactamente igual a `bundle.config.execution`.
- Fingerprint de la primera semilla igual a `bundle.config.strategy`.
- Horizonte explicito igual al techo en minutos del horizonte M7.
- Cohorte del canal correcto: Gold `canal2`, Dubai `canal1`; no se reinterpretan
  chats historicos ni declaraciones de canal de controles causales.
- Ausencia de flags legacy de entrada explicitos, incluso si igualan su valor
  por defecto: fixture, from/to, replay/audit, money-contract, caches de ticks,
  y en Gold signal-scope/catalogo/media/raw-events; en Dubai parent-parquet
  y parent-limit. Ningun selector de M7 se sustituye por los defaults de julio.

El dataset procede exclusivamente del bundle: solo paths con datos listos, elegibles
incluidas las bloqueadas, exclusiones y mapa de dias intactos. Dubai construye
folds de dias completos con el plan cronologico existente, renombrados
`study_fold_*`; Gold conserva su plan `gold_fold_*`. No se relajan los minimos
de dias ni los filtros de cobertura, challenge futuro, dinero, oracle,
estabilidad o riesgo. La cartera utiliza las fuentes market/conversion del
bundle y su contrato monetario por path; la antiguedad FX procede de
`bundle.max_fx_age_ms` y el intervalo de `bundle.max_fx_interval_ms`.
Este segundo campo M7 es opcional y por defecto conserva el mismo limite que
la antiguedad. Un intervalo 60 s solo se usa si esta declarado explicitamente;
no aumenta la antiguedad permitida ni certifica el contrato monetario. Reutiliza
la conversion historica existente por bracket temporal con Bid/Ask previo,
sin cambiar su semantica desde esta CLI.
Gold no lee scorecards ni archivos habituales de proveedor en esta ruta.

`study_input` liga identidad, inventario completo, contrato monetario y uso
retrospectivo al contexto/checkpoint y run card. Gold tambien los incluye en
su clave de experimento, fragmentos y run_metadata. El inventario retenido
incluye TODOS los IDs, estados, motivos y diagnosticos de origen, sin cambiar
sus `engines` vacios ni mutar los diccionarios protegidos del bundle.
`verify_sources()` se ejecuta antes y despues de buscar, alrededor de construir
la cinta de cartera e inmediatamente antes de publicar el archivo local.
Un cambio de fuentes aborta la publicacion; no borra evidencia previa.

La interfaz conectada declara `connected_to_m7_diagnostic`. La seleccion
mantiene `ranking_allowed=false`, politica/fingerprint seleccionados `null`
y promocion falsa, con bloqueos explicitos de admision M7, dinero no verificado
y OOS intacto no establecido. Se conservan los resultados medidos de los gates
del motor por separado. Los totales observados son `null`, incluidos el total
conocido MT5 en Gold y los agregados estructurales vacios de Dubai; nunca se
transforma su cero interno en una afirmacion contable. En Gold los finalistas
para comprobacion se rotulan `diagnostic_finalists`, no estrategia seleccionada.

### Verificacion De La Continuacion

Regresion inicial de contrato publico: fallo real al rechazar `--study-config`
como argumento desconocido. Primer cierre: **130 PASS en 23.95 s**.
Cierre final con el bundle ampliado de Hume: **134 PASS en 26.75 s**, cero
fallos/errores/omisiones, en el mismo comando focalizado de cuatro archivos
mostrado arriba (Python 3.14.2, pytest 9.0.3). La extension M7 aporta 52 casos
de integracion/contratos y 2 pruebas aisladas del intervalo FX, ademas de los
80 casos anteriores. Sin busqueda real ni fullsuite de este frente:

- Ambos CLI: admision M7 real de export sintetico y tapes de tres dias completos
  de enero; variante con un cuarto dia bloqueado, texto no admitido y mensaje
  fuera de periodo conservados. Una unica evaluacion por control.
- Contexto, perfil, inventario, cobertura y exclusiones retenidos; ninguna
  lectura de loaders/caches/scorecards legacy. Fuente y canal no se mezclan.
- Perfil, semilla, horizonte implicito/incorrecto, perfil/reglas ausentes y
  cohorte incorrecta abortan antes de entrar al buscador.
- Rechazo de todos los selectores legacy incompatibles, incluidos defaults
  explicitos y parentescos por Parquet.
- Orden observado de verificaciones antes/despues de buscar y antes de guardar.
  Cambio de fuente al cargar, tras buscar o tras construir artefactos bloquea
  la publicacion en ambos CLI.
- Cinta canonica real del bundle, limites FX 5000/5000 ms por defecto y
  5000/60000 ms explicitamente declarados, y cartera sintetica
  reproducible de 3.00 EUR hipoteticos, sin bloqueos ni contabilidad observada.
- Gold reanuda el mismo input sin reescribir el archivo retenido; un cambio
  valido del periodo M7 rechaza el checkpoint anterior y preserva sus bytes.

`git diff --check` focalizado pasa. Freeze previo a la ampliacion del intervalo:

- Gold SHA-256: `8d8da54b0d9990cdcc8809c2e1cc94db7d25db8d74b347c76b5b014cf0ef85d6`.
- Dubai SHA-256: `47359c85d73fa47e4d81b3d170e6a6a0aad3b3eedd7ccba47221b27f8e99cbe8`.
- Tests compartidos SHA-256: `5cfaa9b42bf8faaa6edf4e9b28d267b4e5fbcdcbcfe040c8ca4028f5906c24ae`.

El cierre de 130 PASS y sus hashes se reabrio para propagar el campo
`max_fx_interval_ms`, ampliado por Hume en M7/bundle y sin ediciones de este
frente al helper ni al replay compartido. Tras la propagacion CLI, el recorrido
sin M7 conserva **80 PASS en 13.97 s** (mismo comando con `-k 'not m7'`).
La prueba aislada `test_study_portfolio_contract_keeps_fx_age_separate_from_interval`
aporta **2 PASS en 1.50 s** para 5000/5000 y 5000/60000 ms, conserva ambas
fuentes y verifica antes/despues de construir la cinta. Esta prueba de interfaz
no sustituye la comprobacion del nuevo loader M7.

Freeze final SHA-256 tras la ampliacion CLI, identico antes y despues de las
134 pruebas:

- Gold: `8d8da54b0d9990cdcc8809c2e1cc94db7d25db8d74b347c76b5b014cf0ef85d6` (sin cambio).
- Dubai: `184cdb8da123896f4f65951e52ea3fd05e9bbd3dc5d90eeddb934dee95832028`.
- Tests compartidos: `3f51ac2efe96a7c0cc0864525ffe7f33f7b843ef90f4163030a2f5c75b901860`.

Las 134 pruebas se ejecutaron en un interprete nuevo con la propiedad
`StudyDatasetBundle.max_fx_interval_ms` presente. El loader M7 normaliza la
omision al valor de antiguedad, valida el intervalo declarado y el bundle
comprueba ambos limites contra su configuracion protegida. Ambos CLI superan
las pruebas de cartera con configuracion M7 real para valor omitido y 60000
declarado; no se sustituye el loader ni la cinta por un doble de prueba.

Core M7 de Hume verificado sin cambios antes/despues de la ejecucion:

- `research/strategy_study.py`: `cc1aebff4c008372710e888650e2a1c3e281e42b08757b2477cea12363d7a4ba`.
- `research/strategy_study_dataset.py`: `95e0d8cd9b7bb872907777d55b571d096da4610e162fa342fb4e19eda6084bc2`.
- `research/causal_replay.py`: `137f229c508af21c102ec416f679727115fb482c91bb52a1363abdf008fb7cdf`.

Tambien se comprobaron invariantes los hashes de los cuatro archivos de
pruebas focalizados. Este cierre solo modifica la auditoria; no ha requerido
cambios adicionales de implementacion, pruebas, helper ni fuentes reales.
La integracion CLI queda estable para la revision independiente de Lorentz
y la fullsuite unica coordinada por el parent; este frente no afirma que esas
dos verificaciones externas hayan concluido.

El loader Gold legacy no se modifica: su perfil sin study sigue declarado
`not_connected_to_m7`, sin inferir admision de snapshots o anclas temporales.
Esta continuacion invalida el control M7 real v1 por cambio de identidad de
implementacion: el coordinador repetira exactamente la misma hipotesis/config
en su archivo final, sin ajuste ni seleccion. Si cambia otra implementacion
ligada antes del cierre conjunto, los controles fijos se reejecutan sin alterar
su hipotesis. No se afirma aqui que esos reruns ya esten realizados.
El limite del bundle (40 senales / 2 millones de quotes) no representa una
admision anual ni autoriza una busqueda historica masiva. Todo permanece local,
sin commit, push, despliegue, reinicio ni verificacion de la VM.
