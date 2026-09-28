# Semana Nativa: Anclas De Precio Y Reloj

Estado: evidencia diagnostica local. No se ha publicado codigo, reiniciado el
bot ni modificado la VM. La terminal MT5 aislada era demo, con trading/API
deshabilitados, y quedo detenida tras la extraccion.

## Fuentes Y Cobertura

El inventario reconstruido con el diario local **y** el sufijo verificado del
diario de la VM contiene 416 senales, sin IDs de evento duplicados o en
conflicto. Enlaza 40 cestas nativas con recibos observados. El inventario
anterior, que solo leia el diario local, enlazaba 12; no representaba la
semana completa. Ninguna de las 40 cestas contiene el contrato de estrategia
previo a su sesion en el sufijo remoto conservado.

La extraccion aislada recupero del 14 al 18/09 cinco dias por simbolo:
3.354.354 ticks XAUUSD y 634.256 EURUSD. Las diez parejas de lectura de dia
completo frente a dos mitades fueron consistentes, sin inversiones de reloj
ni Bid/Ask invalidos. La misma cuenta demo, servidor y moneda estan ligados
por hash a los deals nativos; los dos movimientos de saldo no se tratan como
ejecuciones XAUUSD. Los archivos crudos siguen con `clock_admitted=false` y
`engine_dataset_ready=false`.

El auditor [`native_tick_anchor_audit_v3.json`](../../runtime_data/week_native_ticks_20260923_v1/native_tick_anchor_audit_v3.json)
vincula los 230 deals XAUUSD a un tick causal anterior en su fecha de epoch
del broker y verifica por hash/esquema los diez dias-simbolo, incluido EURUSD.
Antiguedad del tick: mediana 25 ms, p90 69 ms, maximo 428 ms.
Distancia absoluta entre precio de deal y lado ejecutable del tick: mediana
0,03 USD/onza, p90 0,60, maximo 15,85; 13 deals superan 1 USD/onza. Los
230 precios aparecen dentro del rango de los 60 segundos causales anteriores,
pero esto no acredita ni el tick exacto del fill ni el precio que hubiese
obtenido otra orden.

Las 40 recepciones UTC frente al primer deal son compatibles con la hipotesis
de epoch del broker adelantado 10.800 segundos: residuo mediano 43,7 s, p90
178,6 s y un caso -11 ms. El residuo incluye espera de estrategia, cola y
ejecucion; **no es una medida de latencia de la VM**. El desfase aun no se
admite como conversion UTC historica por esta sola comparacion. La anterior
impresion de una hora provenia de visualizar timestamps UTC convertidos a la
zona local de Madrid.

Una segunda comprobacion local, [`native_tick_anchor_audit_v4.json`](../../runtime_data/week_native_ticks_20260923_v1/native_tick_anchor_audit_v4.json),
anade las capturas independientes del servicio MQL/MT5 que ya constaban en el
contrato monetario de la VM. Verifica servidor, moneda, aritmetica entre epoch
GMT y epoch del broker y la presencia de un tick historico cercano al tick
Python activo. Hay anclas directas de +10.800 segundos el 15/09 (7), 17/09
(2) y 18/09 (5), que abarcan 27 de las 40 cestas por dia de primera apertura.
El 14/09 y el 16/09 conservan tres capturas cada uno, pero ninguna cumple
las condiciones de mercado activo, tick fresco y base temporal Python del
broker: quedan **sin ancla directa**, no se les asigna el offset por continuidad.
El historial de la terminal demo y las capturas de VM comparten servidor, no
un acuse historico de cada ejecucion; incluso en los tres dias anclados
`clock_admitted=false` y la paridad integral sigue pendiente.

La auditoria monetaria [`native_money_anchor_v2.json`](../../runtime_data/week_native_ticks_20260923_v1/native_money_anchor_v2.json)
liga los 115 pares de deals de posiciones cerradas a EURUSD historico y al
contrato monetario conservado, sin convertir el epoch del broker a UTC. El
beneficio de cierre modelado coincide al centimo en 114 posiciones. En
`canal2_2995` el EURUSD previo tenia 9.563 ms de antiguedad, por encima de
los 5.000 ms causales, pero el siguiente tick lo acota a un intervalo total
de 13.321 ms: se admite **solo retrospectivamente**, conforme al contrato
de 60.000 ms, sin usar ese tick futuro como precio ni como informacion de
decision. El v1 conservado lo habia dejado bloqueado al aplicar solo la edad
estricta; el v2 distingue ambas coberturas. `canal1_22738` difiere
-0,01 EUR entre conversion calculada y beneficio nativo, y se conserva como
discrepancia. Los netos nativos siguen reconciliados; este control de dinero
al cierre **no mide** el flotante, el equity ni el drawdown reales durante la
operacion. Tampoco demuestra que la cotizacion FX historica fuese exactamente
la usada por el broker en el instante de ejecucion.

El control [`native_account_snapshots_v2.json`](../../runtime_data/week_native_ticks_20260923_v1/native_account_snapshots_v2.json)
cruza las 33 lecturas conservadas de `mt5_account_connected` con posiciones
nativas y ticks causales en el sello **del evento**. Quince lecturas muestran
`equity - balance` distinto de cero (14/09 y 16/09, ambos dias sin ancla horaria
directa): tres coinciden al centimo, once difieren y una queda bloqueada por
FX obsoleto. Hay 14 lecturas planas dentro de la cinta y cuatro fuera de ella.
No se ajusta el sello ni se declara paridad por coincidencias aisladas.
El evento no guardo el credito de la cuenta ni otros ajustes; por tanto,
`equity - balance` no acredita por si solo el flotante puro de las posiciones.

El mayor desajuste esta en la lectura del 14/09 10:27:48.501 UTC: -36,69 EUR
de `equity - balance` frente a -45,36 EUR de posiciones modeladas en el sello, con seis
posiciones abiertas. En una exploracion de la cinta, un tick 511 ms anterior
habria dado aproximadamente -36,68 EUR. Esto es una hipotesis de desfase de
observacion, **no** una correccion admitida. El `executor.py` del commit que
emitio esa lectura (`d9037c5c`) toma `account_info()` antes de otras consultas
y de escribir `mt5_account_connected`; no guarda la hora exacta de la lectura.
Sin esa hora no podemos exigir igualdad punto a punto ni usar esta muestra
para certificar el drawdown maximo real.

La [trayectoria nativa sobre todos los ticks](../../runtime_data/week_native_ticks_20260923_v1/native_week_risk_path_v1.json)
marca 809.620 observaciones de las 40 cestas, incluidas las fronteras de los
230 deals. Las 40 trayectorias retrospectivas quedan completas y cada una
conserva hash del recorrido, exposicion, realizado, flotante modelado y
drawdown. En 24 cestas hubo alguna marca cuya cobertura EURUSD solo se puede
confirmar despues con el tick siguiente; 16 tienen cobertura FX causal
estricta en todas sus marcas. El offset +3 h es una **hipotesis** en 13 cestas
sin ancla directa; tampoco las 27 ancladas constituyen por si mismas paridad
de decisiones. Los eventos empatados con un tick comparten la convencion
de aplicarlos antes de esa marca; no prueba el orden intramilisegundo real.
Como ilustracion del riesgo que oculta el neto, `canal2_3102`
cerro con +20,02 EUR pero alcanzo -176,98 EUR de minimo y 178,72 EUR de
drawdown **modelados sobre la cinta historica**. `canal2_2908` cerro en
-302,38 EUR y alcanzo 390,65 EUR de drawdown modelado. No son lecturas
continuas de equity nativo ni un resultado de estrategia alternativa.

## Control Vivo Frente A Deals Nativos

El [cruce de sombras y operaciones nativas](../../runtime_data/week_native_ticks_20260923_v1/week_shadow_control_path_v5.json)
verifica por hash el extracto comprimido, las huellas del catalogo de controles,
las cadenas de estados, los deals, el contrato EUR y los dias crudos de precio.
El archivo incluye dos recepciones del 11/09, separadas expresamente de la
ventana 14-18/09. En esta ventana hay 44 senales recibidas: 40 cestas nativas,
14 controles vivos registrados en el sufijo del diario y **12 con ambos**.
Las otras 28 cestas carecen de registro de control en el sufijo; dos registros
de control no tienen cesta nativa en el extracto y otras dos recepciones no
tienen ninguno de los dos. Ninguna ausencia se interpreta como no operar.

Las 12 cestas comparables estan en dias con ancla horaria directa. Sus 500
eventos de transicion con tick se reducen a 487 estados terminales distintos
por tick. Cada tick coincide exactamente en sello y Bid/Ask con la cinta XAU
conservada. En 485 marcas se puede valorar el flotante nativo con EURUSD
previo causal; dos quedan bloqueadas por FX anterior demasiado antiguo. Solo
16 de las 485 igualan simultaneamente volumen abierto, realizado y flotante
al centimo. Esto **no** certifica que los 469 restantes tengan un defecto de
politica: incluso con la misma cotizacion, entradas, tiempos, precios de fill
y redondeo de la sombra pueden diferir de los nativos. Cada delta y el primer
punto de divergencia se conservan por senal para atribuirlo despues.

En el control Gold 555, seis de siete netos finales coinciden; `canal2_3086`
termina 30,18 EUR por debajo en sombra. Su primer fill nativo sucede 67.611 ms
despues de la **hora del tick historico** del primer fill virtual, pero la
transicion virtual se emitio 68.469 ms despues de ese tick y 858 ms despues
del fill nativo. El diario registra `strategy_shadow_tick_archive_tail_resumed`
justo antes de emitirla. Por tanto, esos 67.611 ms **no miden** una orden real
en espera ni una latencia de VM demostrada: la sombra estaba procesando
historia atrasada. La traza real recuperada despues identifica por separado
67,547 s dentro de `mt5.order_send` para esa entrada. Las siete trayectorias Gold tienen diferencias
intermedias aunque seis netos coincidan. Por ejemplo, en las marcas comunes
`canal2_3096` registra drawdown 25,91 EUR en sombra frente a 20,90 EUR
modelados con fills nativos. Son **marcas de transicion**, no el maximo de
todos los ticks ni equity nativo continuo. `canal2_3086` no tiene ninguna
marca con ambas versiones abiertas: su drawdown nativo de 0 en esa rejilla
significa falta de cobertura, no ausencia de riesgo. Para el recorrido nativo
completo sigue vigente el auditor de 809.620 marcas anterior.

El tiempo `ts` del evento de diario se crea al emitir la transicion; no es
el tiempo del tick. En las 12 primeras entradas virtuales, el evento quedo
registrado despues del primer fill nativo (entre 130 ms y casi 46 h despues).
Cinco primeras entradas virtuales se emitieron mas de 5 s despues de su tick;
191 de las 500 transiciones con tick superan 5 s. `canal1_22587` registro
su primer fill virtual unos 46 h despues del tick, durante recuperacion de
sesion. El umbral de 5 s es descriptivo, no una tolerancia de paridad. Los
controles conservan su rol de catalogo `live_control`, pero estas marcas
atrasadas no son evidencia prospectiva de seguimiento en tiempo real.

El diario local `runtime_data/trade_events.jsonl` contiene IDs iguales a los
de esta semana, pero de julio: por ejemplo, `canal2_3086` aparece alli el
10/07 y `canal2_2977` el 09/07. Un cruce solo por `signal_id` mezclaria
operaciones diferentes. El inventario semanal v8 acota las fechas desde el
27/07 y sus recibos para estos IDs son de septiembre; las trazas locales de
julio **no** se usan para explicar el envio o el fill de septiembre. El
primer extracto remoto selecciono sombra/recibos, no los pedidos reales.

