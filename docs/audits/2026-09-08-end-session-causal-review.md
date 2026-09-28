# Revision Causal Del Cierre Del 08/09

Prioridad aclarada despues de este informe: preparar las bases para simular
miles de estrategias. Las operaciones actuales son controles de fidelidad,
no el objeto de seleccion. La continuacion vigente esta en
`../development/2026-09-08-simulation-foundation-readiness.md`; esta pagina
conserva el resultado y las conclusiones historicas de la revision del dia.

## Veredicto

La prueba va por buen camino como validacion de captura, politica y ejecucion
observada. No va todavia lo bastante lejos para promover capital, lotes o una
politica nueva. La evidencia del dia es favorable para Gold 555 y mucho mas
debil para Dubai, pero procede de una sola sesion prospectiva parcial.

La politica viva, la cuenta y los lotes no se cambiaron durante esta revision.
El bot publicado siguio en `fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd`.
La correccion realizada aqui afecta solo al auditor causal offline y permanece
local, sin commit, push, despliegue ni reinicio.

## Corte Y Fuente

La captura cubre la sesion activada el 08/09 a las 06:15:39.315 UTC hasta el
corte 19:19:52.945592 UTC, 21:19:52 Madrid. No es el dia civil completo de
Madrid. Todas las 18 cestas aceptadas antes del corte estaban cerradas y la
cuenta quedo sin posiciones ni pendientes.

El prefijo reconstruido contiene 913.255 filas y 1.430.602.901 bytes, SHA-256
`03f9b3bd535363a370e0f920ec8bbf06d336d52f0951dbc43384729a8535683e`.
La captura incremental, el archivo ZIP, cada archivo interno, el terminal, la
cuenta demo EUR y la version del codigo quedaron verificados. No se descartaron
filas invalidas ni senales para formar la cohorte.

## Resultado Observado

| Canal | Senales | Ganadoras | Perdedoras | Posiciones | Neto EUR |
| --- | ---: | ---: | ---: | ---: | ---: |
| Gold, canal 2 | 11 | 11 | 0 | 45 | +157,53 |
| Dubai, canal 1 | 7 | 4 | 3 | 14 | +7,56 |
| Total | 18 | 15 | 3 | 59 | +165,09 |

Los 59 cierres y sus costes se conciliaron con deals y ordenes del broker. En
la cuenta observada hubo 0,00 EUR de comision, swap y fee. El maximo volumen
abierto simultaneo fue 0,14 lotes. La caida maxima de la secuencia de resultados
ya realizados fue -38,95 EUR; no debe confundirse con drawdown flotante de la
cuenta.

Gold tuvo 44 salidas pasivas por TP y una salida experta por cierre del
proveedor. El rango por cesta fue +1,72 a +19,81 EUR. Es una senal alentadora,
pero el camino asumio riesgo material: `canal2_2618` llego a un P/L de cesta
observado de -80,18 EUR antes de cerrar en +19,78 EUR. Otras cestas Gold tocaron
-66,58, -54,18 y -41,85 EUR. Once cierres positivos no convierten esa excursion
adversa en riesgo pequeno.

Dubai sumo +46,79 EUR en sus cuatro ganadoras y -39,23 EUR en sus tres
perdedoras. Sus resultados por senal fueron -0,28, +9,42, +16,90, +8,66,
+11,81, -14,01 y -24,94 EUR. La ultima perdida quedo cerca del presupuesto de
cesta de -25 EUR. El total positivo de +7,56 EUR es demasiado estrecho para
afirmar una ventaja estable.

## Politica Y Linaje

Se reprodujeron 328.596 decisiones de gestion desde los inputs y estados
capturados. Resultado, transicion de estado, solicitudes y acciones declaradas
coincidieron en todas ellas; hubo cero errores. Las 59 aperturas quedaron ligadas
uno a uno a sus resultados y deals nativos.

El auditor causal final selecciono 6.929 filas: 6.929 completas y cero
bloqueadas. Las 59 salidas estan contabilizadas: 12 cierres expertos ligados a
su solicitud/intento/deal y 47 salidas pasivas, 44 TP y tres SL. Hubo 16 intentos
de cierre experto y cero bloqueos de atribucion.

La primera pasada habia rechazado cinco carreras validas entre modificaciones
de SL/TP y posiciones que MT5 ya habia cerrado. Se reprodujeron primero como
regresiones. El auditor offline ahora acepta solo dos formas estrictas de
evidencia: un reintento transitorio seguido de preflight `position_gone`, o un
intento enviado cuyo propio resultado fue 10036. Exige identidad, solicitud,
observacion de posicion, niveles, TP preservado, retcode y cronologia. Casos
sin esa evidencia siguen bloqueados. El informe intermedio conserva los fallos;
la version final pasa de 2.715 dependencias bloqueadas a cero sin cambiar dinero,
decisiones, ticks ni ejecucion viva.

## Incidencias Del Dia

En las 18 cestas hubo 45 eventos `anomaly`: 15 criticos y 30 avisos.

- Diez criticos avisaron de que el SL de cesta Dubai aun no estaba confirmado.
  Todos tuvieron confirmacion o recuperacion posterior entre 0 y 6,232 segundos.
- Cinco criticos aplazaron la finalizacion mientras MT5 aun mostraba posiciones.
  Todas las senales cerraron despues, entre 0,651 y 5,274 segundos.
