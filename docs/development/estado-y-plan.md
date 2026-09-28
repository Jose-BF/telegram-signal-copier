# Estado y plan del proyecto (documento vivo)

Ultima actualizacion: 24/09/2026 23:50, Claude (relevo desde Codex).
Leer esto primero. Es corto a proposito: lo historico esta en los documentos
de la seccion 10 y no hace falta leerlo para trabajar. Actualizar este
documento al cerrar cada bloque (estado, evidencia, siguiente paso) para que
otra sesion, aunque use un modelo con menos esfuerzo, pueda seguir.

## 1. Objetivo

Con cientos de senales historicas de los dos canales y miles de combinaciones
simuladas, encontrar la gestion de la senal (entradas, salidas y riesgo) con
mas probabilidad de dar beneficio neto, limitando las perdidas.
Canal 1 = Dubai Investing. Canal 2 = Gold Signals. No mezclarlos.

Requisito previo: un simulador fiable (objetivo ~95%, ver paso 3).
La busqueda masiva no empieza sin revisarlo antes con Jose.

## 2. Historia breve

- Copiar la gestion de los traders no dio beneficio.
- Desde el 26/07 hay logs robustos del bot.
- Un primer simulador produjo la estrategia 555 (canal 2, va bien en vivo) y
  Dubai balanced v1 (canal 1, estable, aprox. neutra).
- Luego se vio que el simulador no reproducia la realidad (un drawdown
  simulado de 200 y pico EUR frente a ~160 EUR real). Fase actual: hacerlo
  fiable antes de buscar mas estrategias.
- Codex trabajo meses en esto; relevo a Claude el 24/09/2026.

## 3. Estado a 24/09

Funciona:
- La logica de la estrategia. Si al simulador se le dan las entradas reales,
  canal2_3102 (17/09) sale con el mismo drawdown maximo que la real
  (178,72 EUR) y los tres motores (escalar, Numba, oraculo) coinciden.
- El dinero realizado: 114 de 115 cierres de 14-18/09 cuadran al centimo.

Falla o falta:
- Tiempos del broker mal modelados. En 3102 el modelo deja el TP puesto
  9,3 s despues de entrar; el log del terminal MT5 muestra la peticion a los
  24 ms del deal y el TP confirmado en ~0,6 s. Ademas el modelo llena el TP
  en el primer toque. Resultado: posiciones abiertas de mas y drawdown
  simulado 256,55 frente a 178,72 EUR real.
- Sin margen: en vivo hubo rechazos "No money" (16/09) que el simulador no ve.
- Huecos de EURUSD bloquean la valoracion en EUR de varias cestas.
- No hay curva de equity de cuenta independiente (solo snapshots sueltos).

Aviso de datos: la semana 14-18/09 NO sirve para sacar conclusiones.
El fin de semana anterior se activo un log muy pesado; el diario llego a
~7 GB en la VM (pocos recursos) y el bot se quedaba cargandolo en los
reinicios. Hubo desconexiones, latencias de hasta ~2 min y miles de rechazos.
Casi todo el analisis reciente de Codex usa esa semana: sirve para descubrir
mecanismos, no para calibrar ni validar.

## 4. Plan

Paso 1. Medir los tiempos reales del broker, sin tocar produccion.
- Fuente principal: logs diarios del terminal MT5 de la VM. Cada orden y
  cada modificacion SL/TP con su hora, "accepted", "done in X ms" y los
  rechazos con motivo; tambien desconexiones. Mas deals del broker, ticks
  XAUUSD/EURUSD y diario del bot.
- Solo hay copia local de 14-18/09:
  runtime_data/week_native_ticks_20260923_v1/terminal_logs/ (UTF-16).
  Comprobar cuantos dias antiguos conserva la VM (idealmente desde julio).
- Empezar por: una herramienta nueva que convierta esos logs en eventos
  (tomar como base tools/audit_mt5_terminal_open_logs.py, que solo lee
  aperturas), ligada a los deals por ticket, con cota de reloj VM-broker
  por dia. Probarla con 14-18/09 y aplicarla a semanas buenas.
- Hecho cuando: tabla de tiempos (abrir, poner SL/TP, cerrar), tasas de
  rechazo y retraso del TP tras el toque, por semana buena, con hashes.
- Estado 24/09 noche: herramienta hecha y probada.
  tools/audit_mt5_terminal_trade_logs.py (+ tests/test_audit_mt5_terminal_trade_logs.py,
  9 tests en verde). Empareja peticion/respuesta de ordenes y modificaciones,
  separa rechazos locales del terminal de rechazos del servidor, liga deals
  al ledger, acota el reloj VM-broker por dia y resume tiempos y red.
  Salida inmutable (summary.json, positions.json, events.jsonl.gz).
  Primera salida, solo como referencia de la semana NO representativa:
  runtime_data/terminal_trade_logs_20260914_18_v1/. Falta: logs de semanas
  buenas (necesita acceso a la VM).

Paso 2. Ajustar el modelo de ejecucion con esas medidas.
- Distribuciones medidas, no un numero fijo. TP que no se llena en el primer
  toque. Rechazos (Invalid stops) y reintentos. Margen de la cuenta.
- Ajustar con unas semanas y reservar otras sin mirar para el examen.
- Codigo: research/dubai_iterative/ (broker_execution.py, market.py,
  protection.py, client.py, shared_replay.py) y los tres motores engine.py,
  fast_engine.py y oracle.py; identidad en research/iterative_provenance.py.
- Hecho cuando: los tres motores implementan lo mismo y los tests pasan.

Paso 3. Examen del simulador contra operaciones reales.
- Semanas limpias no usadas en el paso 2 (primera candidata: 21-25/09).
- A. Logica: dandole la ejecucion real, sus ordenes (tipo, pierna, valor
  SL/TP, orden) deben coincidir con las del log del terminal. 100%, o cada
  diferencia explicada y corregida con su test.
- B. Simulacion completa desde el mensaje de Telegram, sin fills reales, de
  las estrategias en vivo (555 y Dubai balanced), repetida con la
  incertidumbre de ejecucion. Comparar por cesta y en total: beneficio,
  drawdown y exposicion maxima.
- Criterio "95%" (propuesta; congelarlo antes de mirar el examen):
  1) Neto: |total simulado - total real| <= 5% de la suma de |neto real|
     de las cestas.
  2) Drawdown: |suma simulada - suma real| <= 5% de la suma real, y sin
     subestimarlo de forma sistematica.
  3) El valor real de cada cesta cae dentro del rango simulado en al menos
     el 90% de las cestas.
  Lo que no cumpla: explicado o bloqueado, nunca borrado.
- Reutilizar: tools/audit_week_conditioned_controls.py,
  tools/probe_canal1_incremental_window.py, tools/compare_incremental_tick_paths.py,
  research/account_risk_path.py, research/gold_iterative/live_parity.py.

Paso 4. Busqueda masiva (solo tras revisarla con Jose).
- Elegir con julio-agosto, comprobar con septiembre sin tocar, luego demo.
- Presupuesto finito y todas las variantes registradas.

## 5. Datos en vivo y produccion

- Para este plan no hace falta cambiar produccion: MT5 ya apunta los tiempos
  en sus logs, el broker guarda deals y ticks y el bot guarda su diario.
- Si falta un dato: proponerlo a Jose y desplegar mejor en fin de semana
  (mercado cerrado), verificando en la VM que se registra.
