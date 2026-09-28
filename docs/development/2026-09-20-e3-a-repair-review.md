# Revision De La Reparacion E3-A

## Estado Posterior

R1-R2 se han corregido en la
[segunda reparacion E3-A](2026-09-20-e3-a-second-repair-results.md): 87 pruebas
de frontera, 157 focales, 6.091 globales y ensayo nativo final de 60 s pasan.
Una segunda lectura independiente no encuentra defectos materiales. Este
documento conserva las reproducciones que motivaron el cambio. La
[revision formal posterior](2026-09-20-e3-a-acceptance.md) acepta E3-A como base
local para implementar E3-B.

## Dictamen

Quedan dos correcciones acotadas antes de aprobar E3-B: cierre durante el
arranque y aislamiento de la salida del backend respecto al protocolo.
Los cuatro contraejemplos originales F1-F4 estan corregidos: el bootstrap no
repite la aplicacion, la parada de una lectura ya iniciada no depende del pool
general, los flags son -1/1/2 y se valida la propia muestra ACCOUNT/TERMINAL.
La nueva prueba encuentra una fase adicional en la que la parada sigue esperando
al trabajo que debe poder interrumpir.

Revision de HEAD base `7415941a410d18288fa4e08da76809f70b125efa`, rama
`feature/gold-555-live-trial`, con los cambios locales de la
[reparacion](2026-09-20-e3-a-repair-results.md). Sus 23 hashes coincidian antes
de actualizar el seguimiento documental. No se modifica codigo del bot ni
pruebas anteriores en esta revision.

Cliente y cierre revisados en esta tarea, que conserva el historial anterior.
Worker/protocolo revisados por un subagente sin historial de implementacion;
su hallazgo se reprodujo tambien aqui. Solo esa segunda lectura es independiente.

## R1: El Cierre Espera Al Arranque Bloqueado [P2]

Localizacion principal: `mt5_client.py:270-277`; frontera asociada:
`mt5_client.py:142-150`.

La via de control dedicada solo se invoca despues de esperar `_start_task` y
`_spawn_future`. Hay dos caminos reproducidos con un hijo real y backend falso:

1. `initialize` retiene el GIL del hijo durante seis segundos, con plazo de
   arranque de veinte segundos. Se pide `close` despues de confirmar que entro
   en initialize. A los 4,5 segundos el hijo sigue vivo y `_stop_worker` no se
   ha invocado. El cierre termina a los 6,212 segundos, cuando vuelve initialize.
   Un arranque que no vuelve retrasa la parada hasta el plazo de arranque.
2. El hijo ya existe, pero se demora cinco segundos antes de leer su entrada.
   Una configuracion JSON valida con password ficticio de 12.000 caracteres
   llena la tuberia de Windows. `_spawn` escribe antes de publicar `_process`;
   `start` devuelve UNKNOWN por su plazo de 100 ms, pero `close` sigue esperando
   a los 3,5 segundos y `worker_pid` es None pese a existir un hijo vivo.
   Si ese hijo no llega a leer, la espera de `_spawn_future` carece de plazo.

La segunda prueba no presupone que un password real tenga esa longitud: usa
una carga admitida por el contrato de 16 KiB para demostrar la contrapresion
del transporte. Tampoco se confunde una creacion de proceso que aun no ha
retornado del SO con un hijo que ya fue creado y puede detenerse.

Impacto: un reinicio/cancelacion durante arranque puede quedar esperando la
misma operacion bloqueada que necesita retirar. El bucle padre sigue disponible;
el defecto es la recuperacion del propietario, no un bloqueo de su GIL.

Correccion requerida: publicar y conservar la propiedad del proceso en cuanto
Popen retorna, antes de cualquier escritura potencialmente bloqueante. La via
de parada debe poder interrumpir un hijo ya creado sin esperar a que finalice
el arranque, su inicializacion o el envio de bootstrap. Conservar la recogida
del proceso si el cierre compite con su creacion y limpiar tuberias/hilos ante
fallo. Reducir el timeout de start no resuelve el segundo camino.