- Catorce avisos de auditoria detectaron niveles transitorios, estado local sin
  posicion o una accion en cola. Los 14 tienen evento de resolucion; el mas largo
  fue el episodio de `canal2_2640`, 230,427 segundos.
- Dieciseis avisos marcaron deslizamiento absoluto alto en aperturas. Sobre las
  59 aperturas, 22 fueron adversas, 32 favorables y cinco exactas respecto a la
  cotizacion solicitada; la mediana direccional fue -0,01 XAU y el peor caso
  adverso fue +0,70 XAU. Los fills reales se conservaron en la contabilidad.

La incidencia operativa mas clara fue `canal2_2640`: tres revisiones de una
modificacion recibieron retcode 10016 (`Invalid stops`). Hubo 205 intentos entre
16:25:52 y 16:29:44 UTC. Durante todos ellos MT5 seguia mostrando el SL anterior
4423,28; la posicion no quedo sin proteccion segun la evidencia capturada. La
ultima revision se confirmo con retcode 10009, SL 4421,88 y TP 4394,20. La cesta
termino en +11,28 EUR. El desenlace fue correcto, pero esa cadencia de reintentos
es el principal riesgo operativo que conviene corregir antes de aumentar carga.

## Controles Del Simulador

Se ejecutaron una sola vez 108 evaluaciones fijas: 18 cestas, dos cintas de
ticks y tres motores, sin buscar candidatas ni ajustar parametros. Las entradas
son los fills observados, por lo que el control es retrospectivo y condicionado,
no una simulacion independiente de entrada ni OOS nuevo.

Los tres motores coincidieron en las 108 evaluaciones. Coincidieron las 59
entradas y volumenes. En las 44 salidas TP coincidieron precio y mecanismo; 43
de 59 importes por posicion fueron exactos. La suma simulada difirio -1,49 EUR
del resultado observado, con diferencias por pata entre -0,76 y +0,21 EUR.

Ninguna hora de cierre fue exacta: el simulador anticipo al broker entre 3 ms y
253,383 s. Los 12 cierres expertos y tres SL requieren la traza causal separada;
el comentario del deal no basta para atribuir la decision del motor. Por ello
las ocho cestas afectadas quedan bloqueadas para paridad integral y las otras
diez no pasan por diferencia temporal.

La cinta historica reprodujo exactamente 135.887 de 135.906 cotizaciones de
trailing capturadas. Faltan 19: cuatro revisiones distintas en el mismo
milisegundo, en `canal2_2590` y `canal2_2599`, y 15 milisegundos ausentes en
`canal2_2631`. Insertar esas 19 cotizaciones en una cinta de sensibilidad no
cambio ningun resultado significativo, pero no demuestra que la cinta este
completa. `full_live_simulator_parity_verified` y `full_tick_replay_verified`
permanecen `false`.

## Decision Y Continuacion

La decision prudente es continuar observando la politica actual sin cambiarla.
Antes de cualquier promocion de capital o lotes hacen falta:

1. Corregir y probar offline la tormenta de reintentos 10016, conservando el SL
   existente, coalescencia y cierre seguro.
2. Capturar una cinta v3 exacta en proximas sesiones para comparar tambien la
   hora de instalacion de niveles y la secuencia de salidas gestionadas.
3. Mantener Gold 555 y Dubai congelados durante nuevas sesiones prospectivas;
   no seleccionar variantes con esta misma cohorte.
4. Repetir contabilidad, excursion adversa, causalidad y paridad por senal. La
   decision de capital debe esperar evidencia de perdidas Gold y de la cola
   Dubai, no solo mas cestas ganadoras.

## Verificacion Final

La suite completa del codigo local termino con 3.668 pruebas aprobadas, cero
fallos, errores u omisiones y 633 avisos en 178,12 segundos. Las 362 fuentes y
los cuatro manifiestos de entrada permanecieron iguales durante la ejecucion.
SHA-256 del XML: `7598fcfb2e653bb1b8ce0a00c11047dad8faf20c0a7c045e66a02d3ab105bdd8`.

Una comprobacion remota de solo lectura a las 21:10:25 UTC encontro la VM en
`fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd`, repositorio limpio, bot, watcher y
terminal originales activos, heartbeat de 10,355 segundos y estado del bot
`flat`: cero senales, cero posiciones del bot y cero entradas pendientes. La
publicacion de telemetria estaba correcta, sin archivos pendientes, pausa,
actualizacion ni tareas temporales. Esta lectura final del heartbeat no sustituye
la comprobacion nativa de toda la cuenta; esa quedo verificada en el corte de la
captura.

## Evidencia

- Captura: `runtime_data/causal_capture_today_20260908/end_1919/`.
- Auditoria final: `runtime_data/causal_capture_today_20260908/end_analysis_v4/`.
- Controles fijos: `runtime_data/causal_capture_today_20260908/end_simulation_controls_v1/`.
- Ciclo de incidencias: `runtime_data/causal_capture_today_20260908/end_anomaly_review_v1/`.
- Suite final: `runtime_data/causal_capture_today_20260908/end_review_verification_v1/`.
- Regresiones del auditor: `tests/test_audit_causal_lineage.py`.
- Implementacion local: `tools/audit_causal_lineage.py`.

Los resultados intermedios `end_analysis_v1`, `v2` y `v3`, las pruebas rojas y
las fuentes originales se conservan. Ningun artefacto se relabela como evidencia
independiente ni se publica automaticamente.
