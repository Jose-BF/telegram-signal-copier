# Revision De La Cuarta Ronda E1-E2

Fecha: 20/09/2026. Revision independiente de la implementacion de Sol. E1-E2
no aprobados para E3: quedan dos transiciones reproducidas en transporte y
aplicacion, un hueco de identidad de latencia y una limitacion ante numeros
malformados. Se preservan codigo operativo, datos originales y configuracion.
Sin commit, push, acceso VM, reinicio ni ordenes reales.

Actualizacion posterior: F1-F4 estan reparados localmente en la
[quinta ronda](2026-09-20-e1-e2-fifth-repair-results.md). Este documento se
conserva como evidencia historica de los contraejemplos que debe volver a
contrastar Astra; ya no describe el estado actual de la implementacion.

## F1 / P1: Un Acuse Antiguo Consume El Resultado Nuevo

`execution_intents.py:599` borra `applied_utc` al sustituir incertidumbre de
recuperacion, pero `mark_applied` (`:706`, consulta `:710`, escritura `:720`)
solo recibe intent_id. No puede distinguir que resultado leyo el consumidor.

Intercalado reproducido, sin hilos ni sleeps: consumidor lee UNKNOWN; llega
DONE/deal=903; consumidor acusa su lectura antigua; consumidor de DONE intenta
acusar el resultado confirmado. La primera llamada devuelve True y la segunda
False. DONE aparece aplicado aunque solo se haya tratado el aviso anterior.

La regresion de Sol cubria acuse antiguo ANTES de resolver UNKNOWN. Falta el
orden inverso. Borrar el marcador solo resuelve una de las dos secuencias. E3
aun no consume este contrato, por lo que no es una perdida demostrada en la VM.

Cierre: identidad/version de resultado entregada al consumidor y acuse
condicionado atomicamente a esa version. Un acuse de revision obsoleta no puede
marcar la actual. Conservar reentrega tras reapertura y probar ambos ordenes,
duplicados y concurrencia. La atomicidad de efectos del futuro consumidor debe
disenarse en E3; el marcador actual por si solo no acredita exactly-once.

## F2 / P2: Error Tardio Del Trabajador Bloquea Recuperacion

`mt5_gateway.py:436` concilia el UNKNOWN provisional cuando llega `outcome`.
La rama `error` (`:458-474`) mantiene la comparacion exacta con el resultado
provisional y lanza conflicto si el trabajador comunica su propio error.

Se ejecuto `_worker_main` con broker doble invocado una vez y un fallo de
decodificacion inyectado despues del retorno. Genera un sobre `worker_error`
valido y deja DISPATCHING. Si la recuperacion se adelanta a ese sobre, procesarlo
lanza `worker error does not match the durable terminal outcome` y retiene el
backlog. El mismo error sin recuperacion previa se consume correctamente como
UNKNOWN. La inyeccion comprueba una ruta de excepciones soportada; no demuestra
un fallo de decodificacion real de MT5 ni permite afirmar que no hubo fill.

Cierre: ambas formas de evidencia tardia deben poder sustituir exclusivamente
la incertidumbre provisional para la misma peticion/intento/sesion. Mantener
UNKNOWN, retener el motivo y liberar transporte tras persistirlo, sin reenvio.
Resultados realmente incompatibles deben seguir rechazandose.

## F3 / P2: Identidades Contradictorias Aun Certifican Latencia

`execution_latency.py:217` agrupa decisiones por sesion/decision sin comprobar
coherencia global de event_id. En `:243-245`, la identidad del evento de intento
incluye sesion, permitiendo reutilizar un mismo event_id en sesiones distintas.
`causal_trace.new_event_id` genera identificadores UUID; la sesion no convierte
un mismo evento en varias observaciones independientes.

Dos reproducciones devuelven sample_count=30, ready y cero conflictos: un
event_id de inicio asociado a 30 decision_id distintos, y un event_id de intento
asociado a 30 sesiones distintas. Todos los tiempos del fixture son validos;
el defecto esta en la identidad que admite la muestra.

Cierre: verificar coherencia de event_id en todo el lote, tanto en decisiones
como en intentos, antes de construir uniones y percentiles. Propagar la
cuarentena a las duraciones que dependan de un inicio contradictorio. Mantener
el control de 30 intentos validos que si debe declarar ready y la separacion
entre relojes de sesiones distintas.

