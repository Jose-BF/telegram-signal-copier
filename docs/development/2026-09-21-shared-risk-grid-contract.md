# Contrato De Rejilla Conjunta De Riesgo

## Implementacion Local

Contrato fijado antes de editar el driver, implementado despues del checkpoint
de 6.661 pruebas de la conexion. `SharedRiskFrame` se emite tras todas las
rondas/respuestas de una cotizacion; conserva referencias al ultimo estado
asentado de cada cesta, no copia ni vuelve a calcular sus fills o costes.
Una cesta terminada solo arrastra su realizado si esta plana, con flotante cero
y resultado final sin bloqueos. Una cesta no admitida conserva su identidad y
contribucion desconocida. Si falla un paso dentro de una cotizacion no se
fabrica su barrera final. El numero de referencias de la rejilla tiene limite
propio `max_events`, comprobado antes de reservarlas, incluido el tramo final
en el que todas las cestas hayan terminado.

`summarize_shared_risk` valida orden, identidad, fase y antiguedad de cada
contribucion; agrega antes de reducir extremos. Conserva cada contribucion y
separa metricas completas, metricas de muestras conocidas y valor del corte
final, que puede ser desconocido aunque existan muestras anteriores conocidas.
Moneda y precision proceden del contrato de entrada. No lo llama equity de
cuenta: solo P/L conjunto de los scopes modelados inicialmente planos.

`research/risk_metrics.py` comparte la reduccion monetaria con el comparador
historico `risk_trajectory`, sin cambiar su seleccion/convencion de muestras.
Las identidades de investigacion y de ambos CLI de riesgo incluyen la nueva
dependencia; no se reetiquetan informes historicos.

Primeras 12 pruebas fallaron por ausencia de la nueva API: es desarrollo de
un contrato nuevo, no demostracion de doce defectos del motor antiguo.
27 pruebas nuevas actuales; 216 focales aprobadas antes de anadir el control
exhaustivo (tambien aprobado), incluyendo trayectoria
historica, comparadores, procedencia y 99 controles de motor suspendible.
Revision independiente detecta un P2 del validador: sustituir el ultimo estado
plano de una cesta por un estado plano preentrada del mismo scope/traza podia
dar final 0 y DD 200 centimos, sin bloqueos, frente a final 200 y DD 80 correctos.
Reproducido de forma independiente. No se ha demostrado seleccion incorrecta
en el driver; el validador debe exigir el ultimo settled del ordinal y, para
arrastre, el estado terminal confirmado. Reparacion aplicada despues del
ensayo: dos regresiones (ordinal actual y arrastre terminal) fallaron antes y
pasan despues. El validador requiere el ultimo settled del ordinal, y para
arrastre tambien el ultimo del scope, resultado terminal sin bloqueos, P/L
conocido consistente y estado plano. Revision final acotada sin nuevos
hallazgos materiales; 29 controles directos del revisor aprobados. La revision compara tambien
cinco controles con/sin rejilla en memoria: decisiones, resultados, transporte
y fases iguales antes de cualquier limite de captura.

Evidencia intermedia `reports/e5-post-event-risk-grid-20260921/`: protocolo,
resultado y fuentes congelados. BUY/SELL conservan todos los resultados por
cesta, eventos de riesgo, transporte y politicas del checkpoint previo. El
gap confirma DD post-evento 80 centimos con origen cero. No es certificacion
nativa. Primera suite global: **6.687 aprobados y un fallo**, una advertencia
previa, 472,34 s en consola / 465,823 s JUnit. Fuente congelada sin cambios;
se conserva en `verification-before-repair.json` y `pytest.xml`, SHA256
`6aea8d24ebbbc7bfe5c58b089206b15501fc0f3b98fc2baf0d6f406a8c87185e`.
La version intermedia no esta aprobada ni se sobrescribe su evidencia.

Fallo: el test de edicion diferida de canal2 uso la cola persistente local
normal y arranco monitores; `os.replace` del spool en `runtime_data` recibio
PermissionError WinError 5. El defecto demostrado del ensayo es su aislamiento
incompleto; no se atribuye la causa fisica del bloqueo a OneDrive, VM o broker.
No se modifican ni limpian los datos locales preexistentes como parte del fix.
El fixture del modulo ahora conserva la persistencia en tmp_path, impide
arranque del runner y sustituye monitores/alertas por dobles. Los tests de
enrutamiento y niveles siguen activos; sus spies especificos prevalecen.
Un test verifica escritura local y ausencia de runner. No se cambia runtime.

