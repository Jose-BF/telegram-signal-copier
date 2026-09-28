# Inventario Actual De Entrega Del Simulador

Actualizacion posterior 11/09 07:40 Madrid: usuario cancela la continuidad de
revisiones programadas. Automatizacion y capturas14/17 retiradas, sin tocar
el bot ni borrar evidencias. No estan Ready actualmente. Consultar primero
`../development/2026-09-11-simple-progress.md`: faltan contraste integral y
definicion de tolerancias, no solo esperar mas operaciones. Resto historico.

Actualizacion vigente 11/09: [reparaciones activadas](2026-09-11-operational-release.md).
Commit d9037c5c publicado/activo tras autorizacion del usuario y guardas sin
exposicion; 3191 pruebas de entrega minima aprobadas. Investigacion preservada
sin publicar. Nuevo protocolo viernes v3 2b41be5f91d3 y capturas14/17 Madrid
registradas/verificadas antes de la ventana, aun no ejecutadas. No hay cierre
nuevo de M3/M6 por activar el bot. Las secciones inferiores son antecedentes.

Fecha de corte anterior: 10/09/2026. Proyecto `telegram-signal-copier-gold-live`,
rama `feature/gold-555-live-trial`, HEAD
`892bc33c8f6c19be7248401ec3cf6a27ba3d0563`. Arbol de investigacion sucio
historico preservado. El HEAD no identifica por si solo estos cambios locales.

## Actualizacion De Entrega

Ultimo cierre, 19:52 UTC: [revision nocturna](2026-09-10-night-review.md).
15 posiciones adicionales y 20.94 EUR conciliados con fills nativos/FX causal;
no repetir las 19 anteriores. Ventana independiente de tarde INCOMPLETE:
cero Gold elegibles/evaluaciones, sin concordancia demostrada. Comparador
bloqueado por cinco productores sin admision explicita en el protocolo viejo.
Incidente conservado y registro completo comprobado en el freeze FUTURO
de viernes v2; capturas propias Ready14/17 Madrid, no ejecutadas aun.
M3/M6 siguen abiertos. Las 5093 pruebas mantienen sus 425 fuentes estables;
60 comprobaciones nuevas de auxiliares no certifican el motor completo.
Bot sin cambios, 892 limpio; investigacion y reparaciones siguen sin publicar.

Revision posterior a 13:45 UTC: consultar
[recogida comprobada y reparaciones](2026-09-10-collection-review-1515.md).
Seis senales, 19 posiciones y 38 deals reconciliados; 19/19 importes coinciden
con fills reales y FX causal. Tres reparaciones locales verificadas con
5093 pruebas completas y 425 fuentes estables. No estan publicadas ni activas.
No implica cierre de la ejecucion independiente, de la admision historica ni
de la escala. El estado estructurado v1 inferior es el corte anterior.

Corte posterior del 10/09, 11:22 UTC. La implementacion M7/S1 ya esta entregada
y revisada, con **5043 pruebas completas aprobadas** sobre 424 fuentes estables.
Tres controles reales: 141 evaluaciones y 141 de reproduccion, cero desacuerdos
y archivos identicos. El control de tres dias contiene 28 triggers raw y
cartera canonica sin bloqueos; no una busqueda ni evidencia OOS intacta.
Precaptura natural descargada y verificada; protocolo de cierre vigente v2
y tarea propia confirmada para las 17:00 Madrid. La v1 queda conservada tras
corregir el registro de ExitCode del wrapper, sin cambiar calculos o tolerancias.

Estado estructurado actual:
`runtime_data/calculator_delivery_20260910/delivery_readiness_v1.json`.
El JSON `capability_inventory.json` del directorio anterior conserva las
capacidades/evidencias del corte de partida y enlaza esa actualizacion.

| Requisitos | Estado actual y alcance |
| --- | --- |
| M1, M2, M4, M5 | Cerrados para el dominio inicial declarado y dinero hipotetico; no se habilita toda la gramatica ni se certifica contabilidad real |
| M7, S1 | Conexiones implementadas, revisadas y controladas con archivos reales y sinteticos; perfil conservado |
| D1 | Adaptador de cuatro exports cerrado; no implica ticks o historial completo admitido |
| D2, D3, D5 | Cerrados para el control Gold raw Jul28-30 de 60 minutos; todos los mensajes/bloqueos conservados, no admision global de los cuatro exports |
| D4 | Parcial: FX/costes/metadata como contrato hipotetico explicito; binding de cuenta y dinero historico no certificados |
| M3, M6 | Abiertos: trace integral y contraste natural; protocolo futuro Sep10 13:00-15:00 UTC congelado antes de resultados |
| S2, S4 | Propuesta preparada; periodo/reserva/espacio/budget definitivos y autorizacion conjunta pendientes |
| S3 | Reproduccion fija y reanudacion sintetica verificadas; escala/memoria de una busqueda masiva aun sin medir |

Son **10 cierres totales o acotados al dominio inicial y 6 parciales/pendientes**,
no una declaracion de que el historico entero esta admitido. La calculadora
diagnostica es utilizable; la seleccion de estrategia y el realismo monetario
siguen sin aprobar. Capital abierto, margen no simulado, candidatas nuevas cero.
Detalles en `2026-09-10-own-rule-integration.md`,
`2026-09-10-calculator-integration-regressions.md` y el plan autonomo.

## Corte De Partida

No hay que reconstruir el simulador. Ya existen motores escalar/rapido/oracle,
entrada causal, ciclo integrado de mercado y SL/TP, conversion monetaria,
cartera y controles reproducibles. Falta entregar y admitir el recorrido para
**estos JSON, estas reglas propias y estos horizontes**.

El inventario identifica **15 requisitos de entrega abiertos, de 16 inventariados**:
**7 de motor/recorrido utilizable, 4 de JSON+ticks y 4 de preparacion de busqueda**.
D1 esta cerrado. No son 15 bugs: **13 requieren control integral, evidencia o
decision y 2 requieren conexion de codigo**: M7 y S1. Los dos hallazgos del
comparador R1/R2 estan corregidos y verificados; M3 sigue abierto por full trace.
La implementacion offline de M7/S1 ya esta autorizada; no espera al permiso S4.
D1 cerrado tras revision y ejecucion propia del coordinador: archivo, cuatro
fuentes y dos hashes de implementacion verificados; no duplicados aqui.

Las **41 filas de capacidades** de abajo no se suman a esos 15 pendientes:
describen lo que existe y sus restricciones. Tampoco sumamos como pendientes
obligatorios opciones no solicitadas. Las capacidades ofrecidas por
`StrategyGenome` pero bloqueadas quedan expresas: no se ocultan ni se
consideran admitidas por tener una rama legacy.

**No hay admision integral ni autorizacion de busqueda.** Candidatas nuevas: 0.
No se ha ejecutado aqui comparacion integral, preparacion de los cuatro JSON,
suite completa, SSH/VM/MT5, red, ordenes ni coordinacion mediante otras tareas.
El bot recuperado y la publicacion de las reparaciones en 892 son antecedentes
operativos del 10/09, no evidencia adicional de simulacion offline.

## Evidencia Actual Y Cortes Historicos

