# Canal 1: Calidad De Entrada, Hipotesis Acotada

Protocolo fijado despues de ver el estudio simple, antes de calcular estos
filtros. Es descubrimiento retrospectivo, no OOS ni promocion.

Motivo: entrada al recibir niveles supera a espera/rango/DCA, pero 99 ganancias
medias de 47.19 EUR frente a 26 perdidas medias de 161.34 dejan un margen fino.
Ademas, 21 casos cerrados naturalmente antes de una hora, fuera de la cobertura
completa de cuatro horas, suman -543.03 EUR. No ocultar esta sensibilidad.

Comparar cuatro condiciones de abstencion: distancia al TP1 / distancia al SL
al menos 0.25, 0.50, 0.75 o 1.00. Solo entrada inmediata al primer mensaje con
rango, salida completa TP1 y gestion ya fijada de 60/240 minutos. Dos perfiles
anteriores, costes y riesgo identicos. Ocho combinaciones, ninguna optimizacion
adicional por mes, direccion, fecha o resultados posteriores.

Precio de decision: cotizacion observada en la primera solicitud de entrada,
no fill posterior. Exigir indice inicial cero, identidad de solicitud y
coherencia con el riesgo nominal congelado. Si la base no solicita orden, el
filtro no puede crearla. Si los datos base estan bloqueados, conservar bloqueo;
no convertir desconocidos en operaciones rentables ni rechazos de filtro.

Es una transformacion de abstencion sobre senales aisladas y mercado exogeno:
si pasa el filtro, todos los eventos y resultados base se conservan sin cambio;
si no pasa, no se abre posicion. No modifica el motor, el lote, el stop, el
objetivo ni el reloj. No acredita adaptador live, interaccion entre senales,
cartera ni realizabilidad de futuros fills. Todo se guarda en un archivo nuevo.

Limites: 8 combinaciones, 258 identidades, 2 perfiles, 4128 filas; cero llamadas
al motor, MT5, VM, operaciones, commit o push. Una ejecucion local acotada a
cinco minutos. Conservar tambien las negativas. Terminar con comparacion de
beneficio, peor perdida, concentracion por dias y sensibilidad de cobertura.
