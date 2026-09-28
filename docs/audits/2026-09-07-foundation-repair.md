# Reparacion local de la base de simulacion

Fecha: 2026-09-07. Estado: reparaciones locales verificadas; validacion
prospectiva en demo pendiente.
Base Git: b4426e6, rama feature/gold-555-live-trial. Sin commit, push,
despliegue, reinicio ni ordenes. Se conservan los cambios anteriores.

## Alcance

El usuario autorizo reparar primero la base, investigar despues Canal 2 y
finalmente Canal 1 por separado. Sol realiza cambios delimitados; Astra revisa
riesgos. No se selecciona ninguna candidata ni se cambian parametros live.

## Defectos reproducidos y reparaciones

1. Identidad incompleta: tres reglas terminales diferentes tenian el mismo
   identificador. El contrato v2 incluye entradas pendientes, finalizacion al
   quedar plano y exigencia de cero posiciones. El identificador historico de
   estrategia permanece congelado; v1 conserva su propia huella, sin simular
   que ya contenia esas reglas. El motor respeta las reglas explicitas.
2. Perdida de evidencia al recuperar: los estados de una version anterior se
   descartaban. Ahora se validan antes de comparar versiones y se conservan
   bloqueados con su identidad, posiciones e importes originales.
3. Recuperacion no durable: los eventos de recuperacion no se leian en el
   siguiente arranque. Se incluyen en la cadena y no se rehabilita una cohorte
   incompleta automaticamente.
4. Bloqueo global por cursor perdido: los reintentos tienen presupuesto y la
   cuarentena afecta solo a las parejas que siguen en el cursor fallido. Las
   otras parejas requieren su propio historial verificable; no se salta al
   ultimo tick ni se inventan cierres.
5. Ticks en el mismo milisegundo: un lote antiguo se difundia tambien a parejas
   mas adelantadas. Cada lote pertenece ahora a su grupo exacto de cursores.
   Los precios no se usan como orden temporal dentro de un milisegundo.
6. Escrituras parcialmente fallidas: confirmar el ultimo evento no acreditaba
   sus predecesores. Se comprueban los recibos pendientes de cada cadena. Las
   esperas de escritura del lote y la cuarentena no retienen el bloqueo que
   necesita una nueva inscripcion procedente del bot live.
7. Informe que convertia una sombra fallida en completa: una reconstruccion
   posterior con archivo disponible permanece como reparacion retrospectiva,
   con resultado prospectivo bloqueado y sin desaparecer del denominador.
8. Contexto ambiguo del clasificador: se separan moneda de cuenta, protecciones
   observadas en MT5, niveles informativos del proveedor y politica activa.
9. Cursor repetido no consecutivamente: una secuencia P,Q,P no demuestra cual
   P era el ultimo procesado. Se bloquea esa continuidad como ambigua. Cada
   cursor conserva su propio presupuesto de reintentos, incluso al alternar
   cohortes con la misma marca temporal; ninguna puede reiniciarlo sin limite.
10. Gestion del proveedor aplicada antes de estar disponible: el contrato v2
    conserva una cola causal de cierres y protecciones. No se consume en un
    tick anterior a la recepcion original, aunque comparta su milisegundo.
11. Precision e identidad perdidas en el informe: el diario guarda el evento
    original completo. Recuperacion e informe conservan su UTC submilisegundo,
    identidad y huella; consumir una proteccion pendiente no crea una segunda
    instruccion ni toma la identidad de otro mensaje informativo posterior.

## Evidencia

Las regresiones nuevas fallaron antes de sus correcciones. Entre otras,
demostraron colisiones de identidad, reentradas indebidas, desaparicion de
estados, resurreccion tras reiniciar, perdida de predecesores y promocion
incorrecta de una reconstruccion retrospectiva.

La comprobacion sobre el registro congelado de hoy incluye 1.165 eventos de
sombra y conserva las 24 parejas senal-estrategia: 15 cerradas, 3 abiertas y
6 esperando antes de migrar. Ninguna pierde posiciones o importes ni cambia
su huella original. Un segundo arranque conserva el mismo resultado.
Las 24 siguen bloqueadas por version; 16 ya tenian ademas una limitacion de
conversion monetaria. Esto no certifica sus resultados ni cierra incidentes
previos por el mero hecho de cambiar una version.

