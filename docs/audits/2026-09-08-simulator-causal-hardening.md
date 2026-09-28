# Robustez Causal Del Simulador

Nota de continuidad: `2026-09-08-simulator-exit-sequence.md` documenta la fase
siguiente y sustituye la aplicabilidad del certificado v3 por v4. Las 3.256
pruebas y hashes de esta pagina corresponden a este cierre historico, no
certifican por si solos los cambios posteriores. No se borra esta evidencia.

## Alcance Y Estado

Segunda fase local de robustez, continuacion de `2026-09-08-simulator-trust.md`.
Rama `feature/gold-555-live-trial`, HEAD `08551e9a86aa716473e43b978c13a65f21003b7a`.
Se preservan los cambios anteriores y los estudios originales. Sin commit,
push, despliegue, reinicio, acceso MT5 ni ordenes. No cambia la politica live,
los lotes, los costes declarados ni el capital, que sigue abierto.

La fase corrige defectos demostrados y barreras de reutilizacion. No busca
candidatas ni certifica paridad integral con MT5. El plan y su presupuesto
estan en `../development/2026-09-08-simulator-causal-hardening.md`.

## Defectos Corregidos

### Frontera Temporal

Los motores comunes convertian UTC a nanosegundos mediante segundos flotantes.
Una instruccion disponible exactamente a los 2 ms podia desplazarse 128 ns y
perder su tick elegible. El defecto afectaba al escalar, al rapido y al oracle:
su acuerdo mutuo no lo detectaba. En la regresion sintetica de cierre, tomar
el tick siguiente cambiaba -0,40 EUR a -4,80 EUR.

Ahora el motor calcula UTC con aritmetica entera y el oracle lo hace mediante
una conversion independiente basada en dias/calendario. La conversion inversa
tambien evita segundos flotantes. Se conserva el primer tick >= disponibilidad
exacta + latencia declarada; no se cambian politica, precios ni tolerancias.

Las 60 regresiones cubren entrada/cierre, BUY/SELL, instantes anteriores,
iguales y posteriores, latencia, zonas horarias equivalentes y fin de datos
con posiciones abiertas. Incluyen cestas sinteticas de cinco patas, trailing,
stop y gap: -480/-496 EUR a su tamano de prueba. Son controles aritmeticos,
no perdidas observadas ni estimaciones de frecuencia o de capital apropiado.

### Hechos Del Broker Por Ticket

La revision independiente encontro que v2 todavia podia confiar en los
importes por pata del path, aunque el ledger del broker discrepara por ticket
y mantuviera el mismo total. Ahora `ledger_evidence.py` normaliza y reconcilia
posiciones/deals; la identidad es `position_id`, distinta del ticket del deal
de apertura. `live_parity.py` vincula esos hechos con el path y el resultado.

El contrato schema 3 / `ledger_bound_ticket_money_v3` exige identidad, hora
con offset explicito y milisegundos disponibles, precio, volumen, direccion,
simbolo, cierre completo y costes de cada deal. Valida cierres parciales,
componentes y total por posicion/cesta. Datos ausentes, duplicados, aperturas
ambiguas y contradicciones quedan visibles y bloqueados, no se eliminan.
No agrega errores opuestos de tickets distintos para fabricar una coincidencia.

### Informe De Conjunto

`pipeline_truth.py` vuelve a comprobar cobertura, dinero, volumen, numero de
entradas/salidas y hechos declarados de entrada contra el ledger. Una etiqueta
de version o banderas de exito no sustituyen las filas por ticket. Tambien
rechaza contadores fraccionarios/no finitos e importes fuera de representacion.
Las pruebas de integracion detectaron ocho variantes adicionales de hechos
de entrada ausentes/contradictorios; fallaron antes de aplicar esa comprobacion.

Los certificados v1/v2 no habilitan la barrera v3. La secuencia observada de
decisiones y deals de salida sigue sin un contrato de comparacion completo:
`exit_decision_and_deal_sequence_not_verified` mantiene cerrada la extension
integral. Sumar costes o cierres parciales no verifica por si solo esa secuencia.

### Reanudacion E Historia

Los checkpoints compartidos pasan a schema 4. Su identidad liga codigo,
dependencias locales, entorno, operadores, poblacion y configuracion efectiva
de `FastEvaluator`, sin fiarse de declaraciones antiguas del caller. La
revision detecto y reprodujo la aceptacion indebida de cambios de latencia,
slippage o spread y una dependencia transitiva ausente. Se corrigen ambos.

