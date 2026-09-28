# E3-B: Operaciones Y Aplicacion Durable

Entrega candidata local del 20/09/2026, rama
`feature/gold-555-live-trial`, HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`. Sigue el
[plan E3](2026-09-20-e3-integration-plan.md). No activa E3 en `main.py`, no
conecta MT5 real y no modifica la politica de ninguna estrategia. Revision
independiente pendiente.

## Implementacion

- `execution_intents.py`: intenciones, admisiones, intentos, reintentos
  explicitos, reservas por exposicion, proyecciones de efectos y evidencia de
  conciliacion en SQLite WAL/FULL. El request nativo exacto queda durable antes
  del commit a `order_send`.
- `mt5_trade_protocol.py` y `mt5_trade_worker.py`: protocolo de dos fases para
  apertura market, limit, SL/TP, cierre y cancelacion. Tick, contrato, precio,
  stop monetario, cuenta, simbolo, magic y ticket se preparan/revalidan dentro
  del unico propietario aislado.
- `mt5_client.py`: el mismo proceso de E3-A atiende lecturas y operaciones. La
  persistencia corta usa un executor propio para que una orden en cola pueda
  vencer como `PREPARED` sin bloquear el bucle ni desaparecer. Timeout tras el
  envio conserva `DISPATCHING`/resultado tardio; nunca reenvia por timeout.
- `durable_execution.py` y `durable_entry_execution.py`: frontera tipada de
  aplicacion, proyeccion reconstructible con payload causal completo, estados
  `NOT_SENT`, `RECONCILE`, `APPLIED` y `REJECTED`, y reconstruccion que mantiene
  visibles exposiciones ambiguas o despachos sin resultado.
- `pending_actions.py`: integracion opcional de gestion. `UNKNOWN`, `PLACED` y
  `DONE_PARTIAL` esperan conciliacion. Un rechazo conocido solo entra en el
  reintento durable permitido; ticket ausente se resuelve como exito implicito;
  magic/simbolo ajeno es rechazo permanente. Si TP es legal y SL aun no, aplica
  el TP como efecto independiente y conserva el SL pendiente con nueva revision.
- `listener.py`: las siete rutas de apertura pasan por una frontera comun cuando
  E3 se instala: primeras entradas de ambos canales, legs adicionales, doble
  market y rescate. Una apertura ambigua conserva la reserva, bloquea redelivery
  y no dispara fallback. Sin instalacion explicita, el comportamiento legacy
  permanece intacto.

## Invariantes Comprobadas

- Persistencia y reserva preceden al envio; revision nueva solo sustituye una
  politica anterior que aun no creo exposicion.
- `order_send` se ejecuta una vez como maximo por intento admitido. Muerte del
  worker dentro del envio, respuesta `None`, aceptacion pendiente y fill parcial
  quedan sin reenvio automatico.
- Cuenta cambiada despues de preparar produce resultado incierto sin enviar.
  Proteccion requerida no calculable bloquea la apertura antes del broker.
- Fallo de SQLite despues de una respuesta confirmada retiene el outcome en
  memoria para persistirlo; una proyeccion se aplica una vez por revision.
- La aplicacion puede reconstruir payload, raiz, generacion, leg, revision,
  ticket y precio confirmados, y tambien enumerar estados que exigen conciliar.
- El camino legacy sigue cubierto mientras E3 no se instala. No existe
  activacion automatica al importar `listener` ni un segundo propietario live.

## Verificacion

`python -m pytest` sobre contratos, ledger, gateway, cliente/worker de lectura y
escritura, servicio durable, cola pendiente y rutas de listener: 420 pruebas
aprobadas, cero fallos, 327 advertencias, 29,68 s en Windows / Python 3.14.2.
Incluye backend falso en proceso hijo; no usa MT5 real ni envia ordenes.

Suite global de la candidata previa a revision: `python -m pytest -q`: 6.155
pruebas aprobadas, cero fallos/errores/omitidas, 648 advertencias, 426,97 s. No se declara que todas las
advertencias sean preexistentes solo por su numero. La ejecucion no cargo MT5
real ni envio operaciones.

Se cubren redelivery, reservas concurrentes, revision superada, fallo de disco,
timeout antes y despues del dispatch, proceso muerto dentro de `order_send`,
cuenta distinta, rechazo, `PLACED`, parcial, resultado desconocido, proyeccion,
reconciliacion y apertura ambigua del listener. La revision independiente se
registra en [E3-B review](2026-09-20-e3-b-review.md). Sus tres regresiones
adicionales fallan sobre esta candidata y por tanto E3-B sigue sin aceptarse.

## Limites Y Siguiente Paso

`main.py` aun inicia el runtime legacy; E3-C debe instalar un solo cliente,
recuperar/proyectar el estado al arrancar y migrar monitores, auditor, heartbeat
y lecturas auxiliares. La conciliacion durable existe como contrato, pero su
inspector operativo de MT5 se conectara al arranque en E3-C. No se declara
paridad real/simulada, rendimiento VM, Python 3.11, carga prolongada ni aptitud
para produccion.

Todo permanece local: sin commit, push, despliegue, reinicio, consulta de VM ni
operacion real. Los hallazgos reproducidos se han reparado en la
[candidata F1-F3](2026-09-20-e3-b-repair-results.md), que queda pendiente de una
nueva revision Astra/alto antes de aceptar E3-B.
