# Quinta Ronda De Reparaciones E1-E2

Fecha: 20/09/2026. Estado: F1-F4 de la cuarta revision independiente estan
reparados y verificados localmente. Pendiente de contraste independiente con
Astra antes de aprobar E1-E2 o disenar E3. Sin commit, push, VM, reinicio,
configuracion ni orden real. No se modifica ninguna estrategia.

Revision posterior: la [quinta revision](2026-09-20-e1-e2-fifth-repair-review.md)
confirma F1, F2 y F4, pero reproduce una colision de event_id entre decisiones
e intentos que F3 todavia no detecta. Las afirmaciones de cierre siguientes
describen los contraejemplos de implementacion, no una aprobacion conjunta.

Base: rama `feature/gold-555-live-trial`, HEAD
`7415941a410d18288fa4e08da76809f70b125efa`. El checkout contiene cambios
locales anteriores; los hashes de abajo identifican los modulos efectivos.

## F1: Acuse Ligado A La Revision Del Resultado

El registro de intenciones pasa a schema 4 y cada resultado terminal tiene un
`outcome_revision` creciente. El consumidor debe acusar la revision exacta que
leyo. La comprobacion y la escritura del acuse se realizan en una unica
transaccion: una revision obsoleta devuelve `False`, y dos consumidores
concurrentes de la revision vigente solo obtienen un `True`.

Las migraciones desde schema 2 y 3 asignan revision 1 a resultados existentes,
conservan resultado y `applied_utc`, y no inventan una entrega nueva. Resolver
un UNKNOWN provisional incrementa la revision y vuelve a dejar disponible el
nuevo resultado.

## F2: Error Tardio Tras Recuperacion

Un sobre `worker_error` autentico de la misma peticion, intento y sesion puede
sustituir exclusivamente el UNKNOWN provisional creado por recuperacion. Sigue
siendo UNKNOWN, conserva el motivo real y libera el backlog una vez persistido.
No permite reenvio ni sustituye resultados terminales confirmados.

La regresion ejecuta las dos intercalaciones: error antes de recuperar y error
despues de recuperar. El doble de broker solo se invoca una vez en ambas.

## F3-F4: Identidad Y Numeros De Latencia

El informe de latencia pasa a schema 4. `event_id` se valida como identidad
global tanto en decisiones como en intentos. Reutilizarlo con `decision_id`,
`attempt_id` o sesiones incompatibles crea un conflicto y excluye la muestra de
los escenarios; no puede producir estado `ready`.

Retcodes no finitos o no integrales y metricas opcionales no finitas se cuentan
en `invalid_numeric_samples`. Se excluye solo el dato invalido y se conservan
las muestras validas; Infinity y -Infinity ya no abortan el resumen.

## Inventario Conservado

F1-F4 no cambian el constructor del inventario, por lo que no se genera un v7.
El probe independiente reconstruye exactamente el v6 desde el prefijo sellado:

- SHA256 del artefacto:
  `06c79ed4f8251c0445c44b5bccb965c4bf11554f5b7975d996181ba7b1380b01`.
- 1.190.442 bytes, 267 senales, 253 recepciones, 53 grupos y 5.937 intentos.
- El diario actual tiene 37 filas y 38.482 bytes posteriores al corte; todas
  quedan fuera del prefijo reconstruido.
- El generador conserva SHA256
  `a09ef960cb647f1eafcd54cb00a55900e65cc80b2877bcb45c485b04b85e101e`.

## Verificacion

- Regresiones canonicas y matriz independiente: 99 passed en 17,85 s.
- Artefacto JUnit de la matriz independiente: 34 passed, cero fallos y cero
  omitidos en 5,66 s; reemplaza los dos `xfail` historicos.
- Protocolo, intenciones, gateway, latencia e inventario: 111 passed en 12,82 s.
- Probe de datos independiente: pasa; los dos casos de identidad quedan
  `diagnostic_only` con muestra 0 y los tres no finitos se contabilizan sin
  excepcion.
- Suite completa: 6.001 passed, 638 avisos existentes, 476,81 s.
- Compilacion de cinco modulos: correcta.
- `git diff --check`: sin errores; solo avisos CRLF del checkout.
- Entorno local: Windows, Python 3.14.2.

Hashes de implementacion:

| Modulo | SHA256 |
| --- | --- |
| execution_intents.py | 8b54325e0c38f45d23b08f99d6893a72f22d0751ae41703c70ce44cb0d284b21 |
| mt5_gateway.py | 2106774068ded9af45945c508c46a5a01dce418b17fbe4ffb36ab7d5853370aa |
| execution_latency.py | 931405fae1d82b8e24a2ff57417d13539264743461af38b0bda62b279ab50ff2 |
| research/runtime_simulation_inventory.py | a09ef960cb647f1eafcd54cb00a55900e65cc80b2877bcb45c485b04b85e101e |
| tools/build_runtime_simulation_inventory.py | fe96d545c08779c70c61209b6def593c033aa1babf2a22773abae3f2defbe5c7 |

## Limites Y Siguiente Gate

Este bloque demuestra los contratos offline y elimina los cuatro
contraejemplos. No demuestra aun Python 3.11, MT5 real, bloqueo nativo de 60 s,
carga sostenida, propiedad unica tras reinicios, conciliacion contra broker ni
integracion E3. Tampoco demuestra rentabilidad ni cambia el bot en produccion.

Siguiente gate: Astra/alto revisa el diff efectivo, las migraciones, las
intercalaciones y los probes actualizados. Solo si no quedan defectos abiertos
se aprueban E1-E2 y se disena E3. Publicar o activar requiere autorizacion
separada y las salvaguardas de exposicion de la VM.
