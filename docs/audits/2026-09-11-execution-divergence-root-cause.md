# Causas De La Divergencia De Ejecucion

## Conclusion

No hay una sola causa ni una estrategia nominal distinta que explique todo.
En produccion hubo esperas reales de ejecucion extraordinariamente largas
dentro de la muestra observada. El puente Python-MT5 convierte esas esperas
en pausas del proceso del bot. El simulador no representa ese recurso
compartido ni toda la semantica de las entradas encadenadas. Ademas, permite
aceptar decisiones y estadisticas monetarias con conversion incompleta.

Por tanto, **la produccion sufrio un incidente de ejecucion y el modelo
resulta insuficiente para representar sus consecuencias**. La simulacion
conservada sigue sus supuestos de 250 ms; eso no convierte esos supuestos
en una descripcion fiel de este entorno. No procede poner 57 segundos de
latencia ni cambiar la regla 555 para igualar una operacion pasada.

| Hallazgo | Evidencia | Alcance |
| --- | --- | --- |
| Respuestas de apertura de 31.375 y 57.125 s | Diario del bot, orden/deal nativos y registro independiente del terminal | Hecho observado; causa interna terminal/red/broker parcialmente sin identificar |
| Espera nativa que retiene el GIL de Python | Binario copiado de la VM, instrucciones de E/S, silencios del diario y control aislado | Defecto de aislamiento del proceso para esta version del puente |
| Aperturas y modificaciones no comparten la misma disponibilidad en el simulador | Control sintetico en los tres motores y secuencia real de 2774 | Carencia general del modelo de ejecucion |
| Una cotizacion compromete varias patas live; los motores vuelven a comprobar tras el acuse | Codigo live, prueba existente y controles BUY/SELL | Diferencia de semantica operativa, aunque coincidan los parametros |
| Datos monetarios desconocidos pueden producir resultados aparentemente completos | Tres contraejemplos, cada uno en los tres motores | Defecto general de admision; no explica la gran divergencia de 2774 |
| Cierre/caducidad no se revisan entre patas tras una espera | Controles con las fronteras de ordenes sustituidas por dobles de prueba | Defecto live latente; no observado como causa de 2774 |

La investigacion identifica mecanismos y conserva reproducciones. **No se
han reparado estos comportamientos, certificado el simulador ni modificado
la produccion.** Los datos usados ya son de diagnostico, no validacion intacta.

## Fuentes Y Versiones

El universo retrospectivo del 10/09 conserva diez Gold NOW: seis con entrada
y cuatro sin entrada. Las 22 posiciones, sus 44 deals y los +78.77 EUR
observados siguen conciliados. La igualdad del beneficio final no certifica
la trayectoria ni las decisiones contrafactuales.[^1]

La ampliacion de tiempos utiliza seis segmentos consecutivos ya conservados:
826218 eventos, 3384 eventos seleccionados para diagnostico, 524 intentos
MT5 y 521 llamadas con tiempos de envio/respuesta. Las 57 aperturas incluyen
parte del 09/09, el 10/09 y primeras operaciones del 11/09, no tres jornadas
completas. Se comprobaron hashes de manifiestos, archivos comprimidos,
contenido descomprimido y numero de filas, sin identidades repetidas.[^2]

Los precios del control son 380703 ticks XAUUSD y 82036 EURUSD. Las 22
cotizaciones usadas por el ejecutor al solicitar aperturas aparecen con
igual hora, Bid y Ask en esa cinta. Esto descarta una cinta de precios
completamente distinta; no demuestra que el bot observara cada tick.[^3]

Codigo local: rama `feature/gold-555-live-trial`, HEAD
`d9037c5c1ac3b91814ec73a00278e6d6c7b52aad`, con trabajo previo sin publicar.
El diario historico del 10/09 identifica `892bc33c8`. Los seis modulos
relevantes de estrategia, vigilancia, monitor, ejecutor, acciones pendientes
y evidencia de gestion no presentan diferencias entre esa version y los
archivos inspeccionados. No se mezcla el comportamiento del despliegue
operativo posterior con una supuesta estrategia nueva.[^2]