Leidos `AGENTS.md`, `docs/development/research-contracts.md`, el mapa compartido,
el contrato de mercado y las auditorias Sep9 de mercado, preparacion futura y
reparaciones de tarde. Las tablas originales de
`2026-09-09-simulation-capability-matrix.md` son cortes independent_v1/v3:
**no** se arrastran como actuales sus faltas de ack, cierre a mercado o
integracion de protecciones, ni los tests de BE delayed/fixed stop/expiracion
que se agregaron despues.

- Suite completa anterior conservada: **4465 PASS**, cero fallos, errores y
  omitidos. XML inspeccionado, SHA-256
  `751ade7fcef8be2dbb235cfb09370ae70391364bc7b4ab9c0f264a51afc4f5f5`;
  `runtime_data/afternoon_repairs_20260909/full_suite_v1.xml`.
  El XML registra 217.668 s; la auditoria describe 217.73 s de ejecucion.
- Suite de integracion de mercado: **4416 PASS**, informe
  `runtime_data/simulation_foundation_20260909/market_verification_v1/final.json`,
  hash `60f9f0a115ab17473ddd9b327a78555d7989000d0dc9fddd7857b70d78b7ebba`.
  No es mas reciente que la anterior; aporta el manifiesto de motor/pruebas.
- Se compararon **87 fuentes pertinentes** con
  `market_verification_v1/start.json`: **87 identicas, 0 cambiadas**.
  La lista, hashes esperados/actuales y archivos de evidencia consultada
  estan en el JSON de este inventario. Esto permite reutilizar los controles
  estables; no afirma que todo el arbol coincida con aquel freeze.
- Control Gold integrado retenido: once senales, tres motores, **33 evaluaciones**,
  coincidencia de campos comparados y cero bloqueos de motor.
  `runtime_data/simulation_foundation_20260909/market_independent_v1/results.json`.
  Es retrospectivo y acotado: no toda la gramatica, no paridad MT5 prospectiva.
- Las **72 PASS del 10/09** constan en
  `docs/audits/2026-09-10-day-review-sequence.md`; verifican las reparaciones
  operativas publicadas, no nuevas funciones del simulador. No repetidas aqui.
- Entorno consultado: `py -3.14 --version` -> Python 3.14.2.
  Se reutiliza la suite estable. Revision adicional: 13 tests iniciales del
  comparador repetidos aqui, PASS en 0.79 s; dos contraejemplos en memoria.
  Ultimo agregado del coordinador: 41 PASS, incluidos 16 del comparador;
  revisados los tests adicionales, sin repetirlos ni generar sus temporales.
  Suite completa del coordinador terminada: **4511 PASS**, 402 fuentes sin
  cambios durante ella; XML `verification_v1/full_suite.xml` comprobado.
  Es anterior a los fixes R1/R2; despues pasan **101 pruebas afectadas**
  (`review_regressions.xml` inspeccionado). Motor sin cambios por esos fixes.

Las referencias a funciones y tests de las tablas son del codigo actual;
los resultados PASS se reutilizan del archivo comprobado, no se presentan
como una nueva ejecucion. Igualdad entre motores comprueba resultados bajo
los mismos supuestos, no la fidelidad de una simplificacion comun frente a MT5.

## Dominio Implementado, No Impuesto Al Usuario

La via integrada usa `schema_version=2`, entradas hipoteticas,
`MarketProfile` y `ProtectionProfile`, cliente serial por cotizaciones,
freeze=0 y precision comun XAU 0.01 / FX 0.00001 / lote 0.01 / moneda 2 decimales.
Stop permitido: `none|fixed_move`; target: `none|per_leg_steps`;
BE: `none`. Trailing, hard stop por leg, profit lock y cierres temporales
tienen rutas integradas. No se presupone que esta lista sea suficiente para
todas las reglas que el usuario quiera ofrecer: M1 exige declarar y revisar
la lista de admision, y M2 cerrar sus combinaciones.

Para el objetivo pedido, la gestion del proveedor se ignora, pero tambien
deben ser independientes sus campos stop/target/BE y filtros. El filtro
`min_reward_risk` actual usa niveles proveedor; no deduce RR de reglas propias.
No necesita faltar un SL proveedor para bloquear todo el estudio: esa
dependencia solo afecta a las reglas que realmente la usan.

El perfil intradia sin rollover y con costes cero **solo puede admitirse con
evidencia o etiquetarse como hipotesis diagnostica**. No se supone cero en todo
el historico ni se declara capital ilimitado. Si el alcance exige overnight,
comisiones no cero, BE/parciales u otras familias bloqueadas, eso genera trabajo
condicional explicito; no se cierra la entrega escondiendolas.

## Matriz De Capacidades

Estados: **Probado** = implementacion y pruebas pertinentes estables bajo el
alcance escrito, no certificado universal; **Requiere control integral** =
componente existente pero falta su cierre en el recorrido/perfil/dataset;
**Requiere implementacion** = conexion funcional ausente en la ruta de entrega;
**No soportado explicitamente** = no se admite ese uso, con impacto declarado.
Una opcion no solicitada no se convierte por ello en requisito obligatorio.