Cierre de R1: regresiones permanentes para initialize bloqueado, hijo creado
que no lee bootstrap, cancelacion de start y close concurrente con creacion;
parada acotada del hijo existente, sin esperar al pool general y sin hijos,
tuberias o hilos pendientes. Preservar plazos independientes, resultados tardios
y las garantias originales de F1-F4. Una sola reparacion del ciclo de arranque.

## R2: La Salida Nativa Puede Contaminar El Protocolo [P2]

Localizacion: `mt5_worker_entry.py:9-13`. Se guarda `sys.stdout.buffer` como
canal JSON y se cambia `sys.stdout` a stderr. Esto redirige `print`, pero el
descriptor 1 y `sys.__stdout__` siguen apuntando a la tuberia del protocolo.

Un backend valido devuelve posiciones correctas tras escribir un diagnostico
con `os.write(1, b"backend diagnostic\n")`. La respuesta pasa a UNKNOWN con
`read_transport_failed` y la sesion deja de admitir consultas. Reproducido
tambien con `sys.__stdout__.write`, mientras el mismo control con `print`
produce FOUND. Son dos vias de un mismo defecto, no dos hallazgos.

Esto acredita una regresion del aislamiento del transporte nuevo; no se afirma
que MT5 real haya emitido ese diagnostico. La extension nativa no esta obligada
a escribir mediante la variable Python `sys.stdout`.

Correccion requerida: usar un canal privado de respuestas separado de stdout,
o aislar tambien los descriptores/handles nativos conservando dicho canal.
Redirigir solo la variable Python no basta. No aceptar ni saltar lineas
arbitrarias como forma de reparar un protocolo contaminado.

Cierre de R2: consultas FOUND con las tres vias de diagnostico (print,
descriptor 1 y stdout original), sin contaminar respuestas ni perder identidad;
conservar limites, limpieza y arranque minimo. Considerar los handles de Windows
al elegir el mecanismo, dado que es el entorno de destino.

## Evidencia

Directorio `runtime_data/e3_a_repair_review_20260920/`.

- `python -m pytest -q tests/test_mt5_read_client.py
  tests/test_mt5_read_worker.py tests/test_mt5_native_access_inventory.py
  --junitxml=runtime_data/e3_a_repair_review_20260920/existing.xml`:
  83 aprobadas, 9,59 s.
- `python -m pytest -q -s
  runtime_data/e3_a_repair_review_20260920/client_review/test_startup_close.py
  --import-mode=importlib
  --junitxml=runtime_data/e3_a_repair_review_20260920/startup_close_confirmed.xml`:
  dos reproducciones, 12,12 s. Afirman el defecto observado, no su correccion.
  El XML `startup_close.xml` conserva el ensayo intermedio con esperas menores;
  el confirmado corresponde al archivo actual y fundamenta este dictamen.
- `python -B -m unittest discover -s
  runtime_data/e3_a_repair_review_20260920/worker_review
  -p test_worker_boundary_9f34.py -v`: 12 pruebas independientes, 10 aprobadas
  y dos fallos que reproducen R2, confirmados aqui en 0,771 s. Se contrastan
  las constantes leyendo el paquete instalado sin importarlo, validacion
  antes/durante/despues de la muestra, last_error y framing/cierre del stream.
- Suite global de 6.087 y ensayo nativo de 60 s reutilizados por identidad de
  codigo sin cambios. La pausa p99 de 16,60 ms corresponde al bucle padre
  durante una llamada bloqueante; no mide tiempo de ejecucion de ordenes ni
  acredita latencia broker, VM o carga de 3.869 consultas.

## Continuacion

Sol/alto repara R1-R2 en un bloque con sus regresiones y verifica las fronteras modificadas.
La suite global debe renovarse tras cambiar concurrencia; el ensayo nativo
debe vincularse a la nueva identidad antes de entregar. Revisar despues el
cierre de R1-R2; no repetir areas aceptadas sin causa ni ampliar a estrategias.

Python 3.11, pruebas MT5/VM, carga de 60 minutos, E3-B/C/E4 y comparacion de
riesgo E5 conservan su estado pendiente. Sin commit, push, despliegue, reinicio,
MT5 nativo ni ordenes reales en esta revision.
