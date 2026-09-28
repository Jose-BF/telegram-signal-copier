# Ejecucion: Criterios Y Trabajo Completado

Peticion del 11/09: avanzar el trabajo offline pendiente y aclarar latencia,
medias y rechazos. No autoriza busqueda masiva, despliegue o nuevas revisiones.
Este documento concreta el uso de los datos y resultados locales obtenidos;
no modifica el protocolo congelado ni declara aprobada la fidelidad de MT5.

## Para Que Medimos

Una senal historica indica cuando nuestras reglas pueden empezar a actuar,
no garantiza una ejecucion instantanea a ese precio. El retraso importa por
su posible efecto sobre precio, oportunidad de entrada y protecciones activas.
No buscamos predecir el tiempo exacto de una orden hipotetica del 15 de marzo.
Las condiciones de ese dia no se reconstruyen poniendo una media reciente.

Separar tres tramos: senal disponible -> decision/solicitud; solicitud ->
ejecucion; ejecucion -> respuesta recibida por el bot. Una espera deliberada
de entrada es regla de estrategia, no latencia del broker. Una respuesta
lenta no desplaza retrospectivamente una ejecucion ya ocurrida.

Valores utiles por solicitud: identidad de senal/accion/intento, canal y
version; tipo abrir/modificar/cerrar; hora de solicitud, ejecucion y respuesta;
precio y volumen pedidos/ejecutados; Bid/Ask y hora de la cotizacion; codigo
de respuesta y motivo. Duraciones locales con reloj monotono y misma sesion.
Comparar relojes broker/cliente solo con su normalizacion acreditada; no
recortar negativos a cero ni dividir un tiempo total arbitrariamente en dos.

## Criterios Finitos

| Aspecto | Criterio de revision |
| --- | --- |
| Identidades y decisiones | Ninguna senal desaparece; solicitudes/operaciones enlazadas sin duplicados. Misma regla e inputs deben producir la decision prevista. Una diferencia de numero/tipo de entrada no se absorbe en una tolerancia de precio. |
| Calculo del motor | Se mantienen pruebas de resultado conocido y acuerdo de los tres motores bajo exactamente los mismos supuestos; no se ensanchan sus tolerancias. |
| Dinero observado | Mismos fills, costes y FX causal deben reconciliar a la precision de cuenta, sin residuo inexplicado de centimos. Una media de costes no se presenta como conciliacion observada. |
| Ejecucion hipotetica | Escenarios fijados antes del resultado; informar cambios de precio, decision, cierres, P/L y riesgo conservando todo el universo. No prometer un fill real exacto. |
| Tiempos reales | Media, mediana, extremos y muestra por tipo/canal; distinguir ejecucion de respuesta. No aprobar la probabilidad futura de un retraso por dos senales ni llamar ping al recorrido completo. |
| Rechazos | Los determinados por reglas/restricciones se modelan por causa, no como una moneda al aire. Los no modelados se declaran; ningun cero observado se convierte en probabilidad cero. |

El alcance hipotetico ya admite diagnosticos bajo escenarios declarados.
No requiere una tolerancia universal de +/- X ms contra cada fill de MT5.
La certificacion cuantitativa frente al broker es distinta: sigue abierta,
con tolerancias sin aprobar en el freeze anterior. No se etiqueta un
diagnostico como esa certificacion ni se desactivan sus guardas.

## Medicion Ya Existente Reutilizada

Resumen nuevo de solicitudes/respuestas de las seis senales de la tarde
del 10/09, extraido de evidencia previamente retenida y verificada. Es una
muestra de diagnostico, no una estimacion representativa del broker.

| Canal | Senales / solicitudes de apertura | Media peticion-respuesta | Mediana | Maximo |
| --- | --- | ---: | ---: | ---: |
| Gold | 2 / 9 | 762 ms | 375 ms | 3688 ms |
| Dubai | 4 / 7 | 268 ms | 250 ms | 484 ms |

Las nueve aperturas Gold tienen respuesta DONE. Dubai conserva seis DONE y
un rechazo 10016 por stops invalidos. Las patas de una misma senal no son
muestras independientes. No se deduce una tasa futura ni se aplica la media
Gold como parametro de fill. Se retienen tambien seis respuestas de cierre;
los rechazos por posicion ya cerrada no se mezclan con rechazos de apertura.

