# Politicas Conectadas Al Transporte Compartido

## Estado

Continuacion local del plan de simulacion fiable. Rama
`feature/gold-555-live-trial`, base
`7415941a410d18288fa4e08da76809f70b125efa`; la base Git no identifica los
cambios locales. Sin commit, push, acceso a VM, reinicio ni ordenes reales.
El objetivo completo sigue abierto. No se cambia ninguna estrategia live.

El coordinador ya recibe solicitudes generadas por las decisiones del motor,
y sus concesiones afectan al precio, momento y gestion posterior de cada cesta.
Esto sustituye el aislamiento artificial entre cestas para los perfiles
admitidos en este diagnostico. No es todavia una reproduccion completa del
cliente live ni habilita busquedas, portfolio, margen o promocion.

## Implementacion

- `research/shared_transport.py`: respuestas externas observadas, retirada de
  solicitudes no enviadas y capacidad restante consultable. Un timeout no
  libera la conexion ni reenvia una solicitud. Una respuesta tardia se drena.
- `research/dubai_iterative/engine.py`: extrae el paso de una cotizacion sin
  duplicar la politica. El generador puede reanudar una concesion en esa misma
  cotizacion sin inventar otra observacion de precio ni otra decision de pata.
- `research/dubai_iterative/shared_replay.py`: todas las cestas presentan sus
  solicitudes antes del arbitraje. Una llamada de otra cesta puede retrasar
  entradas, modificaciones y cierres; un SL/TP instalado sigue actuando.
- Cada respuesta procede de los books de mercado/proteccion. No se suman dos
  veces sus tiempos de procesamiento/confirmacion. Las rutas sin modificacion
  efectiva tambien liberan su concesion exactamente una vez.
- Cierre del proveedor retira entradas pendientes no enviadas; no revoca una
  llamada ya comprometida. Identidad global incluye canal, cesta y solicitud.
- Estados de riesgo conservan realizado, flotante y posiciones antes/despues
  de fills, swap y cierres. Un presupuesto compartido reserva capacidad antes
  de calcular/construir cada snapshot, no solo al serializarlo.
- Limites de solicitudes, eventos, cotizaciones y rondas dejan prefijos y
  cestas incompletas explicitas. No se eliminan del denominador ni se completa
  artificialmente el futuro al cerrar un generador.

Contrato actual: cinta Bid/Ask/FX identica para todas las cestas, cuenta EUR
declarada, preparacion inmediata y orden de rondas explicito. Rechaza cintas
incompatibles y precios de salida distintos del lado ejecutable. No integra
aun lecturas MT5 compartidas, autorizacion/preparacion nativa ni disponibilidad
real de observaciones. El perfil Dubai basket_guard y las familias de niveles
absolutos/BE no quedan habilitados por estos controles genericos. Estas ultimas
no son sinonimo del genome Gold555 de objetivos relativos/trailing/profit-lock.

## Evidencia

Directorio: `reports/e5-connected-policy-replay-20260921/`.

- `protocol.json`: entradas ordenadas congeladas, BUY y SELL; desarrollo
  sintetico, no datos historicos ni OOS. No se regenera para obtener exito.
- `sources-final.json`: fuentes efectivas del motor y tests, entorno Python,
  identidad de implementacion y hash del protocolo, previos a la suite global.
- `result.json`: ensayo intermedio conservado con su propia identidad.
- `result-final.json`: repeticion sobre las fuentes congeladas, con entradas
  identicas al protocolo. Ambos controles completos, sin bloqueos.
- `risk-definition-incident.json`: discrepancia de medicion abierta, descrita
  abajo. Es un incidente diagnostico intermedio, no resultado certificado.

Control conectado: la modificacion de Gold ocupa la conexion hasta el segundo
8; el cierre decidido por Dubai en el segundo 2 espera. Su SL instalado actua
en el segundo 4, y la solicitud de cierre posterior encuentra la posicion ya
cerrada. Aislada, la misma politica cierra en el segundo 3. BUY/SELL conservan
el mismo mecanismo, y cada muestra flotante se contrasta con calculo Decimal
independiente. Son politicas genericas de control, no las live Dubai/555.

