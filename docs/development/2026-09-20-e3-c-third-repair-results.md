# Tercera Reparacion Local E3-C

Implementacion Sol/alto del 20/09/2026 sobre
`feature/gold-555-live-trial`, HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`. Se preservaron todos los cambios
locales preexistentes. No hubo commit, push, despliegue, reinicio de VM ni
conexion a una cuenta real.

## Resultado

Se cierran localmente F1-F5 de la revision anterior. Una entrada durable no se
considera completamente aplicada hasta que su cierre o sus protecciones quedan
encolados. Primera entrada y patas tardias conservan ese trabajo ante fallo,
cancelacion y reinicio, sin volver a abrir la orden confirmada. E3-C permanece
abierta hasta una nueva revision Astra/alto; E4 y E5 no quedan sustituidas.

## Cambios

- La reconstruccion de entradas filtra por cuenta y servidor activos antes de
  exponer un DONE. Al aplicar una recuperacion se cotejan, cuando estan
  disponibles, ticket, simbolo, magic, tipo, volumen y generacion.
- Un cierre solicitado prevalece sobre SL/TP tanto durante la primera entrada
  como en patas tardias. La solicitud sobrevive al journal y al reinicio.
- La reconciliacion pendiente solo se retira despues de encolar todos los
  efectos de gestion. Un fallo parcial o CancelledError conserva la obligacion
  y el siguiente tick puede completarla aunque la ventana de entradas venza o
  el precio ya no cruce el nivel.
- La recuperacion conserva el stop mas protector entre nivel inicial, stop
  provisional, stop observado, nivel confirmado en Signal y solicitud aun
  pendiente. Esto cubre BUY/SELL, primera entrada, patas y reinicio.
- Canal 1 completa el recalculo del stop exacto de cesta dentro del mismo ciclo
  reintentable cuando usa la ejecucion durable.
- Los eventos terminales del watch distinguen gestion completa de una
  aplicacion interrumpida: un DONE confirmado no queda oculto por un aborto o
  por un cierre recibido mientras la apertura seguia en curso.

## Evidencia

Las diez contrapruebas inicialmente fallidas pasan. La matriz se amplio con
Canal 1, identidad de posicion, primera entrada con stop mejorado, stop
provisional ante deslizamiento y solicitud pendiente mas protectora:

```text
tests E3 + decision evidence + pending actions
134 passed, 114 warnings in 11.08 s
```

Otros conjuntos dirigidos ejecutados durante la reparacion:

```text
12 passed, 21 warnings in 2.53 s
116 passed, 43 warnings in 5.60 s
109 passed, 99 warnings in 5.33 s
74 passed, 93 warnings in 4.92 s
```

La pasada global final, despues de actualizar las dos expectativas que ahora
exigen el stop mas protector y registran `current_sl`, obtuvo:

```text
6243 passed, 699 warnings in 425.03 s
```

Una pasada anterior habia localizado dos expectativas historicas: una no
incluia `current_sl` en la trazabilidad y otra exigia bajar el stop de 2470.40
a 2470.25. Ambas se actualizaron para el nuevo contrato y quedaron incluidas
en la global verde final.

`python tools/audit_mt5_native_access.py` informa `Baseline matches; 0 live
modules retain native MT5 access`. `python -m py_compile` paso sobre runtime y
regresiones modificados. `git diff --check` no encontro errores; solo avisos
preexistentes LF/CRLF.

Entorno: Windows, Python 3.14.2, pytest 9.0.3. Broker simulado, SQLite temporal
y subproceso productor para los cortes de primera entrada. No es evidencia de
Python 3.11, de MT5 nativo, de capacidad comparable a la VM ni de rentabilidad.

## Identidad Del Codigo

```text
durable_entry_execution.py          44F46C9F325922BA97187554DAE50A0080EF47797EA9C667C608E65F96BAC779
listener.py                         1DAAF2AC2CC93FE668A78AC558C6EA6F786B35824BA6570D1DB4F00B5AB502E3
pending_actions.py                  6E34E82FBF7972363F3E73FEC9BB20A0DEAABE81329A71BE8248077FED3C8039
position_lifecycle_monitor.py       DF5C68575458572B5ADF144625544E8DC2C1A1452228230267ACDFC1E9743D61
test_e3_c_second_review_listener.py 36CBB910F4C4F31E0E7330907CB13B9B819777493737121C3830E66DB0A5FB16
test_e3_c_second_review_monitor.py  25A93733C0788A4F688A43E329B24F1C1BBCD21738577B15EF0FA2E94A6459EB
```

## Limites Y Siguiente Puerta

DISPATCHING/UNKNOWN sigue siendo desconocido hasta conciliacion con evidencia
del broker; no se reenvia para fabricar certeza. La cola acredita persistencia
de una solicitud, no confirmacion del broker. E4 debe comprobar trabajador
nativo, GIL, carga, disco, procesos huerfanos y recuperacion completa en
Python 3.11/Windows comparable a la VM. E5 debe comparar recorrido de riesgo y
resultado real/simulado sobre muestra amplia.

Siguiente modelo: Astra/alto para revisar el bloque completo, incluidos los
cortes entre SL y TP, cierre en carrera, stops monotonicamente protectores,
identidad de cuenta/posicion y Canal 1. Solo si se acepta se cierra E3-C y se
pasa a E4. No hay autorizacion de publicacion ni activacion live.
