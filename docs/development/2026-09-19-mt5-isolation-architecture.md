# Revision De Arquitectura MT5

Estado: diseno revisado el 19/09/2026; implementacion y activacion pendientes.
Revision dentro de la misma conversacion, no auditoria independiente.
Base: HEAD `7415941a410d18288fa4e08da76809f70b125efa`, checkout local con
investigacion no publicada. En esta revision no se consulta ni modifica la VM.

## Decision

Mantener Telegram, decisiones y vigilancia en el proceso principal. Trasladar
las llamadas nativas MT5 a un trabajador separado, con un unico propietario
de escritura. Incorporar un registro durable de intenciones y resultados.
Los consumidores esperan de forma asincrona: un proxy que haga una espera
sincrona desde el bucle principal seguiria bloqueandolo aun con MT5 separado.

Esto evita que una espera nativa paralice recepcion y supervision. No acelera
por si mismo una respuesta del broker ni permite enviar un cierre mientras
el unico trabajador sigue dentro de una llamada bloqueada. La salud de control
y la disponibilidad para ejecutar se publican por separado. Durante esa espera
siguen vigentes las protecciones ya aceptadas por el broker; las locales pueden
quedar pendientes. Esta limitacion se mide y se comunica expresamente.

## Hallazgos Que Cambian La Implementacion

| ID | Evidencia actual | Consecuencia |
| --- | --- | --- |
| A1 | `executor._send_safe` llama al puente nativo; tambien hay lecturas directas en varios modulos | Aislar solo `order_send` deja rutas capaces de bloquear |
| A2 | `executor.close_position_rc` usa `if not pos`; una consulta `None` devuelve 10013. `mt5_errors.classify(10013)` devuelve `POSITION_GONE` y `PendingQueue._try_once` lo da por `DONE` | Una consulta fallida puede retirar un cierre pendiente. Reproducido con doble local, sin MT5 |
| A3 | 10011 se interpreta como posicion cerrada; 10008 como transitorio; `None` posterior al envio se convierte en mercado cerrado | Resultados desconocidos, aceptados o parciales necesitan conciliacion, no un reintento generico |
| A4 | `EntryExecutionGate` es local al proceso; el listener libera la reserva ante excepcion o resultado `None` | El nuevo timeout no puede reutilizar esa rama. La reserva debe sobrevivir al reinicio |
| A5 | `_spool_payload` guarda accion/revision pero no el estado durable del intento enviado; el journal confirma escritura sin `fsync` | Ni spool ni flush actual demuestran entrega unica tras perdida de proceso/maquina |
| A6 | `_runtime_exposure_snapshot` consulta posiciones dentro de la escritura de heartbeat | Separar vida del bucle, progreso MT5 y frescura de exposicion |
| A7 | `market_context` consulta barras antes de abrir; el calculo de stop monetario hace muchas llamadas `order_calc_profit` | Telemetria auxiliar fuera del camino de apertura; calculos compuestos dentro del trabajador para evitar cientos de viajes IPC |
| A8 | `safe_sl` puede devolver `None` si no hay cotizacion, y la solicitud omite SL | Si la politica exige ese SL, fallo de preflight no permite abrir sin el; respetar por separado politicas con instalacion diferida explicitamente prevista |

Segun la [referencia de MetaQuotes](https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes),
10008 significa orden colocada, 10010 ejecucion parcial, 10011 error de
procesamiento y 10013 solicitud invalida. No significan todos rechazo o cierre.
La [interfaz order_send](https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py)
no documenta una clave de idempotencia de cliente que garantice ejecucion unica.
No se promete exactly-once del broker: se impide el reenvio automatico de una
intencion ambigua y se conserva el bloqueo hasta resolverla.

## Componentes Y Contratos

1. `mt5_protocol.py`: tipos puros de peticion/respuesta, identidad y estados.
   Sin configuracion, MT5, journal ni efectos de arranque. Version de protocolo,
   `request_id`, `intent_id`, `attempt_id`, `action_id`, revision, canal, raiz de
   senal, generacion de entrada, pata, huella de cuenta/servidor y politica.