Pruebas focales: 370 aprobadas. Incluyen 33 de conexion y 23 de transporte
incremental, compatibilidad de los 48 resultados completos previos del motor,
y los 64 informes congelados del transporte. Suite general de esta version:
**6.661 aprobados**, 476,17 s de consola / 476,048 s JUnit, cero fallos,
errores u omitidos. Diez advertencias: nueve por `record_property` con el
formato xunit2 seleccionado en el comando y una previa de pandas. El siguiente
ensayo usara `junit_family=legacy`, sin cambiar tests por esas advertencias.

`verification.json` confirma identidad, protocolo y todas las fuentes/test
congelados sin cambios durante el ensayo. SHA256 de `pytest.xml`:
`106104c0961674fd2b0471dcca0fc0b1d46773d23a6009089829612e1cc80b11`.
SHA256 de `result-final.json`:
`769ec466e41ad022e8121fe4dff31ac930ee216ae522dc3a5f134a8918df203c`.
Esta verificacion no se atribuye a modificaciones posteriores de la rejilla.

Revision independiente acotada encontro y permitio reproducir: reintentos de
modificacion sin efecto en la misma cotizacion, lado de salida incompatible,
agotamiento de solicitudes sin informe parcial y snapshots temporales por
encima del presupuesto. Corregidos con regresiones. Revision final de la
ultima frontera: 81 controles en memoria, prefijo preservado, sin calculos
ni concesiones despues del fallo de reserva; 10 snapshots construidos y
devueltos, frente a los 107 anteriores. No es revision universal del sistema.

La primera repeticion del protocolo se detuvo por serializacion de fechas
con `T` frente al espacio del protocolo original. Se restauro la serializacion
original y se comprobaron todas las entradas sin modificar el protocolo.

## Incidente De Medicion Del Riesgo

Un salto de precio por encima del TP produce un estado previo al cierre
valorado en +19,20 EUR; el modelo realiza +2,00 EUR al precio del TP. El resumen
escalar antiguo informa drawdown 0,00 EUR; recorrer todas las fases nuevas
produce 17,20 EUR. No son la misma rejilla ni prueban el recorrido nativo.

No se ha cambiado la cifra antigua ni aumentado una tolerancia para encajarla.
`risk_parity_verified=False` y la limitacion del resumen escalar son explicitas.
Un estado previo al hit explica el orden interno del modelo, pero no prueba
que el broker haya observado esa equity antes de ejecutar el TP. A su vez,
el resumen antiguo no incluye origen cero ni todos los estados posteriores.

Siguiente reparacion: [rejilla conjunta versionada](2026-09-21-shared-risk-grid-contract.md),
definir y probar separadamente rejilla de estados
internos y rejilla comun posterior a eventos; incluir origen cero, cierres y
costes, mantener desconocidos y comparar contra anclas nativas en instantes
compatibles. Para cuenta/canal, sincronizar exposiciones antes de calcular
drawdown; nunca sumar minimos individuales. No elegir una rejilla porque
acerque el resultado historico deseado.

## Trabajo Pendiente Del Objetivo

1. Revision/pruebas generales de esta integracion cerradas con fuentes estables.
2. Resolver el contrato de riesgo anterior y su contraste independiente.
3. Integrar disponibilidad de lecturas y preparacion/autorizacion donde
   afecten decisiones; admitir los perfiles reales con pruebas especificas.
4. Contrastar varios dias y canales con datos causales suficientes, sin
   ocultar FX faltante, reinicios ni cambios de estado desconocidos.
5. Completar retencion y condiciones operativas de VM; revision y autorizacion
   vigentes antes de cualquier publicacion. La validacion del simulador no es
   un requisito universal nuevo para toda reparacion operativa independiente.
