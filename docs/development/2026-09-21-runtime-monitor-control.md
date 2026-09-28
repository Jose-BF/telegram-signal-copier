# Control Integrado Del Monitor Gold555

Estado: 401 pruebas focalizadas y 7263 globales aprobadas, sin fallos,
errores ni omisiones. Evidencia: `A:/cm-ldvsr_q7/e/verification.json`.
Continuacion del control de aperturas, no admision de estrategia completa.

## Alcance Implementado

- DCA real envia cada pata al mismo libro que contiene entradas iniciales,
  protecciones nativas, exposicion y trayectoria monetaria.
- El monitor real ejecuta su bucle, trailing, guard temporal, cola de cierre,
  lectura de historial y finalizacion. No se sustituye por una politica paralela.
- Los cierres activos usan el precio ejecutable causal, no el precio preparado.
  Efecto, historial y acuse son hechos separados; el avance del reloj no
  confirma por si mismo una respuesta desconocida.
- El historial devuelve deals de apertura y salida con IDs estables, volumen,
  comentarios y costes declarados. Su perfil sintetico explicito usa comision
  y fee cero; no implica equivalencia de costes con el broker real.
- La contabilidad final del listener reutiliza el calculo comun que incluye
  fee, exige apertura propia y cierre completo de todos los tickets. Historia
  ausente o parcial da importe desconocido, no un total parcial presentado
  como definitivo. Los cierres manuales con magic cero siguen contando.
- La clasificacion usa roles nativos y `(time_msc, ticket)` para empates;
  no inventa milisegundos ni confunde apertura con cierre. Historiales nativos
  incompletos o ambiguos no acreditan una posicion cerrada. Se conserva la
  compatibilidad con registros antiguos sin roles.

## Controles Y Evidencia

Dieciseis controles DCA BUY/SELL cubren cinco patas, gaps, preparacion demorada,
resultado desconocido, stop anterior al acuse, intencion modificada y caducidad.
Diez recorridos del monitor cubren TP, gap a SL, cierre temporal, posiciones
desconocidas con recuperacion e historial desconocido. En los recorridos con
dinero conocido se compara exposicion y dinero por cotizacion, no solo saldo.
Se conservan ademas los diez controles de entrada del hito anterior.

- Regresion monetaria previa: `A:/cp-f21/red.xml`, cuatro fallos y tres verdes.
- Reparacion focalizada: `A:/cp-f22/result.xml`, 128 verdes.
- Clasificacion previa: `A:/cp-c5e6ca/red.xml`, 22 fallos y ocho verdes;
  reparacion del modulo: `A:/cp-5d7de0/green.xml`, 76 verdes.
- Paquete congelado: `A:/cm-ldvsr_q7/e`, Python 3.11.9, directorios y caches
  aislados en A:. `focused.xml`: 401 verdes, cero fallos/errores/omisiones.
- `controls/` conserva 36 trazas sinteticas con manifiesto SHA256; comprobados
  los 36 hashes sin discrepancias. Codigo e identidad se guardan antes/despues.
- `full.xml`: 7263 verdes; proceso global 439.94 s. Fuentes e identidad de
  implementacion sin cambios durante la ejecucion. SHA256 del XML:
  `b1c896f20fafeb538314ced8e273d7e773805c04c0a91f9d10155b4b4281aacf`.
- Ejecutor: `reports/e5-runtime-monitor-control-20260921/run_verification.py`.
  No sobrescribe las evidencias de los hitos anteriores ni borra fallos previos.
- Revision independiente estatica sin nuevos defectos confirmados en este
  alcance. No reemplaza las pruebas ni el contraste nativo.

## Lo Que Sigue Abierto

Los controles del bucle completo esperan su frontera de reposo y la cola antes
de avanzar la siguiente cotizacion. Los controles de componentes si prueban
cotizaciones durante algunas esperas, pero falta combinarlas con el monitor
completo ocupado. No acredita todas las ramas, contencion entre cestas,
recuperacion integral, aislamiento de proceso real ni ciclo Dubai completo.

Profit lock, valoracion nativa/FX, costes no nulos durante el recorrido,
overnight y politica de realizaciones parciales abiertas no quedan certificados
por estos controles. Los fees no nulos tienen regresion contable separada.
El reloj/cuenta son fixtures declarados, no mediciones de latencia de la VM.

Broker plano y contabilidad completa son estados diferentes: el runtime puede
finalizar una cesta cerrada con importe desconocido si no llego su historial.
La prueba conserva esa falta; no demuestra recuperacion contable posterior.

Siguiente bloque: concurrencia del monitor completo, ramas restantes y Dubai;
despues contraste de ventanas reales y admision por capacidad/dataset. Se
mantiene la revision conjunta antes de una busqueda masiva. No se promete
rentabilidad ni equivalencia nativa a partir de ensayos sinteticos.

Sin commit, push, despliegue, reinicio ni nueva comprobacion de la VM.
