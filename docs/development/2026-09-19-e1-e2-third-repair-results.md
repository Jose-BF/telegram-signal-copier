# Tercera Ronda De Reparaciones E1-E2

Fecha: 19/09/2026. Estado: SR1-SR4, SI1-SI4 y SL1 reparados y
verificados localmente; pendientes de una nueva revision independiente con
Astra. Sin commit, push, VM, reinicio, configuracion ni orden real. E3 sigue
sin integrar ni activar.

Actualizacion: la [revision independiente de esta ronda](2026-09-19-e1-e2-third-repair-review.md)
confirma las regresiones anteriores, pero reproduce fallos adicionales en las
transiciones de recuperacion, aplicacion, plazo y compatibilidad, ademas de
fronteras de identidad y procedencia de datos. E1-E2 siguen sin aprobar; las
afirmaciones siguientes describen el alcance de las pruebas de
implementacion, no el cierre de esas nuevas transiciones.

## Transporte E Intenciones

- La admision durable y el despacho son estados distintos. Cada request_id y
  attempt_id queda unido a la peticion completa y a la sesion del trabajador
  antes de entrar en la cola.
- Un fallo demostrado antes del despacho deja la intencion PREPARED, cierra la
  admision como FAILED_PRE_DISPATCH y libera el gateway sin crear UNKNOWN ni
  reenviar silenciosamente.
- La correlacion pendiente usa request, intent y attempt. Reutilizar una
  identidad incompatible se rechaza antes del envio.
- Timeout, muerte del trabajador, recuperacion y cierre drenan y reintentan
  primero la evidencia retenida. Un resultado concluyente tardio solo puede
  sustituir un UNKNOWN creado por recuperacion para la misma peticion, intento
  y sesion.
- Start, close, call y recuperacion comparten la misma exclusion dentro del
  gateway. Esto cierra la carrera local; no demuestra aun propiedad unica
  entre procesos o reinicios del host.

La base de intenciones queda en schema 3. Las cuatro regresiones permanentes
cubren fallo SQLite anterior al despacho, DONE en el borde del timeout,
start/close concurrentes y request_id incompatible.

## Inventario Schema 5

Nueva salida, sin reemplazar schemas 1-4:
`runtime_data/runtime_simulation_inventory_20260919_v5/inventory.json`.

- SHA256: `a7280509658d6897dda398cea0edb8db84cfb6687703fe9b404428cc0c345636`.
- 1.172.621 bytes; generador SHA256
  `8f30d0ea990d0d05d85ce4b58191bff90ea21c5d785ea69776745c8ffd92f462`.
- Las seis fuentes conservan exactamente los hashes del v4. El v4 permanece
  inmutable con SHA256
  `e4ed904124026807ae0395248fd4b579ec7213eb175244d20eecab1006b2d491`.
- 192.484 lineas, 267 senales, 253 recepciones, 29 fechas UTC y 53 grupos
  dia/canal; 5.937 intentos, 5.927 enviados y 10 no enviados.
- Cero conflictos o ambiguedades en estas fuentes concretas. Las pruebas
  sinteticas demuestran que contratos simultaneos conflictivos, protecciones
  contradictorias y recepciones empatadas ya no dependen del orden de lectura.
- Cada fila de paridad y cesta nativa conserva `day_utc` y
  `source_row_index`; 103 senales mantienen huecos contractuales y 118 filas
  auxiliares siguen sin poder colocarse.
- Las 5.937 duraciones siguen siendo legado: el inventario no las convierte
  en timing schema 1 certificado.

## Latencia Schema 2

El agregador agrupa intentos por identidad estable antes de calcular
percentiles y gates. Las copias exactas se cuentan como duplicados, las
identidades contradictorias se excluyen y dos inicios incompatibles de una
misma decision ya no se unen escogiendo el menor reloj. Por tanto, treinta
copias de un intento no pueden declarar listo el escenario de simulacion.

## Verificacion

- 71 pruebas focalizadas superadas en 11,67 s.
- Suite completa previa a la ultima prueba adicional: 5.980 pruebas superadas,
  638 avisos existentes, 389,02 s. La prueba adicional solo amplia la
  cobertura del agregador y esta incluida en la pasada focalizada posterior.
- Compilacion de los cinco modulos afectados: correcta.
- `git diff --check`: sin errores de espacios; solo avisos CRLF existentes del
  checkout Windows.
- Entorno local: Windows, Python 3.14.2.

## Limites Y Siguiente Gate

Este bloque corrige contraejemplos generales; no certifica una VM, un broker
ni una estrategia rentable. Siguen pendientes Python 3.11, bloqueo MT5 nativo
de 60 s, carga sostenida, propiedad unica tras reinicios, conciliacion real y
la migracion E3 de todas las llamadas al transporte aislado.

El siguiente gate es una nueva revision independiente con Astra de estas
fronteras y sus pruebas. Solo si no quedan defectos abiertos se podra aprobar
E1-E2 y disenar E3. Publicar o activar requiere autorizacion separada.
