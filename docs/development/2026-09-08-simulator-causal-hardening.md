# Robustez Causal Del Simulador

## Alcance

Continuacion autorizada de la validacion: corregir defectos que puedan alterar
comparaciones entre candidatas, sin buscar candidatas ni cambiar el bot.
Trabajo local/offline. Sin publicacion, reinicios, consultas MT5 ni ordenes.
Mantener todos los cambios y fuentes previos. Capital de inversion abierto.

El control por ticket de la fase anterior permanece activo. La ausencia de un
contrato completo de decisiones/deals de salida no se resuelve con una bandera.

## Hitos

- [x] Verificar rama, estado y contratos; iniciar revision independiente acotada
  de las barreras por ticket y del informe de conjunto, sin ediciones delegadas.
- [x] Reproducir el posible desplazamiento de entradas/cierres por conversion
  flotante de UTC a nanosegundos en los motores comunes.
- [x] Corregir las fronteras causales confirmadas con pruebas de BUY/SELL,
  tick exacto, informacion posterior y latencia, manteniendo oracle independiente.
- [x] Resolver hallazgos verificables de la revision de barreras y revisar
  el efecto sobre procedencia de resultados historicos.
- [x] Verificar motores, controles y suite completa; documentar limites y
  evidencia pendiente para la paridad integral.

## Invariantes

La disponibilidad de una instruccion no puede moverse por redondeo de reloj.
El primer tick elegible es el de hora mayor o igual a la disponibilidad exacta
mas la demora declarada. Los mensajes posteriores nunca cambian un resultado
anterior. Un informe viejo conserva su codigo/contrato; una reparacion no lo
convierte en evidencia nueva ni en OOS.

Presupuesto: regresiones y controles fijos, cero estrategias nuevas. Los
resultados del piloto anterior solo se reutilizan si sus dependencias siguen
iguales; cualquier repeticion con motores modificados tendra identidad nueva,
parametros identicos y etiqueta de reparacion retrospectiva.

Repeticion acotada autorizada en esta fase: los 24 escenarios ya fijados del
piloto mas sus ocho controles semanticos, comparando tambien el motor rapido
con los otros dos. No hay nueva candidata, cambio de lotes ni busqueda. Se
conserva el protocolo anterior y se crea un protocolo hijo que identifica el
cambio de codigo de conversion temporal, sin ampliar costes ni tolerancias.

## Cierre De Esta Fase

Se corrigieron la frontera temporal, la vinculacion de los hechos por ticket
al ledger, su comprobacion en el informe de conjunto y la identidad necesaria
para reanudar busquedas. Contratos: certificado de gestion v3 y checkpoint v4.
La integracion focal acredita 160 pruebas y la suite completa 3.256, sin fallos,
errores ni omisiones. Las 354 fuentes registradas no cambiaron durante la suite.
El piloto conserva sus 24 resultados y sus 32 controles entre motores; sigue
siendo reparacion retrospectiva con sus limites originales, no OOS nuevo.

Evidencia y limites pendientes en
`../audits/2026-09-08-simulator-causal-hardening.md`. Este cierre corresponde
al endurecimiento local realizado, no a paridad integral con MT5 ni a seleccion
de candidatas. No se publico ni se modifico el bot en produccion.
