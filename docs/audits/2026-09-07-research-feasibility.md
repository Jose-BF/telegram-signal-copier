# Investigacion de estrategias: Dubai y Gold

Fecha: 2026-09-07. Corte de la copia directa: 09:05:38 UTC.
Estado: investigacion y controles offline; ninguna nueva estrategia seleccionada.

## Conclusion ejecutiva

La desconfianza en los resultados tiene motivos verificables. No falta solamente
probar mas parametros: faltan cerrar discrepancias entre observacion, simulacion
y ejecucion, y construir una muestra historica cuya informacion sea causal.

La propuesta de combinar mensajes antiguos con ticks es viable como proyecto de
investigacion. He consultado el MT5 del broker: devuelve ticks de enero y de cada
mes sondeado hasta septiembre. Ya existen exports locales con mensajes de enero.
Sin embargo, descargar ambos historiales no basta para obtener un backtest valido.

Tampoco se ha perdido toda la semana anterior. Hay 66 senales distintas recibidas
por el bot y 198 registros de estrategias en sombra. Hay deterioro real de la
evidencia y avisos recuperados tarde, pero tambien material recuperable.

No hay base para prometer dos estrategias rentables. El resultado correcto de
esta investigacion es un diagnostico comprobable y una ruta de experimentos que
puede concluir que no existe ventaja suficiente, no una obligacion de encontrar
una curva positiva.

## 1. Trabajo realizado

- Auditoria del codigo de importacion, catalogo causal y motores de ambos canales.
- Revision independiente de fuentes historicas y de resultados/paridad.
- Descarga de telemetria publicada, seguida de copia directa de los registros
  actuales de la VM al detectar que la publicacion estaba retrasada.
- Verificacion SHA-256 de la copia directa de eventos: 235.150 registros, sin
  lineas JSON invalidas; ultimo evento de la copia a las 09:04:05 UTC del dia 7.
- Nueve consultas de disponibilidad de ticks XAUUSD al terminal ya abierto.
- Inventario de enero usando los parsers actuales y fechas Unix en UTC.
- Comparacion de avisos iniciales y revisiones realmente capturadas de Gold.
- Nueva ejecucion del control de sombras del 27-28 de agosto: 18 senales.
- Verificacion de integridad del archivo de investigacion Gold `e4413c34856a5a4a6ed1`.
- Tests focalizados del catalogo y especificacion del proveedor: 134 pasaron.

Los artefactos originales, registros de incidentes previos y cambios pendientes
del usuario se conservaron. Las salidas nuevas estan en una carpeta separada.

## 2. Que queda de la semana 31/08-04/09

Estas son senales distintas con `signal_received`, no todas las publicaciones
que pudo emitir el proveedor ni todas las operaciones ejecutadas:

| Dia UTC | Dubai | Gold | Registros de estrategias en sombra |
| --- | ---: | ---: | ---: |
| 31/08 | 0 | 6 | 18 |
| 01/09 | 0 | 10 | 30 |
| 02/09 | 4 | 11 | 45 |
| 03/09 | 5 | 9 | 42 |
| 04/09 | 11 | 10 | 63 |
| Total | 20 | 46 | 198 |

Los 198 registros corresponden a tres estrategias por senal: control y dos
candidatas. Hay 67 eventos `signal_received`, pero 66 identidades distintas:
`canal2_2294` aparece dos veces. Contar eventos sin deduplicar inflaria la muestra.

### Perdida parcial comprobada

Los ceros de Dubai del lunes y martes NO significan que el canal no emitiera
senales. Se conservan seis pares de sticker/texto, tres de cada dia, cuya primera
captura aparece como `startup_catchup_new` el 01/09 alrededor de las 15:45 UTC:

- Lunes: stickers 21923, 21938 y 21946; textos 21924, 21939 y 21947.
- Martes: stickers 21959, 21968 y 21977; textos 21960, 21969 y 21978.