2. `execution_intents.py`: SQLite de biblioteca estandar en disco local bajo
   `runtime_paths`, fuera de carpetas sincronizadas en produccion. Transacciones
   cortas, unicidad, `synchronous=FULL`, prueba de recuperacion y espacio. La
   durabilidad real depende tambien del almacenamiento; no prometer resistencia
   a hardware defectuoso. I/O fuera del bucle principal con esperas acotadas.
3. `mt5_worker.py`: proceso iniciado de forma explicita y oculta, compatible
   con Windows/Python 3.11. Unico importador operativo de MetaTrader5. Valida
   cuenta, simbolo, propiedad/magic, generacion y revision antes del envio.
4. `mt5_gateway.py`: transporte asincrono, limites de cola y payload, correlacion
   de respuestas, plazos y entrega tardia. Canal local heredado del padre,
   protocolo con operaciones permitidas; sin evaluacion de codigo ni red abierta.
5. Adaptar `executor`, listener, pendientes y monitores. Conservar calculos y
   politicas existentes; extraer ayudantes puros cuando el trabajador los necesite.
   No ejecutar el modulo entero `executor` en el hijo si arranca journal/estado.

SQLite registra PREPARED desde la admision y el hijo registra DISPATCHING antes
de llamar a MT5. El hijo conserva el resultado antes de responder al padre.
Padre e hijo pueden hacer transacciones serializadas cortas; ninguna transaccion
ni cerrojo de la base queda abierto durante una llamada MT5. Contencion de disco
produce rechazo de admision o degradacion explicita, nunca espera del bucle.

Una intencion identifica la decision logica; un intento identifica una llamada.
Identidad estable: cuenta + canal + raiz/generacion + pata + operacion + revision.
Un retry conocido mantiene intencion y crea intento. Una redelivery no crea
otra intencion. No derivar esta clave de un UUID nuevo en cada llamada.

Las respuestas usan datos propios serializables, distinguiendo `None` de lista
vacia y conservando codigos, volumen ejecutado, precio, orden/deal, error y relojes.
No depender de objetos nativos picklables. No representar un fill desconocido
como fill al precio solicitado. Lotes y filtros historicos se acotan por tamano.

Registrar recepcion, decision, admision durable, despacho, inicio/retorno nativo
y aplicacion de respuesta por intencion/intento. Cada marca conserva proceso,
sesion, reloj monotono y UTC. Calcular duraciones dentro del mismo dominio de
reloj; no restar monotonic de procesos/sesiones distintos sin contrato probado.
Los tiempos nativos de fill conservan su conversion de calendario justificada.
De esta forma se distingue demora del cliente de espera total MT5; esta ultima
no se etiqueta automaticamente como latencia pura de red o broker.

## Estados De Una Intencion

| Estado/evidencia | Actuacion |
| --- | --- |
| PREPARED durable, sin DISPATCHING | Puede caducar/cancelarse antes de enviar; envio tras validar de nuevo |
| DISPATCHING durable | La llamada puede haber alcanzado el broker; ya no se presupone cancelable |
| Resultado de rechazo comprobado | Reintento solo si la politica y el error concreto lo permiten |
| PLACED | Aceptacion pendiente; seguir orden/deals, sin reenviar |
| DONE_PARTIAL | Conservar volumen ejecutado; nada de completar automaticamente el volumen original |
| DONE | Conservar respuesta; aplicar estado de forma idempotente y comprobar efectos que requieran confirmacion |
| Sin respuesta, excepcion tras DISPATCHING o muerte | UNKNOWN; retener reserva y conciliar |
| Resultado durable con padre caido | Reentregar mismo resultado y aplicarlo una vez; no nueva orden |

Conciliacion: cuenta/servidor, tickets nativos si existen, ordenes abiertas,
historial de ordenes y deals, posiciones, volumen y comentario/magic como ayuda.
Una posicion ya cerrada puede haber tenido un fill: comprobar historial. Una
consulta vacia, un plazo transcurrido o varias coincidencias similares no prueban
ausencia de ejecucion. Con identificacion insuficiente se mantiene UNKNOWN y
se avisa. Comentarios limitados/truncados por broker no son garantia de unicidad.