## Tiempo Real De Primera Apertura

Se recuperaron por SSH, **solo lectura y baja prioridad**, cuatro lotes de
10 ventanas acotadas alrededor del primer fill nativo. Se leyeron en total
400.461.127 bytes del diario de 9,4 GB, con maximo de 64 MB por ventana y
256 MB por lote; no se escribio en la VM ni se reinicio el bot. Cada lote
conserva offsets y SHA256 del tramo leido. El [cruce de las 40 primeras
aperturas](../../runtime_data/week_native_ticks_20260923_v1/native_week_first_calls_v2.json)
vincula resultado, intento, pedido y recibo al ticket exacto del deal, y
rechaza elegir una peticion solo por proximidad temporal cuando hubo varias.
Las 40 enlazan: 26 tienen orden temporal nativa dentro de la llamada y ancla
horaria directa; una, `canal1_22761`, tiene el deal nativo 18 ms antes del
inicio registrado y conserva esa anomalia sin tolerancia inventada; las 13
restantes pertenecen a dias sin ancla directa de +3 h, por lo que sus deltas
contra la hora nativa siguen siendo hipotesis.

La mediana de `mt5.order_send` fue 351,5 ms; 16/40 superaron 1 s, 9/40
superaron 5 s, 6/40 superaron 10 s y 3/40 superaron 60 s. Maximo: 106,86 s
en `canal2_2917` (14/09, reloj nativo sin ancla directa). Los nueve casos
de mas de 5 s aparecen en ambos canales y en 14, 15, 17 y 18/09, bajo tres
commits distintos. Para `canal2_3086`, el recibo fue a las 12:28:53.250,
la estrategia confirmo y pidio la entrada a las 12:30:00.462, el deal nativo
se sello a las 12:31:07.803 y la llamada devolvio a las 12:31:08.014. La
espera desde recibo hasta pedido es de estrategia; los **67,547 s** entre
inicio y respuesta son el tramo bloqueante de `mt5.order_send`, con 0 ms
instrumentados antes y despues. `canal1_22778` presenta 62,578 s y
`canal2_3096` 19,282 s en el mismo tramo. En los tres, el desfase entre
primer tick virtual y deal nativo difiere de la duracion de la llamada en
solo 90, 64 y 37 ms, respectivamente. Esto identifica un mecanismo general
de divergencia de entrada; no demuestra que todas las diferencias de
gestion, flotante o drawdown tengan esa causa.

El codigo historico de los tres commits rodea directamente `mt5.order_send`
con `mark_broker_request_started/mark_broker_response_received`. El nombre
del campo `broker_roundtrip_ns` **no** separa puente Python, IPC de terminal
MT5 y red/servidor del broker. Hace falta investigar terminal y transporte,
ademas de contrastar el recorrido completo de las 40 cestas; no se debe
introducir una latencia fija en la simulacion para maquillar la discrepancia.
SHA256 del informe de primeras llamadas v2:
`61F0937D17D2CB1B8FD6BF78736BF03850DEC1671066E7AD98D5BFFC046B8268`.
El v1 del mismo informe queda superado por el calculo correcto de mediana.

## Contraste Con El Log Del Terminal MT5

Se copiaron en **solo lectura** los logs diarios UTF-16 del terminal de
14-18/09; las cinco copias locales tienen el mismo SHA256 que sus fuentes en
la VM. El [auditor del terminal](../../runtime_data/week_native_ticks_20260923_v1/terminal_open_log_audit_v5.json)
enlaza los **40/40** primeros deals por ticket de deal y numero de orden con
el resultado exacto de la llamada real. Cada uno tiene su linea `order done
in ... ms`. El informe conserva SHA256 de logs, ventanas de VM, deals y
codigo; v1 queda superado porque usaba un margen horario arbitrario de 2 s.
V3 anade la cobertura de todos los deals vinculados a las cestas: **226/230**
estan en el log con ticket y order exactos. Los otros cuatro son salidas de
`canal2_2917` con `reason=5` en el ledger; no se infiere su ausencia en MT5
por no aparecer en estas lineas del terminal. Dos deals adicionales de la
fuente nativa no pertenecen a las 40 cestas y no entran en el denominador.
V4 exige tambien que el order del ledger coincida en cada primera apertura;
V5 comprueba las huellas del ledger y las cestas contra el informe de llamadas.
V2-v4 quedan superados. SHA256 v5:
`919FE575D0FF9DB945B0360634CF5DA150AFC6919ED717E47F56E2F7D960D49A`.

En siete de las nueve llamadas de mas de 5 s, la linea `market` aparece
entre 0 y 174 ms despues del inicio Python. Para `canal2_3086`, el terminal
registra `market` a +127 ms, `accepted` 43,195 s despues, el deal 24,142 s
despues de `accepted`, y `order done in 67489.360 ms`; Python midio
67,547 s. En `canal1_22778`, el terminal mide 62,565.828 s frente a
62,578 s en Python. En `canal2_2908` y `2917`, la linea `market` aparece
a +42,859 y +35,721 s, respectivamente, pero `order done in` contabiliza
56,203.385 y 105,804.263 ms: no se puede asignar ese tramo solo a IPC,
red o servidor a partir del orden de las lineas. En `2908`, el deal se
registra 8,869 s **antes** que `accepted`; el informe no reordena eventos
para ocultarlo. Las lineas `market/accepted` carecen de numero de orden y
solo se usan cuando direccion y volumen dan un candidato unico en la ventana.

En 16/40, `order done` queda 1 ms despues de la marca de respuesta Python;
es una diferencia entre relojes/resoluciones, no una falla de identidad ni
un criterio de aprobacion relajado. El contraste localiza las esperas dentro
del recorrido de envio MT5 y descarta atribuirlas a un delay fijo de la
estrategia para esos casos. **No** aisla si la espera viene del puente,
terminal, red o broker y **no** valida los DCA, cierres ni drawdown. Las
14 pruebas focalizadas de los cuatro auditores de apertura pasan.

## Piloto De Trayectoria Completa

Un [extractor acotado](../../tools/probe_vm_signal_lifecycle.py) de solo
lectura, baja prioridad y sin texto de Telegram inventario la vida completa
de cinco cestas dentro de ventanas de hasta 15 minutos. Cada captura guarda
offsets/SHA256 de los bytes leidos, huellas de entradas y **recuentos de
todos** los tipos de evento. Solo los primeros 100 registros de cada tipo
(1000 para `mt5_action_attempt`) salen en el detalle; los tipos truncados
se declaran en `sampled_kinds`. No es un diario completo ni una prueba de
ausencia fuera de la ventana. Se leyeron 17,44 MB para `canal2_3086`, 25,32
MB para `canal1_22732`, y 12,87/38,03/10,26 MB para `canal2_3148/3168/3171`.

En [3086](../../runtime_data/week_native_ticks_20260923_v1/lifecycle_probe_3086_v4.json),
el diario contiene 33 modificaciones pedidas y 254 intentos `MODIFY_SLTP`
para un ticket y 31 IDs de accion, durante 262,724 s. Los 254 pasaron a
`mt5.order_send` y retornaron `10016` (stops invalidos); no hay confirmacion
de modificacion en esta ventana. Apertura y cierre devolvieron `10009`.
Esto sucede **despues** de la apertura lenta y no explica sus 67,547 s.
Las 3135 evaluaciones `gold_555_basket_guard` y 253 `gold_555_trailing`
se cuentan, pero las primeras 100 de cada tipo no se presentan como recorrido
integral. SHA256 del piloto 3086:
`E426AB300D237E977C43E7C7EC803287E5E638A13B34BFCF96BBA72223535497`.

El [piloto Dubai](../../runtime_data/week_native_ticks_20260923_v1/lifecycle_probe_22732_v3.json)
registra tres posiciones nativas, cinco pedidos/resultados de apertura (tres
`10009`, dos `10016`), dos eventos `dca_filled`, tres modificaciones aceptadas
y cierre. SHA256:
`313FF5470DA72A3CEFFD14F0EC1FACB45762F8E397B179544C1B6D2F3ADE281C`.
En Gold, `3148` tuvo 12 intentos de modificacion y ninguno `10016`; `3168`,
39 y ninguno; `3171`, cuatro con dos `10016`. Por tanto el rechazo repetido
existe, pero no se debe proyectar a todas las cestas. El episodio 10016 de
`canal2_2640` ya estaba documentado el 08-09/09; `3086` muestra recurrencia,
no una causa nueva demostrada de latencia de apertura.

El motor de sombra [strategy_shadow_engine.py](../../strategy_shadow_engine.py)
aplica `protection_tightened` al estado virtual sin un acuse/rechazo MT5;
es un control hipotetico, no una reconstruccion certificada de stops
instalados. Para el contraste historico completo, cada `MODIFY_SLTP` debe
separar intencion, intento, retcode y estado observado por ticket; `10016`
conserva el nivel anterior hasta evidencia posterior. Tambien hay que ligar
todos los DCA y cierres con deals nativos, y comparar exposicion, flotante y
drawdown sobre la misma cinta y reloj admitidos. El piloto no satisface ese
gate para las 40 cestas ni autoriza cambios de estrategia.

## Cobertura Semanal De Eventos Materiales

El [plan semanal](../../runtime_data/week_native_ticks_20260923_v1/lifecycle_window_plan_v1.json)
deriva del recibo y todos los deals nativos de las **40 cestas / 115
posiciones / 230 deals**. Mantiene 27 cestas con reloj directo para todos
sus dias y 13 con offset de broker aun hipotetico. Las 331 ventanas de
cinco minutos por cesta se reducen a **249 tramos temporales unicos** al
unir solapamientos, unas 19,55 horas de diario. El plan no es una extraccion
ni certifica el reloj de 14/16-09. SHA256:
`19CD0DC13F8421243AC0D4ABB89158C1273173DCF3EC1F3D379111DA0DE6A146`.

El [extractor de tramos](../../tools/collect_vm_week_material.py) solo
exporta campos permitidos: pedidos, intentos, retcodes, decisiones con
acciones y cambios de estado. Cuenta, pero no exporta, evaluaciones sin
accion, snapshots frecuentes y la sombra separada. Cada tramo conserva los
hashes del plan, codigo y bytes fuente; limita lectura a 64 MB, tiempo a
120 s, y baja la prioridad del proceso remoto. Dos tramos de `3086`
leyeron 11,75 y 9,71 MB, respectivamente. Sus 467 IDs de eventos no
muestreados, incluidos pedidos, intentos y resultados, coinciden con el
piloto anterior (ninguno perdido ni anadido); ambos conservan los 254 `10016`.
La localizacion v1/v2 por busqueda binaria de hora presupone un diario
aproximadamente ordenado. Una pasada local sobre 132.686.091 bytes y 198.084
eventos encontro cuatro retrocesos entre horas de lineas consecutivas: no
demuestra perdidas en esos cuatro tramos, pero impide elevar esa presuposicion
a prueba general de completitud. El nuevo indice local por bloques registra
minimo/maximo de cada bloque y selecciona todos los que cruzan la ventana;
el extractor v3 lee un bloque entero y verifica su hash, incluso con eventos
fuera de orden. El lector remoto autonomo y de baja prioridad ya se probo
localmente por stdin; el ensamblador exige todos los bloques contiguos del
prefijo y la misma huella del lector. El colector v3 selecciona cada bloque
que cruza el tramo, verifica los hashes de bytes al releerlo y agrega los
eventos con limites de tamano. Todo ello esta probado con desorden sintetico.
Falta capturar el indice completo del prefijo remoto y los tramos v3 reales.
Aunque se completara, v3 solo acreditaria el **prefijo indexado**: un diario
vivo podria recibir despues eventos de la misma semana. Hace falta una
prueba de cierre del origen para elevarlo a cobertura semanal completa.
El auditor separa desde ahora `fully_covered_baskets` (todos los archivos
de tramo presentes) de `full_cohort_coverage_verified`: con tramos v1/v2
esta ultima puerta permanece falsa aunque se reunieran los 249, hasta
tener cobertura temporal del origen probada por indice completo.