Sus marcas de edicion ya estaban presentes al recuperarlos. Son evidencia
historica tardia, no seis senales prospectivas que podamos agregar a las 20.
Las versiones iniciales no capturadas no reaparecen por reconstruir un informe.

### Deterioro de las sombras

En el ultimo estado guardado de esas 198 combinaciones:

- 63 figuran incompletas con `money_contract_missing`: 21 de Dubai y 42 de Gold.
- 135 figuran completas en el estado interno, pero ese indicador no certifica
  por si solo continuidad de ticks, version de codigo ni paridad con MT5.
- Hubo seis commits de codigo distintos en los eventos de la semana.
- Se registraron 8.384 eventos de espera de la cola del archivo de ticks,
  246 reanudaciones y una espera de continuidad. Son eventos de estado, no
  8.384 huecos independientes ni 8.384 operaciones perdidas.

No sumo los importes de estados incompletos ni presento el resto como beneficio
certificado. La comparacion semanal monetaria completa sigue pendiente de
reconciliar operaciones, costes, versiones y continuidad por senal.

### Publicacion no equivale a captura

La telemetria recien descargada del commit `58912d87fa8b9e570d322f1ebaed974f0e742b14`
termina el 03/09 a las 23:51:45 UTC. La VM tenia eventos posteriores y por eso se
obtuvo una copia directa. Un informe desactualizado puede ocultar datos existentes;
no debe interpretarse automaticamente como ausencia de actividad.

## 3. Por que las simulaciones anteriores no bastan

### Control comun repetido hoy

Reejecute el instrumento existente, sin optimizar, sobre el 27-28/08:

| Canal | Senales | Filas esperadas/presentes | Filas utilizables para comparar |
| --- | ---: | ---: | ---: |
| Dubai | 7 | 21/21 | 0 |
| Gold | 11 | 33/33 | 0 |

Las 54 filas siguen bloqueadas. Hay problemas de version historica no verificada,
registros ausentes/reconstruidos y controles incompatibles. El registro persistente
conserva 78 incidentes abiertos, 38 de ellos bloqueantes para comparar. Esos 78
NO son un conteo de fallos nuevos de la semana pasada.

Un caso material: Dubai `canal1_21754`, del 27/08. El control observado tiene dos
entradas y +8,80 EUR. La reproduccion de reparacion con el motor actual tiene una
entrada y -4,36 EUR. Diferencia: -13,16 EUR y una entrada menos. Cambiar tolerancias
o eliminar esta senal del denominador ocultaria el problema.

El hash del nuevo settlement es
`7446f26716b90bad7862cae5ff2386d24a0bfa6a322b50d2b813c23d18669146`.

### Gold 555: contabilidad exacta no equivale a entradas exactas

El informe estricto anterior del 31/08-02/09 contiene 27 senales. El replay
condicionado a fills reales reproduce +99,24 EUR, pero la variante prospectiva
`deterministic_flat_cancel` arroja +88,87 EUR. Cuatro senales discrepan por rechazo
y reintento del broker, desplazamiento del fill y reentradas antes de finalizar
el ciclo de vida. Los gates estrictos de trigger, fill y lifecycle no pasan.

Estos son resultados del artefacto previo, no una nueva reconciliacion de toda
la cuenta ni un beneficio de la semana completa. Su campo aislado `certified` en
una subetapa no anula `end_to_end_historical_extension_allowed=false`.

### Busqueda anterior: hay una conclusion negativa util

Verifique el archivo Gold `e4413c34856a5a4a6ed1`: su integridad pasa. Contiene 197
senales de investigacion en 26 dias completos entre 27/07 y 01/09. De cinco
candidatos considerados para estabilidad diaria, ninguno supera esa comprobacion.
La seleccion queda nula y el estado es `diagnostic_only`.