Los seis snapshots de estrategia concuerdan con el genoma congelado en 16
comprobaciones: entradas, tamanos, escalera, caducidad, objetivos, trailing,
proteccion de beneficio, salida temporal, proveedor e identidad. Esta es
igualdad nominal; las decisiones durante esperas requieren otro contrato.[^3]

El terminal y el binario se copiaron en modo lectura. La VM informa Python
3.11.9 y MetaTrader5 5.0.5735; se inspecciono `_core.cp311-win_amd64.pyd` sin
cargarlo ni ejecutarlo. El Python local de pruebas es 3.14.2 y su paquete
MT5 es distinto, por lo que no se uso como prueba de la version de la VM.
La fecha de modificacion del archivo remoto es anterior al episodio; no
existe una captura de su memoria historica que pruebe el modulo cargado
en aquel instante.[^4]

## Esperas Reales

Tiempos de llamada hasta respuesta, medidos con reloj monotono del cliente:

| Muestra | Llamadas | Mediana ms | Maximo ms | Mas de 1 s | Mas de 5 s |
| --- | ---: | ---: | ---: | ---: | ---: |
| Gold aperturas 10/09 | 22 | 827.5 | 57125 | 10 | 3 |
| Gold modificaciones 10/09 | 206 | 187 | 10156 | 13 | 1 |
| Dubai aperturas 10/09 | 15 | 422 | 12140 | 4 | 1 |
| Gold aperturas, tramo 09/09 | 15 | 1656 | 12312 | 9 | 3 |

Dubai incluye una apertura rechazada, no quince fills. Las observaciones
de una misma cesta no son muestras independientes y no estiman una
distribucion futura de latencia. Pero si demuestran que el problema no
es exclusivo de una pata de Gold ni una espera inventada por el simulador.[^2]

Descomposicion de las aperturas de 2774, normalizada a UTC:

| Pata | Solicitud cliente | Setup nativo | Fill nativo | Respuesta cliente | Llamada monotona ms |
| --- | --- | --- | --- | --- | ---: |
| Inicial | 12:32:51.869 | 12:32:52.457 | 12:32:52.458 | 12:32:52.599 | 734 |
| B1 | 12:33:46.430 | 12:34:02.879 | 12:34:17.676 | 12:34:17.806 | 31375 |
| B2 | 12:34:28.041 | 12:34:33.700 | 12:34:34.723 | 12:34:34.891 | 6844 |
| B3 | 12:34:38.010 | 12:35:24.507 | 12:35:34.867 | 12:35:35.132 | 57125 |
| B4 | 12:35:35.160 | 12:35:36.164 | 12:35:36.570 | 12:35:37.055 | 1906 |

La duracion monotona y la resta de horas difieren hasta 11 ms en esta tabla;
se conservan ambas mediciones, sin atribuir precision inexistente al reloj.
En B3, 46.497 s transcurren antes del setup nativo, 10.360 s entre setup y
fill, y 0.265 s entre fill y respuesta. En B1 son 16.449, 14.797 y 0.130 s.
No son simplemente 57 o 31 segundos de demora al escribir el diario.[^2]

El terminal registra B1 como completada en 31373.108 ms y B3 en
57122.054 ms. Sus comienzos/finales coinciden con el cliente dentro de
3 ms en los cuatro refuerzos auditados. Entre B1 y B2 tambien registra una
modificacion de 10150.949 ms; entre B2 y B3 otra de 3078.390 ms.[^5]

El reloj del log del terminal es Madrid, UTC+02; el campo nativo conservado
usa el desfase previamente acreditado de 10800 s. Los ids de orden/deal
enlazan ambas fuentes. `ORDER_TIME_SETUP_MSC` y `ORDER_TIME_DONE_MSC`
representan etapas diferentes; no se confunde el mensaje local de
aceptacion con el instante interno de setup.[^6]

