# Catalogo Anual De Senales Dubai

Actualizacion posterior: el usuario delega la preparacion y se incluyen textos
claros y reentradas. [Universo anual de trabajo y ventanas congeladas](2026-09-13-canal1-annual-universe.md):
736 hipotesis, 14 ventanas completas y una parcial; 76 pruebas. La consulta
pendiente de este documento queda resuelta. El catalogo base se conserva
inmutable; sus propuestas y cifras no son todavia admisiones al motor.
El resto documenta el estado anterior a esa adjudicacion.

## Estado Y Acuerdo

El usuario acepta preparar todo el listado anual desde los JSON y despues
fijar ventanas moviles que representen distintos periodos. Queda sustituida
la propuesta rigida enero-abril / mayo-junio / julio-septiembre. No se han
fijado aun duraciones de ventanas, pasos, presupuesto ni nuevas candidatas.
La reserva futura debe comenzar despues de congelar reglas; los periodos
ya reutilizados no se vuelven OOS intacto por cambiar su agrupacion.

Preparacion terminada como **catalogo**, no como dataset ejecutable ni como
recuento definitivo de entradas independientes. Trabajo solo local, sin
commit/push, acceso a VM, precios nuevos, simulaciones, reinicios ni ordenes.
No se modificaron el adaptador previo, el parser compartido ni los motores.

## Archivos De Trabajo

Base: `runtime_data/canal1_history_20260913/catalog_v2/`.

- [Stickers con fecha, hora UTC y direccion](../../runtime_data/canal1_history_20260913/catalog_v2/sticker_signals.jsonl).
- [Entradas textuales y asociaciones propuestas](../../runtime_data/canal1_history_20260913/catalog_v2/text_entries.jsonl).
- [Inventario completo](../../runtime_data/canal1_history_20260913/catalog_v2/messages.jsonl).
- [Resumen](../../runtime_data/canal1_history_20260913/catalog_v2/summary.json).
- [Manifiesto verificable](../../runtime_data/canal1_history_20260913/catalog_v2/manifest.json).

Identidad del catalogo:
`43870cb79abfa3f41addfc6dbbd614adf2a6ec84b1e92873d48f1e6ad500190e`.
`catalog_v1` se conserva como salida intermedia anterior a la comprobacion
adicional de mensajes de servicio; no es la version vigente.

Cada fila conserva identidad del canal/mensaje, direccion y simbolo cuando
estan identificados, publicacion UTC, revisiones conocidas, huellas de las
fuentes y medios, reenvios e incidencias. `received_utc` y `trigger_utc` son
nulos; `engine_admitted` es falso. No se inventan recepciones ni se elige
silenciosamente el reloj que utilizara el simulador.

Las direcciones de los stickers se identifican mediante hashes de bytes
inspeccionados visualmente, no por emoji ni por nombres locales de archivo.
La misma identidad aparece una sola vez aunque figure en varias exportaciones;
sus distintas ocurrencias y revisiones siguen enlazadas. Los medios de dos
snapshots se contrastan aunque el adaptador textual hubiera agrupado sus
descriptores. Direcciones contradictorias quedan como incidencia.

## Resultado

| Concepto | Cantidad |
| --- | ---: |
| Mensajes de 2026 conservados | 5494 |
| Publicaciones con sticker BUY/SELL GOLD | 712 |
| Sin marca de reenvio / reenviadas | 687 / 25 |
| Stickers con al menos un snapshot sin marca de edicion | 233 |
| Stickers sin ese snapshot | 479 |
| Mensajes candidatos NOW del parser existente | 737 |
| Propuestas de texto complementario | 698 |
| Stickers distintos con esas propuestas | 696 |
| Textos sin asociacion propuesta | 39 |
| Textos con alguna advertencia de relacion | 43 |
| Otros textos direccionales, zonas o ambiguos conservados | 459 |
| Stickers sin etiqueta direccional GOLD asignada | 5 |
| Otros mensajes conservados | 3581 |
| Entradas definitivas / admitidas en el motor | No determinado / 0 |

