# Captura Natural Del Jueves

ESTADO VIGENTE 11/09 07:40 Madrid: las capturas de hoy14/17 fueron canceladas
por aclaracion del usuario, antes de ejecutarse. Seguimiento Codex eliminado.
Recibo `forward_friday_v3/cancelled_by_user.json`; no recrear ni compensar con
otra programacion. Bot no tocado. Las secciones siguientes son antecedentes.

## Estado Vigente Del Viernes

Seguir primero `../audits/2026-09-11-operational-release.md`: actualizacion
operativa autorizada, d9037c5c activo, bot3068 creacion1789103248315, mismo
terminal6312. Viernes v3 2b41be5f91d3 sustituye v2 antes de la ventana;
fuentes y reglas intactas. Tareas propias nuevas Ready para12UTC/14Madrid
y15UTC/17Madrid, con sufijo2b41be5f91d3, recibos en `forward_friday_v3/`.
Carpeta VM `C:/Users/bot/codex-control/simulator_friday_20260911_2b41be5f91d3`.
V2 retirado sin ejecucion, no recrearlo. Mantener ancla jueves f9cf, verificar
pre exitoso antes de final, sin fallback. Capturas aun no ejecutadas al cierre
05:22UTC; no confundir Ready con evidencia recogida. Resto: historia operativa.

## Estado Anterior Al Cierre

Seguir primero `../audits/2026-09-10-night-review.md`: las capturas del jueves
ya se recuperaron, verificaron y sus tareas propias terminadas se retiraron.
No repetirlas. La prueba independiente 18-20 quedo incompleta y el comparador
bloqueado por cinco fuentes sin registro explicito; conservar ambos hechos.
Viernes v1 esta sustituido por v2 5d882fdfc67b, con todos los productores
comprobados antes de la ventana Sep11 13-15 UTC. Dos tareas propias Ready,
precaptura12UTC/14Madrid y final15UTC/17Madrid, registradas en Windows y
verificadas con XML/propiedades/hash; recibos en `forward_friday_v2/`.
La final requiere manifiesto, archivos y recibo exitoso de pre; no retroceder
al ancla antigua ni saltar delta si falla. Bot/terminal originales, 892 limpio.
No hubo despliegue ni reinicio. La seccion siguiente conserva historia operativa.

## Protocolo Y Limites

Ventana Sep10 13:00-15:00 UTC, equivalente a 15:00-17:00 Europe/Madrid.
Protocolo congelado antes de resultados:
`runtime_data/calculator_delivery_20260910/forward_thursday_v2/frozen/protocol.json`.
SHA-256 `aedd750e4e58254fc147a7a24ab0c0814ab20e98d7f4eb620184468ace63fafb`.
Fingerprint `0b144f2acc006a884736326d7460315e6cda664da19f236abbc43937a117c6e7`.

El freeze verifica `verification_v3`: 5043 PASS, 424 fuentes/wrappers estables y
codigo `12e0720c967c5a32bfb6bd639352594a20788c64c8db289d88642c0eff60ec3a`.
No editar ese nucleo/pruebas antes del contraste sin conservar una copia
ejecutable exacta y nueva verificacion. Un hash solo no reconstruye el codigo.
Cambios de modelo requieren otro experimento, no retimar/reusar este freeze.

Solo operaciones naturales. Maximo dos horas, mismas restricciones y
frescura; tolerancias aprobadas null, paridad total false, busqueda cero.
No reusar el freeze Sep9 ni forzar operaciones para completar la muestra.

## Archivos Auxiliares En VM

VM `bot@192.168.0.48`, carpeta fuera del checkout del bot:
`C:/Users/bot/codex-control/simulator_thursday_20260910_aedd750e4e58/`.
Copias verificadas por SHA, sin commit, push ni despliegue del repositorio:

| Archivo | SHA-256 |
| --- | --- |
| collect_simulator_window.py | 73c5e7013846c3c45026a18465ece8909a801660753b2cb805777ad51a0586e8 |
| collect_simulator_window.ps1 | 0bfcf65dcee868af5b80a9b3ba5094d6ca4f9453e15ff289f7d9bab799876ea6 |
| protocol.json | aedd750e4e58254fc147a7a24ab0c0814ab20e98d7f4eb620184468ace63fafb |
| register_capture_task.ps1 | e2a0a99acb74405fcfbaddced9700cd543fcde7496ea2ccea298be4d999c21dc |

La carpeta anterior `simulator_thursday_20260910_07636f32757d` conserva el
protocolo/launcher originales y el instalador fallido. La tarea por demanda no tiene
NextRunTime: corregido el serializador que intentaba convertir null. El
rollback elimino la tarea incompleta; la lectura posterior confirmo ausencia
de tarea y logs de captura. La v2 se registro y lanzo correctamente.
El script se ejecuta con politica de proceso explicita; no se cambio la
politica permanente de la maquina, Windows Update o las protecciones.

