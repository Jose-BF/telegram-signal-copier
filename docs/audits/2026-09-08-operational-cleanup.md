# Cierre operativo e inventario de datos

## Alcance acordado

El usuario separa expresamente el trabajo: cerrar primero pendientes operativos
y revisar la calidad de los datos. Simulaciones, seleccion de estrategias y
busqueda de rentabilidad quedan para una fase posterior independiente.
No se ha ejecutado ninguna simulacion, busqueda, promocion ni descarga de ticks.

## Pendiente operativo resuelto

- [x] Diagnosticar el bloqueo de publicacion de registros.
- [x] Recuperar la cola sin reiniciar el bot ni cambiar su codigo/configuracion.
- [x] Verificar la subida, su recuperacion local y los prefijos originales.
- [x] Verificar envios automaticos posteriores sin intervencion.
- [x] Inventariar exportaciones y caches existentes, manteniendo excepciones.
- [ ] Comprobar el comportamiento reparado con senales nuevas y ciclos cerrados.
- [ ] Completar los huecos historicos identificados con fuentes adicionales.

La ultima publicacion correcta previa era del 3 de septiembre a 23:55:13 UTC.
Un `index.lock` vacio, abandonado en el checkout exclusivo de telemetria,
impedia publicar la cola. La recuperacion inicial reprodujo un segundo problema:
preparar de golpe miles de archivos agotaba los 30 segundos disponibles y
dejaba otro bloqueo. Se resolvio la acumulacion con lotes de 100 archivos,
manteniendo la exclusion del publicador y verificando que no hubiera procesos
Git activos antes de mover cada bloqueo identificado.

Los dos bloqueos se conservaron como `index.lock.recovered-repair1` y
`index.lock.recovered-repair2`, dentro del mismo directorio Git auxiliar.
No se borraron registros ni se cambio el indice del checkout del bot.
La adaptacion de lotes existio solo en el auxiliar de mantenimiento: no hubo
modificacion de codigo desplegado ni cambio global de tiempos de espera.

Resultado a 23:30:57 UTC: 4634 archivos publicados y verificados en el commit
de telemetria `8dd23475ff2f73ed2a72235712803c70cc25852b`; cero pendientes.
Los envios automaticos normales volvieron a pasar, entre ellos los de 23:32:10,
23:42:12 y 23:47:14 UTC. Este ultimo publico cuatro archivos en
`24daa9dc5d66d8a107892c771aca52db389f572e`, sin errores ni pendientes.
El bot emitio tambien `telemetry_publication_recovered`.

Se recupero desde la rama remota una copia nueva con el transporte existente.
Los cuatro registros operativos reconstruidos coinciden exactamente por SHA256
con los prefijos correspondientes de la VM: eventos, diario CSV, consola y
registro de medios. El analisis de 243205 eventos no encontro errores JSON ni
una ultima linea incompleta. Esto acredita integridad de datos, no certifica
por si solo contabilidad, fills hipoteticos ni rentabilidad.

Se conservaron aparte dos registros TEST. El CSV TEST coincide; el JSONL TEST
remoto conserva 102200 bytes frente a 16528 del archivo actual de la VM,
inalterado desde julio. Esa diferencia diagnostica queda declarada, no se
presenta como una copia exacta del archivo TEST actual y nunca se usa como
actividad de trading real. No se truncaron historicos para hacerlos coincidir.

## Inventario existente

Se revisaron cinco exportaciones conocidas de Telegram Desktop y 136 archivos
Parquet de nueve carpetas de caches del proyecto. No se exploraron otros
proyectos ni se supuso que sus datos o estrategias fueran equivalentes.

| Fuente | Mensajes distintos de 2026 | Observacion |
| --- | ---: | --- |
| Dubai, chat 1642806869 | 2934 | Exportaciones enero-abril y junio-julio; mayo no figura en las copias revisadas |
| Gold, chat 2614601304 | 4182 | Generacion historica, enero-abril |
| Gold, chat 3828356530 | 2797 | Otra generacion, marzo-julio |

