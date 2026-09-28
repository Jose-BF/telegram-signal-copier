# E3: Integracion Del Acceso A MT5

Fecha: 20/09/2026. Diseno Astra tras el
[cierre de revision offline E1-E2](2026-09-20-e1-e2-sixth-repair-review.md).
Implementacion local autorizada dentro del proyecto; siguiente entrega con
Sol/alto, tras el cambio de modelo acordado. Ninguna entrega parcial activa
el trabajador en produccion. Se preservan estrategias, volumen, SL/TP, BE,
DCA, vencimientos, entradas por rango y procedencia de cada decision.

## Resultado Buscado

Una llamada nativa lenta deja disponible el bucle de Telegram y la supervision.
Las ordenes ambiguas conservan identidad y reserva, se concilian y no se
reenvian por timeout. El estado real y el simulado podran compararse con
entradas y resultados explicitos; este cambio no certifica aun su equivalencia.
Un trabajador bloqueado sigue sin poder ejecutar otras llamadas hasta volver
o recuperarse: publicar por separado vida del bot y disponibilidad del broker.

## Mapa Contrastado

| Modulo | Integracion necesaria |
| --- | --- |
| executor.py | Separar calculos puros, operaciones compuestas y acceso nativo; aperturas, limites, SL/TP, cierre, cancelacion y consultas |
| listener.py:2117 | Sustituir _run/thread pool para operaciones migradas por await al cliente; conservar identidad causal y rutas de ambos canales |
| pending_actions.py:1287 | Consumir estados tipados y revision; no convertir UNKNOWN/PLACED/parcial en RETRY ni DONE |
| mt5_errors.py | Clasificacion pura y consulta de stops mediante el trabajador; corregir 10011/10013 como ausencia y 10008 como reintento |
| entry_execution_gate.py | Mantener cache local subordinada a reservas durables por raiz/generacion/pata |
| state.py | Estado en memoria reconstruible; resultado y efectos durables antes del acuse |
| main.py:330 | Heartbeat desde snapshot fechado, sin consulta nativa durante su escritura |
| main.py:1157 | Conexion, arranque y shutdown mediante cliente; last_error capturado junto a la llamada |
| position_lifecycle_monitor.py, live_auditor.py | Consultas y gestion asincronas, None distinto de vacio conocido |
| market_context.py, alert_graphics.py | Lecturas auxiliares acotadas fuera del camino critico de apertura |

El inventario inicial tambien encuentra importaciones en reconcile_mt5_ledger.py
y mt5_tester_replay.py: clasificar su alcance por flujo de arranque, no confundir
herramientas offline con permisos para acceso directo en el proceso live.
Incluir aliases como executor.mt5, _mt5 y funciones importadas; la busqueda textual
anterior es orientativa, no sustituye el control de importacion/call graph.

## E3-A: Cliente Y Trabajador Sin Activacion

Primera entrega acotada para Sol. Crear mt5_worker.py y mt5_client.py; extender
mt5_protocol.py y mt5_gateway.py solo para los contratos necesarios. No importar
executor/listener/main/state/journal en el hijo: sus efectos de arranque no
pertenecen al trabajador. Importacion nativa diferida y backend inyectable en
pruebas; ningun ensayo local debe iniciar MT5 por accidente.

1. Definir operaciones permitidas, solicitudes/respuestas serializables y
   version de protocolo. Separar consultas repetibles de efectos de trading:
   las lecturas no deben convertirse en intenciones de compra/cierre durables.
2. Implementar inicializacion, verificacion de cuenta/simbolo, shutdown y
   consultas de tick, posiciones, ordenes, cuenta/terminal/simbolo, historial
   acotado y calculos. Capturar last_error dentro del trabajador inmediatamente.
3. Cliente async con plazos, cancelacion, cola y tamano de respuesta acotados;
   no esperas sincronas del bucle ni sustitucion silenciosa por llamada directa.
   Preservar tiempo/proceso/sesion, frescura y estado FOUND/EMPTY/UNKNOWN.
4. Crear inventario ejecutable de accesos nativos y baseline temporal explicita.
   Las rutas legacy siguen visibles como pendientes hasta migrarse; no se
   aprueba E3 mientras queden accesos live fuera del propietario.

Cierre E3-A: pruebas con backend doble verifican serializacion, consulta fallida
frente a vacia, cuenta equivocada, peticion no permitida, limites, timeout,
cancelacion, respuesta tardia y continuidad del bucle. Prueba de importacion
sin dependencia nativa en el padre. Las pruebas E1-E2 permanecen verdes.
Ensayo nativo GIL de 60 s y compatibilidad Python 3.11 se preparan/ejecutan con
los recursos locales disponibles; registrar cualquier entorno faltante sin
declararlo validado. No crear nuevas llamadas live ni habilitar flags.

