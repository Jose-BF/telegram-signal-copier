# Calculadora: Entrega Jueves Y Viernes

ACTUALIZACION 11/09 07:40 Madrid: revisiones de hoy NO autorizadas segun
aclaracion del usuario; automatizacion eliminada y ambas capturas14/17
canceladas antes de correr, bot sin tocar. No reprogramar. Entrada actual:
[seguimiento simple](2026-09-11-simple-progress.md). Estados inferiores historicos.

Estado vigente: [activacion operativa del 11/09](../audits/2026-09-11-operational-release.md).
Usuario autorizo publicar el bloque operativo pendiente. Commit d9037c5c
publicado y verificado activo, bot3068; misma cuenta/terminal/estrategias.
3191 pruebas de entrega minima y 60 de auxiliares aprobadas. Investigacion
y sus 5093 pruebas conservadas; no se publica ese arbol. Viernes v3 2b41be5f91d3
sustituye v2 por cambio de identidad, mismas reglas y ventana 15-17 Madrid.
Dos capturas Ready14/17 comprobadas, aun no ejecutadas; v2 retirado sin correr.
La comprobacion natural sigue pendiente de casos; activacion no la certifica.

Antecedente: [revision nocturna, cierre 21:52 Madrid](../audits/2026-09-10-night-review.md).
Final de tarde y suplemento descargados/verificados; 15 posiciones nuevas
conciliadas, 20.94 EUR observados. Prueba independiente incompleta por cero
Gold elegibles y bloqueo separado del registro de productores del comparador.
No se reescribio el protocolo pasado. Nuevo viernes v2 5d882fdfc67b congelado
con ese registro completo, misma estrategia/fuentes probadas/tolerancias.
Capturas REALES programadas para Sep11 14:00 y 17:00 Madrid, comprobadas en
Windows. Aun no ejecutadas. Bot sigue en 892 limpio, sin reinicio ni despliegue.
Las revisiones anteriores inferiores son antecedentes, no tareas pendientes.

## Revision Posterior De Las 15:45

Ver [revision real de recogida](../audits/2026-09-10-collection-review-1515.md):
seis senales, 19 posiciones y conversion causal a EUR reconciliadas; tres
defectos corregidos solo en local y 5093 pruebas completas aprobadas sobre
425 fuentes estables. VM verificada a las 15:36 con `892bc33c8`, sin reinicio.
La revision prevista de las 14 no se ejecuto. El protocolo de jueves conserva
su codigo y bloqueos originales; no confundirlo con los arreglos posteriores.
Este corte actualiza la evidencia, no autoriza publicacion ni busqueda masiva.

## Encargo Vigente

El 10/09 el usuario delega la coordinacion mientras duerme. Autoriza continuar
la preparacion, elegir la secuencia de desarrollo y seguimiento mas util y
aprovechar las operaciones naturales de jueves/viernes cuando aporten evidencia.
Objetivo: calculadora utilizable hoy o, si es posible, el viernes, para revisar
una primera busqueda de estrategias el fin de semana. No hay promesa de
rentabilidad ni autorizacion para operar manualmente o cambiar la cuenta.

La busqueda masiva sigue condicionada a tener la calculadora lista y a la
revision previa del experimento. Presupuesto de candidatas nuevas ahora: cero.
Se permiten controles fijos pequenos, pruebas sinteticas y preparacion offline.
No se espera al bot para desarrollar componentes independientes.

El inventario `../audits/2026-09-10-simulator-delivery-inventory.md` conserva
el corte de partida de 15 requisitos abiertos tras D1, no 15 errores ni fases
seriales. Su actualizacion distingue cierres del dominio inicial, datos aun
no admitidos y decisiones previas a una busqueda.

## Frentes Y Propiedad

| Frente | Propiedad de escritura | Entrega | Estado |
| --- | --- | --- | --- |
| M7, recorrido de reglas propias | Agente: `research/strategy_study.py`, `tools/run_strategy_study.py`, sus pruebas y auditoria | Configuracion explicita, fuente/admisibilidad, tres motores, informe inmutable y resultados/bloqueos completos; sin busqueda | Implementado e integrado; revision independiente cerrada. Controles nuevos bajo el codigo final en curso |
| S1, perfil en buscador | Agente: las dos CLI iterativas, sus pruebas y auditoria | Perfil opt-in preservado en todos los mundos, oracle, cartera e identidad; sin fallback ni busqueda real | Implementado y revisado: 134 pruebas y controles sinteticos de tres dias; cero busqueda real |
| M1 y datos D2-D5 | Coordinador: `research/execution_profile.py`, sus pruebas, provenance y admision/integracion; agente de datos con informe y script aislados | Contrato comun estricto, fuentes/horizontes utilizables y limites explicitos | Helper implementado, 43 pruebas previas y 46 controles nuevos de combinaciones aprobados; admision Gold en revision |
| M2/M4/M5 y revision | Coordinador al integrar; revisor independiente si aporta una comprobacion concreta | Controles de reglas, dinero y cartera con mismo perfil; cerrar defectos demostrados | Despues de cada entrega, reutilizando pruebas estables |
| M3/M6, evidencia natural | Coordinador en las revisiones programadas | Mensajes/precios/ejecucion con corte y version identificados; hechos posteriores separados de inputs | Jueves/viernes, sin provocar operaciones |