| ID | Funcion | Estado | Alcance e impacto en reglas | Codigo y evidencia actual |
| --- | --- | --- | --- | --- |
| C01 | Gramatica, limites, identidad y unidades por modo | Probado | Contrato, no admision automatica. Schema 2 para entradas hipoteticas con market; un genoma sintacticamente valido puede estar bloqueado por protecciones, precision, dinero o datos. | `research/dubai_iterative/contracts.py:StrategyGenome`; `research/dubai_iterative/contracts.py:SearchSpace`; `tests/test_dubai_iterative_contracts.py`; `tests/test_gold_iterative_contracts.py` |
| C02 | Entrada signal_market | Probado | Perfil integrado schema 2. BUY usa Ask, SELL Bid; request, fill y ack distintos. No equivale a fill historico observado. | `research/dubai_iterative/engine.py:_prepare_entries`; `research/dubai_iterative/market.py:MarketBook`; `tests/test_iterative_market.py:test_management_does_not_use_entry_before_ack`; `tests/test_oracle_market.py:test_entry_ack_then_delayed_market_close_and_ack` |
| C03 | Entrada delay | Requiere control integral | Regla probada; combinacion con perfil integrado por cerrar. entry_value son segundos. No faltan los bordes de expiracion legacy/schema2; falta admision del recorrido integrado elegido. | `research/dubai_iterative/engine.py:_prepare_entries`; `tests/test_iterative_entry_boundaries.py`; `tests/test_iterative_entry_fill_latency.py` |
| C04 | Entrada pullback | Requiere control integral | Regla probada; combinacion con perfil integrado por cerrar. entry_value es distancia de precio. No confundir con orden limit persistente del broker. | `research/dubai_iterative/engine.py:_prepare_entries`; `tests/test_iterative_capability_contracts.py`; `tests/test_iterative_entry_boundaries.py` |
| C05 | Entrada momentum | Requiere control integral | Regla probada; combinacion con perfil integrado por cerrar. Cruce causal; prueba independiente SELL y bordes BUY/SELL existentes. | `research/dubai_iterative/engine.py:_prepare_entries`; `tests/test_iterative_capability_contracts.py`; `tests/test_iterative_entry_boundaries.py` |
| C06 | Entrada adverse_reversal | Probado | Perfil Gold de control, no todas las combinaciones. entry_value y entry_confirmation_value son distancias. El control 555 no admite automaticamente toda la gramatica. | `research/dubai_iterative/engine.py:_prepare_entries`; `tests/test_gold_iterative_engine.py:test_555_buy_waits_for_adverse_move_and_reversal_before_entry`; `tests/test_gold_iterative_engine.py:test_555_sell_is_the_exact_directional_mirror`; `runtime_data/simulation_foundation_20260909/market_independent_v1/results.json` |
| C07 | No entrada explicita | Probado | Control de participacion cero. Conservar fila y motivo; no seleccionar por comparacion monetaria contra filas bloqueadas. | `research/dubai_iterative/engine.py:simulate`; `tests/test_iterative_capability_contracts.py`; `tests/test_gold_iterative_engine.py:test_no_entry_control_is_an_explicit_zero_participation_strategy` |
| C08 | actual_mt5 | No soportado explicitamente | No admitido con MarketProfile; sigue disponible condicionado. Fills reales carecen de evidencia de ack de entrada para este modo. No se necesitan para cada JSON disparador. | `research/dubai_iterative/market.py:market_blockers`; `tests/test_oracle_market.py:test_market_requires_explicit_protection_and_hypothetical_schema2`; `tests/test_iterative_protection.py:test_observed_fill_requires_explicit_initial_protection` |
| C09 | Ladders simultaneous/adverse/favourable | Probado | Cliente serial por cotizaciones; batches ya enviados conservados. Una nueva solicitud de ladder espera al siguiente quote tras ack; rechazo cancela solo futuras no solicitadas. entry_ladder_step es precio. | `research/dubai_iterative/engine.py:_prepare_entries`; `research/dubai_iterative/market.py:MarketBook`; `tests/test_market_parity.py`; `tests/test_fast_market.py:test_simultaneous_batch_keeps_requests_already_sent_before_rejection`; `tests/test_oracle_market.py:test_ladder_request_is_strictly_after_prior_ack_quote` |
| C10 | Numero de legs y volumen ejecutable | Probado | Min/max/step declarados y precision comun. volume_weights son lotes absolutos por leg, no porcentajes. No hay tope universal 0.04. Sin margin-check. | `research/dubai_iterative/contracts.py:SearchSpace`; `research/dubai_iterative/market.py:MarketBook.valid_volume`; `tests/test_iterative_market.py:test_invalid_volume_is_a_rejection_not_a_fill`; `tests/test_fast_market.py:test_rejected_entry_is_a_completed_nonfill` |
| C11 | SL fixed_move/none | Probado | Protecciones integradas. SL inicial procede del quote de solicitud, validado al procesar; niveles posteriores usan estado propio. SL se ejecuta al quote adverso, no se garantiza limite de perdida ante gap. | `research/dubai_iterative/protection.py:profile_blockers`; `research/dubai_iterative/engine.py:_effective_stop`; `tests/test_iterative_capability_contracts.py`; `tests/test_iterative_protection.py:test_initial_sl_remains_active_while_modify_is_pending`; `tests/test_oracle_market.py:test_requested_stop_and_own_fill_keep_distinct_price_cost_conventions` |
| C12 | TP per_leg_steps/none | Probado | Protecciones integradas. TP solo activo instalado; convencion de fill al nivel limit aunque haya gap favorable. target_steps son distancias por leg. | `research/dubai_iterative/engine.py:simulate`; `research/dubai_iterative/protection.py:ProtectionBook`; `tests/test_iterative_protection.py:test_target_crossed_before_installation_does_not_close_at_desired_tp`; `tests/test_portfolio_execution_prices.py` |
| C13 | Trailing | Probado | SL dinamico integrado. Intencion distinta de nivel instalado. Puede atravesar entrada, pero no sustituye semantica be_mode. | `research/dubai_iterative/engine.py:_effective_stop`; `research/dubai_iterative/protection.py:ProtectionBook.request`; `tests/test_gold_iterative_engine.py:test_555_trailing_stop_tightens_and_never_loosens`; `tests/test_protection_parity.py`; `tests/test_iterative_protection.py:test_retry_waits_for_ack_and_uses_latest_causal_intent` |
| C14 | hard_stop_eur_per_leg | Probado | Cierre a mercado por posicion. Umbral de moneda de cuenta; usa conversion. Es solicitud de cierre diferida, no garantiza perdida maxima exacta ni margen. | `research/dubai_iterative/engine.py:simulate`; `tests/test_fast_market.py:test_policy_risk_requests_delayed_market_close`; `tests/test_oracle_market.py:test_market_basket_rules_request_then_fill_then_ack` |
| C15 | Profit lock de cesta | Probado | Cierre a mercado del volumen restante. arm/giveback en dinero; pico que el cliente no conoce durante espera de ack no arma la regla. | `research/dubai_iterative/engine.py:simulate`; `tests/test_oracle_market.py:test_profit_lock_cannot_arm_from_peak_during_entry_ack_wait`; `tests/test_fast_market.py:test_policy_risk_requests_delayed_market_close` |
| C16 | Salida temporal schema 2 | Requiere control integral | Cierres integrados existentes; conjunto de modos por admitir. loss_only <=0, profit_only >0, non_negative >=0; none desactiva. Minutos desde primera apertura. No hay modo always schema2; time_exit_min no garantiza cierre. | `research/dubai_iterative/engine.py:_time_exit_applies`; `research/dubai_iterative/engine.py:simulate`; `tests/test_iterative_capability_contracts.py`; `tests/test_oracle_market.py:test_market_basket_rules_request_then_fill_then_ack` |
| C17 | Ignorar gestion del proveedor | Probado | Objetivo pedido: direccion/hora y reglas propias. provider_management_mode=ignore junto a stop/target/BE/filtros sin dependencia proveedor. No basta ignore si min_reward_risk sigue leyendo niveles. | `research/dubai_iterative/engine.py:_is_provider_close`; `research/causal_replay.py:compile_signals`; `tests/test_gold_iterative_engine.py:test_c490_loss_only_time_exit_does_not_require_provider_management`; `tests/test_causal_replay.py` |
| C18 | Cierres de proveedor exact/close_only/explicit_close_only | Requiere control integral | Soporte integrado, opcion no solicitada. Requiere eventos causales. exact y close_only comparten vocabulario amplio de cierre; no anunciar gestiones distintas. No bloquea estudio ignore. | `research/dubai_iterative/engine.py:_is_provider_close`; `research/dubai_iterative/engine.py:_provider_entry_cancellation_ns`; `provider_action_semantics.py`; `tests/test_oracle_market.py:test_provider_close_waits_for_entry_ack_and_cancels_remaining_plan`; `tests/test_dubai_iterative_engine.py:test_exact_provider_management_retains_legacy_close_vocabulary` |
| C19 | SL/TP/BE del proveedor | No soportado explicitamente | Fuera del perfil integrado y no solicitado. stop_mode=provider; target_mode=provider_per_leg/provider_target_all; be_mode=provider bloqueados por protection_policy_unsupported. Existen ramas legacy. Niveles ausentes no bloquean reglas propias. | `research/dubai_iterative/protection.py:profile_blockers`; `tests/test_fast_protection.py:test_unsupported_profile_families_fail_closed_before_entry` |
| C20 | Objetivos monetarios y stop de cesta | No soportado explicitamente | Ofrecidos por gramatica, no admitidos con protecciones. target_mode=fixed_basket y stop_mode=basket_money tienen calculo legacy probado, pero necesitan integracion y regresiones para ofrecerlos con market. No confundir con profit_lock/hard_stop ya integrados. | `research/dubai_iterative/protection.py:profile_blockers`; `research/dubai_iterative/engine.py:simulate`; `tests/test_iterative_capability_contracts.py`; `tests/test_oracle_protection.py:test_basket_money_remains_unsupported_upfront` |
| C21 | Target fixed_move de cesta | No soportado explicitamente | Ofrecido por gramatica, no admitido con protecciones. Distancia desde entrada media ponderada; no equivale sin mas a TP por leg. No convertir automaticamente a per_leg_steps. | `research/dubai_iterative/protection.py:profile_blockers`; `research/dubai_iterative/engine.py:simulate`; `tests/test_dubai_iterative_engine.py` |
| C22 | partial_runner | No soportado explicitamente | Cierre parcial de estrategia no integrado. Legacy: un parcial comun por leg al umbral monetario y runner; valida fraccion/step. No hay close parcial request/fill/ack. Varias posiciones completas con TPs distintos si estan soportadas y son otra regla. | `research/dubai_iterative/contracts.py:SearchSpace.validation_errors`; `research/dubai_iterative/protection.py:profile_blockers`; `research/dubai_iterative/engine.py:simulate`; `tests/test_dubai_iterative_contracts.py`; `tests/test_dubai_iterative_engine.py` |
| C23 | BE price/delayed/partial | No soportado explicitamente | Ofrecido por gramatica, no admitido con protecciones. Legacy probado; delayed usa minutos, price/partial distancia. partial cambia SL segun role != market_a, NO cierra volumen. Habilitarlos requiere integracion broker y controles. | `research/dubai_iterative/protection.py:profile_blockers`; `research/dubai_iterative/engine.py:_update_custom_be`; `tests/test_iterative_capability_contracts.py`; `tests/test_fast_protection.py:test_unsupported_profile_families_fail_closed_before_entry` |
| C24 | Filtros none/max_spread/time_window/max_volatility | Requiere control integral | Reglas existentes; integracion al estudio por cerrar. Spread/distancia en precio; time_window es limite superior hora UTC; volatilidad es rango mid de hasta cinco minutos del path disponible. Exigir lookback necesario, no suponer indicador ATR ni banda horaria general. | `research/dubai_iterative/engine.py:_context_allows`; `tests/test_iterative_capability_contracts.py`; `tests/test_dubai_iterative_engine.py:test_context_filter_can_reject_an_observed_mt5_entry_causally` |
| C25 | Filtro min_reward_risk | No soportado explicitamente | No corresponde a RR de reglas propias en uso solo direccion/hora. Lee TP/SL causales del primer template proveedor. Sin niveles rechaza entrada, no calcula RR del stop/target propios. Desactivarlo explicitamente o implementar otra semantica si se pide; no disfrazar rechazo como ausencia de oportunidad. | `research/dubai_iterative/engine.py:_context_allows`; `tests/test_iterative_capability_contracts.py` |
| C26 | pending_entry_policy none/until_expiry | Probado | Continuacion de plan, no nuevo ciclo. Permite leg pendiente tras flat temporal antes de expiry; cierre de cesta cancela plan. No usarlo como reentrada nueva. | `research/dubai_iterative/engine.py:_pending_entries_remain`; `research/dubai_iterative/engine.py:simulate`; `tests/test_gold_iterative_engine.py:test_555_temporary_flat_basket_can_fill_a_later_pending_leg`; `tests/test_fast_market.py:test_no_pending_plan_cannot_reopen_while_waiting_for_ack`; `tests/test_oracle_market.py:test_passive_exit_cancels_none_policy_even_while_entry_ack_is_pending` |
| C27 | Reentrada arbitraria, multiples ciclos/cooldown | No soportado explicitamente | No ofrecida como ciclo nuevo por la gramatica; opcion no solicitada. No reconstruir para la primera entrega; si una regla propia lo exige hay cambio de alcance e implementacion. | `research/dubai_iterative/contracts.py:StrategyGenome`; `research/dubai_iterative/engine.py:simulate`; `tests/test_gold_iterative_engine.py:test_555_temporary_flat_basket_can_fill_a_later_pending_leg` |
| C28 | Requests/fills/rejects/acks y modify/retry | Probado | Modelo de quotes serial, no cola universal. Volumen/proteccion inicial invalidos rechazan entrada; SL/TP modifica par atomico, instala antes de ack y reintenta tras ack+retry con intencion causal. No retries automaticos de entrada ni rechazos de cierre distintos de position_already_closed. | `research/dubai_iterative/market.py:MarketBook`; `research/dubai_iterative/protection.py:ProtectionBook`; `tests/test_iterative_market.py`; `tests/test_fast_market.py`; `tests/test_oracle_market.py`; `tests/test_protection_parity.py` |
| C29 | Colisiones, estado propio y fin incompleto | Probado | Interacciones cubiertas del perfil actual. Pasivo instalado antes de cierre pendiente y modificaciones; no doble dinero. Entrada en vuelo al intent de cierre de cesta bloquea market_close_with_entry_in_flight_unsupported. Fin sin ack conserva prefijo y bloquea; modify tardio de posicion ya cerrada no certificado. | `research/dubai_iterative/engine.py:simulate`; `research/dubai_iterative/market.py:MarketBook.due_closes`; `tests/test_iterative_market.py:test_installed_stop_wins_over_pending_market_close`; `tests/test_oracle_market.py:test_market_close_processes_while_later_entry_waits_and_both_acks_survive`; `tests/test_market_parity.py` |
| C30 | Tiempo, ordinal y presupuestos | Probado | Quotes ordenados; latencias separadas. latency_ms es observacion; entry_fill_latency_ms procesamiento entrada; otros delays procesamiento/ack/retry. Mantener ordinal aun con timestamp igual. Presupuesto agotado bloquea sin inventar salida. | `research/dubai_iterative/engine.py:ExecutionAssumptions`; `research/dubai_iterative/market_contract.py`; `research/dubai_iterative/protection_contract.py`; `tests/test_iterative_timestamp_precision.py`; `tests/test_iterative_entry_boundaries.py`; `tests/test_iterative_market.py:test_missing_fill_cannot_relocate_request_to_invalid_equal_timestamp_quote` |
| C31 | P/L por fill, conversion y redondeo | Probado | Hipotesis de costes declarada y contrato monetario admitido. Separar movimiento de precio, pips y moneda. FX causal por signo/orientacion; dinero a centimos por realizacion. No llamar observado a P/L hipotetico ni EUR si contrato no es EUR. | `research/dubai_iterative/engine.py:_money_minor`; `research/dubai_iterative/fast_engine.py`; `research/dubai_iterative/oracle.py`; `tests/test_dubai_iterative_engine.py:test_stale_conversion_at_exit_blocks_money_claim`; `tests/test_fast_market.py:test_market_money_uses_existing_fixed_point_conversion_and_swap`; `tests/test_portfolio_execution_prices.py` |
| C32 | Rollover/swap | No soportado explicitamente | Existe por posicion; no admitido como cartera nocturna integral. Motor/loader calculan swap nativo con eventos; cartera recompone dinero bruto por fill sin ese devengo y puede bloquear exit_money_mismatch. Overnight requiere datos historicos y conexion/validacion de cartera, no se resuelve suponiendo cero. | `research/dubai_iterative/dataset.py:_build_rollover_events`; `research/dubai_iterative/engine.py:_allocate_position_swap`; `broker_money.py`; `research/dubai_iterative/portfolio.py:_result_slices`; `tests/test_fast_market.py:test_market_money_uses_existing_fixed_point_conversion_and_swap`; `tests/test_dubai_iterative_portfolio.py` |
| C33 | Comisiones/fees no nulos | No soportado explicitamente | Loader exige observed_zero_intraday; controles cero solo hipoteticos. No hay admision general de comision/fee no cero para variantes. Si fuentes prueban cero en cohorte intradia, cerrar control; si no, falta dato y/o soporte de costes, sin sustituir por slippage. | `research/dubai_iterative/dataset.py:load_strategy_dataset`; `research/dubai_iterative/portfolio.py:_result_slices`; `tests/test_dubai_iterative_dataset.py` |
| C34 | Cartera: union de posiciones, equity, DD, exposicion | Probado | Reconstruccion retrospectiva de trayectorias con ciclos completos. Cinta canonica, orden duplicados y neto reconciliado; equity de P/L relativo a cero, no saldo inicial ni ejecucion dependiente de saldo. Bloquea posiciones abiertas al corte; no reiniciar por semana ni contabilizar cierre ficticio. | `research/dubai_iterative/portfolio.py:reconstruct_portfolio`; `research/dubai_iterative/portfolio.py:build_portfolio_tape`; `tests/test_dubai_iterative_portfolio.py`; `tests/test_portfolio_execution_prices.py` |
| C35 | Capital y perdida configurada | Probado | Envolvente de mercado continuo, no modelo de cuenta financiada. Volumen y concurrencia real/configurada; stop_mode=none bloquea unbounded_strategy_stop incluso si hay trailing/hard_stop. Es limite del gate actual, no prueba de perdida infinita de toda regla. Sin margen, stop-out, insolvencia ni garantias ante gaps. | `research/dubai_iterative/risk.py:assess_capital_risk`; `tests/test_dubai_iterative_risk.py` |
| C36 | Precision comun de motor rapido y cartera | Probado | XAU 0.01, FX 0.00001, lotes 0.01, moneda 2 decimales. Contract_size entero positivo; perfil stops/freezes en points y point=10^-digits. Fast falla ante precision no representable; cartera redondea fixed point, no usarla sola como validador de precision. No ampliar a cualquier broker. | `research/dubai_iterative/fast_engine.py:_compile_path`; `research/dubai_iterative/fast_engine.py:_fixed_scalar`; `research/dubai_iterative/portfolio.py:_portfolio_contract`; `tests/test_fast_protection.py:test_profile_requires_two_digit_fixed_point_prices`; `tests/test_portfolio_execution_prices.py` |
| C37 | JSON historicos a disparadores causales | Requiere control integral | Adaptacion y admision de estos cuatro archivos a cargo del otro frente. D1 cerrado: adaptador y bridge verificados por coordinador con hashes de cuatro fuentes y codigo. publication_initial 36 Dubai/0 Gold; revision_time 441 Dubai/453 Gold. D2-D5 abiertos: elegir reloj y admitir causalidad/ticks/dinero/horizonte; cifras son disparadores no operaciones. | `research/causal_replay.py:compile_signals`; `research/gold_iterative/dataset.py`; `tools/prepare_gold_study.py`; `tests/test_causal_replay.py`; `tests/test_prepare_gold_study.py`; `docs/development/2026-09-08-simulation-foundation-readiness.md`; `docs/audits/2026-09-10-telegram-export-admission.md` |
| C38 | Proveniencia, checkpoints y oracle independiente | Probado | Infraestructura existente, freeze concreto por cerrar. No hay que reconstruir buscador/oracle. Cambios de codigo/perfil/datos invalidan reanudacion. Hash no prueba calidad ni continuidad del dato. | `research/iterative_provenance.py`; `research/dubai_iterative/search.py`; `research/dubai_iterative/certification.py`; `research/dubai_iterative/oracle.py`; `tests/test_iterative_provenance.py`; `tests/test_oracle_market.py:test_oracle_market_lifecycle_never_imports_scalar_execution_implementations` |
| C39 | Perfil integrado en comandos de busqueda y mundos | Requiere implementacion | Bloqueo antes de busqueda, no del calculo offline fijo. CLI construye ExecutionAssumptions sin market/protection/entry_fill_latency_ms; mundos Gold secundarios recrean defaults y pierden perfil. Conectar perfil y dominio compatible en todos los mundos antes de buscar. | `research/gold_iterative/__main__.py:_execution_validation_worlds`; `research/dubai_iterative/__main__.py`; `tests/test_gold_iterative_cli.py`; `tests/test_dubai_iterative_cli.py` |
| C40 | Ruta utilizable para periodo y reglas propias | Requiere implementacion | Orquestacion reutilizando componentes, no motor nuevo. Arnes actual es cohorte fija Gold 555 del 09/09 con hasta 11 senales; no es interfaz generica para cuatro JSON y genoma propio. Falta entregar enlace acotado adaptador->perfil->reglas->motores->cartera->informe sin alterar freeze historico. | `tools/prepare_simulator_forward.py:ready`; `tools/prepare_simulator_forward.py:_simulate`; `tools/run_simulator_forward.py`; `research/causal_replay.py:make_path`; `tests/test_prepare_simulator_forward.py`; `tests/test_run_simulator_forward.py` |

