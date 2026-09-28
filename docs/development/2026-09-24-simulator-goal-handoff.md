# Relevo del objetivo de simulacion fiable (24/09/2026)

Estado y plan vigentes desde el relevo a Claude (24/09): [estado-y-plan.md](estado-y-plan.md). La semana 14-18/09 no es representativa (log pesado y reinicios colgados): no calibrar ni concluir con ella.

El usuario prepara un cambio de asistente y quiere conservar este punto para
volver a el o seguir desde los avances posteriores. Ver
`2026-09-24-project-restore-point.md` para copia congelada y restauracion en
una carpeta nueva. `CLAUDE.md` es la entrada del siguiente asistente. Mantener
actualizadas las decisiones y evidencias de este relevo al continuar. Las
utilidades de copia nuevas no modifican el motor de simulacion.

La peticion amplia de Jose al siguiente asistente esta en
`2026-09-24-claude-transfer-prompt.md`. Complementa este estado tecnico y
expresa libertad para revisar el diagnostico, mejorar documentos y proponer
una ruta propia. El checkpoint remoto se trata por separado en
`2026-09-24-github-migration-checkpoint.md`; no confundir copia con despliegue.
El usuario autorizo despues publicar ese archivo privado independiente.
Sus commits no cambian la rama local ni publican los cambios en el remoto
de produccion; las afirmaciones de no despliegue de este relevo siguen vigentes.

## Alcance y estado

El objetivo actual es admitir un simulador de senales propias cuyo recorrido
(decisiones, exposicion, flotante, drawdown y resultado) sea correcto bajo
inputs identicos y cuyo error frente a MT5 este medido y acotado. No se exige
adivinar todos los fills futuros ni igualar a mano dos operaciones aisladas.
El plan de cinco fases esta en
`2026-09-08-simulation-foundation-readiness.md`. Para **cerrar este objetivo**
quedan abiertas las fases 1-3; la fase 4 (busqueda masiva) y la 5 (validacion
de candidatas) son posteriores. El usuario exige aviso y revision conjunta
antes de comenzar la fase 4. El presupuesto de nuevas candidatas es cero.

Rama local: `feature/gold-555-live-trial`. El arbol esta muy modificado y
contiene abundantes archivos sin seguimiento; preservarlos. Los cambios de
esta fase son locales, sin commit, push, despliegue ni nueva comprobacion de
la VM. No atribuir a produccion resultados del arbol local.

## Cinco entregas pendientes

| Entrega | Fase | Criterio de cierre |
| --- | --- | --- |
| 1. Admision causal de datos | 1 | Declarar por canal, epoca y capacidad las senales, ediciones, ticks Bid/Ask, FX, costes y recibos disponibles; conservar denominadores y bloqueos. No exigir recibos live donde una politica historica no los usa. |
| 2. Replay causal completo | 2 | Desde mensaje/entrada, procesar solicitudes, respuestas y ticks en orden conocido; instalar protecciones solo bajo un supuesto declarado, reproducir decisiones reales sin inyectar fills futuros y verificar los tres motores para capacidades admitidas. |
| 3. Dinero y riesgo conjunto | 2 | Integrar lotes, costes, conversion, posiciones abiertas y cestas concurrentes; comparar exposicion, flotante, realizado y drawdown en una rejilla comun sin inventar precios ni cierres. |
| 4. Contraste historico amplio | 3 | Congelar criterios y comparar dias/cestas completos de ambos canales, incluidos casos adversos, con controles nativos independientes; explicar, reparar o bloquear diferencias materiales sin reducir el denominador. Los casos 3086 y 28/08 son regresiones, no muestra suficiente. |
| 5. Validacion independiente y operativa | 3 | Congelar version/supuestos y verificar una cohorte intacta o prospectiva, ademas de latencia, recuperacion y continuidad de datos en la VM; revisar juntos alcance, incertidumbre y fallos antes de autorizar cualquier busqueda. |

Las entregas 1-3 pueden avanzar por capacidad y no requieren que todo el
historico quede perfecto. El cierre del objetivo exige una admision explicita
del alcance que se vaya a usar; capacidades fuera de el se bloquean. Una
prueba interna verde no sustituye los contrastes de las entregas 4-5.

## Revision independiente para el siguiente modelo

Este relevo transmite hechos comprobados, limites y una **ruta propuesta**;
no obliga a Claude a compartir el diagnostico ni a ejecutar los pasos en ese
orden. Antes de implementar, debe contrastar el mapa con el codigo y los datos
actuales, identificar supuestos equivocados, omisiones y trabajo innecesario,
y recomendar una ruta propia si mejora la posibilidad de demostrar fidelidad
del recorrido real/virtual. En particular, revisar si la cohorte con recibos
es representativa, si se esta confundiendo acuse del cliente con instalacion
del servidor, si los comparadores cubren riesgo abierto y cuenta conjunta, y
si existe una prueba mas directa de paridad que el auditor TP propuesto.