Continuacion 10/09, 10:07 UTC: M6 admite ventanas v2 explicitas y conserva v1;
104 pruebas focalizadas. La captura antigua aun tenia fechas Sep9 fijas:
un agente agrega un colector nuevo parametrizado y su selector de fuente
congelada, sin modificar los artefactos archivados. No hay freeze real todavia.
El agente FX entrega 128 pruebas aprobadas y dos regresiones independientes
reparadas: mutacion de cinta en memoria e importacion de M7 anterior al cambio
de fuente. Este parrafo conserva el corte de las 10:07 UTC.

Continuacion 10/09, 10:36 UTC: colector nuevo implementado y revisado, sin
modificar el antiguo. Se cierran las regresiones de revalidacion de fuentes,
reloj/metadatos y tipos de identidad de la captura: 312 pruebas focalizadas.
La bateria completa empieza sobre 423 fuentes y wrappers estables en
`runtime_data/calculator_delivery_20260910/verification_v1/`; aun no se da PASS.

La gramatica raw reutiliza ahora `parser.is_canal2_entry`, sin cambiar el parser
live: reconoce NOW explicitos de zona/again y rechaza negaciones/condicionales.
Siete regresiones fallaban antes y pasan despues; conjunto afectado 164 PASS.
Jul28-30 se declara como universo de mensajes inmediatos distintos, no cestas
agrupadas del catalogo: 585 y 586 tienen identidades originales diferentes y
ningun enlace raw entre ambos. El catalogo une esos dos por una heuristica de
8 s de publicacion; el intervalo de recepcion es 3.338 s. Se conserva esa
discrepancia y se prepara un denominador raw de 28 (10/12/6), sin fusionarlos
por precio, tiempo o resultado. No hay evaluacion de candidatas.

La lectura de las seis fuentes completas requiere 2609968 quotes. El techo
duro de M7 pasa de 2 a 4 millones con pruebas de rechazo previo a la carga;
los controles antiguos mantienen su presupuesto de 2 millones. Solo el nuevo
config de tres dias declara 3 millones; no se truncan fuentes ni se amplian
horizontes, frescura, tolerancias, numero de senales o evaluaciones.

Continuacion 10/09, 10:50 UTC: bateria completa final **5041 PASS**, cero
fallos, errores u omisiones, 287.16 s, 423 fuentes y wrappers estables.
`verification_v2/final.json` y XML verificados. El primer intento v1 se
conserva: 76 fallos por importaciones de tests live en el proceso compartido,
corregidos solo en el fixture de tests; la guarda offline sigue intacta.
Detalle en `../audits/2026-09-10-calculator-integration-regressions.md`.

Controles actuales: Sep8 `gold_fixed_control_v3` (27 evaluaciones, 2 bloqueos
FX conservados), Jul28 `gold_jul28_fixed_v2` (30) y Jul28-30
`gold_three_day_fixed_v1` (84, 28 simuladas, cartera canonica sin bloqueos).
141 evaluaciones mas 141 de reproduccion, cero desacuerdos, archivos identicos.
Veinte campos semanticos de cada control anterior conservan el mismo resultado.
Todos los IDs, razones y 585/586 separados llegan al bundle de busqueda.
`fixed_controls_verification_v2.json` SHA-256
`6ab37003125328c2ee66f8cd02326857c1d3e724602e562c7b206c2f7fcb1ebf`.
El coordinador verifica de nuevo el archivo de tres dias con la CLI publica.

Freeze real de jueves completado antes de la ventana en
`forward_thursday_v1/frozen/protocol.json`, SHA-256
`07636f32757d53024536c812b91a8fe793d1a8e06a26fe4c4dd6fcadc8df9266`:
13:00-15:00 UTC (15:00-17:00 Madrid), sin bloqueos de preparacion.
Solo comprobacion local acotada; paridad MT5, tolerancias aprobadas y busqueda
siguen false/null. El recolector se copia fuera del repositorio live, ligado
por hash; las tareas de captura y sus recibos se verifican por separado.

