# Base Gold: datos observados y barreras pendientes

## Estado

Preparacion y correcciones locales. No hay commit, push, despliegue, reinicio,
orden ni busqueda de estrategias en esta fase. No se modificaron parametros,
cuenta ni lotes. HEAD local: `08551e9a86aa716473e43b978c13a65f21003b7a`, rama
`feature/gold-555-live-trial`. La ultima verificacion de la VM sigue siendo la
de la activacion anterior, a las 00:59 UTC del 08/09; no es una comprobacion
nueva del estado vivo.

El resultado es **diagnostico, no admision de un estudio monetario**. Completar
este inventario no convierte el historial en evidencia completa ni en OOS.

## Datos conservados

- Chat Gold actual, aislado de los chats historicos y Dubai.
- Periodo propuesto 22/07-02/09 UTC, corte exclusivo 03/09 00:00 UTC.
- 2.159 mensajes del inventario, 9.276 recepciones; todos tienen destino.
- El catalogo usa 9.288 recepciones: las 12 adicionales son contexto previo
  del 17/07 y 21/07, conservado por sus relaciones con otros mensajes.
- 610 registros: 243 senales formales, de las que 242 son NOW en el trigger;
  84 planes de zona permanecen fuera del universo NOW.
- 22 NOW se recibieron en una fecha UTC posterior a la de la revision del
  proveedor. No se adelantan artificialmente sus entradas a la publicacion.
- 103 NOW no tienen original sin editar observado antes del trigger; 27
  carecen de identidad canonica en su recepcion inicial. No se eliminan.
- Cuatro NOW tienen reloj del proveedor ligeramente posterior al local
  (entre 8 y 401 ms). Se conservan bloqueadas por alineacion temporal pendiente,
  sin inventar precision ni ensanchar tolerancias.

El catalogo no carga ejecuciones MT5: `execution_observed=null` significa
desconocido, no operacion no ejecutada. Las recepciones posteriores al corte
quedan fuera del catalogo y se conservan en la fuente original congelada.

## Correcciones Comprobadas

La adaptacion Gold tomaba la ultima direccion del catalogo, que podia provenir
de una edicion posterior. Ahora prioriza la direccion causal del contrato de
entrada; para formatos antiguos solo acepta revisiones observadas hasta el
trigger. Falta o contradiccion temporal de direccion causa error explicito con
identidad de la senal, no desaparicion silenciosa del denominador.

La preparacion tambien bloquea revisiones contradictorias con identica hora
y distingue original disponible al entrar, antes del corte y en el inventario
global. Ambos casos se reprodujeron con pruebas rojas antes de corregirse.

Ademas, NOW se comprueba en la revision causal de entrada, no en una edicion
posterior. Los niveles requieren una hora de observacion; ya no se usan niveles
efectivos finales ni horas del proveedor para rellenar retroactivamente un
historial ausente. Las senales sin niveles siguen presentes, con sus historiales
vacios. La capacidad de representar niveles recibidos despues se conserva, con
sus horas reales; no determina el volumen de las entradas.

El catalogo y el adaptador corregido coinciden en las 242 identidades NOW y sus
direcciones. Hay siete senales sin objetivos y cinco sin stop del proveedor;
su tratamiento depende de una politica explicita, no de niveles inventados.
Si existe direccion contractual pero falta la revision observada de entrada,
el adaptador rechaza el catalogo con `missing_causal_entry_revision` y la
identidad de la senal. No la reclasifica silenciosamente como fuera de NOW.

## Precios Y Cobertura

Las 63 fuentes seleccionadas (32 XAUUSD y 31 EURUSD) pasan los validadores
existentes, incluidos hashes, secuencia y contratos temporales. Las seis
discrepancias EURUSD del 23-28/08 tienen identicos valores, tipos y orden; las
diferencias fisicas estan en los metadatos Parquet. Cada captura conserva su
propio archivo, sidecar y hashes. No se mezclaron ni reconstruyeron caches.

EURUSD del 24/07 no aparece en las dos fuentes locales conocidas. Afecta a las
nueve NOW recibidas ese dia. No se sustituye por XAUUSD ni por otra fecha.

Hay huecos que una validacion de procedencia no resuelve:

