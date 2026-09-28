# Revision Nocturna Del Jueves

Revision recibida realmente a 19:01:28 UTC, 21:01 Madrid. No equivale a la
revision puntual de las 19 Madrid. Se mantiene la peticion del usuario:
dejar el bot corriendo y preparar mediciones naturales sin publicar arreglos.

## Capturas Retenidas

Final de la ventana 18-20 Madrid ejecutada a 18:00 UTC, salida cero. Corte
18:00:02.835561 UTC, finalizacion 18:00:14.668379. Doce archivos verificados,
24642 filas y 44468855 bytes de delta completo. 46 observaciones raw, cero
conflictos o ausencias causales en este corte; no hereda una admision global.

Base: `runtime_data/calculator_delivery_20260910/forward_evening_v1/`.
Manifiesto final `f9cf24324d088c263bd43edd36d922b4294bc3a047a20572f5f3b1e1cde609ef`;
predecesor `c71ca29fea93cbf51cadd13645f0588ef475f0cb9bf0a65e656ee7a47cc45405`.
ZIP `43e3b5a4263ebe1cedd20f593741f7acf76c2f3710eda51dcafc7fa6bdc0ff21`.
Suplemento de precios 15-16 UTC completado, cuatro archivos verificados:
manifiesto `c4dc82efc0711204b4bdcc8554508fb0bf29bb993d1037455a1913b61d05ff5c`.

La retencion comprobo hashes y bytes antes de cargar resultados del broker.
Evidencia: `runtime_data/day_review_20260910/review_2100/capture_retention.json`.
Solo las tareas propias de suplemento y final ya terminadas fueron retiradas
a 19:18:50 UTC. XML, resultados cero y hashes conservados en
`review_2100/completed_tasks_cleanup.json`, SHA256
`a33fc393295d9083cba93b6f084508a6fa5495cac4cf3f6a55fa226a03025b5b`.
No se modificaron el watcher, bot o terminal.

## Simulacion Independiente

Se ejecuto primero la CLI `tools/run_simulator_forward.py produce` con el
protocolo de tarde congelado y fuentes de entrada. Dataset preparado desde
46 observaciones, **cero senales Gold elegibles**. La ejecucion `run` termino
`incomplete`: cero evaluaciones, entradas y salidas; motivos
`insufficient_natural_signals` e `insufficient_completed_simulated_trades`.
No es concordancia ni rentabilidad cero. La cartera permanece bloqueada/null.
Dubai y posiciones anteriores no se convierten en controles Gold independientes.

Resultados: `review_2100/independent_dataset/`, `independent_run/run_report.json`
y `independent_run/independent_results.json`. SHA256 del ultimo:
`9d62ced9e88ef3a77870e6dc82e71f8008299dc5244130c19cde03d1ffd6138f`.

La CLI publica `tools/compare_simulator_forward.py` se intento despues y
bloqueo antes de leer resultados nativos: cinco productores no estaban en el
registro explicitamente admitido por el protocolo, aunque sus fuentes estaban
preservadas y probadas. Faltaban `compare_causal_controls.py`,
`audit_causal_lineage.py`, `audit_management_capture.py`,
`prepare_simulator_forward.py` y `run_causal_controls.py`, todos bajo `tools/`.
La preparacion anterior no verificaba la cadena completa del comparador.

Se conserva el fallo; no se anaden referencias retroactivamente al protocolo
de tarde ni se relaja `_producer`. Este defecto de preparacion y la ausencia
de muestra son limitaciones distintas. No se produjo un informe de comparacion
aprobado ni se inyectaron fills reales en el motor.

## Protocolo Nuevo De Viernes

`runtime_data/calculator_delivery_20260910/forward_friday_v2/` reemplaza solo
la preparacion futura v1, que se conserva. Ventana Sep11 13-15 UTC, 15-17 Madrid.
SHA256 `5d882fdfc67b385d4ee035d7fd4522b811fde727cdd1ddf218e11a112a967e97`.
Incluye los doce productores del comparador, comprobados con la guarda real
antes de la ventana. No cambian reglas, ejecucion, tolerancias ni presupuesto.

Las 425 fuentes y wrappers siguen iguales a las 5093 pruebas completas
aprobadas en `review_1515/verification_v2`. Copia ejecutable verificada:
`forward_friday_v2/verified_sources.zip`, SHA256
`cf02fe4d36d66493aa9f25a6f78db4ef435891d811d1a4f4c9b2a8f7205ccdd3`.
Reproduccion separada del fallo viejo y comprobacion del nuevo registro en
`review_2100/protocol_binding_incident.json`; no certifica un contraste futuro.

La preparacion operativa tiene dos fases: precaptura a 12 UTC / 14 Madrid,
con delta continuo desde la final de hoy; final a 15 UTC / 17 Madrid, que
exige el ancla y recibo exitoso de la precaptura. No saltar bytes si falta.
Auxiliares locales en `runtime_data/day_review_20260910/operations/`.
Segunda revision local: 60/60 comprobaciones en PowerShell 5.1, sin ejecutar
los programas operativos. La primera revision detecto cuatro problemas de
recibo previo, intento anticipado, identidad del bot y logs sobrescritos;
se corrigieron en los auxiliares y se conservaron ambas revisiones.
Informes y pruebas: `review_2100/operation_review/REPORT.md` y `REPORT_V2.md`.

Registro REAL a 19:48:42 UTC en la carpeta externa al checkout live
`C:/Users/bot/codex-control/simulator_friday_20260911_5d882fdfc67b/`.
Dos tareas propias confirmadas Ready por lectura posterior:

| Tarea | Proxima ejecucion UTC | Madrid |
| --- | --- | --- |
| `Codex-Simulator-20260911-1300-pre_window-5d882fdfc67b` | Sep11 12:00 | 14:00 |
| `Codex-Simulator-20260911-1300-final-5d882fdfc67b` | Sep11 15:00 | 17:00 |

Interactive/Limited, ejecucion unica, ventana de lanzamiento de dos minutos,
sin StartWhenAvailable, limite de 12 minutos y logs ocultos por intento con
UUID. La pre exige hasta 1 GiB/2 millones de filas/600 s; final 512 MiB/un
millon de filas y los limites del wrapper. Sin truncar ni ampliar tras fallo.
Final exige tanto el manifiesto/archivos de pre como su recibo completo de
exito; bot PID/creacion/sesion esperados se comprueban antes y despues.
No se ha lanzado anticipadamente ninguna de las dos capturas futuras.

Recibo descargado `forward_friday_v2/registered_tasks.json`, SHA256
`d517efeaf97773b47856bb6b8ea79544bca179a7b23f9c967d57e2f3c3344a43`.
XML releido sin cambios y seis archivos iguales a los locales en
`registration_readback.json`; propiedades efectivas en `task_properties.json`.
El resumen de consola del registrador salio con campos null por Select-Object
sobre hashtables, pero el recibo contiene los valores completos y se verifico
directamente contra Windows. No se uso aquel resumen para afirmar el registro.
El XML omite ciertos valores predeterminados: Enabled, RunLevel y
StartWhenAvailable fueron verificados mediante sus propiedades CIM reales.

`review_2100/closeout_verified.json` verifica fuentes estables, hashes locales/VM,
XML/propiedades, horarios, informes y resultado nativo a 19:51:43 UTC.
Estar programado no prueba ejecucion futura ni suficiencia de la muestra.

## Contabilidad Observada Nueva

Auditoria acotada en `review_2100/native_audit/`. Tras el corte anterior,
15 posiciones nuevas, seis senales y 30 deals: **20.94 EUR netos**. Los 15
importes coinciden al centimo con fills reales, volumen y FX causal; edades
FX 40-1799 ms, sin ampliar tolerancias ni usar time_msc del servidor como UTC.
Comision, fee y swap sumados desde todos los deals, no supuestos como modelo.

| Senal | Posiciones nuevas | Neto EUR |
| --- | ---: | ---: |
| Gold 2795 | 4 | 13.33 |
| Gold 2801 | 5 | 19.79 |
| Dubai 22442 | 2 | -24.96 |
| Dubai 22447 | 1 | 8.18 |
| Dubai 22452 | 2 | -4.66 |
| Dubai 22456 | 1 | 9.26 |

Las 19 posiciones y 8.38 EUR ya conciliadas no se vuelven a declarar
pendientes. Otras dos posiciones anteriores, 4.30 EUR, se conservan aparte.
El conjunto nativo retenido tiene 36 posiciones y 72 deals, no 72 operaciones.
La ventana independiente 16-18 UTC tiene cero deals Gold; contiene el cierre
Dubai de 9.26 EUR abierto a 15:54:27.930, no una entrada nueva de esa ventana.
No quedan posiciones nativas reconstruidas abiertas al corte final retenido.

Dubai 22442 duplica signal_closed para las mismas dos posiciones: -24.96 EUR
por emision no son -49.92 EUR reales. La tercera pata fue rechazada con 10016;
los rechazos de cierre posteriores al SL no son nuevos fills. Permanecen
todos los eventos. Conexion MT5 perdida 10.023 s y recuperada; aviso monetario
posterior tambien recuperado. Hechos, horas y referencias en `incidents.md`.

No se mezclaron las cintas nativas: 21504 pares distintos en solapes completos
se conservan como diferencias, no como cotizaciones fabricadas o eliminadas.
Cada fill usa su tramo declarado. Esto concilia dinero observado, pero no
certifica continuidad global de ticks ni la precision de fills alternativos.

Verificacion separada `native_audit/verify.py`: 82 fuentes rehasheadas, 876
eventos y 216269 filas nuevas comprobadas, ocho grupos de comprobaciones PASS.
Recibo `verification_final.json`, SHA256
`b3258ae5323a9792cfe6933be100d4813b5ca870920237cd2e10b26fafa52fcd`.
Manifiesto final de salidas `efa4003d96c42067918495fb3b6506d3c3bb73851c24ae042586ef79a79b32c6`.
Los agentes dejaron sus archivos, pero no completaron el cierre por limite
de uso; el coordinador reviso las entregas, cerro ambos agentes y ejecuto la
verificacion final. Un intento repetido de verificar el constructor alcanzo
el recibo existente y rechazo sobrescribirlo; se conservo y se verifico su
hash con el verificador final. No se reemplazaron evidencias para hacer pasar.

## Estado Y Limites

VM comprobada a 19:49:35 UTC: bot 5216, sesion 2 y creacion original
1789026577526 ms. Heartbeat de 19:49:24 con cero posiciones, senales y entradas
pendientes. Checkout `892bc33c8` limpio, terminal 6312 y cadena Python
4824/4468/5216 originales. No hay commit, push, reinicio ni ordenes enviados por esta revision.
Las reparaciones del bot permanecen locales y no activadas.

La conciliacion nativa incremental verificada no sustituye la ejecucion
independiente. Siguen abiertos el contraste natural,
la admision historica aplicable y la escala. No se inicia busqueda masiva ni
se elige candidata: hace falta la revision conjunta del experimento.