El [informe de cobertura parcial vigente](../../runtime_data/week_native_ticks_20260923_v1/material_coverage_partial_v11.json)
mantiene el denominador: **4/249 tramos, 2/40 cestas con todos sus archivos de tramo y 38 con
tramos pendientes**. Para `3086` hay 500 eventos materiales, evento de
cierre y apertura ligada al deal nativo. En `canal1_22732` hay 90 eventos
materiales; sus 83 IDs no muestreados coinciden con el piloto y las tres
aperturas nativas ligan con resultados `10009`, mientras se conservan dos
aperturas rechazadas `10016`. Las ventanas de Dubai tambien contenian otras
senales activas; el filtro preservo cada identidad. Las cuatro aperturas
nativas de ambas cestas ligan con pedido, intento y resultado por
accion/sesion, deal, orden, precio y volumen; el deal cae dentro del intervalo
medido de llamada MT5. SHA256 v11 del informe material:
`E20E822E5A65B05B776A844CE3C93FA682D53690DDFA3DD216D4D71EFE3E6EFC`.
El informe v11 firma tambien los cinco modulos auxiliares usados por el
auditor, no solo el script principal.

El informe adjunta netos **observados en deals** y una trayectoria de riesgo
**reconstruida** con esos fills y los ticks retenidos: para `canal1_22732`,
neto -24,62 EUR, drawdown calculado 27,27 EUR y volumen maximo 0,05; para
`canal2_3086`, neto +31,92 EUR, drawdown calculado 13,19 EUR y volumen
maximo 0,04. El drawdown calculado no es una lectura independiente del
flotante de MT5. En Dubai, dos deals nativos tienen motivo SL y precio
4369,25. Para cada ticket hay snapshot anterior con SL 4369,25 despues de
una modificacion aceptada (`10009`), con pedido/intento/confirmacion
concordantes y cronologia comprobada. Esto observa el nivel despues del
acuse, pero no demuestra el instante de instalacion en el servidor. Los otros dos cierres son candidatos
expertos por ticket y accion, sin identidad de deal en el resultado terminal;
no se declaran ligados exactamente. El reloj global del informe de riesgo
permanece no admitido y la cobertura FX es retrospectiva.

El diario original contiene `deal` y `order` en el resultado anidado del
intento. El extractor local ahora los exporta con precio, volumen y datos
minimos de la solicitud para futuras capturas acotadas; 15 pruebas focales
pasan. Los cuatro tramos existentes fueron capturados con el extractor
anterior: el informe v11 sigue mostrando **dos candidatos**, no identidades nativas
verificadas. Al continuar la extraccion habra que recapturar esos cuatro
tramos con la nueva huella de codigo: el auditor rechaza mezclar versiones.
Tambien rechaza un `deal` declarado que contradiga el deal nativo, en vez de
tratarlo como evidencia simplemente ausente.
Incluso un match de deal/orden no sustituye la historia nativa de ordenes
que exige el contrato completo de cierre.

El diario de la version de produccion tambien registra
`management_decision_inputs_v1`: inicio con entradas/estado y final con
resultado/estado para las evaluaciones de gestion, incluidas las que no
producen accion. El extractor local v2 conserva esos pares en una lista
separada, con campos estructurados permitidos, presupuesto de 25.000 eventos
y 32 MB de salida por tramo, y rechazo explicito por exceso. El maximo de
inicio/final observado en los cuatro tramos v1 es 13.766 eventos en cinco
minutos; por eso el presupuesto no se fijo en 10.000. El auditor v2 comprueba
identidad, sesion, estrategia, orden temporal y unicidad de los pares, pero
**emparejar no es reproducir la politica**. Veintiuna pruebas focales,
incluida una integracion extractor-auditor, pasan. Los tramos actuales son v1:
el informe real mantiene `management_paired_baskets=0` y ambas cestas piloto
en `not_collected_v1`. No se ha releido la VM ni se ha comparado todavia
ninguna decision de gestion o trayectoria simulada con esta captura nueva.
El validador de esquema acepto 2.678 eventos de gestion del diario local;
en ese diario anterior los 1.339 pares quedan bloqueados porque carecen de
`code_commit`. El contrato del commit de septiembre tiene una diferencia
historica legitima: `gold_555_leg_protection` aun no registraba `current_sl`;
solo ese campo es opcional, sin abrir el resto del esquema.

## Replay Puro De Guards

El [control local de guards](../../runtime_data/guard_replay_local_20260923_v4.json)
recalcula el resultado y los cinco campos de estado posterior de cada
evaluacion Gold 555 capturada, con el mismo evaluador puro que usa el monitor.
De 290 pares del diario local, **287 coinciden** y tres quedan bloqueados
porque `now_utc`/primer fill no tienen zona horaria explicita; no se les
asigna UTC a posteriori. SHA256 del informe:
`5BD8223D90B7D8883CF31CE33210D7268F0995EF4E1798CB2E8745852E03B2D8`.
El diario de entrada tiene SHA256
`ADFD7AD1A4F43DEB4C64AD373A1E1B901C2CCA6710EA42CFE16008B8366C6D8F`.
Los archivos actuales de politica Gold 555 y Dubai tienen los mismos objetos
Git que el commit `7e33c116e53e51a9fcddc0d5dc0ff34fbf0bbb18` de las
dos cestas piloto. Ocho pruebas del comparador y 29 focales conjuntas
pasan. Este diario local no es la captura congelada de la semana; carece de
`code_commit` por evaluacion y el informe declara
`source_version_admitted=false`. El control no prueba entradas, trailing,
ordenes/cierres, dinero flotante ni drawdown, ni certifica la VM.
El ejecutor puede ahora consumir todos los tramos v2 previstos para una cesta:
rechaza faltantes, duplicados, mezcla de versiones o registros de gestion
fuera del esquema, empareja todas las evaluaciones y compara los guards puros.
Solo existen pruebas sinteticas de esa ruta; ningun tramo semanal v2 ha sido
recogido todavia.

El informe v2 por cesta tambien puede reducir el P/L que `positions_get`
entrego al guard en cada evaluacion: minimo desde cero, maximo y drawdown
**sobre las muestras conocidas**, reteniendo lecturas bloqueadas y sin
rellenarlas. Solo suma tickets conocidos por el bot; aun no comprueba que sean
todos los tickets nativos ni observa extremos entre evaluaciones. La hora
`guard_evaluated_at_utc` es posterior a la lectura de posiciones; su hora
exacta no se capturo y una comparacion puntual con ticks seria injustificada.
El ejecutor puede adjuntar los deals nativos fijados por hash en el plan y
calcular los tickets abiertos a la hora del guard. Rechaza una fuente distinta,
posiciones sin cierre completo y deals en el mismo instante de evaluacion;
separa coincidencias, diferencias y relojes sin ancla directa. Ese contraste
es **diagnostico**: enfrenta tickets nativos a la hora del guard con tickets
leidos por MT5 antes, en hora desconocida. Por tanto incluso una coincidencia
mantiene `native_position_universe_verified=false` y no certifica el flotante.
Las 33
capturas de cuenta previas coinciden con exposicion abierta en 15 momentos
de cuatro cestas, y en ninguno de los pilotos `22732`/`3086`. La trayectoria
nativa calculada con fills reales y ticks no es una segunda observacion de
MT5; comparar ese calculo consigo mismo no validaria el simulador. El nuevo
P/L de guard sera una referencia independiente, pero todavia no hay datos
v2 semanales para calcularla ni cotejarla con el replay.

El colector no se ejecuto masivamente con el mercado abierto. Completar
ventanas fuera de carga, cotejar cada apertura/DCA/cierre y estado de stops,
admitir reloj y comparar exposicion, flotante y drawdown con el replay
siguen siendo gates separados. `full_simulator_path_parity_verified=false`.
El intento ligero de conectividad SSH del 23-09 a las 14:08 UTC agoto el
tiempo de espera; no se infiere de ello el estado del bot y no se reintento
una lectura pesada durante el mercado.

El informe v5 de **sombras** es el vigente para ese contrato distinto;
v1-v4 quedan superados por la correccion del
denominador temporal, las etiquetas de cobertura, las huellas de codigo y
la separacion entre reloj del tick y reloj de emision. SHA256 v5:
`DBD6F605DF98E5682AC603D92623EBA7390BD823BFA8D1ECCE4E71D399B68C8B`.
Seis pruebas focalizadas del auditor de sombras pasan. No se ha cambiado
politica, enviado ordenes ni hecho commit/push; la VM solo se leyo por SSH.

El siguiente gate no es optimizar 555 con estos 12 casos. Las primeras
aperturas reales de las 40 cestas ya tienen traza de recibo, pedido, llamada,
respuesta y deal. Falta el contrato de sesion previo, la secuencia de
gestion/cierres y el contraste del recorrido completo de exposicion y riesgo
con el replay compartido. La causa interna de las llamadas MT5 largas sigue
sin aislar; la paridad integral de decisiones permanece sin verificar.

## Verificacion Y Siguiente Gate

- `python -m pytest -q tests/test_audit_native_tick_anchors.py
  tests/test_runtime_simulation_inventory.py tests/test_isolated_mt5_history.py`:
  45 aprobadas. Incluye EURUSD alterado y deal fuera de cinta que no desaparece.
- El informe usa hashes de codigo, inventario, deals, cestas y cada dia crudo;
  mantiene `diagnostic_only`, `clock_admitted=false`, sin certificar paridad.
- El v4 incorpora por hash el contrato monetario, rechaza capturas horarias
  contradictorias e IDs duplicados en el inventario; sus dos regresiones
  adicionales pasan junto con las pruebas previas del auditor.
- `pytest -q tests/test_audit_native_money_anchor.py
  tests/test_audit_native_tick_anchors.py tests/test_risk_trajectory.py`:
  30 aprobadas. El auditor monetario conserva FX ausente, obsoleto y beneficio
  discrepante en el denominador, y rechaza netos que contradigan los deals.
- `pytest -q tests/test_audit_native_equity_snapshots.py
  tests/test_audit_native_money_anchor.py tests/test_audit_native_tick_anchors.py
  tests/test_risk_trajectory.py`: 32 aprobadas. El informe de snapshots mantiene
  los 33 eventos y separa cuenta plana, diferencia y bloqueo por cotizacion.
- `pytest -q tests/test_audit_native_week_risk_path.py
  tests/test_risk_trajectory.py tests/test_audit_native_money_anchor.py`:
  29 aprobadas. El modo de intervalo FX retrospectivo es opt-in; el comparador
  causal anterior sigue bloqueando cotizaciones de mas de 5 s.
