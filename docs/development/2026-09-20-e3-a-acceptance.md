# Aceptacion Local De E3-A

Revision formal del 20/09/2026 sobre la
[segunda reparacion](2026-09-20-e3-a-second-repair-results.md), HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`, rama
`feature/gold-555-live-trial` y cambios locales identificados por su manifiesto.

## Dictamen

Sin nuevos hallazgos materiales en las fronteras revisadas. R1-R2 cerrados y
E3-A aceptado como base offline para implementar E3-B segun el
[plan de integracion](2026-09-20-e3-integration-plan.md).
Esta aceptacion no certifica despliegue, paridad de estrategias ni operacion VM.

Se contrastaron 27/27 hashes de la entrega antes de actualizar documentacion.
El codigo operativo y las pruebas permanentes no se han modificado en esta
revision. Se mantiene la segunda lectura independiente de la entrega previa;
esta revision formal tiene acceso al historial de implementacion y no se
presenta como otra lectura ciega independiente.

## Fronteras Comprobadas

- R1: propiedad publicada antes del bootstrap, parada antes de recoger un
  arranque bloqueado y segunda parada cuando Popen devuelve el hijo despues
  del primer intento de cierre. Se conserva la espera por la creacion del SO
  cuando aun no ha retornado, sin confundirla con un hijo ya accesible.
- Cancelacion durante initialize, close concurrente, cancelacion durante
  creacion y configuracion que excede el limite: proceso recogido, stdin/stdout
  cerrados, cero consultas pendientes y ambos pools con todos sus hilos parados.
- R2: aislamiento desde la construccion del backend para descriptor 1,
  stdout original y `GetStdHandle(STD_OUTPUT_HANDLE)` con `WriteFile` de Windows.
  Dos consultas consecutivas conservan FOUND, datos e identidad de sesion.
- Los controles anteriores de pool general saturado, plazos, respuesta tardia,
  cuenta/terminal, flags y arranque minimo permanecen vinculados a la misma
  identidad. No se reabre su implementacion sin un contraejemplo nuevo.

## Evidencia

`python -m pytest -q runtime_data/e3_a_acceptance_20260920/test_acceptance.py
--import-mode=importlib
--junitxml=runtime_data/e3_a_acceptance_20260920/acceptance.xml`:
seis pruebas aprobadas, cero fallos/errores/omitidas, 1,97 s,
Windows 11 / Python 3.14.2. Backend falso; sin cargar MetaTrader5.

Se reutilizan por identidad sin cambios: 87 pruebas E3-A, 157 focales E1-E3,
6.091 globales y ensayo nativo de 60 s de la segunda reparacion. No se repiten
la suite ni el ensayo solo por cambiar de modelo. Las 647 advertencias del
informe global no prueban por su numero que todas sean preexistentes.
El p99 de 16,51 ms mide pausas del bucle padre, no latencia de ordenes al broker.

## Continuacion Con Sol/Alto

Implementar E3-B sobre esta base aceptada, respetando el alcance ya definido:
unificar lecturas y operaciones bajo un propietario, conectar las operaciones
compuestas y sus consumidores a la persistencia de intencion/reserva, y guardar
resultado y proyeccion de efectos de forma durable antes de confirmar su
aplicacion. Las respuestas ambiguas requieren conciliacion, no reenvio.

Conservar las decisiones vigentes de ambos canales; probar caidas alrededor de
envio/escritura/aplicacion, redelivery, parciales y resultado desconocido. El
gateway prototipo y el lector no deben arrancar dos propietarios en produccion;
la unificacion debe conservar el bootstrap minimo y las garantias aqui aceptadas.
Esta continuacion es implementacion y prueba local; el plan conserva la revision
del conjunto antes de publicar.

Siguen pendientes Python 3.11/MT5 real, capacidad y carga VM, E3-C, recuperacion
operativa E4 y comparacion amplia de ejecucion/drawdown E5. No hay commit, push,
despliegue, reinicio ni nueva comprobacion remota de la VM en esta revision.
