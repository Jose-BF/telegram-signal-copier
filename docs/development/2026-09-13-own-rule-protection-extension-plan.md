# Extension De Break-Even Y Parciales

## Alcance

Continuacion autorizada del laboratorio de canal 1. Ampliar simulacion propia,
no cambiar operativa, capital, canal 2, VM o publicacion. Se conserva el perfil
base y se incorpora una capacidad opt-in `own_rule_be_partial_v1` al contrato
de proteccion. No se habilita por defecto ni por cambiar el nombre del perfil.

Break-even de precio: solo una cotizacion observable tras confirmar entrada
puede activar la intencion; el stop no existe hasta su instalacion. El stop
anterior gana frente a modificaciones o cierres pendientes en la misma quote.

Parcial: umbrales en dinero hipotetico de la cesta (no precio XAUUSD), peticion
de un volumen compatible con min/step del broker, procesamiento posterior,
remanente con proteccion vigente y segunda salida despues de la confirmacion.
El primer cierre parcial se solicita una sola vez. No es ejecucion parcial
por liquidez del broker. Se bloquean las combinaciones sin contrato representado.

## Hitos

- [x] Revisar estado y verificar `shared_family_lab_v1` antes de editar.
  Identidad `2f2f8e04ba57c43e3a5646fc8bac00f79cbd25b953b25deb8b104e8a108e9b78`.
  Copia verificada de sus fuentes de codigo en
  `runtime_data/canal1_history_20260913/implementation_before_extensions_v1`.
- [x] Regresiones previas: activacion causal BE, rechazo/reintento, stop antiguo,
  parcial seguido de runner/stop/tiempo, volumen residual, FX/swap, presupuesto
  de eventos y fin de datos pendiente. Controles BUY/SELL y limites de perfiles.
- [x] Implementacion independiente en scalar, fast y oracle; contrato y
  capacidad explicitos. Preservar el perfil base y los bloqueos no resueltos.
- [x] Catalogo y laboratorio opt-in actualizados; corregir unidades del parcial,
  conservando el archivo anterior y su estado historico.
- [x] Pruebas focalizadas y completas, control historico vigente v3 con misma
  muestra y limites, verificacion nueva; informe y mapa compartido actualizados.

## Limites

La extension exige entrada hipotetica schema2, mercado/proteccion integrados,
gestion propia sin proveedor y sin cliente serial. BE `price`; BE por proveedor,
tiempo o seleccion de patas siguen con su barrera hasta ampliacion especifica.
Parciales con entradas simultaneas y volumen parcial/remanente valido; escaleras
con futuras aperturas requieren otro contrato. Contexto previo sigue pendiente
de un bloque propio, no se fabrica volatilidad cero.

Tras cambiar motores, los resultados v1 siguen siendo evidencia de su codigo
archivado, no resultados vigentes por simple reetiquetado. Fuentes de mensajes,
precios y cobertura no cambian. No hay seleccion ni busqueda masiva.

## Evidencia Intermedia

Se conservaron 63 fuentes de codigo con sus hashes originales. La prueba de
contrato fallo primero por campo desconocido; tras incorporar solo el contrato,
24 pruebas fallaron por la barrera de capacidad y dos pasaron. La implementacion
hizo pasar las 26. Ampliacion posterior: FX, reparto de swap, limites de perfiles,
240 recorridos sinteticos de interacciones y limites numericos de precio.

Cuatro casos exactos de BE a 0.3 detectaron redondeo binario en scalar/oracle:
no se instalaba el stop que si instalaba fast. Se reparo la comparacion decimal
en la extension integrada, sin tolerancias ni cambios del BE legacy sin perfil.
Queda como regresion antes de cualquier control historico ampliado.

Pruebas focalizadas anteriores: 551 aprobadas en 15.98 s (89 nuevas de la extension
y dos nuevas del laboratorio incluidas). Suite completa: 5534 aprobadas en
401.59 s, 634 advertencias. Laboratorio v2 terminado y verificado: 5472
evaluaciones, 416 combinaciones completas y 32 bloqueadas por junio, sin
incidencias de motores/cartera. Las 392 combinaciones y 4788 resultados de motor
de las 14 familias anteriores coinciden con v1, excluyendo `behavior_digest`.

Revision posterior antes del cierre: NaN en proporcion/volumen parcial provocaba
`decimal.InvalidOperation`. Se conserva el contrato estricto: parametros no
finitos se rechazan con el ValueError de serializacion canonica, no se crea una
huella para ellos ni se fabrican ceros. Los finitos invalidos conservan un
resultado bloqueado. Se evita calcular lotes para un genoma ya invalido; tres
guardas independientes, sin modificar las trayectorias de parametros validos.
Se corrigio el fixture de escalera a dos patas para probar esa capacidad sobre
un genoma estructuralmente valido.

v2 y otras 63 fuentes se conservan en
`implementation_before_partial_validation_fix_v1`. Codigo definitivo congelado:
121 pruebas focalizadas aprobadas en 12.85 s; suite completa, 5539 aprobadas en
426.45 s y salida 0, 634 advertencias. Laboratorio v3 terminado y verificado:
identidad `55cb522be3239178a23359a791f23e954065796fd4345171b3e22d2837f4b2be`,
5472 evaluaciones, 416 combinaciones completas y 32 bloqueadas por junio.
Las 448 combinaciones coinciden con v2 excluyendo solo `behavior_digest`.
Inspeccion de volumen/confirmaciones/proteccion en 684 recorridos nuevos:
cero errores. Informe y mapa compartido actualizados, sin reetiquetar v1/v2.
Siguiente bloque separado: contexto previo a la entrada. Sin busqueda masiva,
ganadora, MT5, VM, ordenes, commit, push o despliegue en este bloque.