- `python -m pytest -q`: 7.635 aprobadas, una advertencia previa de pandas,
  596,44 s en Python 3.11.9/Windows. Tras la corrida, los 28 hashes de codigo
  y datos del informe de riesgo seguian identicos; SHA256 del informe
  `A4C94D417829320CAB2F5D17264A7330B43E80730D92B70B94EC79C5E0B4A468`.
- Falta recuperar de forma solo lectura el prefijo de sesion/contrato de
  estrategia de la VM y sellos de adquisicion de lecturas de cuenta, admitir
  el reloj y cada horizonte con anclas independientes, y entonces contrastar
  el replay con trayectorias nativas completas de ambos canales. Ninguna
  variante de estrategia se selecciona con estos datos.

## Actualizacion Del Colector Indexado

El lector autonomo `audit_journal_chunk_index.py` y el colector
`collect_vm_journal_index.py` capturan bloques limitados a 64 MB mas una
linea de alineacion y 120 s por
invocacion SSH, sin escritura en la VM. El segundo solo ensambla un prefijo
cuando estan todos sus bloques, contiguos y con la misma huella de lector.
`collect_vm_week_material.py --index-manifest` relee por SSH cada bloque
relevante para un tramo y coteja su SHA256 antes de agregar eventos. La ruta
v3 se probo con eventos desordenados y permanece `diagnostic_only`:
`full_source_time_coverage_verified=false` y
`full_simulator_path_parity_verified=false`. No hay capturas v3 reales.

Verificacion local posterior a estos cambios: 36 pruebas focalizadas y la
suite completa de 7.693 pruebas pasan (686,37 s); persiste una advertencia
previa de asignacion encadenada de pandas. No se hizo commit, push, despliegue,
reinicio ni operacion de trading. La recoleccion remota completa sigue
pendiente de una ventana de baja carga.

SSH respondio de nuevo el 23-09 a las 15:17 UTC. Una lectura de metadatos
mostro 9.895.771.380 bytes en el diario remoto. Una prueba remota de un
bloque solicitado de 64 KiB (65.960 bytes tras alinear lineas), con el
lector por stdin y prioridad baja, devolvio
253 eventos y un hash SHA256 verificable; se guardo como
`journal_index_smoke_chunk0_v1.json`. No demuestra continuidad, cobertura de
la semana ni estado de salud del bot. El colector local tiene ahora modo
reanudable con un maximo de 32 bloques nuevos por llamada, pausa entre
bloques y validacion de cada archivo ya presente. Sus pruebas focalizadas
pasan; no se ha lanzado un lote masivo con el mercado abierto.

Relectura del informe independiente de cuenta v2: 15 snapshots con posiciones
abiertas, todos en dias `no_direct_anchor`; 11 tienen diferencia no nula
entre flotante modelado y `equity-balance` (maximo absoluto 8,67 EUR), tres
coinciden y uno bloquea por cotizacion obsoleta. Ademas, `equity-balance` no
esta demostrado como flotante puro sin ajustes de cuenta. Son controles
diagnosticos, no anclas de paridad ni extremos de drawdown. La captura de
gestion semanal y sellos de lectura MT5 sigue siendo necesaria.

La instrumentacion local del monitor registra ahora inicio, fin y duracion
monotona de cada lectura `positions_get` que alimenta el resumen del guard.
El extractor permite esos tres campos; el replay comprueba orden temporal y
consistencia entre reloj de pared y monotono. Cuando existe ancla nativa
directa, compara tickets en el intervalo de lectura y bloquea un deal que
caiga dentro de el. Los registros historicos sin ese sello conservan el
resultado diagnostico anterior. Este cambio no esta en produccion y no
convierte por si solo los snapshots escasos en una trayectoria continua.
Tras corregir la dependencia del reloj monotono para los replays sinteticos,
56 pruebas de monitor/replay focalizadas y la suite completa de 7.696 pruebas
pasaron (662,22 s). Persiste solo la advertencia conocida de pandas. No se
ha hecho commit, push ni despliegue de esta instrumentacion.

## Piloto Emparejado De Gestion Y Riesgo

Se recapturaron por SSH y en modo de solo lectura los cuatro tramos de
`canal2_3086` (179/180) y `canal1_22732` (196/197) con el extractor v2. Sus
hashes de bytes y sus IDs materiales coinciden con las capturas v1. Hay
3.388 pares completos de gestion para 3086 y 2.278 para 22732, sin errores
de emparejamiento. El replay puro del guard coincide en 3.135/3.135
decisiones Gold y 2.278/2.278 Dubai; los conjuntos de tickets observados
coinciden al evaluar el guard, pero el instante de lectura MT5 historico no
esta sellado. Una falsa divergencia de 2.889 muestras Gold provenia de
comparar `ts` truncado a milisegundos con `now_utc` en microsegundos: las
2.889 diferencias eran menores a 1 ms. El comparador ahora admite solo el
intervalo implicado por la precision del dato, no un retraso arbitrario.

La auditoria de intervalo cruza cada P/L observado por el guard con el rango
exacto del modelo entre el tick fuente y la evaluacion. Exige tick en cinta,
reloj directo, FX causal, tickets iguales y ningun deal en medio. Revalida
los hashes al terminar para detectar entradas cambiadas durante la
reconstruccion. Los informes inmutables mas recientes son
`guard_native_interval_3086_v3.json` (SHA256
`A9E3A696A42A769E68740B11391465BBA5AF36D150DA0E233363229388AE5D9A`)
y `guard_native_interval_22732_v4.json` (SHA256
`9E9F9986CE69DDAFFAA5A96BE715F7D5EC2F5D2B8C75777A418113C0CA4D6C7A`).

- Gold: 3.118/3.135 dentro del intervalo, 16 bloqueadas por FX causal
  desconocido y una por deal nativo dentro del intervalo; ninguna fuera.
- Dubai: 2.270/2.278 dentro, cinco bloqueadas por deal y tres fuera: dos
  por 0,09 EUR y una por 0,01 EUR. El tick anterior podria explicar la
  ultima, pero no prueba retardo MT5; las otras dos siguen sin explicar.
  No se amplio ninguna tolerancia para absorberlas.

`material_coverage_partial_v15.json` (SHA256
`9A1E81E6BA15C5A83D0A2DD6F2B78C4F0FB4758ECDEBFAF31CDBED77BF549B0B`)
reconoce 4/249 tramos y 2/40 cestas emparejadas. Sigue
`diagnostic_only`, `full_source_time_coverage_verified=false`,
`full_cohort_coverage_verified=false` y
`full_simulator_path_parity_verified=false`. El intervalo tick-evaluacion
acota una lectura MT5 de hora exacta desconocida; estos valores tampoco son
una trayectoria independiente y continua de equity. Faltan indice remoto
completo y captura v3, cierre de origen, ampliacion a mas cestas y contraste
de decisiones, ejecucion, exposicion, flotante y drawdown de punta a punta.
Verificacion del piloto de intervalo, anterior al gate de proteccion v15:
`python -m pytest -q`, 7.702 aprobadas, una
advertencia conocida de pandas, 645,19 s en Python 3.11.9/Windows. Los
25 tests focalizados de intervalo, replay capturado y cobertura tambien
pasaron. Esta verificacion local no demuestra paridad completa ni despliegue.

## Cambios De Proteccion: Solicitud Frente A Instalacion Observada

El auditor semanal ahora separa por accion la solicitud de modificar SL/TP,
el intento enviado, el retcode, la confirmacion y un snapshot posterior
enlazado por accion, ticket, intento, decision y sesion. Solo el enlace
unico, cronologico y con nivel coincidente queda como
`observed_after_accepted_modify`; ni siquiera este estado conoce el momento
exacto de instalacion en el servidor. La captura incompleta queda bloqueada
y cada accion mantiene su denominador y estado. Las modificaciones tampoco
describen el SL inicial incluido en una orden de mercado.

En los cuatro tramos v2, Gold 3086 tiene 33 acciones solicitadas y 254
intentos de modificacion: 31 acciones con intentos rechazados (`10016`),
una coalescida sin intento directo propio y una solicitada sin intento
directo propio; ninguna modificacion aceptada ni snapshot posterior
de cambio de nivel. Dubai 22732 tiene tres acciones solicitadas y tres
intentos aceptados, cada uno con confirmacion y snapshot de nivel. No se
infiere que Gold careciera de SL inicial ni que el snapshot Dubai pruebe
una hora exacta de instalacion. El informe v15 anterior conserva
`full_simulator_path_parity_verified=false` y las otras 38 cestas pendientes.
Las pruebas focalizadas de cobertura, captura de gestion, colector e
intervalo pasaron (96/96). Este cambio es local y no se ha publicado.

## Atribucion Del Desfase De Flotante En Marcas Comunes

El informe `week_shadow_control_path_v8.json` (SHA256
`AF03FF8C510A9FBF85959CEDFC1C169C37F1FEB58462236EC3568A2B98AFAEED`)
anade una pareja diagnostica entre piernas virtuales y posiciones nativas
por orden de entrada y volumen, solo con reloj nativo directo. El enlace
individual pierna-orden sigue sin verificarse; un numero de entradas o
volumen distinto bloquea la atribucion. Para una marca con ambos lados
abiertos, sin cierres ni realizado, calcula el efecto de sustituir solo
el precio de entrada nativo por el virtual usando la misma cotizacion y
FX causal. No cambia el resultado observado ni la simulacion.

De 40 cestas nativas, 12 tienen control sombra en el fragmento; 11 admiten
pareja ordinal y una bloquea por numero de entradas. De 487 marcas comunes,
145 admiten esta atribucion estricta. En esas 145, el residuo monetario
tras el efecto del precio de entrada es 0,00 EUR; las otras 342 marcas no
quedan explicadas por este calculo, y 3086 no tiene ninguna marca apta.

En 3148, la sombra entro a 4381,35 / 4379,84 / 4378,18 y MT5 a
4381,79 / 4380,17 / 4378,75 para 0,04 / 0,03 / 0,03 lotes. La diferencia
de tiempo nativo menos virtual fue +1.375 ms, -1.060 ms y -13.140 ms,
respectivamente: la primera entrada llego despues, las otras dos antes.
Estas diferencias de exposicion siguen abiertas fuera de las marcas aptas.
La diferencia
de entradas equivale a 4,46 USD de movimiento ponderado; en el valle
compartido del 18-09, el efecto monetario bajo la misma cotizacion/FX fue
3,88 EUR. Alli la sombra marco -17,23 EUR y el modelo con fills nativos
-21,11 EUR, con residuo 0,00 EUR. El drawdown muestreado es 18,00 frente a
21,11 EUR porque la sombra tuvo antes un pico de +0,77 EUR cuando MT5
todavia no habia abierto. Esto atribuye el desfase de ese tramo a precio
de entrada y momento de exposicion, no a un error demostrado de conversion.
Las 25 marcas aptas de 3148 dejan residuo 0,00 EUR; las demas conservan
sus bloqueos por exposicion distinta o cierres.

La copia local de `telemetry_checkout` solo llega a unos 189 MB del
diario historico y no contiene los offsets de varios GB de esta semana:
no sustituye el indice remoto pendiente. El informe v8 sigue
`simulator_certified=false` y no observa equity continuo de MT5.
Nueve pruebas focalizadas del comparador y 26 pruebas conjuntas con los
auditores de material e intervalo pasaron. No hubo lectura remota nueva,
commit, push ni despliegue.

