# Plan De Campana De Busqueda Por Familias

Estado actualizado 13/09: plan aprobado para empezar SOLO por canal 1, con
busqueda iterativa y ampliacion razonada del presupuesto a criterio del agente.
Ver [ejecucion de canal 1](2026-09-13-canal1-recursive-campaign.md), que prevalece
sobre el presupuesto inicial y la secuencia de ambos canales descritos debajo.
No autoriza publicacion, reinicios ni ordenes. El cuerpo siguiente conserva
la propuesta del 12/09 como antecedente del acuerdo.

## Objetivo Y Punto De Partida

Construir sobre el buscador existente una campana reproducible para Dubai y
Gold: explorar formas distintas de entrada, refinar con limites y entregar
finalistas defendibles o un diagnostico que oriente la siguiente hipotesis.
No garantizar una ganadora por canal ni una fecha de aprobacion en real.

La primera ronda de 24 reglas solo probo entrada inmediata y variaciones de
SL/TP/duracion. Su resultado negativo no evalua las otras familias de entrada.
Los motores, presupuestos, operadores, checkpoints y contrastes cronologicos
ya existen. Falta conectar una campana completa, con denominadores y barreras
de evidencia coherentes, sin introducir semillas antiguas fuera del alcance.

Antecedente: [resultado de la primera ronda](../audits/2026-09-12-small-search-results.md).

## Alcance Inicial

Cuatro familias alternativas a la entrada inmediata de referencia:

- Espera fija: entrar despues de un tiempo declarado, dentro de la caducidad.
- Retroceso: esperar un precio mas favorable respecto al disparador.
- Confirmacion: entrar tras un avance declarado en la direccion de la senal.
- Retroceso y giro: exigir primero retroceso y despues recuperacion del precio.

Se varia dentro de cada familia la distancia/espera de entrada y los SL, TP
y cierres temporales propios. Los rangos se fijan antes de simular resultados.
Entrar inmediatamente y no operar son controles, no conclusiones rentables.
Una posicion por senal y 0.01 lotes como referencia, no como capital elegido.
Sin refuerzos, martingala, cambios de direccion implicitos ni gestion del
proveedor. Nuevos filtros o gestion dinamica requieren su propia admision;
no se incorporan automaticamente por estar enumerados en la gramatica.

## Hitos Y Criterios De Cierre

1. Adaptar el coordinador y comprobar las familias. Reutilizar motores y
   busqueda existentes; coordinar canal, familia, presupuesto y registro de
   todos los intentos. Verificar en casos conocidos direccion BUY/SELL,
   confirmacion causal, caducidad, ausencia de entrada, esperas y datos
   incompletos, en tres motores y cliente serial dentro de su alcance.
   Fin: piloto reproducible, bloqueo visible por familia y reanudacion que
   rechaza cambios de codigo/datos/supuestos. No buscar resultados rentables
   mientras se decide si una capacidad calcula correctamente.

2. Ampliar y admitir el historico. Usar los inventarios existentes para
   comprobar mas fechas de ambos chats, mensajes/revisiones, relojes, Bid/Ask,
   conversion y costes. Investigar los desfases de reloj y huecos conocidos
   sin desplazar arbitrariamente mensajes ni inventar cotizaciones. Fijar la
   cobertura por horizonte antes de ver resultados. Separar dias completos
   de desarrollo, contraste retrospectivo y reserva realmente intacta; una
   operacion no puede cruzar inadvertidamente particiones de seleccion.
   Fin: tabla de datos utilizables por familia y motivos de bloqueo. Datos
   faltantes limitan la evaluacion afectada, no el desarrollo independiente
   del coordinador. Con historico reutilizado solo cabe descubrimiento y
   contraste retrospectivos; sin reserva intacta no se aprueba una candidata.

3. Ejecutar la campana acotada. Hasta 2000 candidatas unicas por canal, 4000
   en total: hasta 1600 de exploracion y 400 de refinamiento por canal. Los
   controles, el piloto y cualquier vecino nuevo cuentan dentro de esos
   limites. Reparto por familias, rangos, semilla, reglas de refinamiento y
   minimos de dias/operaciones congelados antes de evaluar. Piloto pequeno
   para comprobar coste y memoria; tope propuesto de cuatro horas de calculo
   para la campana, no una promesa de tiempo hasta aprobacion. Declarar antes
   del lanzamiento el numero maximo de evaluaciones senal/regla/escenario y
   el volumen de datos. Agotar un limite produce cierre o checkpoint, no
   ampliacion automatica. Periodos de validacion no alimentan mutaciones.