Cierre operativo 11:22 UTC: precaptura completada sin bloqueos y descargada,
ZIP mas diez archivos verificados. Exposicion nativa a 10:59:52 UTC: cero
posiciones y pendientes, misma cuenta/terminal; no se extrapola al futuro.
El wrapper Windows perdia ExitCode con salida redirigida: reproducido con
salida nativa 7 -> null y corregido reteniendo el handle del proceso. Dos
regresiones ejecutan el bloque real, casos 0/7. No cambian motores o hipotesis.
Nueva suite `verification_v3`: **5043 PASS**, 424 fuentes/wrappers estables,
cero fallos/errores/omisiones, 292.32 s. La v2 aprobada se conserva.

Se congela antes de la misma ventana futura un protocolo nuevo, sin retimar
ni editar v1: `forward_thursday_v2/frozen/protocol.json`, SHA-256
`aedd750e4e58254fc147a7a24ab0c0814ab20e98d7f4eb620184468ace63fafb`.
Tarea final verificada `Codex-Simulator-20260910-1300-final-aedd750e4e58`, Ready,
11:19:10 UTC, proxima ejecucion 17:00 Madrid y expiracion 17:02, sin ejecucion
atrasada. Usa la precaptura ya validada como ancla. La tarea previa terminada
se retiro; no se modifica la tarea watcher del bot. Recibos y advertencias en
`2026-09-10-forward-capture-operations.md`.

Los agentes no publican, no usan VM, no modifican estrategias live ni pisan
archivos de otro frente. El helper comun agrega tambien la CLI Dubai a la
identidad de implementacion: no se reutilizan checkpoints bajo un perfil o
codigo diferente. El productor y comparador del bloque previo quedan archivados
con su identidad; modificar la frontera de ejecucion invalida su uso como
evidencia del codigo nuevo hasta el control aplicable.

El frente de datos solo escribe el script/informe aislado
`runtime_data/calculator_delivery_20260910/gold_data_preflight/` y
`../audits/2026-09-10-gold-own-rule-data-preflight.md`. No evalua estrategias
ni resultados economicos. El autor de M7 revisa S1 de forma independiente;
el coordinador revisa M7 y ejecuta la integracion tras congelar las escrituras.

Consulta historica pequena del coordinador terminada el 10/09 a las 09:13 UTC:
dos peticiones de 20 minutos del 01/07 devuelven 8560 ticks XAUUSD y 1811 EURUSD.
Precio Bid/Ask finito y ordenado; disponibilidad positiva, NO admision de reloj
historico ni continuidad. Archivo bajo `historical_probe/` del directorio de
entrega. Mismo terminal, cero ordenes enviadas, consulta final nativa 0 posiciones
y 0 pendientes en ese instante. Tarea puntual retirada al terminar; bot intacto.

Primer control M7 concreto congelado antes de resultados:
`runtime_data/calculator_delivery_20260910/gold_fixed_control_config.json`.
Gold actual Sep8 completo, una regla fija de una posicion, horizonte 120 s,
reloj/perfil/costes hipoteticos explicitos. No es candidata seleccionada ni
calibracion. Conservar todos los bloqueos sin ampliar limites tras verlos.

Control Sep8 repetido bajo el codigo integrado en `gold_fixed_control_v2`:
117 identidades, 11 triggers, 9 simuladas y 2 bloqueadas FX; 27 evaluaciones.
Filas, estados, cartera y contadores identicos a v1, configuracion sin editar.
Verificacion actual del archivo aprobada. No se sustituyen los dos bloqueos.

Primer dia historico seleccionado solo por calidad de inputs: 28/07, diez NOW
con snapshot original e identidad canonica, 219 receipts / 44 mensajes en el dia.
Las 9288 filas del origen quedan contabilizadas, incluidas 9069 fuera del dia.
Fuentes y regla unica congeladas en `gold_jul28_input_v1` ANTES de resultados;
809927 quotes, horizonte 60 min, edad FX 5 s e intervalo historico 60 s explicito.
Se usa Bid/Ask previo, no precio futuro. El modo estricto Sep8 no se modifica.

Resultado `gold_jul28_fixed_v1`: diez simuladas, 30 evaluaciones de tres motores
sin desacuerdos; cartera de cinta canonica sin bloqueos. Los otros 34 mensajes
sin trigger siguen en el informe. Bridge al buscador: diez paths de diez
elegibles, cobertura completa, dinero y seleccion NO certificados. Verificacion
actual aprobada. Es diagnostico retrospectivo de una regla fija, no busqueda,
validacion de rentabilidad ni tres dias suficientes para los folds del buscador.

