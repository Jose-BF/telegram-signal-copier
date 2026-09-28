# Dubai Y Ejecuciones Pendientes

Estado: 705 focalizadas aprobadas; global con 7408 aprobadas y un fallo de
sincronizacion de test, corregido despues con 80 pruebas del bloque aprobadas.
No se ha repetido toda la global tras ese cambio de test. Evidencia congelada
`A:/cd-7r2evd4b/e`; complemento `A:/cp-u11/green.xml`. No hay publicacion ni
nueva comprobacion VM, ni admision de estrategia completa.

## Recorrido Dubai

Ocho controles BUY/SELL conectan `_open_canal1_from_text`, admision y entrada
durable, worker, calculo real de proteccion inicial y stop de cesta, monitor,
guard, cierres, historial causal y finalizador. El mensaje ya esta interpretado:
no se certifica aqui la ingestion de Telegram. Precios, contrato y costes son
supuestos sinteticos declarados, no evidencia monetaria nativa.

Se compara por cotizacion el dinero realizado/flotante, tickets, volumen y
precios de entrada. Cuatro controles cubren profit lock y SL; otros dos cambian
el precio durante preparacion y comprueban ancla al fill y expiracion desde
recepcion. Los dos restantes impiden la entrada si falta FX para calcular la
proteccion obligatoria. No se introducen stops precalculados en el fixture.

El ensayo `A:/du-bcc7c/result.xml` contiene seis aprobadas y dos fallidas. Las
dos demuestran un defecto real: una segunda actualizacion de stop, despues de
completar la primera, reutilizaba su revision durable. El conflicto de identidad
impedia instalar el nuevo stop de cesta. La correccion asigna una revision
durable por accion/version local, con identidad estable para reintentos y
restauracion. La asignacion atomica consulta un indice de linaje y no depende
de que la cola conserve acciones ya terminadas. Un resultado previo incierto,
incluido TP dependiente, bloquea una nueva ejecucion en asignacion y despacho.

`A:/pr-52ab7/focused.xml` contiene 131 aprobadas y dos fallos; las 24 regresiones
de continuidad pasan. Verificacion del padre `A:/cp-u08/result.xml`: 97 aprobadas
y los mismos dos fallos. Los stops ya se instalan correctamente; la expectativa
de anomalias omitia el aviso real de stop pendiente al encolar una segunda
actualizacion despues de la correccion post-fill. Se exige ahora exactamente
ese aviso, ticket 1001, presupuesto 25 y campos completos, solo en held_prepare;
se mantienen las comprobaciones independientes de ambos stops instalados.
No se silencian las anomalias ni se consideran todos los avisos aceptables.

## Entrada Ejecutada Sin Confirmacion

El monitor podia finalizar una cesta omitiendo una DCA ejecutada cuya respuesta
era desconocida. En el control sintetico, contabilizaba -162.4 en vez de -279.7,
al no incorporar -117.3 de esa pata. Son cifras del modelo de prueba, no de la
cuenta real ni una explicacion confirmada de un episodio historico.

La presencia de una entrada por reconciliar bloquea ahora el total monetario
completo y la finalizacion, incluida la expiracion del plan o un cierre pedido.
No se inventa la confirmacion ni se reenvia la orden a ciegas. Los controles
integrados conservan como incompleto el resultado modelado si falta el ACK.
Un cierre activo con respuesta desconocida puede tener contabilidad conocida
por historial; eso no certifica que su transporte este completamente conciliado.

Evidencia: `A:/cp-u01/red.xml`, dos fallos integrados iniciales;
`A:/cp-u02/red.xml`, 17 fallos unitarios; `A:/cp-u05/result.xml`, 23 aprobadas.
La recuperacion durable posterior de la entrada sigue sin demostracion integral;
limpiar el marcador en una prueba unitaria no demuestra esa recuperacion.

## Cancelacion Mientras Se Espera

La cancelacion canonica del plan no era respetada por todas las fronteras de
DCA. Podia abrir patas futuras o no solicitar el cierre de una pata confirmada
tarde. Se comprueba ahora antes del envio, en la autorizacion tras preparar y
al recuperar el resultado. Una entrada pendiente de reconciliacion sigue siendo
recuperable; la cancelacion no autoriza un envio nuevo ni las patas siguientes.