No hay eventos de categoria Network entre 12:30 y 12:45 UTC, aunque si
hay 42 en el archivo del dia. Eso no demuestra que la red funcionara sin
retrasos. Sin trazas internas del terminal, transporte y servidor no puede
atribuirse exclusivamente al broker la demora anterior al setup. La parte
setup -> fill tambien es larga en su propio reloj nativo.[^5]

## Bloqueo Del Proceso

En 513 de las 521 llamadas temporizadas no aparece ningun evento del bot
estrictamente entre envio y respuesta. Las ocho restantes contienen pocos
eventos de frontera/planificacion. Las nueve llamadas superiores a cinco
segundos tienen cero eventos internos. El silencio afecta al diario global,
no solo a la corrutina que espera una apertura.[^2]

El binario de la VM contiene el metodo `order_send` en RVA `0xec50`.
Su ruta alcanza `ReadFile` en `0x41b8`, dentro de `0x4140`, con E/S sincrona
y sin liberar el GIL. La ruta de escritura alcanza `WriteFile` en `0x117a`.
La tabla de importaciones no contiene las primitivas de liberacion/retoma
de GIL o estado de hilo. La interpretacion proviene de instrucciones del
binario y sus importaciones, no del codigo fuente privado del proveedor.[^4]

En CPython 3.11, una extension que mantiene ese bloqueo durante una espera
impide avanzar a los otros hilos que necesitan ejecutar Python. Enviar la
funcion a `asyncio.to_thread` no la convierte por si solo en una espera que
libere el GIL. La lectura sincrona de Windows espera a completar la E/S.[^7][^8][^9]

Como control independiente del broker se ejecuta la misma espera del sistema
de 400 ms a traves de dos llamadas locales: una libera el GIL y otra lo
retiene, ambas desde `to_thread`. La tarea de vigilancia de 5 ms muestra un
maximo intervalo de 16.76 ms en el primer caso y 408.13 ms en el segundo.
No es una orden MT5 ni una medicion de latencia de la VM: discrimina el
mecanismo de bloqueo, que coincide con la inspeccion y los registros.[^10]

La consecuencia es general: mientras dura esa llamada, el bot no puede
tomar nuevas decisiones Python de entradas, cierres, trailing o consumo
de mensajes. Las protecciones ya instaladas en MT5 siguen siendo nativas
y no se convierten en dependientes de ese proceso. Un hilo adicional no
resuelve este aislamiento; un proceso separado es una opcion de reparacion,
que requerira cola, reconciliacion y tratamiento de respuestas tardias.

## Cola Y Decisiones

En 2774, tras recibir B1 a 12:34:17.806, se inicia una modificacion a
12:34:17.887 que acaba a 12:34:28.039. B2 se solicita a 12:34:28.041.
Tras B2, otra modificacion ocupa aproximadamente tres segundos antes de B3.
El retraso de abrir no es independiente del trabajo de modificar SL/TP.[^2][^5]

El control sintetico usa una modificacion pendiente desde t=0.25 hasta
t=6 y comprueba que los tres motores solicitan una segunda entrada en
t=1. Sus vias separadas permiten ese solapamiento. El cliente observado
no podia emitirla durante la llamada que bloqueaba el proceso. Cambiar una
media de latencia no introduce esa dependencia entre operaciones.[^10]

Hay una segunda diferencia. `_process_candidate_entry_tick` calcula las
patas cruzadas dentro de un bucle que reutiliza el mismo tick despues de
cada `await`. La prueba existente `test_crossed_tick_opens_every_new_leg_in_sequence`
espera precisamente que abra todas las patas cruzadas por esa cotizacion.
Los motores, con acuse secuencial, vuelven a evaluar una cotizacion posterior.[^10][^11]

