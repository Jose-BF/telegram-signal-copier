# Entrada Y Transporte Incrementales

Continuacion local autorizada por el usuario. Rama
`feature/gold-555-live-trial`, base `7415941a410d18288fa4e08da76809f70b125efa`;
cambios previos conservados. Sin commit, push, acceso a VM, reinicio ni ordenes.
El objetivo completo no esta terminado. La limitacion del objetivo automatico
no se presenta como trabajo autonomo en segundo plano.

## Frontera Implementada

Dos dependencias de la conexion entre cestas, sin duplicar estrategias:

1. `_ClientEntryTrigger` observa una cotizacion cada vez. La ruta cliente ya
   no ejecuta `_causal_entry_index` sobre toda la cinta para escoger la primera
   entrada antes de empezar. Conserva reglas de mercado, demora, pullback,
   momentum, reversal y no entrada; disponibilidad, expiracion exclusiva,
   BUY/SELL, Bid/Ask invalido, contexto y cierre del proveedor. El selector
   anterior permanece para rutas sin cliente y como referencia de regresion.
2. `TransportSession` mantiene el recurso entre llamadas. `submit` valida un
   lote completo sin mutaciones parciales; `advance` observa eventos sin
   enviar; `dispatch` concede como maximo una solicitud. Asi el consumidor
   puede reaccionar a una respuesta antes de conceder otra llamada. El API
   previo `simulate_transport` y el CLI drenan esta misma sesion; no se crea
   una segunda implementacion de las reglas de transporte.

El contrato de sesion impide retroceder o saltarse el siguiente evento y
exige despachar trabajo elegible antes de avanzar el reloj. Una respuesta de
duracion cero requiere otra observacion en ese mismo instante. Solicitudes
generadas por una respuesta pueden participar en el siguiente despacho de
ese instante; no reordenan retroactivamente uno ya concedido. La convencion
de empates es por ronda causal de inputs disponibles, no una garantia del
orden de asyncio ni una prueba de microtiempos del broker.

Snapshots separados del estado interno. No se declara completado antes del
corte ni con inputs recien presentados en el corte aun sin observar. El
agotamiento de eventos bloquea toda continuacion; respuestas ausentes o
tardias no liberan artificialmente el recurso. SL/TP pasivos siguen siendo
eventos declarados, no fills calculados por el coordinador.

La ruta cliente sin entradas, eventos ni bloqueos devuelve resultado vacio
con exposicion/riesgo cero. Esto incluye cancelacion anterior al trigger, y
evita depender de si la busqueda antigua habia encontrado una entrada futura.
No se normaliza una solicitud enviada con respuesta/fill pendiente: conserva
su incertidumbre y bloqueo. No se amplian capacidades de perfiles.

## Verificacion

- 46 pruebas de entrada incremental; equivalencia del selector en 1.080
  escenarios deterministas de seis modos, BUY/SELL, tres latencias, ordinales
  repetidos, expiracion y cotizaciones invalidas. Ademas, prefijos con distinto
  futuro, rechazo de contexto sin reintento y cancelacion anterior al trigger.
  Antes del cambio, la prueba que prohibe precalcular la primera entrada falla
  en la llamada efectiva a `_causal_entry_index`.
- Los 48 resultados completos previamente congelados siguen iguales, incluidos
  eventos y riesgo. No se regenero su fixture para aprobar el cambio.
- 12 pruebas de sesion incremental: feedback respuesta -> nueva solicitud,
  avance seguro, empate de duracion cero, snapshot, validacion atomica,
  presupuesto, llegada al corte, SL durante espera y comparacion por etapas.
  El falso exito al presentar un job en el cutoff fallo antes de corregirse.
- Antes de extraer la sesion se congelo el digest de informes completos de
  64 escenarios / 1.280 solicitudes del coordinador anterior. Se conserva:
  `187aa0c1bc26312a08f93ec0219b476d7679ba8f1c826af9dcf5264d83b45b1d`.
  Incluye pendientes y rechazos, no solo casos terminados. La misma matriz
  tambien compara el driver por etapas con el wrapper completo.
- 231 focales aprobadas, Python 3.11.9 / pytest 9.1.1, 2,67 s. Archivos:
  `test_incremental_transport`, `test_shared_transport`,
  `test_run_shared_transport_controls`, `test_mt5_scheduling`,
  `test_client_incremental_entry`, `test_iterative_resume`,
  `test_iterative_client`, `test_client_causal_fill`.
- Revision independiente de entrada sin hallazgos materiales, con 3.600
  comparaciones adicionales en memoria y cero diferencias de indice. Revision
  independiente de transporte sin hallazgos pendientes, incluido el caso
  corregido de inputs al corte; controles job, SL y cutoff cero. Alcance acotado,
  no revision de todo el proyecto ni prueba de paridad de estrategia completa.
- Los cuatro controles del protocolo ya archivado de transporte conservan
  exactamente sus resultados: tres ciclos terminados y uno bloqueado como
  corresponde. Nuevo informe separado, sin sobrescribir el anterior:
  `reports/e5-incremental-entry-transport-20260921/transport-controls.json`.
- Primera suite global: **6.608 aprobados y un fallo**, una advertencia previa
  de pandas, 481,86 s. El unico fallo fue `FileExistsError` en el lanzador al
  coincidir dos nombres de log basados solo en el reloj. Se conserva el JUnit
  `pytest.xml`, SHA256
  `f7e983e9ff6e0918f57210d8370a65ae7fb0ec42aacecd9f4b977de8baffb57f`.
  No se descarta como fallo intermitente ni se declara la suite aprobada.

