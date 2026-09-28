# Canal 1: Entender La Senal Antes De Elegir Gestion

El usuario pide un enfoque abierto y sencillo: comprender que senales envia
el trader, como responde el mercado y si tiene sentido entrar inicialmente,
esperar su rango o repartir entradas. Se aparca el catalogo amplio sin borrar
la evidencia anterior. No hay autorizacion de cambios live ni canal 2.

## Preguntas Actuales

1. Cual es la secuencia habitual: sticker, entrada con precio/rango, niveles,
   comentarios de progreso, peticiones de proteccion y cierre.
2. Cuanto tarda en conocerse el rango y donde esta el precio entonces.
3. Con que frecuencia sigue directamente, retrocede, visita el rango y rebota;
   cuanto tarda y que excursiones adversas acompanian esos recorridos.
4. Que oportunidades quedan al esperar el rango, y cuantas se pierden o llegan
   al stop. Hasta donde se profundiza dentro del rango antes de recuperarse.

Usar BUY y SELL por separado y juntos. Los meses describen diferencias, no
son examen obligatorio ni seleccion por fechas. El objetivo no es acertar el
maximo retrospectivo; los extremos observados no son beneficios ejecutables.

## Primer Bloque Descriptivo

Reutilizar los 258 IDs del inventario raw, sus 240 primeras recepciones
respaldadas, los mensajes originales y las cotizaciones existentes. Asociar
por reply cuando exista y, en su ausencia, secuencia del aviso anterior con
direccion compatible; marcar esa inferencia y las ambiguedades. Nunca confundir
un comentario con rango con una orden de entrada. Conservar las revisiones
y el momento en que cada rango/nivel se conoce. Rangos incoherentes quedan
visibles, no se corrigen con el resultado posterior del precio.

Medir recorrido a 5/15/60/240 minutos, contexto de precio de los 15 minutos
anteriores, excursiones, orden de movimientos de +/-2/5/10 unidades de precio
y recuperacion tras retrocesos. Para el primer rango operativo observado,
registrar visita, precio disponible, cruce de centro/extremo y orden observado
de TP1/SL. No convertir esos cruces en fills ni P/L. Separar ventanas completas
y parciales: los huecos limitan lo que sabemos, no vuelven falsas las
observaciones existentes ni permiten afirmar que no hubo un movimiento.

Este bloque describe XAUUSD con Bid/Ask, no euros ni una cuenta. La falta de
FX o contrato overnight no bloquea una descripcion de precio; tampoco habilita
una simulacion monetaria. Se mantienen intactas las barreras del simulador.

Limites operativos: 300 identidades, un millon de lineas raw, 150 millones de
cotizaciones fuente, 150 millones de puntos seleccionados y una hora de
calculo. Cero optimizacion de estrategias y cero ordenes. Fuentes y salida
identificadas, resultados anteriores preservados. Pruebas focalizadas de
asociacion, disponibilidad, BUY/SELL, barreras y datos incompletos; no repetir
toda la suite por un informe si los motores no han cambiado.

## Despues

Elegir pocas hipotesis a partir de patrones observados: entrada inicial,
espera de rango y escalonado limitado a una o dos adiciones. Comparar a riesgo
total semejante, no simplemente aumentando lote. Sin martingala ilimitada,
sin suponer recuperacion obligatoria ni borrar las senales que salen mal.
Las reglas por rango necesitan soporte causal de niveles en el motor; si
falta, implementarlo con pruebas antes de presentar dinero o una candidata.

Informar probabilidades observadas, distribuciones, perdidas y ejemplos
comprensibles. Buscar una expectativa favorable es razonable; no esta
garantizada por la autenticidad del canal. Consultar al usuario solo si surge
una decision material que no pueda resolverse con su criterio delegado.
