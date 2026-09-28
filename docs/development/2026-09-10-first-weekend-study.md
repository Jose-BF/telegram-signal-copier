# Primera Sesion De Busqueda: Propuesta Para Revision

## Estado

Preparada el 10/09/2026 bajo la delegacion del usuario. No es autorizacion de
ejecucion: presupuesto vigente de candidatas nuevas cero. Revisar juntos el
experimento al finalizar las comprobaciones de jueves/viernes. No promete
rentabilidad ni publica una estrategia en el bot.

## Pregunta Y Dominio

Comparar entradas y salidas propias, activadas por mensajes inmediatos de
Gold actual, con volumen fijo de referencia 0.01. No imitar la gestion del
proveedor. Dominio tecnico y exclusiones en
`../audits/2026-09-10-own-rule-integration.md`.

Primera familia propuesta: una posicion, cinco modos de entrada ya integrados,
SL fijo y TP por distancia, sin BE, parciales, overnight ni entradas adicionales.
Conservar una regla fija como control; fijar el espacio numerico antes del
primer resultado de busqueda. Los retardos se declaran como escenarios, no
como latencia historica calibrada. No ampliar lotaje para mejorar un ranking.

## Datos Y Cronologia

Gold actual es un chat distinto del export Gold antiguo. Los cuatro exports
mantienen sus escenarios de publicacion/revision y bloqueos; no se mezclan
para rellenar fechas. Jul28-30 es un control tecnico retrospectivo, no una
reserva OOS: 1094 receipts, 239 mensajes y un universo raw previsto de 28
entradas, frente a 27 cestas del catalogo que aplica una heuristica de duplicado.
Preservar todas las identidades y explicar la convencion, no elegirla por P/L.

El periodo de busqueda final debe superar este control pequeno. Antes de
ejecutar, congelar dias completos, uso previo, folds y una reserva realmente
intacta. Si no existe reserva historica no inspeccionada, llamar a la evaluacion
retrospectiva y reservar validacion futura; no fabricar OOS a partir de un
challenge que vuelve a alimentar ajustes. No certificar anualidad con tres dias.

Cobertura por horizonte antes del resultado, Bid/Ask y FX causales, todos los
bloqueos visibles. El intervalo FX historico de 60 s es una hipotesis explicita;
el precio usado es el previo. No sustituye la admision monetaria.

## Dinero Y Riesgo

Registrar resultados, equity abierta, drawdown, perdidas de cola, duracion y
exposicion simultanea bajo el mismo perfil. Capital queda abierto para el
usuario: null no significa ilimitado. El modelo no prueba margen ni stop-out.
Los resultados monetarios hipoteticos no se presentan como dinero realizable.

La evidencia actual de comision/fee cero en la captura nativa es positiva,
pero no basta para certificar costes y cuenta de todo el historico. Los casos
con swap, conversion o identidad incompletos siguen declarados. No seleccionar
ni promover una politica mientras los gates aplicables de dinero/OOS sigan abiertos.

## Presupuesto Y Paradas

Propuesta a aprobar: primera tanda diagnostica de como maximo 1000 genomas,
una hora de pared y una sola configuracion de fuente/perfil/semilla, con archivo
por fragmentos y reanudacion de identidad exacta. No es una estimacion de
velocidad: medir un lote acotado tras autorizarlo y reducir el presupuesto si
no cabe. M7 conserva sus propios limites de control, no se amplian implicitamente.

Parar ante desacuerdo de motores, mutacion de fuentes, presupuesto agotado,
fallo de checkpoint, cobertura insuficiente o gate de evidencia incumplido.
Registrar intentos y sesgo de multiples comparaciones. No volver a mutar desde
la reserva y seguir llamandola validacion intacta. Sin estrategia ganadora
automatica: selection null y bot sin cambios.

## Comprobaciones Previas

- Codigo estable y bateria completa verificada, con fuentes y entorno ligados.
- Control fijo reproducible y puente al buscador sin perdida de filas o perfil.
- Ventana natural de jueves/viernes evaluada con el alcance que sus datos permitan.
- Periodo final, folds, hipotesis, metricas y coste computacional aprobados.
- Decision conjunta sobre busqueda diagnostica o de seleccion; permisos separados.

Los resultados del control de tres dias y la captura pendiente se enlazan en
el plan `2026-09-10-calculator-autonomous-delivery.md`. La sesion solo se abre
despues de esa revision, no automaticamente por ser fin de semana.