Si cambia el plan, explicar que evidencia lo motiva, que barrera del objetivo
resuelve, que se deja de hacer y que se verificara para declararlo terminado.
No tratar el recuento de pruebas, los informes retrospectivos ni esta lista de
entregas como certificacion. Conservar la prohibicion de busqueda masiva y de
publicacion sin revision/autorizacion del usuario.

## Evidencia actual

- Los escenarios declarados de TP pendiente estan integrados en motor escalar,
  Numba y oraculo. Es consistencia interna, no prueba del fill del broker.
- `BrokerExecutionModel` en modo `observed_fill` deja la modificacion pendiente
  hasta una respuesta observada; no la instala al siguiente quote. La respuesta
  aceptada indica cuando el cliente conoce el TP, no el instante exacto de
  instalacion en el servidor. Las pruebas focales de broker: 44 aprobadas.
- Ultima suite completa tras ese cambio: `python -m pytest -q`, Python 3.14.2,
  **8.046 aprobadas, 927 advertencias, 600,97 s**. No hubo cambios funcionales
  posteriores; solo se documento el resultado.
- `reports/expanded-native-controls-20260924/native-gold-tp-touch-population-v1.json`
  conserva 46 posiciones: 9 TP con respuesta aceptada trazable, 36 TP sin
  recibo suficiente y una salida no TP. Una de las nueve tiene dos toques
  favorables anteriores al acuse; el primero sucede durante una solicitud
  despues rechazada. Esto bloquea inferir automaticamente un cierre temprano.
- El informe de sensibilidad por posicion `tp-fill-scenario-risk-9accepted-v3.json`
  tambien quedo ligado al hash antiguo del modelo de broker. Su version
  vigente es `reports/expanded-native-controls-20260924/tp-fill-scenario-risk-9accepted-v4.json`:
  mismas filas, estados y 90 comparaciones de escenarios que `v3`, con todos
  los hashes fuente actuales. Sigue siendo sensibilidad retrospectiva, no
  validacion del recorrido live. El informe de cestas `tp-fill-scenario-basket-risk-3gold-v1.json`
  y el inventario de 46 conservan hashes fuente validos.
- El replay observado existente
  `tools/audit_observed_tp_lifecycle.py` comienza **en la respuesta aceptada**,
  no en la entrada. Tras el cambio del modelo de broker, el informe `v2` quedo
  ligado a un hash antiguo. Se ejecuto de nuevo sin cambiar el auditor:
  `reports/expanded-native-controls-20260924/native-tp-observed-lifecycle-v3.json`.
  Conserva las 46 filas y los mismos estados (9 observadas, 36 bloqueadas,
  1 no TP); todos sus hashes fuente coinciden con el arbol actual. Sigue siendo
  diagnostico, **no** paridad live completa. La primera entrega tecnica de la
  siguiente sesion es unir
  solicitudes/respuestas/ticks desde la entrada sin ver informacion futura y
  sin descartar los 36 casos no admitidos. Ese auditor nuevo se habia
  anunciado, pero **no se llego a editar ni lanzar**.
- Los informes historicos y el mapa de proceso son diagnosticos de descubrimiento,
  no muestra intacta ni certificacion de paridad. No se ha demostrado aun el
  recorrido real/virtual completo del simulador ni su estabilidad en la VM.

## Continuacion segura

1. Verificar raiz Git, rama, estado y `AGENTS.md`; leer este relevo, el mapa de
   proceso y los contratos de investigacion antes de editar. Auditar la ruta
   propuesta con criterio propio y presentar cualquier correccion material.
   No limpiar el arbol ni sincronizar otros worktrees por iniciativa propia.
2. Si el auditor TP sigue siendo el siguiente paso mejor justificado,
   reproducir la evidencia de 9/36/1 y fijar una vida TP desde
   entrada a deal, con fuentes y hashes. Distinguir solicitud, respuesta,
   instalacion servidor desconocida, toque terminal y fill observado; bloquear
   relojes empatados u orden no demostrable. No ajustar el modelo a una pierna.
3. Conectar la trayectoria causal admisible a los comparadores existentes de
   dinero y riesgo; contrastar por posicion, cesta y cuenta. Mantener por
   separado replay de lo observado y alternativas hipoteticas.
4. Congelar criterios/cohorte independiente antes de usarla. No entrar en
   busqueda masiva, cambiar estrategia live, publicar ni reiniciar el bot sin
   la revision y autorizacion especificas del usuario.

No hay proceso de ensayo de esta fase que deba quedar corriendo al relevo: la
ultima suite termino con codigo 0. Consultar el estado actual antes de asumir
que la VM o la rama siguen iguales.