`runtime_data/execution_work_20260911/retained_execution_summary.json` conserva
los 16 pares, seis cierres, referencias de evento, relojes, codigos, precio
y limitaciones. Se verificaron por separado enlaces y medias/medianas.
No hubo nueva consulta MT5 para producir este resumen ni auditoria repetida
del beneficio de las 34 posiciones ya conciliadas.

## Sensibilidad Ejecutada Ahora

Se usa una estrategia fija del control anterior, una posicion de referencia
0.01, datos Gold del 28/07. No se elige la estrategia por estos resultados.
Antes de correr se fijaron cuatro escenarios: base existente de fill250ms y
ack250ms; fill1000ms; fill5000ms; y ack1000ms manteniendo fill250ms.
1000/5000 son escenarios de estres declarados, NO cuantiles estimados, limites
aceptables, perdida maxima ni parametros nuevos del bot. Resto de reglas,
precios, costes, horizonte y universo iguales. No se modifica el spread.

| Escenario | Senales conservadas | P/L hipotetico EUR | Mayor caida EUR | Senales cuyo P/L cambia frente a base |
| --- | ---: | ---: | ---: | ---: |
| Base fill250/ack250 | 10 | -4.08 | 5.39 | 0 |
| Fill1000/ack250 | 10 | -4.71 | 5.74 | 7 |
| Fill5000/ack250 | 10 | -3.73 | 4.92 | 6 |
| Fill250/ack1000 | 10 | -4.08 | 5.39 | 0 |

Estos resultados demuestran sensibilidad del ejemplo, no rentabilidad ni
robustez certificada. Mas retraso no siempre empeora cada fill: el mercado
puede moverse a favor o en contra. El escenario mas lento no es por si solo
un peor caso. No se escoge el escenario de mayor beneficio.

Se verificaron los tres motores, todos los mensajes (44 identidades, diez
disparadores), y cero cambios de volumen/causa de salida en este control.
Cambiar solo ack modifica las marcas de confirmacion y gestion, pero en
este ejemplo no cambia las salidas ni el dinero. No se generaliza a todas
las estrategias; algunas dependen de recibir ese acuse para continuar.

## Medida De Capacidad

Cuatro controles de un dia: 6.06-6.39 s cada uno y 504-512 MiB de RSS sumada
de sus procesos. Control fijo de tres dias: 28 senales, 84 evaluaciones,
21.06 s y 1084.5 MiB. Se muestrea cada50ms la suma RSS padre/hijos, que puede
contar paginas compartidas: no es un pico exacto de memoria fisica privada.
Incluye lanzamiento y calculo, no el verificador posterior.

Cinco ejecuciones / 204 evaluaciones totales, cero estrategias nuevas, sin
busqueda. Los dos controles base reproducen semanticamente sus archivos
anteriores. Los 437 archivos preservados siguen iguales. Verificacion final
PASS en `runtime_data/execution_work_20260911/verification.json`.
La medida avanza S3 pero no certifica una busqueda anual/miles de candidatas:
no extrapolar linealmente el uso de memoria de estos tres dias.

## Gold De Hoy Y Estado

Consulta limitada a las08:10:55Madrid: d9037c5c limpio, bot3068 y terminal6312
originales; aparece senal2816. Heartbeat declara cero posiciones y una senal
con una entrada pendiente; no demuestra que nunca se abriera ni que su ciclo
este terminado. Es una lectura parcial, no captura integral ni conciliacion
nativa de esta operacion. La primera lectura de cola fallo por conversion
Int32 del offset; se repitio con Int64, limite65536bytes, sin tocar el bot.
Evidencia en `live_health_observation.json` del mismo directorio.

No hace falta esperar otra senal para inspeccionar la primera; dos senales
y cierres comparables eran el minimo del control preparado, no una garantia
de calibracion estadistica. Las capturas y revisiones automaticas siguen
canceladas. No se espera bloqueando a que el mercado produzca casos raros.

Pendiente: admitir el historico elegido y sus supuestos, ampliar la medida
de capacidad al presupuesto propuesto y revisar el experimento antes de
buscar. El contraste cuantitativo MT5 conserva sus limites. No se ha instalado
un estimador automatico, rellenado cotizaciones ausentes ni cambiado perfiles
congelados. Este trabajo y documentos siguen locales, sin commit/push.

Referencia primaria: [ejecucion y retardos del comprobador MT5](https://www.metatrader5.com/en/terminal/help/algotrading/testing).
La plataforma tambien separa peticion, ejecucion y devolucion de resultado;
su configuracion de retardo no calibra automaticamente nuestro broker.