## E3-B: Operaciones Y Aplicacion Durable

Tras revisar E3-A, migrar las operaciones compuestas de executor y sus
consumidores en listener/pending_actions. Tick, validacion y preparacion de
precio/stop monetario permanecen juntos en el trabajador cuando requieren
varias llamadas; evitar viajes IPC por cada iteracion del calculo.

- Persistir identidad, revision de politica, payload y reserva antes de enviar.
  Una actualizacion de SL/TP crea revision; no modifica la peticion ya enviada.
- UNKNOWN, PLACED y DONE_PARTIAL requieren conciliacion; no liberar reserva ni
  reenviar por un None/timeout/excepcion. Preservar tickets, volumen y precio.
- Un rechazo conocido solo permite otro intento si la politica lo admite.
  Disenar la transicion durable de reintento que hoy no ofrece el prototipo:
  misma intencion/payload, nuevo intento, motivo probado; payload distinto
  requiere revision. Nunca fabricar intencion nueva para saltarse UNKNOWN.
- Guardar una proyeccion durable de efectos de resultado y su revision en la
  misma transaccion que su acuse. Reconstruir el estado en memoria desde esa
  proyeccion. mark_applied por si solo no hace atomica una mutacion de Signal.
  No realizar llamadas broker dentro de esa transaccion. Publicacion de eventos
  con identidad idempotente y reentrega; no prometer exactly-once de Telegram.
- Revalidar magic, cuenta, simbolo, generacion y vigencia antes de dispatch.
  Una proteccion requerida que no puede calcularse impide esa apertura; las
  politicas con instalacion diferida explicita conservan su contrato.

Cierre E3-B: caida antes/despues de cada escritura/envio/aplicacion; redelivery,
parcial, aceptacion pendiente, consulta desconocida, fallo de disco, revision
superada y reinspeccion de resultados ya confirmados. Ningun duplicado de
exposicion; mismo volumen y decisiones que las politicas vigentes en fixtures
causales de ambos canales. Una orden hipotetica no se valora con fills reales
de otra operacion.

## E3-C: Rutas Restantes Y Supervision

Migrar arranque, monitores, auditor, lecturas auxiliares y capturas live al
cliente. Constantes/calculos puros no necesitan importar el modulo nativo en
el padre. Snapshot incluye fuente, hora, sesion y edad; vencido/fallido significa
unknown, nunca cuenta plana. Ajustar contrato del heartbeat y sus lectores
(watcher incluido) conjuntamente; el heartbeat no espera MT5.

El trabajador operativo atiende gestion identificada y lecturas esenciales con
prioridad/fairness explicitadas y reproducibles. Las capturas grandes y sombras
tienen limites para no monopolizarlo. Si se usa un recolector separado, su
propiedad, acceso de solo lectura y contencion se prueban antes de activarlo.

Cierre E3: cero accesos nativos live fuera del trabajador, verificado por AST,
importacion y ejecucion con backend que detecte accesos indebidos. Suite global,
regresiones de ambos canales, rutas de reinicio y capturas conservadas. Revisar
con Astra antes de cualquier publicacion; no activar un modo mixto accidental.

## Pruebas Operativas Y Continuidad Del Proyecto

Mantener los criterios de la arquitectura: prueba nativa que retiene GIL 60 s,
objetivo local p99 <=100 ms y max <=1 s del bucle; carga 60 min a dos veces el
pico medido, tasas/colas/memoria/plazos registrados. Python 3.11/Windows y
capacidad comparable a la VM tienen evidencia propia; un sleep no sustituye
la prueba nativa. E4 completa propietario unico ante proceso huerfano,
disco lento/lleno, watchdog, prioridad y retencion. Nada se activa sin esos
requisitos aplicables y exposicion conocida/conciliada.

Despues E5 compara ejecucion y riesgo en muestra amplia de ambos canales:
entradas, volumen, protecciones, salidas, flotante, MAE/MFE y drawdown. Reutilizar
dias completos disponibles y mantener huecos de captura visibles. 3086 y el
28/08 son controles, no toda la muestra. Solo entonces retomar comparacion
economica de estrategias con las limitaciones de datos que correspondan.

## Seguimiento Y Modelo

- [x] Revision final de la frontera F3 y aceptacion del prototipo offline.
- [x] Mapa inicial y contratos de integracion contrastados con el codigo.
- [x] E3-A: cliente, consultas y trabajador con backend doble implementados localmente.
- [x] E3-A: 6.080 pruebas y ensayo nativo local de 60 s; Python 3.11 pendiente.
- [x] E3-A: [revision](2026-09-20-e3-a-review.md) con cuatro hallazgos reproducidos.
- [x] E3-A: [F1-F4 reparados](2026-09-20-e3-a-repair-results.md) y verificados
  localmente con 6.087 pruebas y ensayo nativo final de 60 s.
