# Muestra Anual Y Ventanas Moviles

Actualizacion posterior: el usuario autorizo MT5 aislado y la
[extraccion anual de precios esta terminada](2026-09-13-canal1-annual-prices.md).
150477123 cotizaciones, 94 pruebas y tres incidencias de caches anteriores
documentadas. El permiso de extraccion ya no esta pendiente. Siguen abiertos
reloj, cobertura por horizonte, costes y mensajes editados antes de simular.
El resto de este informe conserva el estado de preparacion del universo.

## Estado Vigente

El usuario delega las decisiones de preparacion: incluir entradas textuales
claras y reentradas, ademas de stickers, sin una separacion rigida del ano.
La consulta anterior queda resuelta con ese criterio. Trabajo exclusivamente
local: sin commit, push, VM, arranque de MT5, cambios live, ordenes ni busqueda
de estrategias. Se mantienen intactos el catalogo previo, el parser y los motores.

Se ha congelado un **universo de trabajo de 736 hipotesis de entrada**. No son
736 operaciones observadas ni 736 disparadores admitidos. Las relaciones
texto/sticker se adjudican retrospectivamente, conservando incertidumbre; no
se presentan como una regla de deduplicacion causal lista para operar.

## Resultados

| Concepto | Cantidad |
| --- | ---: |
| Mensajes fuente conservados y asignados una sola vez | 5494 |
| Entradas de trabajo, BUY / SELL | 736, 306 / 430 |
| Entradas con raiz sticker / texto independiente | 712 / 24 |
| Textos complementarios, incluidos seis revisados fuera del filtro NOW | 717 |
| Mensajes adicionales solo de contexto | 2 |
| Otros mensajes retenidos fuera del alcance de entrada | 4039 |
| Entradas con algun mensaje reenviado | 28 |
| Entradas con notas de revision, algunas no son dudas | 21 |
| Hora de referencia respaldada por snapshot direccional sin edicion | 234 |
| Algun componente direccional sin edicion, aunque sea posterior | 240 |
| Sin respaldo inicial en la hora de referencia | 502 |
| Sin ningun componente direccional sin edicion | 496 |
| Entradas admitidas al motor | 0 |

Los 736 tiempos de referencia coinciden con el epoch del mensaje original en
la exportacion reciente. Primera entrada: 02/01/2026 12:50:44 UTC; ultima:
11/09/2026 14:47:56 UTC. El historial general llega al 13/09 12:20:45 UTC.
No se confunde la ultima senal con el ultimo mensaje o con la hora de exportacion.

| Mes | Entradas | BUY | SELL | Respaldo inicial en referencia |
| --- | ---: | ---: | ---: | ---: |
| Enero | 75 | 37 | 38 | 17 |
| Febrero | 73 | 26 | 47 | 35 |
| Marzo | 109 | 60 | 49 | 55 |
| Abril | 94 | 44 | 50 | 40 |
| Mayo | 91 | 34 | 57 | 17 |
| Junio | 97 | 35 | 62 | 20 |
| Julio | 73 | 16 | 57 | 22 |
| Agosto | 73 | 32 | 41 | 9 |
| Septiembre parcial | 51 | 22 | 29 | 19 |

La disponibilidad de snapshots iniciales es muy desigual por mes. No seria
correcto llamar representativa de todo el ano a la submuestra de 234 sin
mostrar esta perdida de cobertura. No se rellenan los casos restantes con
el contenido final editado como si hubiese estado disponible originalmente.

## Decisiones De Mensajes

Registro de 50 decisiones, ligado al hash del catalogo y con IDs de evidencia:
[revision](../../runtime_data/canal1_history_20260913/reviewed_entry_relationships_v1.json).
No se utilizaron precios historicos ni rentabilidades para adjudicarlas.

- Los 694 complementos NOW sin advertencias conservan la hipotesis declarada
  de proximidad. 658 son inmediatamente posteriores al sticker; se revisaron
  los mensajes intermedios de los otros 36 casos. No es una confirmacion de
  causalidad ni una garantia absoluta de unicidad.
- Se resolvieron las 43 filas NOW advertidas: 24 ofertas independientes,
  17 complementos, un comentario fuera de entradas y un recordatorio de contexto.
- 19553 es otra oferta: 19552 anuncia expresamente un nuevo trade, aunque
  ambos textos queden dentro de cinco minutos del sticker 19550.
- 18761 conserva su reentrada pese a repetir el texto de 18758: 18760 la
  anuncia. La identidad no se deduplica por igualdad del texto.
- AGAIN no implica siempre otra entrada: 17149 y 17282 complementan stickers
  nuevos; 17144, 17278 y 17299 son ofertas adicionales.