233, frente a 232 en la exportacion reciente sola: se conserva el snapshot
anterior sin marca de edicion del mensaje 18515, publicado el 01/04 a las
12:08:28 UTC. No se borra su revision posterior ni se presupone una historia
completa de ediciones. Ausencia de marca se mantiene como evidencia de un
snapshot exportado, no de recepcion observada.

## Relaciones Y Dudas

La proximidad de cinco minutos se usa UNICAMENTE para proponer relaciones
retrospectivas: misma direccion, mismo origen y sticker anterior, sin cruzar
otro sticker. No fusiona IDs, no determina nuevas ordenes, no adelanta textos
editados ni confirma causalidad. Publicaciones en el mismo segundo, cambios
de origen, contradicciones y textos contextuales conservan advertencias.
No se ha ajustado ese umbral utilizando precios o rentabilidad.

- Hay cinco textos con AGAIN que no se absorben como mero complemento.
  Ejemplo: 17142, sticker BUY del 26/01 a las 13:01:14 UTC; 17143, texto a las
  13:02:43; 17144, BUY again a las 13:03:54. El tercero puede ser otra entrada.
- Dos stickers tienen dos textos asociados propuestos cada uno: los cuatro
  textos tambien requieren revision. Por ello 698 asociaciones no significan
  698 pares definitivos, y los 39 sin pareja no agotan los casos dudosos.
- Hay seis textos cuyo NOW aparece fuera de una cabecera clara de entrada.
  Ejemplo 17073: comentario sobre volver a una zona y esperar confirmacion.
  El parser previo lo recoge; este catalogo lo senala, no lo ejecuta ni
  modifica el parser compartido.
- Se mantienen diferencias de origen: 21531 es sticker reenviado del propio
  canal seguido por 21532 sin reenvio; 22098 es sticker sin reenvio seguido
  por 22099 reenviado de VIP 3.0. No son errores que deban arreglarse borrando
  metadatos ni pruebas suficientes para dos operaciones independientes.

Consulta enviada al usuario durante esta preparacion: incluir tambien las
entradas textuales independientes cuando no haya sticker correspondiente,
usando su publicacion y contando sticker+texto una sola vez cuando sean una
misma entrada. Ejemplo 17176 del 27/01 a las 12:51:11 UTC. Respuesta pendiente
al cerrar este bloque; el catalogo conserva ambas posibilidades.

## Verificacion

- `py -3.14 -m pytest -q tests/test_dubai_export_catalog.py tests/test_telegram_exports.py`:
  **54 pruebas aprobadas** (24 nuevas, 30 del adaptador existente).
- Cobertura: identidad, reloj UTC, ediciones, fuentes superpuestas, cambios
  de bytes de medios, reentradas AGAIN, contexto, reenvios, orden temporal,
  rutas de medios, mensajes de servicio, archivos inmutables y CLI offline.
- La prueba de servicio detecto una clasificacion indebida de un sticker en
  una fila no `message`; se corrigio antes de generar `catalog_v2`.
- Reejecucion completa de la preparacion: misma identidad y mismos bytes.
- Verificacion independiente de manifiesto, todos los artefactos, fuentes
  JSON/medios y archivos de implementacion mediante hashes: aprobada.
- Conciliacion independiente con PowerShell: **712/712** IDs, direcciones y
  epochs de publicacion identicos al JSON, sin recepcion ni admision inventadas.
  Se usa `ConvertFrom-Json -DateKind String` y cultura invariante: el primer
  intento de verificacion reconvirtio fechas automaticas a texto local y
  altero su interpretacion. Se corrigio el verificador, no los datos ni relojes.
- No se repite la suite de trading completa: no se modificaron rutas
  compartidas de ejecucion, dinero, replay ni contratos existentes.

Siguiente paso: resolver alcance de textos y casos concretos, fijar tratamiento
de ediciones/publicacion/retrasos y admitir precios Bid/Ask/conversion para
los horizontes anuales necesarios. Ventanas moviles antes de evaluar nuevas
estrategias, no despues de elegir resultados favorables.
