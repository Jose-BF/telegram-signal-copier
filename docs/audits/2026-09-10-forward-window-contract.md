# M6: Ventanas Forward Explicitas

Estado local comprobado el 2026-09-10, 09:53 UTC. Rama
`feature/gold-555-live-trial`, HEAD
`892bc33c8f6c19be7248401ec3cf6a27ba3d0563`, worktree con cambios previos
preservados. No commit, push, red, VM, MT5, ordenes ni agentes nuevos.
No se ha creado ningun perfil, verificacion o freeze real. Las pruebas usan
datos y relojes sinteticos; no se ha ejecutado la suite completa.

## Contrato Y Compatibilidad

- `prepare(profile_path, *, out=None, now=None, start_utc=None, end_utc=None)`.
  `ready` y `freeze` admiten `--start-utc` y `--end-utc`: ambos o ninguno.
- Sin argumentos de ventana se conserva `simulator_forward_protocol_v1`,
  exclusivamente 2026-09-09 06:30-08:30 UTC. `START` y `END` siguen siendo
  constantes historicas. El plazo vencido no se rebautiza como prospectivo.
- Con ambos argumentos se genera `simulator_forward_protocol_v2`. Se conserva
  el resto del esquema: no cambia el perfil v1, la estrategia ni los gates.
  Inicio y fin deben ser timestamps conscientes de zona, explicitamente UTC
  (`Z`, `+00:00` o datetime con offset cero), con inicio menor que fin y duracion
  maxima `DATA_RULES.max_window_hours = 2`. No se adivina la zona local ni se
  normalizan silenciosamente offsets distintos de cero.
- El fingerprint existente incluye version, fechas, instante real de freeze,
  perfil y su SHA, codigo/runtime, estrategia, reglas y presupuestos. El SHA
  del protocolo enlaza dataset, ejecucion independiente y comparacion.
  Cambiar fechas crea otra identidad; los datos de otra identidad no sirven.
  Recalcular un fingerprint no convierte v1 en una ventana distinta.
- Los esquemas de dataset/resultado/cartera siguen en v1: sus estructuras no
  cambian y ya vinculan el SHA del protocolo forward. No hay migracion de
  protocolos archivados ni reescritura de outputs existentes.
- `_protocol_window` extrae y valida la ventana. Readiness, validacion del
  protocolo/dataset, compilacion, tres motores, capturas, metadatos, reloj,
  limites de tapes y cartera reciben esas fechas, sin modificar globales.

## Plazos Y Gates

La verificacion debe terminar antes del inicio y no estar en el futuro respecto
al instante de admision. Para freeze tambien se limita por el reloj real.
`now` permite comprobaciones reproducibles, pero no permite antedatar una
congelacion: `frozen_at_utc` sale de `_now()` y se comprueba de nuevo el plazo
tras los hashes finales, antes de publicar. Un reloj que retrocede bloquea.

Readiness es solo lectura. Cada freeze requiere un directorio nuevo. Se
revalidan fingerprint, implementacion/runtime, perfil, verificacion y todos los
proofs de productores. Los hashes antiguos no se actualizan para aparentar
admisibilidad con codigo nuevo. Esta modificacion requiere que el coordinador
termine su verificacion del conjunto estable y prepare pruebas de procedencia
actualizadas antes de cualquier freeze real.

Se mantienen el intervalo de mensajes `[inicio, fin)`, cutoff inclusivo dentro
del intervalo, lookback EURUSD de 5 s y lag maximo de captura de 120 s. El lag
de evidencia no amplifica la cohorte. Los presupuestos siguen siendo 2 senales
minimas, 11 maximas y 132 evaluaciones maximas, sin truncamiento de senales.
Una ventana vacia o parcial sigue incompleta. Las filas bloqueadas permanecen
en el denominador; se cuenta solo la ejecucion de motores realmente realizada.

