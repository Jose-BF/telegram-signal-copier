# Inventario De Runtime, Simulacion Y Criterios De Cierre

Fecha de corte: 19/09/2026. Alcance solicitado de eventos: 27/07/2026 inclusive a
19/09/2026 exclusivo. Revision posterior: inventario diagnostico provisional;
la [revision de arquitectura](../development/2026-09-19-mt5-isolation-architecture.md)
reproduce defectos de sesion, tiempos y alcance en schema 1. Las cifras de
presencia de eventos no certifican linea temporal ni admision de trayectorias.
E1 corrigio despues el generador conservando esta salida original. La salida
schema 2 y sus limites estan en los
[resultados E1-E2](../development/2026-09-19-e1-e2-offline-results.md). No hubo
push, reinicio, cambio de configuracion ni ordenes reales durante este bloque.

## Resultado Ejecutivo

La VM esta operativa en la observacion actual, pero el riesgo que produjo los
retrasos sigue presente. El proceso principal, el supervisor y MT5 estan vivos,
los dos canales siguen sondeandose y no hay exposicion ni cola pendiente. Sin
embargo, todos esos procesos trabajan en prioridad `BelowNormal`, y el puente
MT5 5.0.5735 continua dentro del mismo proceso Python con accesos directos. La
evidencia historica demuestra que ese puente puede retener el GIL durante una
espera nativa. Arrancado no equivale a aislado.

La simulacion tampoco esta lista para elegir una estrategia por su P/L. Ya
podemos distinguir exactamente tres clases que antes se mezclaban:

1. Contabilidad nativa: 40 cestas y 115 posiciones de la semana, conciliadas
   desde deals de MT5.
2. Evidencia detallada del cliente: 253 senales recibidas entre 27/07 y 28/08,
   con acciones, sesiones, ticks y FX. No contiene las recepciones detalladas
   de la semana 14-18/09.
3. Sombras: 14 IDs semanales con alta original observada y 30 IDs sin ese alta.
   Los 30 no se convierten en sombras recuperadas ni en ceros.

El siguiente trabajo ya no es otra busqueda abierta. Es reparar y aprobar los
defectos R01-R06, y despues usar R07-R12 para cerrar paridad y evidencia.

## Estado Actual De La VM

Lectura remota de solo lectura a las 17:29:10 UTC:

| Elemento | Estado observado |
| --- | --- |
| Codigo | `main`, limpio, `7415941a410d18288fa4e08da76809f70b125efa` |
| Runtime | Python 3.11.9; MetaTrader5 5.0.5735; terminal 5.0.0.6182 comprobado a las 17:06 UTC |
| Tarea | `Running`, S4U, `IgnoreNew`, tres disparadores, prioridad 7 |
| Procesos | Lanzador, supervisor, bot y terminal presentes; todos `BelowNormal` |
| Heartbeat | Schema 3, PID 4712, escrito 17:28:59.906 UTC, 10.7 s antes de la lectura |
| Telegram | Cobertura de sondeo de canal1 y canal2 a las 17:28:47 UTC |
| Exposicion | `flat`: 0 posiciones, 0 senales abiertas, 0 entradas pendientes |
| Cola de diario | 0; aviso de almacenamiento `false` |
| Memoria | 3,294.2 MiB libres de 5,100.4 MiB |
| Disco | 18.238 GiB libres de 49.049 GiB |
| Diario | 6.594 GiB, equivalentes a unos 7.08 GB decimales |

El diario sigue recolectando datos y si se usa: permite reconstruir recepcion,
decisiones, acciones, reinicios y fallos. El problema no es que sea inutil,
sino que aun no tiene una retencion acotada compatible con lectores y cursores.

`LastTaskResult=2147946720` (`0x800710E0`) significa que una solicitud fue
rehusada. Con una repeticion cada minuto y `IgnoreNew`, es compatible con que
el disparador periodico encuentre la instancia existente. No se usa como prueba
de caida: tarea, heartbeat y sondeo estaban activos. Si debe funcionar como
alarma, hay que publicar un estado separado y no reutilizar ese codigo ambiguo.

La prioridad es una regresion comprobada. El 10/09 se habia fijado 4 para usar
prioridad normal. `tools/configure_vm_recovery.ps1` recrea los ajustes sin
especificar `Priority`, por lo que la tarea actual ha vuelto a 7 y sus procesos
a `BelowNormal`.

## Inventario Reproducible

Herramienta: `tools/build_runtime_simulation_inventory.py`.
Salida local: `runtime_data/runtime_simulation_inventory_20260919/inventory.json`.
SHA256: `c1aef53b3accfff46b051b1172482797ab075332b5ca8ffcb4bf84ebbcb0cb85`.
Tamano: 882,180 bytes. El archivo no contiene cuerpos de mensajes Telegram.