Terminal esperado en el registro: PID 6312, sesion interactiva 2, ruta
`C:/Program Files/MetaTrader 5/terminal64.exe`. No asumir que siga siendo el
mismo al revisar: el wrapper y colector lo vuelven a comprobar antes/despues.
Cuenta y commit esperados quedan ligados por el protocolo, no impresos como
credenciales. Checkout verificado 892 limpio; no sustituye prueba de version
cargada o exposicion reciente. Bot principal observado PID 5216 y journal con
polling a 10:48:16 UTC, sin reinicio realizado por este frente.

## Precaptura

Tarea propia registrada a 10:57:11 UTC:
`Codex-Simulator-20260910-1300-pre_window-07636f32757d`.
Interactive/Limited, sin trigger repetido; Start-ScheduledTask una sola vez.
Wrapper iniciado a 10:58:07 UTC. Prefijo de logs en `C:/Users/bot/codex-control/`:
`capture_simulator_operation_20260910t105807z_faa027e6ba6945769d29574f2a65a5a9`.
Sufijos `.started.json`, `.finished.json`, `.stdout.log`, `.stderr.log`.

Ancla anterior, conservada sin modificaciones:
`capture_today_20260909_sep9_check_20260909t081022z_609f8eea/manifest.json`
en la misma carpeta de control; SHA
`7dd9706bff680c7b4aeda2598f0d794a1e33c4e44e11cc3b36e254d88265f29f`.
Bootstrap medido superior a 512 MiB; solo esta fase declara 1 GiB, dos millones
de filas y 600 s. Checkpoint/final mantienen sus techos anteriores. Nunca
truncar fuentes ni saltar una seccion para fabricar un ancla posterior.

Precaptura completada: cutoff 10:58:18.027428 UTC, cierre de evidencia
10:59:58.231229 UTC, 654423104 bytes de delta, sin bloqueos. Posiciones y
pendientes nativos vacios a 10:59:52 UTC; terminal/cuenta conservados.
Carpeta VM `capture_simulator_20260910t130000z_20260910t150000z_pre_20260910t105814z_45ff222b`.
SHA manifiesto `4800383d4b07af5bcfc65ff0665e89a80397c7904f54e904aae92b09d504b911`.
ZIP `03257cb7834f9f89814e2b10738b76bf4978daf0eb60b356d2bffd0b11968961`;
descargado en `forward_thursday_v1/`, extraido en `pre_capture/` tras validar
rutas/tamanos y los diez archivos contra el manifiesto. Ninguna captura parcial
se ha convertido en evidencia. Tarea previa retirada a 11:17:28 UTC.

El recibo del wrapper original registra ExitCode null pese a la captura valida.
Se reproduce el problema de PowerShell con proceso redirigido que sale 7:
sin retener handle devuelve null, con handle devuelve 7. Corregido el wrapper
y agregado test del bloque real para 0/7. Nueva bateria completa 5043 PASS y
freeze v2 antes de resultados; las copias originales quedan conservadas,
incluido `forward_thursday_v1/retained_collect_simulator_window.ps1` (SHA ccd462...).
La evidencia del colector valida la precaptura, no el codigo 0 de aquella tarea.

## Final Registrada

`Codex-Simulator-20260910-1300-final-aedd750e4e58`, registrada a 11:19:10 UTC,
estado Ready; proxima ejecucion verificada 17:00 Madrid / 15:00 UTC. Principal
Interactive/Limited, una sola ejecucion, sin StartWhenAvailable, expiracion
17:02 local. Ancla: manifiesto 480038... de la precaptura validada.

Recibo descargado y comprobado:
`forward_thursday_v2/Codex-Simulator-20260910-1300-final-aedd750e4e58.registered.json`.
SHA-256 `32bf55016cfe8a647d9d98c7aa84d62c19711f0ef56aa0dc7c2b90290c118f7b`.
No ejecutar Start-ScheduledTask sobre esta final antes de hora ni registrar
otra copia. La captura futura todavia no ha sucedido y puede quedar bloqueada.

## Avisos Operativos

Una consulta auxiliar a logs redirigidos crecio en memoria y no respondio.
Se identifico por PID y texto exacto de nuestro comando, se detuvo solo ese
PowerShell de lectura (1764), y se cerraron las sesiones de consulta. No se
detuvo Python del bot ni MT5. Para logs de operacion comprobar Length y usar
lecturas limitadas (hasta 1 MiB); no volver a usar Get-Content -Tail sobre esos
logs vacios. Journal grande: como maximo 65536 bytes mediante FileStream,
descartar primera linea parcial y analizar el resto. No leerlo entero en memoria.

Control posterior 11:14 UTC: Python principal 5216 y terminal 6312 conservan
hora de arranque; polling reciente 11:13:33 UTC. Hubo avisos de captura monetaria
`live_tick_value_validation_failed` a 10:51 y 11:07, con recuperacion 11:02 y
11:12. No declararlos ausentes ni desplegar/reiniciar por esos avisos recuperados;
comprobar recurrencia y evidencia monetaria en la siguiente revision.

## Secuencia De Revision

1. A las 14:00 Madrid comprobar salud real del bot, recibo y precaptura, fuentes
   inmutables y tarea de cierre. No desplegar investigacion ni provocar ordenes.
