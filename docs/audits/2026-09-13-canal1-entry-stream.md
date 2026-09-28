# Regla Temporal De Entradas De Canal 1

## Estado

Regla implementada, aplicada al historial completo y verificada. Version
vigente **entry_stream_v3**. Los 5494 mensajes quedan contabilizados en cada
escenario; los **1453 mensajes direccionales** del universo anterior quedan
asignados en el escenario de versiones conocidas, sin diferencias de IDs.
Las relaciones manuales anteriores se usan para comparar, nunca para decidir
la entrada. No se han usado precios o resultados para elegir la agrupacion.

| Escenario temporal | Activaciones hipoteticas | BUY / SELL | Cobertura 20 min | Cobertura 4 h |
| --- | ---: | ---: | ---: | ---: |
| Primera version conocida, original o editada | 881 | 367 / 514 | 863 | 724 |
| Solo versiones conservadas sin editar | 240 | 106 / 134 | 235 | 204 |

Son escenarios de informacion disponible, **no dos particiones del ano**.
Ambos conservan los meses completos disponibles y sus ventanas cronologicas.
El contraste sin editar mantiene visibles 496 grupos antiguos sin disparador
respaldado. La falta de recepcion real del bot no se rellena con publicacion.

Los porcentajes de cobertura dependen del horario del broker declarado +2/+3
y de los limites existentes, no de una certificacion nueva de reloj o dinero.
La regla de entrada esta definida; el dataset completo para evaluar estrategias
sigue sin admitirse mientras no se declare ejecucion, exposicion y dinero.

## Regla Congelada

1. Cada version conservada esta disponible desde su edicion, o desde la
   publicacion si no tiene marcador de edicion. Se declara latencia cero como
   hipotesis, no como recepcion observada. El contraste inicial omite las
   versiones editadas y registra el motivo, sin retroceder su contenido.
2. Un sticker reconocido por sus bytes como BUY/SELL GOLD, o un texto con
   encabezado inmediato GOLD NOW, puede iniciar una oferta. Se reutiliza el
   parser existente con encabezado estricto; SCALP/SCALP TRADE/SCALPTRADE son
   prefijos admitidos. Seis etiquetas semanticas de snapshots ya revisados
   cubren variantes literales, sin incorporar sus emparejamientos manuales.
3. La primera forma disponible abre la oferta. Una forma opuesta, sticker y
   texto, puede ser su unico complemento si coinciden direccion y origen,
   estan a no mas de cinco minutos tanto en disponibilidad como en publicacion,
   y no hay otra oferta elegible intermedia. Cinco minutos procede del catalogo
   previo; no se optimiza contra rentabilidad. Un complemento consume el hueco.
4. Dos textos distintos o dos stickers distintos son ofertas diferentes,
   aunque tengan precios iguales o respondan al mismo mensaje. AGAIN puede
   ser el primer texto de un nuevo ciclo de sticker; despues de un complemento
   completo, otro texto es una oferta adicional. La politica de posiciones
   propias decidira despues si puede ejecutarse; no se importa el estado del
   proveedor para decidirlo.
5. Una edicion posterior de un mensaje ya asignado no reabre, borra ni cambia
   retroactivamente su entrada. Un cambio de direccion se registra como
   incidencia, sin reescribir el pasado. Si una version inicial era contexto
   y una posterior es una orden clara, solo esta ultima puede disparar.
6. Un sticker desconocido interrumpe el emparejamiento. Varias ofertas en el
   mismo segundo se tratan como lote simultaneo: no se emparejan dentro ni a
   traves de ese lote. Las versiones incompatibles de un mismo ID en ese
   instante se bloquean. No se inventa el orden real a partir del ID.
7. Los reenvios conservan su origen y no se unen implicitamente con mensajes
   de otro origen. Los comentarios, planes de zona, simbolos desconocidos y
   condiciones sin orden GOLD autosuficiente se conservan sin abrir entradas.
   Stops, objetivos y resultados del proveedor no gobiernan nuestra estrategia.

Las reglas son una hipotesis de trabajo retrospectiva. Ser reproducibles y
respetar los prefijos temporales no demuestra que coincidan con la intencion
del proveedor ni con la secuencia original que ya no esta en los JSON.