- Extraer tras el cierre del 25/09 (solo lectura, sin cargar la VM): logs del
  terminal disponibles, deals, ticks y diario/mensajes de 21-25/09.
- Disco de la VM: 19,6 GB libres el 20/09 y ~12,3 GB el 24/09. Bajo 4 GiB
  el bot avisa; si el diario no puede escribir se vetan entradas nuevas.
  Revisar y archivar el diario antiguo con copia verificada (nunca borrar
  evidencia sin copia).

## 6. Operacion de la VM (responsabilidad de Claude)

- VM Windows en Proxmox; SSH bot@192.168.0.48 (LAN). Acceso de Claude:
  ACTIVO desde 27/09 con clave dedicada C:\Users\josea\claude-vm\id_ed25519
  (known_hosts en la misma carpeta; `bot` es usuario normal: clave en
  C:\Users\bot\.ssh\authorized_keys, linea "claude-code@josea-laptop"; se
  revoca borrando esa linea). Repo en la VM: C:\Users\bot\telegram-signal-copier.
- Sombra en la VM (27/09): `runtime_data/strategy_shadow_report.json` no se
  regenera desde el 30/08, y en cada arranque la observacion se desactiva a los
  pocos minutos ("observacion desactivada tras un fallo aislado"). No hay
  resultados de sombra de 21-25/09. Sin tocar: requiere OK de Jose.
- El watcher de la VM despliega lo que llega a origin/main
  (tools/run_bot_watch.py). La rama local feature/gold-555-live-trial tiene
  upstream origin/main: un push despliega.
- Cada despliegue: OK explicito de Jose para ese cambio -> exposicion plana
  y sin pendientes -> push -> comprobar en la VM commit, heartbeat nuevo,
  Telegram y MT5 activos -> anotarlo aqui.
- Repositorio Jose-BF/telegram-signal-copier: publico segun consulta del
  24/09. Nunca subir .env, sesiones, claves ni datos privados.

## 7. Reglas (resumen de AGENTS.md)

- Hablar con Jose en espanol sencillo y corto; el detalle va en los docs.
- Sin OK explicito: nada de busqueda masiva, cambios de estrategia en vivo,
  reinicios, ordenes ni despliegues.
- Conservar logs, datos e informes; no borrar evidencia.
- No ajustar el simulador a un caso concreto ni ampliar tolerancias para
  que pase. Todos los casos siguen en el recuento, tambien los bloqueados.
- Separar siempre: lo que paso / lo que decidiria la politica / como
  modelamos la ejecucion del broker.

## 8. Decisiones de Jose y pendientes (24/09, 23:15)

- Semana 21-25/09: segun Jose el bot ha ido estable. Es la primera candidata
  a semana limpia para el examen (confirmarlo con los datos).
- Acceso SSH a la VM: autorizado por Jose. El puente de Claude no permite
  conectar ~/.ssh (carpeta protegida): no usar ni copiar la clave personal
  de Jose. Plan: carpeta dedicada C:\Users\josea\claude-vm (fuera de
  OneDrive y del proyecto) con una clave solo para Claude; Jose (o Codex)
  anade la clave publica al usuario bot de la VM. Revocable quitando esa linea.
- Repositorio privado: autorizado. Antes, comprobar como descarga la VM el
  codigo: si usa HTTPS sin credenciales, al hacerlo privado dejaria de
  actualizarse. Hacerlo en fin de semana y verificar despues que el watcher
  sigue actualizando.
- Rendimiento de la VM: Jose pide revisar retrasos por hardware u
  optimizacion (CPU, memoria, disco, tiempos de MT5) y proponer mejoras.

## 9. Hallazgos del 24/09 pendientes de formalizar (Claude)

Analisis rapido de los logs del terminal de 14-18/09 (semana no
representativa, sin hashes todavia):
- 45 de 45 posiciones cerradas por TP tienen el TP confirmado por MT5 antes
  del cierre; en 43 el primer toque llega despues de esa confirmacion. Los
  "36 TP sin recibo" del relevo eran falta de extraccion, no de datos.
- El reloj VM-broker cuadra con el desfase de 10.800 s (+-0,1 s) el 15, 16,
  17 y 18/09. El 14/09 no cuadra (muchas desconexiones).
- Latencias muy variables: orden a mercado con mediana diaria 0,2-1,2 s y
  maximos de hasta 115 s; 16/09 14:02-14:10 UTC, 7.863 rechazos "No money"
  con 0,26 lotes abiertos.
- El perfil de ejecucion usado en las ventanas de Codex era inventado y
  varias veces mas lento que lo medido (TP puesto >= 4 s tras entrar frente
  a ~0,6 s medido): tools/probe_canal1_incremental_window.py
  usa MarketProfile(2000, 1000, 1000) y ProtectionProfile(..., 1000, 1000, 1000)
  ms (acuse de entrada 2 s, proceso y acuse de SL/TP 1 s cada uno). Medido en
  el terminal (14-18/09): modificacion SL/TP p50 ~0,14 s, acuse->done casi 0,
  TP puesto ~0,6 s tras la entrada (p50). Explica el caso 3102.
- Red: ping al broker 115-123 ms; MT5 indica que su servidor esta cerca de
  Ashburn (EE. UU.): 1,38 ms desde un VPS alli frente a 114,71 ms desde la VM.
  Es el suelo fisico de cualquier orden desde casa; los retrasos de segundos o
  minutos son otra cosa (VM, desconexiones o servidor) y hay que separarlos.
- Rechazos "Invalid stops" y "No money" suelen ser locales del terminal (sin
  linea de peticion): el bot reintenta ~1 vez por segundo (Invalid stops) o
  a ~50 por segundo (No money, 16/09). Defecto del bot a revisar aparte.
- Decision: no se implementa el auditor TP "desde la entrada" propuesto por
  Codex. Usa el deal real como entrada y no puede detectar errores de
  prediccion; lo sustituye el paso 1.

## 10. Documentos de detalle

- AGENTS.md: reglas completas y contratos de evidencia (compartido con Codex).
- 2026-09-24-simulator-goal-handoff.md: relevo tecnico de Codex.
- 2026-09-24-claude-transfer-prompt.md: peticion original de Jose.
- 2026-09-08-simulation-foundation-readiness.md: historial completo; muy
  largo, buscar por fecha en vez de leerlo entero.
- research-contracts.md, shadow-evidence.md, runtime-and-replay.md: contratos.
- ../vm-runtime-recovery.md: arranque, recuperacion y disco de la VM.
- 2026-09-24-project-restore-point.md: copia congelada previa a Claude.

## 11. Proximos pasos y forma de trabajo

Proximos pasos:
1. Viernes 25/09: produccion sin cambios. Si ya hay acceso, solo una revision
   ligera de la VM (mirar, sin tocar).
2. Fin de semana (mercado cerrado): revisar VM (salud, disco, rendimiento,
   logs de MT5 guardados); extraer en solo lectura los datos de 21-25/09 y los
   logs antiguos del terminal; repositorio privado con la comprobacion previa;
   archivar el diario viejo con copia verificada (con OK de Jose); empezar
   el paso 1.
3. Semana siguiente: pasos 2 y 3; resultado explicado en corto a Jose y
   revision conjunta antes del paso 4.

