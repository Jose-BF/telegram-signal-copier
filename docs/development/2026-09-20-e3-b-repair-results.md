# E3-B: Reparacion F1-F3

Reparacion local Sol/alto del 20/09/2026 sobre la
[revision Astra](2026-09-20-e3-b-review.md). No activa E3 en `main.py`, no usa
MT5 real y no modifica estrategias, volumen ni niveles de trading.

## Cambios

- **F1, reconstruccion tras caida:** el cliente entrega `projection_key` y la
  politica de liberacion al ledger. Outcome, efecto reconstruible, acuse y
  liberacion aplicable se escriben ahora dentro de una sola transaccion. La
  aplicacion ya no realiza una segunda escritura si recibe ese acuse atomico.
- **F1, defensa historica:** el ledger enumera terminales antiguos sin
  proyeccion. `DurableEntryExecutor` recupera un `DONE` con ticket/precio como
  apertura confirmada, de modo que una base previa o interrumpida no oculta la
  exposicion al arrancar.
- **F2, rechazo conocido:** una apertura `REJECTED` se reconstruye como rechazo,
  sin ticket ni precio; solo `DONE` incompleto y estados ambiguos requieren
  conciliacion.
- **F3, reintentos:** `retry_not_before` se respeta tambien en la ruta durable.
  Un rechazo nuevo nunca provoca un segundo envio dentro del mismo tick; frozen
  y stops arman cooldown. Releer el mismo intento ambiguo ya no incrementa el
  contador de intentos de broker.

## Regresiones

`tests/test_e3_b_review_regressions.py` cubre los tres hallazgos y amplia F3 con
un rechazo 10029 y dos lecturas del mismo `UNKNOWN`. Una prueba adicional
inyecta un fallo si la aplicacion intenta proyectar despues del commit del
outcome; pasa porque la proyeccion ya quedo en la transaccion original.

- Revision dirigida: 5 aprobadas, 3 advertencias, 0,46 s.
- E3, gateway y consumidores afectados: 174 aprobadas, 88 advertencias, 22,83 s.
- Compilacion de los modulos y pruebas modificados: correcta.
- `git diff --check` sobre archivos versionados afectados: correcto; solo aviso
  informativo de conversion LF/CRLF en `pending_actions.py`.

## Verificacion Global

`python -m pytest -q`: **6.161 aprobadas**, cero fallos/errores/omitidas, 651
advertencias, 514,52 s en Windows / Python 3.14.2. Las advertencias incluyen
las deprecaciones y avisos ya visibles en la candidata, mas tres de los nuevos
fixtures de revision. No se declara compatibilidad Python 3.11 ni prueba MT5
real a partir de esta ejecucion.

## Estado

Los casos originales F1-F3 pasan localmente. La
[revision de reparacion](2026-09-20-e3-b-repair-review.md) verifica la atomicidad
pero reproduce R1-R2 en recuperacion multirrevision y reentrega tras liberar
reserva. Esos hallazgos se corrigen en la
[segunda reparacion](2026-09-20-e3-b-second-repair-results.md), que queda
pendiente de revision Astra/alto. E3-B no esta aceptada y E3-C no ha comenzado.
No hubo commit, push, despliegue, reinicio de VM ni orden real.