Fuente congelada SHA-256:
`f3b2cb2754d8101533d5f43d33e02685a4e89c659f955b846192ae083cfa4d36`.
Resultados en `runtime_data/foundation_repair_20260907_2046/`. La comprobacion
final de migracion es `result_final_reader.json`; sus ocho huellas de codigo
coinciden con los archivos actuales. Los resultados anteriores se conservan
como pasos intermedios, no como evidencia de la version final.

Verificacion final:

- `python -m pytest -q
  --junitxml=runtime_data/foundation_repair_20260907_2046/pytest_full_2.xml`:
  3.000 pruebas correctas, cero fallos, errores u omisiones, 624 advertencias,
  124,70 segundos. Las advertencias incluyen usos obsoletos de `utcnow()`.
- Entorno local: Python 3.14.2 y pytest 9.0.3. La suite sustituye dependencias
  externas; no demuestra integracion con MT5/Gemini reales ni con Python 3.11
  de la VM. No se han enviado ordenes.
- Revision critica de Astra: hallazgos identificados y corregidos; revision
  final de las dos correcciones del lector sin P1/P2 pendientes en ese alcance.
- Las regresiones finales comparan ejecucion directa, recuperacion desde JSONL
  e informe con un evento recibido a `.123900`, entre ticks `.123` y `.124`.
  Incluyen proteccion pendiente seguida de informacion de otro mensaje.

## Piloto de latencia y deslizamiento

`latency_pilot.json` conserva el analisis de 20 aperturas del 7 de septiembre:
16 de Gold y 4 de Dubai. Se emparejaron las 20 con deals del broker, sin
exclusiones, verificando ticket, direccion, simbolo, volumen y precio. El
deslizamiento firmado se recalculo desde solicitud y fill. Se conservaron
identidades de intentos/deals y huellas de ambas fuentes congeladas.

| Medida observada | Mediana | Percentil 90 | Maximo |
| --- | ---: | ---: | ---: |
| Envio a respuesta del broker | 289 ms | 398,8 ms | 2.906 ms |
| Decision a respuesta del broker | 289 ms | 498,1 ms | 2.906 ms |
| Deslizamiento adverso en precio XAUUSD | 0 | 0,059 | 0,22 |

El deslizamiento esta expresado en unidades de precio XAUUSD, no en euros de
perdida de cuenta; un valor negativo representa mejora de precio. El tiempo
de envio a respuesta incluye terminal, red y respuesta: no es una medida
exacta del tiempo hasta el fill ni calibra la entrega de Telegram.

Este piloto de un solo dia no fija las colas anuales, no aporta observaciones
posteriores a la reparacion y no cambia supuestos de simulacion. Al calibrar
no debe sumarse el mismo movimiento de precio dos veces mediante retraso de
entrada y deslizamiento solicitud-fill. Se ampliara la muestra y se separaran
componentes observables antes de fijar un modelo de ejecucion.

## Condiciones para avanzar

- Validar la version reparada prospectivamente en demo, previa autorizacion
  de publicacion y comprobacion de exposicion y reinicio seguro.
- Comprobar captura persistente, continuidad y calidad del contexto real del
  clasificador. Los tests locales no equivalen a evaluacion de Gemini real.
- Calibrar latencia y deslizamiento con mensajes, decisiones, solicitudes y
  fills emparejados. Separar relojes, conversion de moneda, comisiones y datos
  ausentes; no sustituirlos por tolerancias amplias para lograr coincidencia.
- Inventariar cobertura de ticks y versiones originales de mensajes de 2026.
  Un mensaje fechado al segundo define un intervalo, no el tick exacto de un
  fill alternativo. Las ediciones finales no recuperan versiones desaparecidas.
- Congelar supuestos, presupuesto de busqueda y validacion no usada al elegir
  parametros. Primero Canal 2, despues Canal 1; sin promocion automatica.

Ni esta reparacion ni una curva historica positiva garantizan rentabilidad
futura, ausencia de perdidas consecutivas o una reconstruccion exacta de fills
que nunca ocurrieron. La finalidad es medir lo demostrable y mantener visibles
los casos que todavia no lo son.
