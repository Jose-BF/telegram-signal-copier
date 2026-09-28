# Recuperacion Y Validacion Nocturna

## Estado

Continuacion autorizada tras `listo`, sin mas cambios de modelo. Trabajo local
en `feature/gold-555-live-trial`, base
`7415941a410d18288fa4e08da76809f70b125efa`, conservando el checkout sucio.
Revision directa y reparacion en esta misma sesion, no revision independiente.
Sin commit, push, despliegue, reinicio remoto ni ordenes reales.

**No autoriza despliegue ni certifica el simulador.** E3-C conserva puntos de
revision; E4 solo tiene evidencia operativa parcial y E5 sigue pendiente.
No se han cambiado estrategias, lotajes, niveles de politica ni tolerancias.

## Correcciones Reproducidas

1. Recuperacion fuera del disparador: una marca de conciliacion permitia
   llegar al envio de un intento todavia PREPARED aunque el precio del nuevo
   tick no cumpliese la entrada. El guard previo al envio ahora comprueba el
   disparador. Las respuestas ya confirmadas siguen siendo recuperables sin
   exigir otro cruce. Se congela una copia de la pata para detectar mutaciones.
2. Stop Gold pendiente: la cola conservaba el SL mas fuerte, pero la memoria
   de estrategia guardaba el mas debil. Una gestion posterior podia deshacer
   esa proteccion. Recuperacion y trailing conservan el valor efectivo devuelto
   por la cola; trailing tambien solicita preservacion del pendiente mas fuerte.
3. Recuperacion al arrancar: se marcaba la pata completada antes de encolar
   proteccion/cierre. Ahora el marcador de conciliacion permanece hasta que
   ese paso termina. El fallo de escritura no se oculta y admite reintento.
4. Stop inventado desde el tick posterior: la recuperacion calculaba parte
   del SL con el precio al reconciliar. La respuesta durable ahora expone el SL
   de su solicitud congelada; Gold lo conserva y no usa el tick posterior como
   supuesto SL inicial. Esto no sustituye las reglas explicitas de trailing.

Los contraejemplos fallaron antes de cada reparacion por esas causas: primero
cuatro fallos de disparador/memoria SL, luego dos de persistencia al arrancar,
y dos de precio posterior. Los controles cubren BUY y SELL cuando aplica.

Se corrigio tambien una prueba previa: faltaba `policy_revision=0` en el caso
de volumen incorrecto, por lo que podia pasar rechazando la revision en lugar
del volumen. La nueva matriz incluye recuperacion valida y cambios individuales
de volumen, revision, comentario y generacion. La prueba de cancelacion avanza
el reloj sin alterar la caducidad congelada del intento.

## Evidencia Local

- Python 3.14.2: 6.267 pruebas globales superadas, 769 warnings, 434,03 s.
  Despues se anadieron dos controles con transporte real y broker ficticio;
  las 20 pruebas de `test_e3_c_recovery_boundaries.py` pasaron. Se conserva la
  distincion: esos dos controles no estaban en la recogida global anterior.
- Transporte completo: el intento no enviado queda NOT_SENT por el guard,
  sin `order_send`; la misma identidad llega a un unico envio ficticio cuando
  se proporciona un precio elegible. Se comprueba tambien el cierre del hijo.
- Auditoria de accesos MT5: baseline coincidente, cero modulos live con acceso
  nativo directo. No prueba por si sola que todo I/O sea no bloqueante.
- Python 3.11.9 aislado en
  `C:/Users/josea/AppData/Local/Codex/offline-runtimes/copier-python311`.
  No se modificaron PATH ni Python de la VM. Instalacion mediante el gestor
  Python, indice oficial con firma verificada. Dependencias de requirements
  mas matplotlib, necesario para recoger pruebas de investigacion y ausente
  en requirements. El primer intento de suite tuvo ocho errores de recogida
  por esa ausencia, no ocho fallos de la reparacion.
