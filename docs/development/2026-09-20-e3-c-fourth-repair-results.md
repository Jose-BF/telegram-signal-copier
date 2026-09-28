# Cuarta Reparacion Local E3-C

Implementacion Sol/alto del 20/09/2026 sobre
`feature/gold-555-live-trial`, HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`. Se preservaron todos los cambios
locales preexistentes. No hubo commit, push, despliegue, reinicio de VM ni
conexion a una cuenta real.

## Resultado

Las cuatro contrapruebas de la tercera revision quedan cerradas localmente.
Una pata durable conserva su obligacion de conciliacion desde el instante
anterior al envio; un cierre o cambio de generacion se comprueba de nuevo justo
antes del commit; Canal 1 no sustituye un stop pendiente mas protector; y una
recuperacion solo se aplica si coincide con la fila exacta del plan congelado.
E3-C permanece abierta hasta revision Astra/alto.

## Cambios

- Las patas tardias marcan su indice como conciliable antes de esperar el
  resultado durable. Una cancelacion conserva la identidad y el siguiente tick
  consulta la misma intencion aunque el precio se haya alejado o la ventana
  haya vencido.
- La preparacion vuelve a comprobar cierre y cesta cerrandose despues de sus
  esperas asincronas. El `dispatch_guard` valida en el ultimo instante estado
  abierto, ausencia de cierre, generacion y fila congelada.
- El recalculo del stop comun Dubai usa la proteccion pendiente mas fuerte y
  conserva ese nivel tambien en `candidate_hard_stops` y en el nivel solicitado.
- La recuperacion de patas compara raiz, indice, volumen, direccion, magic,
  simbolo, comentario, politica, revision y generacion con la `Signal` y su
  plan congelado antes de incorporar el ticket o encolar gestion.

## Evidencia

Las cinco contrapruebas de la revision pasan, incluida una comprobacion nueva
del cierre que aparece dentro del `dispatch_guard`:

```text
109 passed, 177 warnings in 6.45 s
```

El conjunto E3-C ampliado paso 152 controles. La bateria global final obtuvo:

```text
6248 passed, 713 warnings in 480.65 s
```

`python tools/audit_mt5_native_access.py` informa `Baseline matches; 0 live
modules retain native MT5 access`. `python -m py_compile` paso sobre runtime y
pruebas modificadas. `git diff --check` no encontro errores; solo avisos de
conversion LF/CRLF.

Entorno: Windows, Python 3.14.2, pytest 9.0.3, broker simulado. No acredita
Python 3.11, MT5 nativo, carga comparable a la VM, paridad real/simulada ni
rentabilidad.

## Siguiente Puerta

Siguiente modelo: Astra/alto para revisar esta reparacion acotada y el bloque
completo E3-C. Si se acepta, E4 debe ejecutar las pruebas operativas de GIL,
latencia, carga, disco, watchdog, proceso huerfano y conciliacion de estados
ambiguos en un Windows/Python 3.11 comparable a la VM. No hay autorizacion de
publicacion ni activacion live.