El control BUY y su reflejo SELL parten de una entrada y una cotizacion que
cruza dos niveles. El precio vuelve despues. La rutina live abre ambas
patas, tres posiciones en total; cada motor abre solo una adicional, dos
en total. El horizonte sintetico queda explicitamente incompleto para el
cierre de estrategia, pero la divergencia del prefijo de entradas es conocida.
No se presenta ese control como una simulacion completa aprobada.[^10]

En el episodio real, B2 conserva un disparador de 12:34:17.697; cuando
puede enviar la orden tiene 10.344 s de antiguedad. El ejecutor consulta
un precio nuevo: 4348.22, por encima del nivel BUY 4348.13, pero la
intencion de entrada ya estaba tomada. B3 y B4 comparten el tick disparador
de 12:34:34.751; para B4 tiene 60.409 s. El precio de solicitud se refresca;
la decision y parte de su proteccion provisional se basan en la observacion
anterior. No son el mismo objeto causal.[^2]

No hay que convertir silenciosamente esta semantica en otra. Es preciso
declarar si una cotizacion compromete un lote de intenciones o si cada
orden debe volver a cumplir el nivel al quedar libre el ejecutor. Ambas
pueden modelarse de manera general; afirmar igualdad solo por compartir
numero de patas y distancias oculta una regla operativa importante.

El monitor consulta la ultima cotizacion y cede con esperas de 10 ms,
ademas del coste de las llamadas. No reproduce necesariamente cada tick
historico. El simulador debe separar el mercado nativo, que sigue moviendose,
de las observaciones disponibles para el cliente, que pueden saltarse ticks.
No hay evidencia para atribuir cada pequeno adelanto a un unico mecanismo.[^11]

## Diferencias Explicadas

Para cada una de las 22 entradas se conserva esta identidad, sin ajustar
ningun fill:

`diferencia de fill = diferencia de solicitud + espera simulada hasta fill - espera real hasta fill`.

En B3 de 2774, en segundos: `-20.177 + 0.300 - 56.857 = -76.734`.
El gran adelanto procede tanto de una solicitud previa como de la espera
real hasta ejecucion. No se puede trasladar todo al parametro de acuse.[^3]

La otra divergencia grande, B2 de 2777, tiene una causa distinta. Su
primera entrada real es 4342.59 y la simulada 4342.50. Con dos pasos de
1.5, los niveles quedan en 4339.59 y 4339.50. En la misma cinta, el primero
se alcanza a 12:36:13.616 con Ask 4339.57; el segundo no se alcanza hasta
12:36:46.081. Nueve centesimas de diferencia de ancla cambian el primer
cruce en 32.465 s. El fill simulado resulta 31.097 s posterior al real.[^3]

Esto demuestra amplificacion por umbrales y trayectoria, no un error
universal de reloj ni una demora constante que pueda restarse. En otros
casos hay residuos de reloj cruzado de -2 o -6 ms entre solicitud/fill;
se conservan, sin truncarlos a cero ni interpretarlos como ejecucion futura.

### Riesgo De 2774

Al marcar independientemente ambas trayectorias en la misma cinta, sus
minimos ocurren a 12:39:42.008 UTC, Bid 4324.03, FX de 440 ms de edad:

| Magnitud EUR | Posiciones reales | Posiciones simuladas |
| --- | ---: | ---: |
| Flotante | -154.39 | -239.60 |
| Ya realizado | 6.89 | 1.72 |
| Suma desde inicio de cesta | -147.50 | -237.88 |
| Posiciones abiertas | 3 | 4 |

No es equity de cuenta ni drawdown conjunto de cartera. Se valoran las
posiciones nativas observadas frente a las posiciones hipoteticas congeladas,
con Bid/Ask y conversion causal, sin suponer que la cuenta completa tuviera
esa exposicion.[^3]

B3 real entra a 4340.54 y cierra en su TP 4342.54 a 12:35:49.916,
antes de la caida. B3 simulada entra a 4347.05, tiene objetivo 4349.05 y
permanece abierta hasta 12:49:57.881. En el minimo, su contribucion es
+5.17 EUR realizada en real frente a -59.57 EUR flotante simulada.

