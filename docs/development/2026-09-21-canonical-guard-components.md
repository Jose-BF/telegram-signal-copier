# Guards Canonicos Sobre Lecturas Causales

## Estado Y Alcance

Siguiente incremento local de la integracion interquote. El motor puede consumir
el guard canonico Dubai o Gold 555 sobre sus propios resumenes secuenciales.
No se admite todavia la estrategia completa: entradas y protecciones nativas
siguen descritas por el genome y las hipotesis de ejecucion, separadamente del
componente guard. No hay push, despliegue, reinicio ni acceso a VM.

## Contrato Compartido

`basket_management.py` comparte normalizacion y evaluacion entre los wrappers
locales del monitor y el replay. Usa los evaluadores existentes de
`dubai_live_candidate` y `gold_555_live_candidate`, no el candidato Gold anterior.
No modifica umbrales, identidad, journaling, persistencia ni comprobaciones de
fingerprint de los wrappers. Ocho caracterizaciones pasaban antes y despues
de extraer el contrato.

- Conserva presencia de claves: `pl=None` no equivale a `pl` ausente; cero no
  se sustituye por otro valor. POSITIONS desconocido no se convierte en vacio.
- No redondea a centimos antes de decidir. Conserva el dinero observado, los
  estados previo/posterior y el elapsed al recibir el resumen desde primer fill.
- Dubai evalua stop monetario antes del profit-lock y salida temporal no
  positiva. Gold 555 no tiene ese stop monetario en su guard y su salida temporal
  exige resultado no negativo. El primer armado retorna antes del time-exit.
- Una recuperacion comprometida precede a la barrera de historia incompleta.
  Sin compromiso previo, evidencia incompleta no arma ni actualiza el pico.
- La rama canonica sustituye exclusivamente al profit-lock/time-exit generico
  del motor. No se ejecutan ambos sobre la misma lectura.

`CanonicalGuardComponent` exige uno de los dos identificadores conocidos. El
scope debe pertenecer al canal correspondiente, declarar lecturas y usar el
perfil EUR. El informe conserva selector, fingerprint, politica completa,
perfil de lecturas y cadencia; no deduce identidad solo del genome.

## Decidir No Es Enviar

Se reprodujo con limites de traza 3 y 4 la perdida del registro de una decision
de cierre cuando fallaba su encolado. El driver ahora guarda primero
`policy_decision` y despues `policy_application`. Esta ultima distingue
`not_requested`, `queued` y `blocked`; queued no significa aceptado por MT5.

Si falla el encolado, se conserva accion, motivo original, estados, error y
`recovery_pending`. Tambien se distinguen tickets aun en cola y solicitudes
ya enviadas al modelo. Una regresion con dos posiciones y limite 8 conserva
el primer cierre en cola y el segundo no encolado. El informe queda bloqueado:
no se declara recuperacion exitosa ni se elimina esa cesta del denominador.
Esto no certifica recuperacion persistente tras reiniciar el simulador.

## Evidencia

38 casos nuevos en `tests/test_canonical_basket_guard.py`: caracterizacion live,
umbrales y dinero subcentimo, prioridades, ausencia/recuperacion de evidencia,
seleccion de canal, decisiones distintas sobre el mismo mercado, aislamiento
entre cestas, fallo completo/parcial de encolado y trazabilidad de entradas.
492 pruebas focalizadas aprobadas. Revision independiente: defecto de perdida
de decision reproducido; reparacion confirmada con controles 3, 4 y 1000, sin
nuevos hallazgos materiales en ese alcance. No es otra suite general.

General congelada con 390 fuentes, runtime temporal aislado y comando real
`python -m pytest -q`. Resultado definitivo en
`reports/e5-canonical-guard-components-20260921/verification.json`. El paquete
conserva seis controles sinteticos con inputs completos, perfil, resultados,
fuentes, salida y JUnit. La general anterior de 6.922 no verifica este incremento.

Resultado final: **6.960 aprobadas**, 478,87 segundos, cero fallos, errores o
casos omitidos. Diez advertencias anteriores (nueve record_property/xunit2 y
una de pandas). Las 390 fuentes y la identidad de implementacion permanecen
sin cambios. SHA256 del JUnit comprobado:
`112bfeb24ba1c39d405270488d01d64b984539fb057135f4c89e33af4f47a19e`.
El proceso termino con codigo 0; no queda una general propia ejecutandose.

## Siguiente Paso

Completar el ciclo de monitor observado y protecciones, sin levantar por mera
compatibilidad los gates actuales: tick inicial/deduplicacion, entradas propias,
stops Dubai de cesta valorados por broker, targets y trailing 555, respuestas,
reintentos y persistencia. Las peticiones deben conservar por separado decision,
quote fuente y envio; un precio de una lectura anterior no se actualiza con
informacion futura. Despues: ventanas historicas completas, primera divergencia,
exposicion/flotante/drawdown y validacion posterior al bloque de reparacion.

El objetivo general, la fidelidad completa, la verificacion VM y la admision
de busqueda de estrategias siguen abiertos. No se promete rentabilidad.

## Fronteras De Proteccion Para El Siguiente Bloque

- Dubai: `_ensure_dubai_candidate_hard_stops` consulta posiciones abiertas y
  llama a `executor.basket_loss_stop_price`. Este lee SYMBOL y valora mediante
  PROFIT cada posicion durante la busqueda acotada del nivel comun. Las
  respuestas deben seguir el transporte, no calcularse desde un resumen futuro.
  El presupuesto del stop nativo sobre posiciones abiertas no es lo mismo que
  el guard que suma realizado y flotante; no sustituir uno por el otro.
- Gold 555: las protecciones provisionales se calculan antes del envio. Tras
  conocer el fill, la primera entrada usa `_queue_gold_555_first_leg_protection`
  y las adicionales `_queue_gold_555_leg_protection_unrecorded`. Se conserva
  el SL mas fuerte entre provisional/instalado/deseado y el calculado desde fill;
  TP se calcula desde el fill real de esa pata. Encolar SL y TP no equivale a
  que ambos esten ya instalados ni a una modificacion atomica del broker.
- El helper `_ensure_gold_candidate_hard_stops` no es la ruta 555: solo acepta
  el identificador del candidato Gold anterior. No incorporarlo por el nombre.
- El trailing 555 usa tick inicial y estado deseado persistido, y puede elevar
  ese estado antes de la aceptacion del broker. Distinguir deseado, en cola,
  enviado, instalado y confirmado en lugar de mover el stop instantaneamente.

Pruebas de admision necesarias: TP tocado antes del ajuste desde fill; SL
provisional mas fuerte que el nuevo; cierre nativo mientras llega respuesta;
fallo entre encolado de SL y TP; otra cesta reteniendo transporte; FX cambiado
durante la busqueda Dubai; recovery con peticion ya comprometida. Despues se
contrasta la secuencia completa, no solo el precio final del stop.