Para el siguiente contraste de proteccion, la captura local
`lifecycle_probe_3148_v1.json` conserva todas las categorias relevantes
segun sus propios contadores: 15 solicitudes `mt5_modify_requested`,
15 intentos, 11 confirmaciones, 11 snapshots y 55 transiciones sombra.
Puede usarse como piloto de niveles observados frente a niveles virtuales,
pero el probe es un extracto acotado, no la cobertura completa del origen.

El [contraste de proteccion 3148 v2](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3148_v2.json)
(`SHA256 1A21BD2A3CC968BDB02104EE5A1CA0A90BD267EAA0CEF3349069076BAA8AC95D`)
enlaza las tres piernas reales con eventos de fill, resultados MT5 y deals
nativos por ticket, precio, volumen, fingerprint y orden temporal. Esto
sustituye la mera pareja ordinal para esta cesta, no para las otras 11.
De 11 snapshots de modificaciones aceptadas, solo 3 admiten cotejo de
niveles con el ultimo estado sombra **emitido**: el SL virtual menos el
observado fue -0,44 / -0,35 / -0,44 unidades de precio XAUUSD; el TP
virtual menos el observado fue -0,44 / -0,57 / -0,44. En los 3 falta
`position_exists`, por lo que son comparaciones de campos, no prueba de
stop instalado. En otros 6 snapshots la pierna virtual ya no estaba
abierta; 2 se registraron despues del cierre nativo. Hay ademas un intento
de modificacion rechazado con retcode `10036`. La hora de emision del
snapshot no es la hora precisa de lectura MT5 ni de instalacion del stop.
La captura sigue acotada; no hay paridad de proteccion continua ni de
exposicion/drawdown integral. El auditor focalizado y los dos comparadores
adyacentes pasaron 23 pruebas. Todo es local, sin nueva lectura SSH,
commit, push ni despliegue.

El [contraste 3148 v3](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3148_v3.json)
(`SHA256 67BB67097A6D08ABF66049B8391A9EC1207EC9D6CA9403E7212E04991042DEF8`)
amplia v2 con el camino de salida por pierna. El codigo de Gold 555
calcula el TP desde el fill real y el motor sombra desde el fill virtual.
Las diferencias real menos virtual de entrada son +0,44 / +0,33 / +0,57
XAUUSD; las de TP observado antes del cierre son exactamente las mismas.
Los deals reales de salida coinciden con esos TP observados: 4382,29 /
4381,17 / 4380,25. Por tanto, para estas tres piernas la diferencia de
objetivo es explicable por la diferencia de fill, no por un parametro de
TP distinto. Los cierres reales sucedieron 11.829 / 231 / 14.470 ms
despues del tick de cierre virtual. Frente a la **emision** del evento
sombra, los desfases fueron +9.728 / -1.343 / +10.529 ms: la segunda
emision ocurrio despues del cierre real. Ninguno de esos intervalos mide
aisladamente latencia de la VM. Los snapshots carecen de
`position_exists` y su hora no es la lectura exacta del broker; el match
de nivel y precio de salida no certifica el disparador de TP ni proteccion
continua. Las tres pruebas focalizadas del auditor pasan; v2 queda como
antecedente, no como ultimo resultado.

El auditor actualizado confirma la misma regla en
[3148 v4](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3148_v4.json)
(`SHA256 510EE03EBCEE6B9B4173C6DB66FB53EF140573FE7434B7C4E88B3B0FE6590882`),
[3168 v1](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3168_v1.json)
(`SHA256 F0EDA59D5C477D72ABD2074EDA37185414342432B04EB40D9AB47DA690E3CB0E`)
y [3171 v2](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3171_v2.json)
(`SHA256 15810D78A89EDBDAAE1F4A3233F43A4F98600756B160609EF3B81934CC9BA1C1`).
Las 9 piernas reales de las 3 cestas enlazan fill, resultado MT5 y deal
por ticket/precio/volumen. En las 9, el cambio de TP observado antes del
cierre coincide con el cambio de fill, y la salida real coincide con ese
TP. En 3168 el probe limita transiciones sombra a 100 de 125, pero las
125 se verificaron contra el fragmento sombra local con hash y los 100
IDs capturados estan contenidos en el; las otras clases materiales del
probe no estaban truncadas. Una pierna de 3168 cerro en real 38.655 ms
**antes** del tick de cierre virtual: el desfase de cierre no equivale a
latencia positiva de ejecucion. Hay 37 snapshots con niveles cotejables
entre las tres cestas, todos sin `position_exists`; no prueban instalacion
ni continuidad de TP o SL. La muestra sigue siendo diagnostica y no
certifica el simulador. Cuatro pruebas focalizadas del auditor pasan;
v2/v3 de 3148 y v1 de 3171 quedan como antecedentes.

El gate temporal se anadio en
[3148 v5](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3148_v5.json)
(`SHA256 6622CF0495ACCCC0B2A135E168C16260FD3752900F2B7F6809F6981FD488CA5A`),
[3168 v2](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3168_v2.json)
(`SHA256 B3CA80B661ACEF884A17999CA42FA51507EED41C956BBE1702FEB80C00061533`)
y [3171 v3](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3171_v3.json)
(`SHA256 BE36DED198204D6632D61D9CFA95D2878789C63CB81117A70918B8C7840608BA`).
En 3171 la orden inicial no pidio TP, hubo dos intentos de modificacion
rechazados con `10016` y la primera respuesta aceptada de TP llego
4.445 ms despues del tick de cierre virtual, 4.745 ms despues del deal
de entrada real. En las otras ocho piernas la respuesta aceptada precede
al tick virtual de cierre. Esta es una ventana de **aceptacion observada**,
no una medida del instante de instalacion en el servidor. Tampoco prueba
que el rechazo causara por si solo la diferencia de salida: el TP real
era distinto por el fill. Los informes mantienen
`broker_tp_install_verified=false` y `full_exit_causal_path_verified=false`.
La hora de ese tick no es la hora en que se emitio la decision virtual;
la cronologia se separa en los informes actualizados al final de esta nota.

