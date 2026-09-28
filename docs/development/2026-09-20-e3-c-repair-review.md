# Revision De La Reparacion E3-C

Revision local del 20/09/2026 sobre `feature/gold-555-live-trial`, HEAD
`7415941a410d18288fa4e08da76809f70b125efa`. Los ocho hashes operativos de
[la entrega](2026-09-20-e3-c-repair-results.md) coinciden exactamente.
Se preserva el arbol sucio. No se modifica codigo operativo, ni se publica,
reinicia o conecta a la VM. Revision directa y segunda lectura independiente
de recuperacion/aplicacion tardia.

## Dictamen

No aceptar E3-C todavia. Los controles originales pasan, pero no cubren las
contrapruebas siguientes. E4 y E5 siguen pendientes; esta revision no evalua
rentabilidad ni verifica la version instalada en VM.

## Hallazgos Confirmados

### R1 / P1: F3 No Cubre Reinicio Ni Patas Tardias

Primer caso: el broker ejecuta la entrada Gold, SQLite conserva DONE y el
proceso termina antes de aplicar SL/TP exactos y registrar el primer fill.
El arranque (`main.py:4978-4987`) reconstruye posiciones antes que esperas;
`listener.py:8109-8111` omite la espera porque ya existe una Signal. La
recuperacion (`main.py:1870-1900`) copia SL/TP presentes en el broker y, sin el
evento del fill, caduca el plan. No consume el resultado durable para instalar
el TP que falta ni completar la politica. El trailing no repara ese TP.

`tests/test_e3_c_restart_review.py` persiste DONE en un subproceso que termina,
abre SQLite desde otra instancia y ejecuta resync, restauracion de spool y
restauracion de esperas en el orden del arranque. Con ticket 701 y fill
2500.25, deberia solicitar TP 2500.75: obtiene cero solicitudes, cero esperas y
`tp_by_ticket={}`. Las posiciones son simuladas; no es un arranque completo
del bot ni una prueba nativa. Es una reproduccion del hueco entre persistencia
y aplicacion que la prueba anterior, dentro del mismo proceso, no ejercitaba.

Segundo caso: `_process_candidate_entry_tick` comprueba caducidad y nuevo
cruce del precio antes de consultar el resultado de una pata ya enviada
(`position_lifecycle_monitor.py:1526-1556`, `1564-1612`). Si devuelve RECONCILE
y luego queda confirmada, un rebote impide recuperarla; la caducidad impide
recuperarla definitivamente por esta ruta. El ticket no entra en dca_tickets,
ni en el SL/TP exacto Gold ni en el recuento/SL de cesta Dubai. Conserva, como
mucho, la proteccion provisional enviada al broker.

Cuatro controles del consumidor con adaptador doble reproducen ambos canales,
con rebote y con caducidad: una consulta inicial ambigua, cero consultas
posteriores y cero tickets incorporados. No prueban por si solos persistencia
o ausencia de duplicados del broker; esos requisitos deben sumarse al ensayo
integral de la reparacion.

Correccion requerida: reconciliar y aplicar efectos ya enviados fuera de las
condiciones para nuevas entradas, tanto en marcha como al arrancar. Registrar
la aplicacion efectiva a Signal/protecciones de forma recuperable; la proyeccion
SQLite por si sola no acredita esa aplicacion. Repetir la recuperacion no debe
duplicar ordenes, patas ni acciones; probar tambien cierre del proveedor,
ticket ya cerrado y corte entre cada fase. No reactivar un plan caducado para
abrir nuevas patas: recuperar un fill existente no equivale a autorizar otro.

### R2 / P1: La Reentrega Oculta Cambios De La Orden

`durable_entry_execution.py:155-171` sustituye todo el payload solicitado por
el persistido antes de llegar a la comprobacion de conflicto de IntentStore.
No distingue un SL recalculado por la cotizacion de cambios en direccion,
volumen, magic o politica de proteccion. Con la misma identidad/revision,
preparar BUY 0.04 y reintentar SELL o 0.01 sigue pasando BUY 0.04 al cliente.
En PREPARED puede enviarse una orden distinta de la solicitada por el llamador;
en DONE puede asociarse el fill antiguo a la configuracion actual del llamador.

Cuatro contrapruebas con SQLite real y cliente que solo prepara fallan porque
no se rechaza el conflicto. Ninguna envia ordenes. Reparar preservando el
payload causal, pero comprobando los campos de identidad/riesgo antes de
reutilizarlo; declarar expresamente que valores derivados pueden congelarse.
No inventar una revision para eludir un resultado ambiguo. Mantener el control
original que reintenta con una cotizacion distinta.

