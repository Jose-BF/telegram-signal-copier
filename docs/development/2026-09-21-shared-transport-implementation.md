# Recurso De Transporte Compartido

## Continuidad Y Alcance

La vuelta anterior produjo progreso verificable: gestion condicionada sobre
18 senales, 6.394 pruebas aprobadas y revision acotada. No fue una espera ni
un cierre del objetivo. Esta entrega avanza una frontera pendiente del plan:
el recurso compartido de MT5 entre ambos canales. Rama
`feature/gold-555-live-trial`, base `7415941a410d18288fa4e08da76809f70b125efa`.
Cambios previos preservados. Sin commit, push, VM, reinicio, estrategia nueva
ni ordenes reales. El objetivo completo sigue activo.

## Implementacion

`mt5_scheduling.py` extrae las reglas puras de admision y eleccion de clase
del cliente existente. `mt5_client.py` las utiliza sin modificar prioridades:
gestion antes de entradas; tras cuatro operaciones mutantes se permite una
lectura pendiente; una unica plaza adicional reservada a gestion. OPEN_MARKET
y PLACE_LIMIT siguen siendo entradas. No se cambia protocolo ni recuperacion.

Prueba exhaustiva contra los predicados anteriores: 1.344 combinaciones de
conteos/racha/limite, mas capacidades 1, 2, 8 y 128. Esa referencia esta
conservada en el test, no se presenta como una segunda implementacion de
estrategia. El cliente era un archivo no seguido por Git, por lo que HEAD no
contiene su version inmediatamente anterior; lectura previa y predicados
preservados son la evidencia de la extraccion de este turno.

`research/shared_transport.py` simula una conexion global para solicitudes de
canal 1, canal 2 y lecturas de sistema:

- Identidad de solicitud, canal/senal, operacion, admision, fin de preparacion,
  plazo y tiempo de ocupacion declarado. Reloj relativo de una unica sesion,
  no mezcla de UTC y relojes monotonos de maquinas distintas.
- Preparacion consume admision pero no conexion. Si expira, retiene capacidad
  hasta drenar; no permite otra apertura usando esa plaza mientras sigue E/S.
- Cola de transporte compartida, misma prioridad por clase y reserva acotada.
- Timeout activo conserva conexion ocupada. Respuesta tardia la libera dejando
  constancia del timeout; una respuesta ausente no produce reenvio ni liberacion
  artificial al terminar el estudio.
- Eventos pasivos declarados del broker pueden suceder durante una espera. No
  se usa un SL/TP pasivo para liberar la llamada ni se atribuye su fill al bot.
- Plazo agotado antes de enviar no se interpreta como orden ejecutada. Se
  conservan rechazadas, expiradas, desconocidas y pendientes en el denominador.
- Limites: 10.000 solicitudes y 10.000 eventos pasivos por API, hasta 1.000.000
  de eventos con limite por defecto de 100.000. Cutoff, overflow y agotamiento
  del presupuesto conservan bloqueo y evidencia parcial, no exito silencioso.

El modelo declara FIFO por disponibilidad **como hipotesis entre peticiones
de igual clase**, no como garantia del runtime asyncio. Conserva la lista de
elegibles en cada despacho. Empates de reloj: plazo, respuesta, evento pasivo,
admision, disponibilidad y despacho. No oculta esa convencion como orden nativo.

`tools/run_shared_transport_controls.py` permite ejecutar protocolos JSON
inmutables con hashes de entradas/fuentes antes y despues. Contrato estricto,
maximo 100 casos, 8 MB de entrada, 10.000 elementos sumados y 600 s comprobados
entre casos. Los probes de carga/aislamiento incluyen ahora el helper nuevo
en sus hashes: resultados previos se conservan con sus propias versiones.

## Controles

35 pruebas focales aprobadas: 24 del recurso compartido, dos de equivalencia,
cinco de CLI y cuatro previas de prioridad real. Incluyen comparacion con
`MT5ReadClient._acquire_transport/_release_transport` en un event loop con ambos
canales: misma secuencia de cinco gestiones, lectura intercalada y entrada.
Esa prueba no crea trabajador ni conecta MT5. La suite previa de prioridad
tambien prueba transporte aislado usando un backend enteramente ficticio.

