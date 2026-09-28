# Gestion Condicionada Y Recorrido De Riesgo

## Alcance

Continuacion local del objetivo activo, no cierre de E5 ni del proyecto.
Se reutilizan los tres motores existentes y el adaptador de gestion condicionada
de `research/gold_iterative/live_parity.py`. No hay cambio en politicas live,
despliegue, VM, operaciones, busqueda de estrategias ni nueva automatizacion.

`research/conditioned_management.py` proyecta exclusivamente las entradas
confirmadas a un `SignalPath`: precio, instante, direccion, volumen y pata.
Los cierres reales, dinero final y niveles instalados NO entran en el motor.
Solo se aceptan prefijos contiguos con volumen correspondiente a la politica;
no se crean patas ni se reparan identidades buscando precios parecidos.
El resultado tiene que conservar cada entrada confirmada exactamente. Si el
motor cierra antes e impide una entrada posterior, se conserva como bloqueo.

Este modo **no comprueba las decisiones de entrada**, ni reproduce las
observaciones/colas/respuestas que recibio realmente el cliente. Es una
hipotesis de gestion sobre cinta retenida con entradas condicionadas. No se
presenta como control independiente de entradas ni como paridad live completa.
La identidad de la politica historica ejecutada tampoco esta probada por el
ledger: se usa la politica declarada en el protocolo y se indica esta limitacion.

`tools/run_conditioned_management.py` verifica mensajes, metadatos, universo,
ledger/manifiesto, moneda, reloj y hashes de cintas. Recompila las entradas
causales y exige igualdad con los metadatos congelados. Registra la identidad
actual de motores, distinta de la identidad historica conservada del protocolo.
Retiene casos faltantes y no soportados, rechaza costes no nulos sin contrato,
y publica resultados nuevos sin sobrescribir. Limites: 40 senales, 600 s
comprobados entre casos y presupuestos de cintas/valoraciones del comparador.

## Contraste Del 08/09

```text
python tools/run_conditioned_management.py --study runtime_data/simulation_foundation_20260909/independent_v3 --analysis runtime_data/causal_capture_today_20260908/end_analysis_v4 --output reports/e5-conditioned-management-20260921/retrospective-20260908-v2.json --max-market-gap-ms 5000
```

18 senales, 7 Dubai y 11 Gold; 54 ejecuciones de motores, cero candidatas.
18,656 s. Los tres motores concuerdan en las 18, incluidos sus bloqueos.
Ocho casos siguen bloqueados por cobertura de conversion; no se excluyen del
denominador. Diez permiten contraste de trayectoria. Se conservan todos los
bloqueos de admision del protocolo original y el limite FX de 5 s.

En los diez comparables, el minimo desde origen coincide exactamente con el
reconstruido de los deals nativos; el drawdown coincide en ocho. **Los diez
conservan diferencias temporales**, no se declaran aprobados por esos extremos.
Es una sola jornada retrospectiva y no demuestra suficiencia de muestra.

| Senal | DD observado / condicionado EUR | Diferencia maxima de trayectoria EUR | Neto observado / condicionado EUR |
| --- | --- | ---: | --- |
| canal1_22284 | 2,21 / 2,21 | 0,07 | 9,42 / 9,43 |
| canal1_22297 | 13,42 / 13,42 | 0,39 | 16,90 / 16,96 |
| canal1_22312 | 28,21 / 28,21 | 3,81 | 11,81 / 10,79 |
| canal1_22328 | 27,54 / 27,54 | 0,66 | -24,94 / -25,60 |
| canal2_2590 | 8,98 / 8,98 | 0,08 | 8,17 / 8,17 |
| canal2_2595 | 2,45 / 2,38 | 0,88 | 4,30 / 4,30 |
| canal2_2599 | 4,10 / 4,10 | 0,04 | 1,72 / 1,72 |
| canal2_2615 | 75,83 / 75,83 | 2,92 | 19,79 / 19,79 |
| canal2_2628 | 12,81 / 11,60 | 1,21 | 19,78 / 19,78 |
| canal2_2631 | 19,32 / 19,32 | 2,27 | 13,33 / 13,33 |

Valores por cesta sobre rejilla comun retenida, no extremos continuos, anclas
nativas de flotante ni drawdown de cuenta. El mismo calculador revalora ambos
recorridos; esa igualdad no constituye comprobacion monetaria independiente.

## Causas Acotadas, No Demora Ajustada

- Gold 2615: la hipotesis con entradas propias tenia DD 116,09 EUR frente a
  75,83 observado reconstruido. Con entradas reales, el DD pasa a 75,83 y el
  minimo a -66,58 en ambos. El efecto de entradas ya explicado por umbrales
  derivados queda respaldado por un control de gestion, sin copiar cierres.
  Persisten diferencias de hasta 2,92 EUR en transitorios de salida; la ultima
  pata cierra 1.179 ms despues en el ledger que en la cinta simulada.
- Dubai 22312: el modelo cierra las tres patas a 13:43:49.561 UTC; los cierres
  nativos constan a 13:43:54.733, 13:43:57.601 y 13:44:01.138. Es una diferencia
  concreta de secuencia de salidas, no solo de entrada. Sin enlazar solicitudes
  y acuses no se atribuyen estos 5,172/8,040/11,577 s exclusivamente a la VM.
