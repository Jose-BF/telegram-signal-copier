# Seguimiento Simple De La Calculadora

Ultima actualizacion 12/09: [primera busqueda pequena](../audits/2026-09-12-small-search-results.md).
Fallos de dinero conocidos reparados localmente; 5263 pruebas aprobadas.
Primera familia autorizada: 12 combinaciones por canal, sin refuerzos y con
tamano de referencia 0.01. Desarrollo terminado: solo una Dubai apenas positiva
(+3.77 EUR, +0.77 con coste adicional, drawdown de cuenta 31.36); ninguna Gold.
No son candidatas aprobadas. Los datos incompletos permanecen visibles y los
periodos ya usados no se llaman validacion nueva. Comprobaciones terminadas:
664 casos coinciden entre motores y cliente serial por cesta; 124 resultados
iniciales reproducidos exactamente. La unica Dubai pierde en desarrollo con
esperas de 10 y 60 s. Decision: ninguna propuesta para aprobar; primera familia
cerrada y revision conjunta antes de ampliar. Sin publicacion, ordenes ni
cambios del bot. Sigue pendiente la validacion de dinero, capital y cliente
global aplicable, no se declara toda la base certificada.

Ultima aclaracion 12/09: [por que difieren realidad y simulacion en 2774](../audits/2026-09-12-2774-why-reality-and-simulation-differ.md).
El control -237.88 sigue usando 250/250 ms, no la grabacion de tiempos reales.
Una nueva ejecucion lo reproduce; misma funcion monetaria y 14467 ticks
reconstruyen -147.50 con las posiciones reales. La diferencia nace en las
operaciones, no en esa valoracion. Reconstruir hechos ya es posible; generar
la misma secuencia con un modelo independiente sigue pendiente. No tenemos
todos los tiempos internos del sistema. Sin cambios de motor ni bot.

Actualizacion mas reciente 12/09, medicion de incertidumbre:
[resultado y logica](../audits/2026-09-12-execution-uncertainty-results.md).
530 llamadas enviadas medidas; mediana de apertura 0.711 s, maximo 57.125 s.
40 precios de entrada conciliados con hechos nativos. 168 fusiones aclaran
que intenciones distintas de SL/TP no implican siempre llamadas separadas.
La simulacion no es siempre peor: 2777 llega a -121.57 EUR reconstruidos
frente a -98.34 simulados. Mediana del error +0.02 EUR no protege esa cola.
No hay margen estadistico certificado; faltan reparaciones y validacion
posterior. Bloque local de analisis, sin cambiar motor, bot ni publicar.

Actualizacion 12/09, correccion local del cliente:
[resultado y limites](../audits/2026-09-12-client-execution-results.md).
Cola compartida por cesta, decisiones de tanda pendientes y reloj de precio
separado implementados como perfil experimental del motor de referencia.
29 pruebas nuevas pasan; contraste fijo de 19 senales, 76 evaluaciones
reproducidas y 95 marcados monetarios independientes. La cola con los tiempos
anteriores NO resuelve 2774: minimo -237.88 frente a -147.50 EUR observado.
No se adopta ninguna variante. Faltan tiempos/contencion representativos,
interaccion entre tareas, SL/TP separados y motores independientes. Los
huecos monetarios siguen visibles y pendientes de reparacion. Suite
completa final: 5122 aprobadas. No hay publicacion, cambio del bot ni busqueda.

Actualizacion 12/09: [experimento de tiempos y transferencia](../audits/2026-09-12-timing-transfer-results.md).
96 pares de tiempos sobre las diez senales del 10/09, dos selecciones
congeladas y contraste en siete senales del 09/09 y dos del 11/09, sin
eliminar casos. Un ajuste casi iguala el minimo de 2774 (-148.09 frente
a -147.50 EUR), pero cierra otro refuerzo y empeora otras senales/dias.
El ajuste por jornada tampoco mejora el contraste. No se adopta ninguno.
TP real confirmado antes del primer cruce; cierre nativo 46 ms despues.
La espera real por si sola tampoco reproduce el precio nativo del fill.
960 + 27 evaluaciones reproducidas, 57 casos contrastados en tres motores,
57 recorridos monetarios recalculados y 146 pruebas focales aprobadas.
Se conservan huecos FX y resultados parciales. Sin correccion publicada,
busqueda de estrategias, ordenes, cambios del bot ni acceso a la VM.