`native_accounting` y `request_native_binding` siguen exigiendo `exact`.
Las ausencias de cobertura y los importes desconocidos siguen visibles.
No se crean capacidades ni verificaciones automaticas: siguen separados los
gates de cartera acotada, contabilidad nativa, realismo y busqueda masiva.

## Uso Por El Coordinador

Ejemplo de API, solo despues de la verificacion del conjunto estable y con
`profile` y `new_out` correspondientes a nuevos artefactos del coordinador:

```python
from tools.prepare_simulator_forward import prepare

window = {"start_utc": "2026-09-10T13:00:00Z", "end_utc": "2026-09-10T15:00:00Z"}
readiness = prepare(profile, **window)
assert readiness["status"] == "ready_to_freeze", readiness
frozen = prepare(profile, out=new_out, **window)
assert frozen["status"] == "frozen_waiting_for_cohort", frozen
```

Equivalente CLI desde la raiz del proyecto; comandos documentados, no ejecutados:

```powershell
python tools/prepare_simulator_forward.py ready --profile $profile --start-utc 2026-09-10T13:00:00Z --end-utc 2026-09-10T15:00:00Z
python tools/prepare_simulator_forward.py freeze --profile $profile --out $new_out --start-utc 2026-09-10T13:00:00Z --end-utc 2026-09-10T15:00:00Z
```

Jueves 10: 13:00-15:00 UTC, 15:00-17:00 Madrid. Ambos pasos y su verificacion
deben preceder al inicio; si no, esta ventana queda bloqueada. Readiness no
reserva el plazo ni garantiza que freeze vaya a completarse a tiempo.

Viernes 11: puede prepararse 13:00-15:00 UTC con el mismo API y ambas fechas
cambiadas a `2026-09-11`, en otro directorio. Hacerlo despues de las reparaciones
del jueves y de su verificacion aplicable, siempre antes del nuevo inicio.
No cambiar o ampliar la cohorte del jueves despues de observar su resultado.

`produce` y `run` no reciben nuevas fechas: consumen `--protocol` validado.
La captura debe declarar exactamente esa ventana y conservar sus bindings de
helper, commit y cuenta. El colector existente y sus hashes no se han editado;
su configuracion/ejecucion real corresponde al coordinador. No se promete que
ocurran dos senales naturales y no se generan ordenes para completar la muestra.

## Verificacion Focalizada

TDD: 21 casos nuevos fallaron inicialmente por no existir los argumentos de
ventana. Tras implementar y ampliar los casos, ejecucion final:

```text
python -m pytest -q tests/test_forward_window_contract.py tests/test_prepare_simulator_forward.py tests/test_run_simulator_forward.py tests/test_compare_simulator_forward.py::test_real_independent_subprocess_then_native_output_consumed_by_forward_contract
104 passed in 20.98s
Python 3.14.2; pytest 9.0.3; win32
```

Incluye 47 pruebas del contrato nuevo, 45 de preparacion, 11 del productor y
una integracion existente con subprocess independiente y comparador nativo.
Se cubren coexistencia jueves/viernes en un proceso y ejecucion en orden
inverso, archivo v1 intacto, limites temporales, falsificacion de protocolo,
datos y fuentes de otra ventana, hashes alterados, captura nativa sintetica,
control completo y denominadores bloqueados con dinero desconocido.
La suite completa coordinada sigue pendiente; esta evidencia no la sustituye.

SHA-256 de las dos herramientas verificadas:

```text
tools/prepare_simulator_forward.py  1fed8d59816bda7c02ed588cac988b1c9365b2421b220374ef2ffc293e3017a6
tools/run_simulator_forward.py      e1d78aac090bd2fa8ed2136a10a7f56ab5fa5c4de764c5af40faaa57824f4de4
```

Write set exclusivo: las dos herramientas anteriores,
`tests/test_prepare_simulator_forward.py`, `tests/test_run_simulator_forward.py`,
`tests/test_forward_window_contract.py` y esta auditoria. El comparador solo se
ha leido y comprobado, no editado. No se han tocado perfil real, proveniencia,
dataset historico, CLIs de agentes ni informes de otros responsables.
