# Plan De Ejecucion Y Simulacion Fiable

Estado 19/09/2026: recopilacion del bloque 1 realizada y arquitectura revisada.
La [revision de aislamiento](2026-09-19-mt5-isolation-architecture.md) identifica
correcciones concretas del inventario y retira nuevos umbrales no justificados.
El [inventario provisional](../audits/2026-09-19-runtime-simulation-inventory.md)
conserva la salida original. El paquete local E1-E2 ya tiene
[resultados reproducibles](2026-09-19-e1-e2-offline-results.md): correccion de
evidencia y protocolo durable con dobles, sin integrar aun en produccion.
La [revision posterior](2026-09-19-e1-e2-review.md) reproduce defectos pendientes
y retira el cierre de E1/E2. Tras cuatro ciclos de contraste, la
[quinta reparacion](2026-09-20-e1-e2-fifth-repair-results.md) cierra localmente
los contraejemplos vigentes y supera 6.001 pruebas. La
[quinta revision](2026-09-20-e1-e2-fifth-repair-review.md) confirma F1/F2/F4
y reproduce solo la colision entre tipos de evento de F3. Siguiente bloque:
la [sexta reparacion](2026-09-20-e1-e2-sixth-repair-results.md) completa esa
validacion y la [revision final](2026-09-20-e1-e2-sixth-repair-review.md) acepta
la base offline para avanzar. [Plan E3 preparado](2026-09-20-e3-integration-plan.md):
siguiente entrega E3-A con Sol/alto; ensayos operativos siguen abiertos.
Rama `feature/gold-555-live-trial`, HEAD
`7415941a410d18288fa4e08da76809f70b125efa`, con cambios previos sin publicar.
HEAD no identifica todo el codigo local: cada ensayo debe guardar tambien
hashes de los archivos efectivos. No hubo push, reinicio ni ordenes en esta
auditoria. La VM estaba operativa en la lectura de las 17:29 UTC, pero siguen
abiertos el aislamiento MT5, la prioridad de tarea y la retencion del diario.

## Objetivo Y Limites

Conseguir dos capacidades verificables: que una espera de MT5 no paralice
todo el bot, y conocer para que decisiones/riesgos podemos usar la simulacion.
Despues comparar mejoras sencillas de ambas estrategias con riesgo comparable.
No prometer rentabilidad, latencia cero, coincidencia universal al centimo ni
proteccion absoluta frente a fallos externos. No lanzar otra busqueda masiva,
reescribir todo el sistema, comprar servidor ni cambiar estrategia live ahora.
No reactivar la automatizacion de 30 minutos.

## Lo Que Ya Sabemos

- [Contabilidad semanal](../audits/2026-09-19-weekly-shadow-comparison.md):
  Dubai -104.86 EUR, 17 cestas; Gold -278.12 EUR, 23 cestas. No hay una semana
  completa de sombras certificadas. Tres grandes perdidas Gold no tienen
  observacion sombra completa; reconstruirlas no las convierte en prospectivas.
- [Auditoria de riesgo](../audits/2026-09-11-drawdown-sampling-investigation.md):
  Gold 2774 tiene minimo reconstruido con fills reales -147.50 EUR frente a
  -237.88 EUR hipoteticos. Son minimos de cesta desde origen, no drawdown de
  cuenta. La frecuencia de muestreo solo explica una pequena parte.
- [Explicacion de 2774](../audits/2026-09-12-2774-why-reality-and-simulation-differ.md):
  entradas diferentes y un TP real anterior explican la discrepancia grande.
  Copiar una demora no reproduce necesariamente el precio del fill.
- [Causa general documentada](../audits/2026-09-11-execution-divergence-root-cause.md):
  la version del puente MT5 investigada retenia el GIL durante llamadas
  nativas. Hay evidencia de esperas de decenas de segundos, no solo 3086.
  El ejecutor local sigue llamando directamente a `mt5.order_send` en
  `_send_safe`. Falta contrastar paquete y recorrido efectivos de la VM actual.
- [Colas ya implementadas](../audits/2026-09-12-client-execution-results.md):
  `single_basket_serial_v1` existe. No representa aun toda la contencion
  compartida entre cestas ni todas las acciones reales de proteccion.
- [Incertidumbre ya estudiada](../audits/2026-09-12-execution-uncertainty-results.md):
  533 intentos, 530 llamadas temporizadas; hay sobreestimaciones e
  infraestimaciones de riesgo. No asumir que la simulacion es conservadora.
- [Control MT5 independiente](../audits/2026-09-11-native-mt5-control-results.md):
  reutilizar el tester y sus discrepancias de fills/TP; no confundirlo con
  una reproduccion del cliente Python bloqueado.