Modelo y esfuerzo (recomendado a Jose):
- Planificar o decidir algo importante: Opus 5.5 en max (o xhigh).
- Implementar: Opus 5.5 en high (su valor por defecto es medium). Empezar
  cada paso en plan mode. Alternativa mas barata: opusplan (Opus planifica,
  Sonnet programa); para el simulador se prefiere Opus.
- /goal (equivalente al goal de Codex): un goal por paso, con condicion de
  fin comprobable y limite de turnos. Ejemplo:
  /goal Paso 1 de docs/development/estado-y-plan.md terminado: herramienta
  de logs de MT5 con tests en verde, informe generado y anotado en este
  documento, sin tocar produccion; o para tras 20 turnos

## 12. Registro 25/09 (manana)

- Acceso SSH de Claude a la VM operativo (clave dedicada anadida por Jose a
  administrators_authorized_keys). Clave privada solo en el sandbox de la
  sesion; en otra sesion habra que crear otra o guardarla en carpeta dedicada.
- VM 08:03: bot sano (heartbeat fresco, plano, cola 0), commit 7415941a en
  main, RAM 5 GB (2,4 libres), CPU 1%, disco C 10,4 GB libres de 49.
  runtime_data/trade_events.jsonl = 10,8 GB (7,08 GB el 20/09): ~0,7 GB/dia.
  Aviso de disco (<4 GiB) en ~1 semana: archivar con copia verificada.
- Remoto Git de la VM: HTTPS con Git Credential Manager (tambien hace push).
  Poner el repo privado no deberia romper el fetch; si fallara, el watcher
  mantiene el bot vivo y solo deja de actualizar. Verificar tras el cambio.
- Copiados en solo lectura 153 logs del terminal (26/04-25/09) a
  runtime_data/mt5_terminal_logs_vm_20260925/; informe
  runtime_data/terminal_trade_logs_20260426_0925_v1/.
- Hallazgo: la orden a mercado se ha vuelto mucho mas lenta con el tiempo con
  ping estable (~120 ms): p50/p90 ms = abr-jul 125/174; 26/07-12/09 137/881;
  14-18/09 410/13.477; 21-25/09 615/11.879 (maximo 155 s). Las colas largas
  existen tambien en la semana "estable". Causa por investigar (servidor demo,
  terminal o VM cargada/diario gigante). El simulador debe modelar esas colas.
- Tiempo de punta a punta (tools/audit_signal_to_fill_latency.py, diario local
  26/07-31/08 + logs del terminal). Mediana por tramo: Telegram publica ->
  bot la ve ~1,5-1,7 s (fecha de Telegram redondeada al segundo: +-1 s);
  bot decide ~0,01 s; Python -> terminal ~0,005 s; broker ~0,13-0,25 s;
  vuelta al bot ~0 s. Total publicacion -> fill ~2 s (p50). Push y sondeo
  (cada 0,5 s) llegan igual de rapido. El precio que manda el bot tiene
  ~0,5-0,8 s de antiguedad (revisar).
- La subida reciente esta en el tramo del broker: en 21-25/09 peticion ->
  aceptada p50 421 ms (antes 124) y aceptada -> ejecutada p50 100 ms (antes
  1). Las modificaciones SL/TP siguen rapidas (~140 ms) y no habia otras
  peticiones en vuelo: apunta a la ejecucion de mercado del servidor demo,
  no a la VM ni a la red. Pendiente: medir 21-25/09 con el diario de la VM
  (extraccion programada tras el cierre del viernes).
- Semana 21-25/09 (diario extraido de la VM en tramos de un dia, prioridad
  de fondo; runtime_data/vm_journal_extract_20260925/): Telegram -> bot p50
  1,37 s (p90 6 s); bot 12 ms; Python -> terminal 4 ms; broker p50 671 ms
  (p90 13,5 s); vuelta 1 ms; fill -> primer SL/TP p50 0,8 s. El precio
  enviado tiene ~0,4 s. Las estrategias actuales esperan condicion de precio
  antes de entrar (no es retraso). Deslizamiento 21-25/09: ~0,22 USD por
  orden (antes ~0).
- Paso 2 iniciado: tools/build_execution_calibration.py convierte los tiempos
  medidos en retrasos del motor (contract execution_calibration_v1);
  tools/probe_canal1_incremental_window.py acepta --execution-calibration
  (sin ella, mismo comportamiento que antes; 18 tests en verde).
  Calibraciones en runtime_data/execution_calibration_v1/: A = 26/07-12/09
  (p50: entrada 136 ms, SL/TP 127 ms), C = 21-25/09 (p50: entrada 671 ms).
- Comprobacion de mecanismo (ventanas de 17/09 y 18/09, semana no
  representativa, calibracion A de otro periodo, sin ajustar a los casos):
  canal2_3102 drawdown 256,55 -> 180,05 EUR (real 178,72) y volumen 0,16 ->
  0,12 (real 0,12); canal2_3168 65,95 -> 65,64 (real 65,65); canal1_22732
  32,72 -> 28,25 (real 27,27); canal2_3106 9,27 -> 8,38 (real 6,32);
  canal2_3171 4,23 -> 0,84 (real 4,22: TP en vuelo con rechazos, el perfil
  fijo no lo capta). Ventana conjunta 17/09: 255,07 -> 178,51 (real 177,24).
  Salidas en runtime_data/calibrated_window_check_20260925_v1/.
- Siguiente: ticks de 21-25/09 (copia aislada de MT5 en la VM tras el
  cierre del viernes) y examen de esa semana; despues retrasos por peticion
  (distribucion, no un numero fijo) y rechazos Invalid stops.
- Paso "senales limpias": tools/build_signal_trigger_catalog.py ->
  runtime_data/signal_trigger_catalog_20260726_0925_v1/ con 489 disparadores
  reconocidos por el bot (canal1 161: 153 sticker + 8 texto; canal2 328),
  26/07-25/09, con hora de publicacion y de recepcion. Cruce con el catalogo
  de Codex (hasta 31/08): 276 senales formales, 33 sin signal_received del bot
  (caidas o huecos de lectura) y 16 del bot sin formal. Revisar esas 49.
- Examen 21-25/09 preparado: mensajes crudos 21-24/09 en
  runtime_data/week_20260921_v1/raw_week_20260921_24_v1.json (2.923 filas);
  la sonda acepta --raw-name. Faltan ticks y deals de la semana (copia aislada
  de MT5 tras el cierre) y la cadena nativa (summarize_native_week ->
  audit_native_money_anchor -> audit_native_week_risk_path).
- Catalogo v2 (runtime_data/signal_trigger_catalog_20260726_0925_v2/): fuente
  telegram_understood kind=entry_signal (lo que el bot entendio como entrada)
  marcando si actuo (signal_received). 554 senales: canal1 181 (161 operadas),
  canal2 373 (328 operadas). Frente al catalogo de Codex hasta 31/08 solo faltan
  2 formales; las 65 no operadas son decisiones de la estrategia/filtros, no
  mensajes perdidos. Para septiembre no hay referencia externa de caidas.
- Banda p50/p90 (calibracion A) en las ventanas 17-18/09: el real cae dentro
  en 3102 (176,89-180,05 vs 178,72) y en el borde en 3168; fuera en 22732,
  22738, 3106, 3171, 22768. Un retraso fijo por ejecucion no basta: siguiente
  mejora del motor = retraso por peticion muestreado de la distribucion medida
  (varias ejecuciones con semilla) y rechazos Invalid stops.