## Por Que No Son Las 736 Agrupaciones Anteriores

De los 736 grupos retrospectivos, **590 conservan exactamente sus miembros**.
145 se separan en varias activaciones y uno se une a otro grupo. No se ajusta
el algoritmo para forzar el total anterior. Los 146 grupos que cambian tienen
un desglose de componentes, fechas, nuevas identidades y hechos explicativos:
[diagnostico de relaciones](../../runtime_data/canal1_history_20260913/entry_stream_v3_relationship_diagnostics.json).

Hechos que pueden coincidir en un mismo grupo: 124 exceden cinco minutos entre
disponibilidades, 48 tienen otras ofertas elegibles entre componentes, 12
incluyen disponibilidad simultanea, ocho contienen varias piezas de la misma
forma, cuatro mezclan origen y uno excede cinco minutos entre publicaciones.
Dos grupos participan en el cruce entre agrupaciones antiguas. No son categorias
excluyentes ni recuentos de nuevas senales reales.

Ejemplos que impiden confundir la hipotesis con el flujo original:

- 17142/17143/17144: el texto 17143 se publico antes que 17144, pero su version
  conservada esta fechada despues. El algoritmo puede unir 17144 al sticker
  y tratar 17143 despues. No utiliza la version final de 17143 antes de su fecha.
- 18069/18070/18071: dos componentes estan disponibles en el mismo segundo.
  Se conservan como ofertas simultaneas, sin inventar su orden ni su union.
- 20303/20304: el complemento llega fuera de los cinco minutos. Una explicacion
  posterior del proveedor no permite emparejarlo retroactivamente.
- 22158/22160: el sticker generico sin GOLD no establece el simbolo. El texto
  GOLD es quien puede iniciar la oferta, nunca el sticker anterior por inferencia.

De las 881 activaciones, 645 comienzan con una version editada y 236 con una
version sin editar. En 152, la publicacion precede en mas de cinco minutos al
disparador conocido; 52 superan una hora y **24 superan un dia**, hasta 714482
segundos. Esos retrasos describen los snapshots conservados, no la latencia
real de Telegram. Este escenario **no es una reproduccion de entradas iniciales**
y no debe usarse para afirmar la rentabilidad real del canal. El escenario
sin editar tampoco es automaticamente representativo: su disponibilidad varia
por mes. Cualquier estudio posterior debe mantener separados estos supuestos,
mostrar los retrasos y conservar los casos no evaluables.

## Meses Y Ventanas

Escenario de primera version conocida; septiembre es parcial:

| Mes | Activaciones | Cobertura 20 min | Cobertura 4 h |
| --- | ---: | ---: | ---: |
| Enero | 87 | 86 | 78 |
| Febrero | 89 | 86 | 73 |
| Marzo | 132 | 132 | 119 |
| Abril | 112 | 109 | 90 |
| Mayo | 101 | 100 | 82 |
| Junio | 121 | 118 | 94 |
| Julio | 89 | 86 | 65 |
| Agosto | 91 | 88 | 73 |
| Septiembre | 59 | 58 | 50 |
| Total | 881 | 863 | 724 |

Se recalculan las ventanas de 56 dias de desarrollo, 14 de comprobacion,
avance quincenal y purga de cuatro horas. El escenario principal tiene 173
entradas de calentamiento y 708 en una comprobacion. El contraste inicial
tiene 55 y 185. Cada escenario conserva 14 comprobaciones completas y una
parcial. Ninguna es OOS nuevo: ya hemos consultado el historial.

Se conservan **2242 verificaciones de cobertura**: 1972 reutilizan exactamente
la misma fecha/horizonte y contrato ya comprobados; se miden 268 ventanas
nuevas. Dos filas adicionales comparten ventanas nuevas ya calculadas. No se
reutiliza un resultado por parecido, por ID antiguo o por pertenecer al mismo
dia. Los limites de oro y FX no se amplian para mejorar la cobertura.

## Verificacion Y Versiones

**246 pruebas relacionadas pasan**, incluidas 36 del flujo nuevo. Se obtuvieron
regresiones fallidas antes de corregir los falsos comentarios de entrada,
versiones simultaneas, nombres de sticker entre exportaciones y prefijos SCALP.
No se modificaron el parser live, listener, motores ni contratos monetarios.