Los **90.38 EUR de diferencia** se descomponen en 64.74 EUR por esa pata
ya cerrada y 25.64 EUR por los precios distintos de las otras tres patas
abiertas: 6.39, 11.49 y 7.76 EUR. No se necesita alterar el motor ni la
conversion para explicar esa diferencia contable.[^3]

El beneficio final +19.81 EUR en ambos recorridos proviene de objetivos
relativos a cada fill y tamanos iguales. Aqui la demora acabo reduciendo
exposicion en el peor momento. No demuestra que demorar ordenes mejore una
estrategia ni que el escenario mas lento sea siempre el mas conservador.

## Conversion Incompleta

Un hallazgo distinto impide dar por completa la fiabilidad de otras reglas.
En los tres motores, una conversion invalida antes de armar la proteccion
de beneficio puede omitir el maximo sin producir bloqueo. La salida temporal
condicionada puede tratar dinero desconocido como condicion no cumplida.
Los extremos monetarios tambien pueden omitir ticks y conservar un numero
sin declarar que es parcial.[^12]

| Control, una sola posicion | Con conversion completa | Sin conversion en el extremo | Los tres motores |
| --- | --- | --- | --- |
| Proteccion aun no armada | Observa +39.20 EUR y cierra por proteccion | Pierde la activacion y llega al cierre del proveedor | Cero bloqueos en ambos |
| Salida temporal no negativa | Solicita salida al cumplirse plazo y condicion | Omite la decision desconocida y llega al proveedor | Cero bloqueos en ambos |
| Regla solo de precios, medicion del riesgo | Minimo -40.80 EUR | Publica minimo -0.80 EUR | Cero bloqueos en ambos |

Son 18 evaluaciones sinteticas, no una optimizacion de 555. Los tests
demuestran decisiones diferentes o riesgo incompleto aceptados sin aviso.
El beneficio final coincide en estos ejemplos elegidos: otra razon para
no usar el neto como unica prueba de correccion.[^12]

En la cesta real/simulada 2801 hay once ticks con posiciones abiertas
entre 14:23:53.324 y 14:23:54.006 UTC cuya ultima conversion tiene
5040-5722 ms de antiguedad, fuera del limite congelado de 5000 ms.
El simulador termina sin bloqueo. El nuevo calculo conserva esos once
casos y marca la cobertura como parcial; no ensancha el limite.[^3]

Sus minimos conocidos -35.67 EUR real y -36.66 EUR simulado coinciden con
un instante anterior que si tiene FX valido. Pero no se certifica todo el
recorrido: un minimo de observaciones conocidas es una cota superior del
verdadero minimo monetario, no una perdida maxima plenamente observada.
Este hallazgo matiza el antiguo resumen de cero bloqueos y riesgo verificado;
no invalida automaticamente la conciliacion de dinero de los deals cerrados.
En 2774 no hay este hueco FX mientras existe exposicion, y por tanto no
explica sus 90.38 EUR de diferencia.[^3]

La reparacion debe distinguir conversion para decisiones, conversion de
fills realizados y cobertura de estadisticas. Dinero desconocido no equivale
a condicion falsa. Cuando no permita resolver una decision, el resultado
debe bloquearse o conservar ramas/cotas justificadas. Una politica solo de
precios puede conservar decisiones validas y aun tener riesgo monetario
parcial; esa distincion debe impedir su seleccion como riesgo completo.

## Caducidad Y Cierre

El monitor comprueba cierre solicitado y caducidad antes del bucle, no
despues de cada espera de apertura. Dos controles aislados cambian ese
estado mientras la primera pata esta pendiente: el siguiente envio se
produce aun con cierre solicitado, o despues de la caducidad.[^10][^11]

Esto demuestra un defecto latente del bot, no su efecto historico en 2774:
aquella escalera no estaba caducada durante el episodio. Corregirlo requiere
revalidar antes de cada nueva solicitud y tratar por separado las ordenes
ya enviadas que pueden ejecutarse tarde. Cancelar una intencion local no
cancela retroactivamente una orden del servidor.