SL/TP, cierre y cancelacion tienen efectos distintos. Verificar nivel/revision
efectivos, volumen restante y orden pendiente respectivamente. Posicion ausente
en una consulta valida permite marcar gestion ya innecesaria, separada de
proteccion instalada. Magic ajeno es rechazo de propiedad, nunca cierre correcto.
La nueva clasificacion requiere actualizar pruebas antiguas que hoy consagran
10011/10013 como `POSITION_GONE`.

## Supervision, Concurrencia Y Prioridades

- Un bloqueo nativo no se resuelve lanzando otro escritor. Cerrojo de SO por
  cuenta/terminal; epoch de trabajador y correlacion de respuesta. Confirmar
  muerte del anterior antes de reemplazarlo, aun con padre reiniciado.
- Una expiracion del await no cancela el broker. Conservar respuesta tardia;
  si se sustituye el proceso, los intentos DISPATCHING pasan a UNKNOWN.
- Recuperacion del trabajador: comprobar cuenta y estado; conciliar intenciones
  pendientes antes de nuevas entradas. Si exposicion de cuenta es desconocida,
  bloquear nuevas entradas de ambos canales; seguir gestion identificada cuando
  el broker vuelva a estar disponible. No inventar capital compartido.
- Heartbeat de control sin llamada MT5. Exposicion incluye origen, instante y
  edad; una cache vieja pasa a `unknown`, nunca `flat`. El watcher conserva sus
  guardas de exposicion y distingue bot muerto de broker ocupado.
- Orden por posicion/revision y dependencias. Cierres/protecciones elegibles
  tienen prioridad frente a entradas; lecturas esenciales no deben quedar
  indefinidamente pospuestas. No se interrumpe una llamada nativa ya iniciada.
  La prioridad entre cestas cambia la ejecucion: versionarla y reproducirla.
- Cola de operaciones limitada, coalescencia solo de lecturas repetibles y
  revisiones aun no enviadas. Reservar capacidad de gestion de posiciones;
  entradas rechazadas por saturacion quedan visibles, sin acumularlas caducadas.
- Contexto de barras, captura historica y sombras no compiten sin limites con
  ordenes. Inicialmente extraer capturas grandes del trabajador operativo;
  no introducir dos sesiones de escritura ni consultas ilimitadas paralelas.
- Cubrir tambien `symbol_info`, `symbol_select`, `order_calc_profit`,
  `copy_rates_*`, `last_error`, initialize/login/shutdown. El mapa anterior de
  nueve modulos era un punto de partida, no una lista exhaustiva de APIs.

## Inventario: Correcciones Obligatorias

La salida schema 1 es un indice diagnostico. Cuatro controles sinteticos en
esta revision reprodujeron: herencia de contrato entre sesiones; envio a
10:01:00 cuando el campo de inicio indica 10:00:01; inclusion de fecha nativa
18/09 en ventana que termina 17/09, etiquetada UTC; perdida de evento anterior
al primer evento raiz. Ademas `sl_updated` se emite al interpretar el SL del
proveedor (`listener.py`), sin confirmar instalacion en broker.

Antes de usar la matriz para admision o latencias:

- Unir por sesion/contrato y accion/intento; conservar cada politica tras reinicio.
  Primeros eventos de una cesta no constituyen una secuencia de una sola orden.
- Usar `broker_request_started_*` y `broker_response_received_*` cuando estan
  presentes y validos. Un intento sin envio no cuenta como envio. Reutilizar
  las comprobaciones de `execution_latency.py`, sin equiparar legado a completo.
- Conservar UTC de recepcion, publicacion y calendario nativo en campos distintos.
  Fuentes sin conversion no entran en un dia UTC inferido. Ventana y exclusiones
  tambien aplican a fuentes auxiliares; desconocidos en lista separada.
- Hacer dos pasadas acotadas o unir por identidad para conservar eventos previos
  a la raiz. Duplicados por `event_id` y conflictos de contenido explicitos.
- Alta sombra solo demuestra inicio, no continuidad prospectiva. Mostrar
  recuperaciones, huecos y bloqueo de la candidata por separado.
- Estado de cache por dia solo es una afirmacion del manifiesto aportado;
  revalidar hashes, simbolo, reloj e intervalos completos de vida de cada cesta
  antes de aprobar una trayectoria, incluidas las que cruzan medianoche.