### R3 / P2: Memoria Sin Limite Por Consulta Distinta

`mt5_runtime.py:99-122` conserva permanentemente cada combinacion de operacion
y parametros, incluso calculos de beneficio e intervalos historicos. No hay
expulsion, caducidad ni limite de cardinalidad. Las nuevas cotizaciones,
precios evaluados y ventanas crean claves nuevas durante toda la vida del bot.
`executor.py:965` es un llamador real de calculos repetidos. Los consumidores
de snapshots operativos estan en `main.py:399` y `main.py:489`; no necesitan
retener cada calculo historico.

Sondeo aislado, sin MT5, en Python 3.14.2: dos lotes consecutivos de 20.000
consultas PROFIT distintas, pasando respuestas pequenas a `_remember`, con
`tracemalloc` activado antes de los lotes:

| Consultas | Snapshots retenidos | Memoria Python retenida |
| --- | --- | --- |
| 20.000 | 20.000 | 19,15 MiB |
| 40.000 | 40.000 | 38,45 MiB |

Esto prueba crecimiento local, no un consumo ni un tiempo hasta fallo en VM.
Acotar scopes/retencion, preservar la vista completa de posiciones y las
cotizaciones por simbolo y agregar una prueba de estabilizacion bajo carga.
No arreglarlo volviendo a mezclar consultas por ticket con la cuenta completa.

### R4 / P2: Mezcla De Relojes Elude El Limite Historico

`mt5_read_protocol.py:112-116` exige un par completo, pero permite ademas medio
par de la otra unidad. Con segundos `1700000000..1700000001` y
`date_from_msc=0`, la validacion acepta un segundo; `_arguments` del trabajador
usa el inicio en milisegundos y el final en segundos. El intervalo efectivo
es `1970-01-01..2023-11-14T22:13:21Z`, 1.700.000.001 segundos, muy superior al
maximo de un dia. La variante con solo `date_to_msc` provoca KeyError.

Dos contrapruebas fallan. No se ejecuto esa consulta contra MT5: se comprobo
la validacion y la traduccion de argumentos. Rechazar cualquier mezcla parcial
o completa antes del transporte. Los llamadores actuales del proxy usan pares
homogeneos; el defecto afecta a la frontera que debe limitar las solicitudes.

## Evidencia De Esta Revision

`tests/test_e3_c_repair_review.py` y `tests/test_e3_c_restart_review.py`
agregan once controles que deben fallar hasta la reparacion, sin xfail.
`tests/test_e3_c_review_regressions.py`,
`tests/test_mt5_runtime.py` y `tests/test_durable_entry_execution.py` conservan
20 controles aprobados, incluidos los siete de la revision original.

```text
python -m pytest -q tests/test_e3_c_repair_review.py tests/test_e3_c_restart_review.py tests/test_e3_c_review_regressions.py tests/test_mt5_runtime.py tests/test_durable_entry_execution.py --tb=short --disable-warnings
11 failed, 20 passed, 15 warnings in 5.30 s
```

La bateria adicional de trabajador de lectura/escritura, recuperacion Gold,
listener, monitor, arranque E3-C y heartbeat supera 137 controles. La suite
global anterior es evidencia de la entrega, no fue repetida en esta revision
porque el codigo operativo no cambia y existen fallos nuevos reproducidos.

```text
python -m pytest -q tests/test_mt5_read_worker.py tests/test_mt5_trade_worker.py tests/test_gold_555_recovery.py tests/test_gold_555_listener.py tests/test_gold_555_monitor.py tests/test_e3_c_runtime_startup.py tests/test_runtime_exposure_heartbeat.py --tb=short --disable-warnings
137 passed, 65 warnings in 2.57 s
```

Entorno: Windows, Python 3.14.2, pytest 9.0.3. No sustituye la prueba de
Python 3.11 ni la carga, bloqueo nativo, disco y procesos huerfanos de E4.

## Siguiente Bloque

Sol/alto para reparar R1-R4 como un bloque, con R1 prioritario y compartido
por ambos canales. Conservar los controles anteriores, extender el reinicio
hasta aplicacion idempotente/protecciones y agregar retencion acotada medida.
Repetir suite global tras cambiar codigo operativo y solicitar revision
Astra/alto del bloque completo. No cambiar modelo por cada prueba.

Avance real: los siete controles originales pasan y 157 controles existentes
se han repetido con exito. No equivale a cerrar F3 ni aceptar E3-C. Solo se
agregan pruebas y este informe; no hay commit, push ni estado live verificado.
