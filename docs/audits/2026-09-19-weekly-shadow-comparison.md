# Comparacion Semanal De Produccion Y Sombras

Periodo: 14-18/09/2026. Preparado el 19/09/2026.
Estado: contabilidad nativa conciliada por posicion; sombras parciales,
diagnostic_only. No ranking certificado ni candidata promovida.

## Conclusion

Las sombras registraron actividad, pero NO cubrieron toda la semana. La
afirmacion previa de funcionamiento basada en ejemplos del viernes no
acreditaba continuidad. No hay registros de las candidatas para las tres
cestas perdedoras reales Gold: 2908, 2917 y 3003. Por tanto, estos datos no
permiten afirmar que una candidata resistiera las caidas de la semana.

El CSV inicial tampoco era una contabilidad completa: omitia cinco cestas
presentes en MT5. Las cifras preliminares anteriores quedan sustituidas por
los totales nativos siguientes; conservar el CSV original como evidencia.

## Produccion: Semana Completa Del Historial Consultado

| Canal | Cestas | Posiciones | Ganadoras / perdedoras | Neto EUR |
| --- | ---: | ---: | ---: | ---: |
| Dubai balanced | 17 | 30 | 6 / 11 | -104.86 |
| Gold 555 | 23 | 85 | 20 / 3 | -278.12 |
| Total | 40 | 115 | 26 / 14 | -382.98 |

Consulta nativa ampliada 13-20/09: 232 deals, de los cuales 230 son movimientos
de trading y dos son ajustes de saldo, excluidos del P/L. Se verificaron IDs
unicos, identidad por comentario de apertura y position_id, volumen abierto
igual al cerrado, y profit + commission + swap + fee de todos los deals.
Las 115 posiciones tienen aperturas y cierres completos. Todas sus fechas
nativas estan entre el 14 y el 18. Calendario del broker, no conversion UTC
inferida del timestamp. Moneda EUR confirmada en cuenta y contrato del bot.

La cuenta conectada coincide con el login configurado y servidor/moneda del
contrato monetario actual del bot. El nombre antiguo de servidor en .env es
VantageInternational-Demo; terminal y contrato usan VantageMarkets-Demo.
No se cambio cuenta, servidor, configuracion ni estado de trading.

Filas ausentes del CSV inicial:

| Senal | Neto nativo EUR |
| --- | ---: |
| canal1_22652 | -25.00 |
| canal2_2917 | -85.87 |
| canal2_2919 | +19.93 |
| canal2_3005 | +19.93 |
| canal2_3171 | +1.75 |

Las otras 35 cestas coinciden al centimo con el CSV. No se atribuye la causa
de las omisiones a un componente sin investigar su secuencia de eventos.

## Sombras: Solo El Subconjunto Con Registros Originales

Son EUR simulados registrados en su momento, NO resultados reales ni una
simulacion nueva. Dentro de cada canal se usan las mismas identidades para
las tres politicas. No comparar estos totales con la semana real completa.

| Canal | Politica | Senales comunes | Resultado registrado EUR |
| --- | --- | ---: | ---: |
| Dubai | Balanced, control | 5 | +20.12 |
| Dubai | Frontloaded 30m | 5 | +2.99 |
| Dubai | Frontloaded 40m | 5 | +0.19 |
| Gold | 555, control | 9 | +69.74 |
| Gold | B210 | 9 | -23.53 |
| Gold | C490 | 9 | +73.55 |

Cobertura del universo identificado en diario/eventos/historial: 5 de 17 IDs
Dubai y 9 de 27 IDs Gold. No se afirma cobertura de todas las publicaciones
Telegram. Las nueve Gold incluyen dos avisos en los que 555 no abrio posicion
(entry_expired); se conservan para comparar la decision de entrar. No son
nueve operaciones reales 555. Faltan 12 IDs Dubai y 18 Gold en las sombras.

Desglose por fecha UTC de registro de la senal:

| Dia | Dubai control / 30m / 40m | Gold control / B210 / C490 |
| --- | --- | --- |
| 14 | sin cobertura | sin cobertura |
| 15 | +7.71 / +6.57 / +6.57 (1 ID) | +4.33 / +0.78 / +7.10 (1 ID) |
| 16 | sin cobertura | sin cobertura |
| 17 | sin cobertura | +21.76 / -39.71 / -1.35 (3 IDs) |
| 18 | +12.41 / -3.58 / -6.38 (4 IDs) | +43.65 / +15.40 / +67.80 (5 IDs) |

No puede atribuirse robustez semanal a C490 por superar en 3.81 EUR al control
en este subconjunto. En canal2_3096, el control registro +20.02 EUR, B210
-60.12 y C490 -29.45. Tampoco evitar perdidas es una propiedad general de
las alternativas. No se han igualado sus riesgos ni certificado sus fills.