El primer parche de fixture inserto un yield en el test nuevo y pytest lo
marco xfail con advertencia. Se detecto, corrigio y repitio: **294 focales
aprobadas**, sin omisiones, incluyendo 29 de rejilla y 75 de mensajes. Esto
no prueba por si solo el aislamiento de todos los modulos de la suite. El
siguiente ensayo general usa ademas BOT_RUNTIME_DATA_DIR temporal exclusivo,
conservado para inspeccion y registrado en su manifiesto. No afecta al entorno
del bot ni borra el directorio runtime normal.

## Verificacion Final Del Bloque

La siguiente general termino con **6.683 aprobados y ocho fallos**, 460,11 s
en consola / 459,934 s JUnit; una advertencia previa. Se conserva completa en
`pytest-repaired.xml`, SHA256
`0011cfb3293f880986e71359f461267855ad5d577e7ff4ae7badb033366be009`.
No es una suite limpia. Cuatro fallos fueron del lanzamiento del ensayo:
invocar pytest.main desde stdin impedia resolver el main de multiprocessing
en Windows. Los otros cuatro eran expectativas de tests sobre rutas/corpus
por defecto que no respetaban un runtime temporal externo.

Reparacion posterior SOLO de tres archivos de tests: ruta interna/externa
de cache explicitas, corpus historico vinculado al seed legacy en vez del
runtime mutable, y destino de pruebas de reparacion de archivos explicito.
No se modifico codigo productivo o de investigacion despues de esa general.
La repeticion usa un subprocess con `python -m pytest`, no pytest.main en stdin,
y BOT_RUNTIME_DATA_DIR exclusivo. Todos los modulos fallidos y todos los
modulos afectados de riesgo/mensajes/procedencia se repitieron: **471 aprobados**,
sin fallos, errores, omitidos ni advertencias; 47,62 s consola / 40,590 s JUnit.

`sources-isolated-focused.json` registra comando, entorno y hashes efectivos;
`verification-final.json` confirma fuentes estables y reutilizacion explicita
de la general salvo los tres archivos de tests reparados. No se suman los
conteos solapados ni se afirma una nueva general completamente verde. Es
verificacion combinada de la implementacion sin cambios y las reparaciones
acotadas del ensayo; la proxima modificacion compartida volvera a requerir
general con entrada normal y runtime exclusivo.

SHA256 `pytest-isolated-focused.xml`:
`7aa28320ec6bbda18fdde2b514b8cf13e2b944081cb47e5a37455c38e347ad19`.
`result-repaired.json` conserva los tres controles validos EXACTAMENTE iguales
tras reparar el validador (incluidos informes, trazas y resumen); SHA256
`a812240da4e02145bbaf08686c254f0ba20786119a501e4b716fafcf65883e44`.
Las 1.024 secuencias del reducer se contrastan con extremos por pares, tanto
en unidades enteras como Decimal. El caso de parciales/costes de la rejilla es
aritmetico: no habilita por si solo la familia de ejecucion de cierres parciales.

Sin commit, push, VM, reinicio ni cambio de estrategia. Directorios temporales
conservados, sin limpiar datos preexistentes. Los ficheros runtime locales
usados por ensayos anteriores no deben asumirse evidencia exclusiva de vivo;
los controles historicos deben mantener sus fuentes originales verificadas.
No quedan ensayos propios en ejecucion al cerrar este checkpoint. El objetivo
completo sigue activo y la paridad nativa sigue sin certificarse.

## Siguiente Frontera

La revision de capacidades identifica una entrega concreta antes de admitir
la gestion real Dubai: observacion causal de gestion e intencion persistente
de cierre de cesta mientras el cliente esta ocupado. `client.pending` retorna
antes de actualizar BE/pico de politica; `request_market_close` rechaza una
entrada aun en vuelo. No basta retirar el gate `basket_guard_v1`.

Separar observacion disponible de despacho, sin alimentar decisiones desde
esta rejilla diagnostica. Una decision terminal debe conservar motivo/reloj,
cancelar solo entradas no enviadas, esperar el resultado de las comprometidas
y cerrar sus fills tardios una vez. Reutilizar books, colas y reglas existentes;
no eliminar globalmente retornos que protegen contratos de entrada fresh/batch.
BUY/SELL, pico-giveback durante espera, SL nativo, acuse tardio, entrada tardia,
datos desconocidos y cutoff son los controles de cierre de esa frontera.