- 25/09 16:20: la VM deja de responder por SSH desde el portatil (pendiente de
  saber si es el portatil fuera de casa o caida de internet/luz en casa).
- Coste VPS: MQL5 VPS 12,8-15 USD/mes pero solo ejecuta EAs de MT5, no el bot
  Python; un VPS Windows cerca de Ashburn cuesta mas. Vantage devuelve 25-50
  USD/mes de VPS con deposito >= 1.000 USD y volumen (cuenta real). Un VPS
  solo ahorra ~0,12 s de red; no arregla los segundos de ejecucion observados.


## 13. Registro 25/09 (tarde)

- 17:16 VM accesible otra vez: la clave de Tailscale del proxmox habia
  caducado (25/09 14:01); Jose la renovo.
- Reinicios del bot explicados (6 entre 19 y 25/09; 3 hoy 10:05, 10:31,
  10:46). Siempre justo despues de "git fetch origin main timed out after
  15s": en Windows subprocess.run(timeout) mata solo git.exe y luego espera
  sin limite a que se cierren las tuberias, que git-remote-https mantiene
  abiertas. El bucle del watcher se queda 95-135 s parado, lo toma por "Pausa
  del sistema" (umbral 90 s) y mata y relanza el bot (3-5 min sin operar).
  Prueba en la VM: subprocess.run(timeout=2) con un nieto vivo tarda 24,3 s;
  el arreglo 2,2 s. Arreglo: git_sync.run_bounded (mata el arbol de
  procesos, lectura acotada) usado por el watcher y git_sync, y el tiempo del
  propio fetch no cuenta como pausa. Commit bcf1520 preparado en la VM
  (C:\Users\bot\claude_work\repo, push por SSH comprobado con --dry-run);
  160 tests OK en Linux (3 fallos previos por reserva de disco del
  contenedor, ajenos). PENDIENTE: OK de Jose para desplegar.
- La copia local de trabajo NO es produccion: tools/run_bot_watch.py local
  tiene cambios sin commit de Codex (contrato mt5_owner). El arreglo se hizo
  sobre origin/main (7415941a). Al sincronizar la copia local hay que
  integrar ambos.
- La copia aislada de MT5 no arranca en la VM (sesion de servicio): el
  terminal se inicia pero nunca se conecta y mt5.initialize da -10005 (IPC
  timeout) incluso con 60 s. Se borro la copia. Alternativa creada:
  tools/attached_mt5_history_readonly.py (se conecta al terminal del bot en
  solo lectura, no selecciona simbolos ni envia ordenes, y se niega a leer
  ticks si las cotizaciones se mueven: comprueba que time_msc no cambie en
  30 s). Deals 21-25/09 (captura 17:40): 148 posiciones, 296 deals en
  runtime_data/week_20260921_v1/attached_20260921_25_deals_snapshot1/.
- tools/reconcile_native_week_deals.py (generaliza summarize_native_week;
  reproduce exactamente native_week_reconciled.json de 14-18/09, 40 cestas y
  todas las posiciones). Semana 21-25/09 hasta las 17:40: canal1 17 cestas
  +69,59 EUR (13+/4-), canal2 37 cestas +378,23 EUR (36+/1-); total +447,82.
- tools/native_basket_risk_from_deals.py: drawdown por cesta de las
  ejecuciones reales marcadas en cada tick, sin la cadena de anclas
  (misma funcion que audit_native_week_risk_path). Reproduce
  native_week_risk_path_v1.json de 14-18/09 identico en 40/40 cestas (hash
  del recorrido y metricas).
- tools/run_week_exam.py: examen por semana. Corta ventanas donde la cuenta
  real estaba plana, incluye senales que el simulador veria aunque el bot no
  operase, ejecuta la sonda con varios perfiles (UNCAL, A_p50, A_p90) y
  compara por cesta neto, drawdown y volumen. Se ejecuta en el contenedor
  (2 CPU; una ventana de 70 min tarda 18 s y da lo mismo que en el portatil).
- Esta noche (recordatorio 21:25 UTC): ticks 21-25/09 con
  attached_mt5_history_readonly.py tras el cierre, telegram_raw completo del
  25/09, cadena nativa y examen.
- Examen de prueba sobre 14-18/09 (semana no representativa; solo para
  probar la herramienta): 30 de 40 cestas cubiertas (la tarde del 16/09 no
  cabe en el limite de eventos del motor: 15 senales en 6 h). Perfil A_p50:
  suma de drawdown +0,05 % frente al real, neto -12,7 % (sim -308 EUR, real
  -210 EUR), real dentro del rango de perfiles en 63 % de cestas. Cambios en
  la sonda (opcionales, por defecto igual que antes): --max-fx-age-ms (el
  examen usa 30 s: EURUSD pasa >5 s sin tick ~25 veces/hora y bloqueaba
  cestas), --scope-tick-budget y --same-ms-message-after-quote (un mensaje
  registrado en el mismo milisegundo que un tick se ordena 1 us despues;
  cada desplazamiento queda en el informe).
- Caso canal2_2982 (15/09): mismo neto (19,92) pero drawdown real 11,70 vs
  sim 81,04. Causa medida en el log del terminal: la orden de mercado del
  nivel 3 (14:21:44) tardo 55,5 s en llenarse (aceptada a los 48 s); el bot
  quedo bloqueado y los niveles siguientes salieron seguidos a las 14:23:14
  y 14:23:16 a otros precios. El simulador con retraso fijo de 136 ms llena
  a tiempo y deja dos patas abiertas 80 min. Conclusion: hace falta (1)
  retraso por peticion muestreado de la distribucion medida (cola pesada) y
  (2) serializar las peticiones como el bot (una orden en vuelo a la vez).
- Diseno propuesto para (1)+(2), pendiente de revisar con Jose antes de
  tocar los motores: un muestreador determinista delay(tipo, clave, semilla)
  que devuelve un cuantil de la distribucion empirica (clave = senal + ordinal
  de peticion); por defecto devuelve la constante actual (sin cambio de
  resultados). Cola FIFO de peticiones de mercado por cuenta. Motor escalar
  primero; fast_engine y oracle rechazan el modo muestreado con un bloqueo
  explicito hasta implementarlo, para no romper la equivalencia en silencio.
  Examen con N semillas por ventana: rango p5-p95 por cesta.
- Calibraciones nuevas por cuantil (p25, p75, p95) para A (26/07-12/09) y C
  (21-25/09). C es mucho peor: entrada p75 3,6 s, p90 11,8 s, p95 15,4 s;
  cierre p90 46 s, p95 61 s.
- Riesgo: la rama telemetry del repo (publico) contiene fragmentos del
  diario del bot y de los mensajes de Telegram. Motivo mas para ponerlo en
  privado ya.
- Retraso por peticion (v1, 25/09 tarde): research/dubai_iterative/
  latency_model.py. LatencyModel(seed, muestra) da a cada orden de entrada
  un retraso de llenado sacado de la muestra medida, determinista por
  (semilla, senal, pata). Solo motor escalar en modo cliente (fast_engine y
  oracle ya rechazan el modo cliente; execution_to_scenario rechaza el
  modelo en vez de perderlo). Sin modelo el resultado es identico al
  anterior (hash del recorrido igual). Muestras: tools/build_latency_samples.py
  -> runtime_data/latency_samples_v1/{A,B,C_partial}.json (B = 14-18/09:
  p50 413 ms, p90 19 s, p95 49 s, p99 106 s). La sonda acepta
  --latency-samples/--latency-seed y el examen perfiles
  SAMPLED:<calibracion>:<muestra>:<semilla> con --range-prefix.
