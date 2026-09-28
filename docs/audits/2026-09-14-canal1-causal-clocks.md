# Canal 1: Correspondencia, Relojes Y Gestion

Investigacion local autorizada tras la objecion del usuario al alcance del
cribado anual. No se cuestiona la honestidad del proveedor ni se presupone
rentabilidad. Las campanas anteriores se conservan cerradas.

## Correspondencia Verificada

Archivo `runtime_data/canal1_history_20260913/causal_clock_audit_v1`.
Identidad `cc9b117643ec81786a790df8c6bfe3414b08cd1ed72aa87981ab2d6ef86d1f8c`.
Verificacion fresca de fuente, compilador, identidades, gestion y resumen.

- Fuente original: 243205 lineas, 4221 recepciones raw del canal 1 dentro de
  27/07-02/09, 1685 revisiones distintas, 94 activaciones recompiladas.
- Inventario anual retenido completo: 881 activaciones por version conocida
  y 240 por versiones sin editar. No equivalen a 1121 senales independientes.
- Correspondencia principal: 84 parejas comparables, 77 mensajes directos
  y 7 componentes de grupo. Ocho primeras versiones recibidas como edicion
  y dos mensajes sin entrada exportada permanecen como no comparables.
- 73 parejas principales tienen marca de edicion en la exportacion y la
  misma direccion que la recepcion original. Ninguna pareja se ha obtenido
  retrotrayendo contenido editado a su publicacion. Los raw carecen de hash
  de medios: igualdad de direccion/identidad no prueba igualdad de bytes.
- Contraste sin editar: 11 parejas. No representa el ano ni es otra muestra
  independiente de las parejas principales.
- 84/94 recepciones originales se producen dentro de cinco segundos; diez
  superan cinco minutos, incluyendo recuperaciones de mensajes de dias antes.
- Persisten 149 diagnosticos del compilador y seis revisiones direccionales
  no compiladas. No se alteran los relojes para darles entrada.

Ejemplos concretos (UTC): 21078, publicado 27/07 00:07:51, recibido
00:07:51.676, activacion exportada 00:09:21. 21270, publicado y recibido
06/08 13:52:32, activacion exportada 09/08 21:10:58. 21497, publicado
21/08 14:35:40 y exportado con esa hora, pero recibido el 23/08 19:17:47.
Estos casos muestran que ninguno de los dos relojes representa por si solo
la entrada ejecutable original para todas las senales.

## Controles Fijos

Verificado: `paired_clock_controls_v2`, identidad
`1099762fa4131c219bd2df0faf6c7c0f62a2cb5690a5418639f352ae53133056`.
Misma identidad y mismos archivos de
cotizaciones para comparar recepcion raw y reloj exportado. 179 casos de
reloj unicos; cuatro reglas, 0.01 lotes, una posicion aislada, caducidad 3 min,
horizonte 20 min, perfiles de referencia y desfavorable, tres motores.

Reglas: mercado SL10/TP5/15min; demora90/SL8/TP12/5min;
recuperacion2+1/SL15/TP8/15min; impulso5/SL10/TP10/15min.
Las dos finalistas antiguas mantienen exactamente sus huellas de estrategia.
La pista nocturna se normaliza de 0.02 a 0.01 lotes para este diagnostico.

El resumen separa mensaje directo, grupo y tramos de retraso raw fijados
antes de resultados. Sumas solo sobre las mismas parejas completas; no
entrada vale cero, datos ausentes no. No hay cartera, capital, certificacion
de euros reales, muestra nueva OOS ni promocion operativa.

Resultado: 4152 evaluaciones, 50754216 visitas de cotizacion, 267.26 segundos.
1432 filas: 886 simuladas, 498 sin entrada y 48 con datos bloqueados.
No hay discrepancias entre motores. De 84 parejas principales, 79 tienen
ambos recorridos completos: 72 directas y siete de grupo. Los cinco pares
incompletos siguen en el resumen. Dos relojes exportados caen fuera de
cobertura inicial (21270/21306), dos recepciones tardias caen en domingo
(21497/21502) y 21543 tiene un hueco de cotizaciones en ambos relojes.