- [x] E3-A: [revision de reparacion](2026-09-20-e3-a-repair-review.md), con
  segunda lectura independiente de worker/protocolo; F1-F4 originales confirmados.
- [x] E3-A: R1 (cierre durante arranque) y R2 (stdout nativo) reproducidos y
  especificados antes de modificar la implementacion.
- [x] E3-A: [R1-R2 reparados](2026-09-20-e3-a-second-repair-results.md), 6.091
  pruebas globales, ensayo nativo final y segunda lectura sin hallazgos materiales.
- [x] E3-A: [revision formal aceptada](2026-09-20-e3-a-acceptance.md), seis
  controles adicionales sin nuevos hallazgos materiales; base offline aceptada.
- [x] E3-B: escrituras, reservas y aplicacion durable;
  [aceptacion local](2026-09-20-e3-b-acceptance.md) con R1-R2 cerrados.
- [ ] E3-C: resto de rutas, supervision y verificacion de conjunto.
- [ ] Ensayos operativos E2/E4, E5 y despliegue autorizado.

E3-A implementado con Sol/alto; [entrega y evidencia](2026-09-20-e3-a-implementation.md)
verificada localmente. La revision reprodujo cuatro defectos y la
[reparacion F1-F4](2026-09-20-e3-a-repair-results.md) esta terminada con nuevas
regresiones y evidencia global. La revision posterior confirmo los casos
originales y reprodujo R1-R2; la segunda reparacion los cierra localmente con
regresiones y segunda lectura independiente. La revision formal acepta E3-A
como base offline. Siguiente modelo: Sol/alto para implementar E3-B segun sus
contratos anteriores, sin activacion live. E3-B aun no implementado ni aceptado.
No cambiar modelo por cada prueba. El cambio de modelo solicitado
es coordinacion del flujo acordado, no solicitud nueva de permiso para analisis
o ediciones locales. No hay decisiones de usuario pendientes para continuar E3-B.

La [candidata de implementacion E3-B](2026-09-20-e3-b-implementation.md) esta
terminada localmente y conserva la casilla abierta. La
[revision Astra/alto](2026-09-20-e3-b-review.md) reprodujo tres hallazgos F1-F3;
la [reparacion Sol/alto](2026-09-20-e3-b-repair-results.md) supera 6.161 pruebas
originales. La [revision posterior](2026-09-20-e3-b-repair-review.md) confirma
los casos originales y reproduce R1-R2 con cuatro regresiones nuevas fallidas.
La [segunda reparacion Sol/alto](2026-09-20-e3-b-second-repair-results.md)
corrige la recuperacion multirrevision, la reentrega terminal y el descarte de
politicas sustituidas; supera 6.169 pruebas. La
[revision Astra/alto](2026-09-20-e3-b-acceptance.md) cierra R1-R2 y acepta E3-B
como base offline; 190 pruebas dirigidas con ocho controles nuevos. Registra
dos pruebas sensibles al plazo previo al envio, que deben sincronizarse en
E3-C. Siguiente paso: Sol/alto para E3-C. E3-B no esta instalada por `main.py`
ni publicada; la aceptacion local no acredita paridad ni funcionamiento VM.

La [candidata local E3-C](2026-09-20-e3-c-implementation.md) integra el
propietario unico en arranque, monitor, supervision y sombras; sincroniza los
dos controles temporales y supera 6.196 pruebas globales. La casilla permanece
abierta hasta revision independiente. Siguiente modelo: Astra/alto para revisar
la candidata, sin publicar ni activar. E4, E5 y el despliegue autorizado siguen
pendientes.

La [revision E3-C](2026-09-20-e3-c-review.md) no acepta la candidata: F1-F7
documentados, seis controles nuevos fallidos y 132 controles previos aprobados
en el conjunto dirigido. El codigo operativo se conserva. Siguiente modelo:
Sol/alto para reparar snapshots, reconexion, recuperacion/aplicacion tardia,
caducidad y revisiones de patas, precision temporal y simbolos de conversion.
Agregar regresion integral de F3 antes de repararla; repetir suite global tras
las correcciones y volver a revision Astra/alto. No hay autorizacion de despliegue.

La [reparacion local E3-C F1-F7](2026-09-20-e3-c-repair-results.md) cierra los
controles reproducidos, agrega timeout interno y recuperacion tardia integral,
supera 267 pruebas dirigidas y 6.210 pruebas globales. La casilla E3-C permanece
abierta hasta revision independiente. Siguiente modelo: Astra/alto para revisar
la reparacion sin publicar ni activar; E4, E5 y despliegue siguen pendientes.

