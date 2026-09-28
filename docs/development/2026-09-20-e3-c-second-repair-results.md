# Segunda Reparacion Local E3-C R1-R4

Implementacion Sol/alto del 20/09/2026 sobre
`feature/gold-555-live-trial`, con HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`. Se preservaron todos los cambios
locales preexistentes. No hubo commit, push, despliegue, reinicio de VM ni
conexion a una cuenta real.

## Resultado

Las once contrapruebas de la revision anterior pasan. La reparacion cubre la
aplicacion tardia en marcha y despues de reiniciar, la identidad de reentregas,
la retencion acotada de snapshots y la exclusividad de los relojes historicos.
E3-C permanece abierta hasta una nueva revision Astra/alto. E4 y E5 no se han
ejecutado ni quedan sustituidas por esta evidencia local.

## Cambios

- R1, primera entrada Gold: una espera confirmada con resultado durable se
  restaura aunque el resync ya haya creado la Signal. Si el ticket sigue
  abierto, se completan SL/TP exactos sobre esa misma Signal y se registra el
  fill terminal sin abrir otra orden ni arrancar un monitor duplicado.
- R1, patas tardias: RECONCILE queda ligado al indice de pata. Su resultado se
  consulta y aplica aunque el precio rebote, venza la ventana o cambie el
  estado que permite entradas nuevas. Al arrancar, los DONE confirmados de
  ambos canales se cotejan con el ticket abierto, se incorporan una vez y se
  recupera la proteccion exacta Gold o el SL de cesta Dubai. Un cierre ya
  solicitado encola cierre, no una proteccion nueva.
- R2: una reentrega solo puede reutilizar los niveles SL/TP congelados por la
  primera preparacion. Cambiar simbolo, direccion, volumen, presupuesto,
  politica de proteccion, magic, comentario o desviacion con la misma
  identidad/revision produce conflicto explicito.
- R3: el runtime solo conserva snapshots usados por salud/exposicion y limita
  el total a 256. La vista completa de posiciones y las capturas de
  cuenta/terminal no se expulsan por consultas filtradas.
- R4: TICKS_RANGE acepta exactamente el par de segundos o exactamente el par
  de milisegundos. Una mezcla parcial o completa se rechaza antes de llegar al
  trabajador.

## Evidencia

Regresion dirigida, incluida terminacion del proceso productor, nueva instancia
de SQLite, recuperacion Gold y patas tardias de ambos canales:

```text
37 passed, 19 warnings in 5.37 s
```

Bateria global desde cero:

```text
python -m pytest -q --tb=short --disable-warnings
6224 passed, 670 warnings in 380.65 s
```

Inventario estatico:

```text
python tools/audit_mt5_native_access.py
Baseline matches; 0 live modules retain native MT5 access
```

`python -m py_compile` sobre modulos y regresiones modificados paso. `git diff
--check` no encontro errores; solo avisos preexistentes LF/CRLF.

El sondeo que antes retenia aproximadamente 19,15 MiB por 20.000 consultas
PROFIT distintas ahora conserva cero snapshots para 20.000 y 40.000 consultas,
con 0,0 MiB retenidos segun `tracemalloc` en ese caso aislado. La regresion
adicional inserta cuatro veces el limite con tickets distintos, exige como
maximo 256 snapshots y conserva la vista completa de posiciones.

Entorno: Windows, Python 3.14.2, pytest 9.0.3. No es evidencia de Python 3.11
ni de capacidad comparable a la VM.

## Identidad Del Codigo

```text
durable_entry_execution.py     667BC496A7B816F4ACC68C802E72F7094F83070A9475526AF244271EF6EBD233
mt5_read_protocol.py           E1B5BBC12C80C80DFCEAEA451818FFC76C043A61022719C5E68DEC03199062D8
mt5_runtime.py                 C61029C560EB5F6A0C88A54F06B4B66ED8BB05AF5A5CB1EB18364B57E85EBF0F
state.py                       7112DA0CC0244E42990E9EB780AECCD7B8303888F631239C7B9FD97EDB0CC589
position_lifecycle_monitor.py  7DFC563E61BBE0D0F36E5E90FB067BA0275B367A6798F663984A5F5168F14CD2
listener.py                    4C101053710FCD527676B66F877FF66D44D649FE6EF144DFCCE76A7920D67EAC
main.py                        901792DB0D07AE9FF664CD0721D09D755BA778FF406706F0803FC13A24FBCB40
test_e3_c_repair_review.py     B3D58117DAFE3673576A021471EECF9DDFB2284932502FB67CB5A77EE8C54B02
test_e3_c_restart_review.py    9C823AE025FC1C30F21D780C48123FD89B274E23AE88C4542ED1C1AA34703271
```

## Limites Y Siguiente Puerta

La prueba de reinicio usa posiciones simuladas y un subproceso local para
persistir DONE; no arranca Telegram ni MT5 nativo. La recuperacion nueva exige
un resultado durable CONFIRMED con ticket/precio. Un proceso que muera dejando
solo DISPATCHING/UNKNOWN requiere la conciliacion operativa de E4 contra la
evidencia del broker; no se afirma resuelto por estas pruebas.

Siguiente modelo: Astra/alto para revisar la reparacion completa, prestando
especial atencion a idempotencia tras cortes entre resync, spool, proteccion y
journal; validacion de ticket; y coste del escaneo durable al arrancar. Solo si
se acepta pasa a E4 en Python 3.11/Windows comparable a VM. No hay autorizacion
de publicacion ni activacion live.