## Reparacion General

El dominio de simulacion debe declarar tres componentes: politica de
estrategia, comportamiento del cliente y ejecucion del broker. Esos
componentes permiten comparar estrategias nuevas sin copiar entradas
historicas ni hacer una excepcion para 2774.

1. **Cerrar el fallo de dinero desconocido.** Regresiones de activacion,
   salida condicionada y extremos parciales en los tres motores; propagar
   la admision hasta resultados, cartera y seleccion. No basta con un aviso
   que el buscador ignore.
2. **Definir un recurso comun de ejecucion.** Solicitud, cola, servicio,
   setup/fill, respuesta y disponibilidad del cliente deben ser distintos.
   Aperturas, modificaciones y cierres compiten cuando corresponda;
   protecciones ya instaladas siguen actuando en el mercado nativo.
3. **Explicitar las intenciones de entrada.** Lote comprometido frente a
   reevaluacion tras acuse, antiguedad de cotizacion, caducidad, cierre y
   prioridad. Cubrir BUY/SELL, multiples cestas y politicas no escalonadas.
4. **Aislar el puente live y revisar cancelacion.** Separar su espera del
   proceso de decisiones, con identificadores idempotentes, reconciliacion
   de fills tardios y pruebas sin ordenes. Publicarlo es una decision
   separada, con seguridad de exposicion y autorizacion de despliegue.
5. **Modelar incertidumbre, no memorizar tiempos.** Escenarios declarados de
   servicio, congestion y observacion, incluyendo retrasos correlacionados
   y efectos sobre decisiones. No imponer un promedio por pata ni trasladar
   la distribucion de dos dias a todo el historico.
6. **Congelar y validar mecanismos.** Casos sinteticos de resultado conocido,
   contraste de versiones independientes y bloque posterior no usado para
   reparar. La condicion de exito es causalidad y cobertura dentro del
   dominio admitido, no reproducir una curva de beneficio.

Los JSON y ticks historicos siguen siendo utiles para estrategias que
dependan de esos datos y supuestos declarados. No hace falta registrar en
produccion cada estrategia alternativa. Si una capacidad exige informacion
ausente, se excluye o se analiza con incertidumbre explicita; no se certifica
todo el historico por haber igualado un caso.

## Verificacion Y Limites

Se conservan los scripts, entradas y salidas en
`runtime_data/execution_investigation_20260911/`. `audit.json` enlaza cada
evento con archivo, fila y hash; `explanation.json` conserva las 22
descomposiciones, diez senales y cobertura FX. Los resultados originales
de la simulacion no se reescribieron.

La primera version del calculo independiente de riesgo aborto al encontrar
FX de 5040 ms en 2801. Se conserva como `explain_attempt_v1.py`. La version
2 registra explicitamente los intervalos invalidos; no los elimina para
declarar exito. `fx_controls.json` se etiqueta como defectos reproducidos,
no arreglados. Los controles de cola/entrada tampoco son una certificacion.

Verificacion focal: **158 pruebas aprobadas, 45 avisos de deprecacion**.
Incluye ocho pruebas de los auxiliares, monitor 555, latencia de entrada,
mercado, protecciones y contrato de intervalos FX. Comando reproducible:

```powershell
python -m pytest -q runtime_data/execution_investigation_20260911/test_diagnostics.py tests/test_gold_555_monitor.py tests/test_iterative_entry_fill_latency.py tests/test_iterative_market.py tests/test_iterative_protection.py tests/test_study_fx_interval_contract.py
```

La bateria completa no se repitio: no hay cambios en codigo de producto ni
en los motores. Las pruebas existentes siguen aprobadas y los nuevos
contraejemplos muestran precisamente lo que no cubrian. Las pruebas
temporales del GIL son un experimento local separado, no un test con ordenes.

