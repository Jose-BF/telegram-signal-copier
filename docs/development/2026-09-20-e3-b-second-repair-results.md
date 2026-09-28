# E3-B: Segunda Reparacion R1-R2

Reparacion local Sol/alto del 20/09/2026 sobre los hallazgos de la
[revision de reparacion](2026-09-20-e3-b-repair-review.md). No activa E3 en
`main.py`, no usa MT5 real y no modifica estrategias, volumen ni niveles.

## Problemas Cerrados Localmente

- **R1, reconstruccion desde historia obsoleta:** el arranque ya no combina
  proyecciones historicas con el estado actual. El ledger entrega en una sola
  lectura coherente una identidad y un estado vigentes por intencion; la
  reconstruccion emite exactamente un resultado actual. Un UNKNOWN antiguo no
  oculta un DONE conciliado ni un nuevo DISPATCHING.
- **R1, politica sustituida:** las reservas `SUPERSEDED` se conservan para
  auditoria, pero se excluyen de la vista operativa de reconstruccion. Una
  revision antigua PREPARED no reaparece como trabajo pendiente tras reiniciar.
- **R2, reentrega terminal:** una reentrega exactamente identica puede releer
  DONE o REJECTED aunque la aplicacion atomica ya haya liberado su reserva. No
  readquiere la reserva ni vuelve a enviar al broker.
- **R2, limites conservados:** siguen fallando payload, identidad, clave,
  politica o caducidad incompatibles. Las reservas `SUPERSEDED` y los estados
  ambiguos no usan la excepcion terminal.

## Regresiones

`tests/test_e3_b_repair_review.py` cubre:

- UNKNOWN historico seguido de DONE, con y sin proyeccion actual.
- REJECTED historico seguido de reintento DISPATCHING.
- cierre confirmado releido mediante el cliente y proceso MT5 aislado, con un
  unico envio.
- respuesta tardia tras timeout y posterior reentrega, tambien con un envio.
- reapertura de SQLite, reentrega exacta y rechazo de payload o reserva
  incompatibles.
- revision PREPARED sustituida que no vuelve a reconstruirse.
- rollback atomico si SQLite falla antes del commit.

Resultados en Windows / Python 3.14.2:

- Regresiones y consumidores directos: **134 aprobadas**, 66 advertencias,
  9,37 s.
- E3-B, gateway y rutas afectadas: **182 aprobadas**, 88 advertencias, 20,74 s.
- Suite global: **6.169 aprobadas**, cero fallos/errores/omitidas, 651
  advertencias, 406,56 s.
- Compilacion de modulos y pruebas modificados: correcta.
- Control de espacios finales sobre los archivos afectados: correcto. Los
  archivos de E3-B siguen sin seguimiento y `git diff --check` no los incluye.

Las advertencias son las deprecaciones y avisos ya presentes en la candidata;
no aparecio una advertencia nueva material asociada a R1-R2.

## Estado

R1-R2 quedan cerrados localmente por Sol/alto con pruebas de comportamiento,
reinicio, IPC y persistencia. La candidata necesita una nueva revision
independiente Astra/alto antes de aceptar E3-B. E3-C, la validacion operativa en
entorno comparable y la paridad amplia realidad/simulacion siguen pendientes.
No hubo commit, push, despliegue, reinicio de VM ni orden real.

## Revision Posterior

La [aceptacion Astra/alto](2026-09-20-e3-b-acceptance.md) cierra R1-R2 y admite
E3-B como base offline para E3-C. Incluye ocho controles nuevos y documenta
dos pruebas de respuesta tardia sensibles al plazo de preparacion. El estado
pendiente anterior corresponde al momento de entrega de esta reparacion.
