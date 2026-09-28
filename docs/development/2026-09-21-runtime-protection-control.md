# Protecciones Reales Sobre El Estado Del Simulador

## Alcance

Este bloque conecta, exclusivamente en pruebas locales, las protecciones
Gold555 con el estado causal del motor existente. No es una nueva estrategia,
una seleccion rentable ni la certificacion completa del simulador.

Cadena ejercitada:

1. Fill generado por el modelo, con ticket/volumen/precio declarados.
2. Helpers reales de proteccion inicial y de pata adicional Gold555; tambien
   trailing canonico invocado sobre cotizaciones elegidas.
3. Persistencia y bucle `_run` reales de `PendingQueue`.
4. `DurableExecutionService`, `MT5ReadClient` y preparacion/commit/validacion
   reales de `TradeWorker`.
5. Backend falso que modifica el mismo `ProtectionBook` del motor.
6. Cierres nativos modelados y trayectoria monetaria de ese mismo libro.

La frontera nueva de research contiene solo contratos puros y callbacks del
modelo. No importa el runtime ni adquiere capacidad para enviar ordenes reales.
La integracion del runtime vive en `tests/`; el cliente sustituye unicamente
el arranque del transporte por uno en memoria con mensajes tipados. No prueba
aislamiento entre procesos, un terminal MT5 ni latencias reales.

## Controles

El modulo integrado contiene 25 pruebas; siete validan el contrato puro y 18
recorren la cola/cliente/worker. Otros 15 controles cubren la sincronizacion
entre el hilo de transporte y el unico propietario del estado simulado.

- BUY y SELL: SL y TP iniciales se agrupan, instalan y confirman.
- Cierre por TP antes de entregar la respuesta de instalacion al bot.
- SL no permitido: primero se instala TP y despues SL, preservando TP.
- Revision nueva durante una respuesta antigua: no se pierde la nueva orden.
- Revision nueva antes de commit: el payload obsoleto no se envia.
- Posicion cerrada entre preparacion y commit: rechazo sin recrearla.
- Respuesta desconocida tras un efecto: proteccion instalada, sin confirmacion
  falsa ni reenvio ciego en la cotizacion siguiente.
- Fallo de persistencia: ninguna orden nueva llega al backend.
- Identidad de otro canal: no se modifica una posicion ajena.
- Dos patas, gap y stop movil: cambios de exposicion, flotante y cierres.

El rechazo nativo 10036 termina esta modificacion como `DROP/REJECTED`; una
ausencia detectada antes del envio termina como `DONE`. Ambos retiran la
modificacion innecesaria sin confirmar niveles. No se cambiaron estas reglas
para uniformar el resultado de la prueba ni se extrapolan a cierres a mercado.

La revision independiente encontro dos defectos del propio harness: una espera
interna podia abandonar una llamada todavia pendiente, y respuesta/abort podian
finalizar el mismo Future simultaneamente. Se elimino ese timeout interno y se
sincronizaron las finalizaciones. Ahora el cliente real conserva el control de
los plazos y la ocupacion hasta respuesta o abort. Los controles de mailbox
se vuelven a ejecutar con el mismo Python 3.11.9 que el resto del paquete.
Los dos controles adicionales impiden que el historial no implementado se
presente como un historial conocido y vacio: esas consultas fallan explicitamente.

## Evidencia Y Riesgo

Primer paquete local: `reports/e5-runtime-protection-control-20260921/`.
Repeticion con espacio disponible:
`A:/Codex-test-artifacts/copier-runtime-protection-20260921/retry/`.

- `sources.json`: hashes de fuentes, entorno e identidad del motor.
- `focused.xml`: 109 pruebas aprobadas, cero fallos/errores/omisiones;
  44.69 segundos de pytest. Incluye regresiones previas de cola y stops Dubai.
- `control-manifest.json` y `controls/`: 18 escenarios con inputs completos,
  mensajes tipados, llamadas al backend, protecciones, riesgo y estado final.
- `path-verification-v3.json`: 18 secuencias post-evento completas comparadas
  contra valores esperados explicitos, y 54 alteraciones detectadas: cotizacion
  ausente, flotante cambiado un centimo y reloj incorrecto.
  Horizonte esperado explicito por escenario; los casos completados requieren
  libro plano, una salida por entrada, resultado conocido y ausencia de blockers.
- `retry/full.xml`: 7066 pruebas aprobadas y un fallo por longitud de ruta
  temporal en Git/Windows; 7067 recogidas, cero errores/omisiones, 870.55 s.
- `retry/telemetry-short-path.xml`: las 47 pruebas del modulo afectado pasan
  sin cambios de codigo y con un basetemp mas corto. `followup-verification.json`
  comprueba que fuentes e implementacion siguen iguales al ensayo global.

El primer ensayo global recogio 7065 pruebas, pero C: alcanzo cero bytes libres.
Hubo fallos y la finalizacion dejo un JUnit truncado de 38 bytes. No existe un
recuento global valido ni se clasifican esos fallos como defectos del motor sin
investigarlos. Se conservan salida, XML y `full-failure.json`. El archivo final
del runner tambien fallo al intentar leer ese XML incompleto.

