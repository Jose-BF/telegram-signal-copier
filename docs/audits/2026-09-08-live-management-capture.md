# Captura Activa Para La Prueba Del 08/09

Nota de continuidad: `2026-09-08-end-session-causal-review.md` documenta el
cierre posterior de las 18 cestas aceptadas, la auditoria causal completa y los
limites de simulacion. Esta pagina conserva el estado historico de activacion.

## Estado

Publicado y verificado en la VM: fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd.
Captura activa desde 08:15:39 Madrid; conexiones Telegram/MT5 confirmadas
a las 08:15:56. La primera senal natural, Gold canal2_2590, llego a las
08:18:07. No hubo cambios de estrategia, parametros, lotes ni cuenta.

El despliegue se hizo con cuenta demo EUR comprobada, sin posiciones ni
ordenes pendientes y mediante el gate de pausa/drenaje del supervisor.
Configuracion y prefijos de registros preservados. Watcher 8436, bot 6052,
terminal 8968 en la misma sesion. No se enviaron ordenes artificiales.

## Verificacion

- 3639 pruebas locales aprobadas; 1040 en clon aislado de la VM con MT5 y
  conexiones externas bloqueados. Paquete de cinco archivos con hashes fijados.
- Dos copias inmutables: inicio 08:17:08 y primera senal 08:22:32 Madrid.
  Registro completo, contexto, ticks XAUUSD/EURUSD, deals, ordenes y metadatos.
- Primera muestra: 2890 decisiones de gestion, 2885 sin accion, cinco
  solicitudes y tres coalescencias. Cero errores de captura en esas decisiones.
- Las 5841 filas causales nuevas pasan el chequeo de linaje. El resultado de
  la primera entrada enlaza por IDs nativos con el deal/orden del broker.
- Al ultimo control, 08:25:47, seguia una senal activa, cero posiciones y cero
  entradas pendientes. Publicacion nativa correcta a las 08:25:47, sin cola.

## Limites

La muestra es parcial y no se declara terminada la cesta ni la jornada.
El informe de TODO el journal sigue bloqueado por evidencia historica:
39355 filas bloqueadas en la copia inicial, incluidas 27020 anteriores al
contrato causal. No se ocultaron ni eliminaron para aprobar la muestra nueva.

Captura y linaje comprobados no equivalen a reproducir independientemente la
politica completa, ni a concordancia exacta del simulador con todos los deals.
full_live_parity_verified y policy_motor_verified permanecen false. Los ticks
conservados son diagnosticos hasta generar/verificar sus contratos v3.

No hubo otra publicacion o reinicio tras recibir la senal. El control final
que exigia estar flat se detuvo al detectar la senal activa; se hizo despues
una observacion de estado, sin relajar el gate de despliegue.

## Evidencia

Directorio local ignorado: runtime_data/causal_capture_today_20260908/.
Resumen: publication_and_first_signal_final.json.
Pruebas: v2/local_final.json, v2/pytest_full.xml, v2/pytest_vm.xml.
Activacion: v2/before_final.json, v2/after_final1.json,
v2/active_first_signal.json. Fuentes: start_0617/ y first_0622/.
Auditorias integras: start_0617_global_capture_audit.json y
first_0622_global_capture_audit.json. Todos conservan hashes y fronteras.

La instrumentacion esta publicada. El auditor offline, este informe y los
estudios previos siguen locales; no se promovieron candidatos ni estudios.