Las cifras positivas de su frontera, +323,40 y +450,20 EUR simulados, no cambian
esa conclusion. Pasar un oraculo bajo supuestos comunes o verificar el archivo
no demuestra que los fills hipoteticos sean ejecutables ni que haya ventaja futura.

## 4. Enero: disponibilidad confirmada, causalidad pendiente

### MT5 del broker

Consulte ventanas de cinco minutos, solicitadas a las 12:00 en la interfaz de
fechas, en nueve dias laborables. Resultado de disponibilidad, no certificacion
del reloj historico ni de cobertura diaria:

| Fecha solicitada | Ticks recibidos |
| --- | ---: |
| 07/01/2026 | 1.605 |
| 04/02/2026 | 2.736 |
| 04/03/2026 | 2.555 |
| 08/04/2026 | 2.104 |
| 06/05/2026 | 3.413 |
| 03/06/2026 | 2.382 |
| 08/07/2026 | 3.719 |
| 05/08/2026 | 2.493 |
| 04/09/2026 | 1.984 |

Total: 22.991 ticks devueltos, sin cotizaciones Bid/Ask invertidas o Bid no positivo
en estas muestras. Se guardaron estadisticas de disponibilidad, no un corpus
completo de enero-septiembre. Quedan por descargar/verificar los dias necesarios,
huecos, limites de sesion, EURUSD, conversion, comisiones y costes historicos.

La API oficial permite obtener Bid/Ask y `time_msc` con fechas UTC; eso no es una
garantia de retencion de todos los dias ni sustituye la comprobacion local del
reloj. [Documentacion de MetaTrader 5](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py).