Extraccion nativa de costes terminada 09:46 UTC, solo lectura: 2508 deals de
1254 posiciones, comision y fee cero en todas; 12 deals con swap distinto de cero.
Auditoria de emparejamiento/moneda en curso, no se infiere cero coste overnight.
Mismo terminal y cuenta comprobados, ninguna orden; tarea de extraccion retirada.

## Decisiones De Alcance

- Una primera entrega debe ejecutar reglas propias con un perfil declarado,
  no reproducir cada intervencion ambigua de un proveedor ni cada milisegundo
  del bot. Esas discrepancias no desaparecen: se separan de este dominio.
- Primer perfil verificable: mercado y SL/TP integrados, precision y volumen
  admitidos por los contratos existentes. Opciones incompatibles se rechazan
  o bloquean expresamente; no se desactiva el modelo para hacerlas pasar.
- Capital de inversion sigue abierto. Un control de escala puede declarar un
  volumen de referencia y costes hipoteticos, pero no supone capital ilimitado,
  margen suficiente o dinero del usuario. No selecciona por subir lotes.
- Intradia con costes cero solo como hipotesis diagnosticada salvo evidencia
  monetaria suficiente por periodo. Overnight, comisiones no soportadas y
  huecos de cotizaciones no se inventan ni se convierten en cero automaticamente.
- Los cuatro JSON antiguos siguen siendo cohortes separadas del Gold actual.
  Solo 36 comandos NOW en 2026 conservan snapshot sin edicion; los 894 bajo
  reloj de revision representan otro experimento. No adelantar ese texto.
- El inventario Gold actual de julio-septiembre ya tiene catalogo causal y
  63 fuentes de ticks verificadas previamente, con huecos conocidos. Se
  evalua su utilidad sin mezclarlo con el chat Gold antiguo ni repetir todo
  el inventario. Ninguna de esas fechas se presume OOS intacta.

## Seguimiento Y Seguridad

Conservar las revisiones de las 14:00 y 19:00 Europe/Madrid del jueves 10/09
y extenderlas al viernes 11/09 a las mismas horas. Entre ellas se avanza offline.
Una ventana de contraste prospectivo se declara antes de consultar sus
resultados, con modelo/configuracion e identidad: no renombrar como prospectivo
un corte que ya se utilizo para reparar. Si no hay suficientes operaciones,
informar del limite, sin abrir ordenes de prueba ni esperar indefinidamente.

El jueves se mantiene la autorizacion anterior para publicar reparaciones
operativas acotadas y verificadas si realmente hacen falta. No extenderla a
publicar toda la investigacion ni a nuevas estrategias. Cualquier activacion
requiere exposicion nativa reciente, entradas/senales/gestion y guardas de
actualizacion; exposicion abierta o desconocida impide reiniciar. El viernes
no se infiere permiso nuevo de despliegue por una comprobacion de seguimiento.
Conservar el bot sano y sus registros; la recuperacion de la manana en 892 no
se repite. Usar el lanzador con logs y la prioridad normal ya verificados.

Despues de la ultima revision del viernes, cerrar esta programacion, sin
archivar la tarea y sin iniciar busqueda masiva automaticamente. Si el usuario
retoma antes, prevalece su instruccion mas reciente.

## Cierre Para Revisar La Busqueda

- [x] M7 y S1 implementados, integrados y revisados.
- [x] Dominio inicial y reglas admitidas fijados con interacciones comprobadas.
- [x] Cohorte/horizonte y datos admitidos, bloqueados y ausentes contabilizados para el control inicial de tres dias; no admision de todo el historico.
- [x] Dinero, cartera, incertidumbre y limites informados por separado; no certificacion monetaria global.
- [x] Control reproducible del conjunto y suite proporcional verificados.
- [x] Experimento de fin de semana preparado para revision, sin ejecutarlo: `2026-09-10-first-weekend-study.md`.

Los seis puntos describen la entrega tecnica/preparacion local, no el cierre
de M3/M6 ni la autorizacion S4. Continuan el contraste de ejecucion natural,
la admision monetaria aplicable y la eleccion de periodo/reserva/presupuesto
antes de una busqueda real. Tres dias son un control, no la muestra final.

El plazo orienta prioridades, no rebaja las comprobaciones. Si queda un
impedimento material, se entrega con causa y siguiente accion concreta,
sin declarar lista una calculadora que aun no puede ejecutar el uso ofrecido.