- Comprobacion del mecanismo en canal2_2982: con el retraso observado de la
  pata 3 (55,5 s) el simulador llena a las 14:22:40.138 a 4288,00 (real
  14:22:40.201 a 4287,93) y el drawdown baja de 81,04 a 40,52 (real 11,70);
  el resto viene de 34 s en que el bot tardo en mandar la siguiente orden
  (semana con log pesado). Con 8 semillas de B una da 11,5.
- Examen 14-18/09 con 8 semillas de B: el drawdown real cae dentro del rango
  simulado en 82,6 % de cestas (antes 63 %) y el neto en 91,3 %; el error
  medio de la suma de drawdown es +0,4 % (cada semilla entre -11 % y +13 %).
  El neto sigue peor en el simulador (media -38 % sobre la suma absoluta),
  dominado por canal2_2917 y canal2_2908 (semana no representativa).
- 25/09 21:33 Jose: el repositorio se queda publico de momento (decision suya, avisado del contenido de la rama telemetry). Arreglo del watcher sigue sin OK de despliegue.

## 14. Examen semana 21-25/09 (25/09 noche)

- Datos tras el cierre: ticks XAUUSD/EURUSD 21-25 (attached_mt5_history_readonly,
  cotizaciones congeladas comprobadas), 298 deals, telegram_raw completo
  (raw_week_20260921_25_v1.json, 3.604 filas), logs del terminal 21-25
  (terminal_trade_logs_20260921_25_close_v1), calibracion C final
  (execution_calibration_v2; entrada p50 789 ms, p75 4,9 s, p90 11,8 s,
  p99 123 s) y muestra latency_samples_v1/C.json (149 ordenes).
- Real: 55 cestas, +447,96 EUR (canal1 18: +69,73; canal2 37: +378,23).
- Resultado (runtime_data/week_20260921_v1/exam_2125_v1.json), 44 cestas
  comparables de 55:
  * Neto: C_p50 sim 392,25 vs real 404,50 (error 2,4 %). 10 semillas con
    retraso por peticion: 304-444, mediana ~405; error medio 6,6 %.
  * Drawdown (suma): semillas -6 % de media (de -22 % a +1 %); C_p50 -11 %.
    Ligera infraestimacion sistematica (12-20 cestas por debajo).
  * Real dentro del rango de semillas: drawdown 80 %, neto 86 % (meta 90 %).
- Fallos a arreglar: (1) 16 ejecuciones rotas por "unknown provider scope
  or finished basket" (un mensaje de gestion llega a una cesta que en esa
  semilla ya cerro; el bot lo ignoraria); (2) 4 cestas canal1 que el bot
  abrio y el simulador no (22869, 22891, 22913, 23068); (3) 1 cesta canal2
  que el simulador abre y el bot no (3488); (4) 3 cestas sin cubrir
  (22839, 3229, 3538).
- Arreglos tras el primer examen: (1) la sonda acepta
  --ignore-unknown-stickers (el bot solo reconoce sus 2 stickers de canal1;
  el sticker 6269520429493258755 aparecia antes de 22869, 22891, 22913 y
  23068 y el compilador causal bloqueaba todo canal1); cada fila ignorada
  queda en el informe. (2) shared_replay ignora (y cuenta en limitations) un
  mensaje de gestion que llega a una cesta ya cerrada, en vez de romper.
- Examen v2 (exam_2125_v2.json): 55/55 cestas cubiertas, 0 errores.
  * Neto real +447,96. A_p50 +457,52 (1,7 %), C_p50 +421,21 (4,8 %).
    10 semillas C: 334-471, mediana ~425; error medio 6,2 %. -> cumple en
    perfiles fijos; la dispersion por semilla es el azar del broker.
  * Drawdown: semillas mediana -5,1 % (de -19 % a +1 %); A_p50 -4,9 %;
    C_p50 -9,3 %. Infraestimacion leve y sistematica (14-20 cestas por
    debajo frente a 8-16 por encima). Es lo siguiente a investigar.
  * Real dentro del rango de 10 semillas: drawdown 81,5 %, neto 87 %.
  * Quedan 4 cestas bloqueadas (22853, 3252, 3255, 3258: abiertas al corte
    o ventana larga), 1 solo real (23074) y 1 solo simulada (3488).
- Veredicto Paso 3: neto dentro del objetivo; drawdown al limite y con sesgo
  a la baja; rango por debajo del 90 %. Aun no se da por calibrado.
- 26/09 01:18: desplegado bcf15205 (arreglo del watcher) con el bot plano y mercado cerrado. El watcher se relanzo (log watcher_20260925T231817305512Z), bot pid 6052, Telegram y MT5 conectados, heartbeat fresco, plano. Vigilar que no vuelvan los 'Pausa del sistema' tras 'git fetch timed out'.
- 26/09 madrugada, mas arreglos (todos opcionales en la sonda, activos en el examen):
  * idle_unresolved_ignored (research/causal_text_admission.py y
    causal_shared_stream.py): un texto de canal1 que el compilador no sabe
    interpretar, llegado sin ninguna senal abierta, se ignora (el bot lo
    ignora) en vez de bloquear todo canal1 despues. Caso: 23073 "closed at
    BE" dejaba fuera 23074. Tests: tests/test_idle_unresolved.py.
  * Retraso de canal1 propio: sus ordenes tardaron mas en el broker
    (p50 1,8 s frente a 0,67 s canal2 en 21-25). Muestra
    latency_samples_v1/C_canal1.json (19 ordenes, del diario); la sonda
    acepta canal1_latency_model y el examen un 5o campo en SAMPLED.
  * El examen revierte una ampliacion de ventana que agota el presupuesto
    de eventos y no amplia mas alla de las 20:58 UTC.
- Examen v4 (exam_2125_v4.json): 55/55 cubiertas, 0 errores, 0 solo-real.
  Neto real 447,96: A_p50 466,81 (3,4 %), C_p50 472,59 (4,5 %); 10
  semillas 410-546, mediana ~457. Drawdown: semillas mediana -5,6 %, A_p50
  -5,1 %, C_p50 -9,3 %. Rango: drawdown 80 %, neto 85,5 %.
- Sigue abierto: la infraestimacion de drawdown (~5 %). Caso tipico
  canal2_3471: la pata B1 real entro 0,59 USD mejor... para el broker y su
  TP no se toco; quedo 3 h abierta (DD 70,5 real frente a ~26 sim). Son
  casos al filo que dependen de decimas en el precio de llenado. Hipotesis
  siguiente: los retrasos del broker van en rachas (una cesta entera lenta),
  y el muestreo independiente por orden reparte la mala suerte.

## 15. Broker y muestra historica (26/09, pregunta de Jose)

- Datos propios: el retraso de Vantage demo empeoro en el servidor, no en
  la VM (orden de mercado p50: abr-jul 125 ms, ago 137 ms, 14-18/09 410 ms,
  21-25/09 789 ms; colas de 1-2 min). Demo y real pueden ir por
  infraestructuras distintas; no hay forma de saber el real de Vantage sin
  probarlo.
- Propuesta: prueba A/B de broker. Jose crea demo MT5 en VT Markets; un
  script de medida abre y cierra 0,01 lotes de XAUUSD cada ~15 min en horas
  de mercado (solo demo, necesita OK expreso) durante 1 semana, y se compara
  con los tiempos del bot en Vantage demo a la misma hora. Opcional: igual en
  una cuenta real minima de Vantage para saber si demo != real.