La sonda viva vuelve a mostrar un desplazamiento cercano a +3 horas entre el
epoch bruto del broker y UTC. Vantage documenta GMT+2/GMT+3 segun horario de verano.
No se debe aplicar el desplazamiento de septiembre a enero ni elegir el reloj
que produzca mas beneficio. Hace falta evidencia historica independiente por
regimen, especialmente alrededor de cambios estacionales.
[Horario oficial de Vantage](https://helpcenter.vantagemarkets.co.uk/hc/en-gb/articles/11933202441103-How-do-I-change-the-time-displayed-on-the-trading-platform).

### Telegram ya disponible localmente

El export global de 22/04 contiene, para enero UTC:

| Fuente | Mensajes normales | Con marca de edicion | Textos de entrada reconocidos hoy | Entradas reconocidas editadas |
| --- | ---: | ---: | ---: | ---: |
| Dubai | 441 | 395 | 77 textos de niveles | 72 |
| Gold antiguo | 1.272 | 1.260 | 173 avisos NOW | 172 |

Ademas hay tres eventos de servicio de Dubai y ocho de Gold, excluidos de la
columna de mensajes normales. Los 77 textos de Dubai no equivalen automaticamente
a 77 senales independientes: deben emparejarse causalmente con sus stickers.

Gold no es un unico historial homogeneo. Hay distintas generaciones de chat:
`2614601304`, `3828356530` y el actual `3908582492`, en formato de export Desktop.
Se deben preservar chat, mensaje y version. No concatenarlos usando solamente
`canal2 + message_id`, ni dar por constante el comportamiento del proveedor.

Telegram Desktop ofrece export JSON, y `messages.getHistory` permite consultar
historial accesible a una cuenta autorizada. Ninguna de esas operaciones promete
recuperar todas las versiones previas de cada texto ni mensajes borrados.
[Export oficial](https://telegram.org/blog/export-and-more),
[API de historial](https://core.telegram.org/method/messages.getHistory).

### La edicion no invalida todo, pero obliga a separar evidencia

Los originales locales conservan fecha de publicacion, ultima edicion, texto final
y replies. El objeto de mensaje de Telegram distingue `date` y `edit_date`, no
incluye una secuencia completa de textos anteriores.
[Esquema oficial de mensaje](https://core.telegram.org/constructor/message).

Una marca de edicion NO demuestra que cambiara BUY por SELL. En el Gold actual
hay 152 mensajes con NOW inicial no marcado como editado y revisiones posteriores
capturadas: no observe cambios de direccion en esas revisiones. Esto apoya la
hipotesis de estudiar solo direccion. No demuestra invariancia en enero, en
versiones no capturadas ni en otro chat.

Tambien hay 108 mensajes del Gold actual cuya primera captura ya estaba editada,
dentro de los 261 mensajes con alguna revision NOW encontrados en todo el archivo.
No deben mezclarse con los 152 anteriores para afirmar que se vio su original.

El momento del disparo importa incluso sin cambiar direccion. En `2307` el texto
inicial era `Buy Gold Noe`; el parser formal detecta NOW despues de corregirse a
`Buy Gold Now`. La captura inicial fue 09:05:57,550 y la version reconocible llego
a las 09:05:59,137. Una nueva estrategia tolerante a erratas podria actuar de otra
forma, pero seria una regla nueva que debe declararse y probarse.

### El importador actual no debe alimentar directamente esta prueba

- Canal 2 conserva la fecha original y solo `was_edited`, sin conservar el instante
  de edicion en la fila de senal. Aplana replies sin su identidad/cronologia.
- Dubai toma direccion y niveles de un texto posterior y los asigna al instante
  del sticker; ademas marca `was_edited=False` y descarta stickers incompletos.
- El cargador acepta el esquema global `chats.list`, pero no todos los exports
  individuales de un chat.
- El catalogo causal usa disponibilidad observada. Un mensaje de enero descargado
  en septiembre no puede convertirse sin mas en una observacion de enero.

El adaptador necesario debe preservar ambos relojes, identidad del chat y mensaje,
replies, revisiones disponibles, hashes y motivo de incertidumbre. Debe producir
cohortes separadas: inicial contemporanea, export sin edicion, export editado con
supuesto direccional, y casos bloqueados. No debe inventar versiones intermedias.

La cohorte sin edicion tampoco es automaticamente representativa: editar o borrar
puede estar relacionado con el resultado de la operacion. Excluir esos mensajes
sin mostrar su peso introduce sesgo de seleccion/supervivencia. Deben seguir en
el inventario aunque no entren en una estimacion causal certificada.

Entrar en la ultima edicion tampoco es una solucion automatica: en directo no
sabemos que una edicion sera la ultima. Solo es defendible como otro experimento
con reglas de disponibilidad declaradas, no como reconstruccion del aviso original.

## 5. Investigacion propuesta

### Preguntas antes de optimizar

1. Ventaja del aviso: a igual riesgo y coste, la direccion publicada aporta algo
   frente a controles de horario/direccion predefinidos? Medir horizontes fijos
   y excursion favorable/adversa sin convertir el maximo futuro en beneficio.
2. Ventaja de entrada: mejora entrar al aviso, al retroceso, a confirmacion o a
   ruptura? Separar el efecto de esperar del efecto de cambiar el volumen.
3. Ventaja de gestion: que aportan parciales, break-even, trailing, cierre temporal
   y gestion del proveedor cuando la entrada permanece fija?
4. Ejecutabilidad: sobrevive el resultado a latencia, spread, rechazo/reintento,
   orden de activacion y cierre del ciclo de vida?
5. Cartera: sobreviven ambos canales juntos a senales simultaneas y exposicion
   correlacionada al mismo XAUUSD?

Los controles de direccion/horario son falsaciones, no estrategias a seleccionar.
Si hace falta adaptar el dataset para ejecutarlos, debe hacerse en investigacion,
sin sustituir el motor de ejecucion por una simulacion simplificada.

### Familias con una razon observable

| Familia | Dubai | Gold |
| --- | --- | --- |
| Entrada inmediata simple | Sticker con direccion independiente | Primer aviso formal NOW |
| Entrada retrasada | Comparar esperar texto frente a sticker | Retraso fijo para medir sensibilidad |
| Retroceso | Entrada mas favorable con caducidad | Retroceso simple frente a retroceso + confirmacion |
| Continuacion | Confirmacion favorable con riesgo limitado | Ruptura/confirmacion tras NOW |
| Gestion sencilla | Una salida frente a parciales | Una salida frente a escalera |
| Control de permanencia | Cierre temporal y gestion del proveedor separados | Caducidad, trailing y no reentrada tras quedar plano |

La gramatica existente ya soporta gran parte de estas entradas y gestiones.
Gold admite senales sin ejecucion real; Dubai necesita ampliar su adaptador de
dataset para investigar todo el universo de stickers no ejecutados. Features
de volatilidad, noticias o regimen solo entrarian despues, con datos disponibles
antes del aviso y soporte explicito del motor. No anadir una coleccion de filtros
porque mejoren retrospectivamente la curva.

### Presupuesto y validacion

Propuesta para la primera campana, aun no ejecutada: seis familias, hasta ocho
configuraciones gruesas por familia y canal, mas controles fijos. Maximo inicial
48 candidatos por canal, sin mutaciones ilimitadas. Reducir a como mucho tres
finalistas antes de una unica evaluacion reservada. La sensibilidad posterior
tambien cuenta como investigacion y debe registrarse; no reinicia el contador.

Los valores concretos, capital y riesgo se congelaran en el manifiesto de la
campana al conocer el dataset admisible. No se toman los 500 EUR de otra EA ni
se aumenta el volumen para fabricar mas beneficio. Una primera comparacion debe
aislar la logica con exposicion comun y despues estudiar el dimensionamiento.

- Separacion temporal por dias completos y por generacion de canal. Las senales
  solapadas comparten bloque; ninguna operacion cruza silenciosamente una frontera.
- Enero no sera llamado OOS solo porque sea mas antiguo. Hay que auditar si sus
  resultados ya se utilizaron antes. Lo reutilizado queda como desarrollo.
- Dentro de un periodo nuevo y suficiente: desarrollo primero, validacion despues
  y un tramo final intacto. Si la muestra no permite tres tramos informativos,
  conservar el tramo final y ampliar datos, no declararlo suficiente por convenio.
- Repetir escenarios de latencia/coste del motor, con calibracion a la captura
  observada. No utilizar errores de precio del broker como slippage exacto de
  operaciones alternativas que nunca existieron.
- Evaluar beneficio neto, drawdown flotante y realizado, peor dia, colas, coste,
  participacion, capital inmovilizado y concentracion en pocas senales/dias.
- Incertidumbre con remuestreo por bloques de dias/semanas y sensibilidad al
  bloque; registrar todas las pruebas y corregir la seleccion multiple. Una
  probabilidad calculada con pocas observaciones no convierte el modelo en cierto.
- Una nueva modificacion tras ver validacion consume ese tramo: no sigue siendo
  una validacion intacta. La confirmacion final necesita evidencia posterior a
  congelar estrategia y version ejecutable.

La razon de registrar la amplitud de la busqueda no es burocratica: seleccionar
entre muchas pruebas eleva el riesgo de elegir una ganadora por azar. El trabajo
original sobre Deflated Sharpe Ratio trata precisamente esa inflacion por
seleccion y retornos no normales; no corrige sesgo de mensajes ni errores de fills.
[Bailey y Lopez de Prado, 2014](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).

## 6. Orden de ejecucion y condiciones de parada

| Orden | Entregable | Condicion para continuar |
| --- | --- | --- |
| 1 | Reparacion de controles Dubai/Gold con casos de regresion | Decisiones comparables bajo mismos inputs; contabilidad reconciliada; discrepancias reales modeladas y visibles |
| 2 | Inventario completo semanal y recuperacion de ticks/contratos | Denominadores completos o bloqueados explicitamente; no convertir reconstruccion en registro prospectivo |
| 3 | Adaptador historico y auditoria de solape | Chat/mensaje/version/relojes conservados; ninguna informacion futura en el universo certificado |
| 4 | Corpus de mercado de los dias admisibles | Bid/Ask, UTC historico, conversion y cobertura verificadas; originales retenidos |
| 5 | Campana limitada por hipotesis | Candidatos y validacion congelados antes de ver resultados |
| 6 | Confirmacion posterior y propuesta de adopcion | Misma estrategia en motor y runtime; sin incidencias bloqueantes ni promocion automatica |

La recopilacion historica puede avanzar en paralelo a reparar controles. La
seleccion monetaria no puede saltarse esos controles. Que una ejecucion alternativa
no replique exactamente un fill real no exige fingir igualdad: exige distinguir
una reconstruccion exacta de un modelo de ejecucion probabilistico y calibrado.

Parar una familia si no aporta evidencia nueva, si solo gana en un punto aislado,
si depende de mensajes inciertos o si desaparece al aplicar costes/latencias
defendibles. No reabrir la busqueda hasta obtener mas muestra o una hipotesis nueva.

## 7. Evidencia reproducible

Directorio de resultados:
`runtime_data/research_feasibility_20260907/`.

- `direct_snapshot_manifest.json`: bytes y hashes de los dos streams copiados.
- `trade_events.jsonl`, `telegram_media.jsonl`: copias directas; datos privados,
  excluidos de publicacion y sin credenciales de sesion.
- `evidence_inventory.json`: conteos diarios, identidades, revisiones y enero.
- `mt5_history_probe.json`: las nueve sondas de disponibilidad y reloj vivo.
- `terminal-before.json`, `terminal-after.json`: mismo PID 8968, sesion 2 y arranque.
- `control_20260827_28.json`: control nuevo y bloqueos conservados.
- `control_incident_registry.json`, `control_incidents.jsonl`: copias aisladas del
  registro previo mas la evaluacion nueva, sin sobreescribir el original.
- `research_manifest.json`: hashes de codigo, inputs monetarios, ticks y control.
- `snapshot/`: telemetria publicada, conservada separadamente de la copia directa.

Fuentes locales anteriores relevantes:

- `tools/parse_export.py`, `provider_signal_catalog.py`, `provider_trade_spec.py`.
- `research/dubai_iterative/` y `research/gold_iterative/`.
- `runtime_data/canal2_deep_review_20260902_2353/verify_pipeline_truth_strict.json`.
- `runtime_data/gold_strategy_runs/provider_first_robust_20260902_seed20260902/e4413c34856a5a4a6ed1/run_card.json`.
- `C:/Users/josea/Downloads/Telegram Desktop/DataExport_2026-04-22/result.json`.

Comprobaciones nuevas: control finalizado con comparacion bloqueada;
`python -m research.gold_iterative verify --run-dir` sobre el archivo citado,
VERIFICADO; `python -m pytest -q tests/test_provider_trade_spec.py
tests/test_provider_signal_catalog.py`, 134 passed. No se ejecuto la suite completa
del bot, ya que esta investigacion no cambia su codigo de produccion.

## 8. Estado local y VM

Rama local: `feature/gold-555-live-trial`. HEAD:
`b4426e6cb5649dee10c3ca57eb95866203d0bd98`. Se preservan cambios previos sin publicar.

VM: mismo commit verificado en los eventos nuevos; mismo terminal y procesos de
bot/watcher antes y despues de las consultas. No se enviaron ordenes, no se cambio
la politica activa y no se reinicio el bot. La tarea temporal de lectura de ticks
termino con codigo 0 y fue retirada. Quedan archivos auxiliares de investigacion
en `C:/Users/bot/codex-control/research-20260907`.

No se inicio otro cliente de Telegram, no se copio su sesion fuera de la VM, no
se envio correo y no se hizo commit, push, despliegue ni promocion de candidata.