2. La captura final debe empezar al cierre de las 17:00 Madrid. Se registra
   como unica ejecucion, sin StartWhenAvailable, expiracion a 17:02. El limite
   de ejecucion de 11 minutos no amplia los 120 s de frescura del consumidor.
3. Conservar fuente y ZIP con hashes; comprobar ruta, tamanos y archivos antes
   de extraer localmente. Nunca consumir una carpeta parcial sin manifest.
4. Producir dataset e informe independiente desde mensajes/precios/metadata;
   consultar resultados MT5 solo despues para contraste. Conservar todos los
   bloqueos y no presentar un checkpoint incompleto como final.
5. A las 19:00 Madrid revisar resultado/comparacion, clasificar discrepancias
   sin calibracion retrospectiva disfrazada y retirar solo tareas propias ya
   terminadas. Si una captura fallo o no hubo operaciones suficientes, informarlo.
6. Para viernes fijar protocolo nuevo antes de su propia ventana, con pruebas
   pertinentes actuales. No retimar jueves. No hay permiso nuevo de despliegue
   el viernes, ni autorizacion de busqueda masiva por completar una captura.

Usar `tools/run_simulator_forward.py` y `tools/compare_simulator_forward.py`
con sus contratos actuales; el productor no lee resultados observados para
generar entradas. Revalidacion de fuentes, ventana, cuenta y protocolo sigue
obligatoria. Este documento operativo no convierte una hipotesis en evidencia.

## Revision Real De Las 15:15 Y Nuevo Ancla

La revision prevista de las 14 no se ejecuto; la revision real empezo a las
15:10 Madrid. Ver `../audits/2026-09-10-collection-review-1515.md` para alcance,
datos, reparaciones, pruebas y limites. Checkpoint terminado correctamente:
manifiesto `30a048fc9544de53110763bc1b907f9ef9887348fa4adab60e199dce5d74da2a`,
predecesor `4800383d4b07af5bcfc65ff0665e89a80397c7904f54e904aae92b09d504b911`.
La tarea final existente se encadeno a ese checkpoint, sin cambiar las 17:00,
el protocolo aedd750e4e58, fuentes, presupuesto o limites, ni saltar bytes.
Recibo: `runtime_data/day_review_20260910/review_1515/final_capture_reanchor.json`.

Las dos tareas propias completadas de checkpoint/precios suplementarios se
retiraron tras verificar salida cero y manifiestos; recibos conservados.
No se reiniciaron bot ni terminal. Ultima VM comprobada: 15:36:36 Madrid,
`892bc33c8` limpio. No usar ese heartbeat como permiso futuro para reiniciar.

Antes de reparar tres defectos se preservaron las 424 fuentes originales,
dos wrappers y evidencia en una copia ejecutable verificada:
`runtime_data/day_review_20260910/review_1515/frozen_original/verified_sources.zip`,
SHA256 `1277ab4b447c94887af0e01770faf26fd79b20d5c4ebd0393a46d9e91b86da62`.
Las reparaciones siguen locales: 5093 pruebas aprobadas, 425 fuentes estables.
No se copio el recolector nuevo a la VM. El protocolo anterior conserva sus
23 falsos conflictos heredados y la ruta local mutable ya no prueba el codigo
anterior: no admitirlo silenciosamente usando el nuevo arbol. Conservar esos
bloqueos y preparar un nuevo freeze futuro para evaluar el recolector reparado.
La captura de las 17 aporta fuentes, no una certificacion automatica.

Seguimiento posterior real de las 16:00: final Ready confirmada a 14:01 UTC,
misma ancla y hora 15:00 UTC. Nuevo protocolo LOCAL de viernes 13:00-15:00 UTC
en `runtime_data/calculator_delivery_20260910/forward_friday_v1/`, SHA256
`6d86d44e5234bce042aec9a3bda599333f81500d0b38c093111aeff2e847b3c0`.
Tiene recolector reparado, 425 fuentes verificadas y copia ejecutable retenida;
NO tiene captura programada todavia. Completar la preparacion operativa en el
siguiente seguimiento del jueves, una vez recuperada la final, sin modificar
la tarea actual. Detalle y limites: `../audits/2026-09-10-followup-1600.md`.

Actualizacion 17:45 Madrid: final de jueves 15-17 ya completada, descargada y
verificada, no admitida por sus conflictos retenidos. Usuario pide dejar bot
operando y preparar mediciones adicionales hoy. Nueva ventana 18-20 congelada
antes de inicio en `forward_evening_v1`, protocolo 16708c87965a; final propia
Ready20:00 y precios del intervalo17-18 Ready18:01. La precaptura17:37 esta
verificada y es el ancla c71ca29f; la final anterior543eaf24 es su predecesor.
Se retiraron solo esas dos tareas ya completadas, con recibos preservados.
Detalles, rutas, hashes y limites en `../audits/2026-09-10-evening-measurement-ready.md`.
No repetir la recuperacion de las17 ni registrar una segunda final20. Captura
de viernes aun pendiente de programar despues de comprobar este nuevo cierre.