## Tres Perdidas Gold Sin Sombra

| Senal | Dia nativo | Produccion EUR | Candidatas |
| --- | --- | ---: | --- |
| canal2_2908 | 14/09 | -302.38 | sin registro |
| canal2_2917 | 14/09 | -85.87 | sin registro |
| canal2_3003 | 16/09 | -165.88 | sin registro |

No se cuentan como cero ni se eliminan del denominador. Reconstruirlas
despues seria un backtest retrospectivo, no recuperar observacion prospectiva.

## Prueba De Fidelidad Del Control

Antes de comparar candidatas se emparejo la politica de control de cada canal
con su resultado observado en MT5, por identidad de senal y con cardinalidad
uno a uno. El diagnostico conto igualdad del numero de entradas y del
resultado al centimo para cada senal; una suma total parecida no basta porque
errores positivos y negativos pueden cancelarse. Estos contadores no son una
certificacion de decisiones, trayectoria ni ejecucion hipotetica. La igualdad
monetaria exacta si es exigible a la contabilidad de los mismos deals reales;
no se promete para fills hipoteticos. No se cambian gates del motor.

No existe un dia certificado completo entre el 14 y el 18/09: el 14 y el 16
no tienen sombras, el observador se desactivo durante el 15 y el 17, y el 18
contiene reinicios y cobertura parcial. El bloque historico mas cercano a una
matriz completa es el 27-28/08: 18 senales y tres politicas por senal, sin
huecos de candidata. Aun asi, su propio informe no permitia una afirmacion
operativa y solo 15 de las 18 filas estaban conciliadas como ejecucion MT5;
las otras tres fueron avisos Gold sin posicion observada.

| Cohorte | Senales | Entradas iguales | EUR iguales al centimo | Ambas iguales | Real EUR | Control sombra EUR |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 27-28/08, matriz historica | 18 | 11 | 3 | 3 | +93.32 | +147.86 |
| 14-18/09, solo pares disponibles | 14 | 13 | 8 | 8 | +117.24 | +89.86 |

La discrepancia historica se concentra especialmente en Gold el 28/08: el
real suma +23.65 EUR y el control simulado +89.79 EUR; solo 4 de 9 senales
igualan entradas y 2 de 9 igualan tambien el dinero. En Dubai ese dia las
cinco senales igualan el numero de entradas y los totales quedan proximos
(+60.29 frente a +58.97), pero ninguna coincide al centimo individualmente.

El subconjunto reciente muestra coincidencia de los contadores en algunos
tramos: Gold el 18/09 coincide en las cinco senales y en +43.65 EUR. No es
suficiente para certificar la semana: Gold 3086 registra +31.92 EUR reales y
+1.74 EUR en sombra, y las tres grandes perdidas semanales no fueron
observadas por las sombras.

Conclusion: las sombras sirven ahora como evidencia diagnostica, pero sus
rankings monetarios no son fiables todavia. Primero hay que explicar y
corregir las divergencias del control. Un dia prospectivo no certifica
fiabilidad general: hace falta una matriz amplia historica, sintetica y
prospectiva con cobertura y criterios definidos previamente. El
[plan vigente](../development/2026-09-19-runtime-simulation-validation-plan.md)
separa contabilidad, decisiones y ejecucion hipotetica, incluyendo trayectoria
de riesgo. No reduce las exigencias de admision de los informes anteriores.

## Fallos Comprobados

1. Desactivacion del observador por `RuntimeError: shadow journal write not
   confirmed`, operacion process_tick_batch, el 15/09 a las 13:14:33.844 UTC
   y el 17/09 a las 12:48:54.869 UTC. Hay nuevos arranques el 17 y el 18.
   El error explica interrupciones, no prueba aun la causa fisica de cada
   fallo de escritura ni el estado del observador anterior al tramo leido.
2. El informe remoto antiguo seguia limitado al 27-28/08; su existencia no
   acreditaba seguimiento semanal. La configuracion habilitada tampoco.
3. El control no coincide integralmente con produccion. Ejemplo Gold 3086:
   sombra +1.74 EUR, real +31.92 EUR. Ejemplo Dubai 22781: tres entradas
   simuladas frente a dos reales. Hay que distinguir modelo de ejecucion,
   politica e intervenciones antes de atribuir la causa o admitir un ranking.

Sobre las mismas identidades con sombra, produccion suma +17.32 EUR Dubai
y +99.92 EUR Gold; esta ultima incluye siete cestas ejecutadas y dos avisos
sin ejecucion observada. Estos numeros no corrigen ni sustituyen la sombra.

## Fuentes Y Verificacion

Directorio: runtime_data/weekly_review_20260919/.

- `shadow_week_20260919.jsonl.gz`: 3557 eventos seleccionados. SHA256
  401ba7209c07560ec13d6d370b814441e6b8f2f3ac167ae5dad753601424c66e.