La salida conserva hash y tamano de cada fuente, ausencias, sesiones,
acciones, sombras y estados de cobertura aportados. La revision posterior
detecta que `day_utc` puede proceder de calendario nativo y que el contrato
precedente no se restringe a la misma sesion: esos campos quedan provisionales.
Es un inventario de fuentes seleccionadas, no
una afirmacion de que el diario local sea una copia completa de produccion.

| Evidencia | Canal 1 | Canal 2 | Total |
| --- | ---: | ---: | ---: |
| Identidades relevantes | 99 | 198 | 297 |
| Recepcion direccional detallada | 82 | 171 | 253 |
| Solo fuente semanal/nativa | 17 | 27 | 44 |
| Cestas nativas semanales | 17 | 23 | 40 |
| Sombra con alta original, todas las fechas | 12 | 18 | 30 |
| Sombra semanal con alta original | 5 | 9 | 14 |
| IDs semanales sin alta original | 12 | 18 | 30 |

Las 30 senales con sombra original suman 16 del 27-28/08 y 14 de la semana
actual. La comparacion historica contiene 18 IDs, pero dos avisos Gold sin
entrada no tienen `strategy_shadow_registered` en el diario detallado usado.
Sus resultados siguen disponibles como comparacion retrospectiva, pero no se
elevan a observacion prospectiva sin esa prueba de alta.

No hay sombras `recovered-only` demostradas por estas fuentes. Los eventos de
recuperacion del 27-28/08 pertenecen a estados que tambien tienen alta original.
Los 30 huecos semanales solo prueban `missing_original_week_state`.

### Cobertura Detallada

- Las 253 recepciones detalladas terminan el 28/08. Los eventos posteriores del
  archivo local son actividad de runtime, no las recepciones 14-18/09.
- Las 253 tienen dias marcados completos por los estados de cache aportados.
  El generador no vuelve a comprobar hashes de ticks ni toda la vida de cada
  cesta. Esa marca no acredita cobertura por senal ni rellena la semana reciente.
- 103 de las 253 son anteriores al primer `live_strategy_contract` conservado.
  Sumadas las 44 filas recientes sin recepcion, 147 filas no pueden vincularse
  a un contrato precedente desde estas fuentes.
- El diario tiene 5,937 intentos con duracion registrada en el tramo: 956 de canal1 y
  4,981 de canal2. El maximo por senal es 14.343 s y 38.219 s respectivamente.
  La espera de 57.125 s del 10/09 vive en la cohorte de ejecucion separada.

### Presencia De Eventos (No Linea Temporal Certificada)

La clasificacion schema 1 siguiente es descriptiva y contiene falsos proxies:
`mt5_action_attempt.ts` es el momento de emision al finalizar, no el inicio
del envio; `sl_updated` puede ser interpretacion de un mensaje, no proteccion
instalada. Se conservan contadores originales para reproducir el informe.

| Fase observada | Senales |
| --- | ---: |
| Recepcion | 253 |
| Decision | 249 |
| Solicitud/cola cliente | 249 |
| Evento de intento (incluye preflight) | 249 |
| Evento clasificado como fill | 249 |
| Resultado/acuse | 249 |
| Evento clasificado como proteccion; requiere correccion | 227 |

Las cuatro senales sin intento observado son Gold 2103, 2126, 2151 y 2158. Sus vigilancias
de entrada caducaron; no se cuentan como ordenes perdidas. Las 22 rutas sin
evento de proteccion son todas Dubai. La ausencia de ese evento no demuestra
por si sola una posicion desprotegida: bloquea la afirmacion hasta conciliar
orden/deal y SL/TP nativo.

La semana reciente no tiene esta linea temporal en el extracto disponible.
3086 demuestra una divergencia de resultado (+31.92 EUR real frente a +1.74
EUR sombra), pero no contiene aqui recepcion, cola ni acciones. No se atribuye
su minuto de demora a una causa concreta hasta extraer esas fases. Lo mismo
aplica a las perdidas 2908, 2917 y 3003: tienen contabilidad nativa, no sombra
original ni secuencia detallada en el inventario actual.

## Defectos Cerrados Para Implementacion

