# Canal 1: Recepciones Ampliadas Y Duracion

## Resultado

Se conserva el estudio original de 94 senales y se amplia la evidencia de
recepcion al periodo 05/06-07/09. Hay 258 identidades direccionales retenidas,
240 con primera recepcion respaldada y 236 de ellas dentro de cinco segundos
de publicacion. No son 258 operaciones ni un historial completo del canal.

Las ventanas con cotizaciones suficientes son 230 a 20 minutos, 226 a una
hora, 214 a dos horas y 186 a cuatro horas. Esto permite estudiar duraciones
mas largas sin imponer otra vez una salida universal a 15 minutos. No acredita
todavia una estrategia rentable ni una cuenta ejecutable.

## Evidencia De Entrada

| Formato observado | IDs | Primera recepcion respaldada | Dentro de 5 s |
| --- | ---: | ---: | ---: |
| Chat y revision canonica | 115 | 103 | 101 |
| Etiqueta de canal, sin chat numerico ni revision | 133 | 129 | 129 |
| Chat numerico observado, sin revision canonica | 10 | 8 | 6 |
| Total | 258 | 240 | 236 |

Se verificaron 7757 recepciones: 5108 canonicas, 2403 antiguas y 246 del
formato intermedio de julio. No se rellenan los campos ausentes. El validador
canonico previo permanece intacto. El formato intermedio admite el chat
efectivamente observado, no una revision inferida.

Diez primeras versiones retenidas son ediciones; ocho tienen publicacion
posterior a recepcion. Esos 18 IDs quedan en el inventario sin afirmar que
tenemos su entrada original. Otros cuatro originales son tardios y se
mantienen identificados. Cuatro mensajes con sticker de direccion desconocida
siguen en diagnosticos; no se les atribuye BUY o SELL.

La exportacion sirve para contraste por identidad, no para decidir con
informacion posterior si una entrada original existe. Una ausencia o un
cambio posterior de direccion no borra la recepcion observada. Tampoco se
retrotrae una edicion a su fecha de publicacion.

El compilador canonico ampliado produce 111 senales: 94 anteriores sin
cambios de entrada ni del prefijo de instrucciones, y 17 adicionales de
03/09, 04/09 y 07/09. Este contador no es el de originales respaldados:
el compilador conserva otro contrato de recepcion y sus diagnosticos.
Las exportaciones del periodo ya se usaron; no es OOS nuevo.

## Cobertura De Precios

| Horizonte | IDs retenidos | Respaldados | Ventana valida | Rapida y valida | Bloqueo de precios |
| --- | ---: | ---: | ---: | ---: | ---: |
| 20 min | 258 | 240 | 230 | 228 | 10 |
| 60 min | 258 | 240 | 226 | 224 | 14 |
| 120 min | 258 | 240 | 214 | 212 | 26 |
| 240 min | 258 | 240 | 186 | 184 | 54 |

A estos bloqueos se suman los mismos 18 originales no respaldados en cada
horizonte. Sus ventanas no se decodifican. El archivo contiene las 1032 filas
esperadas, sin convertir ausencias en operaciones de beneficio cero.

| Mes de recepcion | IDs | 20 min | 60 min | 120 min | 240 min |
| --- | ---: | ---: | ---: | ---: | ---: |
| Junio desde dia 5 | 86 | 82 | 82 | 77 | 67 |
| Julio | 73 | 65 | 63 | 60 | 50 |
| Agosto | 71 | 62 | 61 | 59 | 53 |
| Septiembre hasta dia 7 | 28 | 21 | 20 | 18 | 16 |

Entre los 65 dias con originales respaldados, tienen cobertura completa
59/55/49/34 dias, respectivamente. Si se exige cobertura de todos los IDs
retenidos, incluidos los de entrada no respaldada, son 54/50/44/31 de 66
dias. Ambos denominadores permanecen visibles: ninguno prueba que se haya
recibido todo lo publicado por el canal.

Se conservan Bid/Ask, conversion causal EURUSD, reloj declarado y umbrales
previos. A 20 minutos hay ocho ventanas con pausas de mercado superiores al
contrato y dos con problemas de extremos/conversion. A cuatro horas hay 51
con pausas y 25 que atraviesan rollover fuera del contrato monetario; los
motivos se solapan, no son operaciones adicionales. No se corrigen relojes,
rellenan ticks ni rebajan los limites para admitirlas.

## Verificacion

Inventario actual `runtime_data/canal1_history_20260913/receipt_inventory_v2`:
`52ea1c567b416cc256c4b3403c3e2c03f108b014a3fe8cf00b11d1fd4a58297b`.

Cobertura `runtime_data/canal1_history_20260913/receipt_coverage_v1`:
`2e2905debf0b0bce99f852a680a6667f4023ae8fc586bb3a73254030154af2a5`.

Ambos archivos se verificaron desde sus fuentes, no solo leyendo el resumen.
La verificacion de cobertura recompone las 1032 filas desde las cotizaciones.
Las 84 ventanas raw compartidas con `paired_clock_controls_v2` tienen todas
sus metricas exactamente iguales. No se compararon cohortes diferentes para
atribuir una mejora a un cambio de motor.

La primera version del inventario se conserva junto con copia comprobada de
sus dos fuentes de implementacion. No contemplaba aun el formato intermedio
de julio y lo clasificaba de forma demasiado restrictiva. v2 lo separa
explicitamente, con pruebas para que identidades modernas incompletas no se
cuelen como legado. No se eligio la correccion por un resultado de precios.

Presupuesto real de cobertura: 51284504 filas decodificadas, 61506749 puntos
de ventana, 61.73 segundos; cero evaluaciones de estrategia. Protocolo escrito
antes de decodificar precios y salidas inmutables. Las 54 pruebas focalizadas
pasan, incluidas verificacion independiente, matriz incompleta, resumen
adulterado aun con hashes recalculados y fuentes cambiadas.

Suite general posterior: **5687 pasan**, 634 avisos, 334.82 segundos,
Python 3.14.2. No hay procesos de calculo o pruebas pendientes. No se han
modificado motores compartidos ni contratos operativos en esta ampliacion.

Comandos reproducibles, desde la raiz del proyecto:

```powershell
py -3.14 tools/audit_dubai_receipts.py verify --output runtime_data/canal1_history_20260913/receipt_inventory_v2
py -3.14 tools/audit_dubai_receipt_coverage.py verify --output runtime_data/canal1_history_20260913/receipt_coverage_v1
py -3.14 -m pytest -q tests/test_dubai_receipt_coverage.py tests/test_dubai_receipt_inventory.py tests/test_dubai_annual_dataset.py --disable-warnings
py -3.14 -m pytest -q --disable-warnings
```

## Siguiente Contraste

El [plan actualizado](../development/2026-09-14-canal1-causal-discovery-plan.md)
fija el puente por nivel de evidencia y la matriz de las cuatro referencias
exactas en toda la muestra ampliada. Despues se congelan las rondas amplias
de entradas, stops, objetivos y gestion, con comparaciones cronologicas
moviles y riesgo visible. Esta auditoria no cuenta como estrategias nuevas.

La ampliacion no resuelve contexto previo, gestion condicional del proveedor,
riesgo de cuenta, capital, margen, swaps ni equivalencia del bot. Enero-mayo
continua disponible en JSON, con evidencia de version exportada distinta de
recepcion original. No se falsea esa distincion para sumar mas muestra.

Todo local: sin commit, push, VM, MT5, reinicio, ordenes ni cambio de politica.
Canal 2 intacto. No hay candidata nueva seleccionada ni promocion automatica.