- Manifiesto asociado: bytes [3300001656, 7078987060) del diario de la VM,
  snapshot fijo, sin cola truncada, SHA256 del tramo
  77301defd86751cd2f25c5e28b5020dffdc2e244b3252bf55de04af1a4d6914b.
  El primer evento seleccionado es del 11/09, anterior al periodo. Lectura
  limitada con prioridad baja; no se descargo el diario entero.
- `native_week_deals_20260919.json`: SHA256
  91723c6cc65070407bbb5a79721a6109896450354fe20b74f3a344ba7206f021.
- `native_week_reconciled.json`: posiciones, cestas, ajustes de saldo y
  diferencias frente al CSV. `shadow_week_diagnostic.json`: filas, ausencias,
  importes, estados, fallos y contraste del control con produccion.
- `control_parity_diagnostic.json`: union uno a uno y metricas de fidelidad
  del control para la matriz 27-28/08 y el subconjunto semanal disponible.
- Registro original y ultimo estado anterior al corte requeridos para
  contar una sombra. Estados recuperados posteriores no sustituyen esos
  registros. Hashes de estados, enlaces de cadena, identidades, fingerprints,
  cierre de posiciones y suma de dinero comprobados en las filas utilizadas.
- No se ha ejecutado el settlement contra una cinta independiente ni
  certificado la fidelidad del motor: el resultado permanece diagnostic_only.
- `python -m pytest -q runtime_data/weekly_review_20260919/test_weekly_review.py`:
  9 aprobadas. Incluyen integridad de descarga, UTC, costes, cash separado,
  duplicados, volumen incompleto, ausencias, recuperacion y corrupcion de hash.

## Siguiente Paso

Investigar y corregir captura/supervision de sombras sin cargar el bot;
contrastar el control en los casos discrepantes; despues evaluar cobertura
de ticks/mensajes para reconstruir las tres perdidas con las politicas
congeladas. No abrir nuevas variantes ni promover C490 con estos resultados.
No se modifico codigo/configuracion de produccion, no hubo push, reinicio,
cambio de politica ni ordenes. Solo extraccion y consultas de historial.

## Ampliacion Del Recorrido Gold (23-09)

Se verifico la identidad de cada pierna en las siete cestas Gold con control
sombra (`gold_broker_comment_leg_binding_7controls_v1.json`, SHA256
`D3F1B2975E1986D7D0AE975CB5D41361E5B33FE8A3DF0E330A8B60251D8ED112`).
Tres cestas tienen ademas solicitud/resultado vinculados en el diario del
bot; las otras cuatro solo identidad por comentario/deal del broker. Ningun
comentario demuestra por si mismo que la decision del bot fuera igual.

El contraste tick a tick con FX causal
(`gold_broker_bound_full_tick_path_7controls_v2.json`, SHA256
`BE8B591879F9105A01DAEB16BF38ECB0A7FE4A401E908C687D559AFCBF6737C5`)
admite cuatro cestas de las siete: `2977`, `3148`, `3168` y `3171`; las cuatro
presentan diferencias de exposicion/flotante. En `2977` el neto final es
4,33 EUR en ambos recorridos, pero la exposicion difiere en 126 de 7.424
marcas y el drawdown reconstruido en ticks es 3,88 frente a 3,29 EUR.
`3086`, `3096` y `3156` siguen explicitamente fuera del contraste de riesgo
porque su conversion EURUSD no cubre toda la ruta de modo estrictamente
causal. Hay otras 16 cestas Gold nativas sin control sombra comparable.

Estos importes son reconstrucciones sobre deals/ticks y estados sombra ya
registrados, no equity MT5 independiente ni replay autonomo de Telegram. La
ampliacion muestra que igual beneficio no implica mismo recorrido; no
certifica el simulador ni una estrategia. Pruebas relacionadas: 66 aprobadas.
Todo el trabajo fue local, sin push, despliegue ni cambio de politica live.

El informe sucesor `gold_broker_bound_full_tick_path_7controls_v3.json`
(SHA256 `1DE61A5BF61827521FE3D0C0ADB4569C4C1CD8CB9FD3B46D729A0A1FE751B5CF`)
conserva los cuatro contrastes completos y anade comparaciones parciales
de exposicion/realizado para las otras tres, sin admitir FX posterior:
`3086` difiere en exposicion en 3.162/4.053 marcas (15 de riesgo con FX
caducado); `3096`, 10.781/17.096 (60); `3156`, 155/5.587 (7). En estas
tres el drawdown integral y su delta quedan **sin resolver**; los maximos
monetarios calculables solo corresponden a marcas conocidas. No debe
interpretarse que el v3 convierte una ruta bloqueada en ruta verificada.