- Congelar hash de bytes efectivamente leidos y hashes del generador/codigo;
  no certificar una fuente que cambie entre lectura y calculo de su hash.

No se modifica el JSON schema 1 para simular una correccion ya ejecutada. Nueva
salida versionada cuando las regresiones anteriores pasen; preservar el original.

## Criterios Revisados Y Entregas

Conservar contabilidad exacta de los mismos deals a precision de cuenta,
intenciones categoricas iguales bajo inputs equivalentes y regresiones de
exposicion/MAE/MFE/drawdown. Medir discrepancias de ejecucion con fuentes
independientes y los contratos de error existentes del motor.

Se retiran como gates nuevos los numeros arbitrarios del inventario: cobertura
90% del intervalo de fills, sesgo de un punto y tolerancia fija de 0.01 EUR por
posicion en trayectoria. Un intervalo arbitrariamente ancho puede aprobar;
la tolerancia de valoracion depende de cuantizacion, FX y muestreo. Definirla
por evidencia independiente y materialidad antes del contraste, conservar los
gates previos y no ajustarla para encajar resultados. Hasta entonces no hay
certificacion de contrafactuales ni busqueda masiva autorizada por este informe.

60 senales/canal y cinco sesiones pueden ser un primer corte operativo; no
justifican tasa de fallo <5% porque las senales pueden estar correlacionadas.
No afirmar que no queda historico sin examinar sin un registro de acceso.
Los bloques usados ya son retrospectivos. Reservar futuro sin seleccion por P/L.

| Entrega | Trabajo acotado | Cierre verificable |
| --- | --- | --- |
| E1 | Corregir inventario y clasificacion de evidencia | Regresiones anteriores; nuevos resultados versionados; ninguna falsa aprobacion |
| E2 | Protocolo, intenciones y transporte con broker doble | Caidas antes/despues de persistir/enviar/recibir; redelivery; parciales; PLACED; lookup fallido; cero reenvios ambiguos |
| E3 | Integrar todas las llamadas live y adaptar pendientes/listener | Prueba AST/import de accesos nativos, consumidores asincronos, suite completa y contratos de politica conservados |
| E4 | Recuperacion/watchdog, prioridad 4 explicita, telemetria acotada | Proceso huerfano, doble arranque, disco lento/lleno, snapshots viejos y bloqueo nativo |
| E5 | Contraste de decisiones/riesgo con runtime versionado | Matriz por canales/casuisticas, primera divergencia localizada, sin exigir centimos a fills hipoteticos |

E2 prueba primero 5 s de bloqueo nativo que retenga GIL, control negativo en
proceso y trabajador separado; despues 60 s y carga con concurrencia. Medir
pausa del bucle principal y supervisor, no solo del proceso externo. Objetivo
de laboratorio p99 <=100 ms y maximo <=1 s queda como objetivo de aceptacion,
pendiente de medir en Python 3.11 y capacidad equivalente a VM. Prueba sostenida
de 60 min al doble del pico definido y medido; registrar tasas, memoria/colas y
plazos de gestion ademas de heartbeat. Un `sleep` que libere GIL no reproduce A1.

Rotacion de diario va despues de probar lectores/cursor/manifiesto, no como
dependencia de la primera prueba de aislamiento. La reparacion de sombras
conserva los huecos; nunca los convierte en observacion continua. E4 puede
dividirse en cambios independientes para no retrasar una reparacion ya lista.

## Publicacion Y Handoff

