# Candidata Local E3-C

Implementacion Sol/alto del 20/09/2026 sobre la rama
`feature/gold-555-live-trial`, con HEAD base
`7415941a410d18288fa4e08da76809f70b125efa` y todos los cambios locales
preexistentes preservados. No hubo commit, push, despliegue, reinicio de VM ni
conexion a una cuenta real.

## Resultado

E3-C queda como candidata local para revision independiente. La integracion
instala un unico propietario aislado de MT5 durante el arranque, enruta por el
las lecturas y efectos live restantes, conserva resultados tardios de forma
durable y mantiene el heartbeat libre de esperas a MT5.

No se marca E3-C como aceptada. Faltan la lectura independiente con Astra/alto
y los ensayos operativos E4 en entorno comparable a la VM. Tampoco se acredita
todavia paridad realidad-simulacion E5.

## Fronteras Cerradas Localmente

- `main.py` crea, inicia e instala `MT5Runtime`, comparte su servicio durable
  con listener, acciones pendientes y monitor, y lo cierra explicitamente.
- Los modulos live ya no conservan acceso nativo a MetaTrader5. El proxy
  compatible solo traduce lecturas al trabajador aislado; los efectos de
  broker exigen el servicio durable.
- Las aperturas demoradas del monitor, incluidas DCA y legs candidatas, usan
  identidad estable, reserva, revision, expiracion y resultado durable. Un
  resultado ambiguo no provoca reenvio legacy.
- Las lecturas de finalizacion, PnL, contexto y acciones pendientes salen del
  bucle principal. Las llamadas nativas lentas no bloquean Telegram, heartbeat
  ni supervision.
- El transporte prioriza gestion/escrituras sobre lecturas en espera y concede
  turno a una lectura tras un maximo de cuatro escrituras consecutivas. Una
  llamada nativa ya activa no se interrumpe.
- El heartbeat publica identidad del propietario, sesion, proceso, colas y
  frescura de snapshots. Un snapshot ausente, fallido o vencido es `unknown`,
  nunca cuenta plana. El watcher acepta durante la transicion el heartbeat v3
  anterior, pero valida el bloque nuevo cuando existe.
- Las recuperaciones historicas de sombras se dividen en ventanas acotadas y
  no solapadas. Se conservan ticks repetidos reales, incluida la evidencia de
  cursores ambiguos.
- Las dos pruebas temporales heredadas de E3-B ahora esperan evidencia de que
  `order_send` ha comenzado y despues interrumpen al llamador. Ya no dependen de
  que la preparacion termine por casualidad antes de 50 ms.

## Evidencia

Frontera durable de E3-B mas los controles sincronizados:

```text
python -m pytest -q --tb=short tests/test_e3_b_acceptance.py tests/test_e3_b_repair_review.py tests/test_e3_b_review_regressions.py tests/test_execution_intents.py tests/test_durable_execution.py tests/test_durable_entry_execution.py tests/test_mt5_trade_client.py tests/test_mt5_trade_worker.py tests/test_mt5_gateway.py tests/test_pending_actions.py tests/test_canal2_entry_intent.py
192 passed, 88 warnings, 25.71 s
```

Rutas de lectura, arranque, heartbeat, watcher, sombras, monitor y listeners:

```text
374 passed, 98 warnings, 14.49 s
```

Arranque E3-C, runtime y protocolo:

```text
29 passed, 2.21 s
```

Suite global final, repetida desde cero despues de corregir los dobles de prueba
que aun parcheaban el modulo nativo antiguo:

```text
python -m pytest -q --tb=short
6196 passed, 646 warnings, 419.80 s
```

Inventario estatico:

```text
python tools/audit_mt5_native_access.py
Baseline matches; 0 live modules retain native MT5 access
```

`git diff --check` sobre los archivos modificados por E3-C no encontro errores.
Los avisos de pytest son deprecaciones y advertencias ya visibles; no hubo
errores, tareas pendientes ni cierres anormales al terminar la suite.

## Hallazgo Durante La Verificacion

La primera bateria transversal detecto que el troceado de ticks eliminaba una
repeticion no consecutiva dentro del mismo lote. Eso podia ocultar un cursor
historico ambiguo. Se sustituyo la deduplicacion global por ventanas inclusivas
no solapadas, avanzando un milisegundo entre lotes. Las 55 pruebas de sombras y
la suite global confirman la correccion.

La primera suite global encontro seis pruebas que seguian parcheando
`MetaTrader5` directamente. Se migraron sus dobles a la frontera aislada, sin
cambiar expectativas funcionales. Los 19 casos afectados y la repeticion
global completa quedaron verdes.

## Identidad Del Codigo

SHA-256 al cierre de la candidata:

```text
mt5_runtime.py B4299A09201BF23486ABA2AC8E054B8158FF08EB15E113281B57826DC95CEA74
mt5_client.py 509BDFBD3DA9614428100EB7B9C506FE360FAE5EEA8D96A388C0473B379659F1
mt5_worker.py 0E9897084887B9892FCC55B7F156A887AB421374DC64DB52167B6B9714318CE2
main.py 87782F4D9C0476F6EC9EE44C08B0541C0CA82F7E5260150B7E67144691F07F19
listener.py 7BC158A707BE97DDD19AE0DA8A127F41CCD24F5EA2DB5AEB953D60E128994C5B
position_lifecycle_monitor.py D390FD20D9831DD4ED71053530E8ECB2B6F93E2F1F45D03CB51D4381B105F571
pending_actions.py AB3C82194210FC6A9E6A1B10EE31EFF67A6916EEE820D46EF7A8649917EEDF9F
tools/run_bot_watch.py 10CA4A845FB1CE366DF818C764928CC4E154C4E1A9FDC07FC9F16C79478CE1EE
tools/audit_mt5_native_access.py 8046D3AC0F4745A8587719AA907553D9230F43796A68DF08C5E0354C497092EB
tests/test_e3_c_runtime_startup.py EFD380D8287499C3B68B414B7870B98D0FE51E12FC923C5460B2332D4E506110
tests/test_mt5_trade_client.py 194977D1F645F884E43F6A3AE6EDC7F161C78257304605366D3A2DC3541FACCF
tests/test_strategy_shadow_main.py 7858E58996F0C76CC00AA66808780BD1F5839157E082F6C55020B640F3769D5C
```

## Pendiente Antes De Publicar

La revision Astra/alto debe buscar fallos funcionales, condiciones de carrera,
modo mixto accidental, falsa cuenta plana y monopolizacion del trabajador. Si
la candidata se acepta, E4 debe ejecutar el bloqueo nativo de 60 s, carga de 60
min a dos veces el pico, Python 3.11/Windows, disco lento o lleno, proceso
huerfano, watchdog y capacidad comparable a la VM. Despues E5 valida entradas,
volumen, protecciones, salidas, flotante, MAE/MFE y drawdown sobre muestra
amplia; 3086 y 28/08 siguen siendo controles, no la muestra completa.
