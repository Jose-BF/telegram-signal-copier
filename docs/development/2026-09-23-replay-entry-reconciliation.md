# Conciliacion Historica En El Replay

Estado: implementado y probado localmente. Sin publicacion, reinicio ni
verificacion de la VM.

## Que Cambia

El broker sintetico expone deals y ordenes por ventana temporal acotada.
Conserva los identificadores, precio, volumen, comentario y reloj de la misma
entrada que registra el libro, sin adelantar acontecimientos de la cinta. La
reentrega de una entrada con respuesta perdida consulta ese historial por el
transporte real del proyecto. El servicio durable atribuye la ejecucion una
sola vez o deja UNKNOWN cuando falta evidencia.

Despues del commit durable, el controlador offline comunica el resultado al
libro. La entrada deja de estar pendiente con un evento `entry_reconciled`
fechado en la observacion, no con `entry_acknowledged`. El timestamp de ACK
continua vacio; un resultado final parecido no puede ocultar este retraso.
El libro comprueba que request, orden, deal, precio, volumen y propiedad
coincidan con el efecto nativo que ya contiene. Una observacion incongruente
no cambia el estado. No se ha conectado este observador a produccion.

## Evidencia Y Limites

- Prueba local de muerte del proceso padre despues del efecto: dos variantes
  (historial presente/vacio) terminan sin segundo envio; el hijo se detiene y
  el cliente sustituto recupera o conserva UNKNOWN segun la evidencia.
- 58 pruebas enfocadas aprobadas tras conectar la observacion; 23 aprobadas
  tras anadir controles de identidad adversariales y BUY/SELL recuperados.
- Primer ensayo global, `A:/cr-iljnz2gi/e`, fallo en 314 casos: una condicion
  confundia ACK programado con ACK recibido en el camino ordinario. Se
  conservo esa evidencia; la condicion ahora exige el estado confirmado.
- Ensayo final, `A:/cr-wl_h0rl9/e/verification.json`: 742 controles enfocados,
  7.622 pruebas globales y 88 controles sinteticos; cero fallos, errores u
  omisiones. Fuentes e identidad de implementacion estables durante la corrida.
- El replay prueba su propia consistencia con un backend MT5 simulado. No
  demuestra que los datos del broker real tengan la misma disponibilidad,
  reloj o formato; tampoco mide demoras reales de Telegram, VM o red.
- Siguen pendientes el barrido automatico de UNKNOWN al arrancar, los cierres
  antes de recuperar con conciliacion monetaria, y la comparacion independiente
  de exposicion, flotante y drawdown en multiples sesiones completas.
- No se admite ninguna estrategia nueva ni se certifica el simulador completo.

La verificacion local no reemplaza evidencia observada del broker ni de la VM.