Actualizacion 20:02 Madrid: [hipotesis del minimo perdido entre consultas](../audits/2026-09-11-drawdown-sampling-investigation.md).
Confirmada limitacion de muestreo: el resumen se actualiza cada 5 s y
guarda -146.19 EUR, aunque las consultas frecuentes llegaron a -147.34 EUR.
Recalculo de las posiciones reales sobre 14024 ticks: -147.50 EUR, no
-237.88 EUR. La omision del resumen es 1.31 EUR; los restantes 90.38 EUR
siguen explicados por exposiciones/precios de entrada diferentes. Cinco
velas nativas M1 corroboran la cinta; maximo hueco entre ticks cerca del
minimo 94 ms. El minimo historico reconstruido no era el valor muestreado
del bot. Trabajo de lectura local, sin cambios operativos ni publicacion.

Actualizacion 19:32 Madrid: [resultado del control nativo MT5](../audits/2026-09-11-native-mt5-control-results.md).
Dia 10 ejecutado en el probador con ticks reales y 22 posiciones en ambos
escenarios fijados. Cesta 2774: minimo -234.62 EUR sin retraso y -237.77 EUR
con 250 ms, frente a -237.88 EUR del simulador propio y -147.50 EUR con fills
realmente observados. No es drawdown de cuenta. Neto nativo 83.57/85.21 EUR;
salidas TP a mejor precio explicitan una diferencia frente al TP exacto.
No certifica ejecucion ni reproduce las esperas reales del puente Python.
Corregida finalizacion prematura del EA de control con regresion nativa;
primera version y resultados invalidos conservados, produccion sin cambios.
44 salidas conciliadas al centimo; 2801 conserva once ticks FX incompletos.
Dia 11 pendiente: MT5 excluye el dia actual. No se desplazan fechas ni se
programa otra tarea. MT5 local reabierto, autotrading apagado y cuenta sin
posiciones/ordenes; VM sin tocar. Trabajo local, sin publicacion ni busqueda.

Actualizacion 18:54 Madrid: [control nativo MT5 en preparacion](../audits/2026-09-11-native-mt5-control-preparation.md).
El usuario pide repetir los dos controles dentro del probador nativo, sin
buscar parametros. Doce senales y cuatro series de precios preparadas y
verificadas; 437 fuentes anteriores intactas. Una sonda matematica ejecuto
codigo en MetaTester, pero no se ha simulado la estrategia ni comparado su
riesgo. La copia aislada no completo la inicializacion de la carga de datos.
Pendiente permiso para utilizar el MT5 habitual solo para estas pruebas, con
operativa automatica desactivada y sin tocar la VM. Falta adaptar/verificar
el robot de control y ejecutar ambos ensayos. No hay resultado nativo nuevo,
certificacion, publicacion, busqueda masiva ni proceso experimental activo.

Actualizacion 11/09, investigacion de causas: [diagnostico de ejecucion](../audits/2026-09-11-execution-divergence-root-cause.md).
Demostradas esperas reales de 31/57 s, interferencia de modificaciones,
bloqueo del proceso en el puente MT5 y diferencia entre entradas comprometidas
por un tick y reevaluadas tras el acuse. Los 90.38 EUR de diferencia de riesgo
de 2774 quedan descompuestos por posiciones; no son un fallo de conversion.
Detectados tambien defectos generales: decisiones/datos monetarios desconocidos
aceptados sin bloqueo en los tres motores, y caducidad/cierre no revalidados
entre patas live. 2801 tiene once ticks FX fuera de contrato mientras hay
exposicion: riesgo parcial aunque el motor no lo advertia. 158 pruebas
focales aprobadas y contraejemplos retenidos, no arreglos ni certificacion.
Fase 2 vuelve a tener defectos concretos pendientes y fase 3 sigue abierta.
No se ajusto el simulador a una senal ni se cambio produccion. Sin publicacion,
busqueda o programacion nueva. El cero de bloqueos anterior no prueba cobertura.

Actualizacion 09:50 Madrid: [prueba de la jornada de ayer](../audits/2026-09-11-yesterday-gold-retrospective.md).
Diez Gold NOW del 10/09, incluidas cuatro sin entrada. Caso base: 22
posiciones y +78.77 EUR, iguales en numero, volumen e importe por senal a
MT5. 120 evaluaciones y 120 de reproduccion; tres motores concordantes y
dinero verificado. Pero NO hay paridad completa de ejecucion: en 2774 los
refuerzos difieren hasta 76.734 s y 6.51 en precio; incidencia retenida para
revision conjunta. El peor saldo simulado de una cesta es -237.88 EUR,
realizado mas flotante, no drawdown de cartera. Sin ajuste del motor ni
seleccion de escenarios. Corregida en el auxiliar la omision de las patas
adicionales; comparacion autoritativa `comparison_v3.json`, no la primera.
La prueba esta hecha; sigue abierta la fidelidad de esos refuerzos.
Fuentes originales intactas, trabajo solo local y programacion cancelada.