3086 y 27-28/08 son regresiones dentro de una matriz, no la muestra de
validacion. El extracto semanal selecciono sombras/senales/cierres, no todas
las acciones MT5: no permite atribuir por si solo el minuto de 3086 a la VM.
El informe de agosto es antiguo (schema 1), no una prueba del motor actual.
Estados recuperados despues de reiniciar se distinguen de observaciones
prospectivas. Septiembre y otras cohortes ya investigadas son retrospectivas.

## Tres Comprobaciones Diferentes

1. Contabilidad y trayectoria observadas: deals reales, parciales, volumen,
   comisiones, swap, divisa y posiciones. Conciliar dinero a precision de
   cuenta. Reconstruir trayectoria con Bid/Ask y FX causales; contrastar contra
   muestras nativas independientes en los mismos instantes. No inventar
   extremos en huecos ni usar cierres reales para certificar contrafactuales.
2. Decisiones: misma politica versionada, estado, mensajes/ediciones disponibles,
   observaciones, orden causal y respuestas del broker deben producir las
   mismas intenciones. Localizar la primera divergencia, no solo el saldo final.
   Reproducir historico con su contrato; una correccion puede cambiar el futuro
   y no debe obligarse a repetir un bug historico.
3. Ejecucion hipotetica: estimar diferencias de fills, rechazos, confirmaciones
   y colas con perfiles empiricos y adversos. Validar fuera de calibracion;
   no ajustar demoras por senal para igualar su resultado. No ensanchar los
   gates existentes: las capacidades no aprobadas siguen bloqueadas.

Definiciones comunes: P(t) = resultado realizado neto + flotante valorado a
precio ejecutable, con costes devengados y FX disponibles en t. Minimo desde
origen = min(0, min P); maximo favorable = max(0, max P); drawdown = maxima
caida desde un maximo previo, incluyendo origen cero. Publicar flotante puro
por separado. Para canal/cuenta sumar exposiciones en el mismo reloj antes de
calcular drawdown, nunca sumar minimos individuales. Identificar frecuencia,
huecos, conversion, convencion de costes y exposicion abierta en cada corte.
No certificar margen/stopout sin contrato de cuenta, apalancamiento y broker.

## Bloque 1: Inventario Cerrado Y Contrato De Pruebas

Responsable recomendado: Sol, esfuerzo alto. Reutilizar `execution_latency.py`,
`tools/analyze_new_logs.py`, conciliadores y artefactos existentes. No tocar
produccion ni volver a descargar diarios enteros. Consultas remotas solo de
lectura, limitadas; una falta de acceso no autoriza reinicios.

Entregables:

- Matriz por canal/dia/senal: politica y version, recepcion/ediciones causales,
  ticks Bid/Ask, FX, deals, acciones, reinicios, sombra original/recuperada y
  cobertura. Inventariar desde finales de julio hasta 18/09 donde existan
  fuentes, sin prometer ese periodo completo. Mantener ausencias y perdidas.
- Lista finita de defectos abiertos frente a correcciones ya implementadas,
  reproduccion minima y modulo responsable. Verificar antes de duplicar trabajo.
- Linea temporal recepcion -> decision -> cola -> envio -> fill -> acuse ->
  proteccion confirmada, con UTC justificado y reloj monotono por sesion.
  Separar red/broker/terminal/cliente cuando los datos permitan; desconocido
  no equivale a cero. Completar 3086 con acciones/terminal antes de atribuir causa.
- Versiones reales de bot, Python, puente y terminal; capacidad, uso de disco,
  memoria, colas y recuperacion. Preservar secretos. Registrar si no se obtienen.
- Congelar cohortes de calibracion/contraste, matriz funcional, presupuestos
  de recursos y limites de error ANTES de evaluar cambios. Reservar datos no
  examinados si existen; si no, declarar validacion retrospectiva y preparar
  seguimiento futuro. No seleccionar exclusivamente dias tranquilos/completos.

Cierre: cada discrepancia tiene evidencia, causa demostrada o dato faltante,
capacidad afectada y prueba asociada. Publicar lista priorizada y contratos
medibles; ninguna fila desconocida se marca aprobada. Falta de datos bloquea
solo la afirmacion afectada, no todos los controles sinteticos o historicos.
El siguiente bloque es reparar las causas reproducidas, no otra auditoria igual.

## Bloque 2: Aislamiento Y Recuperacion