La lista explicita cubre 36 fuentes, con una regresion de cierre de imports
locales; incluye el validador transitivo `mt5_tick_cache.py` sin ejecutarlo.
Los folds se comprueban antes de sobrescribir un checkpoint previo. Gold liga
tambien los fragmentos Parquet a la implementacion/experimento y no migra
automaticamente historiales antiguos. El control detecta cambios durante la
busqueda antes de aceptar nuevos checkpoints o publicar un archivo local.

No se infiere automaticamente el estado de evaluadores arbitrarios o wrappers
personalizados; el contrato de configuracion automatica cubre el evaluador
soportado. Extensiones futuras deben declarar y probar su identidad de estado.
`gold_iterative verify` comprueba bytes archivados, no revalida resultados
historicos con el motor actual. Recalcular solo beneficios tampoco elimina el
sesgo de una poblacion o conjunto de candidatas ya vistas bajo codigo anterior.

## Piloto Retrospectivo

Se repitieron una vez los 24 escenarios congelados y ocho controles semanticos
del piloto del 07/09, corte 15:02:33 UTC. Protocolo hijo nuevo, mismos inputs,
genomas y latencias 0/250/1000 ms, sin buscar estrategias. No se sobrescribe el
protocolo padre ni se reutiliza su identidad para el codigo reparado.

- Ninguno de los 24 resultados cambia por esta reparacion temporal.
- Escalar y oracle coinciden en los 24 escenarios; el rapido coincide en los
  32 controles, comparando los campos publicos completos del resultado oracle.
- Las ocho senales, 20 aperturas/cierres y contabilidad observada conservan
  sus resultados. No se convierten en nuevas observaciones ni en OOS.
- Persisten la conversion antigua, las diferencias semanticas y de fills,
  y el corte parcial del dia documentados en el piloto original. No hay un
  total hipotetico certificado para todo el piloto ni paridad integral.

Que este piloto no cambie no demuestra que ninguna candidata historica pueda
cambiar. Las comparaciones afectadas necesitan revalidacion identificada;
los resultados antiguos conservan su historia, sin certificacion retroactiva.

## Verificacion

Directorio de evidencia: `runtime_data/simulator_hardening_20260908/`.

- `pytest_timestamp_red.xml`: 12 fallos de frontera temporal reproducidos.
- `pytest_report_red.xml` y `pytest_report_entry_facts_red.xml`: falsas
  aceptaciones reproducidas antes de sus correcciones.
- `ledger_binding/pytest.xml`: 69 controles del certificador/ledger aprobados.
- `provenance/review_red_0419.xml`: 10 fallos esperados; el verde focal final
  `provenance/review_focused_0419.xml` acredita 95 pruebas aprobadas.
- `pytest_integration_final.xml`: 160 pruebas aprobadas, incluyendo los tres
  motores y la integracion de ambos consumidores del ledger.
- `pilot_repair/repair_comparison.json`: protocolo hijo, hashes originales,
  24 resultados sin cambios y 32 controles sin discrepancias del motor rapido.
- `pytest_full_final.xml`: **3.256 pruebas aprobadas**, cero fallos, errores
  u omisiones; 633 avisos, 175,04 s. Python 3.14.2 local, incluida ejecucion
  compilada. No prueba servicios externos reales ni el entorno de la VM.
- `verification_start.json` / `verification_final.json`: 354 fuentes de codigo,
  pruebas y configuracion registradas sin cambios durante la suite; identidad
  de implementacion/entorno estable y hashes de fuentes/resultados del piloto
  comprobados de nuevo. Estado `local_hardening_verified`, con paridad integral,
  evidencia OOS nueva y publicacion explicitamente falsas.

Comando de la suite: `python -m pytest -q
--junitxml=runtime_data/simulator_hardening_20260908/pytest_full_final.xml`.
La ejecucion y la comprobacion de estabilidad quedan ligadas por el wrapper
inmutable `runtime_data/simulator_hardening_20260908/verify_final.py`.
Identidad de implementacion al cierre:
`be66ac9f54d0c70f1e119b09e7ca9cbf155d66ebbbfd841f3a5f06ab44b616d6`.

Los XML rojos y los intentos intermedios se conservan como reproducciones,
no son el veredicto final. La evidencia nueva reemplaza la aplicabilidad de la
suite previa de 3.081 pruebas al codigo actual, no borra su resultado historico.

## Limites Y Siguiente Evidencia

Faltan la comparacion completa de decisiones/deals de salida, jornadas completas
con fuentes monetarias/temporales admisibles y validacion futura no utilizada
para reparar o ajustar. El acuerdo entre motores, las regresiones sinteticas
y la repeticion del piloto son capas diferentes; ninguna garantiza fills
alternativos exactos, rentabilidad ni ausencia de todos los defectos.
