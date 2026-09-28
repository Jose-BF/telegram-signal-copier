# Preparacion Del Simulador: Primer Bloque Verificado

## Estado Y Autorizacion

Objetivo: preparar simulacion fiable de miles de estrategias, no optimizar ni
promover las candidatas que estan ejecutandose en demo. El usuario autoriza la
preparacion local y exige una revision conjunta **antes del punto 4 y de cualquier
busqueda masiva**. Presupuesto de nuevas candidatas en este bloque: **cero**.

Este es un checkpoint intermedio, no el cierre de los puntos 1-3 ni una admision
para buscar. Los fallos de entrada identificados estan corregidos y verificados;
la secuencia completa de ordenes y protecciones del broker sigue pendiente.
Cambios locales sobre `fc8a4202ef44f8fab9db281cee2d7bfcf6bc79bd`, rama
`feature/gold-555-live-trial`. No commit, push, despliegue, reinicio ni cambio de
politica live. La VM no se ha vuelto a verificar durante este bloque.

## Implementado

- `research/causal_replay.py` compila las senales admitidas desde mensajes raw y
  sus tiempos de disponibilidad. No consume fills, resultados ni estados live.
  Conserva diagnosticos de mensajes fuera del vocabulario soportado. Distingue
  orden condicional, sticker desconocido, revisiones A-B-A y publicacion futura.
- `tools/run_causal_controls.py` separa preparacion y ejecucion. Congela mensajes,
  politicas, cintas, codigo y escenarios; no lee el ledger en la ejecucion. La
  simulacion conserva su propio estado por senal, sin reinyeccion de snapshots.
- `research/causal_comparison.py` y `tools/compare_causal_controls.py` comparan
  despues las entradas y salidas con el ledger por posicion logica y registran
  la primera divergencia. El comparador utiliza las direcciones congeladas en la
  preparacion, no una recompilacion posterior con codigo diferente.
- `entry_fill_latency_ms` separa el retraso hipotetico de ejecucion del retraso
  de observacion `latency_ms`. El precio realmente simulado del primer fill
  ancla el ladder; cada solicitud posterior tiene su propio retraso. No se aplica
  este cambio a fills observados ni a contratos no soportados.
- Los motores cancelan solicitudes futuras cuando llega un cierre aplicable
  del proveedor. Una carrera de cierre con la primera entrada o con una entrada
  ya solicitada queda bloqueada cuando su ejecucion no esta modelada.
- En schema 2, delay, pullback y momentum no pueden solicitar entrada en el
  instante exacto de caducidad. Schema 1 conserva su contrato historico inclusivo.
  Un delay causal salta cotizaciones no utilizables dentro del dominio admitido;
  el fast engine mantiene su bloqueo de cintas no representables.
- Una entrada futura sin cotizacion de fill ya no borra operaciones anteriores.
  El replay decide si alcanza esa solicitud y conserva el prefijo cerrado. Se
  exporta y compara `requested_ns`, separado del instante del fill, y los
  informes identifican el retraso de ejecucion utilizado.
- `research/dubai_iterative/broker_execution.py` incorpora un kernel offline de
  SL/TP: estado instalado, solicitudes pendientes, aceptacion/rechazo atomicos,
  restricciones declaradas y prioridad de protecciones antiguas. **No esta
  integrado en los tres motores ni es un emulador certificado de MT5.**

## Contraste Independiente

Artefactos actuales: `runtime_data/simulation_foundation_20260909/independent_v3/`.
`independent_v1` queda como evidencia historica de 162 ejecuciones. `independent_v2`
solo contiene la preparacion anterior a las ultimas correcciones; no se ejecuta
ni se sobreescribe bajo el codigo nuevo.

La preparacion actual verifica el prefijo completo de 1.430.602.901 bytes y
extrae 525 mensajes raw dentro del intervalo capturado, de los que obtiene 18
senales. Las 18 aparecen antes de que el comparador acceda al ledger. Dos
politicas fijas, cinco escenarios y tres motores producen **270 ejecuciones**,
no una exploracion de nuevas estrategias.

Los escenarios `(observacion_ms, fill_ms)` son `(0,0)`, `(250,0)`, `(1000,0)`,
`(0,250)` y `(0,1000)`. Son sensibilidad declarada, no tolerancias calibradas ni
una distribucion estadistica del broker. El limite de tiempo es de 600 segundos
comprobado entre controles; no es un timeout que interrumpa una llamada interna.

Resultados:

- Los tres motores coinciden exactamente en los 90 controles por senal/escenario.
- La comparacion de hechos contra MT5 conserva 89 filas `mismatch` y una
  `blocked`; ninguna queda certificada.
- Baseline: 60 entradas simuladas frente a 59 observadas. La diferencia de numero
  sigue en `canal2_2590`; no se elimina ni se compensa con otra posicion.
- `canal1_22328` con fill-delay de 1.000 ms conserva el bloqueo
  `entry_request_in_flight_at_strategy_exit`.
- No se completan artificialmente las 19 cotizaciones live que faltan en el
  historico. El resultado continua marcado como diagnostico retrospectivo.

