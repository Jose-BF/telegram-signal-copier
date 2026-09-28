# Reparacion Local E3-C F1-F7

Implementacion Sol/alto del 20/09/2026 sobre
`feature/gold-555-live-trial`, con HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`. Se preservaron todos los cambios
locales preexistentes. No hubo commit, push, despliegue, reinicio de VM ni
conexion a una cuenta real.

## Resultado

Los siete hallazgos de la revision E3-C tienen reparacion local y regresion.
La candidata supera la bateria dirigida y la suite global. E3-C sigue abierta:
esta evidencia no sustituye la nueva revision Astra/alto ni los ensayos E4/E5.

## Reparaciones

- F1: los snapshots se guardan por operacion y parametros. Una consulta de
  ticket no puede reemplazar la vista completa de posiciones; los ticks tambien
  quedan separados por simbolo.
- F2: una desconexion transitoria permite revalidar la misma cuenta y terminal.
  Un cambio de cuenta permanece bloqueado. La recuperacion cubre lecturas y
  preparacion de operaciones.
- F3: una espera Gold 555 con resultado ambiguo permanece activa sin reenviar.
  Al aparecer el resultado durable se coteja el ticket abierto, se materializa
  una sola Signal y se encolan SL/TP exactos una sola vez. Un ticket ya cerrado
  no se resucita. Las esperas confirmadas restauradas tambien se cotejan.
- F4: las caducidades naive del runtime se normalizan como UTC explicito en la
  frontera durable; el registro conserva su validacion estricta.
- F5: la reentrega de una pata recupera el payload durable ya preparado. Un
  cambio de cotizacion o calculo no muta una identidad existente ni fabrica una
  revision para eludir un estado ambiguo.
- F6: `copy_ticks_from/range` transporta limites en milisegundos de extremo a
  extremo, con compatibilidad para solicitudes antiguas en segundos.
- F7: los simbolos de lectura y de trading son permisos separados. El simbolo
  de conversion del contrato monetario se selecciona para lecturas, pero solo
  el instrumento principal puede recibir efectos.

## Evidencia

Regresion dirigida final, incluidos los siete controles de revision, timeout
interno real, recuperacion tardia integral, ambos canales y permisos de simbolo:

```text
267 passed, 61 warnings in 21.05 s
```

La regresion transversal adicional de recuperacion, arranque, acciones
pendientes, estado y monitorizacion paso `217 passed`.

Suite global repetida desde cero tras corregir la expectativa antigua que
prohibia el simbolo de conversion:

```text
python -m pytest -q --tb=short --disable-warnings
6210 passed, 650 warnings in 399.42 s
```

Despues se agrego el control integral F3, ejecutado individualmente y dentro de
la bateria dirigida: el `order_send` empieza antes del timeout, solo se envia
una vez, SQLite termina en DONE y el listener aplica ticket, SL y TP una vez.

Inventario estatico:

```text
python tools/audit_mt5_native_access.py
Baseline matches; 0 live modules retain native MT5 access
```

`python -m py_compile` sobre los modulos modificados paso sin errores.
`git diff --check` no encontro errores; solo aviso de conversion LF/CRLF.

## Identidad Del Codigo

SHA-256 al cierre de la reparacion:

```text
mt5_runtime.py                 A5364F3B8B57F7B104472D33507AD4F152A72546CFF34B62F33C05243BF63496
mt5_read_protocol.py           F10F26044D0625EA6F96CD88B70F4EE0F38635931642F93F4EF010E819792651
mt5_worker.py                  EAE9F36AEBD0AC6E2CADCC5C0F5AA41D32856572679A63D14B79DABD61709AF9
mt5_trade_worker.py            410912C1276639B26A5A9BA1665BDBAE6603646EEA18F35C8E846EAE1FA5C6C1
durable_entry_execution.py     A03FBC2EB663CD5E2E730BD4CC367EC7890C05485EFA176650B57DC22A44C93A
main.py                        2DC1F6F031A29151D38C6BD0B4558C76BBA0140697716CD8642B792485C6C2E2
listener.py                    4322269A3982EB891E107F825403A7DDC5A07565C85598DA3C7C2A37D8BFC207
position_lifecycle_monitor.py  DD7C8556CA2FD225FE2BD7516C215B59C020D49E5D4F890ACAFABBC7D3B54BB4
```

## Limites Y Siguiente Puerta

No se declara E3-C aceptada ni operativa en VM. Astra/alto debe revisar la
reparacion, en especial concurrencia de snapshots, recuperacion tras cortes,
reentrega durable, cierre anterior al resync y separacion lectura/trading. Si la
acepta, E4 medira Python 3.11/Windows, bloqueo nativo, carga, disco y procesos
huerfanos en capacidad comparable a VM. E5 sigue siendo la prueba posterior de
paridad realidad/simulacion, incluido drawdown, flotante, MAE/MFE y exposicion.