- Dubai 22328: las tres salidas nativas son SL, mientras el modelo usa
  `basket_stop`. Tener drawdown igual no valida mecanismo/proteccion. Debe
  contrastarse el historial de niveles y su confirmacion, no cambiar la
  politica historica ni el motivo nativo para fabricar concordancia.
- Gold conserva TP al mismo precio y dinero en las seis cestas comparables,
  pero no todos al mismo instante. Hay diferencias de milisegundos y otras
  mayores: 2.912 ms en 2595 y 5.820 ms en 2631. No son por si solas prueba de
  espera del bot: un TP ya instalado puede ser ejecutado por el broker.

Consulta adicional, sin regenerar ni elevar de version auditores archivados:
`native_exit_attribution.json` enlaza los tres cierres de 22312 a intentos
concretos, y `causal_audit.json` enlaza las solicitudes con la misma decision.
Los dos archivos coinciden con sus hashes/bytes del manifiesto:
`d948b5ee115bf52fad6df0eb3142f0da46187f8157ccf27711cd075b67aa94ac`
y `7e96b9c27a1da3fdb41b723d87a03ae54e8a58d3ce6f9516fb7075d204b888ec`.
La decision aparece a 13:43:54.709; solicitudes a .709/.711/.713. Por tanto,
el disparador del modelo (13:43:49.561) antecede en 5,148 s al registro de la
decision. El primer deal sucede 24 ms tras ese registro, pero su evento de
intento se escribe a 13:43:57.495, DESPUES del propio deal. No se interpreta
esa escritura como comienzo de llamada ni se inventa latencia de red negativa.
Hace falta la evidencia de fases monotona para descomponer observacion, cola,
llamada y confirmacion. Las identidades ayudan sin certificar el modelo de cola.

## Verificacion

53 pruebas focales aprobadas: 19 nuevas de condicionamiento, cuatro de CLI y
30 del comparador anterior. BUY/SELL, ambos canales, una/tres patas, salida
distinta sin copiar la real, hechos futuros eliminados, volumen/identidad,
costes, cronologia, FX ausente, fuentes alteradas, denominador e inmutabilidad.
La prueba de FX ausente reprodujo un error del adaptador nuevo: intentaba
convertir a evento una salida de dinero desconocido de un motor bloqueado.
Se corrigio conservando los resultados/bloqueos y sin crear eventos incompletos.
Otro fallo de fixture era final de cinta sin salida de estrategia; se completo
el caso sintetico con CLOSE ALL, sin relajar ese bloqueo del motor.

Suite global: **6.394 aprobadas, una advertencia, 467,87 s**, Python 3.11.9,
pytest 9.1.1 y Windows. No se editaron fuentes Python durante la ejecucion.
Advertencia previa de pandas en `test_study_dataset_review`, sin fallo.
Comando: `python -m pytest -q --junitxml=reports/e5-conditioned-management-20260921/pytest.xml -o junit_family=legacy`.
SHA256 del XML: `e971c1ac85c0e6a9bc3bfd75471814ce19dd9cb7f3a5fa926f93232b00eb461a`.
Revision directa e independiente acotada completadas. El revisor no encontro
hallazgos materiales en los dos modulos nuevos y sus dos archivos de pruebas;
comprobo las cifras/hashes del informe. Revision de solo lectura: no ejecuto
tests ni revalido fuentes nativas/dependencias fuera de esos cuatro archivos.
No equivale a revision completa del paquete de runtime ni autoriza publicar.

Informe final SHA256:
`5dde935e6defcbe2e0c81b140e65c0038a5eff8c107bbcf77b70539a05378c40`.
El primer informe se conserva como snapshot anterior a validar costes malformados;
v2 contiene la implementacion final de este bloque. Fuentes e inputs verificados
de nuevo sin cambios. Hashes completos, politicas y resultados estan en el JSON.

## Siguiente Frontera

1. Enlazar intenciones, solicitudes, niveles instalados y confirmaciones con las
   salidas divergentes usando los auditores existentes; replay de observaciones
   de cliente y arbitraje compartido siguen pendientes. No simular un cierre
   conjunto instantaneo como si representara todas las ejecuciones del runtime.
2. Contrastar valoracion condicionada contra anclas monetarias nativas y ampliar
   cohortes admisibles de ambos canales, conservando huecos y dias adversos.
3. Perfiles empiricos separados de validacion para fills hipoteticos. No escoger
   por cesta el retraso que coincida, ni usar condicionamiento para certificar
   entradas que la estrategia alternativa no habria conseguido.
4. Mantener separada la entrega operativa: revision del paquete de aislamiento,
   condiciones de almacenamiento/VM y publicacion autorizada con preflight.
   Esta mejora de investigacion no debe retrasar artificialmente una reparacion
   operativa que ya tenga sus propios gates satisfechos.

Se localizaron tambien las cohortes `gold_day_retro_20260910/input_v3` (diez
senales, incluida 2774) y `gold_first_two_retro_20260911/input_v2` (dos). Sus
contratos son `whole_day_gold_retrospective_v1` y `first_two_gold_retrospective_v1`,
no el contrato del adaptador actual. No se han renombrado ni forzado a pasar:
ampliar el contraste exige reutilizar su procedencia/contrato de conversion y
vincular sus ledgers, no omitir esos gates. Estos dias ya son retrospectivos.