Contraste directo, mismas 72 identidades, perfil desfavorable y 0.01 lotes.
Euros hipoteticos, antes de comision/swap, no balance de cuenta:

| Regla | Recepcion original | Hora exportada | Entradas original/exportada |
| --- | ---: | ---: | ---: |
| Mercado | -5.56 | -39.70 | 72/72 |
| Demora 90 s | +7.62 | -19.84 | 72/72 |
| Recuperacion 2+1 | +48.51 | +12.17 | 32/29 |
| Impulso 5 | +20.43 | -11.00 | 9/10 |

Con perfil de referencia, recuperacion: +64.62 frente a +28.36; demora:
+27.95 frente a -1.94. El efecto del reloj se demuestra en esta muestra
pareada, no se extrapola a todo el ano ni prueba que explique toda perdida.
La recuperacion no formaba parte de las 106 reglas con sus parametros
exactos: el cribado nocturno no era un rechazo de esta regla.

Sensibilidad descriptiva posterior, sin nuevas reglas ni seleccion: recargo
de 10 EUR por lote cerrado, igual al supuesto del cribado anterior, ademas
de la ejecucion desfavorable. No se afirma que sea una comision observada.

| Regla | Neto raw con recargo | Neto exportado con recargo |
| --- | ---: | ---: |
| Mercado | -12.76 | -46.90 |
| Demora 90 s | +0.42 | -27.04 |
| Recuperacion 2+1 | +45.31 | +9.27 |
| Impulso 5 | +19.53 | -12.00 |

Recuperacion raw: 20 entradas positivas y 12 negativas, peor senal -13.15.
Suma por mes de publicacion: julio +9.07, agosto +38.25, septiembre -2.01
(solo 01-02/09 del corpus original). Al quitar las tres mayores ganancias,
la suma queda +25.32; es sensibilidad descriptiva, no otra estrategia.
La demora queda fragil al recargo; impulso tiene solo nueve ejecuciones.
Estos recuentos no sustituyen drawdown conjunto, margen o dependencia diaria.

Los extremos a 20 min de las mismas 72 identidades se conservan en
`inventory.json`: mediana favorable 5.00 unidades XAUUSD desde la primera
cotizacion raw, frente a 4.385 exportada; mediana adversa -5.71 y -5.245.
Son extremos posteriores conocidos, no objetivos garantizados ni dinero
realizable. La secuencia de stop/objetivo solo la evalua el motor.

Decision de investigacion: priorizar la recuperacion como referencia para
ampliacion y contraste, no como politica elegida. Sus fechas ya se usaron
para descubrirla; este contraste no crea una prueba nueva fuera de muestra.
No requiere que todos los meses salgan positivos, pero si conocer sus
perdidas, estabilidad, sensibilidad y coste de ejecucion.

## Gestion Observada

Inventario descriptivo de eventos del compilador existente, no ordenes
independientes ni fills. Se mantienen revisiones repetidas y la vinculacion
original; no se aplica esta gestion a los cuatro controles de reglas propias.
Los retrasos se miden desde la primera recepcion retenida e incluyen
recuperaciones tardias: no son una medida de latencia general de Telegram.

| Evento | Filas | Senales con evento | Filas posteriores a 20 min |
| --- | ---: | ---: | ---: |
| Actualizacion de niveles | 208 | 91 | 27 |
| Stop a equilibrio | 176 | 62 | 87 |
| Stop a precio | 18 | 10 | 5 |
| Cierre total | 2 | 1 | 2 |

91/94 senales tienen una primera actualizacion con SL: mediana 47.946 s
desde su recepcion, maximo 137.245 s. No estaba disponible desde el primer
instante. Mediana de todas las filas BE: 1190.6645 s; 36 posteriores a una
hora. No son 176 decisiones independientes: una misma instruccion puede
aparecer en varias revisiones retenidas.