La migracion de temporales propios a A: fue parcial por atributos/permisos;
la limpieza posterior fue denegada por la herramienta. No se insistio mediante
otra via ni se borraron logs del bot. C: seguia casi lleno en ese momento. Las nuevas pruebas
usan TEMP/TMP, directorio runtime, basetemp, caches e informes en A:, con control
previo de espacio; el runner ahora registra un XML invalido en vez de perder su
resumen. El segundo bloque focalizado aprueba 111 pruebas, incluidas las dos
regresiones del historial. Las 18 trayectorias y 54 negativos tambien se han
verificado sobre los nuevos controles. La repeticion global termino con el
unico fallo descrito: `test_publication_recovers_only_an_abandoned_index_lock[False]`
no pudo indexar un archivo bajo una ruta temporal excesivamente larga. No se
alteraron reglas ni configuracion Git global para ocultarlo: se repitio todo
el modulo con una ruta corta y pasaron sus 47 pruebas. La limitacion de rutas
largas no queda corregida en produccion; debe considerarse al elegir rutas de
trabajo y verificar la publicacion en VM.

Esta evidencia combina el ensayo global y una comprobacion acotada con fuentes
sin cambios; no se declara una tercera ejecucion global enteramente verde.
SHA-256 de JUnit global:
`e44949e29947c19b20202aeadab51509ef41a032e6546d5252470ef7fbb81e9e`.
SHA-256 del modulo repetido:
`4945bc86645ec00301729f4840e6d5e701bbc0c9c65b5c8258f1faf595f67e11`.
Una lectura posterior encontro 12,599,640,064 bytes libres en C:. No se atribuye
esa recuperacion a la migracion parcial realizada aqui, que solo libero unos
pocos MB. Las evidencias nuevas permanecen en A: y no se ha limpiado el bot.

El riesgo bruto conserva fases diagnosticas como `quote_open`, `entry_fill`
y `settled`. No se mezclan para certificar drawdown. El contraste adicional
toma el ultimo estado `settled` de cada cotizacion, conserva todos los ordinales
hasta el corte del escenario y verifica realizado, flotante y volumen. Para
las dos patas verifica tambien identidad y precio de cada posicion.

Ejemplos sinteticos, expresados en EUR bajo los inputs declarados:

| Escenario | Resultado Final | Drawdown Post-Evento |
| --- | ---: | ---: |
| Una pata, caida directa | -80.80 | 80.80 |
| Misma salida, subida previa | -80.80 | 100.00 |
| Dos patas, gap a traves de ambos stops | -240.40 | 240.40 |
| Trailing Gold555 antes de reversal | -119.60 | 120.40 |
| TP inicial | 2.00 | 0.80 |

La caida de dos patas usa spread 0.20, contrato 100, volumen 0.04 + 0.03 y
conversion identidad. No se limita artificialmente la perdida al stop deseado.
Son controles del mecanismo, no resultados del historial ni valoracion nativa.

Las trazas cubren escenario y cleanup; no son checkpoints atomicos de todo el
runtime. Los snapshots de libro/riesgo se toman antes del cierre del harness,
pero los mensajes del cliente pueden incluir despues su shutdown. Los casos
abiertos o pendientes conservan resultado final nulo, no beneficio cero.

## Lo Que Sigue Abierto

- Generacion real de primeras entradas y DCA, protecciones provisionales y
  ajuste tras fill. En estos controles el fill sigue siendo del modelo.
- Monitor completo de Dubai y Gold555: orden de lecturas, deduplicacion,
  cadencia y gestion compartida, sin activar helpers a mano.
- Historial de deals/ordenes completo, valoracion nativa y payloads de cuenta.
  El backend de este bloque solo representa posiciones/protecciones del control.
- Calibracion de fills, rechazos y tiempos contra varias ventanas historicas
  completas; igualdad independiente de observaciones y decisiones.
- Recuperacion integral tras reinicio, evidencia VM, validacion no usada para
  ajustes, admision de busqueda y publicacion autorizada.

Siguiente frontera: entradas reales del controlador sobre el mismo libro,
sin conservar simultaneamente un generador de entradas del modelo. Primero
conectar el estado de apertura/preparacion/fill/ACK y sus protecciones; despues
el ciclo completo del monitor. Este control no sustituye ninguno de esos pasos.

Puntos concretos ya localizados para ese siguiente bloque:

- `listener._open_gold_555_confirmed_intent` y
  `position_lifecycle_monitor._process_candidate_entry_tick` deben llamar al
  `DurableEntryExecutor` real. No sustituir `open_market` por un fill prefijado.
- Primera apertura Gold555 envia SL provisional, pero no TP; tras el fill
  conserva el SL mas fuerte entre provisional y calculado y solicita el TP
  anclado al fill. Es distinto de abrir ya con ambos niveles definitivos.
- El backend del modelo debe decidir precio/ticket/volumen sobre la cotizacion
  disponible al ejecutar, y separar ese efecto de la respuesta al controlador.
- Rechazo, fill antes de ACK, cancelacion/revision antes de commit y resultado
  incierto deben conservar exposicion y reservas sin crear entradas duplicadas.
- El historial necesario para reconciliar debe implementarse sobre ese mismo
  libro o quedar no disponible, nunca sintetizar una ausencia conocida.
- Reloj de estrategia, expiracion y estado recuperado necesitan un contrato
  explicito. No ajustar una demora por senal para forzar coincidencia historica.

No hubo commit, push, despliegue, reinicio VM ni ordenes reales. El objetivo
general permanece activo.