| C41 | Cotizacion de referencia inicial y seleccion de ticks | Requiere control integral | Regla de observacion declarada, distinta de latencia de broker. make_path comienza en primer tick >= observed_at; live puede usar snapshot del ultimo tick anterior disponible. Cambia umbrales y decisiones (2628), no corregir con seis minutos de latencia. Declarar referencia/antiguedad/lookback por estudio y probarla; no afirmar exact live. | `research/causal_replay.py:make_path`; `research/dubai_iterative/engine.py:_prepare_entries`; `docs/audits/2026-09-10-market-factual-comparison.md`; `runtime_data/simulator_delivery_20260910/market_factual_comparison_v1.json` |

## Interacciones, Dinero Y Ultimo Contraste

- Request/fill/ack son distintos; pasivos instalados siguen activos durante
  esperas. SL/TP rechazado conserva el par anterior; retry tras ack+pausa.
  SL ejecuta al quote; TP mantiene convenio limit. Un close pendiente no borra
  posicion y un SL previo no permite doble realizacion.
- Timestamp igual no equivale a mismo quote: conservar ordinal. Close/modify
  requieren quote posterior incluso con delay cero. Cierre de cesta con entrada
  en vuelo bloquea; fin sin quote/ack conserva prefijo, no inventa salida.