- No usar una media fija de retraso: es lo que hacia fallar el simulador. Para
  elegir estrategia, evaluarla con varios escenarios de broker (rapido julio,
  lento septiembre, colas) y quedarse con la que gane en todos.
- Muestra historica: Codex ya preparo canal1 ene-sep 2026 desde el export
  (736 disparadores, runtime_data/canal1_history_20260913/, entry_stream_v3)
  y ticks 2026 de Vantage (~150 M cotizaciones). Canal2 (Gold) del chat
  antiguo 3828356530: 453 "NOW" en 2026 pero todos editados despues (sin
  version inicial): vale la hora y la direccion, no los niveles -> encaja con
  "solo disparador + gestion propia". Falta: juntar ambos en un flujo de
  disparadores, reloj de recepcion (+~1,5 s y retraso muestreado) y separar
  periodos (buscar ene-jun, validar jul-sep, luego demo en vivo).

## 16. Fin de semana 26/09: simulador cerrado y muestra historica lista

Simulador (examen 21-25/09 y 24-28/08):
- Regla del bot anadida al simulador aislado: canal1 lleva una cesta cada vez;
  lo que llega con la cesta abierta (texto o sticker repetido) es la misma
  senal. Quita 17 cestas de mas. Ninguna cesta real de canal1 se abrio nunca
  dentro de otra.
- Precio de referencia de Gold 555 (retroceso/rebote): el bot usa la ultima
  cotizacion que ve su terminal, ~0,5 s mas vieja que el historial del broker
  (48 senales 21-25/09: p10 343 ms, p50 503 ms, p90 660 ms). Nuevo parametro
  `quote_view_lag_ms` (ExecutionAssumptions; motor lento, rapido y modo
  cliente; 0 = comportamiento anterior; el oraculo lo rechaza). Con 450 ms
  desaparece canal2_3488 (el sim entraba, el bot no: el oro subio 0,4 en ese
  medio segundo). Calibraciones: execution_calibration_v2/{A,C}_p50_q450.json.
- Motor rapido aislado 21-25: 55/55 cestas, 0 de mas, 0 errores; neto +5 %,
  DD -5,3 %. Motor lento (v7, 10 semillas): 0 de mas, neto +3,7 % (semillas
  0,9..10,1), DD -5,4 % (mediana semillas -3,9 %, rango -12,5..+4,3);
  real dentro del rango: DD 84 %, neto 86 %. 5 cestas quedan fuera por el
  tope de eventos (1 M, limite de seguridad; no se toca).
- Agosto 24-28 (solo canal1; canal2 tenia otra estrategia): 26 cestas,
  neto mediana -1,5 %, DD -2,6 %, todas las semillas dentro de +-5 %.
- Pendiente conocido: DD ligeramente optimista (-3 a -5 %); resultados por
  cesta muy sensibles al segundo exacto (1 s cambia +10 EUR a -25 EUR en
  canal1_23020): solo cuentan agregados grandes.

Muestra historica (disparadores = hora de publicacion + direccion):
- Gold canal nuevo (-1003908582492): no hace falta export. El diario del bot
  (trade_events.jsonl, eventos telegram_raw) guarda cada version de cada
  mensaje con date_utc y hora de recepcion. tools/build_journal_gold_triggers.py
  -> 394 disparadores 22/07-25/09, 0 cambios de direccion, 48/48 iguales a
  las entradas del bot en 21-25/09. Gold canal antiguo (3828356530): export
  abr-3/07. Hueco 3/07-22/07 sin disparadores.
- Dubai: catalog_v2 (ene-13/09) + diario desde 13/09
  (tools/build_journal_dubai_triggers.py); ambos coinciden 276/277 con la
  misma direccion. Fichero combinado history_triggers_v1/dubai_triggers_all.jsonl.
- Retraso Telegram->bot medido en el diario (en vivo): Dubai p50 1,2 s,
  p90 2,1 s; Gold p50 1,4 s, p90 3,2 s; 1-3 % tarda 10-120 s.
  history_triggers_v1/tg_delay_samples_v2.json.
- Reloj del broker: UTC+2 hasta el cambio de hora de EEUU (08/03/2026) y
  UTC+3 despues (el corte diario del oro esta siempre a las 00:00 del
  servidor y el lunes abre a las 01:00 = 23:00 UTC en invierno). Con +3 fijo,
  ene-7/03 queda desplazado 1 h. Probable causa de que Codex solo admitiera
  240/736 disparadores de Dubai.
- Ticks 02/01-11/09 (180 dias, XAU+EUR) verificados por hash.
- research/history_replay.py: reproduce la historia con cualquier estrategia
  (1.597 disparadores en ~72 s). Validacion frente a la realidad:
  Dubai 24-28/08 neto 118,9 vs 122,0 real (26/27 cestas); Gold 21-25/09
  neto 390 vs 372 real (37/37 cestas), DD -7 %.
- Los mensajes de gestion del canal no cambiaron ninguna cesta en 21-25/09:
  las estrategias actuales ya son "disparador + gestion propia".

Linea base historica (estrategias actuales, broker lento C / rapido A):
- Dubai: -71 / -326 EUR en ene-11/09; solo gana en jul-ago (donde se ajusto).
- Gold (abr-11/09): +318 / +702 EUR; gana 86-98 % de cestas pero 27 cestas
  pierden >100 EUR (suma -6.373) frente a +6.691 del resto (sin stop).
- Siguiente: espacio de busqueda (paso C) sobre este arnes, buscar en
  ene-jun, validar jul-sep, examen final 14-25/09.

## 17. Estudio de las senales (26/09 noche, punto 1 acordado con Jose)

Decisiones de Jose (26/09): canal Gold antiguo y nuevo = mismo proveedor; busqueda
masiva de ideas nuevas, pero primero la herramienta lista y el procedimiento
acordado (nada de busqueda por mi cuenta); objetivo final con cuentas de fondeo
(poco riesgo con ganancias); "aprender la estrategia del proveedor" en pausa.
Herramientas de nico66fx: solo inspiracion (Monte Carlo, simulador de fondeo);
ojo, miran solo el cierre de cada operacion, no el flotante: con cestas seria
optimista. Se replicaran dentro de nuestra herramienta con el flotante real.

Datos (runtime_data/signal_universe_v1/signals.jsonl, tools/build_signal_universe.py):
- 1.723 senales: Dubai 776 (ene-25/09), Gold 947 (8/04-25/09, sin huecos:
  el hueco 3-21/07 se relleno del diario, canal antiguo; los ids 13xxx sin chat
  de 5-12/06 eran un espejo del mismo proveedor y ya estaban en el export).
- Reglas del bot en vivo verificadas en listener.py: sticker de direccion abre
  cesta SIEMPRE (duplicado <5 s se ignora); texto de entrada abre solo si no hay
  senal canal1 abierta (si la hay, se asocia). Corregido en el replay (antes
  bloqueaba stickers con cesta abierta) y en el examen aislado (mismo resultado:
  las 17 "de mas" eran textos). Entradas solo-texto de Dubai: 26; texto seguido
  de su sticker = una senal.
- Dubai reenvia desde 03/09 senales de otro canal suyo ("VIP 3.0"); hay 3
  reenvios de si mismo = repeticiones (marcados).