## F4 / P2 Condicionado: Retcode Infinito Aborta El Informe

`execution_latency.py:347` convierte retcode a entero sin capturar
OverflowError. Un intento valido y otro con retcode=Infinity o -Infinity
abortan todo el resumen. NaN ya se rechaza sin perder la muestra valida.

Cierre: validar el campo y contabilizar el dato malformado conservando las
muestras validas. No se ha demostrado este valor en el corpus; es robustez
diagnostica, no una incidencia operativa constatada de la VM.

## Inventario Y Fuentes

TI1-TI4 quedan confirmados en el alcance revisado: conflictos de canal en
cuarentena, filas nativas independientes del orden, fecha/fila de shadow
conservadas y metadatos anidados eliminados. No aparecen nuevos defectos
materiales de inventario en esta revision.

Se verifica el SHA256 completo publicado del v6 y su generador. Las cinco
fuentes auxiliares coinciden integramente. El diario tiene ahora 38.482 bytes
y 37 lineas adicionales respecto al v6; todas son posteriores al corte. Su
prefijo de 127.577.916 bytes mantiene exactamente el hash del manifiesto.
Reconstruir desde ese prefijo produce igualdad completa con el JSON v6:
267 senales, 253 recepciones, 53 grupos y 5.937 intentos. No se elimina el
sufijo ni se atribuye su origen sin evidencia.

El revisor auxiliar dejo el probe reproducible
`runtime_data/e1_e2_fourth_review_20260920/data_review/probe.py`. El revisor
principal inspecciono y ejecuto sus controles, reproducciones y reconstruccion.

## Controles Positivos

- 25 cruces de cinco resultados (DONE, parcial, PLACED, rechazo, UNKNOWN) y
  cinco entradas (timeout/settle, await_late, recover, start, close): mientras
  falla SQLite conservan sobre/pendiente; al restaurarlo se persiste el
  resultado sin encolar otra orden.
- Cinco migraciones schema 2 conservan resultado y acuse, validan la peticion
  completa y sobreviven a una segunda reapertura.
- Una migracion con peticion incompleta falla sin avanzar version ni insertar
  admisiones parciales.
- Control de error del trabajador sin recuperacion previa: UNKNOWN consumido.

## Evidencia

Probes permanentes de revision en
`runtime_data/e1_e2_fourth_review_20260920/test_transport_review.py` y salida
`transport_review.xml`. Los dos `xfail(strict=True)` marcan defectos conocidos;
no son aprobaciones. `--runxfail` permite observar las dos fallas directamente.

- Suite focal existente, incluyendo protocolo: 103 passed en 13,53 s.
- Matriz adicional: 32 passed y 2 xfailed en 6,47 s.
- La suite global de Sol (5.993 passed) se conserva como evidencia sobre el
  codigo sin modificar; estos intercalados no estaban cubiertos en ella.
- Windows/Python 3.14.2; revision offline con SQLite temporal y dobles.

Hashes de implementacion revisada:

| Modulo | SHA256 |
| --- | --- |
| execution_intents.py | 0f3277ee9bb4d5336dd16bc8f32a6c59a5a67c40aaa467db974d07c6912c2e31 |
| mt5_gateway.py | 033867601ee8f9dba65d17d46c26a1f1ce55db2bd2fe03acf7e068db8e14ed2d |
| execution_latency.py | cafd2ae8576c1caddf9e8efbae452550f1175fa408ca7566eb9fb666c2e17a9f |
| research/runtime_simulation_inventory.py | a09ef960cb647f1eafcd54cb00a55900e65cc80b2877bcb45c485b04b85e101e |

## Siguiente Entrega

Corregir F1-F3 con regresiones de aceptacion sobre esta matriz; incluir F4 en
el mismo tratamiento de entradas numericas. El inventario v6 no requiere
otra regeneracion por estos hallazgos. Mantener el
alcance E1-E2 y el reparto acordado: Sol/alto para implementar, Astra/alto para
contrastar la frontera modificada. Python 3.11, aislamiento nativo 60 s, carga,
propiedad de proceso, conciliacion e integracion E3 conservan sus gates futuros.