- Tiempo schema2: `none` desactiva; `loss_only` incluye cero,
  `profit_only` no, `non_negative` si. No existe `always` schema2.
  Un horizonte cargado no garantiza cierre ni autoriza reset semanal.
- Precio, pips, lotes y moneda son unidades distintas (tabla). FX causal,
  redondeo por realizacion y slippage una sola vez. Mantener separados
  `money_contract_verified` y `account_currency_money_verified`.
- Cartera es union de trayectorias/P-L relativo a cero, no saldo financiado
  ni rechazo por margen. Riesgo continuo no garantiza perdida maxima ante gap.
  `stop_mode=none` bloquea su gate aunque haya trailing/hard stop.
- Ya hay swap por posicion, pero no cartera nocturna integral; puede aparecer
  `exit_money_mismatch`. Costes cero historicos necesitan evidencia;
  no sustituir comision desconocida por spread/slippage.

**Control actualizado del coordinador:** `market_independent_v2` bajo
`runtime_data/simulator_delivery_20260910/`: 11 senales, 33 evaluaciones,
cero discrepancias entre motores/cero bloqueos. Se repitio por cambio de
`runtime_paths.py`; no se reutilizo la identidad previa. Comparacion factual
`market_factual_comparison_v1.json`: **11 mismatch + 7 Dubai out_of_scope**,
59 posiciones/18 senales conservadas; Gold **45 reales frente a 46 simuladas**.
Hash `12c83ae1706840b6f31a8adefb312f6aadde934c6ff4310f1988ce558ddc2c29`.
Es retrospectivo, no OOS ni full parity. Las listas heredadas del control viejo
no son una nueva lista de funciones ausentes.

