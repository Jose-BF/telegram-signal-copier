# Preparacion Del Contraste De La Manana

## Resultado

Preparacion local terminada y congelada a las **08:14:03 Madrid del 09/09/2026**,
antes de las 08:30 solicitadas. La ventana natural es 08:30-10:30 Madrid.
El bot estaba detenido en la ultima comprobacion de procesos (08:12:47).
Su arranque no esta autorizado por la peticion de preparar el simulador.

No se ha hecho commit, push, despliegue de bot, cambio de estrategia ni reinicio.
Solo se instalaron dos helpers de lectura fuera de su repositorio y se ejecutaron
dos capturas nativas. Las tareas temporales de captura terminaron y se retiraron.
No se enviaron ordenes de prueba.

## Motor Y Evidencia

- Solicitud, fill/rechazo y acuse de apertura/cierre separados en los tres motores.
  Las protecciones ya instaladas siguen activas durante las esperas del cliente.
- Regresiones de rechazos, cancelacion de escalonados, timestamps repetidos,
  ausencia de quotes, datos incompletos y presupuestos de eventos.
- Cartera: validacion de precio de entrada y TP limitado aunque haya salto de
  cotizacion; no valida margen, stop-out ni swap nocturno.
- Suite completa: **4.416 pruebas aprobadas**, cero fallos, errores u omisiones,
  229,596 segundos; 395 fuentes Python/configuracion y siete helpers estables.
  Fin: 2026-09-09T06:13:47.788320+00:00. Las 633 advertencias quedan en el log.
- Control retrospectivo nuevo: once Gold, un perfil, tres motores, **33 evaluaciones**.
  Todos los campos comparados concuerdan; cero bloqueos del motor. Conserva las
  limitaciones del archivo historico y no se presenta como evidencia futura.
- Revision independiente de los bordes del comparador completada sin hallazgos
  pendientes; 31 pruebas seleccionadas y 54 pruebas focalizadas del comparador.

Evidencia bajo `runtime_data/simulation_foundation_20260909/`:

| Artefacto | SHA-256 |
| --- | --- |
| market_verification_v1/final.json | 60f9f0a115ab17473ddd9b327a78555d7989000d0dc9fddd7857b70d78b7ebba |
| market_independent_v1/results.json | fe1e732ebc67ba8e13255d629a1e74003f45b0854cf19fcf484be1b31bbd3b11 |
| market_forward_v1/profile.json | 161a3e22b4fdefb0d2e16c739c246c561dfe2a42e0901f492b75fd1f74e0c7ed |
| market_forward_v1/forward/protocol.json | 1edab5799897073fedfc9bf911c35e167b9c0444f03e5958748ea8dfd9284c25 |

Identidad de implementacion:
9e234cbe57936b797119dd944226c4513827ce2cb9eaee066f5cf2be20344c1f.
El perfil conserva el Gold 555 original, volumen y condiciones del simbolo
leidos de MT5. Retrasos hipoteticos fijos de 250 ms; reintento SL/TP de 1.000 ms.
No fueron ajustados para igualar los fills de la cohorte futura.

## Recorrido Preparado

Captura nativa de lectura -> dataset de mensajes/Bid/Ask -> tres motores
independientes -> cartera del control -> contraste factual posterior.
Los adaptadores estan ligados al freeze, al colector, la cuenta y el commit.
La evidencia observada se abre solo despues de verificar la ejecucion independiente.

Una lectura `positions_get` coherente y ligada a solicitud/intento/revision puede
verificar estado de SL/TP. No demuestra el instante de instalacion en el servidor.
Si lectura y cierre se solapan temporalmente, queda cobertura incompleta; una
contradiccion real bloquea. No se descartan senales ni se compensan diferencias.
Los cierres de posiciones externas previas se identifican antes de excluirlos.

## Estado Operativo Y Limites

VM comprobada: main fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd, checkout limpio.
MT5 existente: PID 8968, sesion 2, cuenta demo EUR. Las capturas de 07:48 y 07:55
Madrid mostraron cero posiciones y pendientes; no autorizan asumir flat despues.
Heartbeat detenido desde 23:47:54 UTC del 08/09 y sin proceso Python a las 06:12:47
UTC del 09/09. El log apunta a pausa del sistema y recuperacion bloqueada por la
verificacion del codigo. No se ha saltado esa proteccion.

Cliente serial y reloj de quotes, no modelo universal de MT5. Sin fills parciales,
requotes, concurrencia real, freeze no nulo, margen o stop-out. Comision cero
hipotetica y sin rollover. Sin tolerancias cuantitativas aprobadas ni certificado
de fidelidad integral. Cero busquedas de candidatas y ninguna promocion automatica.

Seguimiento preparado en esta tarea a las 08:30, 09:30 y 10:30 Madrid, limitado
al 09/09. Sin bot recuperado o sin suficientes operaciones, se documentara la
limitacion; no se inventaran casos ni se ampliara la ventana. Procedimiento en
`../development/2026-09-09-morning-forward-runbook.md`.