Tambien se cierra la revision independiente pendiente del mini-fix runtime
`_DispatchDecision`, sin cambios adicionales de codigo: seis probes en memoria,
sin hallazgos materiales. Evidencia y limites en el
[informe de cancelacion](2026-09-21-runtime-cancellation-races.md).

Identidad previa a la suite en
`reports/e5-incremental-entry-transport-20260921/sources.json`: fuentes de
investigacion, archivos de este bloque, referencias y runtime. Identidad
de implementacion `8e789ef4db47b7223515e7d57d22c18e6404c082781edeb0512fbbbeb9f80e6f`.
No se cambia codigo Python durante la comprobacion global. La carga runtime
anterior sigue siendo evidencia de su propio alcance, no de este simulador;
los cambios iniciales de simulacion no modifican el runtime. La correccion
posterior afecta al lanzador, no al trabajador/diario medidos en esa carga.

## Incidente Del Lanzador

La primera suite completo todos los tests antes de editar Python. El fallo
de nombre repetido se reprodujo con reloj fijo y reinicios ficticios; tres
regresiones de reloj fijo, atrasado y nueva invocacion fallaron antes del fix.

`tools/start_logged_bot.py` conserva prefijo UTC legible y anade UUID por
intento. Abre salida y errores de forma exclusiva; ocho intentos maximos
solo si hay colision de nombres. Cierra el archivo propio si falla abrir el
segundo, preserva todos los anteriores y solo lanza el supervisor cuando
ambos estan abiertos. No confunde permisos/disco o error del hijo con una
colision, ni modifica codigos de salida/backoff, conexion o estrategia.

Ocho regresiones nuevas cubren reloj fijo/atrasado, invocaciones separadas,
colision en cada archivo, agotamiento de intentos, permiso denegado y error
del subprocess. 108 focales pasan en 9,79 s con resiliencia, lanzador,
supervisor y prioridad Windows. Los consumidores encontrados usan glob de
nombre/prefijo y admiten el sufijo; no se han inspeccionado scripts externos
de la VM. Es correccion preventiva general, no atribucion de reinicios
historicos al mismo mecanismo sin evidencia.

Revision independiente del parche final sin hallazgos materiales; ocho probes
adicionales con archivos/subprocess simulados incluyeron disco lleno y cierre
de streams. Launcher revisado SHA256:
`6fa03a7ae69eb1550f20037b8ccac3151d12c91318a7288ac50f25463a45c4de`.
Una colision exclusiva en `.err` puede dejar el nuevo `.out` vacio; se conserva
y se usa otra identidad sin borrar evidencia. No certifica scripts externos.

La identidad antes de verificar la correccion final se conserva en
`sources-final.json`. Los archivos del primer bloque y la identidad de
investigacion permanecen iguales tras aquella suite. No se sobrescriben
fuentes ni JUnit del fallo; la siguiente ejecucion usa `pytest-final.xml`.

Suite final completada: **6.617 aprobados**, cero fallos/errores/omitidos, una
advertencia previa de pandas, 466,78 s en consola. JUnit informa 460,037 s de
suite; se conservan ambas mediciones sin reemplazar una por otra. Comando:

```text
python -m pytest -q --junitxml=reports/e5-incremental-entry-transport-20260921/pytest-final.xml -o junit_family=legacy
```

SHA256 JUnit:
`cd5877f619cfda34a1a6502414726ca6defd4338a9d94b4acec689dd37b35983`.
SHA256 controles de transporte:
`222aac9df7a0f5c19b7076d856f1ee7d75d458c250a7734d61c44ba6c7e399ea`.
Comprobacion posterior en `verification.json`: identidad de investigacion,
archivos del bloque y fuentes del CLI sin cambios. No quedan procesos pytest,
medidor, worker o guardian de estos ensayos. Diff check aprobado. Estado local
sin publicar; la revision no acredita version o salud actual de la VM.

## Conexion Pendiente

Estado historico al cerrar este bloque. Continuacion posterior:
[politicas conectadas](2026-09-21-connected-policy-replay.md), con enlace ya
implementado para los perfiles diagnosticos admitidos. Permanecen los limites
de disponibilidad, autorizacion, riesgo y capacidades explicados alli.

Cada cesta todavia usa su `ClientBook` local. La sesion nueva no recibe aun
sus solicitudes ni devuelve concesiones/confirmaciones a sus books de mercado
y proteccion. No es un replay conjunto ya operativo.

Falta una frontera interna de decision/despacho por cotizacion, ademas de la
pausa entre cotizaciones: no se puede esperar al siguiente tick de la cesta
para entregar una concesion ya disponible, pues inventaria demora. Tampoco
debe congelarse el broker pasivo mientras espera el cliente.

La conexion debe usar identidades canal/senal/ticket/secuencia y las fases
de `MarketBook`/`ProtectionBook` para conservar el recurso. La API actual
conserva preparacion y servicio declarados; aun falta resolver disponibilidad
y finalizacion desde esos books sin sumar otra duracion duplicada. `started`
significa concesion del transporte, no autorizacion nativa ni fill.

Siguiente cierre verificable: control integrado con gestion de una cesta
ocupando la conexion, decision propia de otra cesta en espera, SL instalado
actuando y respuesta modificando las decisiones posteriores. No generar las
solicitudes leyendo resultados independientes ya terminados. Mantener controles
monocesta, falta de respuesta, BUY/SELL, ordinales, identidad y sufijos futuros.

Los gates de basket_guard, fast/oracle, portfolio y certificacion siguen
intactos. Contraste historico amplio, retencion compatible y preflight/entrega
operativa son tareas separadas aun abiertas. Ningun resultado de este bloque
demuestra rentabilidad, calibracion de latencia o que la VM tenga estos cambios.