- Reloj del broker confirmado de forma independiente: el NFP (8:30 NY) cae a las
  15:30 del servidor en invierno y en verano -> UTC+2 hasta 08/03, UTC+3 despues.
- Examen: una cesta real con recorrido bloqueado (hueco de cotizaciones, 21543)
  se contaba como "de mas"; ahora se compara solo en neto. Agosto canal1:
  28/28, neto mediana -2,8 %, DD -2,6 %.
- Replay historico validado: Dubai 24-28/08 28/28 (114 vs 117 EUR), 21-25/09
  18/18 (55 vs 70); Gold 21-25/09 35/37 (386 vs 372). DD ~9 % por debajo del real
  en modo historico (retraso sorteado).
- Caida real del 29/01 (5.545 -> 5.102 en 50 min, ticks continuos) con 5 BUY de
  Dubai seguidos.

Resultado (research/signal_study*.py, runtime_data/signal_study_v1/):
- Precio de entrada del estudio = el real del bot (mediana 0 $, 46 entradas).
- Sin ventaja direccional medible en ningun canal: la senal gana la "carrera"
  +-3/5/10 $ el 45-48 %; mediana a 15/60/240 min ~0. Placebos (misma hora
  direccion contraria; misma direccion hora al azar) dan lo mismo.
- Por meses no hay mejora clara reciente (percepcion de Jose no confirmada).
- Estrategias actuales sobre toda la muestra (broker C): Dubai -208 EUR,
  Gold -15 EUR. Con placebos: Dubai +214 (contraria) / -430..-700 (azar);
  Gold -993 (contraria) / -1.063..-1.416 (azar). Gold real mejor que los 4
  placebos pero dentro del ruido.
- Consecuencia: lo que gane una estrategia vendra de la gestion (forma del
  pago) y no de la informacion de la senal; toda candidata debe pasar el control
  placebo (ganar claramente mas con senales reales que con entradas al azar).

## 18. Canal 2 reconstruido como lo cuenta el propio canal (26/09 noche)

Idea de Jose: acercarse a los resultados que publica el canal 2. Fuente: diario
del bot (todas las versiones y respuestas, 3/07-25/09): 581 senales (499 "NOW",
82 "ZONE" que el bot no opera). research/gold_channel_*.py,
runtime_data/gold_channel_v1/.
- Publican la zona ~12 s despues de "Buy Gold Now" (p90 26 s), TP/SL ~11 s
  despues de la zona; zona ~5 $. Cuentan "pips from best entry" (extremo de la
  zona), 1 pip = 0,10 $.
- Erratas corregidas por edicion (zona 4132 con precio 4031, corregida a los
  30 s) y ediciones de senales viejas dias despues: se usan solo versiones de
  los primeros 15 min y niveles creibles segun la cotizacion de ese momento.
- Seguir sus niveles tal cual pierde en todas las variantes (mercado, borde
  cercano, mitad, mejor entrada; TP1..TP4): -0,4 a -1,5 $/oz por operacion.
  TP1 se alcanza 70-76 %, pero SL ~10 $ vs TP1 ~3 $. Con su propia gestion
  (partes a TP1..TP4 + BE tras TP1): -0,6 $/oz, 71 % de operaciones en verde.
- Sus anuncios: en 103 de 458 senales el precio toco su SL antes que el TP1
  (91 por mas de 1 $ a precio medio); anunciaron SL en 27, silencio en 61 y
  "TP hit" en 15 (el precio volvio despues). Pips anunciados (aprox.) +2.693
  $/oz frente a -248 $/oz reales de un seguidor, 3/07-25/09.
- Conclusion: su historial publicado no es alcanzable siguiendo sus niveles;
  sobrestima por contar desde la mejor entrada y omitir SL. Cualquier ventaja
  habra que buscarla en subconjuntos de senales o en gestion propia, con el
  control placebo.

### 18b. Contraste pedido por Jose (26/09 22:40): con toda la gestion del canal

- Correccion: la cifra "103 SL antes de TP1 / -248 $" ignoraba la gestion del
  canal. En 80 de esas 103 hubo mensajes antes del SL (risk free, move/adjust/
  EXTEND SL, TPs edited). No era valida.