Implementacion recomendada: Sol/alto en entregas E1-E4 delimitadas. Volver a
Astra/alto al revisar E2/E3 y antes de publicar frontera compartida. No cambiar
modelo para cada prueba. E1 + E2 offline tienen un prototipo y
[resultados iniciales](2026-09-19-e1-e2-offline-results.md). La
[revision posterior](2026-09-19-e1-e2-review.md) reproduce once defectos y retira
su cierre, manteniendo la prueba positiva de aislamiento nativo de 5 s.
Las [reparaciones locales](2026-09-19-e1-e2-repair-results.md) generaron schema 3
y 5.949 pruebas superadas. Su [revision](2026-09-19-e1-e2-repair-review.md)
reproduce rutas de fallo posteriores. La
[segunda ronda](2026-09-19-e1-e2-second-repair-results.md) las cierra localmente,
genera schema 4 y supera 5.968 pruebas. La
[revision de esa ronda](2026-09-19-e1-e2-second-repair-review.md) identifica
SR1-SR4, SI1-SI4 y SL1. La
[tercera ronda](2026-09-19-e1-e2-third-repair-results.md) incorpora esas
regresiones; su [revision](2026-09-19-e1-e2-third-repair-review.md) reproduce
transiciones adicionales. La
[cuarta ronda](2026-09-20-e1-e2-fourth-repair-results.md) corrige localmente
esa matriz de recuperacion, entrega/aplicacion, plazo y compatibilidad, genera
inventario schema 6 y supera 5.993 pruebas. Su
[revision independiente](2026-09-20-e1-e2-fourth-repair-review.md) confirma
el inventario y las regresiones originales, pero mantiene pendientes F1-F4:
acuse versionado, error tardio, coherencia de identidad y numeros malformados.
La [quinta ronda](2026-09-20-e1-e2-fifth-repair-results.md) cierra localmente
esos cuatro contraejemplos y supera 6.001 pruebas. Su
[revision](2026-09-20-e1-e2-fifth-repair-review.md) confirma F1/F2/F4 y deja
solo F3 pendiente por colisiones entre tipos de evento. Conservar los contratos
aprobados. La [sexta reparacion](2026-09-20-e1-e2-sixth-repair-results.md)
completa esa validacion y supera 6.004 pruebas. La
[revision final](2026-09-20-e1-e2-sixth-repair-review.md) acepta la base offline
para avanzar al [plan E3](2026-09-20-e3-integration-plan.md). Ensayos operativos
de E2/E4 e integracion siguen pendientes; el trabajador no esta activado en
produccion.

Antes de publicacion: revision del diff real, suite completa en cambios
compartidos, compatibilidad Python 3.11/Windows, version de protocolo/esquema,
rollback que conserve el registro de intenciones y no reabra UNKNOWN. Nunca
volver al envio directo como fallback silencioso si falla el nuevo proceso.
El primer cambio de propietario se activa con exposicion y pendientes conocidos
y conciliados; no inventar identidades de intentos heredados que no las tienen.
Tras autorizacion de publicacion, verificar exposicion, version, un solo
escritor, prioridad y continuidad en la VM. El push puede activar watcher.

## Verificacion De Esta Revision

- Lectura de fronteras reales de executor, listener, pendientes, gate, heartbeat,
  journal, errores y contexto; cotejo de retcodes con referencia de MetaQuotes.
- Cuatro escenarios sinteticos del inventario, con archivos temporales:
  reprodujeron los cuatro fallos descritos; no son pruebas aprobadas del arreglo.
- Ejecucion de funciones extraidas del AST con doble MT5: lookup `None` devuelve
  10013 y se clasifica POSITION_GONE; 10008 TRANSIENT; 10011 POSITION_GONE.
  Sin import/configuracion real de MT5, initialize, red ni ordenes.
- Cambios de este bloque son documentales. Los 39 tests anteriores siguen
  describiendo lo probado entonces; no cubren estos nuevos contraejemplos.
- Verificados cuatro documentos y 44 enlaces locales, sin destinos ausentes.
  No se repite suite de trading por cambios exclusivamente documentales.

Identidad SHA256 de fuentes inspeccionadas (HEAD no basta para checkout sucio):

| Archivo | SHA256 |
| --- | --- |
| `research/runtime_simulation_inventory.py` | `8df7a8ff04e40073e236a8371f284b9319a7505b66f3cd8cba23731c2fb3377b` |
| `executor.py` | `cce005ec1ce3cb2c2ba482a6bf080d84a9446bedbbfcccf2fe00e97cc2a24def` |
| `mt5_errors.py` | `c107872777bdaa334bc816c89f9d57f67a1b1bf58e1d9525b73f63df183098c2` |
| `pending_actions.py` | `b41d0240417f552d183853cad2ddea5939457915b20522f5b838fe217a221638` |
| `entry_execution_gate.py` | `341355fd89c54777258c5bd15cfd7bec2dadbe2342e1b728c2901e26a4c3c898` |
