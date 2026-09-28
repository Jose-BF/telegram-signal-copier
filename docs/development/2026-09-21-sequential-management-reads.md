# Lecturas Secuenciales De Gestion

## Frontera Actual

Continuacion del cierre terminal y de la cola compartida. No se considera
completado el objetivo del simulador ni la admision de estrategias reales.

`basket_observation.py` extrae la construccion del resumen monetario del
monitor en generadores de peticiones del protocolo de lecturas existente.
`position_lifecycle_monitor.py` ejecuta esos generadores con la fachada MT5
actual. Conserva los wrappers anteriores, sus operaciones, cache y diario.
Es codigo local sin publicar; no se ha cambiado el monitor de la VM.

`research/management_observation.py` conduce la misma cadena mediante
`TransportSession`, con identidad propia por cesta/ciclo/peticion. No toma
prestado el propietario del ClientBook de trading. Las respuestas pueden
originar la etapa siguiente entre cotizaciones, sin avanzar una cotizacion
ni repetir entradas, swap o protecciones.

Este proveedor de lecturas todavia no esta conectado a `_simulation_steps`
ni al coordinador `simulate_shared`. Los controles ejecutan la cadena sobre
el transporte real del laboratorio y un proveedor de estados sinteticos.
No se afirma que las decisiones del motor completo ya usen estas lecturas.

## Contrato Compartido

1. TICK: solicita precio; un resultado desconocido no fabrica una cotizacion.
2. POSITIONS: se solicita despues de recibir TICK. Se filtran tickets conocidos
   cuando retorna positions_get, como hace el monitor actual. Se suma p.profit,
   sin sumar el swap abierto ni costes de una hipotetica salida.
3. Historias: una lectura por cada ticket conocido que no aparece abierto y
   no tiene realizado confirmado en cache. Solo se confirma cuando el volumen
   de salida cubre la entrada. Se suman profit, commission, swap y fee.
4. El resumen se devuelve tras recibir todas esas respuestas. Historia vacia,
   parcial o desconocida conserva total_pl desconocido; no equivale a cero.

Una nueva entrada conocida despues de muestrear posiciones puede no aparecer
en ese resultado antiguo: se consulta su historia y se conserva desconocida
si sigue abierta. No se inventa su exposicion retroactivamente. No se promete
una instantanea atomica de cuenta: cada etapa conserva sus propios relojes.

La extraccion conserva la politica existente, incluida su precision monetaria,
tolerancia de volumen y seleccion del cache. No corrige por su cuenta la
contabilidad historica ni cambia el tratamiento live del swap abierto.

## Relojes Y Fallos

Cada lectura registra peticion, concesion, muestreo, origen y entrega. El
proveedor debe devolver un estado causal; se rechazan origenes futuros y
avances que se salten un muestreo debido. Un origen desconocido puede seguir
siendo desconocido. La copia del payload se congela al muestrear y no cambia
mientras espera la respuesta.

`complete()` solo agenda la respuesta en TransportSession. La entrega al
generador y cualquier actualizacion del cache esperan a que el transporte
observe su liberacion sin fallos. Un presupuesto de eventos agotado no puede
publicar un resumen completo. Este defecto fue reproducido con presupuesto
11 y corregido mediante una fase explicita de respuesta pendiente.

Un timeout activo cierra el consumidor pero mantiene el recurso hasta drenar
la respuesta tardia, que se descarta. Una cancelacion retira solo trabajo no
iniciado. Una concesion aun no consumida se devuelve sin realizar la lectura.
No se revoca una peticion ya caducada. Un corte conserva el ciclo incompleto.

Limites: numero de lecturas, registros por respuesta, bytes por respuesta y
bytes acumulados del ciclo. Si se supera el limite acumulado se omite el
payload con marca explicita y se drena la llamada; no se presenta esa omision
como un resultado vacio del broker. No hay escrituras de telemetria en la ruta
live nueva, aparte del mismo evento de realizado confirmado que ya existia.

## Verificacion

Dos controles previos a la extraccion pasaban contra el monitor original y
fijan secuencia de llamadas, filtrado de tickets, dinero y cache. Los controles
nuevos cubren fuentes desfasadas, lecturas entre cotizaciones, contencion con
otra cesta, historiales desconocidos, cache, timeout, cancelacion, respuesta
invalida y limites de memoria/evidencia. Una matriz recorre los presupuestos
del transporte 1..13, incluido el fallo de la respuesta final.

175 focales aprobadas tras reparar el incidente de entrega prematura. Revision
independiente final cerrada: 45 casos del ciclo y siete del generador/wrappers
aprobados por invocacion directa, fuentes estables, sin nuevos hallazgos en el
alcance revisado. General propia terminada: **6.889 aprobadas**, 475,49 s,
diez advertencias previas (nueve record_property/xunit2 y una de pandas),
cero fallos y cero errores. Incluye los 52 casos nuevos. Fuentes e identidad
sin cambios durante el ensayo. No quedan procesos de ensayo propios activos.

Evidencia: `reports/e5-sequential-management-reads-20260921/` contiene protocolo
y respuestas sinteticas congeladas, cuatro controles completos (secuencial,
contencion, respuesta tardia y confirmacion perdida por limite de traza),
fuentes, entorno temporal, comando, salida, JUnit y comprobacion final.
SHA256 del JUnit registrado en `verification.json` y comprobado tras finalizar.
No se reutiliza la general anterior 6.837 para certificar este cambio nuevo.

## Siguiente Integracion

- Coordinar consumidores de trading y lecturas por identidad de peticion, con
  entrega de respuestas entre quotes antes del siguiente despacho elegible.
  Tambien deben distinguirse las confirmaciones de trading del procesamiento
  de una nueva quote: no introducir una asimetria temporal inadvertida entre
  las lecturas nuevas y el cliente anterior ligado al reloj de cotizaciones.
- Alimentar la cadena con estados broker causales del motor, no con resultados
  finales ni con la rejilla diagnostica de riesgo. Conservar estados muestreados
  y respuestas entregadas como evidencias diferentes.
- Consumir cada resumen una vez para la politica, manteniendo terminales y
  protecciones nativas independientes. No repetir process_quote para simular
  una respuesta: podria duplicar observaciones y decisiones de entrada.
- Reproducir el orden real del monitor: tambien espera sus propias entradas
  antes del resumen. Un monitor siempre concurrente seria otra hipotesis.
- Completar admision monetaria, protecciones y contratos Dubai/Gold555 antes
  del contraste historico amplio. Los gates anteriores permanecen cerrados.

Sin commit, push, ordenes, reinicio ni acceso a VM. Objetivo completo abierto.