| ID | Prioridad y estado | Evidencia / causa | Propietario | Prueba de cierre |
| --- | --- | --- | --- | --- |
| R01 | P0 abierto, reproducido | El puente MT5 5.0.5735 retiene el GIL en esperas nativas; `_send_safe` llama directamente a `mt5.order_send` | Frontera MT5 de runtime | Dobles bloqueantes de 5 y 60 s; supervisor no MT5 p99 <=100 ms y pausa maxima <=1 s |
| R02 | P0 abierto, confirmado | Lecturas y escrituras MT5 estan repartidas entre `executor`, `main`, `listener`, `state`, pendientes, monitor, contexto y auditor | Arquitectura runtime | Un propietario de proceso; prueba falla si cualquier modulo activo salta la frontera |
| R03 | P0 hueco de seguridad | Timeout/muerte tras enviar puede significar fill con acuse perdido; no autoriza reenvio | Ejecutor, ledger y recuperacion | Caidas antes/despues de envio, fill y acuse: cero duplicados; resultado `unknown` hasta conciliar |
| R04 | P1 abierto, observado | El observador de sombras se desactivo el 15 y 17/09 por `shadow journal write not confirmed`; causa fisica aun por reproducir | Diario/sombra/supervisor | Escritura lenta, cola llena y reinicio: degradacion visible, reanudacion y cadena intacta |
| R05 | P1 abierto, confirmado ahora | Tarea en prioridad 7; configurador omite prioridad y procesos quedan `BelowNormal` | Recuperacion VM | Instalacion idempotente conserva prioridad 4 y procesos `Normal` tras reinicio |
| R06 | P1 abierto, capacidad | Diario de 6.594 GiB sin retencion acotada; 18.238 GiB libres, sin presion actual | Telemetria/almacenamiento | Rotacion atomica, cursores y lectores sobre segmentos; recuperacion tras corte; nunca borrar original sin manifiesto |
| R07 | P1 abierto, evidencia | 22 rutas Dubai no tienen confirmacion de proteccion en el esquema actual | Ejecutor/monitor/telemetria | Solicitud, respuesta y estado nativo correlacionados por intento, orden, deal y posicion |
| R08 | P1 abierto, modelo | Cliente simulado modela una cesta, no toda la contencion compartida ni secuencia real de protecciones | Motor cliente/oraculo | Dos canales y varias cestas concurrentes, orden causal exacto y oraculo independiente |
| R09 | P2 abierto, observabilidad | `LastTaskResult` mezcla trigger rehusado con fallo operativo | Watcher/tarea | Salud separada de triggers ignorados, con prueba de instancia existente y de caida real |
| R10 | P1 hueco de evidencia | El diario detallado local no cubre recepciones/acciones de 14-18/09 | Captura/extraccion | Extracto acotado por IDs y fases, hash y manifiesto; no descargar 7 GB completos |
| R11 | P1 hueco de contrato | 103 recepciones detalladas no tienen contrato precedente conservado | Publicacion de contrato | Cada sesion publica contrato antes de aceptar senales; replay rechaza contrato desconocido |
| R12 | P1 validacion pendiente | Cohortes usadas ya examinadas; no se ha acreditado un bloque historico intacto con registro de acceso | Proceso de validacion | Cohorte prospectiva congelada antes de verla, sin excluir perdidas, reinicios ni no-entradas |

### Accesos MT5 Que Deben Entrar En R02

La escritura pasa por `executor.py`, pero no es el unico acceso nativo. Hay
lecturas activas en `executor.py`, `state.py`, `listener.py`, `main.py`,
`pending_actions.py`, `position_lifecycle_monitor.py`, `market_context.py`,
`mt5_errors.py` y `live_auditor.py`. Varias usan `asyncio.to_thread`; eso no
evita el bloqueo del proceso cuando la extension conserva el GIL. Herramientas
offline y el tester MT5 quedan fuera del proceso live y se revisan aparte.

## Correcciones Ya Activas

No se repetiran como si siguieran pendientes:

- `2e737d63b`: restauracion de diario de arranque acotada.
- `5882f5563`: recuperacion VM y carga de reintentos MT5 acotadas.
- `7e33c116e`: escaneo inicial del poller acotado.
- `83baf1df6`: recuperacion despues de red y scheduler, S4U y supervision.
- `7415941a4`: reintento de inicializacion MT5 en frio con la cuenta configurada.

La VM limpia en ese HEAD, el heartbeat reciente y el sondeo de ambos canales
prueban activacion actual. No prueban aislamiento R01, prioridad R05 ni
retencion R06.

## Contrato Congelado De Pruebas

### Runtime Y Recuperacion

- Ninguna prueba de fallo envia ordenes reales.
- Bloqueo nativo sintetico de 5 y 60 s, en lecturas y escrituras, con ambos
  canales concurrentes. Tras calentamiento, tareas no MT5: p99 <=100 ms y
  pausa maxima <=1 s en el entorno de laboratorio.
- Cola llena, disco lleno/lento, desconexion, reinicio antes y despues de
  envio/fill/acuse, cierre nativo durante espera y protecciones pendientes.