- 18430 se conserva como otra oferta SCALP con especificacion distinta, pero
  queda marcada la alternativa de que fuera una modificacion de 18429.
- Seis textos explicitos GOLD fuera de la gramatica anterior se etiquetan por
  snapshot revisado: 16887, 17658, 18070, 18617, 18945 y 19844. No cambian el
  parser compartido. Los otros 453 textos direccionales/zonas/ambiguos siguen
  conservados fuera del alcance, sin afirmar una revision manual exhaustiva.
- Ocho textos self-contained preceden al sticker asociado. Su publicacion es
  referencia del grupo, pero no se presupone que su contenido editado fuera
  conocido entonces. Los componentes posteriores conservan su propia hora.
- 22158 es un sticker BUY sin simbolo: queda solo como contexto de 22160.
  La direccion GOLD del texto posterior nunca se traslada al sticker anterior.
- Los reenvios conservan su origen. Las asociaciones 20590/20591,
  21531/21532 y 22098/22099 son hipotesis entre origenes distintos; se requiere
  sensibilidad a su separacion antes de seleccionar resultados.
- Cinco ofertas condicionales a estar fuera y dos ofertas adicionales con
  posible solapamiento mantienen etiquetas. Su eventual admision dependera de
  nuestra politica declarada de posiciones, no del estado afirmado por el proveedor.
- 17073 es comentario que pide esperar, no nueva entrada. 20931 se trata como
  recordatorio del grupo 20929. 20304 es complemento tardio segun el contexto;
  sus niveles no se adelantan a la publicacion del sticker.

El contexto posterior puede justificar una adjudicacion retrospectiva; no
demuestra que esa adjudicacion pudiera ejecutarse en tiempo real. Ningun TP,
SL, beneficio o cierre del proveedor impulsa la estrategia propia en este bloque.

## Relojes

Cada entrada conserva publicacion de referencia, miembros y fuentes originales,
primer componente conocido sin marca de edicion y primera disponibilidad de
revision conocida. Estos campos son diagnosticos. `trigger_utc` y `received_utc`
siguen nulos; `engine_admitted` es falso. No se infiere recepcion observada.

Una revision conocida no acredita el contenido inicial. La ausencia de marca
de edicion en un snapshot exportado tampoco acredita una recepcion por el bot.
Si el primer texto esta editado y el sticker posterior no, la hora respaldada
del sticker se conserva como posterior: no convierte la primera hora en valida.
Las seis entradas con respaldo solo en un componente posterior no se suman
a las 234 con respaldo en la referencia.

## Ventanas Congeladas

Protocolo para preparar cohortes, fijado antes de medir resultados nuevos:
56 dias de desarrollo, siguientes 14 dias de comprobacion, avance de 14 dias.
Todas las duraciones y limites son UTC; intervalos cerrados al inicio y abiertos
al final. No hay mezcla aleatoria de operaciones de fechas futuras.

Se retienen 145 entradas iniciales como preparacion; cada una de las otras 591
pertenece a una sola comprobacion. Una comprobacion anterior puede formar parte
del desarrollo de una ventana posterior, reproduciendo el avance cronologico.
Esto no convierte el conjunto retrospectivo en OOS nuevo e intacto.

| Ventana | Comprobacion UTC, fin excluido | Entradas |
| --- | --- | ---: |
| 1 | 26/02 a 12/03 | 36 |
| 2 | 12/03 a 26/03 | 52 |
| 3 | 26/03 a 09/04 | 54 |
| 4 | 09/04 a 23/04 | 36 |
| 5 | 23/04 a 07/05 | 43 |
| 6 | 07/05 a 21/05 | 49 |
| 7 | 21/05 a 04/06 | 36 |
| 8 | 04/06 a 18/06 | 51 |
| 9 | 18/06 a 02/07 | 40 |
| 10 | 02/07 a 16/07 | 32 |
| 11 | 16/07 a 30/07 | 31 |
| 12 | 30/07 a 13/08 | 29 |
| 13 | 13/08 a 27/08 | 37 |
| 14 | 27/08 a 10/09 | 50 |
| 15, parcial | 10/09 a 13/09 12:20:46 | 15 |

La ultima ventana no se presenta como quincena completa. El extremo final es
un segundo despues de la ultima publicacion observada, no garantia de ausencia
de mensajes borrados o de huecos. Septiembre tambien es un mes parcial.

Se reserva una purga de cuatro horas antes de cada comprobacion: cualquier
politica futura debe caber en ese horizonte total desde el disparador, incluidas
esperas y ejecucion. Si lo supera, hay que versionar el protocolo antes de medir.
En estos tiempos de referencia no hay entradas dentro de esas franjas, pero
la barrera sigue comprobada y probada en limites sinteticos.