Gold555 tiene objetivos relativos, trailing y profit-lock en su genome local;
no debe confundirse con `absolute_levels_be_v1`. Esa familia y la estrategia
555 requieren contratos propios, no heredan cobertura por el nombre Gold de
un control sintetico. Disponibilidad de lecturas, preparacion/autorizacion,
contraste historico amplio y anclas nativas siguen abiertos.

## Decision Previa A Implementar

Continuacion de `2026-09-21-connected-policy-replay.md`. No modifica politicas,
fills, resumen legacy, tolerancias, reglas live ni gates de portfolio.

La traza de fases sigue siendo `model_phase_diagnostic`: explica estados
intermedios del motor. El producto nuevo sera `post_event_quote_grid_v1`,
generado por una barrera global despues de todas las cestas y rondas de una
cotizacion, antes de avanzar los generadores. No es equity observada del broker.

Scope: P/L de las cestas modeladas, inicialmente sin posiciones. No equivale a
cuenta completa: no incluye posiciones ajenas, depositos, retiradas ni margen.
Cada contribucion conserva canal, senal, posiciones/tickets y ordinal fuente.
Dos cotizaciones con timestamp igual siguen siendo dos ordinals diferentes.

## Invariantes

1. Un solo estado final por cesta/ordinal, tras las respuestas inmediatas de
   todas las rondas; no todos los estados `settled` son barreras globales.
2. No sumar un estado de una cesta de la cotizacion anterior con otro actual.
   Solo se puede arrastrar realizado de una cesta finalizada, plana, sin
   bloqueos ni costes pendientes. Una cesta faltante/incompleta nunca es cero.
3. Un corte durante un ordinal no fabrica su muestra final. Conservar prefijo,
   denominador y causa. Reservar presupuesto antes de asignar la nueva rejilla.
4. Total conjunto = suma sincronizada de realizado y flotante. Extremos y DD
   se calculan despues de sumar, no sumando extremos o DD individuales.
5. Origen cero pertenece al scope modelado inicialmente plano. Con exposicion
   inicial externa hace falta otro contrato, no inferir esa equity.
6. Valores desconocidos permanecen desconocidos. Metricas completas nulas si
   falta cobertura; metricas sobre muestras conocidas se etiquetan aparte.
7. No eliminar trazas anteriores ni reetiquetarlas como observaciones nativas.
   El contrato de observaciones/deals reales requiere alineacion independiente.

## Casos De Aceptacion

- TP gap ya congelado: fase previa +19,20 EUR y cierre +2,00; rejilla posterior
  -0,80, -0,80, -0,80, +2,00; DD con origen cero 0,80. DD de fases 17,20 y
  legacy 0,00 permanecen distintos y explicitamente identificados.
- Dos cestas compensadas que cambian en el mismo ordinal: no crear un pico
  conjunto por actualizar una antes que otra. Comprobar ambos ordenes.
- Minimos desfasados A=[0,-10,0], B=[0,0,-20]: minimo conjunto -20, no -30.
- Cesta con FX desconocido y otra conocida: contribucion conocida conservada,
  total desconocido y metricas completas no admitidas.
- Cierre parcial, comision y swap: contabilizacion unica; posiciones abiertas
  y realizadas separadas, sin sumar coste de nuevo al arrastrar estado plano.
- Dos ordinals con igual timestamp: ambos conservados. No afirmar orden
  nativo submilisegundo sin evidencia del enlace del deal al ordinal.
- Presupuesto agotado durante una cesta/ronda: prefijo global anterior
  conservado, sin snapshot final ficticio para el ordinal incompleto.
- Cesta no admitida desde el inicio: identidad conservada, nunca inferir un
  estado inicial/final valido de la ausencia de puntos.

## Cierre De Esta Frontera

Tests de mecanismos anteriores, contraste aritmetico independiente y evidencia
de memoria acotada. Revision independiente y suite general si cambia el
driver compartido. Conservar fuentes/protocolo/resultados por version.

Esto cierra una definicion util de riesgo del modelo; no certifica fills,
disponibilidad de lecturas, igualdad con MT5 ni estrategias reales aun no
admitidas. El paso posterior es adaptar/contrastar sobre rejilla comun con
observaciones nativas, preservando su convencion de orden e incertidumbre.