En el historial real se verifican 18 prefijos mensuales entre los dos escenarios:
anadir el resto del ano no cambia las entradas ni decisiones anteriores al
corte. Son 16 prefijos no vacios y dos cortes iniciales vacios. Hay 5500 estados
de contenido canonicos procedentes de 5494 mensajes; dos versiones de contexto
caen despues del limite de disponibilidad y se conservan como no disponibles.
Los 1453 IDs direccionales antiguos coinciden exactamente con los asignados
en el escenario principal. Los 10988 estados por mensaje cubren ambos escenarios.

El puente al tipo `CausalSignal` existente conserva las 1121 activaciones de
ambos escenarios, sin gestion del proveedor ni recepciones inventadas.
Es compatibilidad de entrada, no autorizacion para saltar la admision de precios,
ejecucion o dinero. Se ejecutaron **cero simulaciones de estrategias**.

Comprobacion final independiente: **1035 fuentes y 12 resultados**, sin diferencias
de hash. Identidad vigente:
`a4599f89a17d5ab1bff5b24121cdced5575b09ab0e18734a079db645c4fac562`.
Diagnostico adicional de relaciones:
`42126bb1f166b04fb8efe97ab82e5dd5adf92510c907f5c89fb7b48a6cc7d5e6`.

- `entry_stream_v1` queda rechazado: trataba diferencias de nombre/formato del
  archivo exportado como versiones contradictorias. Se conserva el resultado,
  su codigo y [la incidencia](../../runtime_data/canal1_history_20260913/entry_stream_v1_rejected.json).
  No se utilizo para medir estrategias.
- `entry_stream_v2` queda sustituido: faltaba reconocer el encabezado literal
  Scalptrade de 18144. Se conserva su codigo y [la sustitucion](../../runtime_data/canal1_history_20260913/entry_stream_v2_superseded.json).
  Corregirlo no cambia los 881 disparadores, pero si asigna ese complemento.
- Los JSON originales, la extraccion raw, el universo de 736 grupos y su
  auditoria de cobertura permanecen intactos. Ninguna version previa se borra.

Comando de pruebas:
`py -3.14 -m pytest -q tests/test_dubai_entry_stream.py tests/test_dubai_annual_coverage.py tests/test_dubai_annual_universe.py tests/test_dubai_export_catalog.py tests/test_telegram_exports.py tests/test_strategy_study.py tests/test_study_fx_interval_contract.py`.
Python 3.14.2. Importar estas herramientas no carga MetaTrader5, listener ni main.

## Archivos Y Siguiente Paso

- [Protocolo aplicado](../../runtime_data/canal1_history_20260913/entry_stream_v3/protocol.json).
- [Resumen completo](../../runtime_data/canal1_history_20260913/entry_stream_v3/summary.json).
- [Disparadores con su reloj y prueba de contenido](../../runtime_data/canal1_history_20260913/entry_stream_v3/triggers.jsonl).
- [Decisiones por version disponible](../../runtime_data/canal1_history_20260913/entry_stream_v3/decisions.jsonl).
- [Correspondencia con cada grupo anterior](../../runtime_data/canal1_history_20260913/entry_stream_v3/legacy_lineage.jsonl).
- [Cobertura y bloqueos por disparador](../../runtime_data/canal1_history_20260913/entry_stream_v3/coverage.jsonl).
- [Manifiesto y hashes](../../runtime_data/canal1_history_20260913/entry_stream_v3/manifest.json).

La definicion temporal y su puente estan terminados para este experimento.
Siguiente: comprobacion pequena y fija del motor con este flujo, declarando
ejecucion, volumen, costes y limites de posiciones propias antes de evaluar.
No iniciar busqueda masiva ni clasificar estrategias a partir de este archivo.
La ausencia de condiciones monetarias verificadas no obliga a inventarlas:
cualquier diagnostico hipotetico debe conservar sus supuestos como tales.

Trabajo local y sin publicar, sin ordenes, arranque de MT5, reinicio del bot,
acceso a VM, commit ni push. La limpieza de las dos copias temporales de
autenticacion sigue pendiente del bloqueo documentado; no se vuelve a intentar.