- Nuevo seguidor fiel (research/gold_channel_follow_full.py): 1a entrada a
  mercado al "NOW", 2 limites en la zona (mitad y extremo), partes a TP1..TP4,
  niveles vigentes en cada momento (erratas filtradas), gestion aplicada al
  recibirla (+1,5 s): mover/ampliar SL, niveles en respuestas, risk free solo
  cuando el broker lo permite ("when possible"), parciales (incl. "from top
  layers"), cerrar todo, "cerrar o risk free" probado de las dos formas;
  pendientes canceladas tras TP1. Trazas revisadas a mano (2784, 2840, 2903,
  2843, 481).
- Resultado 3/07-25/09, 417 senales NOW: -0,27 $/oz por senal (-114 $/oz en
  total), 52-55 % en verde; ZONE 31 senales -0,94. Por semanas: 4 positivas,
  9 negativas.
- Anuncios: 41 senales acaban con todo en SL (tras su gestion); anunciaron SL en
  25, silencio en 12, "TP" en 4. 101 senales con perdida >1 $/oz para el
  seguidor; SL anunciado en 25. Pips anunciados (aprox., max "+N" por senal):
  +2.693 $/oz.

## 19. Noche 26-27/09: herramienta de busqueda completa y primera busqueda (autonoma)

Jose: "procede con todo, resultados faciles de entender". El bot no se toca.
- Tope de perdida por cesta para Gold (stop_mode basket_money con TPs por pata,
  cierre de cliente a mercado; exige perfil de mercado en el motor rapido).
  Verificado: tope enorme = sin tope (49/49); motor rapido = lento (49/49);
  examen 21-25 identico; 540 tests OK.
- Plan B de Jose (555 + tope): EMPEORA siempre (tope 100 EUR: abr-jun -1.606
  vs -219; jul-sep -504 vs -62). La 555 gana el 90 % porque aguanta; el tope
  corta cestas que se recuperan.
- Filtros causales por senal (research/signal_features.py, signal_filters.py):
  hora, dia, High risk, posicion en rango 4h (soportes/resistencias), RSI 5/15m,
  tendencia EMA50 15m, ATR, movimiento previo 15/60m, movimiento del dia,
  minutos desde la senal anterior. 448 filtros (simples y pares).
- Juez: fiabilidad t = media/desv*raiz(n) por cesta, n minimo y positivo en las
  dos mitades del periodo de busqueda; liston de suerte = mejor t con senales
  placebo (hora al azar) sobre las mismas combinaciones; vecinos (cada parametro
  y umbral un paso) >= 70 % positivos; validacion jul-11/09 positiva con broker
  rapido, lento y muy lento y >= 50 % semanas positivas; seleccion congelada
  (hash) ANTES del examen final 14-25/09. Cuenta completa con flotante
  (research/account_sim.py): peor dia, peor caida, lote seguro para 10k (50 %
  de los limites, calculado solo con la busqueda), fondeo 2 fases (5 % diaria,
  10 % total, objetivos 10 % y 5 %, 4 dias minimo, 60 dias maximo) empezando
  cada dia, y Monte Carlo por dias.
- Periodos fijados: busqueda Dubai ene-jun, Gold abr-jun; validacion jul-11/09;
  examen final 14-25/09.
- Busqueda: 500 gestiones por canal (+ la actual) x 448 filtros, sobre senales
  reales, placebo e invertidas (search/stage1.py, pipeline.py,
  finalize.py). Resultados en search/ (contenedor) y resumen a la manana.

### 19b. Resultado de la primera busqueda (27/09 madrugada)

Informe completo: docs/development/resultados-busqueda-1.md; datos en runtime_data/search_run1/.
- Liston de suerte: Dubai t=5,28; Gold t=32,0. Finalistas: 6 Dubai y 4 Gold. Pasan todo: 1 y 1.
  Seleccion congelada con sha256 ec51013c... antes del examen final.
- Candidata Dubai #3 (gestion #106; filtro RSI15m<45 y rango 60m<25$):
  - validacion +74/+33/+58 EUR (rapido/lento/atascado), examen final +37 EUR, 9 de 9 meses positivos;
  - perdida acotada (-50 EUR por cesta al lote base), unas 10 cestas al mes.
  - Con senales de hora aleatoria el mismo tipo de combinacion tambien gana (9 de 10 finalistas placebo pasan la validacion):
    la ventaja es del contexto de mercado y de la gestion, no del proveedor.
- Candidata Gold #1 (gestion #434; filtro RSI5m<30 y ATR15m>8$), sin stop:
  - 0 perdedoras de 97, pero flotante de hasta -300/-340 EUR para ganar unos 6 EUR de media;
  - con todas las senales pierde -2.790 EUR (cesta peor -905);
  - un stop de 100-300 EUR o cierre por tiempo nunca mejora. NO recomendada para fondeo.
- Fondeo 10k con lote seguro: Dubai #3 da ~1,5 %/mes con caida de ~2-3 %.
  No llega a un reto tipo FTMO de 10 % en 60 dias; podria servir para fondeos sin limite de tiempo.
- Nada va a produccion. Propuesta: demo de Dubai #3 en paralelo, pendiente de decidir con Jose.

### 19d. Las 6 estrategias de la sombra, simuladas en local (27/09 tarde)
La sombra de la VM está parada desde el 30/08 (sección 6), así que se simularon en local con
`tools/shadow_week_replay.py`: reproducción histórica validada, solo disparadores, 3 brokers.
Salidas en `out/shadow_week/`.

La c490 necesita `own_rule_be_partial_v1` (break-even por precio), que aún no está validado contra
la realidad; ver plan-busqueda-2 §8d.

- **Semana 21–25/09** (neto €, broker rápido A / lento C / atascado C_p90):
  - dubai_balanced 116 / 55 / 28 (real ~70);
  - frontloaded_30m 98 / 72 / −2;
  - frontloaded_40m 118 / 92 / 16;
  - 555 382 / 386 / 355 (real 372);
  - b210 −22 / −22 / −27;
  - c490 119 / 122 / 175.
- **Todo 2026** (Dubai ene–25/09, Gold abr–25/09), broker lento: las 6 pierden.
  - dubai_balanced −217 (3 de 9 meses positivos);
  - frontloaded_30m −384; frontloaded_40m −424;
  - 555 −463 (peor cesta −419; 4 de 6 meses positivos);
  - b210 −700; c490 −939.

  Con broker rápido, las 6 también pierden.
- **Lectura:** la semana 21–25 fue buena para todas salvo b210, pero en el año ninguna gana. Una
  semana buena no dice nada; hace falta la búsqueda 2.

### 19e. Búsqueda 2 planificada (27/09 noche) — SIGUIENTE PASO
- **Mandato de Jose:** conseguir dinero; búsqueda muy amplia sin dejarse caminos; dos carriles:
  - fondeo (propuesta FTMO 2 fases);
  - cuenta propia con estrategias tipo 555.

  Entrega en formato "receta → dinero". Jose deja las riendas a Claude.
- **Plan de ejecución:** `docs/development/plan-busqueda-2-ejecucion.md`. Incluye:
  - un catálogo de vertientes con tabla de cobertura obligatoria;
  - un embudo en 3 niveles (calculadora, motor por familias, finalistas);
  - los hitos M0–M9.

  Contexto en `plan-busqueda-2.md`.
- **Empezar por M0.** Máximo 4 procesos (RAM).

### 19g. SIGUIENTE PASO (28/09 noche): plan de verificación y descubrimiento
**Empezar por `docs/development/plan-maestro.md`** (brújula, bloques 0/V/P/O/M/F/G y puerta de decisión).
El bloque P (entender a los traders) es nuevo.
- Ejecutar `docs/development/plan-verificacion-reto.md` desde el paso 0, con `docs/development/mapa-de-fallos.md`
  (48 posibles fallos; nada con dinero sin su evidencia).
- Orden: 0 → V1–V4 → O1–O6 (descubrimiento señal a señal + taller 555 → 666) → M1–M5 → F1–F2 → puerta de decisión.
- Decisiones de Jose:
  - suspenso ≤ 5 % y lo más rápido posible dentro de eso;
  - cuenta FTMO en EUR, tipo Standard, reto de 2 fases empezando por 10k;
  - producción = entorno de examen en una demo de Vantage de 10.000 € (Jose la abre);
  - sombra en tiempo real con el motor validado y comprobación nocturna;
  - demo de FTMO 2 semanas cambiando el bot.
- FTMO confirmó a Jose que se pueden usar las señales (guardar la respuesta escrita).
- Fondeo: `docs/development/fondeo-opciones.md`.

### 19f. Búsqueda 2 terminada (28/09) — SIGUIENTE PASO: examen semanal (M9)
- **Resultados:** `docs/development/resultados-busqueda-2.md`; informe visual
  `out/search2/final/informe_final.html`; recetas congeladas `search2/frozen_v1.json` (sha256 532661a9…).
- **R1 (canal 2):** escalera 6 × 0,01 cada 2 $, tope −100 €, asegurar +15/5, cierre con beneficio a
  los 3 min; solo si `asia_loc > 0.8`. +474/+553/+518 € (A/C/C_p90) en abr–sep, 6/6 meses.
  Placebo −177 €. Riesgo: deja de ganar si más del ~1,8 % de las cestas tocan el tope.
- **R2 (canal 1):** seguir, TP 10 $ / SL 40 $ / 4 h, solo si `rsi_m5 < 30`. +546/+545/+535 € en
  ene–sep, 8/9 meses.
- **Cartera recomendada para FTMO 100k:** R1 ×20 + R2 ×30. Pasa el 99–100 % (hasta un 1 % de topes)
  en 116–148 días. Estimación honesta ≈ 2.100 €/mes.
- **No funcionan:** la 555, invertir Dubai, elegir la mejor de miles, y el resto de vertientes (ruido).
- **Pendiente:**
  - M9, examen semanal en papel con las recetas congeladas;
  - pregunta a FTMO;
  - filtros nuevos en el bot, solo con OK de Jose.

### 19c. Traspaso a Claude Code (27/09 10:30)

El trabajo pasa del chat (nube) a Claude Code en el portatil. Todo copiado y verificado por md5
(1052 ficheros, 0 diferencias). Ver docs/development/traspaso-claude-code.md: preparacion
(tools/build_ticks_all.py reconstruye runtime_data/ticks_all), punto exacto donde se paro y
propuesta de busqueda 2 pendiente de acordar con Jose. Scripts de search/ ya protegidos para
Windows (main guard); verificado identico con procesos en modo spawn.
