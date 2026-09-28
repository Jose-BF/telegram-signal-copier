# Recuperacion De Confirmaciones Tardias

Estado: 727 pruebas focalizadas y 7447 globales aprobadas, sin fallos, errores
ni omisiones. Congelacion conjunta en `A:/cr-sg3xrdtc/e`, fuentes e identidad
sin cambios durante la ejecucion. 86 trazas verificadas contra sus hashes.
No hay publicacion ni nueva comprobacion VM; objetivo general todavia abierto.

## Diferencia Que Se Prueba

Una espera del cliente puede vencer mientras el trabajador todavia ejecuta o
retiene la respuesta. El llamador observa DISPATCHING y conserva el indice
pendiente; posteriormente el transporte real persiste DONE. Esto no equivale
a UNKNOWN persistido cuya respuesta se perdio y hay que reconstruir por historial.

Los controles nuevos no escriben un DONE artificial en el almacen ni extraen
el ticket del libro para inyectarlo al runtime. Retienen la respuesta del mismo
worker/cliente real sobre el broker modelado, esperan el timeout real de dos
segundos, avanzan las cotizaciones y liberan la respuesta. El SL puede cerrar la
posicion antes de entregarla. La evidencia distingue efecto, ACK y aplicacion.

## DCA Cerrada Antes De Recuperar

La aplicacion al arrancar descartaba una entrada durable confirmada si ya no
habia posicion abierta. Ahora conserva ticket, precio e indice ejecutado y
resuelve la incertidumbre de entrada, sin ordenar proteccion ni cierre para un
ticket ausente. Una segunda aplicacion no duplica ticket ni contabilizacion.
Una lectura de posiciones desconocida sigue bloqueando esa aplicacion.

Conservar el ticket obliga a incluir su historial en el denominador monetario.
Historial desconocido, vacio, parcial o de otra propiedad devuelve total
desconocido, no el subtotal favorable de las patas conocidas. Los controles
unitarios incluyen comision, swap y fee no nulos. No asimilan recuperar identidad
a confirmar toda la contabilidad.

Cuatro recorridos BUY/SELL comparan aplicacion en siguiente tick y por la rutina
de startup con el monitor real. Ambos finalizan en -279.7 bajo el contrato
sintetico, con dos entradas y dos SL, sin nuevos envios; el recorrido por
cotizacion conserva volumen y dinero, no solo el total. No reinician el proceso.

## Primera Entrada Cerrada

La primera Gold555 podia perder su Signal si la confirmacion llegaba cuando
el ticket ya estaba cerrado. Se reconstruye ahora con historial completo,
propiedad, posicion, volumen, precio, orden temporal y dinero finito comprobados.
La hora inicial procede del deal de entrada; la expiracion sigue siendo la del
watch original. No se usa el momento de recuperacion para alargar esos plazos.

Si faltan historial, reloj o cargos, la entrada permanece pendiente. No se
instalan SL/TP ni se ordena un cierre sobre la posicion ausente. La reentrega
no reinicia una Signal ya finalizada ni borra sus cancelaciones. Esta capacidad
no afirma recuperar el reloj de todos los casos de posicion todavia abierta.

Dieciocho pruebas nuevas incluyen BUY/SELL, cierre de proveedor, lectura
desconocida, duplicados y evidencias invalidas. Dos controles adicionales usan
el monitor y finalizador reales despues de recuperar la primera entrada;
respetan la gracia de 30 segundos tras iniciar el monitor. Verifican una sola
entrada/cierre, -142.4 modelados y rejilla de riesgo/exposicion completa. No
son resultados de la cuenta real, ni una prueba de ingestion Telegram.

## Ensayos Conservados

- `A:/cp-r01/red.xml`: dos fallos reales de DCA cerrada en startup y dos
  expectativas incorrectas que no contemplaban el aviso de timeout intencional.
  Se exige ahora la anomalia exacta, sin eliminarla del registro.
- `A:/cp-r02/green.xml`: 39 aprobadas; `A:/cp-r03/result.xml`: 35 aprobadas.
- Primera entrada: `A:/li-bfa91/red.xml`, ocho fallos; `A:/li-89003/focused.xml`,
  51 aprobadas y una expectativa antigua que descartaba el watch sin historial.
  Se corrige esa expectativa: debe permanecer pendiente sin ordenar operaciones.
- `A:/cp-r04/result.xml`: dos expectativas de finalizacion inmediata que omitian
  la gracia del monitor. `A:/cp-r05/result.xml`: dos expectativas de rejilla que
  omitian la ultima cotizacion de la cinta. No se cambia la politica para pasarlas.
- `A:/cp-r06/result.xml`: 62 aprobadas, sin fallos ni omisiones.
- Runner conjunto: `reports/e5-runtime-recovery-control-20260921/run_verification.py`.
  Fuentes, entorno, XML, salidas y trazas se conservan en el paquete congelado.
- `A:/cr-sg3xrdtc/e/focused.xml`: 727 aprobadas, 104.250 s de proceso.
- `A:/cr-sg3xrdtc/e/full.xml`: 7447 aprobadas, 580.265 s de proceso. SHA256:
  `be129e3b690abcdb057b9eb7836eb2f13fbbd811eea71e70786a74501453358a`.
  `verification.json` confirma fuentes/implementacion sin cambios; mantiene
  en falso paridad nativa, estrategia completa, publicacion y objetivo terminado.
  Esta global incluye la correccion de sincronizacion del test de gateway del
  bloque anterior, sin alterar ni borrar su resultado historico fallido.

## Pendientes Reales

No existe aun un conciliador automatico UNKNOWN->evidencia broker->DONE probado
de extremo a extremo. `DurableExecutionService.reconcile` recibe el resultado
ya construido; disponer de esa API no demuestra el descubrimiento del fill.
La respuesta desconocida persistida sigue bloqueando reenvios y finales falsos.

El bloque siguiente debe consultar evidencia por el cliente/worker, identificar
inequivocamente la ejecucion y derivar el outcome conservando intento, cuenta,
senal y pata. No tomar tickets privados del modelo, no casar solo por precio o
importe y no convertir ausencia temporal de deals en rechazo confirmado.
Casos ambiguos/incompletos deben permanecer en el denominador como bloqueados.

La recuperacion del proceso entero debe contrastar tambien el journal, el
reconstructor de watches y el finalizador de huerfanos. Los controles actuales
no prueban todos los cortes entre escritura de evento, creacion de Signal y
finalizacion. La revision independiente confirma que el evento
`gold_555_late_fill_already_closed` impide restaurar ese watch, pero no demuestra
por si solo perdida contable: el finalizador de huerfanos tiene otra ruta por
historial, limitada a siete dias, tamano de journal e historia disponible.
Hay que probar ambas rutas y la continuidad de una escalera todavia vigente.

La admision temporal nativa debe comprobar ademas los relojes del historial
contra los limites causales de la intencion, no solo el orden entre deals.
Siguen pendientes contencion entre cestas, valoracion nativa,
procesos/latencia VM y contraste historico independiente. El objetivo general
permanece activo; no se habilita busqueda masiva ni despliegue automatico.