4. Refinar y contrastar solo lo que tenga sentido. Conservar hasta diez
   prefinalistas por canal, sin obligacion de llenar el cupo. No ordenar solo
   por beneficio: comprobar costes, perdida por senal, caida de cuenta con
   posiciones abiertas, simultaneidad, concentracion y recuperacion. Exigir
   evidencia en varios bloques de desarrollo y estabilidad al mover un poco
   parametros; conservar zonas estables, no un unico punto favorable. Todos
   los ensayos, incluidos fallidos y previos, quedan en el registro de la
   investigacion. Los umbrales de muestra y sensibilidad se fijan antes de
   los resultados, no se aceptan valores por defecto que permitan declarar
   validacion suficiente con una sola operacion. Verificar prefinalistas con
   motores independientes y varios mundos de ejecucion/costes predeclarados.
   Fin: hasta dos candidatas por canal para validacion final, o motivos
   concretos por los que cada familia no merece pasar.

5. Validacion final y cartera. Congelar parametros y evaluarlos sin ajustes
   sobre datos realmente no usados. Si no existen, preparar observacion
   posterior con reglas fijas; no rebautizar dias conocidos como nuevos. Para
   dos candidatas, reconstruir el riesgo conjunto desde una misma cinta,
   atendiendo a cuenta compartida o separada. Completar en la cartera existente
   la medicion de perdida flotante conjunta y recuperacion intradia: el primer
   informe solo disponia de drawdown de cuenta y recuperacion realizada diaria.
   Es una ampliacion de metricas verificable, no un motor monetario alternativo.
   La aprobacion requiere ademas dinero/capital/margen y ejecucion aplicables
   comprobados; el cliente serial por cesta no acredita contencion global.
   Una prueba demo que envie ordenes requiere autorizacion propia.

## Como Decide El Buscador

Datos admitidos -> reglas validas -> exploracion rapida -> filtro de evidencia
y riesgo -> refinamiento solo en desarrollo -> contraste independiente ->
reserva intacta -> riesgo conjunto -> propuesta de prueba, nunca despliegue.

Una regla incompleta se conserva como bloqueada, no como ganadora sobre los
casos cargados. Una ausencia de entrada decidida por la estrategia se distingue
de un fallo de datos; la participacion y el numero de operaciones se publican.
Las reglas con menos operaciones no ganan automaticamente por aparentar menor
riesgo. Mientras las barreras monetarias sigan abiertas, las comparaciones
son diagnosticas, sin seleccion certificada ni promocion.

Si no hay prefinalistas, se cierra la campana explicando si el problema esta
en la entrada, costes, riesgo, inestabilidad o datos. Otra hipotesis requiere
un protocolo nuevo y revision conjunta. No se repite el mismo ajuste hasta
obtener un positivo. Multiples pruebas pueden generar falsos descubrimientos;
guardar un unico ganador no permite valorar ese riesgo.

Referencia metodologica: Bailey, Borwein, Lopez de Prado y Zhu,
[The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
Los recuentos y contrastes no se presentaran como una probabilidad de exito
certificada; cualquier medida estadistica exige muestra y supuestos adecuados.

## Implementacion Y Verificacion

- `research/dubai_iterative/search.py`: bucles, presupuestos y checkpoints.
- `research/dubai_iterative/__main__.py`: planes de reglas propias y admision
  de perfil; coordinar una familia estructural por plan, sin semillas heredadas
  incompatibles. El limite actual de grilla es 4096 por plan.
- `research/strategy_study_dataset.py`: fuentes, cobertura y denominadores.
- `research/dubai_iterative/evolution.py`: diagnostico/refinamiento acotado.
- `research/dubai_iterative/portfolio.py`: cartera y metricas pendientes.
- `research/gold_iterative/`: conservar identidad y cronologia de Gold; no
  activar automaticamente los anclajes 555/c490 de su coordinador historico.

Pruebas focales por cambio, integracion de presupuesto/pausa/reanudacion y
suite completa para modificaciones compartidas de ejecucion/dinero/cartera.
Control negativo de cobertura incompleta y resultado conocido positivo/negativo.
Repeticion exacta del piloto; nueva identidad tras cualquier cambio relevante.
Los checkpoints antiguos no se reutilizan bajo otra implementacion.

## Decision Propuesta Ahora

Aprobar la implementacion del coordinador y esta campana offline acotada,
con lanzamiento solo cuando sus datos y capacidades pasen los controles
aplicables. No pedir permiso para cada combinacion dentro del alcance y
presupuesto aprobados; consultar cambios materiales de alcance o limites.

Primera entrega: coordinador verificado, familias habilitadas, cobertura,
periodos, presupuesto exacto y estado de cada barrera. Segunda: exploracion y
refinamiento con comparacion por familia. Tercera: finalistas contrastadas o
diagnostico y propuesta concreta siguiente. Cada cierre explicara que sigue.

Pendiente del usuario: capital de referencia real, cuenta compartida/separada
y perdida conjunta tolerable. Se pueden aclarar mientras se desarrolla el
coordinador; no es necesario inventarlos ni detener por ello toda preparacion.
No hay cambios de motor, ejecuciones nuevas, commit, publicacion ni acceso a
la VM en este bloque de planificacion. La version activa no se ha revalidado.