| Caso | Causa e impacto |
| --- | --- |
| 2590 | Entrada extra slot4 06:37:20.656 UTC: bifurcacion del ladder por fill/ancla. No compensar con otra posicion ni ajustar latencia al caso. |
| 2628 | Entrada +346494 ms, **no seis minutos de latencia broker**. Raw 13:28:15.241; primer quote posterior 13:28:15.325 Ask 4400.28, umbral 4399.28; minimo previo al fill real 4399.29, no arma. Live usa tick anterior 13:28:15.085 Ask 4400.41, umbral 4399.41, arma a 4399.33 y confirma a 4400.85. Familia C41: referencia/seleccion de quotes. `first_structural_divergence=null` NO significa diferencia menor. |
| 2640 | Slot1: expert real 16:52:29.929 vs TP simulado 16:56:18.671. Mensaje2642 "Market is volatile / Be ready to close overall profit" se compilo `MOVE_SL_TO_BE`, direct, no CLOSE_ALL. Politica explicit_close_only con BE none lo ignora. Diferencia semantica proveedor/live, no defecto a corregir forzando una orden del texto ambiguo. |

Deltas verificados en el JSON; texto/accion 2642 comprobados en raw y
`independent_v3/input_diagnostics.json`. Detalle watch/ticks 2628 y atribucion
2590: evidencia del coordinador en
`docs/audits/2026-09-10-market-factual-comparison.md`, no reextraida aqui.
`make_path` confirma seleccion de primer quote posterior. Estas divergencias
impiden full parity live; permiten declarar otro modelo de observacion para
reglas propias, pendiente de su admision. No exigir gestion proveedor a ignore.

## Revision Independiente Del Comparador

**R1 y R2 cerrados y verificados el 10/09**, sin cambios de codigo por este frente.

- R1: `tools/run_market_controls.py` publica contrato v2 y liga resultado al
  SHA del protocolo congelado/reverificado. El comparador exige ese hash de
  bytes y rechaza v1 sin vinculacion. Protocolo real con genoma/perfil cambiado:
  rechazado; no se sobrescriben controles antiguos.
- R2: helper comun mapea `initial_sl->sl` y `initial_tp->tp`.
  Cinco regresiones dirigidas repetidas aqui: **5 PASS**, incluidas mutaciones,
  legacy y ambos motivos iniciales. Test CLI de perfil cambiado tambien revisado.
- Control nuevo `market_independent_v3_bound` terminado: vinculo SHA e identidad
  actual comprobados aqui, **33 evaluaciones, cero desacuerdos/cero bloqueos**.
  No se reejecuto el control ni la suite completa.
- M3 permanece **parcial**, con comparacion de hechos disponible pero sin
  certificacion de full trace ni full parity/OOS. R1/R2 ya no son pendientes.

## Lista Finita De Cierre

Cada ID se cuenta **una vez** aunque aparezca en varias capacidades.
Los responsables son limites de coordinacion, no nuevas tareas creadas.
M3/M6 usan la comparacion del frente principal; D1-D4 reciben lo que prepare
el frente de JSON y la evidencia local que corresponda.

### M1. Fijar dominio ejecutable y unidades

- Estado: **Requiere control integral**. Tipo: `configuracion_y_admision`. Responsable: `coordinacion`.
- Cierre: Lista exacta de campos/modos y combinaciones admitidos con limites de volumen, precision, costes, horizonte y cliente serial; los no soportados visibles, sin desactivar market para admitirlos. Incluir politica de quote de referencia (posterior vs snapshot anterior), antiguedad admisible y lookback.
- Hoy: Si se acepta explicitamente el dominio; no habilita las familias bloqueadas.
- Evidencia/ruta: `research/dubai_iterative/contracts.py`; `research/dubai_iterative/protection.py:profile_blockers`; `research/dubai_iterative/market.py:market_blockers`.

### M2. Cerrar controles de reglas e interacciones habilitadas

- Estado: **Requiere control integral**. Tipo: `evidencia_motor`. Responsable: `coordinacion`.
- Cierre: Para cada modo habilitado y su interaccion relevante: resultado conocido, BUY/SELL donde cambie signo, entrada/ack, SL/TP/trailing/cierre, rechazo, igualdad temporal y fin incompleto; mismos campos en tres motores. Reusar casos estables; agregar solo los que falten al perfil elegido.
- Hoy: Si cobertura restante pasa; no exige probar todas las combinaciones imaginables.
- Evidencia/ruta: `tests/test_iterative_capability_contracts.py`; `tests/test_market_parity.py`; `tests/test_protection_parity.py`.

### M3. Control Continuo Independiente Y Primera Divergencia

- Estado: **Requiere control integral**. R1/R2 cerrados, no pendientes de codigo.
- Comparacion factual realizada; falta secuencia completa de decisiones/requests/modifies/rejects/acks y admision causal. No confundir hechos con full trace.
- Evidencia: `tools/run_market_controls.py`, `tools/compare_market_controls.py`, `market_independent_v3_bound/results.json` bajo `runtime_data/simulator_delivery_20260910/`; auditoria factual del coordinador.

### M4. Cerrar dinero por operacion bajo supuestos identicos