| Fuente | Intervalo UTC sin ticks | Tratamiento |
| --- | --- | --- |
| XAUUSD 20/08 | 17:23:55.692-22:00:00.342 | Incluye minutos de sesion abierta; pendiente |
| XAUUSD 31/08 | 23:41:32.837-23:57:27.940 | Dentro de sesion; pendiente |
| EURUSD 25/08 | 21:44:10.107-21:50:07.254 | Verificar conversion por evento |
| EURUSD 31/08 | 23:41:28.984-23:58:05.255 | Verificar conversion por evento |

Otros 23 huecos largos XAUUSD no contienen una marca de minuto abierta segun
el contrato de sesiones existente. Esto no prueba todos sus instantes ni
permite interpolar. Los bordes diarios, huecos menores y cierre de mercado
todavia se validan contra la politica concreta.

Cribado orientativo, **no parametros elegidos ni muestras admitidas**:

| Horizonte de diagnostico | Fuentes declaradas disponibles | Bloqueo por fuente ausente | Solape con hueco pendiente |
| --- | ---: | ---: | ---: |
| 240 minutos | 233 | 9 | 3 |
| 1.440 minutos | 181 | 61 | 14 |

Estos recuentos se superponen con otros bloqueos y no se suman para obtener
una muestra valida. Las 242 NOW permanecen en ambos cribados.

## Dinero Y Siguiente Barrera

Los dos contratos monetarios locales pasan metadatos. El antiguo solo cubre
el modelo intradia; el posterior contiene 129 snapshots entre 02/08 y 01/09.
Falta comprobar el contrato aplicable a cada periodo, conversion por evento,
comisiones/costes completos y parejas de snapshots para cada rollover requerido.

Se conserva la calibracion previamente verificada: 104 intentos, 74 aperturas
contrastadas con el broker (46 Gold), dos rechazos y 28 casos antiguos sin todos
los campos. No demuestra costes de ida y vuelta, fills alternativos ni colas
futuras de latencia. No se recalibraron parametros para mejorar un resultado.

Antes de investigar estrategias: resolver o mantener bloqueadas las incidencias
de datos; fijar capital EUR, caida tolerable, volumen/exposicion, margen,
caducidad, horizonte y presupuesto finito. Las reparaciones del adaptador no
autorizan a convertir este catalogo diagnostico en una entrada certificada del
motor. La admision de datos y dinero sigue siendo una etapa independiente.

Ningun dia historico se declara OOS intacto. La validacion prospectiva exige
hipotesis congeladas y datos nuevos; no se reutiliza la seleccion como prueba.

## Evidencia

Directorio: `runtime_data/gold_foundation_20260908_0150/`.

- `catalog_v2/package_manifest.json`, identidad `76bf3764195d99a5e16440e251f4900d93aab0e90b781d75437052eb0dfc3fc9`.
- `tick_diagnostics/eurusd_tick_diagnostics_v2.json` y `general_ticks_admission.json`.
- `horizon_gap_readiness.json` y `money_input_readiness.json`.
- `pytest_full_final.xml`: 3.064 aprobadas, 624 avisos, 150,67 s; version final del codigo.
- `pytest_focused_final.xml`: 48 aprobadas, 1,48 s; incluye nueve pruebas de los auxiliares ignorados.
- `pytest_legacy_causality_red.xml`: tres regresiones fallan con el adaptador anterior cargado en memoria, sin revertir archivos.
- `pytest_missing_revision_valid_red.xml`: ausencia de revision causal reproducida antes del ultimo guard; reemplaza la primera prueba con fixture incompleto.
- Revision independiente Astra de los cambios: incidencias cerradas y ningun P1/P2 pendiente en el cambio final revisado.
- `verification_final.json`: comprobacion final de fuentes, pruebas y correspondencia de las 242 senales; SHA256 `e8210231d283b390e3651a33ab2f15e633c815b6bf0025f7ee92c6f7222542a7`.

Se preservan los primeros borradores. El diagnostico EURUSD v1 queda
supersedido por v2: confundia microsegundos y nanosegundos al medir huecos. La
comparacion de precios no cambia; las metricas temporales v1 no deben usarse.