La revision independiente de solo lectura no encontro hallazgos materiales.
Ejecuto 31 pruebas de los tres archivos nuevos (excluye las cuatro previas).
Comprobo limites de alcance y hashes de los probes. No reviso todo el paquete
de runtime ni certifico el orden de empates o despliegue.

Protocolo de cuatro escenarios declarado antes de ejecutarlo:
`reports/e5-shared-transport-20260921/protocol.json`.
Comando:

```text
python tools/run_shared_transport_controls.py --input reports/e5-shared-transport-20260921/protocol.json --output reports/e5-shared-transport-20260921/result.json
```

1. Lectura ocupada, entrada canal 1 y cierre canal 2: lectura comienza en 0 ms,
   cierre en 500 ms y entrada en 600 ms. No se simulan tres conexiones separadas.
2. Modificacion vence en 100 ms pero termina en 1.000 ms: cierre posterior
   espera hasta 1.000 ms; SL pasivo declarado ocurre en 300 ms durante la espera.
3. Llamada sin respuesta: queda pendiente/incierta al corte, cierre en cola
   expira sin enviarse. El caso queda bloqueado, como se esperaba por diseno.
4. Preparacion vence en 100 ms y drena en 500 ms: otra entrada se rechaza por
   capacidad; gestion usa la plaza reservada; nueva entrada en 600 ms es admisible.

Son escenarios sinteticos declarados, no parametros calibrados del broker ni
reconstrucciones de jornadas. Tres terminan el ciclo y uno queda pendiente;
no se elimina este ultimo para producir un informe completamente verde.

Ensayo de 60 s del servicio durable + journal + transporte ficticio aprobado,
sin procesos ni diarios de produccion: 2.400 lecturas FOUND; 60 operaciones DONE
(20 entradas, 20 modificaciones, 20 cierres); 20 checkpoints durables confirmados,
maximo 31 ms; bucle p99 16 ms/maximo 32 ms; operacion p99/maximo 94 ms.
Cola maxima muestreada 2, drenada al final, cero errores de escritura; trabajador
y vigilante terminados. Fuentes sin cambios durante el ensayo. No es el ensayo
de una hora al doble del pico medido ni demuestra recursos/red de la VM.

Comando:
`python tools/probe_mt5_mixed_load.py --seconds 60 --read-rate 40 --directory reports/e4-shared-scheduling-load-20260921 --entry-evidence`.
SHA256 del resultado: `982ff155e9382123c982ef176d734348ed501bc75d7320fe7595d8316c69470d`.
Protocolo sintetico SHA256: `e3e2aff107875a68254237c2122994d5513d0a1ec666b0d5230c948ea3d84adf`.
Informe del protocolo SHA256: `6710a72444c04389cfdca47385c5ccc7bf7e1be2bd0c15e71ee9b2aeafd3615d`.
Suite global final: 6.425 aprobados, una advertencia pandas ya existente,
461,20 s, Python 3.11.9 / pytest 9.1.1. JUnit:
`reports/e5-shared-transport-20260921/pytest.xml`, SHA256
`dae660e0950b9899ba92ce3e6f14a46ba432e557392e9b2b11ced4d2c71d0bf0`.

## Lo Que No Se Ha Cerrado

Este recurso **todavia no sustituye el motor de estrategia por cesta**. Es el
coordinador de transporte necesario para conectar en un reloj comun las
decisiones de ambas politicas, los precios que observan y sus respuestas.
No calcula fills ni riesgo, ni decide si una peticion tras un SL sera rechazada.
Los eventos pasivos son hechos/escenarios suministrados, no predicciones.
Los tiempos de preparacion/ocupacion son inputs declarados, no estimaciones
de red ni medidas de broker obtenidas por esta entrega.

`single_basket_serial_v1` conserva sus restricciones, y los gates de portfolio
y certificacion permanecen cerrados. No se presenta el resultado como paridad
live ni como solucion completa de las diferencias historicas de cierre.

Siguiente integracion: alimentar este coordinador desde decisiones y eventos
de broker causalmente producidos en la simulacion, con respuesta de vuelta a
cada cesta antes de evaluar la siguiente observacion. Contrastar contra el
runtime y oraculo independiente; no pasarle cierres futuros para acertar P/L.
Conservar identidades de modificaciones SL/TP y sus confirmaciones/rechazos.
Lectura del codigo actual: `pending_actions.PendingQueue.add` (bloque de
MODIFY_SLTP, lineas 700 y siguientes) combina campos SL/TP y mantiene revisiones
si coinciden senal, tipo y ticket. Los helpers separados no prueban dos llamadas
nativas. El contrato de simulacion debe distinguir intencion, coalescencia,
solicitud final y confirmacion; no copiar literalmente la simplificacion del
informe historico del 12/09 de que siempre son dos envios.