Las cohortes actuales sirven para planificar la muestra: al elegir el reloj
causal admitido se deben recalcular pertenencias, purga y cobertura de precios.
Se informaran resultados y denominadores por mes y ventana, sin igualar meses
borrando senales. Duraciones no optimizadas sobre rentabilidad; presupuesto,
reglas, ejecucion, capital y reserva futura requieren congelacion propia.

## Precios Localizados

[Inventario con hashes y presencia por entrada](../../runtime_data/canal1_history_20260913/price_inventory_v1.json).

- Los 63 archivos seleccionados por el inventario previo siguen existiendo
  con sus hashes y metadatos intactos: XAUUSD 32 dias y EURUSD 31 dias, del
  22/07 al 02/09. Hay un archivo XAUUSD en el dia de referencia de 103 entradas,
  y ambos simbolos para 100. Esto NO acredita el horizonte completo de ninguna.
- Hay 18 archivos nativos `.tkc` de 2026: XAUUSD enero-mayo en
  `VantageInternational-Demo` y mayo-septiembre en `VantageMarkets-Demo`;
  EURUSD enero-abril y junio-septiembre, respectivamente. Mayo EURUSD no aparece
  en esas carpetas. Se registraron tamanos y hashes, no se decodificaron cotizaciones.
- Dos nombres de servidor no son una unica fuente intercambiable. Hay que
  comprobar compatibilidad, cobertura de mayo y vinculo al experimento; no se
  mezclaron ni se incorporaron los archivos de FP Markets a la muestra.
- Faltan lectura de Bid/Ask nativos, huecos por horizonte, reloj historico y
  cambios de horario, metadatos de simbolo/cuenta, costes y conversion aplicable.
  No se extrapola el desfase de verano a enero.
- MT5 local esta cerrado. No se ejecuto el descargador existente porque su
  inicializacion puede arrancar un terminal con asesores cargados. Se necesita
  autorizar y verificar una instancia aislada, con trading desactivado, o recibir
  una exportacion historica equivalente del broker. No se requiere tocar el bot real.

## Artefactos Y Verificacion

Base congelada: `runtime_data/canal1_history_20260913/universe_v1/`.

- [Entradas y horas](../../runtime_data/canal1_history_20260913/universe_v1/entries.jsonl).
- [Asignacion de todos los mensajes](../../runtime_data/canal1_history_20260913/universe_v1/message_assignments.jsonl).
- [Ventanas y sus miembros](../../runtime_data/canal1_history_20260913/universe_v1/rolling_folds.jsonl).
- [Uso individual de cada entrada](../../runtime_data/canal1_history_20260913/universe_v1/entry_fold_usage.jsonl).
- [Resumen](../../runtime_data/canal1_history_20260913/universe_v1/summary.json) y
  [manifiesto](../../runtime_data/canal1_history_20260913/universe_v1/manifest.json).

Identidad: `db038d3597ecfbba177ef723ea0eaee0c9e814dca6d8fddc83d2c8d1c549dbb6`.
Entrada: catalogo `43870cb79abfa3f41addfc6dbbd614adf2a6ec84b1e92873d48f1e6ad500190e`.

Verificacion con Python 3.14.2, pytest 9.0.3:
`py -3.14 -m pytest -q tests/test_dubai_annual_universe.py tests/test_dubai_export_catalog.py tests/test_telegram_exports.py`
dio **76 aprobadas**: 22 del nuevo contrato y 54 previas. Casos de reloj,
etiquetas por snapshot, reentradas, simbolo ausente, reenvios, evidencia faltante,
ciclos, direcciones contradictorias, manipulacion de fuentes, inmutabilidad,
limites de ventanas, purga y final parcial. CLI ejecutado sobre el archivo real.

Comprobacion independiente con PowerShell y fechas UTC invariantes:
**736/736 referencias conciliadas**, hashes de todos los artefactos correctos,
cero admisiones y cero procesos MT5. La comprobacion de presencia de precios
verifico los 63 archivos y sus 63 metadatos. No se repitio la suite completa de
trading porque no se modificaron rutas compartidas de ejecucion o dinero.
Reejecucion del CLI sobre la misma salida: misma identidad y mismos bytes.
Todos los enlaces locales del informe existen; 5494 asignaciones unicas y
591 entradas en exactamente una comprobacion, con las 145 iniciales retenidas.
Hash del inventario de precios:
`a8f40f3efc848c42d3571b7a41b944074c119e3ee90c7f4e405ea63df8f988a4`.

Siguiente paso: obtener precios legibles mediante una instancia aislada
autorizada, resolver compatibilidad de servidores y admitir los horizontes.
Las limitaciones de ediciones y politica de agrupacion siguen abiertas antes
de lanzar una nueva campana anual. No hay resultados de rentabilidad nuevos.