Areas: `executor.py`, `pending_actions.py`, `main.py`, listener, monitores,
estado/journal, `tools/run_bot_watch.py`, `tools/runtime_telemetry.py`.
Reutilizar pruebas de resiliencia y recuperacion ya existentes.

Si se confirma el recorrido nativo bloqueante actual, aislar el acceso MT5
en proceso dedicado; `to_thread` no basta cuando la extension retiene el GIL.
Diseccionar antes los accesos de lectura y escritura para evitar rutas que
salten el aislamiento. Un solo propietario de operaciones mutantes, colas
acotadas, identidad durable de intencion y correlacion con orden/deal.
Timeout o muerte del proceso = resultado desconocido, no permiso para reenviar.
Conciliar con broker antes de reintentar; impedir duplicados incluso si el
fill ocurre y se pierde el acuse. Gestionar protecciones y cierres pendientes
con prioridades explicitas sin inanicion ni reordenacion silenciosa.

Separar vigilancia de salud de llamadas MT5 bloqueadas. Recuperar estado y
exposicion antes de nuevas entradas. Revalidar caducidad/cierre/precio segun
contrato versionado entre patas; un cambio de semantica es cambio operativo
explicito, no una optimizacion invisible. SL/TP nativos pueden actuar mientras
el cliente espera; las protecciones solo locales no pueden hacerlo.

Telemetria no critica con colas y espacio acotados; evidencia esencial de
ordenes durable y fallos visibles. Si no puede mantenerse la trazabilidad,
politica explicita para nuevas entradas y continuidad de proteccion de abiertas;
no parar toda gestion ni continuar silenciosamente como si nada fallara.
No rotar/comprimir diarios hasta probar lectores, cursores, checkpoints,
manifiestos y recuperacion atomica. No borrar originales para liberar espacio.

Pruebas: espera nativa de 5 y 60 s con doble aislado que reproduzca bloqueo,
ambos canales concurrentes, cola llena, disco lleno/lento, desconexion,
reinicio antes/despues de envio/fill/acuse, timeout ambiguo, cierre nativo
durante espera, duplicados y recuperacion de protecciones. Ninguna envia
ordenes reales. Objetivo inicial de laboratorio para vigilancia no MT5:
p99 <= 100 ms y pausa maxima <= 1 s tras calentamiento; validarlo/fijarlo en
bloque 1 para el entorno de prueba, sin venderlo como SLA del broker.
Registrar tambien p50/p95/p99/max por fase, edad de cotizacion, throughput,
memoria y disco. Prueba sostenida propuesta: 60 min al doble del pico registrado,
con recursos acotados y sin crecimiento continuo de cola. Separar esta carga
de las pruebas de fallo; no someter produccion a ellas.

Cierre: regresiones fallan antes y pasan despues, ninguna orden duplicada o
perdida sin estado explicito, supervisor sigue respondiendo con MT5 bloqueado,
y suite completa aprobada para cambios compartidos. Medir latencia externa
restante; decidir sobre VPS solo si hay evidencia de mejora posible.

## Bloque 3: Paridad De Decisiones Y Riesgo

Areas: `research/dubai_iterative/client.py`, contratos de cliente/ejecucion,
proteccion, motores/oraculo/portfolio; `research/gold_iterative/live_parity.py`,
`pipeline_truth.py`, herramientas de comparacion y pruebas forward existentes.

Compartir reglas puras donde elimine divergencias reales; no reescribir todo.
Mantener un oraculo independiente y casos con respuesta conocida: compartir
el mismo bug no demuestra correccion. Extender colas existentes con recurso
compartido y secuencia efectiva de modificaciones/confirmaciones; no asumir
dos envios por SL/TP si el cliente los fusiona. Capacidades no soportadas por
un motor deben rechazarse explicitamente, no simular otra cosa en silencio.

Separar tres modos: reconstruccion condicionada a fills observados, replay
del flujo realmente observado por el cliente, y contrafactual sobre cinta
completa. El cliente live no procesa hacia atras ticks antiguos para abrir
ordenes hoy. No duplicar slippage sumandolo a un movimiento ya incluido en
el precio posterior. Mantener dependencia entre demora y desviacion del fill.

Matriz minima: BUY/SELL, ambos canales, una/varias patas, concurrencia,
rangos/gaps/spread alto, TP/SL/BE/parciales, mensajes editados o duplicados,
caducidad, respuesta tardia, rechazo, desconexion/reinicio, FX/ticks ausentes,
perdidas grandes y episodios sin entrada. Cubrir fronteras sinteticas ademas
de TODOS los pares historicos admisibles; explicar exclusiones por capacidad.
Reportar discrepancias por senal y estrato, no ocultarlas en promedios.