- Estado: **Requiere control integral**. Tipo: `evidencia_motor_y_contrato_monetario`. Responsable: `coordinacion`.
- Cierre: Comprobar unidades, BUY/SELL, FX ganancia/perdida, step/rounding, gap TP/SL, slippage una sola vez, rechazos sin dinero y cierres diferidos; distinguir coste hipotetico de contabilidad observada. Fuentes historicas en D4.
- Hoy: Si pasan controles con contrato intradia soportado; no admite automaticamente overnight ni costes no cero.
- Evidencia/ruta: `tests/test_fast_market.py:test_market_money_uses_existing_fixed_point_conversion_and_swap`; `tests/test_portfolio_execution_prices.py`.

### M5. Cerrar cartera continua y limites de riesgo

- Estado: **Requiere control integral**. Tipo: `evidencia_integral`. Responsable: `coordinacion`.
- Cierre: Reconciliar suma de fills y union en cinta canonica con solapes; equity abierta/DD/exposicion, filas no ejecutadas, posiciones abiertas/incompletas visibles y cortes semanales sin reset. Declarar riesgo continuo y exclusiones; no afirmar margen/solvencia. Si se usa gate de capital, controlar stop_mode=none.
- Hoy: Si pasan controles intradia aplicables; cambiar politica por saldo requeriria otro soporte.
- Evidencia/ruta: `research/dubai_iterative/portfolio.py`; `research/dubai_iterative/risk.py`; `tests/test_dubai_iterative_portfolio.py`; `tests/test_dubai_iterative_risk.py`.

### M6. Contrastar incertidumbre de ejecucion fuera de reparacion

- Estado: **Requiere control integral**. Tipo: `evidencia_independiente`. Responsable: `frente_principal_comparacion_y_coordinacion`.
- Cierre: Modelo y tolerancias por magnitud fijados antes de evidencia no usada para reparar; diferencias materiales explicadas o bloqueadas. Cohorte fallida del 09/09 no se renombra prospectiva. Sin ello solo modelo hipotetico comprobado, no realismo integral.
- Hoy: Solo si existe evidencia independiente suficiente y criterios previos; no depende de reconstruir historico ni de esperar nuevos fills para cada estrategia.
- Evidencia/ruta: `docs/audits/2026-09-09-market-morning-preparation.md`; `docs/audits/2026-09-09-afternoon-observation-repairs.md`; `tools/prepare_simulator_forward.py`.

### M7. Entregar ejecucion acotada de reglas propias e informe

- Estado: **Requiere implementacion**. Tipo: `conexion_de_componentes`. Responsable: `implementacion_offline_ya_autorizada`.
- Cierre: Una entrada local parametrizada para datos admitidos, fechas, genoma y perfil; reutiliza compilador/motores/cartera, conserva identidad y todas las filas, expone eventos/resultados/bloqueos. Control fijo reproducible con busqueda=0. No reutilizar el freeze 09/09 como generico.
- Hoy: Requiere conexion de codigo y verificacion, no solo aprobar pruebas ya existentes. Autorizacion local ya concedida; no esperar S4 para implementar.
- Evidencia/ruta: `tools/prepare_simulator_forward.py`; `tools/run_simulator_forward.py`; `research/causal_replay.py`.

### D1. Adaptacion De Los Cuatro JSON: Cerrada

- Estado: **Probado / cerrado**. Adaptador, loader y bridge entregados; 50 PASS.
- Coordinador verifico archivo + cuatro fuentes SHA + dos hashes actuales parser/adaptador; no es solo afirmacion del autor.
- Bridge confirmado: publicacion 36 Dubai/0 Gold; revision 441 Dubai/453 Gold. No son operaciones; D2-D5 siguen abiertos.
- Evidencia: `docs/audits/2026-09-10-telegram-export-admission.md`; `research/telegram_export.py`; `runtime_data/historical_admission_20260910/manifest.json`.

### D2. Admitir disparadores, reloj y contenido causal

- Estado: **Requiere control integral**. Tipo: `datos_historicos`. Responsable: `frente_preparacion_4_json`.
- Cierre: Direccion/hora/simbolo/identidad una vez por disparador; distinguir publicacion de recepcion hipotetica, ediciones finales, media y duplicados; Gold historico separado de actual; listar cada ambiguo con impacto en reglas.
- Hoy: Si las fuentes permiten la decision; mensajes originales nunca guardados no se inventan.
- Evidencia/ruta: `research/causal_replay.py:compile_signals`; `research/gold_iterative/dataset.py`.

### D3. Admitir Bid/Ask y orden durante cada horizonte

- Estado: **Requiere control integral**. Tipo: `datos_historicos`. Responsable: `frente_preparacion_4_json_y_coordinacion`.
- Cierre: Cobertura por senal/horizonte/lookback y ultimo ack, UTC/offset, sidecars+hash y secuencia; huecos conocidos visibles; no sustituir por velas ni repetir cotizaciones como observadas.
- Hoy: Solo filas con precios locales suficientes; las ausencias reales necesitan datos, sin descarga en este frente.
- Evidencia/ruta: `mt5_tick_cache.py`; `research/dubai_iterative/dataset.py:VerifiedParquetTickSource`; `docs/development/2026-09-08-simulation-foundation-readiness.md`.

### D4. Admitir metadatos, conversion y costes historicos

- Estado: **Requiere control integral**. Tipo: `datos_historicos`. Responsable: `coordinacion`.
- Cierre: Simbolo/contrato/digits/point/min-max-step/stops/freeze y cuenta/moneda por periodo; FX causal y contrato de frescura; comision/fee cero demostrado o hipotesis diagnosticada. Overnight no admitido por defecto; evidencia actual no certifica todo 2023-2026.
- Hoy: Si existen fuentes del periodo; de faltar FX/metadatos/costes no lo arregla un test. Costes no soportados requieren implementacion adicional condicional.
- Evidencia/ruta: `research/dubai_iterative/dataset.py`; `broker_money.py`; `docs/audits/2026-09-09-afternoon-observation-repairs.md`.

### D5. Emitir admision por capacidad, cohorte y horizonte

- Estado: **Requiere control integral**. Tipo: `admisibilidad_datos`. Responsable: `coordinacion`.
- Cierre: Tabla completa elegible/admitida/bloqueada/no ejecutada, motivos y dependencias por reglas; incluir finales abiertos sin cierre inventado. No descartar solo perdedoras, huecos o senales con incidentes; no exigir SL/TP proveedor a reglas independientes.
- Hoy: Si D1-D4 estan resueltos para un subconjunto explicitamente delimitado; el resto sigue contabilizado.
- Evidencia/ruta: `tools/prepare_gold_study.py`; `docs/development/research-contracts.md`.

### S1. Conectar perfil y admision al buscador y todos sus mundos

- Estado: **Requiere implementacion**. Tipo: `conexion_de_componentes`. Responsable: `implementacion_offline_ya_autorizada`.
- Cierre: CLI/evaluador/oracle y mundos conservan market/protection/fill latency; generacion/mutacion/seeds solo dentro de dominio admitido o rechazo explicito previo. Nada de fallback legacy para eludir bloqueos. Identidad checkpoint incluye perfil y codigo nuevos.
- Hoy: Requiere codigo y regresiones; el buscador, Pareto y checkpoints existentes se reutilizan. Autorizacion local ya concedida; no esperar S4 para implementar.
- Evidencia/ruta: `research/gold_iterative/__main__.py:_execution_validation_worlds`; `research/dubai_iterative/__main__.py`; `research/iterative_provenance.py`.

