# Reparacion E3-A: Cliente Y Lecturas Aisladas

Entrega local del 20/09/2026, rama `feature/gold-555-live-trial`, HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`. Repara los cuatro hallazgos de la
[revision E3-A](2026-09-20-e3-a-review.md) sin conectar operaciones ni cambiar
estrategias. La reparacion esta verificada localmente; requiere una nueva
revision independiente antes de aprobar E3-B.

Estado posterior: la [revision de esta reparacion](2026-09-20-e3-a-repair-review.md)
confirma los casos originales y encuentra R1-R2 en cierre durante arranque y
stdout nativo. La evidencia de esta entrega se conserva; E3-B sigue pendiente.
La [segunda reparacion](2026-09-20-e3-a-second-repair-results.md) cierra ambos
casos localmente y queda pendiente de revision formal de aceptacion.

## Correcciones

- **F1, bootstrap del hijo:** el cliente ya no usa `multiprocessing.spawn`.
  Arranca un ejecutable Python minimo, `mt5_worker_entry.py`, que recibe por una
  tuberia privada la configuracion y una especificacion JSON de la fabrica. El
  hijo no vuelve a ejecutar el entrypoint de la aplicacion ni carga
  executor/listener/main/state/journal. Los secretos no aparecen en argumentos
  de proceso y la salida normal del backend no puede mezclarse con el protocolo.
- **F2, cierre independiente:** el cierre usa una via de control dedicada. No
  depende del executor general de asyncio ni del hilo de transporte que puede
  estar esperando una respuesta; detiene el hijo y despues recoge los recursos.
- **F3, flags de ticks:** `copy_ticks_from` admite exactamente las constantes
  publicas `COPY_TICKS_ALL=-1`, `COPY_TICKS_INFO=1` y
  `COPY_TICKS_TRADE=2`. Rechaza `0`, `3`, `6` y valores ajenos.
- **F4, muestra publicada:** ACCOUNT y TERMINAL validan tambien el resultado
  concreto que van a publicar. Una cuenta/servidor contradictorios o un terminal
  desconectado producen UNKNOWN; una cuenta incompatible invalida `ready`.

Las cinco regresiones principales se escribieron primero y fallaron por las
causas esperadas. Tras la reparacion pasan. Se anadieron dos controles del
transporte para mantener secretos fuera de la linea de comandos y aislar el
stdout del backend.

## Verificacion

Evidencia: `runtime_data/e3_a_repair_20260920/`; su identidad se registra en
`repair_manifest.json`.

- Frontera E3-A: 83 pruebas, cero fallos/errores/omitidas, 9,159 s (`e3a.xml`).
- Bloque focal ampliado: 151 pruebas, cero fallos/errores/omitidas, 20,598 s
  (`focused.xml`).
- Suite global: 6.087 pruebas, cero fallos/errores/omitidas, 647 advertencias,
  367,86 s (`full_suite.xml`). Las advertencias no se han usado como excepcion.
- Ensayo nativo final de 60,011 s: 3.869 muestras; p99 16,60 ms y maximo
  17,03 ms del bucle padre. Control negativo 406,73 ms, respuesta FOUND y
  trabajador terminado al cerrar (`native60_final_python314.json`). Es PyDLL
  local que retiene el GIL, no MT5 ni la VM.
- Camino completo basico aprobado con el Python 3.12.14 incluido: PID hijo
  distinto y `application_entry_reran=false` (`python312_entry_probe.json`). Ese
  entorno no incluye pytest. Python 3.11 sigue sin estar disponible.
- Compilacion de los archivos afectados aprobada. El inventario MT5 coincide
  con su baseline y mantiene visibles diez modulos live legacy por migrar.

Identidad principal:

- `mt5_client.py`: `a41db93d90d2566fe20e040e09ce631dfa64cbe3c67dc317060e749338553030`
- `mt5_worker.py`: `ae01e958a1ccbf3f582bd4aa30a589a6041c5f25b47f011c765e81b1b139fb01`
- `mt5_worker_entry.py`: `5b47703b0876dcd776823ff0a352867a7297e051544c6e8688bc9cb67cfdd933`
- `mt5_read_protocol.py`: `d4c5d863be51e89564691656cfe099b3f46e070406c0590f0434b5490517d374`

## Limites Y Siguiente Paso

No se ha llamado a MT5 real, comprobado la VM, ejecutado carga de 60 minutos ni
validado Python 3.11. E3-A sigue sin conectar lecturas u operaciones al bot live;
los diez accesos legacy permanecen pendientes y E3-B no esta implementado.

Siguiente paso: Astra/alto revisa F1-F4 y sus regresiones sobre esta identidad.
Solo si esa revision acepta la frontera se disena e implementa E3-B. Todo queda
local: sin commit, push, despliegue, reinicio ni orden real.