La comprobacion del motivo nativo de salida, ausente en la version previa,
queda en [3148 v6](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3148_v6.json)
(`SHA256 9A82AB4C6B520274105B7647918BE28E8505726A81911D2439BE2C2934A588E4`),
[3168 v3](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3168_v3.json)
(`SHA256 E53CBF8D2CA4CD65929E4F81ADE08AAAFB23F561BD77F07B57846E683F459049`)
y [3171 v4](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3171_v4.json)
(`SHA256 E8438AB00FD28C150C157CEEF3453F94C3BBFC67E5646FB1DC826F9BCCA9CCF2`).
Los 9/9 deals de cierre tienen motivo nativo `5 = tp`, ademas de precio
igual al TP observado antes del cierre y diferencia de TP igual a la
diferencia de fill. La [referencia oficial de MQL5](https://www.mql5.com/en/docs/constants/tradingconstants/dealproperties)
define `DEAL_REASON_TP` como activacion de Take Profit. Esto verifica el
mecanismo de salida de esas nueve piernas, pero no la decision que fijo
el nivel ni su tiempo preciso de instalacion. Los tres informes mantienen
`broker_tp_install_verified=false` y `full_exit_causal_path_verified=false`.
El auditor retiene el hash del modulo local que interpreta el codigo de
motivo; una coincidencia de precio con un cierre experto ya no pasa el gate.

El [comparador semanal v10](../../runtime_data/week_native_ticks_20260923_v1/week_shadow_control_path_v10.json)
(`SHA256 FE57434EAD35417B0A03EEBFDA12E1CE8165D97E40F4F50CCB0ED08C83EA1278`)
admite los tres informes de enlace real como fuentes con hash y valida
tambien sus dependencias originales antes de elevar una pareja. En 3148,
3168 y 3171, ticket, precio y volumen verificados coinciden con los
pares que v9 habia supuesto; ninguna contradiccion se descarto ni se
remapeo. Son 132 de las 320 marcas atribuidas por precio de entrada con
identidad de pierna real verificada y residuo 0,00 EUR. Las otras 188
mantienen emparejamiento ordinal/por volumen. No cambian los 487 puntos
del denominador, los 167 sin atribucion, la falta de equity MT5 continuo
ni `simulator_certified=false`. El comparador bloquea ante un reporte de
enlace contradictorio o con hashes de fuente distintos; sigue todo local.

[v11](../../runtime_data/week_native_ticks_20260923_v1/week_shadow_control_path_v11.json)
(`SHA256 474F594F59B5F79442E657F6362E01A63EA06BC32F589DA5C7AE599C01F22B57`)
es el informe vigente del mismo contrato: conserva 132/320 marcas con
identidad verificada y los mismos deltas de v10, y vuelve a comprobar al
final que tampoco cambiaron las fuentes de los tres informes de enlace.
Una prueba nueva confirma que una fuente modificada bloquea el enlace.

El [comparador semanal v12](../../runtime_data/week_native_ticks_20260923_v1/week_shadow_control_path_v12.json)
(`SHA256 DA0F2CFEFD189464EA0A8DFDFDCD2E820E226C927A7E563945CAC2BF4CE155DC`)
anade clasificacion de exposicion por pierna solo donde el ticket esta
verificado. En las 167 marcas de 3148/3168/3171: 132 tienen las mismas
piernas abiertas y residuo monetario 0,00 EUR despues del efecto de
entrada; 30 tienen una diferencia de conjunto abierto, de las cuales
18 caen entre entrada virtual y real y 12 entre salida virtual y real;
5 estan planas en ambos lados y tienen delta total 0,00 EUR. La suma
de volumen por piernas reproduce el delta agregado en todas las marcas
comparables; cualquier contradiccion bloquea el informe. Los 30 casos
no quedan monetariamente reconciliados por este clasificador: solo se
localiza el intervalo de evento que separa las exposiciones. El analisis
usa retrospectivamente los tiempos finales, no inyecta esos tiempos
futuros en un replay causal. v11 queda como antecedente; siguen faltando
marcas entre transiciones y equity real continuo de MT5.

El [contraste sombra/nativo v9](../../runtime_data/week_native_ticks_20260923_v1/week_shadow_control_path_v9.json)
(`SHA256 8B1B84261BA30ED104B1D2E37EC352A08EF38BB8E5D1DF11BE46142DA8C690DB`)
amplia la atribucion del precio de entrada a marcas posteriores a un
cierre parcial, pero solo con mismo dinero realizado, mismas piernas
cerradas y mismas piernas abiertas. De 487 marcas, 320 quedan atribuidas
(145 previas y 175 nuevas); en las 175 nuevas el residuo bajo cotizacion
y FX comunes es 0,00 EUR. Quedan 86 con exposicion/identidad diferente,
77 con dinero realizado diferente y 4 sin efecto evaluado. Esas 4 son
las de `canal1_22781`, que no tiene pareja de entradas admisible; ademas,
dos de sus marcas carecen de FX causal reciente. El emparejamiento de
piernas de este informe semanal sigue siendo ordinal/por volumen. El
enlace independiente de 3148 del informe de proteccion aun no alimenta
este calculo semanal. La
atribucion no demuestra paridad de cierre ni fills contrafactuales: los
175 casos nuevos comparten cierre parcial real/sombra como condicion de
entrada. Los casos divergentes siguen siendo trabajo pendiente, no
descartes. Diez pruebas focalizadas del comparador pasan.

El [auditor de rejilla v5](../../runtime_data/week_native_ticks_20260923_v1/risk_grid_sampling_all_controls_v5.json)
(`SHA256 4FFE135EEC59E05E0607537ABF67E7A0B8ADF71035F2A273BE64E94D8ACB3D55`)
reproduce el hash de la trayectoria nativa completa de cada uno de los
12 controles. Sobre **esa misma trayectoria monetaria**, compara el
drawdown de tres rejillas: transiciones, transiciones mas limites de
deals nativos y todos los ticks retenidos. Mantiene identicos fills, costes, FX,
orden de eventos y convencion de eventos en el mismo milisegundo; la
diferencia entre las ultimas dos columnas procede exclusivamente de omitir
marcas intermedias. Las 12 cestas muestran una diferencia positiva, pero
solo cinco tienen FX estrictamente causal en toda la trayectoria. En
estas cinco: `canal1_22768` pierde 0,84 EUR de drawdown modelado,
`canal2_2977` 3,15, `3148` 3,32, `3168` 26,34 y `3171` 4,22.
Para `3168`, el maximo pasa de 37,20 EUR en transiciones a 39,31 EUR
al sumar limites de deals (+2,11), y a 65,65 EUR con todos los ticks
(+26,34). El calculo anterior de 37,20 EUR coincide exactamente con
la primera columna al usar el mismo modelo; los 2,11 EUR no eran
ticks intermedios. En `3096` los deals anaden 42,57 EUR, pero su FX
completo no es causal. En `22781` el comparador anterior daba 5,29 EUR
y la rejilla nueva de transiciones 24,13: el anterior excluye marcas
con FX causal caducado mientras el modelo completo usa cobertura
retrospectiva, de modo que esa cifra tampoco admite inferencia causal.
`3086` tiene 0/6 marcas de transicion
dentro del intervalo nativo: su rejilla reducida consta solo de los
dos limites de deal, por lo que aqui falta cobertura de sombra del
trayecto, no simplemente un pico entre marcas frecuentes. Los siete
controles con FX retrospectivo son diagnosticos, no admision causal.

Este resultado identifica un limite **del muestreo**, no demuestra
drawdown observado de MT5 ni paridad real-virtual. El siguiente gate es
comparar exposicion, flotante y equity independientes y suficientemente
continuos contra un replay causal en la misma rejilla, recuperando antes
las trazas de origen incompletas. El nuevo auditor y los comparadores
adyacentes pasan 47 pruebas focalizadas; todo sigue local, sin cambio
de politica, push ni lectura remota adicional.

La auditoria de cronologia corregida esta en
[3148 v7](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3148_v7.json)
(`SHA256 4D607617E30EBC2ECA2542C9358AE22397133084C8D3524B0788DAA4594ADACF`),
[3168 v4](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3168_v4.json)
(`SHA256 2A76E59010AB3BB5C30DD2DEF8D22CD4B7000EB3375C3A6872EB5C337FAB37D8`)
y [3171 v5](../../runtime_data/week_native_ticks_20260923_v1/shadow_live_protection_3171_v5.json)
(`SHA256 2285EDAD7EE099F336C961ACB81673EE30E029234BF776D39A0420FC60BB1C87`).
De nueve piernas con ticket verificado, ocho recibieron el acuse TP antes
del tick virtual de cierre. En `3171`, el tick virtual fue a las
12:27:58.896 UTC y la respuesta aceptada a las 12:28:03.341, pero el
evento `virtual_position_closed` se emitio a las 12:28:03.380: **39 ms
despues** de la respuesta, con tick de 4.484 ms de antiguedad. Por ello
el campo anterior `virtual_close_before_accepted_tp_response` era
ambiguo: describia solo el orden de marcas de tick, no la emision de la
decision. El gate ahora conserva ambos relojes por separado. Tampoco
afirma el instante exacto de calculo del motor o instalacion del TP en
el servidor. El [comparador semanal v14](../../runtime_data/week_native_ticks_20260923_v1/week_shadow_control_path_v14.json)
(`SHA256 5A0236E57B741F2584FDE011E39AB263E84864A6C2C2FFDC9E5EAF4A913DFCB5`)
enlaza estos informes y mantiene identicos los 12 controles y sus
marcas respecto a v13, salvo el nuevo desglose de antiguedad por tipo
de transicion. El auditor de rejilla v5 mantiene identicas las 12 filas
de riesgo de v4 y actualiza la procedencia a v14.

El desglose v14 separa `virtual_fill`, `virtual_position_closed` y
`shadow_signal_closed`: en `3171`, sus maximos de antiguedad son
2.484, 4.484 y 459.983 ms. El ultimo es un evento terminal que
reutiliza un tick anterior; no mide 460 s de retardo de una orden.
En `3086`, el `virtual_fill` de control se emitio 68.469 ms despues
del tick; los 67.547 ms de `mt5.order_send` real proceden de otra
medicion y no se suman ni se infieren uno del otro. El codigo local
`_strategy_shadow_loop` procesa lotes de ticks historicos y cede
prioridad a ordenes reales antes de procesarlos; `_process_tick_batch`
cede el event loop entre ticks. Esto explica por que la edad tick-emision
de sombra puede incluir replay/cola, sin identificar por si sola la
causa de cada espera ni el retardo del bot real. Los mecanismos se
mantienen como gates independientes para el replay causal.

El [auditor de solicitudes TP v1](../../runtime_data/week_native_ticks_20260923_v1/tp_request_timeline_3148_3168_3171_v1.json)
(`SHA256 8D1127B157E8B3C87E2B20C4BA7B6431505F025585C9B71DC8F2D98E13C68DC0`)
enlaza pedido inicial, resultado, deal nativo, ticket, intentos
`MODIFY_SLTP`, snapshot y cierre virtual en las nueve piernas verificadas.
Las **9/9 ordenes iniciales** pidieron `tp=null`; el live solicito el
TP objetivo despues mediante modificaciones. Ocho recibieron respuesta
aceptada antes del tick de cierre virtual y las nueve antes de la
**emision** de ese cierre. En `3171`, el primer pedido TP al broker
empezo 144 ms antes del tick virtual y seguia en vuelo en ese tick;
posteriormente fue rechazado (`10016`). Hubo otro rechazo y la primera
respuesta aceptada del TP objetivo llego 4.445 ms despues del tick,
39 ms antes de emitir `virtual_position_closed`. El pedido en cola
`mt5_modify_requested` no se confunde con una llamada broker enviada.
El motor sombra, en cambio, crea `target_price` al llenar virtualmente
y permite cerrar por ese nivel inmediatamente. El intervalo inicial
entre **intencion** y **confirmacion** existe como mecanismo general,
pero no se le asigna P/L causal sin el recorrido de precio y los
estados del broker. La respuesta aceptada no identifica el instante
exacto de instalacion, y los snapshots extraidos por los probes antiguos carecen de
`position_exists`; tampoco se descartan modificaciones externas fuera
de la ventana acotada. El replay observado debera conservar estados
`solicitado`, `en vuelo`, `rechazado` y `aceptado/observado` por ticket,
con incertidumbre temporal, antes de comparar cierres y drawdown.
Pasan 51 pruebas focalizadas del conjunto de auditorias de riesgo,
control y proteccion. Todo sigue local.

Revision de la **proyeccion del probe**, no de MT5: los cinco informes
acotados mas recientes de `3148` (11 snapshots), `3168` (38), `3171`
(2), `3086` (1) y `22732` (4) registran `position_exists` en
`field_names_by_kind.mt5_position_snapshot`, que se obtiene del esquema
de las lineas del diario original. Sin embargo, **0/56 eventos
seleccionados** conservan el valor. Los cinco informes proceden del
extractor con SHA256
`C8FAED0EAE3E546DC5EE68EA2C71172832DD619DF9AF5A5987D254C07EFAC35F`;
el extractor local actual admite booleano y `null` para ese campo y
las 11 pruebas de `test_probe_vm_signal_lifecycle.py` pasan, incluidas
dos regresiones nuevas. El codigo de `pending_actions.py` del commit
historico `7e33c116` tambien establece explicitamente el campo en
lecturas presentes, vacias o fallidas. Por tanto, **la ausencia en los
informes es compatible con perdida en la extraccion**; no se infieren
los valores individuales del inventario de nombres.

La siguiente captura, en ventana de baja carga y solo lectura, debe
reutilizar los offsets acotados de esos probes, exigir el mismo
`source_window_sha256`, las mismas cuentas/identidades y todos los
campos previamente capturados, y anadir unicamente `position_exists`
con su valor real. Solo entonces se regeneraran los informes de
proteccion y se evaluara si algun snapshot verifica existencia y nivel
en una hora util. Esta recuperacion no sustituye una trayectoria
independiente y continua de equity ni los 245 tramos materiales aun
no capturados. No se leyo la VM ni se cambio produccion en esta revision.
El verificador local `tools/verify_lifecycle_probe_projection.py` ya
compara captura vieja/nueva y bloquea cambios de hash, offsets,
inventario, identidad, orden o campos previos; solo admite
`position_exists` booleano o `null` anadido a snapshots. No se ha
ejecutado aun contra una captura remota nueva. Las pruebas focalizadas
de esta cadena y las auditorias adyacentes pasan 66 casos.
La regresion del auditor de niveles comprueba que `position_exists=true`
permite `compared_level`, `false` bloquea por posicion ausente y la
ausencia sigue sin certificar instalacion. La funcion `_installed_state`
del comparador forward exige, ademas, request/result nativos completos,
revision, simbolo, magic, precio de apertura, volumen y deals. Por ello
la recaptura acotada puede mejorar el piloto sombra, pero **no** abre
por si sola el gate integral. Las 74 pruebas focalizadas de ambos
comparadores, extractor y verificador pasan; no hubo lectura remota.

El [cruce de ticks antes del acuse TP](../../runtime_data/week_native_ticks_20260923_v1/tp_preack_retained_touches_3148_3168_3171_v1.json)
(`SHA256 C5B125A05C42287A7FC8802151656682CABF5E786D770CFD2573DFAB6682B53A`)
separa un segundo mecanismo de la diferencia de niveles. Usa las nueve
piernas del timeline anterior, el dinero nativo, anclas directas del
18-09 y los ticks XAUUSD originales ligados por hash. Busca desde el
fill nativo hasta, sin incluirlo, el primer acuse aceptado para el TP
objetivo; BUY se compara con Bid y SELL con Ask. **8/9 piernas no tocan
el TP nativo** en ese intervalo retenido; `3171` tiene 2 toques en
58 ticks. El primero, Bid 4356,37, fue a las 12:27:58.962 UTC, 66 ms
despues del tick de cierre virtual y durante un intento de modificacion
que luego devolvio `10016`. La primera respuesta aceptada llego a las
12:28:03.341 UTC. Esto no permite atribuir por si solo la falta de
cierre nativo a una proteccion no instalada: no hay lectura continua del
estado del servidor y el acuse no es su reloj de instalacion. Tampoco
permite usar la ausencia de toque en las otras ocho como prueba de
paridad de sus trayectorias completas. El auditor bloquea intervalos que
cruzan dias sin una unica ancla directa. Si se modela el TP instantaneo
de la sombra como efectivo en live, este control muestra una ventana
material que quedaria oculta. Las ocho pruebas focalizadas del cruce
y timeline pasan; resultado local, sin contacto adicional con la VM.

El [cruce del acuse al deal TP](../../runtime_data/week_native_ticks_20260923_v1/tp_response_to_native_exit_3148_3168_3171_v1.json)
(`SHA256 0CC8808DCD545D1D6E81246ADAC0C333D7E971272CE09AD7D8E0B423B66B15EF`)
conserva el mismo denominador y busca el primer toque del objetivo en
ticks retenidos estrictamente despues del acuse aceptado y antes del
deal nativo; los empates de milisegundo no se ordenan por convencion.
Las 9/9 piernas tienen un toque previo al deal. Siete salen entre 2 y
19 ms tras el primer toque. `3168` pierna 0 tarda 1.614 ms y registra
22 toques; Bid baja de 4363,00 a 4362,93 en dos ticks antes de salir.
`3168` pierna 2 tarda 476 ms y registra ocho toques; todos los Bid
retenidos desde el primero permanecen sobre su TP 4360,82 (minimo
4360,88). El beneficio final en el objetivo no identifica el tiempo
real de exposicion. Ni el primer tick favorable es un trigger broker
verificado ni el deal revela el instante interno de activacion del TP.
Las 11 pruebas focalizadas de la cadena temporal pasan; falta comparar
riesgo monetario simultaneo de la cesta y ampliar el control semanal.

El [estado causal de recibos TP](../../runtime_data/week_native_ticks_20260923_v1/tp_client_receipt_state_3148_3168_3171_v1.json)
(`SHA256 4107DA4B966725FF0B3EC8ED2C9527F798CA9C4CB1C31F5F1A38B12FDE707A61`)
une los tres informes anteriores por ticket, objetivo, acuse y hashes.
`research/observed_tp_receipts.py` aplica solo respuestas anteriores al
tick consultado; igualdad de milisegundo bloquea el orden, y un intento
en vuelo conserva resultado desconocido aunque el informe completo
contenga su rechazo futuro. En `3171`, el primer toque preacuse es
`pending_target_outcome_unknown`. Los nueve primeros toques posteriores
al acuse ven objetivo aceptado por el cliente: cinco sin peticion
pendiente y cuatro con otra peticion identica en vuelo. Ningun estado
afirma instalacion observada en servidor, trigger de TP o paridad de
equity. Las 14 pruebas focalizadas de esta cadena pasan. El siguiente
paso de replay debe usar estos estados sin inyectar el deal futuro,
contrastar la duracion abierta y el flotante de **todas** las piernas
en una rejilla comun y bloquear los periodos sin observacion suficiente.

El [contraste de riesgo con cierre mecanico en primer toque](../../runtime_data/week_native_ticks_20260923_v1/tp_first_touch_risk_3148_3168_3171_v1.json)
(`SHA256 E508A258EA0BE77BADE5F84E4E127CD686D172D133E2954A14D55E2AB92A082E`)
usa los mismos fills, TP, dinero nativo y ticks XAUUSD/EURUSD; solo
adelanta cada salida al primer toque retenido posterior al acuse.
`compare_risk` coteja ambos recorridos en una rejilla comun estricta,
sin FX retrospectivo. Las metricas del lado observado coinciden
**exactamente** con `native_week_risk_path_v1` para las tres cestas.
`3148` difiere en tres pares de muestras y hasta 0,13 EUR de total;
`3168` en 35 pares y hasta 0,76 EUR; `3171` en un par solo por estado
de exposicion, con diferencia total 0,00 EUR. Pese a ello, los tres
beneficios finales y sus maximos drawdowns modelados siguen iguales:
24,43, 65,65 y 4,22 EUR. Es una demostracion concreta de que
concordancia de P/L y drawdown agregado **no** implica paridad del
recorrido. La salida al primer toque es una hipotesis mecanica que
usa el futuro tape retenido, no una ejecucion broker observada ni una
politica candidata. Pasan 16 pruebas focalizadas de la cadena. Falta
equity MT5 independiente y cobertura semanal del ciclo completo.

Revision adicional de la fuente de flotante nativo: las 15 filas no
planas de `native_account_snapshots_v2.json` son todas
`no_direct_anchor`; las 14 filas con ancla directa estan planas.
Ademas, el commit historico que emitio `mt5_account_connected` leyo
`account_info()` antes de otras llamadas MT5 y del evento, sin inicio
ni fin de la lectura. Por tanto, incluso las tres coincidencias
numericas no fijan una marca independiente de equity al mismo tick.

Los cinco probes acotados (`3148`, `3168`, `3171`, `3086`, `22732`)
tienen 56 snapshots. El inventario del origen indica `price_open`,
`price_current`, `position_type`, `symbol`, `magic` y `comment` en
los probes Gold `3148`, `3168`, `3171` y en el Dubai `22732`.
La proyeccion vieja omitio todos esos campos; `profit` si quedo en
51/51 snapshots Gold y tres de cuatro Dubai. El extractor local ahora
puede conservar precio e identidad.
`verify_lifecycle_probe_projection.py --restore-native-details`
mantiene byte-hash, offsets, orden, identidades y todos los campos
anteriores; exige detalle nativo valido si la posicion existe y el
origen declara esos campos. El modo anterior de solo
`position_exists` sigue disponible. Pasan 79 pruebas del extractor,
verificador, proteccion y comparador forward, incluida una
reproyeccion con el mismo hash de bytes sinteticos. Las cinco recapturas
remotas y sus limites constan abajo. La lectura se hizo acotada y de baja
prioridad; no se usa
para declarar equity continua ni instalacion temporal de stops.

El [control de beneficio nativo por posicion en ventana causal](../../runtime_data/week_native_ticks_20260923_v1/native_position_profit_prior_quote_5probes_v1.json)
(`SHA256 C303102B95D59A3BA22021080644089BAF03953C461CEF23D785CE3221B725D6`)
usa los cinco probes viejos, sus tickets y volumen, las entradas
reconciliadas, el contrato EUR y los dias crudos XAUUSD/EURUSD ligados
por hash. Para cada evento busca unicamente ticks anteriores de los
ultimos 5 s con FX previo causal y edad maxima contractual. De los 56
snapshots, 54 tienen `profit` nativo y **54/54** encuentran una
valoracion exacta al centimo; dos quedan `blocked_missing_native_profit`
(`3086` y una lectura Dubai `22732`). En 48/54 el tick coincidente mas
reciente tiene edad <=1 s; los 54 tienen edad <=2 s (maximo 1.979 ms).
Este resultado apoya el calculo de beneficio abierto para las piernas
observadas, no acredita el precio efectivo de lectura: la hora de
`positions_get` no se registro y el test busca entre varios ticks.
El precio de apertura procede de los deals nativos porque la antigua
proyeccion no conservo `price_open`. La recaptura de los mismos bytes
permitira verificar ambos precios e identidad en el mismo snapshot.
Tampoco hay aqui equity continua de cuenta ni paridad de drawdown.
Pasan las catorce pruebas focalizadas del auditor y sus contratos
adyacentes, incluidas ventas y FX previo caducado. Resultado solo
local, sin nueva lectura VM.

Primera recaptura de detalle nativo: SSH respondio a una prueba ligera
el 23-09 a las 19:16 UTC; el extractor de baja prioridad releyo solo
la ventana acotada de `3171` (10.264.564 bytes), sin escribir en la VM.
El [probe v2](../../runtime_data/week_native_ticks_20260923_v1/lifecycle_probe_3171_native_details_v2.json)
(`SHA256 C3931427D36C1BA4781AC2B93C2C9917F43E88A444EB35C8F9A707DFBCCB4B5D`)
conserva el mismo SHA256 de bytes, offsets, 304 eventos e inventario
que el probe anterior. El
[verificador v3](../../runtime_data/week_native_ticks_20260923_v1/lifecycle_probe_3171_native_details_verification_v3.json)
(`SHA256 2B88AFEEE43E2E41477BECAC4A3490E1BA2BDFE9E53086A2A45459DB5BB58386`)
conserva todos los valores viejos y admite los campos nuevos del extractor
solo cuando constan en el inventario de origen o derivan de `request/result`.
Hay expansion de esquema en 269 eventos; no se ha atribuido esa expansion
a un cambio en los bytes fuente. Ambos snapshots estan abiertos y recuperan
`symbol`, `magic`, `position_type`, `price_open` y `price_current`.

El [contraste conjunto de precio y beneficio](../../runtime_data/week_native_ticks_20260923_v1/native_position_mark_profit_3171_v1.json)
(`SHA256 570D7147B2970D5B255ACB6DB24336B34426C41B0CAAAA609138B2E80FD2008D`)
exige un **mismo tick XAUUSD previo** cuyo Bid reproduzca el
`price_current` de la posicion BUY y, con EURUSD causal, su `profit`.
Las 2/2 lecturas lo encuentran, con 130 y 211 ms de edad minima del
tick coincidente. `price_open` coincide con el deal nativo y
`position_type` con BUY. Sigue siendo un control de intervalo: no se
registro el momento exacto de `positions_get`, no hay serie de equity
independiente. Ni SSH ni la salud
del bot quedan certificados por esta prueba puntual. Pasan 36 pruebas
focalizadas de extractor, verificador, dinero y auditor.

Actualizacion del mismo control: tambien se recapturaron, por separado y
sin escritura en VM, las ventanas de `3148` (12.869.552 bytes), `3086`
(17.443.908), `3168` (38.033.303) y `22732` (25.316.438). Cada
[verificacion 3148](../../runtime_data/week_native_ticks_20260923_v1/lifecycle_probe_3148_native_details_verification_v3.json),
[3168](../../runtime_data/week_native_ticks_20260923_v1/lifecycle_probe_3168_native_details_verification_v3.json),
[3086](../../runtime_data/week_native_ticks_20260923_v1/lifecycle_probe_3086_native_details_verification_v3.json)
y [22732](../../runtime_data/week_native_ticks_20260923_v1/lifecycle_probe_22732_native_details_verification_v3.json)
confirma hash de bytes, offsets, inventario, orden, valores anteriores y
expansion de esquema restringida. Los cinco probes suman 56 snapshots:
`3148` 11 abiertos, `3168` 38, `3171` dos, `22732` tres abiertos y
uno cerrado, `3086` uno cerrado. Ninguno es una observacion continua.

El [informe conjunto de precio y beneficio](../../runtime_data/week_native_ticks_20260923_v1/native_position_mark_profit_5probes_v1.json)
(`SHA256 BFA3AF222B2B05742981824CAB602E5E56E70651C22DD703AD28AEAD8BFAF260`)
liga los cinco probes nuevos y los mismos archivos de dinero, reloj y
ticks por hash. Los **54/54 snapshots abiertos** recuperan precio de
entrada, direccion, simbolo y una cotizacion XAUUSD anterior cuyo lado
Bid/Ask es el `price_current` nativo y, con EURUSD causal, reproduce el
`profit` al centimo. En 48/54 el tick coincidente mas reciente tiene
edad <=1 s; todos 54 <=2 s (maximo 1.979 ms). Los otros dos snapshots
tenian `position_exists=false` y `profit` ausente; se mantienen
`blocked_missing_native_profit`. El resultado valida la coherencia de
valoracion por posicion dentro del intervalo, **no** el instante exacto
de lectura ni la equity y el drawdown completos de la cuenta. El gate
semanal de cobertura de origen y trayectorias sigue abierto.

Para dimensionar ese gate se leyo un solo [bloque inicial de indice de
64 MB](../../runtime_data/week_native_ticks_20260923_v1/journal_prefix_chunk_0000_64m_v1.json)
(`SHA256 80D8C0B3ECC27DB1E98979C14728DDF1C56E55328FEA10A797A4DD5AB51830FC`)
del prefijo congelado de 9.895.771.380 bytes. El worker de prioridad
baja termino en 4,7 s; alineo 64.000.084 bytes, conto 122.499 eventos
y detecto dos retrocesos temporales entre lineas contiguas. Ese primer
bloque ya mezcla fechas de junio y agosto, por lo que no se puede
inferir cobertura temporal a partir de un offset aproximado. Quedan
154 bloques de hasta 64 MB; **no** se ejecuto el barrido completo
durante el mercado abierto. Pasan 37 pruebas focalizadas de indice,
extractor, proyeccion y dinero. Este piloto tampoco certifica salud
continua de SSH o del bot.

Instrumentacion prospectiva **solo local, no publicada**: la lectura
`positions_get(ticket=...)` que ya hacia `PendingQueue._position_snapshot`
registra ahora `positions_read_started_utc`,
`positions_read_completed_utc` y `positions_read_elapsed_ms` tanto para
posicion abierta como ausente o error. No se agrego ninguna llamada MT5
ni un evento adicional; el extractor conserva esos tres campos solo en
`mt5_position_snapshot`, y el verificador exige un intervalo UTC completo
y ordenado. Esto permitira acotar el tick de valoracion en futuras
capturas, pero no corrige las antiguas ni observa equity de cuenta.
El extractor de las cinco recapturas quedo archivado en
[`probe_vm_signal_lifecycle_recapture_source_d18e7f89.py`](../../runtime_data/week_native_ticks_20260923_v1/probe_vm_signal_lifecycle_recapture_source_d18e7f89.py)
(`SHA256 D18E7F893701E5C2091B6357E063B96255B7FDCB8CFADC6A4BDEEAB832CA8794`),
y el verificador correspondiente en
[`verify_lifecycle_probe_projection_recapture_source_9b52b5c7.py`](../../runtime_data/week_native_ticks_20260923_v1/verify_lifecycle_probe_projection_recapture_source_9b52b5c7.py)
(`SHA256 9B52B5C73F1447FFB35B3F30DF4F26C3A3B31B490AC846DA933C0230E05B8428`),
para conservar la procedencia de sus informes despues del cambio.
Verificacion: `python -m pytest -q`, 7.761 aprobadas, una advertencia
de asignacion pandas en un test de dataset ajeno a esta modificacion.

El [contraste de exposicion en snapshots v2](../../runtime_data/week_native_ticks_20260923_v1/shadow_snapshot_exposure_3148_3168_3171_v2.json)
(`SHA256 019978F3F7C75176A3B163CF3A7B70637CB9497EB4A04D241A0162DAA5FD3C9C`)
compara los 51 snapshots nativos Gold con el estado del control sombra,
ligando los probes, la cadena de estados y las anclas de reloj/dinero por
hash. Solo admite ventanas tranquilas de 5 s: 18 lecturas tienen el mismo
volumen abierto y tres de `3148` difieren (0,10 lotes reales frente a
0,07 virtuales). Otras 13 quedan bloqueadas por un deal nativo reciente,
diez por un evento sombra reciente, una por estado sombra antiguo y cuatro
por orden de lectura y emision desconocido tras un cierre. Dos mas quedan
bloqueadas porque el tick de salida virtual precedio a la lectura real,
pero su evento se emitio despues. La v1 habia contado erroneamente esas
dos como iguales; una prueba de regresion conserva el caso. Ninguna se
descarta del denominador. La sombra es un control en vivo muestreado, no
un replay independiente; este contraste no certifica equity ni drawdown.

Las dos primeras diferencias estables de `3148` son a las 11:06:00,100
y 11:06:03,238 UTC: la tercera entrada real ya estaba abierta, mientras
la virtual aun no habia entrado. La virtual entro a las 11:06:07,608 UTC
y cerro esa pierna a las 11:06:13,468; la lectura real de las
11:06:17,399 queda incierta porque el cierre virtual no se emitio hasta
las 11:06:17,409. A las 11:06:24,063 el real sigue con 0,10 y el
virtual tiene 0,07 por ese cierre anterior. Por
tanto, las tres discrepancias no tienen una sola causa demostrada:
abarcan tanto entrada como salida. En vivo, la tercera orden se solicito
a las 11:05:54,490, el broker confirmo a las 11:05:55,972 y el TP se
confirmo a las 11:06:00,100; no se ha demostrado si la diferencia de
entrada viene de la regla, de la secuencia de ticks o del momento de
evaluacion. No se ha cambiado produccion.

El [diagnostico general del ancla de la escalera](../../runtime_data/week_native_ticks_20260923_v1/gold_verified_ladder_anchor_3signals_v2.json)
(`SHA256 4643749E47A5126B07E70D0E020EB9601FFB40C6A817104134DD1B2F8344DBF3`)
usa solo entradas Gold con identidad nativa verificada y enlaza cada
solicitud al resultado y ticket broker. Conserva el denominador: 23
cestas Gold reales en el extracto semanal, siete con control sombra
comparable, tres con vinculacion de entradas verificada y examinadas,
cuatro controles aun ordinales y 16 cestas sin comparacion.

La politica 555 fija cada nivel a 1,5 unidades por pierna desde la
**primera ejecucion**, no desde la primera solicitud. En `3148`, la
primera solicitud y la entrada virtual fueron 4381,35, pero el fill
nativo fue 4381,79: ancla +0,44. Para la segunda y tercera entrada,
los niveles nativos fueron 4380,29 y 4378,79, frente a 4379,85 y
4378,35 en sombra. Las solicitudes nativas a 4380,25 y 4378,74
cruzaron los niveles reales, pero **no** los virtuales bajo la misma
cotizacion. Esto explica por que la sombra no habria entrado en esos
instantes con su propio ancla; no demuestra que hubiese procesado el
mismo tick en el mismo momento ni explica aun el cierre posterior.
En `3168` ambas primeras ejecuciones fueron 4362,50: sus cuatro
solicitudes posteriores cruzaron los mismos niveles en ambos lados.
`3171` tuvo una sola entrada, por lo que no contrasta esta cascada.

El primer fill nativo de `3148` consta en el ledger 1,375 s despues
del tick de entrada virtual; la solicitud nativa usaba 4381,35 y el
broker lleno a 4381,79. El diario nativo emitio la confirmacion de
vigilancia y la solicitud a las 11:05:41,554 UTC, y el resultado llego
al cliente a las 11:05:43,155. El tick XAUUSD de 11:05:40,191 UTC
tiene Bid/Ask 4381,14/4381,35; es la unica pareja igual en ese
intervalo del archivo retenido, pero el `tick_time_msc` de la vigilancia
no esta en la proyeccion, asi que esa identificacion es inferida.
La sombra registra el fill al tiempo del tick y lo emite al diario
2,990 s despues: es una hipotesis de ejecucion instantanea en tiempo
de mercado, **no** la ejecucion historica del cliente y broker.
La comparacion de objetos Git confirma que `gold_555_live_candidate.py`,
`strategy_runtime_contract.py` y `strategy_shadow_engine.py` locales
son identicos a los del commit `7e33c116e53e51a9fcddc0d5dc0ff34fbf0bbb18`
registrado en el probe de `3148` (`git hash-object` frente a
`git rev-parse <commit>:<path>`); no se esta atribuyendo esta cascada
a una version local distinta de esas reglas.

El mismo informe semanal muestra que en `3086` y `3096` el primer fill
virtual se emitio 68,469 y 23,783 s despues del tick al que se
atribuye, mientras el fill nativo ocurrio 67,611 y 19,245 s despues
del tick virtual. Sus entradas solo tienen emparejamiento ordinal,
no identidad completa, de modo que son casos prioritarios para estudiar
doble reloj (tick frente a procesamiento) y vincular fuente, **no**
pruebas de que la causa de `3148` se repita en ellos. El control de
ancla no toca salidas ni protege contra una ejecucion retrospectiva
imposible; la comparacion integral sigue bloqueada.

El [control de doble reloj para los primeros fills](../../runtime_data/week_native_ticks_20260923_v1/shadow_first_fill_dual_clock_12_v1.json)
(`SHA256 722B37E8E8BB3CA57FF3227E2CB885335A39F1DA6D4F905DD2DA078B88FEDE12`)
enlaza por identidad, reloj directo y aritmetica el informe de control
sombra v11 con las doce llamadas MT5 vinculadas a deals nativos. De las
40 cestas reales del extracto (17 Dubai, 23 Gold), cinco Dubai y siete
Gold tienen ambos primeros fills comparables. En **12/12**, la sombra
encolo el evento `virtual_fill` despues del fill nativo aunque le asigno
un tick de mercado anterior. En Gold ese registro posterior al deal va
de 130 a 4.538 ms; en Dubai hay cuatro entre 243 y 2.949 ms y un
caso de 165.647.976 ms. Una llamada Dubai conserva una anomalia de
orden de reloj y no se eleva a prueba de latencia exacta. El `ts` del
diario es hora de encolado del evento, no de escritura a disco.

Esto **no invalida** el uso del tick historico para estudiar reglas o
escenarios ideales. Si invalida usar el estado sombra como prueba de
ejecucion simultanea al bot real: el evento virtual fue reconstruido
despues. Para paridad observada, el siguiente control necesita separar
tiempo del tick, disponibilidad/procesamiento de la decision, solicitud,
fill y confirmacion, y alimentar las reglas con los fills observados
sin filtrar esos fills a los contrafactuales independientes. Los otros
28 primeros fills carecen de esta comparacion en el fragmento; ninguna
salida ni serie integral de equity/drawdown queda certificada aqui.

El [contraste Gold de trayectorias sobre todos los ticks retenidos](../../runtime_data/week_native_ticks_20260923_v1/verified_gold_full_tick_path_3signals_v1.json)
(`SHA256 28D3DF2C6D4BFE780B3385339D92C0FEBB7315A44637D8869DB3ED01A809E3DE`)
usa el comparador de riesgo existente, con rejilla comun XAUUSD/EURUSD,
slots vinculados a tickets/deals nativos y estados sombra encadenados.
Antes de comparar, reproduce las tres huellas de muestras del informe
nativo semanal congelado, valida el reloj directo, FX causal y la suma
por pierna de ambos lados. Un cierre virtual por TP usa el precio
liquidado de la posicion; el precio observado en el evento de toque
solo prueba que se alcanzo el TP y no se confunde con el fill.

| Cesta Gold | Marcas comunes | Exposicion distinta | Max. diferencia total | DD nativo modelado | DD sombra modelado | Neto final ambos |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `3148` | 1.403 | 541 | 7,58 EUR | 24,43 EUR | 21,30 EUR | 8,28 EUR |
| `3168` | 4.887 | 449 | 5,50 EUR | 65,65 EUR | 65,95 EUR | 20,09 EUR |
| `3171` | 192 | 186 | 4,12 EUR | 4,22 EUR | 0,73 EUR | 1,75 EUR |

No hay marcas de dinero desconocido en estas tres ventanas; los tres
recorridos difieren aunque coincida el neto final. El informe conserva
tambien flotante, realizado, maximo desfase de volumen, primera
divergencia y hash de cada serie completa. El denominador no se reduce:
son tres cestas Gold con entradas completamente vinculadas de 23 Gold
y 40 nativas de la semana. Las demas no quedan aprobadas ni descartadas.

Es un contraste retrospectivo de **decisiones sombra registradas**
frente a deals reales, con dinero flotante reconstruido desde ticks;
no es un replay independiente desde Telegram ni una medicion continua
de equity nativa de MT5. Los extremos entre ticks, margen/cartera y
fills alternativos siguen fuera. No se modifico la estrategia ni la VM;
51 pruebas focalizadas del auditor y del motor de riesgo pasaron.
