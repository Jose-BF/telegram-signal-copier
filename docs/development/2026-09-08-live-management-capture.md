# Captura De Gestion Para La Prueba De Hoy

Continuacion: `../audits/2026-09-08-end-session-causal-review.md` cierra la
cohorte observada hasta las 21:19 Madrid y fija el siguiente trabajo sin cambiar
la politica viva. Este documento conserva el protocolo previo a las senales.

Objetivo autorizado: preparar y activar la captura de decisiones antes de las
proximas senales naturales del 08/09, dejando un inicio verificable para la
prueba. La cuenta demo, parametros y acciones comerciales actuales se conservan.

Se reutilizan los IDs de mensajes, decisiones, acciones e intentos existentes.
Cada evaluacion instrumentada retendra inputs, estado anterior/posterior,
resultado y acciones declaradas, incluyendo decisiones sin accion y errores.
Los registros emplean el journal asincrono existente; no ejecutan simulaciones
ni hacen consultas adicionales al broker en la ruta de gestion.

Alcance inicial: trailing, proteccion por fill y guard de cesta Gold 555;
guard de cesta Dubai. La gestion del proveedor ya tiene contexto Telegram.
El informe de captura comprobara continuidad e identidad y mantendra separada
la validacion completa contra motores y deals, pendiente de operaciones nuevas.

- [x] Verificar codigo y localizar los huecos observados en la captura.
- [x] Instrumentar y reproducir inputs/resultado/acciones con pruebas de
  regresion, incluyendo excepciones, coalescencias y decisiones sin accion.
- [x] Preparar el informe offline y el protocolo de inicio de hoy.
- [x] Revision independiente, suite local y comprobacion aislada en la VM.
- [x] Publicacion acotada, con cuenta/exposicion verificadas y activacion
  gestionada por el supervisor existente; comprobar version y captura activas.

Presupuesto: cero candidatas nuevas y cero ordenes artificiales. Captura del
dia con fuentes originales retenidas. Una prueba sin nuevas operaciones queda
como preparada/en espera, nunca como validacion prospectiva completada.

## Protocolo Del 08/09

La cohorte del dia empieza a las 2026-09-07T22:00:00Z (00:00 Madrid).
La captura nueva solo es elegible a partir del live_strategy_contract de la
sesion que active fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd. Ese limite se
obtiene del registro observado, no de la hora del commit o del push.

Cada copia usa un destino nuevo, conserva el prefijo integro del journal y
sus hashes, contexto de arranque, ticks Bid/Ask XAUUSD/EURUSD con epoch bruto
y anclas del reloj, metadatos, deals, ordenes y posiciones. El recolector
se conecta solo al terminal existente, comprueba cuenta demo EUR e identidad,
y no envia ordenes. Los parquet iniciales son diagnosticos: la reproduccion
exacta exige generar/verificar los sidecars v3 con el mecanismo del proyecto.

El informe tools/audit_management_capture.py recibe el journal completo y la
frontera UTC para seleccionar decisiones, conservando la auditoria global de
linaje. Una discontinuidad se conserva como bloqueo; una cohorte sin decisiones
queda no_decisions_yet. Captura completa no equivale a concordancia de politica
ni de ejecucion. La comparacion posterior debe enlazar decisiones, solicitudes,
intentos y deals reales, ademas de reconciliar precios, volumen y costes EUR.

No se introducen senales sinteticas en el bot, candidatas nuevas ni cambios
de parametros. Al revisar las senales naturales se conserva cada caso faltante
o discrepante con su motivo. No se modifica la muestra para obtener acuerdo.

## Evidencia De Preparacion

Commit publicado: fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd. Cinco archivos:
main.py, position_lifecycle_monitor.py, management_decision_evidence.py y dos
archivos de pruebas. Herramienta offline y estudios previos permanecen locales.

Suite local v2: 3639 aprobadas, cero fallos/errores/omitidas, 165.187 s.
VM aislada Python 3.11.9: 1040 aprobadas, cero fallos, 229.82 s; MT5 y sockets
externos bloqueados. Se retienen los dos fallos iniciales de expectativas de
eventos, la correccion explicita de su orden y ambas ejecuciones completas.
El paquete probado no cambio durante la suite local; el publicado coincide
con el probado, salvo normalizacion de finales de linea documentada en VM.

Prueba de coste local: 1000 evaluaciones, 2000 registros duraderos; media
0.147 ms y p95 0.166 ms por captura. No es una medida de latencia en produccion.
Evidencias inmutables: runtime_data/causal_capture_today_20260908/, especialmente
v2/local_final.json, v2/pytest_full.xml, v2/preflight_result.json y v2/pytest_vm.xml.

## Activacion Y Primera Muestra

Activacion observada: 06:15:39.315 UTC, 08:15 Madrid; Telegram y MT5 confirmados
a las 06:15:56.844 UTC. Cuenta demo EUR sin posiciones ni pendientes antes del
push; watcher 8436, nuevo bot 6052, terminal 8968 y configuracion preservados.
Los prefijos de ambos registros se verificaron intactos. La comprobacion de
contratos Dubai, Gold y riesgo antes/despues no encontro cambios de politica.

Primera senal natural: canal2_2590 recibida a las 06:18:07.224 UTC. Copia inicial
con corte 06:17:08.593052 y segunda copia con corte 06:22:32.894836 UTC, ambas
sin errores y con prefijos integramente conservados. La segunda retiene 2890
decisiones de gestion, 2885 sin accion, cinco solicitudes y tres coalescencias;
cero decisiones bloqueadas o acciones declaradas pendientes. Sus 5841 filas
causales posteriores a la activacion pasan las comprobaciones de linaje.

El informe global sigue bloqueado por huecos e inconsistencias historicos;
no se retiraron filas ni incidentes. Esto NO certifica la secuencia completa
frente al motor ni una jornada completa. A las 06:25:47 UTC el bot conserva
una senal activa, cero posiciones y cero entradas pendientes. No se fuerza
otro reinicio. La publicacion nativa de registros sigue correcta y sin cola.

Resumen verificable: ../audits/2026-09-08-live-management-capture.md.
Prueba detallada: runtime_data/causal_capture_today_20260908/
publication_and_first_signal_final.json. El auditor de captura y los estudios
offline permanecen locales; los cinco archivos de instrumentacion estan
publicados y verificados en la VM.
