# Segunda Ronda De Reparaciones E1-E2

Fecha: 19/09/2026. Estado: RR1-RR5 y RI1-RI7 reparados y verificados
localmente; pendientes de revision independiente con Astra. Sin commit, push,
VM, reinicio, configuracion ni orden real. E3 no esta integrado ni activado.

Actualizacion: la [revision de esta ronda](2026-09-19-e1-e2-second-repair-review.md)
confirma transiciones sin cubrir y mantiene E3 sin aprobar. Se conservan los
resultados historicos siguientes; no cubren los nuevos contraejemplos.

Actualizacion posterior: los hallazgos quedaron reparados localmente en la
[tercera ronda E1-E2](2026-09-19-e1-e2-third-repair-results.md), pendiente de
nueva revision independiente.

## Transporte E Intenciones

- El plazo asincrono es absoluto desde la entrada hasta antes del despacho. Una
  espera del executor no renueva el tiempo disponible ni permite enviar una
  peticion caducada.
- La capacidad asincrona permanece reservada hasta que termina el hilo real,
  incluso si la cancelacion ocurre durante su arranque.
- Un sobre consumido se retiene antes de leer o escribir SQLite. Recuperar,
  reiniciar o cerrar persiste primero los resultados conocidos y da una espera
  acotada de 100 ms a avisos ya en transito antes de degradar lo desconocido.
- Un aviso tardio resuelto libera solo su propia peticion pendiente. La peticion
  durable completa, la sesion del trabajador y el resultado terminal completo
  se cotejan antes de aceptar una respuesta.
- Dos sobres distintos con la misma identidad no se sobrescriben: se conserva
  la primera evidencia y la contradiccion se rechaza explicitamente.

Estas garantias impiden reenvios ambiguos y perdida silenciosa de evidencia en
los contraejemplos probados. No sustituyen la conciliacion con MT5 cuando nunca
llego ni se persistio una respuesta.

## Inventario Schema 4

Nueva salida, sin reemplazar schemas 1-3:
`runtime_data/runtime_simulation_inventory_20260919_v4/inventory.json`.

- SHA256: `e4ed904124026807ae0395248fd4b579ec7213eb175244d20eecab1006b2d491`.
- 1.166.783 bytes; generador SHA256
  `e22df6a7dc573bc4c7378b4d644fcc114b3e3957351327fb17d0b50f6882ca1f`.
- 192.484 lineas de eventos; 267 senales, 253 recepciones, 29 fechas UTC y
  53 grupos dia/canal.
- 5.937 intentos: 5.927 enviados y 10 conocidos como no enviados.
- Cero identidades conflictivas, cuarentenas, ambiguedades contractuales o
  multiplicidades de paridad/nativo en estas fuentes concretas.
- 103 senales conservan al menos un tramo sin contrato precedente.
- Las 5.937 duraciones son legado; cero cumplen timing schema 1 certificado.
- 118 filas auxiliares siguen sin poder colocarse y se conservan como huecos.

El schema 4 mantiene las senales con identidad conflictiva en cuarentena,
invalida contratos ambiguos hasta nueva evidencia suficiente, trata la sesion
ausente como hueco, valida la duracion total, recalcula el contrato de la
recepcion mas temprana y conserva todas las filas de paridad y cestas nativas.
Los conflictos fuera del periodo no se incorporan al universo de senales.

## Verificacion

- 78 pruebas focalizadas superadas en 11,18 s.
- La carrera de cancelacion asincrona supero 10 repeticiones consecutivas.
- Suite completa: 5.968 pruebas superadas y 638 avisos existentes en 530,35 s.
- Compilacion de los modulos afectados: correcta.
- Entorno local: Windows, Python 3.14.2.

## Limites Y Siguiente Gate

No hay Python 3.11 instalado en este host. Siguen pendientes la prueba nativa
de bloqueo MT5 de 60 s, carga sostenida, propietario unico tras reinicios,
conciliacion real y migracion E3 de todas las llamadas. El corpus aun carece de
frontera fiable de envio en 249 senales y no certifica fidelidad integral entre
simulacion y produccion.

El siguiente gate es revision independiente con Astra de codigo, regresiones y
schema 4. Solo si esa revision no encuentra defectos abiertos se puede aprobar
E1-E2 y disenar E3. Publicar o activar requiere autorizacion separada y las
guardas de exposicion del proyecto.