Cierre: contabilidad conciliada; decisiones iguales con entradas equivalentes;
trayectoria condicionada comparable en rejilla comun y contra anclas nativas;
errores hipoteticos dentro de limites congelados de importe/riesgo/decision,
sin divergencias materiales sin explicar. Los intervalos desconocidos no pasan.
No calibrar y validar en la misma cesta/dia. Fijar suficiencia por cobertura y
precision, no por millones de ticks correlacionados ni por un dia perfecto.

## Bloque 4: Revision Y Vuelta A Estrategias

Cambiar a Astra/alto para revision de cambios compartidos, evidencia y gates.
Una revision de la misma conversacion no se presentara como independiente.
Publicacion requiere autorizacion actual, revision de exposicion, recuperacion
y rollback sin duplicados; el push puede disparar el watcher. No desplegar como
efecto lateral de pruebas. Verificar version/configuracion/proceso realmente
activos despues, no solo que existe un commit o el proceso esta arrancado.

El seguimiento prospectivo completa el trabajo historico, no lo sustituye.
Cubrir varias sesiones y senales de ambos canales; prolongar solo las capacidades
sin evidencia suficiente. Ninguna candidata pasa a real automaticamente.
Reducir los retrasos cambia fills y resultados: recalibrar sobre el runtime
corregido, no conservar ventajas accidentales de bloqueos antiguos.

Con las capacidades necesarias aprobadas, volver a entradas, rango, numero
de patas y salidas sencillas con riesgo comparable. Comparar beneficio neto,
drawdown, peor cesta/dia, exposicion y concentracion en pocos aciertos.
Trabajar canales por separado como solicito el usuario; la contencion tecnica
entre canales sigue dentro de las pruebas. Capital compartido y limites de
cuenta requieren decisiones posteriores del usuario, no supuestos inventados.

## Modelos, Coste Y Continuidad

Recomendacion de trabajo, no promesa de ahorro porcentual: Astra/alto para
diseno y revision de cambios de ejecucion; Sol/alto para bloques delimitados
de implementacion y pruebas. Terra/medio solo para tareas mecanicas aisladas
si cambiar compensa el coste de contexto. No hace falta cambiar en cada paso.
La disponibilidad/nombres se contrastan con las herramientas de esta sesion;
no se extrapolan tarifas API a la cuota semanal. La [guia oficial](https://developers.openai.com/api/docs/guides/latest-model)
es referencia general de razonamiento, no un benchmark de este proyecto.

Avisar ANTES del cambio y esperar al usuario. Arquitectura revisada con Astra;
siguiente cambio: Sol/alto para E1-E2 del documento de aislamiento. Volver a
Astra al revisar ese protocolo y la integracion compartida. Mantener entregables
y pendientes aqui.
Los ensayos los ejecutan programas locales, no consultas de IA por estrategia.
Cada corrida guarda inputs, hashes, presupuesto, checkpoint y motivo de cierre;
no repetir corpus intactos sin una hipotesis nueva. Informes resumidos en
conversacion y resultados detallados en archivos. Sin tareas periodicas nuevas.

## Seguimiento

- [x] Auditoria previa y plan contrastados con evidencia existente.
- [x] Bloque 1: recopilacion de fuentes y observacion VM de las 17:29 UTC.
- [x] Bloque 1/E1 offline: inventario v6 verificado; artefactos anteriores conservados.
- [x] Revision de arquitectura: protocolo UNKNOWN, proceso aislado y mapa de integracion.
- [x] Bloque 2/E2 offline: revision cerrada; prototipo aceptado para integracion.
- [x] Revision E1/E2: once defectos reproducidos; aislamiento nativo local de 5 s medido.
- [ ] Validacion operativa E2: Python 3.11/Windows equivalente y carga; nativo
  60 s ya medido localmente en 3.14, sin certificar la VM.
- [x] Diseno E3: mapa inicial, contratos y entregas E3-A/B/C preparados.
- [x] E3-A local: [cliente/consultas aisladas](2026-09-20-e3-a-implementation.md),
  6.080 pruebas y ensayo nativo 60 s. Pendiente revision con Astra/alto.
- [ ] Bloque 2/E3-E4: integrar accesos live, conciliacion, supervisor y capacidad.
- [ ] Bloque 3: validacion amplia de decisiones, ejecucion y riesgo.
- [ ] Bloque 4: revision, despliegue autorizado y verificacion prospectiva.

Verificacion de este bloque documental: enlaces locales y estructura; no se
ha ejecutado nuevamente la suite de trading ni certificado el estado remoto.
