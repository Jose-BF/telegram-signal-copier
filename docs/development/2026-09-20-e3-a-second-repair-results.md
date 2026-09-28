# Segunda Reparacion E3-A

Estado posterior: [revision formal aceptada](2026-09-20-e3-a-acceptance.md).
E3-A queda aceptado como base local para E3-B; validacion operativa pendiente.

Entrega local del 20/09/2026, rama `feature/gold-555-live-trial`, HEAD base
`7415941a410d18288fa4e08da76809f70b125efa`. Repara R1-R2 de la
[revision anterior](2026-09-20-e3-a-repair-review.md) sin conectar el lector al
bot, cambiar estrategias ni activar operaciones.

## Cambios

- **R1, cierre durante arranque:** el cliente publica la propiedad del proceso
  y sus tuberias inmediatamente despues de `Popen`, antes de escribir el
  bootstrap. La via de control puede terminar un hijo durante initialize o una
  escritura bloqueada. El cierre detiene primero, recoge despues el spawn/start
  y realiza una segunda parada si la creacion de proceso compitio con close.
  Los dos pools propios se cierran despues de recoger transporte y respuestas.
- **R2, stdout nativo:** el entrypoint duplica la tuberia JSON a un descriptor
  privado y redirige el descriptor 1 completo a stderr antes de importar el
  trabajador. `print`, `sys.__stdout__` y escrituras nativas por descriptor 1
  quedan fuera del protocolo; no se aceptan ni saltan lineas contaminadas.

Se convirtieron en regresiones permanentes las cuatro reproducciones: initialize
bloqueado, hijo creado que no lee bootstrap, escritura por descriptor y por
stdout original. Fallaron primero por sus causas esperadas y pasan tras el
cambio. Los casos anteriores de cancelacion, cierre concurrente, pool general
saturado, respuesta tardia, identidad y bootstrap minimo se conservan.

## Verificacion

Evidencia: `runtime_data/e3_a_second_repair_20260920/`; su identidad se registra
en `final_manifest.json`.

- Regresiones nuevas: cuatro fallos rojos (`red.xml`) y despues cuatro aprobadas
  en 1,22 s (`green.xml`).
- Frontera E3-A: 87 pruebas, cero fallos/errores/omitidas, 10,34 s (`e3a.xml`).
- Bloque E1-E3 focal: 157 pruebas, cero fallos/errores/omitidas, 22,52 s
  (`focused.xml`). Las dos carreras de cierre pasaron ademas cinco repeticiones.
- Controles independientes que descubrieron R2: 12/12 aprobados en 0,867 s;
  descriptor, stdout original y `print` producen FOUND con datos integros.
- Suite global: 6.091 pruebas, cero fallos/errores/omitidas, 647 advertencias,
  362,97 s (`full_suite.xml`).
- Ensayo nativo final: 60,013 s, 3.873 muestras, p99 16,51 ms y maximo
  18,05 ms del bucle padre; control negativo 401,61 ms, FOUND y proceso hijo
  detenido (`native60_final_python314.json`). Es PyDLL local que retiene el GIL,
  no una medicion de orden, broker, MT5 o VM.
- Camino completo basico aprobado con Python 3.12.14: PID hijo distinto,
  `application_entry_reran=false` y cierre correcto
  (`python312_entry_probe.json`). Python 3.11 sigue sin estar disponible.
- Compilacion aprobada; inventario MT5 sin cambios, con diez modulos live legacy
  todavia pendientes de migrar.
- Segunda lectura independiente y acotada de R1-R2: ningun hallazgo material.
  Repitio las cuatro regresiones, comprobo la propiedad/cierre del proceso y
  verifico en Windows que el handle nativo de stdout apunta a stderr.

Identidad principal:

- `mt5_client.py`: `746f42580b473b6cb5b5aa641700aae1b17150bdb48cc9129155d6f1d59c1141`
- `mt5_worker.py`: `ae01e958a1ccbf3f582bd4aa30a589a6041c5f25b47f011c765e81b1b139fb01`
- `mt5_worker_entry.py`: `38c8444d0fc63435197fe4383eabc00452f0bedb4a3a03f0faac53ec0819b3ac`
- `mt5_read_protocol.py`: `d4c5d863be51e89564691656cfe099b3f46e070406c0590f0434b5490517d374`

## Limites Y Continuacion

E3-A sigue siendo local y no esta integrado en rutas live. No se ha llamado a
MT5 real, comprobado la VM, validado Python 3.11 ni ejecutado carga de 60 min.
Los diez accesos legacy, propietario unico/huerfanos E4 y E3-B/C permanecen
pendientes. Todo queda sin commit, push, despliegue, reinicio ni orden real.

La revision formal posterior acepta esta identidad y habilita la implementacion
local de E3-B con Sol/alto segun el plan vigente.