Limitacion relevante: el horizonte de 20 min y la salida forzada de 15 min
no cubren toda la gestion observada del canal. Tampoco basta con copiar estos
eventos: 72 diagnosticos de gestion condicional, 25 de semantica no resuelta,
17 sin raiz resuelta, 30 incidentes de reloj y cinco stickers desconocidos
siguen visibles. No se inventan instrucciones ejecutables para cubrirlos.

## Fuentes Adicionales Localizadas

Inspeccion de identidades y relojes, sin nuevas simulaciones de estas filas:

- El mismo archivo fuente llega al 07/09 22:14:42 UTC. Hay 886 recepciones
  canonicas posteriores al 02/09 y 17 senales nuevas recompiladas: cinco
  del 03/09, ocho del 04/09 y cuatro del 07/09. Todas las primeras recepciones
  direccionales son sin editar y dentro de cinco segundos. Al compilar
  27/07-07/09 aparecen 111 senales. Las 94 antiguas no se sobrescriben.
- Antes del 27/07 hay 2650 recepciones desde el 05/06, de las que 2649 no
  tienen toda la identidad canonica nueva. Conservan etiqueta de canal,
  mensaje, hora de publicacion/recepcion, edicion y sticker. Faltan chat
  numerico y revision canonica; no se rellenan inventandolos.
- Inventario preliminar temprano: 143 IDs direccionales; 135 primeras
  recepciones sin editar y dentro de cinco segundos. Cruce de identidades
  con la exportacion: 134 comparables por hechos de mensaje/direccion,
  cuatro conflictos de reloj, tres ausentes y dos primeras revisiones
  editadas. Esto no admite aun los datos antiguos al motor ni acredita
  su chat numerico: requiere contrato separado y pruebas de trazabilidad.

La ausencia de la telemetria moderna no es por si sola prueba de que toda
la recepcion historica sea inutil. Tampoco permite reetiquetarla como raw
canonico. Auditar ese nivel de evidencia separado es una ampliacion concreta,
no otra optimizacion sobre las mismas 94 senales.

## Verificacion Y Estado

`py -3.14 -m pytest -q tests/test_dubai_clock_audit.py tests/test_dubai_paired_clocks.py tests/test_dubai_annual_dataset.py tests/test_dubai_entry_stream.py tests/test_request_quote_binding.py`

117 pruebas focalizadas iniciales pasan. Incluyen identidades, grupos, sentidos, relojes invalidos,
archivos/fuentes alterados, denominadores pareados y 48 evaluaciones
sinteticas de los tres motores. La suite anterior de 5602 pruebas corresponde
a la base compartida sin cambios, no se presenta como suite de estos modulos.

Incidente del coordinador v1: la serializacion ordenaba las claves de las
reglas/perfiles y la recomputacion cambiaba el orden de filas del resumen.
Se preservaron v1 y sus dos archivos fuente; prueba roja especifica y
correccion de orden determinista. Las 1432 filas de resultados de v1/v2
son exactamente iguales, no solo su P/L. v2 se recalculo completo y paso
verificacion fresca. 39 pruebas propias pasan, incluido recorrido integral
raw/export/cotizaciones/motores/archivo y controles de manipulacion.

Revision conjunta inicial: 5636 pasan y cuatro pruebas nuevas fallan porque
la coleccion completa carga modulos live en el mismo interprete. Se reutilizo
el aislamiento de pruebas ya existente en `test_strategy_study.case`, sin
cambiar la barrera offline del producto. Una nueva prueba introduce un
modulo MT5 ficticio y confirma que la auditoria sigue bloqueando antes de
crear salida. 49 pruebas focalizadas conjuntas pasan. Repeticion completa:
`py -3.14 -m pytest -q --disable-warnings`: **5641 pasan**, 634 avisos,
311.54 s, Python 3.14.2. `git diff --check` sin errores de espacios; conserva
avisos previos de finales de linea. Ningun cambio de motores compartidos.

Todo permanece local. Sin commit, push, VM, reinicio, cambio de politica live,
operaciones ni modificaciones del canal 2. El siguiente protocolo debe usar
la evidencia de este contraste para decidir universo y ampliar entradas,
SL/TP, duracion y gestores; no repetir indefinidamente el mismo cribado.