La primera divergencia de `canal2_2590` explica por que el fill importa: la
senal dispara sobre Ask 4402,93; el fill nativo ocurre despues a 4402,76. La
cuarta entrada de un ladder de paso 1,5 quedaria en 4398,43 con el primer precio,
frente a 4398,26 con el fill observado. Una diferencia pequena de precio cambia
la existencia de una operacion posterior. No se ajusta un retraso a medida para
forzar la igualdad de ese caso.

## Medicion De Ejecucion

`execution_measurement_v1/measurement.json` y `attempt_evidence.json`, bajo la
misma carpeta de preparacion, conservan 1.138 intentos del cohorte y enlazan las
59 aperturas con sus deals nativos, sin errores de identidad ni de timing. La
medicion reutiliza `execution_latency.py` y distingue relojes monotono y UTC.

| Magnitud observada, 59 aperturas | Mediana | P95 | Maximo |
| --- | --- | --- | --- |
| Envio a fill nativo, intervalo UTC | 59 ms | 2.556,5 ms | 7.032 ms |
| Fill nativo a recepcion del acuse, intervalo UTC | 130 ms | 3.339 ms | 6.703 ms |
| Ida/vuelta de la llamada, reloj monotono | 359 ms | 4.192 ms | 7.375 ms |

El envejecimiento de la cotizacion al enviar la apertura tiene mediana de
140 ms y maximo de 371 ms. El precio de ejecucion respecto al solicitado se
desplaza entre -1,92 y +0,70 unidades de XAUUSD, con signo positivo adverso.

Se conservan 1.063 intentos de modificacion: 783 con retcode 10009, 248 con
10016, siete con 10029 y 25 con 10036. Son intentos, no posiciones. La llamada
de modificacion llega a 26.735 ms en el extremo observado. Un rechazo no puede
instalar los niveles solicitados, y una respuesta tardia no demuestra que el
nivel se hiciera efectivo precisamente al recibirla.

Estas medidas no autorizan una tolerancia de siete segundos ni se trasladan
sin mas a `entry_fill_latency_ms`. La sugerencia `ready` del resumen historico
de latencia solo significa que existen al menos 30 muestras. No acredita el
modelo, una tolerancia, ni validacion fuera de muestra. Siguen sin observarse
la cotizacion exacta del servidor al procesar cada modify y su instante exacto
de efectividad; los extremos de una llamada solo los acotan.

## Verificacion

Se anadieron 237 pruebas dirigidas: compilacion raw (20), comparador (10),
protocolo/herramientas (7), kernel de broker (31), solicitudes/fills (52),
capacidades adicionales (51) y fronteras de entrada (66). Las nuevas regresiones
de defectos se observaron fallar antes de corregirlos.

Ultima verificacion focal: **284 aprobadas**. La revision critica independiente
repitio 13 regresiones de entrada y dos de informes con JIT desactivado y cerro
sus tres hallazgos pendientes; no certifico fidelidad live.

Bateria completa actual: **3.905 aprobadas, cero fallos/errores/omitidas**, 633
avisos de deprecacion, 170,66 segundos. `verification_v2/final.json` confirma que
las 374 fuentes verificadas y los wrappers no cambiaron durante la ejecucion.
El XML anterior `full_suite_v1.xml` se conserva con su unico fallo de compatibilidad
del informe; el test se actualizo para exigir el nuevo campo, sin relajar dinero
ni tolerancias de comparacion.

Identidades SHA-256 actuales:

| Artefacto | SHA-256 |
| --- | --- |
| `independent_v3/protocol.json` | `a70f2ff8e72a514479051787ee17006818b60d011c8e59d9c29592951b8666e8` |
| `independent_v3/independent_results.json` | `bafcb66ae49ac55ac15db5551ee9b4d0762f93dab3dfc8b1bbad3b9911d0e573` |
| `independent_v3/first_divergences.json` | `10b343555e8361ff293d38d155ceed00cc91836a6f9f189888da03272df98607` |
| `verification_v2/pytest_full.xml` | `0ac6849d418d3eec355c825cbc8f82b193aaa5890a482de9a34ea57d6dfa14c5` |

## Pendiente Antes De Admitir Busquedas

1. Integrar el lifecycle de solicitudes, fills, acuses, modificaciones, rechazos,
   reintentos y cierres en el replay continuo, preservando la referencia
   independiente. El kernel aislado y el fill-delay nuevo no cierran ese trabajo.
2. Contrastar ese modelo con una cinta completa y sus restricciones causales.
   Los 19 huecos del archivo actual no son recuperables por cambiar el motor.
   Medir incertidumbre de ejecucion sin utilizar los resultados futuros para
   fabricar aceptaciones o precios.
3. Completar la cobertura e integracion por capacidades. La matriz identifica
   como no admitidos los parciales de proveedor, la reentrada multiple, comisiones
   no cero y el margen/stop-out no modelados. Portfolio con swap no cero sigue
   bloqueado, aunque el motor individual si soporte rollover. No son requisitos
   de un diagnostico intraday que no use esas capacidades, pero si del dominio
   que pretendiera ofrecerlas.
4. Presentar al usuario el dominio, la evidencia, las limitaciones y el coste
   previsto. **No iniciar la validacion nueva/busqueda del punto 4 sin esa revision.**

La fase siguiente no consiste en esperar a que Gold 555 gane mas dinero ni en
buscar parametros que hagan coincidir el P/L observado. El objetivo sigue siendo
un modelo de ejecucion contrastable y una admision explicita por capacidad.