Son mensajes, no senales de entrada. Se quitaron duplicados entre exportaciones
solo por el mismo chat y mensaje; las generaciones de Gold no se mezclaron.
Los textos exportados son snapshots finales, no un historial completo de
ediciones ni evidencia de cuando el bot recibio cada version. Hay referencias
a medios no incluidos en varias exportaciones; los avisos repetidos de Telegram
se cuentan por referencia, no como un unico archivo perdido.

El diario recuperado abarca del 5 de junio al 7 de septiembre. Hay captura raw
con chat identificado para Dubai desde el 21 de julio y para el Gold actual
desde el 22 de julio; este ultimo incluye mensajes originalmente publicados
desde el 17 de julio. Los 7108 registros raw anteriores sin `chat_id` se mantienen
separados por canal y con identidad incompleta, sin inventar una generacion.

Las caches locales revisadas contienen 29 fechas distintas entre el 27 de julio
y el 2 de septiembre, en XAUUSD y EURUSD; las copias repetidas no amplian esa
cobertura. Se comprobaron hashes, metadatos horarios v3 y numero de filas del
footer: 134 archivos consistentes y dos excepciones de captura parcial.
No es una nueva certificacion semantica de cada tick ni una prueba de que el
broker carezca de datos en las fechas no encontradas localmente.

Las dos capturas parciales del 2 de septiembre se conservan:

- `runtime_data/gold_corpus_20260727_latest/ticks_cache/2026-09-02.parquet`:
  659470 filas, sin verificacion independiente valida para el dia completo.
  Existe una copia posterior con metadatos consistentes y 702748 filas en
  `runtime_data/canal2_deep_review_20260902_2353/all_market_ticks_cache/2026-09-02.parquet`.
- `runtime_data/week_compare_20260831_20260902_2152/money_ticks_cache/2026-09-02.parquet`:
  127597 filas y la misma limitacion de captura parcial. Existe una copia
  posterior consistente con 131669 filas en
  `runtime_data/canal2_deep_review_20260902_2353/all_money_ticks_cache/2026-09-02.parquet`.

No se sobrescribieron las parciales, no se alteraron sus metadatos y no se
cambio automaticamente la fuente de ningun experimento o proceso.

## Estado y pendientes reales

A 23:49 UTC la VM sigue en `9765758ed84bcdcb5e2ca690ec954a2dce154bc6`,
checkout limpio, mismo bot PID 6796, supervisor 6044 y terminal 8968.
Heartbeat v3 fresco y plano, sin pausa ni bloqueos de publicacion retenidos.
No se hizo un nuevo commit/push a `main`, ni se reinicio el bot, ni se cambiaron
estrategias, parametros, lotaje o cuenta. Solo se publicaron datos en `telemetry`.

Hasta la comprobacion incremental de 23:47 UTC no habia senales nuevas desde
el despliegue. Esa validacion requiere actividad posterior y sigue pendiente.
Los huecos de exportaciones/medios y de cobertura local necesitan fuentes
adicionales; la revision los identifica, no los declara reparados. La investigacion
de rentabilidad no se inicia automaticamente al cerrar esta lista.

## Evidencia

Directorio local: `runtime_data/operations_cleanup_20260908_2315/`.

- `recovery_repair1.json`, `recovery_repair2.json`, `recovery_outcome_repair2.json`.
- `telemetry_snapshot_2331/`, `snapshot_transport_manifest.json`,
  `snapshot_remote_match_v1.json`.
- `available_data_inventory_v2.json`, `recovered_events_audit_v2.json`.
- Seis regresiones del inventario pasan. Una reprodujo y corrigio el recuento
  insuficiente de referencias a medios no exportados. No hubo cambios de
  produccion que invalidaran la suite anterior de 3025/1256 pruebas.

Los informes v1 se conservan como resultados intermedios; para decisiones se
usan los v2, con referencias multimedia por ocurrencia y canal separado cuando
falta el identificador del chat. Estos informes y auxiliares permanecen locales,
sin publicarse como codigo del bot.

Continuacion posterior: `2026-09-08-pending-cleanup.md` amplia el inventario a
cuatro caches de la VM y recupera 58 archivos diarios adicionales, manteniendo
25 como diagnosticos. Tambien documenta la proteccion permanente local del
publicador; no modifica retroactivamente el alcance de esta recuperacion puntual.