La [revision de reparacion E3-C](2026-09-20-e3-c-repair-review.md) no acepta
todavia el bloque. Los siete controles originales pasan, pero F3 sigue abierta
al reiniciar y al completar patas tardias sin un nuevo cruce de precio. R1-R4
incluyen tambien conflictos de payload ocultados, snapshots sin limite y
mezclas de reloj que eluden el maximo historico. Evidencia: 157 controles
existentes aprobados y once contrapruebas nuevas fallidas; son fallos locales,
no una medicion de incidencias en VM. Siguiente modelo: Sol/alto para reparar
el conjunto y repetir suite global; despues revision Astra/alto. Codigo
operativo intacto en esta revision, sin commit/push/despliegue. E3-C sigue abierta.

La [segunda reparacion local E3-C](2026-09-20-e3-c-second-repair-results.md)
cierra las once contrapruebas R1-R4 y supera 6.224 pruebas globales. Recupera
DONE tardios de primera entrada y patas de ambos canales, valida campos
congelados, acota snapshots y rechaza relojes mezclados. La casilla E3-C sigue
abierta: siguiente modelo Astra/alto para revision independiente. Despues,
si se acepta, E4 debe probar Python 3.11/Windows comparable a VM, incluidos
DISPATCHING/UNKNOWN tras muerte completa, bloqueo nativo, carga, disco y
procesos huerfanos. Sin commit, push, despliegue ni verificacion live.

La [revision de la segunda reparacion E3-C](2026-09-20-e3-c-second-repair-review.md)
mantiene E3-C abierta: 106 controles aprobados y diez regresiones nuevas
fallidas. F1-F5 reproducen retroceso de stops mejorados, perdida de cierres
solicitados, aplicacion parcial sin reintento, recuperacion desde otra cuenta
y cancelacion que descarta gestion pendiente. R2-R4 anteriores no presentan
nuevos hallazgos materiales en el alcance revisado. Siguiente bloque: completar
la aplicacion recuperable de primera entrada y patas como contrato comun,
con matriz de cortes y gestion existente; no parches por caso aislado.
Implementacion Sol/alto, seguida de revision del bloque completo Astra/alto.
Solo pruebas y documentacion modificadas en esta revision, sin tocar runtime
ni publicar. E4, E5 y autorizacion de despliegue siguen pendientes.

La [tercera reparacion local E3-C](2026-09-20-e3-c-third-repair-results.md)
cierra F1-F5 en la implementacion: filtra cuenta/posicion, conserva cierres y
protecciones pendientes, reintenta aplicacion parcial en primera entrada y
patas de ambos canales, y nunca sustituye un stop por otro menos protector.
La matriz E3 ampliada pasa 134 controles y la bateria global final supera
6.243 pruebas. E3-C sigue abierta hasta revision Astra/alto del bloque
completo. Sin commit, push, despliegue, reinicio de VM ni orden real; E4 y E5
siguen pendientes.

La [revision Astra/alto de la tercera reparacion](2026-09-20-e3-c-third-repair-review.md)
mantiene E3-C abierta: 121 controles dirigidos pasan y cuatro contrapruebas
nuevas fallan. Quedan por cerrar cancelacion durante dispatch de patas, cierre
durante su preparacion asincrona, preservacion del stop pendiente mas protector
en Dubai y validacion del DONE contra la fila exacta del plan congelado.
Siguiente modelo: Sol/alto para una reparacion acotada; despues, una revision
final Astra/alto. No hay commit, push, despliegue ni activacion live.

La [cuarta reparacion local E3-C](2026-09-20-e3-c-fourth-repair-results.md)
cierra esas cuatro condiciones y agrega un control del cierre en el ultimo
instante previo al commit. El conjunto dirigido pasa 152 controles y la suite
global 6.248. E3-C sigue abierta hasta revision final Astra/alto. Si se acepta,
el paso siguiente es E4 operativo en Windows/Python 3.11 comparable a la VM.
Sin commit, push, despliegue, reinicio ni orden real.

Continuacion nocturna autorizada sin nuevos cambios de modelo:
[reparaciones y ensayos locales](2026-09-20-night-recovery-validation.md).
Cuatro fronteras adicionales reproducidas y corregidas en recuperacion,
disparador y protecciones. E3-C no se declara aceptada en conjunto: queda
revision del arranque completo. E4 avanza con bloqueo nativo de 60 s tambien
en Python 3.11 y carga de lecturas acotada, sin sustituir sus ensayos pendientes
de propietario huerfano, disco, prioridades y carga equivalente a VM.

Continuacion 21/09 local: [integracion de barrera de arranque, propiedad,
persistencia y supervision](2026-09-21-runtime-hardening-continuation.md).
Este estado prevalece sobre las proximas acciones historicas anteriores.
Sin nuevo cambio de modelo, publicacion ni certificacion conjunta E3/E4/E5.