Actualizacion 09:19 Madrid: [prueba retrospectiva completada](../audits/2026-09-11-first-two-gold-retrospective.md).
Desde mensajes/precios, sin fills reales como entrada: los tres motores
reproducen los dos disparadores y el caso base de una posicion/TP por senal,
+3.44 EUR en conjunto. 24 evaluaciones y otras 24 de reproduccion; cero
bloqueos/desacuerdos y dinero/timing verificados por separado. Sin cambio
del motor. Con 1 s de fill se mantiene el beneficio pero aumenta el riesgo;
con 5 s aparece una pata mas: tres posiciones y +6.02 EUR, con mas perdida
flotante. No se elige ese escenario. Este control retrospectivo queda
completado; no certifica todo MT5 ni el historico. Las fuentes preservadas
siguen iguales, el bot no se cambio y no hay programacion reactivada.
El contraste independiente pendiente citado debajo ya tiene este resultado.

Revision puntual 08:43 Madrid: [primeras dos Gold de hoy](../audits/2026-09-11-first-two-gold.md).
2816 BUY y 2824 SELL: una posicion de 0.04 por senal, ambas TP +1.72 EUR;
total +3.44 EUR nativo y recalculado. Trazabilidad correcta, un cierre
contable por senal y observador sin flotante residual. Aviso transitorio
de conciliacion de 2824 resuelto y conservado. Respuesta del cliente
719 / 1500 ms; las esperas previas de la regla 555 no son latencia.
Es contabilidad observada y diagnostico, no una nueva simulacion independiente
ni cierre universal del modelo. Se mantiene el trabajo sobre supuestos y
sensibilidad para historicos sin ejecuciones reales. Bot sin cambios y
programacion cancelada; la consulta puntual ya termino y su tarea se elimino.

Actualizacion08:18Madrid: [trabajo de ejecucion y criterios](2026-09-11-execution-review-contract.md).
Medidas existentes resumidas (16 peticiones de apertura), sensibilidad de
cuatro escenarios ejecutada con reglas fijas y capacidad de un/tres dias
medida: 204 evaluaciones, tres motores concordantes, cero busqueda. El trabajo
offline avanza sin esperar mas operaciones. No se certifica latencia futura ni
escala anual. Programacion sigue cancelada; no hubo cambio del bot.

Estado del 11/09/2026, tras la aclaracion del usuario de las 07:40 Madrid.
Objetivo: JSON de senales + precios historicos + reglas propias -> operaciones
simuladas y resultado. No reconstruir cada operacion real del proveedor.

## Programacion Cancelada

El usuario aclara que las revisiones programadas eran solo para ayer.
Se elimino la automatizacion de seguimiento de Codex. Tambien se retiraron
las dos capturas auxiliares de hoy, 14 y 17 Madrid, antes de ejecutarse.
No recrearlas ni sustituirlas por otra automatizacion sin peticion nueva.
El bot y sus registros normales no se tocaron. No hay captura, extraccion
o comparacion automatica de este control pendiente de ejecucion.

Recibo: `runtime_data/calculator_delivery_20260910/forward_friday_v3/cancelled_by_user.json`,
05:40:42 UTC, ambas Ready y nunca ejecutadas antes de retirarlas; archivos
conservados. SHA256 `38d710fa6553f22a454cf199892020fd9a270138f3429a598edaaa194be1dafb`.
La programacion documentada anteriormente es historia, no estado vigente.

## Lo Que Se Ha Conseguido

