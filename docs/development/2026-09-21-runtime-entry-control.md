# Control De Aperturas Del Runtime

Estado: control acotado verificado localmente; 351 pruebas focalizadas y 7148
globales aprobadas, sin fallos, errores ni omisiones.
Continuacion del control de protecciones, no admision de estrategia completa.

## Frontera

- El propietario externo de entradas impide que el modelo genere una primera
  entrada o DCA paralela. El controlador es quien envia cada apertura.
- La peticion preparada no elige el fill. El libro usa la cotizacion ejecutable
  causal al recibir el envio, con las hipotesis de ejecucion declaradas.
- Apertura, proteccion provisional, riesgo y salidas nativas usan el mismo libro.
  El acuse de apertura depende de una respuesta explicita; avanzar el reloj no
  convierte una ejecucion desconocida en confirmada.
- Los callbacks no pueden modificar el libro despues del corte, cierre del
  generador ni entre cotizaciones sin un estado causal activo. Las dos muestras
  de riesgo de una apertura se reservan juntas antes de crear exposicion.
- Research importa solo contratos puros. El runtime, cliente, worker y sus
  dobles de transporte permanecen en tests; no hay capacidad de enviar a MT5 real.

## Controles

`tests/test_runtime_replay_entry_controller.py` conecta EntryWatch y listener
Gold555, DurableEntryExecutor, servicio durable, cliente, worker y cola reales
al libro simulado. Diez controles BUY/SELL cubren apertura/proteccion/TP,
precio distinto entre preparacion y envio, stop provisional antes del acuse,
respuesta desconocida sin reenvio ciego y rechazo tras gap de precio.

Cada control compara la ultima muestra settled de todas las cotizaciones del
horizonte con una tabla explicita de dinero y volumen. Conserva los casos
bloqueados. Las trazas incluyen input, identidad, mensajes, llamadas, riesgo,
eventos y resultado. Son controles sinteticos, no resultados de inversion.

Los controles directos del modelo cubren propiedad de entrada, no duplicacion
automatica, no deduplicacion artificial de envios repetidos, reloj/ordinal,
rechazos, corte incompleto, riesgo y drawdown. La referencia de drawdown usa
la trayectoria post-evento, no picos diagnosticos previos al cierre nativo.

## Verificacion

- Primera integracion: `A:/cp-e16/result.xml`, 36 aprobadas.
- Revision independiente encontro mutaciones tras finalizar y agotamiento de
  riesgo sin resultado recuperable. Regresion previa: `A:/cp-e18/red.xml`,
  12 fallos esperados. Correcciones: `A:/cp-e19/green.xml`, 121 aprobadas.
- La bateria identificada y conservada se ejecuta con
  `reports/e5-runtime-entry-control-20260921/run_verification.py`. Usa rutas
  cortas y caches aisladas en A:, preflight de espacio y fuentes congeladas.
- Paquete final: `A:/ce-kiac8k2a/e/verification.json`, `sources.json`,
  `focused.xml`, `full.xml` y diez trazas en `controls/` con manifiesto SHA256.
  Focalizadas: 351/351, 20.156 s. Global: 7148/7148, 432.31 s de pytest
  (434.75 s del proceso), 10 advertencias, sin fallos/errores/omisiones.
  Fuentes e identidad de implementacion sin cambios durante la ejecucion.
- SHA256 de `full.xml`:
  `ff929d5df139480236ee4c0247f09a8cfd9f6237f69b8f17597c5c4d5c538d1d`.
  Los diez hashes de controles se comprobaron contra el manifiesto.
- Los ensayos fallidos previos se conservan. `A:/cp-e13` detecto un error de
  construccion de fixture; `A:/cp-e14`, acceso al reloj desde otro hilo;
  `A:/cp-e17`, expectativa de horizonte demasiado corta para dos rechazos.
  Se corrigieron sin omitir controles ni ampliar tolerancias monetarias.

## Limites Y Siguiente Paso

No se ejecuta aun el bucle autonomo completo del monitor: su arranque se captura
en el fixture. No se acredita el controlador DCA, cierres activos, lectura de
historial, recuperacion integral ni parada definitiva de la estrategia.
El estado de Signal tras un fill que ya cerro requiere ese monitor; no se
confunde ausencia de posiciones en broker con finalizacion del estado runtime.

El transporte usa clases reales en proceso, no aislamiento real de proceso.
Cuenta demo EUR y reloj son fixtures declarados. No acredita latencia de la VM,
fill nativo, margen, spread/slippage reales ni rentabilidad.

Sigue conectar DCA y ciclo completo del monitor al mismo libro, completar las
lecturas necesarias y comparar ventanas historicas completas independientes.
No se cambia el requisito de revision conjunta antes de una busqueda masiva.

Sin commit, push, despliegue, reinicio ni nueva comprobacion de la VM.
