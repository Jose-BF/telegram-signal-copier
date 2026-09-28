# Experimento De Tiempos Y Transferencia

## Alcance Fijado Antes De Ejecutar

Peticion del 12/09: investigar por que una posicion real cerro antes que su
equivalente simulado, probar tiempos/casuisticas y comprobar la explicacion
en otras jornadas sin eliminar casos ni sobreajustar la correccion.

Es un experimento local de fase 3, no una busqueda de estrategias ni una
reparacion publicada. Se conserva el genoma Gold 555 y los motores existentes.
No se modifica el bot, no se envian ordenes, no se reinicia la VM.

1. Preparar y vincular mensajes originales, ticks, ordenes/deals y versiones.
   El 10/09 es descubrimiento; el 09/09 y las primeras dos senales conservadas
   del 11/09 son contraste sin reajuste. Son evidencia retrospectiva: ya se
   han inspeccionado partes de esos dias, por lo que no son OOS intacto.
2. Probar 96 combinaciones de espera hasta fill y espera desde fill hasta
   respuesta, iguales para todas las ordenes. Fill en ms: 0, 100, 250, 500,
   1000, 2000, 5000, 10000, 20000, 30000, 45000, 60000. Acuse en ms: 0, 100,
   250, 500, 1000, 2000, 5000, 10000. El control sigue siendo 250/250.
3. Elegir dos hipotesis sin consultar resultados del contraste: una minimiza
   la diferencia de minimo de la cesta 2774, priorizando resultado completo
   y numero/volumen de posiciones; otra minimiza el error medio absoluto de
   hora de entrada de todo el 10/09, priorizando resultados completos y
   numero/volumen. Precio y hora de salida desempatan. Las cantidades se
   conservan en sus unidades, sin tolerancias que conviertan discrepancias
   en coincidencias. El minimo monetario parcial no es riesgo certificado.
4. Congelar ambas selecciones antes de simular las jornadas de contraste.
   Comparar todos los casos contra 250/250: entradas, volumen, horas/precios,
   cierres, beneficio y minimo realizado mas flotante de cada cesta.
5. Como control separado, desplazar solo el fill de la solicitud B3 real
   por una rejilla de 0 a 60 s en pasos de 250 ms. Mantener su objetivo
   relativo y buscar su primer cruce posterior en la misma cinta. Es un
   diagnostico condicionado a una solicitud observada, no replay completo
   ni modelo predictivo; no entra en la seleccion de la rejilla general.

## Limites Y Cierre

- Presupuesto: 96 escenarios de descubrimiento, hasta 3 escenarios unicos
  por jornada de contraste, 241 casos del control condicionado, hasta 30
  senales por jornada y 1800 s de calculo. Conservar todo resultado generado.
- No elegir por beneficio. No ajustar lotajes, distancias, reglas ni spread.
- Cero entradas sigue siendo un caso. Datos incompletos, desacuerdos de
  motores y diferencias de version permanecen con identidad y motivo.
- Retener todos los mensajes NOW observados de cada ventana, incluidos los
  recibidos tarde por paradas. Esto no acredita cobertura de mensajes que
  nunca llegaron a registrarse. No sintetizar su disponibilidad historica.
- La validacion monetaria y de ticks mantiene las reglas existentes. No
  usar FX futuro ni ensanchar el limite de 5 s. Reportar cobertura parcial
  aunque el motor antiguo no la marcara.
- La primera prueba discrimina si dos constantes de espera bastan. No
  representa por si sola las colas compartidas ni el bloqueo de todo Python.
  Una coincidencia obtenida en 2774 no demuestra la causa interna del broker.
- Terminar con comparacion reproducida y controles independientes, tanto si
  mejora como si empeora. No seguir ampliando la rejilla tras ver contraste.
  Una reparacion general posterior requerira contrato y pruebas propios.

## Progreso

- [x] Fuentes e inventario congelados: 19 senales, 10 + 7 + 2.
- [x] Regresiones de los auxiliares y control base verificados.
- [x] Rejilla del 10/09 ejecutada y dos selecciones congeladas.
- [x] Contraste sin reajuste del 09/09 y ventana del 11/09 terminado.
- [x] Control condicionado, verificacion y conclusiones documentados.

El protocolo original sin estas actualizaciones de estado se conserva en
`../../runtime_data/timing_transfer_20260912/plan_frozen.md`, con el hash
registrado antes del experimento. No se han cambiado alcance ni criterios.

Resultado: [informe y limites](../audits/2026-09-12-timing-transfer-results.md).
Los dos ajustes seleccionados empeoran el contraste. No se adopta ninguno.