| Paso | Que se buscaba | Resultado retenido |
| --- | --- | --- |
| 8-9 septiembre: datos | Identificar senales, evitar duplicados y no usar ediciones futuras como informacion inicial | Catalogos y correcciones verificados; despues se adapto la lectura de los cuatro JSON. No todo el historico tiene precios y condiciones admitidos. |
| 8-10 septiembre: motor | Entradas, protecciones, cierres y dinero calculados correctamente bajo reglas declaradas | Controles conocidos y tres motores concordantes para el dominio inicial. No prueba realismo universal de MT5. |
| 9 septiembre: observacion | Relacionar solicitudes, ejecuciones y respuestas; recuperar evidencia de conversion a EUR | Siete aperturas conciliadas; fallo de espera FX reproducido y corregido. La ventana inicial no valio como validacion independiente limpia. |
| 10 septiembre: recorrido completo local | Ejecutar reglas propias desde mensajes/precios y conservar resultados reproducibles | Recorrido y conexion al buscador entregados; 141 evaluaciones reproducidas sin desacuerdos. No hubo busqueda de ganadoras. |
| 10 septiembre: dinero real | Comprobar que los importes calculados coinciden con MT5 | 19 posiciones y luego otras 15 conciliadas, todas coincidentes. No son 34 senales distintas; tampoco prueba que entradas alternativas sean exactas. |
| 10 septiembre: control natural independiente | Simular sin copiar los fills reales y comparar despues | Sin cierre: problemas de recogida/preparacion, y cero Gold elegibles en la ultima ventana. Las operaciones Dubai no sustituyen esa muestra Gold. |
| 11 septiembre: arreglos operativos | Evitar cierres duplicados y flotante residual del observador | Commit d9037c5c publicado y verificado activo, 3191 pruebas de entrega minima. Sin cambio de estrategia/cuenta/lotes. La investigacion sigue local. |

## Las Cuatro Comprobaciones

Son cuatro grupos explicativos, no cuatro campos nuevos ni cuatro situaciones
raras. Hay evidencias parciales previas; no empezamos las cuatro desde cero.

| Grupo | Que tiene que poder comprobarse |
| --- | --- |
| Entradas | Cada senal lleva a entrar, esperar o no entrar por las reglas previstas, sin duplicados ni desapariciones. |
| Ejecucion | Solicitud, aceptacion/rechazo, precio, volumen y tiempos se enlazan con la operacion correcta. |
| Gestion y cierre | Objetivos/stop y modificaciones realmente observados, causa de salida y cierre coherente. Sin sumar dos veces un cierre ni conservar dinero flotante sin posiciones. |
| Dinero | Beneficio/perdida y costes de las operaciones observadas coinciden con MT5, con conversion a EUR correcta. |

Senales Gold NOW ordinarias que entren y cierren pueden aportar evidencia
a los cuatro grupos. Para verificar una modificacion hay que observarla;
una senal sin cambios de proteccion no demuestra ese camino. Rechazos,
cancelaciones y carreras excepcionales se cubren tambien con pruebas
controladas; no hay que esperar todos los casos raros en vivo.

## Criterio De Cierre Y Error De Comunicacion

El control preparado exigia al menos dos senales Gold elegibles y dos cierres
simulados/observados, fuentes completas y las coberturas requeridas. Es un
minimo para producir una revision util, no una muestra estadistica suficiente
para certificar el modelo. Cancelar las tareas no ejecuta ni retima ese control.

Hay una carencia de preparacion distinta de la muestra: el perfil conserva
`approved_tolerances=null`. El comparador produce `evidence_ready_for_review`,
no una aprobacion cuantitativa automatica. Decir que solo faltaba esperar
operaciones para tenerlo todo listo fue demasiado categorico.

Para cerrar el contraste del dominio elegido hace falta evidencia integra,
calculos/reglas correctos y diferencias materiales explicadas; los margenes
aceptables de ejecucion deben fijarse antes de evaluar una muestra de validacion.
Eso es trabajo de definicion pendiente, no otro dato a esperar del mercado.
Si se usan casos para ajustar hipotesis, son calibracion, no validacion intacta.
No elegir limites despues para hacer pasar un resultado ni certificar el
instante interno exacto del servidor a partir de una observacion posterior.

Hoy puede obtenerse evidencia util con actividad normal, pero no se promete
el cierre automatico de todo el proyecto. Antes de buscar estrategias siguen
los pendientes conocidos: preparar el historico elegido y sus supuestos,
medir tiempo/memoria a escala y acordar el experimento. No requieren seguir
recogiendo operaciones live indefinidamente. No hay nueva busqueda autorizada.

## Evidencias

- `../audits/2026-09-08-gold-foundation.md`.
- `../audits/2026-09-09-afternoon-observation-repairs.md`.
- `../audits/2026-09-10-own-rule-integration.md`.
- `../audits/2026-09-10-collection-review-1515.md` y `2026-09-10-night-review.md`.
- `../audits/2026-09-11-operational-release.md`.
- `tools/prepare_simulator_forward.py`, `tools/compare_simulator_forward.py`
  y el perfil congelado de viernes v3, leidos sin modificar el experimento.