- Suite completa Python 3.11: 6.267 aprobadas, dos fallos y un warning en
  507,84 s. Ambos fallos eran de analisis sintactico: el auditor de categorias
  recorria un script historico inmutable de `runtime_data`, y la herramienta
  `analysis/mt5_tick_simulator.py` tenia un f-string no valido en Python 3.11.
  Se excluyo del primer auditor la carpeta de evidencias (sin tocarla) y se
  separo la construccion del nombre de cache en la herramienta. Las cuatro
  pruebas de ambos auditores pasan despues, manteniendo el baseline nativo.
  No se presenta aquella ejecucion global fallida como una global verde:
  se reutilizan las 6.267 aprobadas y se verifica la correccion acotada.
  Las dependencias nuevas de este entorno no son una reproduccion exacta
  de todas las versiones instaladas en la VM.
- Comprobacion focal final Python 3.14: 38 aprobadas, 76 warnings, 6,73 s.
  La misma comprobacion final en Python 3.11: 38 aprobadas en 13,05 s.
  Compilacion Python 3.11 de los modulos cambiados y del medidor aprobada.
  `git diff --check` sin errores; solo avisos de normalizacion LF/CRLF.

Informes JSON inmutables en `reports/`:

| Ensayo | Resultado y alcance |
| --- | --- |
| `e4-native-isolation-20260920-local.json` | Bloqueo nativo ficticio 60 s, Python 3.14: p99 16,44 ms y max 23,55 ms; control sin aislamiento 402,17 ms |
| `e4-native-isolation-20260920-python311.json` | Mismo mecanismo, Python 3.11: p99 16 ms y max 32 ms; control 406 ms |
| `e4-read-load-20260920-python311.json` | 120 s, 40 lecturas/s, cuatro productores: 4.800 FOUND, cero errores, cola vacia al final, hijo terminado; pausa max 32 ms |

Los relojes y la resolucion de Windows limitan la precision de las cifras;
no son medidas de latencia broker. En el ensayo de carga el padre paso de
30.412.800 a 31.645.696 bytes RSS y el hijo de 19.288.064 a 19.419.136.
Incluye memoria del propio medidor, no demuestra ausencia de fugas. Registro
del backend ficticio acotado a 32 llamadas. Herramienta limitada a 600 s,
50 lecturas/s y 16 productores, con salida exclusiva y hashes antes/despues.
No incluye escrituras broker, latencias de gestion ni el runtime de estrategia.
**No sustituye 60 minutos al doble de un pico de produccion medido.**

## VM En Solo Lectura

Consulta SSH sin ejecutar initialize/login ni importar la extension MT5:

- 20/09 20:33:53 UTC: cuatro CPU logicas, 5.222.824 KiB de memoria total,
  3.437.188 KiB disponibles y 19.633.205.248 bytes libres de disco.
- Procesos: launcher 4448, watcher 5260, bot 4712, terminal 5588. Esta
  enumeracion no certifica por si sola funcionalidad de trading.
- Heartbeat del bot avanza de 20:34:31 a 20:36:16 UTC, cola journal cero.
  Reporta flat, cero posiciones, senales y entradas pendientes; no se obtuvo
  una consulta independiente de exposicion al broker en esta inspeccion.
- Lectura acotada a los ultimos 64 KiB del diario: cobertura Telegram de
  ambos canales a 20:44:27 UTC, con `code_commit=7415941...`. Checkout remoto
  tambien en ese commit. No contiene las correcciones locales de esta sesion.
- Diario bruto observado: 7.081.241.505 bytes. No se recorrio entero ni se
  borro, trunco o comprimio. Disco disponible ahora no implica retencion infinita.
- Python remoto 3.11.9; MT5 5.0.5735, Telethon 1.43.2, pandas 3.0.2,
  numpy 2.4.4, pyarrow 24.0.0, Pillow 12.3.0, google-genai 1.73.1,
  dotenv 1.2.2. Sin metadata de numba. No se instalaron paquetes remotos.

## Lo Que Falta, En Orden

