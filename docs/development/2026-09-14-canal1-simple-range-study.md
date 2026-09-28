# Canal 1: Comparacion Sencilla Tras La Radiografia

## Pregunta

La radiografia v2 ya esta calculada y verificada. No reiniciar el catalogo de
4217 reglas ni otro gran barrido. Pregunta siguiente: **con riesgo comparable,
compensa esperar el rango y merece la pena anadir dentro de el?**

Evidencia y limites en `docs/audits/2026-09-14-canal1-signal-anatomy.md`.
No existe aun una candidata economica seleccionada. La prioridad de
investigacion es entrada principal en rango con gestion simple de TP1.

## Comparacion Acotada

Preparar primero un contrato causal de niveles para los motores existentes.
`research/causal_replay.make_path` rechaza niveles del proveedor sin ese
contrato. No quitar el rechazo ni convertir toques de precio en fills reales.
Los SL/TP absolutos, cambios disponibles y reglas de cancelacion han de
atravesar los mismos mecanismos de cotizacion, latencia y proteccion.

Cuatro formas de entrada, separadas antes de buscar resultados monetarios:

1. Una entrada al recibir el primer mensaje con precio/rango, como control.
2. Una entrada al primer precio ejecutable dentro del rango.
3. Una entrada esperando el punto medio, sin perseguir precio.
4. Entrada principal al visitar el rango y dos anadidos pequenos, punto medio
   y extremo. Propuesta inicial de reparto de presupuesto de riesgo: 60/25/15.
   No escalar el lote sin ajustar a la perdida nominal total ni inventar
   fracciones de lote que el contrato del simbolo no permita.

No confundir el control 1 con entrar al sticker antes de disponer del stop:
ese control anterior de reglas propias sigue disponible, pero es otra politica.
Las entradas de precio unico llevan control independiente, no rango ficticio.

Primer objetivo de salida: TP1 completo o parcial principal en TP1 con resto
hacia TP2, declarando previamente el movimiento de proteccion. Comparar
gestion a 60 y 240 minutos; fijar la espera de entrada inicialmente a 5
minutos desde que se dispone de los niveles. Son 16 politicas principales,
no un espacio abierto. Maximo 24 incluyendo controles de precio unico.
Dos perfiles de ejecucion ya retenidos y recargo de costes anterior; maximo
4 horas de calculo y 150 millones de cotizaciones fuente por bloque.
Fijar todos los detalles aun abiertos en un protocolo antes de ejecutar:
cancelacion si ya se alcanza TP1/SL, respuesta a correcciones, perdida por
senales superpuestas y protecciones invalidas, tamanos, riesgos y costes.

Las elecciones de 5/60/240 minutos y el reparto nacen de descubrimiento ya
observado: no llamarlas preseleccion independiente ni OOS. El contexto de
15 minutos y el sentido BUY/SELL son diagnosticos, no optimizadores automaticos.

## Evidencia Para Decidir

### Protocolo V1 Fijado Antes Del Calculo Economico

Presupuesto nominal de perdida: 200 USD por senal aislada, solo como escala
de investigacion, no capital del usuario ni garantia de perdida maxima.
Lotes redondeados hacia abajo a 0.01 y limitados a 1.00 por tramo; si un
reparto no llega al minimo, queda bloqueado. DCA: riesgo 60/25/15 en extremo
cercano, centro y extremo lejano. Entrada simple dividida: riesgo 75/25 para
TP1/TP2. DCA dividido: primeros dos tramos a TP1, ultimo a TP2.
No se mueve a break-even en esta primera comparacion.

SL y TP absolutos iniciales viajan con cada solicitud de mercado; no son
ordenes limitadas descansando en el broker. Se ignoran revisiones posteriores,
sin corregir erratas retrospectivamente. Se cancelan solicitudes todavia no
enviadas cuando ya se observa TP1 o SL. Las solicitudes en vuelo conservan
su latencia y posible rechazo. Espera 5 minutos; mantenimiento 60 o 240
minutos desde el primer fill; ventana comprobada de nuevo con 5 minutos de
espera y 10 segundos adicionales de liquidacion. Mismos umbrales de datos.

Dos perfiles retenidos (referencia y adverso), spread y FX historicos mas
10 EUR por lote ejecutado de coste completo. 24 politicas, 300 senales,
20.000 evaluaciones, 12.000 millones de visitas de cotizacion y 4 horas como
limites duros. Matriz rapida completa y paridad escalar/oraculo en la primera
senal con precio de cada mes, direccion y formato, elegidas antes del P/L.
Comparacion adicional sobre identicas senales admitidas en todas las reglas,
ambos perfiles y ambos horizontes, sin borrar bloqueos del universo completo.

- Mismo universo conservado, mismas fechas por comparacion y todos los no
  ejecutados, bloqueados o todavia abiertos visibles. Meses son descripcion,
  no una exigencia de perfeccion ni una particion unica enero contra hoy.
- Usar las primeras versiones disponibles; conservar errores y aplicar una
  correccion solo desde su recepcion si la politica la acepta. No usar el
  conocimiento de que mas tarde habra una correccion como filtro previo.
- Evaluar beneficio neto, peor perdida, excursion abierta y tiempo de
  recuperacion; no elegir por tasa de TP1 ni por aumentar el volumen.
- Los tamanos de referencia no representan el capital del usuario. Mantener
  limites de evidencia monetaria, FX, swap y exposicion. Sin garantia de stop.
- Comprobar paridad escalar/rapido/oraculo cuando cambie el contrato de
  ejecucion; ampliar pruebas solo a los mecanismos afectados.
- Parar tras la comparacion con una respuesta: hipotesis descartada,
  candidata para prueba prospectiva o bloqueo concreto. No mutar miles de
  variantes ni declarar elegida la mejor de un conjunto malo.

Todo local/offline. Canal 2 fuera. Sin MT5, VM, ordenes, cambios live,
reinicios, commit ni push. Cualquier futura publicacion exige autorizacion
actual y comprobaciones de despliegue/exposicion; investigacion no la activa.
