# Revision De La Quinta Reparacion E1-E2

Fecha: 20/09/2026. Revision Astra del codigo efectivo de Sol, con un revisor
auxiliar independiente para latencia. Se cierran F1, F2 y F4 en el alcance
offline; F3 conserva un unico defecto reproducido. No se modifica codigo
operativo ni se hace commit, push, acceso VM, reinicio u orden real.

Actualizacion posterior: la
[sexta reparacion](2026-09-20-e1-e2-sixth-repair-results.md) incorpora la
validacion compartida entre tipos y supera la reproduccion de este documento.
Esta revision se conserva como evidencia historica; queda el contraste final de
la frontera modificada con Astra.

## Hallazgo P2: F3 No Comprueba Colisiones Entre Tipos De Evento

`execution_latency.py:242` y `:295` construyen indices separados de identidad
para decisiones e intentos. Un `event_id` puede pertenecer a una decision y a
un intento incompatible sin que ninguno de los dos indices detecte conflicto.

Reproduccion: 30 decisiones e intentos con tiempos schema 1 validos; cada
intento reutiliza el event_id de otra decision, de otra sesion. El resultado
es `ready`, 30 muestras, cero conflictos, tanto en orden original como inverso.
Las observaciones contradictorias deberian quedar en cuarentena antes de
calibrar escenarios. Los event_id proceden de UUID globales del journal; no
tienen espacios de identidad separados por tipo de evento.

Probe conservado en
`runtime_data/e1_e2_fifth_review_20260920/data_review/test_latency_review.py:142`.
Ejecutar `python -B` sobre ese archivo reproduce el fallo en ambos ordenes.
Es un defecto del contrato de validacion demostrado con datos sinteticos;
no se ha observado una colision de este tipo en produccion ni explica por si
solo los retrasos de la VM o la diferencia de drawdown.

## Contratos Confirmados

- F1: revision durable del resultado y acuse atomico correctos. Cubre cuatro
  motivos de recuperacion, cinco resultados, acuse anterior/posterior,
  reapertura y carrera concurrente entre acuse antiguo y resolucion.
- Migracion: schemas 2 y 3 sin columna de revision, cinco resultados y estado
  aplicado/no aplicado conservados. Segunda reapertura sin perdida de acuse.
- F2: error tardio sustituye incertidumbre provisional, conserva UNKNOWN y se
  consume una vez. Un fallo de disco retiene el sobre hasta persistir; sesion
  o peticion distinta y resultados reales incompatibles se rechazan.
- F4: Infinity, -Infinity, NaN, fracciones y otros retcodes invalidos no abortan
  ni aportan exitos; las muestras validas permanecen. Controles de enteros,
  parciales, PLACED y metricas opcionales pasan.
- F3 original: se cierran las dos reproducciones previas. Pasan duplicados,
  conflictos transitivos, separacion de relojes y umbral de 29/30 muestras.

## Evidencia

- Suite focal y matriz de la cuarta revision: 145 passed en 22,56 s.
- Nueva matriz de transporte: 69 passed en 9,14 s; JUnit sin fallos ni omitidos
  en `runtime_data/e1_e2_fifth_review_20260920/transport_review.xml`.
- Nueva matriz de latencia: 11 pruebas pasan; una falla en dos subcasos de
  orden. Ambos revisores ejecutan y confirman la reproduccion.
- Los cinco hashes de implementacion coinciden con la
  [quinta reparacion](2026-09-20-e1-e2-fifth-repair-results.md). Se conserva
  su evidencia global de 6.001 pruebas, sin repetirla sobre codigo intacto.
- Windows/Python 3.14.2, SQLite temporal y dobles; ninguna llamada MT5 real.

El inventario v6 y el generador mantienen sus hashes publicados. Reconstruir
desde el prefijo verificado del diario produce igualdad completa: 267 senales,
253 recepciones, 53 grupos y 5.937 intentos. Las cinco fuentes auxiliares
coinciden completas. En esta revision el diario tiene 74 filas/76.964 bytes
posteriores al corte; el prefijo sellado permanece intacto. No se elimina el
sufijo ni se atribuye su origen sin evidencia.

## Entrega Acotada Siguiente

Sol/alto: completar la validacion compartida de event_id entre decisiones e
intentos antes de construir duraciones, preservar conflictos transitivos y
contabilizar la cuarentena. Llevar la reproduccion a tests canonicos y agregar
un control mixto que conserve muestras no afectadas. El resultado debe ser
independiente del orden; duplicados exactos y 30 muestras validas siguen
produciendo el resultado actual. Documentar la semantica del informe.

No reabrir ni reimplementar F1/F2/F4, ni regenerar inventario v6 por este fallo.
El cierre del cambio requiere las regresiones nuevas, controles existentes y
verificacion proporcional del modulo afectado segun AGENTS.md. Astra revisara
esa frontera modificada, conservando las aprobaciones anteriores.

La revision no aprueba todavia el conjunto E1-E2 ni integra E3. Compatibilidad
Python 3.11, bloqueo nativo de 60 s, carga, propietario unico, conciliacion real
y atomicidad de efectos del consumidor conservan sus requisitos futuros. El
acuse versionado no demuestra por si solo ejecucion exactly-once del consumidor
ni del broker. El objetivo siguiente sigue siendo integrar el aislamiento y
contrastar decisiones/riesgo, no ajustar una fecha o una estrategia concreta.
