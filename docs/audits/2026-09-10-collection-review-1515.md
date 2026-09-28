# Revision Real De La Recogida

## Alcance Y Horas

La revision de las 14:00 Madrid no se ejecuto. Esta revision se inicio a las
15:10 y utiliza un checkpoint real cerrado a 15:14:17.910 Madrid. No es la
captura final de las 17:00, ni una validacion de rentabilidad de estrategias.

VM comprobada a 15:36:36 Madrid: `892bc33c8` limpio, principal 5216 con su
arranque original, terminal 6312 sin reinicio, heartbeat de 15:36:20. El bot
declara cero posiciones, senales y entradas pendientes en ese heartbeat.
La ultima consulta NATIVA de posiciones del checkpoint fue a las 15:14;
no sustituirla por el heartbeat para autorizar un reinicio futuro.

## Recogida Comprobada

- Checkpoint: 124682 filas, 232630505 bytes de delta; fuente original intacta.
- Mensajes: 223 observaciones, 114 revisiones, 109 reenvios compatibles;
  cero contradicciones de contenido y cero observaciones invalidas.
- Auditor causal existente, sin modificar: 123407 filas relevantes completas,
  cero enlaces, identidades, huellas o evidencias requeridas bloqueadas.
- Gestion: 61253 decisiones, 61194 sin accion, 73 acciones declaradas y
  solicitadas, cero pendientes sin registrar; 25 enlaces de coalescencia.
- Seis senales nuevas enlazadas con 19 posiciones y 38 deals MT5; todas las
  posiciones estan cerradas. Dos posiciones anteriores se conservan aparte.
- Importe nativo de esas 19 posiciones: 8.38 EUR, sin diferencias con las
  confirmaciones por ticket. Comision, fee y swap observados: cero en este corte.
- Un cierre de posicion no implica fin de la senal: Gold 2786 conservaba
  cuatro entradas elegibles. Ese estado era previsto, no un cierre perdido.

Estos resultados verifican la recogida del corte, no toda combinacion posible,
ni que una ejecucion alternativa tuviera los mismos fills o costes.

## Defectos Reproducidos

1. **Cierre repetido en el diario.** Dubai 22411 escribio tres eventos de
   cierre de -24.99 EUR. Dos repeticiones anadian -49.98 EUR a una suma ingenua.
   Corregido localmente con exclusion por senal y confirmacion de finalizacion;
   mantiene reintentos tras fallo/cancelacion y no bloquea otras senales.
   Regresion: dos casos fallaban con tres resultados; ahora registran uno.
2. **Flotante residual del observador.** Gold 2786 tenia 1.72 EUR realizado y
   1.38 EUR flotante pese a no tener posiciones abiertas. El agregado 3.10 EUR
   era incorrecto; no era el saldo MT5. Corregido el refresco de dinero en cesta
   plana sin eliminar entradas elegibles. Regresion BUY/SELL y reproduccion con
   el estado observado y siguiente tick nativo: flotante 1.38 -> 0, realizado
   y posiciones intactos. Comparacion retrospectiva, no prediccion independiente.
3. **Falsos conflictos del recolector.** Comparaba la hora de recepcion y la
   forma de entrega como si fueran contenido. Corregido localmente: las mismas
   49 observaciones de ventana pasan de 23 falsos conflictos a cero. Los
   conflictos verdaderos, omisiones y bloqueos heredados siguen bloqueando.
   No se reescribieron ni admitieron los manifiestos antiguos.

## Precios Y Limites

Se recogio un suplemento NATIVO de solo lectura para 12:28-13:14:17.910 UTC:
37139 ticks XAUUSD y 11432 EURUSD. Cuenta, terminal y desfase de reloj de
10800 segundos comprobados antes/despues. Es captura retrospectiva, separada
del protocolo futuro. Auditoria suplementaria completada y verificada:
19/19 posiciones con fills nativos y FX causal recalculados sin diferencias,
8.38 EUR en ambos calculos, cero casos sin datos. Las 38 horas de entrada/salida
estan dentro de ambas series; FX al cierre con edades de 24 a 166 ms.
El tramo comun con la captura original coincide exactamente en todas las
columnas y orden: 11520 filas XAUUSD y 3422 EURUSD. Los intervalos maximos
observados son 1010 ms y 4050 ms respectivamente; se conservan ocho ticks
XAUUSD con milisegundo duplicado. No se certifica continuidad universal.