Seis regresiones BUY/SELL cubren cancelacion anterior al tick, durante prepare
y durante espera de respuesta. En el ultimo caso se verifica el comando de
cierre, no su ejecucion completa: el test lo declara y no atribuye un cierre
efectivo al modelo. `A:/cp-u06/red.xml`: seis fallos reproducidos.
`A:/cp-u07/green.xml`: 72 aprobadas, incluidas esas seis y controles relacionados
de entradas desconocidas, DCA y monitores de ambos canales.

La revision independiente detecto la misma omision en la recuperacion al
arrancar. Se reprodujo BUY/SELL en `A:/cp-u09/red.xml` y se corrigio tambien esa
frontera. `A:/cp-u10/green.xml`: 35 aprobadas. Los dos nuevos controles leen un
fill confirmado del almacen durable y comprueban el cierre en la cola real y
su identidad al restaurar el spool. No simulan un reinicio del proceso entero
ni la ejecucion final del cierre; verifican la ruta de recuperacion y persistencia.

## Ensayos Conjuntos Intermedios

- `A:/cd-_z6gk1ax/e`: 607 aprobadas y dos fallos de coordinacion del control
  de gap/DCA; fuentes estables y 72 trazas. Las ocho pruebas Dubai pasan.
  Las acciones anadidas durante un lote de cola esperan la siguiente cotizacion;
  el test esperaba vaciado sin entregarla. Se avanza ahora la cotizacion
  identica ya existente antes de esperar, sin elevar tiempos ni presupuestos.
- El fixture de entrada captura ademas anomalias y sustituye la notificacion
  externa por un sink local. El fallo intermedio habia activado el aviso de
  ticks desconocidos al agotar el mailbox; su salida registra el rechazo de
  envio por cliente desconectado. No se atribuye ese aviso a una VM real.
- `A:/cd-qno3vrcv/e`: 609 focalizadas aprobadas. La global fue detenida
  expresamente al incorporar el defecto de recuperacion encontrado en revision;
  salida parcial conservada, no se cuenta como global aprobada.

## Verificacion Del Bloque

- `A:/cd-7r2evd4b/e/focused.xml`: 705 focalizadas aprobadas, sin fallos,
  errores ni omisiones. 72 trazas conservadas; sus hashes se han contrastado
  contra el manifiesto sin discrepancias.
- `A:/cd-7r2evd4b/e/full.xml`: 7409 casos, 7408 aprobados y un fallo, cero
  errores/omisiones, 466.140 segundos de proceso. Fuentes e identidad estables
  durante la ejecucion. SHA256:
  `f19e3e258bd15fae6a6efd3724f2b5011484a491e9022b236778c70d1dc99728`.
- El unico fallo fue `test_cancellation_concurrent_with_executor_start_is_not_swallowed`:
  esperaba diez vueltas del bucle despues del finally del thread, antes de que
  necesariamente se ejecutara el callback que libera admision. Ahora observa
  la liberacion real con evento y el mismo limite de dos segundos, manteniendo
  las comprobaciones de cancelacion, bloqueo de otra orden y capacidad final.
  No se modifica `mt5_gateway.py` ni el runtime.
- `A:/cp-u11/green.xml`: 80 aprobadas, incluyendo el modulo de carrera, gateway,
  cliente de lectura y cliente de operaciones. La comparacion con `sources.json`
  confirma que solo cambio `tests/test_gateway_cancellation_race.py`, cuyo SHA256
  actual es `3a3beae460a4e2398dba2549f8a92ab49222cbe6a8f6781ba01da99a73811c2a`.
  La global fallida se conserva; no se convierte retrospectivamente en verde.
- Runner: `reports/e5-runtime-dubai-control-20260921/run_verification.py`.

La revision independiente detecto y condujo a corregir la omision de cancelacion
en startup; tambien reviso la expectativa de alertas Dubai sin detectar un
debilitamiento material. No certifica el proceso entero ni todas las carreras.

Siguen abiertos recuperacion integral, contencion entre cestas, latencia y
procesos VM, costes/valoracion nativos y contraste historico independiente por
capacidad del dataset. No se habilita busqueda masiva ni despliegue automatico.