No se garantiza el instante exacto de un fill hipotetico, no se estima la
probabilidad futura de las colas extremas, no se determina la causa privada
del retraso del broker y no se certifica riesgo conjunto/margen/stop-out.
Tampoco hay una nueva lectura de exposicion actual. La ultima verificacion
del bot retenida en el control anterior fue a las 07:33 UTC, `d9037c5c`;
la lectura posterior del paquete y el log no sustituye una inspeccion live.

Estado: diagnostico y documentos locales, sin commit, push, reinicio,
ordenes, cambios de parametros ni seguimiento programado. Fase 2 necesita
cerrar los defectos hallados y fase 3 sigue abierta. No se inicia busqueda
masiva ni se declara completada la reparacion del simulador.

## Referencias

[^1]: [Control retrospectivo de Gold](2026-09-11-yesterday-gold-retrospective.md). Resultados nativos y simulados originales, guardados el 11/09; control del 10/09.
[^2]: [Auditoria temporal y enlaces nativos](../../runtime_data/execution_investigation_20260911/audit.json), creada el 11/09 a partir de seis segmentos conservados. Fuentes y SHA256 por segmento dentro del archivo.
[^3]: [Descomposicion independiente y cobertura](../../runtime_data/execution_investigation_20260911/explanation.json). Protocolo SHA256 `a9ccc6d6b509d4f910060915c82bb8c2498724e28e275a5954adb0995517cec4`; simulaciones `b4213900868c13f6e113470a3629168b73e45d27e81997367a16966e752f603e`; comparacion v3 `a76e59aeb972f58d04ee3e9a2ee70f58297c29b646c3fc38f90b0100ae9b5016`.
[^4]: [Inspeccion estatica del puente](../../runtime_data/execution_investigation_20260911/static_bridge.json). MetaTrader5 5.0.5735, Windows CPython 3.11, binario 112128 bytes, SHA256 `5b968f76b357edc4c971d1036581efb069121364f83172b23115e87113b84bb3`, coincidente con el remoto.
[^5]: [Registro independiente del terminal, extracto anonimizado](../../runtime_data/execution_investigation_20260911/terminal_evidence.json). Log original 220208 bytes, SHA256 `3a2fb9e007d3d291a11525e54f44ecb3123a9e8cd4210299c880bd782b026a15`, coincidente con el remoto; lineas 202-223.
[^6]: MetaQuotes, [Order Properties](https://www.mql5.com/en/docs/constants/tradingconstants/orderproperties), documentacion oficial consultada el 11/09/2026.
[^7]: Python Software Foundation, [CPython 3.11: Thread State and the Global Interpreter Lock](https://docs.python.org/3.11/c-api/init.html#thread-state-and-the-global-interpreter-lock), consultada el 11/09/2026.
[^8]: Python Software Foundation, [asyncio.to_thread](https://docs.python.org/3.11/library/asyncio-task.html#asyncio.to_thread), documentacion de Python 3.11, consultada el 11/09/2026.
[^9]: Microsoft, [ReadFile](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-readfile), Windows API, consultada el 11/09/2026.
[^10]: [Controles aislados de mecanismos](../../runtime_data/execution_investigation_20260911/mechanism_controls.json). Scripts, perfiles y hashes de modulos retenidos; cero ordenes reales.
[^11]: [Monitor de posiciones](../../position_lifecycle_monitor.py), entrada encadenada linea 1383 y bucle de cotizaciones linea 2257; [pruebas existentes](../../tests/test_gold_555_monitor.py). Codigo sin cambios en esta investigacion.
[^12]: [Contraejemplos monetarios](../../runtime_data/execution_investigation_20260911/fx_controls.json). [Motor escalar](../../research/dubai_iterative/engine.py), extremos linea 420, proteccion linea 848 y tiempo linea 887; [motor rapido](../../research/dubai_iterative/fast_engine.py), gestion monetaria linea 1897; [referencia](../../research/dubai_iterative/oracle.py). Las 18 evaluaciones conservan codigo/hash y resultados completos.