El dinero a partir de fills reales no certifica fills alternativos. No se
han cambiado tolerancias, estrategias, lotes, cuentas ni presupuestos de busqueda.

## Verificacion Y Estado

Pruebas focalizadas: 175 de recolector/consumidor, 150 de observador y 16 de
finalizacion/integridad aprobadas. Revision independiente sin hallazgos
accionables; deduplicacion acotada a la instancia y proceso, no persistencia
universal ni nueva garantia de escritura durable del diario.
La bateria completa termino a 15:44:50 Madrid: **5093 aprobadas, cero fallos,
errores u omisiones**, con 425 fuentes y wrappers estables. La primera pasada
conservo un fallo de integridad estatica (5092 aprobadas); se ajusto la ubicacion
de la finalizacion sin debilitar esa prueba y se repitio toda la bateria.
La revision independiente del ajuste final tampoco encontro problemas.

**Cambios de producto solo locales, sin commit, push ni reinicio del bot.**
En VM solo se ejecutaron auxiliares propios de lectura fuera del checkout.

Las 424 fuentes y dos wrappers originales, fixtures y evidencia se conservaron
ANTES de los arreglos en `review_1515/frozen_original/verified_sources.zip`,
SHA256 `1277ab4b447c94887af0e01770faf26fd79b20d5c4ebd0393a46d9e91b86da62`.
El recolector corregido requiere otro freeze; no sustituirlo en el protocolo
actual, cuyos conflictos originales permanecen visibles. La captura de las
17 sigue con fuentes/protocolo originales; se encadeno al checkpoint verificado
para no releer 232 MB, sin saltar bytes ni ampliar limites. Recibo retenido.

## Evidencia Local

Base: `runtime_data/day_review_20260910/review_1515/`.

- `capture/manifest.json`: SHA256
  `30a048fc9544de53110763bc1b907f9ef9887348fa4adab60e199dce5d74da2a`.
- `raw_observations_audit.json`, `management_audit.json`, `summary.json`.
- `money_audit/report.json`: SHA256
  `4c00a6e4c91be204e6fe6e711e929ea7cad5e5af79030f4c275142dd6144dc33`.
- `shadow_flat_incident.json`, `collector_repair/checkpoint_diagnostic.json`.
- `supplemental_prices.zip`: SHA256
  `070785d7787f08e26767af0bb8d21f709859a8b6f938ed6ce112a83208a8abdf`.
- `supplemental_price_audit/report.json`: SHA256
  `1bf3822a08a679b7ef7f2c235ee8e711a7328177e3b18005d33241ac522611e7`.
- `verification_v2/final.json` y `verification_v2/full_suite.xml`; XML SHA256
  `516818241a7c25ddb53abbbbe07eac9b409026df5079695b2660cde262f4e2cc`.
- `final_capture_reanchor.json`; la unica tarea final sigue a las 17 Madrid.
- `completed_tasks.json`: retirada verificada de las dos tareas auxiliares
  propias terminadas con resultado cero, conservando recibos y manifiestos.

La auditoria monetaria original fue verificada por el coordinador antes de la
edicion del recolector; conserva su binding al codigo anterior, disponible en
el archivo congelado. No reescribirla para ocultar ese cambio posterior.

## Lo Que Falta

1. Publicar y comprobar las reparaciones operativas solo con autorizacion
   aplicable y guardas de exposicion recientes. Ahora siguen sin activar.
2. Fijar un protocolo nuevo para el recolector corregido antes de su ventana.
   El corte de jueves conserva 23 bloqueos heredados del recolector anterior;
   la reparacion local no los borra ni convierte la captura en certificada.
3. Contrastar la simulacion desde mensajes/precios independientes con las
   operaciones observadas, conservando discrepancias y casos incompletos.
   El recalculo exacto de fills reales de este informe no sustituye ese paso.
4. Ampliar admision historica y medir escala antes de una busqueda masiva,
   todavia pendiente de revision conjunta. No se ejecutaron candidatas nuevas.