Para contrastar el recurso compartido con datos nuevos, reutilizar tiempos de
fase ligados a solicitud/sesion cuando existan. Si falta la hora de admision o
disponibilidad para transporte, no inferirla de la escritura posterior de un
log ni de una duracion nativa aislada. Instrumentacion adicional debe ser acotada
por solicitud mutante, fuera del camino bloqueante, sin volver a registrar
cada lectura a alta frecuencia. No es requisito universal para admitir todos
los contrafactuales historicos, solo para afirmar ese contraste de fases.

Ampliacion historica/anclas monetarias y entrega operativa en VM siguen siendo
requisitos separados. No se retrasa una reparacion operativa aprobada por exigir
esta certificacion completa como requisito universal de publicacion.

## Frontera De Integracion Revisada

Continuacion implementada parcialmente en
[entrada y transporte incrementales](2026-09-21-incremental-entry-transport.md):
motor suspendible y fills causales del bloque previo, detector inicial
incremental y sesion de transporte reutilizada por el CLI. Las concesiones
entre books y finalizacion derivada del broker aun no estan conectadas.

Lectura arquitectonica independiente completada, sin cambios ni simulaciones.
Secuencia de implementacion propuesta dentro del plan original, sin nueva
espera de modelo/permiso para trabajo local:

1. Hacer suspendible `engine.simulate` mediante un generador privado/stepper y
   conservar el API actual como consumidor hasta fin. Mantener posiciones,
   cursores, realizados/riesgo, armado, cancelaciones y books en el mismo frame.
   Exigir igualdad de resultados/trazas monocesta antes de cambiar coordinacion.
2. La frontera son `dispatch_client`, `release_client`, `observe_client_entries`,
   `acknowledge_market` y `request_market_close`. Concesion global en lugar de
   `ClientBook.start` autonomo; identidad canal/senal/ticket/secuencia, pues
   `sim_1` se repite entre cestas. No dejar gestion oculta detras de una entrada
   por el FIFO local. Coalescencia solo en intenciones aun no enviadas.
3. La ruta integrada no debe precalcular fill_index/precio/posicion futuros en
   `dispatch_client`: guardar condiciones temporales pendientes y resolver al
   llegar el evento ejecutable. Detector de entrada incremental que reutilice
   la regla existente; cancelaciones por cursor causal. No crear solicitudes
   globales a posteriori desde los resultados independientes ya terminados.
4. Convertir el coordinador en sesion incremental con submit/ready/advance/
   complete y usarlo tambien desde el CLI sintetico. Las fases de MarketBook y
   ProtectionBook son las que retienen/liberan el recurso; no sumar otra demora
   de servicio duplicando procesamiento/acuse. Conservar la convencion quote-clock
   hasta introducir y validar explicitamente otro reloj.
5. Primer control conectado: una modificacion Gold ocupa transporte, Dubai
   detecta su propio umbral y solicita cierre, un SL instalado se ejecuta durante
   la espera, y la respuesta cambia la observacion disponible para decisiones
   posteriores. No congelar las solicitudes a partir de cierres conocidos.
   Anadir equivalencia monocesta, BUY/SELL, identidades repetidas entre cestas,
   falta de respuesta, empates, cambio de sufijo futuro y cortes incompletos.
6. Dubai basket_guard_v1 actualmente rechaza execution.client en
   `market_blockers`. Cualquier capacidad nueva sera escalar, tipada y acotada;
   no retirar el bloqueo generico. Oracle/fast y portfolio siguen bloqueados
   hasta su propia validacion. Ampliar IMPLEMENTATION_FILES al integrar la nueva
   dependencia, sin reetiquetar estudios archivados como ejecuciones actuales.

No se elige reutilizar el runtime entero como primer driver offline: requiere
aislar reloj, tareas, persistencia, entradas Telegram y observaciones. El stepper
permite conectar el motor existente sin duplicarlo; el runtime permanece como
control de comportamiento. Esto es una frontera de trabajo, no implementacion
ya realizada ni certificacion adicional de este bloque.