1. Cerrar la revision de arranque y propiedad del trabajador. Revisar el orden
   `resync -> restore_from_spool -> await recover_durable_candidate_entries ->
   recover_requested_candidate_closes`: comprobar con intercalado controlado
   que ningun monitor/pendiente actue antes de restaurar el cierre del proveedor.
   La correccion de una pata no demuestra atomicidad de todo el arranque.
2. Ensayo de padre muerto/hijo aun dentro de llamada nativa y doble arranque.
   Los locks de hilos del cliente y el lock del watcher no demuestran exclusion
   entre dos hijos de padres diferentes. Verificar exclusion de SO durante
   toda la vida del propietario y que no se despache mientras sobreviva el
   anterior. No resolverlo matando un MT5 live para probar.
3. Disco lento/lleno y prioridades. Ya existen tests de errores de persistencia,
   cola saturada y resultados tardios. Falta el ensayo combinado con runtime:
   incluir lecturas de IntentStore desde la ruta asincrona de entradas,
   proyeccion y recuperacion; comprobar pausas y no solo excepciones. El
   arbitraje trade/read no demuestra prioridad cierre/proteccion frente a entrada.
4. Congelar carga medible: tasas de lecturas, entradas, gestion y telemetria,
   concurrencia y recursos equivalentes a VM; 60 min al doble de ese pico.
   Si falta el pico de un componente, marcarlo desconocido, no inventarlo.
5. Contraste realidad/virtual por ambos canales con calendario y cobertura
   completos. Localizar la primera decision distinta y comparar trayectoria,
   no solo cierre: entradas, volumen, flotante, MAE/MFE, drawdown y costes.

Matriz minima del contraste: BUY/SELL, uno/multiples niveles cruzados, gaps,
spread variable, SL/TP y trailing, cierre proveedor durante envio, respuesta
tardia, parciales/PLACED/UNKNOWN, duplicados, ediciones Telegram disponibles
causalmente, reinicios, posiciones ya cerradas, varias cestas y medianoche.
Conservar huecos de ticks y de bot, distinguir precios Bid/Ask y no usar datos
futuros. Separar contabilidad observada de fills hipoteticos. Los casos 3086 y
28/08 son controles, no la muestra de certificacion. No seleccionar ventanas
por rentabilidad ni retocar tolerancias para encajar el drawdown.

Para publicar la reparacion de infraestructura, cerrar primero E3 y los gates
operativos aplicables de E4; no exigir la certificacion completa de estrategias
de E5 como nueva condicion universal. E5 puede aprovechar datos historicos
admitidos y continuar prospectivamente; no requiere esperar nuevas senales
para todo el trabajo offline. Antes de desplegar: revision conjunta, autorizacion de publicacion,
exposicion/pendientes frescos, despliegue controlado y comprobacion de version,
unico propietario, recepcion y gestion. Ningun ensayo aqui autoriza cambiar
la estrategia ni garantiza rentabilidad o ejecucion sin latencia.

## Identidad De La Reparacion

| Archivo | SHA256 |
| --- | --- |
| `position_lifecycle_monitor.py` | `039075321fad307ca20e9e25a75365f1dd7b91e8f78b2d9e16b21b6cbc76090a` |
| `durable_entry_execution.py` | `875a309dc50951ad1997605def1e52048cf57294e9d76d65a23fc33dae1a9c9a` |
| `tests/test_e3_c_recovery_boundaries.py` | `3275dd3c038d21f7fe6796c296ab8d1409b08beb20444bb7dd24b3b4c1c30c2c` |
| `tests/test_e3_c_third_repair_review.py` | `7cb9230637d38e42e270021de658efaabb69bceb405a95de279f0e8bc981a700` |
| `tests/test_durable_entry_execution.py` | `53ceb1f32d38a6aa5e4bafc955beb10b0a6212c103fbc00faea2631df8b80826` |

Conservar tambien los hashes del medidor y backend incluidos en cada JSON.