### S2. Fijar experimento, reserva, coste/riesgo y presupuesto

- Estado: **Requiere control integral**. Tipo: `decision_previa`. Responsable: `coordinacion_y_usuario`.
- Cierre: Canales/periodos y uso previo, desarrollo/challenge/reserva no contaminada; reglas/volumen/capital si procede, costes/escenarios/metricas, presupuesto y parada. Acordar tratamiento del sesgo de multiples intentos antes de conclusiones estadisticas; no imponer capital para diagnosticos.
- Hoy: Puede prepararse hoy sin explorar rentabilidad ni consumir reserva.
- Evidencia/ruta: `docs/development/research-contracts.md`; `docs/development/2026-09-08-simulation-foundation-readiness.md`; `research/dubai_iterative/statistics.py`.

### S3. Verificar reproducibilidad y coste del recorrido final

- Estado: **Requiere control integral**. Tipo: `control_operativo_offline`. Responsable: `coordinacion`.
- Cierre: Control fijo pequeno con implementacion/datos/perfil congelados: mismo resultado, archivo inmutable, interrupcion/reanudacion sin duplicados ni mezclas, estimacion de tiempo/memoria y gates de dinero/oracle/cartera. No nueva exploracion de candidatas.
- Hoy: Tras M7/S1 y datos admitidos; no repetir contratos caros estables sin cambio.
- Evidencia/ruta: `research/iterative_provenance.py`; `research/dubai_iterative/search.py`; `tests/test_iterative_provenance.py`.

### S4. Revision conjunta y autorizacion de busqueda

- Estado: **Requiere control integral**. Tipo: `decision_usuario`. Responsable: `usuario_y_coordinacion`.
- Cierre: Presentar dominio, pendientes/exclusiones, datos, evidencia y presupuesto. Autorizacion expresa antes de ejecutar busqueda masiva; no implica publicar bot ni promover ganadora.
- Hoy: No se da por autorizada en este inventario; presupuesto actual de candidatas nuevas 0.
- Evidencia/ruta: `docs/development/2026-09-08-simulation-foundation-readiness.md`.

## Tres Puertas De Entrega

| Puerta | Lista requerida finita | Lo que autoriza y lo que no |
| --- | --- | --- |
| Motor listo dentro de alcance | M1-M7 cerrados, con implementacion/perfil identificados y controles del dominio declarado | Recorrido utilizable y contrastado en ese dominio; no todas las opciones de StrategyGenome ni cualquier broker. Si M6 sigue abierto: solo motor hipotetico comprobado, no realismo integral. |
| JSON+ticks listos | D1-D5 cerrados para las filas/capacidades/horizontes declarados | Simular esa cohorte, incluyendo registro de sus bloqueados. No certifica todo 2023-2026 por tener cuatro archivos o sus fechas extremas. |
| Busqueda lista | Puertas anteriores + S1-S4 cerrados | Solo el experimento/presupuesto revisado conjuntamente. Hoy NO; ninguna promocion/publicacion/live queda autorizada. |

El primer cierre integral no exige rentabilidad ni una candidata ganadora.
Si M2/M3 revelan defectos, se registran y reparan con regresion dentro de su
alcance: un test fallido no crea una nueva meta difusa, ni se descarta el caso.
Si aparecen nuevas reglas pedidas expresamente, se versiona esta lista;
el recuento de 15 no pretende predecir fallos aun no observados.

## Que Puede Cerrarse Hoy

- **Por controles/configuracion, sin reconstruir motor:** M1, M2, M4 y M5,
  si los controles restantes del dominio elegido pasan. M3 ya tiene contraste
  factual y R1/R2 cerrados; falta cierre integral, no esta aprobado por este inventario.
  Es posibilidad, no afirmacion de cierre ni promesa de duracion.
- **Datos:** D1 cerrado. D2-D5, solo donde las exportaciones preparadas y
  las fuentes locales permitan demostrar direccion/hora, cobertura Bid/Ask,
  conversion, metadatos y costes para el horizonte. No conocemos aqui un
  numero nuevo de ticks o senales ausentes; no duplicamos ese inventario.
- **Necesita conexion de codigo:** M7, ruta parametrizada para reglas propias;
  S1, perfil y dominio conservados en buscador y escenarios. No basta la suite
  actual para declarar hechos esos dos enlaces. Ya estan autorizados offline.
  R1/R2 del comparador ya estan cerrados. Pueden ser trabajos pequenos
  de orquestacion, pero siguen siendo implementacion pendiente.
- **Necesita evidencia independiente:** M6. Las reparaciones retrospectivas
  y la cohorte de manana invalidada no se transforman en validacion posterior.
  Puede reutilizar evidencia retenida verdaderamente independiente si satisface
  criterios fijados antes, sin pedir capturar cada variante en real.
- **Antes de buscar:** preparar S2; S3 solo despues de conectar recorrido/perfil
  y admitir inputs; S4 exige revision conjunta expresa. El control pequeno
  usa reglas fijas, no una nueva busqueda disfrazada.
- **Solo si se solicita o los datos lo exigen:** integrar familias C19-C23,
  RR de reglas propias, overnight/costes no cero, freeze no cero o carrera
  cierre/entrada en vuelo. Margen/stop-out, fills parciales broker, requotes,
  cliente concurrente y nueva reentrada no son reconstrucciones obligatorias
  para el estudio de disparadores dentro del dominio declarado. No se promete
  que soporten una regla que dependa de ellos.

## Componentes Que Se Reutilizan

- `research/causal_replay.py`, parser/catalogo y loaders Gold/Dubai: identidad,
  causalidad, templates propios y carga de ticks; adaptar datos, no rehacerlos.
- `engine.py`, `fast_engine.py`, `oracle.py`: simulacion y referencia
  independiente. El rapido comparte preparacion legacy con el escalar;
  esa pareja no reemplaza al oracle independiente.
- `market.py`, `protection.py` y sus contratos: aperturas/cierres/acks,
  SL/TP instalado y reintentos; NO son tareas pendientes de construir de cero.
- `portfolio.py`, `risk.py`, `broker_money.py`, cache de ticks y reloj:
  reutilizar con los limites monetarios y de cartera indicados.
- `iterative_provenance.py`, buscadores, Pareto/folds/checkpoints,
  certificacion, herramientas de control/comparacion: integrar el perfil y
  mantener las barreras, no sustituir todo el sistema.
- Evidencia historica y sus bloqueos: conservarla; no rehacer la sesion Sep8/9
  hasta igualar cada milisegundo ni exigir logs inexistentes a cada JSON.

## Archivos Y Estado Local

Solo escritos:
`docs/audits/2026-09-10-simulator-delivery-inventory.md` y
`runtime_data/simulator_delivery_20260910/capability_inventory.json`.
El JSON contiene los mismos IDs, criterios, estados, limites y hashes.

No se modifican sharedmap, engine, pruebas, AGENTS ni otros frentes.
Sin commit ni push. Este inventario queda **local y sin publicar**;
no cambia ni verifica el estado live de la VM.
