# Canal 1: Tres Gestiones De Break-Even

Fijado antes del nuevo calculo de P/L. Descubrimiento retrospectivo, sin OOS
ni promocion. Base inmediata con rango, una posicion, SL/TP1 iniciales,
espera maxima 5 minutos y cierre a 60 minutos. No repetir las 24 politicas.

Diagnostico previo: 152 operaciones del control de una hora desfavorable;
42 reciben un aviso directo de BE antes de cerrar, 53 despues y 57 no tienen
tal aviso posterior a niveles. De las 47 perdedoras, 14 reciben el aviso antes
del cierre. Son tiempos de mensajes, no protecciones instaladas ni ahorro.

Tres gestiones congeladas:

1. Control sin BE, identico al estudio cerrado.
2. Solicitar BE al recibir la primera instruccion directa MOVE_SL_TO_BE del
   proveedor despues de disponer de los niveles. Modalidades condicionales,
   opcionales e informativas no ordenan BE. Recepcion de revision, no fecha
   de publicacion retroactiva. Ignorar otras gestiones del proveedor.
3. Solicitar BE despues de avanzar a favor la mitad de la distancia inicial
   cotizada hasta TP1. Medir ese avance desde el fill real del escenario;
   el umbral es un desplazamiento fijo calculado al decidir la entrada, con
   minimo de una unidad de cotizacion (0.01) y redondeo hacia arriba al paso
   0.01, nunca antes de alcanzar la mitad. Si TP1 ya se ha observado antes
   de solicitar entrada, la cancelacion original sigue prevaleciendo.

BE significa SL en el precio de entrada, no cero neto garantizado despues
de spread, costes o deslizamiento. La solicitud espera las colas existentes;
SL/TP anterior sigue vigente hasta aceptacion. Rechazos y reintentos se
modelan, no se sustituye un toque de precio por un cierre en BE.

Mantener 200 USD nominales de escala y lotes, los dos perfiles y recargo
anteriores. Misma cobertura de 3910 segundos, FX y reglas intradia. Las tres
gestiones se comparan sobre el universo amplio y R05 por separado mediante
su abstencion causal ya fijada. No cambiar umbrales segun resultados ni excluir
la gran ganadora para elegir una regla; describir su concentracion aparte.

Maximo 3 gestiones, 300 identidades, 20000 evaluaciones, 150 millones de
cotizaciones fuente, 12.000 millones de visitas y 4 horas. Matriz esperada
258 x 3 x 2 = 1548 filas. Contrastar los tres motores en todas las filas
calculables; el control debe igualar las filas anteriores. Guardar incidencias.

Antes de modificar el motor se han conservado 61 fuentes vinculadas en
`runtime_data/canal1_history_20260913/sources_before_absolute_be_v1` con hashes.
Los archivos anteriores siguen perteneciendo a esa implementacion, no se
declaran revalidados por el mero cambio de codigo.

Todo local: sin MT5, VM, ordenes, reinicios, flags, commit ni push. Resultado
exigido: comparacion economica completa o incidencia concreta preservada.