- Una intencion durable produce como maximo una ejecucion. Timeout o muerte
  dejan `unknown`; solo la conciliacion permite reintentar.
- Prueba sostenida de 60 minutos al doble del pico observado del corpus. Sin
  crecimiento continuo de cola o memoria, sin perdida de evidencia esencial y
  con el supervisor respondiendo.
- Tras instalacion y reinicio: tarea prioridad 4, procesos `Normal`, una sola
  cadena lanzador-supervisor-bot, version/configuracion comprobadas y estado
  recuperado antes de admitir nuevas entradas.

### Contabilidad, Decision Y Riesgo

- Misma ejecucion observada: identidad de deals exacta, volumen exacto al paso
  del simbolo y dinero realizado exacto a 0.01 EUR por cesta.
- Mismos inputs y contrato: secuencia de intenciones y decisiones categoricas
  exacta. No se toleran entradas, cierres o protecciones adicionales/ausentes.
- Trayectoria condicionada a fills reales: comparar `P(t)` en reloj comun.
  Tolerancia derivada de cuantizacion, FX y muestreo independientes, pendiente
  de fijar antes del contraste. La revision retira la cifra fija por posicion
  de esta primera propuesta. Extremos sin evidencia quedan desconocidos.
- Drawdown de canal/cuenta se calcula despues de sumar exposiciones en el mismo
  reloj. No se suman MAE individuales. Publicar realizado, flotante, MAE, MFE,
  drawdown y exposicion por separado.
- Ejecucion hipotetica no promete el centimo. La revision retira 90% de
  cobertura y un punto de sesgo como gates nuevos sin justificacion. Conservar
  gates existentes; fijar limites por capacidad antes de evaluar cambios, con
  anchura util de intervalos y error de riesgo/decision, no solo cobertura.
  Publicar p50/p95/p99/max y cobertura. Certificacion pendiente.
- Datos ausentes, reloj no justificable o capacidad no implementada producen
  `unknown`/`blocked`, nunca cero ni aprobado.

### Matriz Funcional Minima

BUY/SELL, ambos canales, no entrada, una y varias patas, varias cestas a la vez,
rango y gap, spread alto, TP/SL/BE/parciales, cierre proveedor, mensaje editado
o duplicado, caducidad, rechazo, timeout, desconexion, reinicio, FX/tick ausente,
las grandes perdidas semanales y una sesion normal. Cada caso comprueba no solo
saldo: decisiones, exposicion, trayectoria y confirmaciones.

## Cohortes Congeladas

| Uso | Cohorte | Regla |
| --- | --- | --- |
| Calibracion runtime/modelo | Eventos detallados 27/07-28/08; ejecucion 09-11/09 | Ya examinada; puede estimar perfiles, nunca validar el mismo ajuste |
| Regresion nominal | 2774, 2777, 3086, Dubai 22781 | Demuestran clases de divergencia, no suficiencia estadistica |
| Regresion de estres | 27-28/08 y Gold 2908, 2917, 3003 | Incluir no-entradas, varias patas, caidas y huecos; ausencia se conserva |
| Contraste retrospectivo | Todas las senales admisibles restantes, por canal y estrato | Sin elegir dias por resultado; se informa como retrospectivo |
| Piloto prospectivo | Version congelada, secuencia consecutiva desde su activacion | Minimo 60 senales elegibles por canal y cinco sesiones; cero divergencias criticas sin explicar |

Sesenta senales y cinco sesiones son un corte operativo inicial, sin garantia
estadistica de tasa de fallo: las senales pueden estar correlacionadas.
Es un piloto de fidelidad, no una prueba de rentabilidad. Si no aparece una situacion de estres,
solo se aprueba funcionamiento normal y la capacidad de estres sigue pendiente.

## Estado Tras Revision

Cada diferencia conocida esta ahora en una de cuatro categorias: causa
historica o fallo observado (R01, R04, R05), contrato de seguridad ausente (R02, R03, R06-R09),
dato faltante (R10-R11) o validacion aun no disponible (R12). Cada una tiene
propietario y prueba de cierre. La matriz JSON conserva las 297 filas para que
ninguna ausencia desaparezca dentro de un promedio.

La recopilacion esta realizada, pero se retira el cierre de exactitud del
inventario y de los nuevos criterios numericos. El diseno de aislamiento y
resultado desconocido esta revisado en el documento enlazado al principio.
Sus entregas E1-E2 permiten corregir la evidencia y probar el protocolo con
dobles antes de integrar. No se repite toda la recopilacion ni se despliega
automaticamente. La observacion de VM de las 17:29 UTC conserva su fecha;
esta revision no es una nueva comprobacion remota.
