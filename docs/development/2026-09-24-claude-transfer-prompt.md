# Prompt de relevo para Claude

Este texto transmite la peticion de Jose al siguiente asistente. Es contexto
para tomar las riendas, no una orden de aceptar el diagnostico anterior.
Fecha del relevo: 24/09/2026. Contrastar siempre el estado actual.

## Lo que quiero conseguir

Quiero que asumas la colaboracion tecnica de este proyecto con criterio propio.
Es un bot que interpreta senales de Telegram y ejecuta operaciones de oro en
MetaTrader 5. Canal 1 es Dubai Investing; canal 2 es Gold Signals. No confundas
los canales, sus historicos ni sus estrategias. El nombre de una rama no
demuestra que esa sea la estrategia activa en produccion.

El objetivo economico es aprovechar las senales con una gestion de entradas,
salidas y riesgo que pueda sostenerse, no maximizar un backtest bonito. Hemos
visto semanas positivas seguidas de perdidas fuertes que borraron los beneficios.
Esto plantea preguntas sobre exposicion, promediado, stops y perdidas de cola.
Los pips publicados por un canal no equivalen al beneficio neto de nuestra cuenta.
No des por garantizada la rentabilidad: demuestra lo que los datos permitan y
senala con claridad cuando una idea no funciona o no es verificable.

No estamos obligados a copiar toda la gestion posterior del trader. Podemos
investigar politicas propias que usen su senal como disparador, o comparar una
politica que siga sus instrucciones. Son experimentos distintos. No inventes
capital, tolerancia a perdidas ni permisos de operacion para completar un estudio.

Mi problema actual es que el proyecto ha crecido y he perdido visibilidad.
No quiero otro mes de ajustes aislados, comprobaciones sin fin o cambios de
modelo a cada paso. Quiero trabajo que cierre problemas generales y explique
que obstaculo elimina y como sabremos que lo ha eliminado.

## Donde estamos de verdad

Antes de buscar mas estrategias necesitamos poder confiar en el simulador.
No basta con que el resultado final se parezca al real: deben contrastarse
tambien decisiones, entradas, volumen, operaciones simultaneas, flotante,
exposicion, recorrido de equity, drawdown y, donde sea verificable, MAE/MFE.
Una trayectoria equivocada puede terminar con un beneficio parecido por casualidad.

Hubo discrepancias entre riesgo real y virtual, y problemas operativos de VM,
reinicios, bloqueo, latencia y continuidad de datos. La operacion 3086 y el
28 de agosto fueron casos de diagnostico, NO una muestra suficiente para
certificar todo ni objetivos que debamos ajustar individualmente.

Hay bastante implementacion, pruebas, auditorias e informes ya hechos. No
reinicies la investigacion de cero, pero tampoco des por buenas conclusiones
solo porque esten documentadas. El goal de Codex esta pausado para el relevo;
esa pausa no significa que el simulador haya quedado certificado.

El punto tecnico reciente incluye escenarios declarados de TP pendiente en
motor escalar, motor rapido Numba y oraculo independiente. En `observed_fill`,
el modelo del broker mantiene pendiente una modificacion hasta su respuesta
observada. La aceptacion conocida por el cliente NO identifica necesariamente
el momento exacto de instalacion del TP en el servidor.

La ultima suite completa documentada tras ese cambio dio 8.046 pruebas
aprobadas y 927 advertencias con Python 3.14.2. Es evidencia interna del codigo
de aquel momento, no certificacion del broker ni de la VM actual. Despues se
anadieron documentacion y herramientas de restauracion; no se ha declarado
una nueva validacion integral del bot por ello.

Una cohorte concreta de TP conserva 46 posiciones: 9 con respuesta aceptada
trazable, 36 sin recibo suficiente y 1 salida no TP. No es todo el historico.
El auditor observado existente empieza en la respuesta aceptada, no desde la
entrada. Un auditor mas completo fue propuesto, pero aun no implementado.
Decide si esa es realmente la mejor siguiente accion o hay una via mas directa
para demostrar la capacidad que falta. No confundas un diagnostico que usa
fills observados con una simulacion que los habria predicho por si sola.

## Tu libertad de criterio

No eres un ejecutor de las conclusiones de Codex. Audita el planteamiento y
propone mejoras, simplificaciones o una ruta alternativa cuando tengan sentido.
Puedes modificar codigo y archivos `.md`, reorganizar documentacion y corregir
supuestos. Conserva la evidencia historica y deja claro que sustituye a que.
No necesitas pedir permiso para cada lectura, prueba o cambio local coherente
con este objetivo. Consulta cuando falte una decision realmente importante,
no para trasladarme decisiones tecnicas rutinarias.

Si discrepas, explica el problema, la evidencia, la alternativa y su criterio
de cierre. Puedes concluir que alguna parte exige datos que no tenemos, pero
delimita cual y que capacidades SI podemos validar mientras tanto. No conviertas
una carencia de una cohorte en un bloqueo universal del proyecto.

El mapa previo tiene cinco fases: datos, motor, realismo, busqueda y candidatas.
El trabajo abierto corresponde a las tres primeras; las otras vienen despues.
Puedes reorganizar esa ruta. Lo importante es demostrar capacidades utiles,
cerrar bloques finitos y mantener visible lo que falta. No conserves tareas
innecesarias solo porque otro asistente las propuso.

## Que significa una simulacion fiable aqui

Separa tres preguntas: que ocurrio realmente, que habria decidido nuestra
politica con la informacion disponible entonces y como modelamos la ejecucion
hipotetica del broker. No uses la respuesta de una para certificar otra.

Respeta la disponibilidad causal de mensajes y ediciones, relojes y zonas
horarias, Bid/Ask, especificaciones del simbolo, lotes, costes y conversion a
moneda de cuenta. Un JSON final puede contener ediciones que aun no existian
cuando se debia decidir. Una vela o un tick incompleto no demuestra todo el
orden intrabar ni un fill negociable.

Compara posiciones, cestas y cuenta cuando haya exposicion concurrente. Usa
definiciones y tiempos compatibles para flotante y drawdown; no sumes maximos
individuales como si hubieran sucedido simultaneamente. Conserva posiciones
abiertas y tramos desconocidos en lugar de inventar cierres o interpolaciones.

Una demora medida es un dato; una demora supuesta es un escenario. No anadas
un minuto arbitrario al simulador para hacer cuadrar una entrada. Tampoco llames
realista a un motor por ejecutarse sin latencia. Hay que separar fallos evitables
del bot/VM de incertidumbre real de comunicaciones y ejecucion del broker.
Migrar de servidor puede evaluarse si las mediciones lo justifican; no es una
solucion demostrada por si sola.

Las muestras deben cubrir ambos canales y condiciones diversas, incluidos
periodos adversos, dias completos y fallos operativos. No elimines los casos
incomodos ni certifiques con dos ejemplos favorables. Mantener denominadores,
motivos de exclusion, hashes y limites de cada resultado. No exigir recibos
live a una politica historica independiente que no los necesita, pero tampoco
usar datos insuficientes para certificar una reconstruccion exacta de lo real.

Reutilizar datos para descubrir fallos los convierte en evidencia retrospectiva.
Reserva validacion no usada, temporalmente coherente, o prospectiva cuando
corresponda. No necesitamos perfeccion absoluta de todo el historico para
avanzar: necesitamos declarar alcance, incertidumbre y criterios de aceptacion
antes de seleccionar lo que parece mejor.

## Donde encontrar el proyecto y la evidencia

Trabaja en esta carpeta, que contiene el estado mas reciente y cambios pendientes:
`C:\Users\josea\OneDrive\Escritorio\proyectos claude\telegram-signal-copier-gold-live`

Comprueba raiz Git, rama y estado. En el relevo la rama era
`feature/gold-555-live-trial`, HEAD
`7415941a410d18288fa4e08da76809f70b125efa`, con muchos cambios sin commit y
archivos nuevos. HEAD solo NO contiene todo el trabajo. Este es un worktree
enlazado; no presupongas que `.git` sea una carpeta independiente.

Lee lo necesario de estas entradas, ampliando por referencias segun el trabajo:

- `AGENTS.md` y `CLAUDE.md`: contexto local y limites de operacion.
- `docs/development/2026-09-24-simulator-goal-handoff.md`: estado tecnico,
  entregas pendientes, informes vigentes y limitaciones precisas.
- `docs/development/2026-09-08-simulation-foundation-readiness.md`: mapa e
  historial del proceso. Contiene bloques antiguos; no son todos instrucciones
  actuales ni hay que leer miles de lineas de una vez.
- `docs/development/research-contracts.md`, `shadow-evidence.md` y
  `runtime-and-replay.md` en esa misma carpeta: contratos segun el area.
- `research/dubai_iterative/` y `research/gold_iterative/`: motores y trabajo
  de ambos canales; los nombres no sustituyen inspeccionar sus contratos.
- `tools/audit_observed_tp_lifecycle.py` y
  `reports/expanded-native-controls-20260924/`: evidencia TP reciente.
- `runtime_data/week_native_ticks_20260923_v1/` y
  `runtime_data/weekly_review_20260919/`: entradas locales de los contrastes.
- `docs/development/2026-09-24-project-restore-point.md`: restauracion.
- `docs/development/2026-09-24-github-migration-checkpoint.md`: alcance y
  estado real del punto de control remoto; no asumir que ya se ha publicado.

Los datos ignorados por Git y las rutas externas no viajan necesariamente en
un clon. Si trabajas en otra maquina, inventaria las entradas necesarias y
su acceso antes de prometer reproducibilidad. Nunca publiques secretos para
resolver una dependencia. No mezcles este proyecto con worktrees historicos
vecinos ni con otros EAs o proyectos del directorio padre.

## Continuidad y vuelta atras

Continua inicialmente en la carpeta actual, con sus mismos documentos. No
hace falta crear otra rama para cambiar de asistente. Una rama nueva por si
sola no guarda cambios sin commit, datos ignorados ni sesiones. Puedes proponer
ramas o commits locales con sentido despues de inspeccionar el estado; no
limpies ni sustituyas el arbol para empezar desde un clon incompleto.

Hay una copia congelada y verificada anterior al relevo en:
`A:\ProjectRestorePoints\telegram-signal-copier\pre-claude-20260924-v3`

Incluye 92.411 archivos del proyecto, unos 9,94 GB, y respaldo Git. Se ensayo
una restauracion independiente verificando bytes y estado Git. No trabajes
dentro de esa copia ni en la carpeta `recovery-check-20260924`, que es el
ensayo. El original de trabajo sigue siendo el directorio de arriba.

Si vuelvo a Codex y tus avances me convencen, seguiremos en esa misma carpeta;
no hay que restaurar nada. Si quiero recuperar el punto anterior, el asistente
debe preservar primero tus avances y restaurar la copia en una carpeta nueva,
sin sobrescribirlos. No necesito ejecutar personalmente un reset. Restaurar
archivos locales no deshace operaciones del broker ni cambia la VM.

Los documentos de instrucciones finales y cualquier checkpoint remoto creado
despues tienen su propia fecha; no se modifica la copia congelada para incluirlos.

## Limites y forma de trabajar

La busqueda masiva de estrategias requiere que antes la revisemos juntos.
No la inicies por interpretar autonomia como permiso para saltar esa decision.
Tampoco publiques en la rama vigilada, cambies politicas live, reinicies el
bot ni coloques operaciones sin autorizacion especifica para esa actuacion.
Una autorizacion de backup no es una autorizacion de despliegue.

El remoto historico `Jose-BF/telegram-signal-copier` es publico segun la consulta
del relevo y el watcher local sigue `origin/main`. Comprueba de nuevo cualquier
destino antes de publicar; no subas `.env`, credenciales, sesiones ni datos
privados en bruto. Una copia remota de codigo no sustituye al respaldo local.

Trabaja y verifica fuera del camino critico del bot; la telemetria debe ser
util, acotada y compatible con disco, memoria y latencia. No ejecutes barridos
pesados en la VM de operacion por comodidad. No afirmes que esta operativa
solo porque los tests locales pasan: aqui no se ha vuelto a certificar su estado.

Usa pruebas proporcionales al riesgo y los contratos de `AGENTS.md`. Reutiliza
evidencia vigente cuando codigo, entradas y entorno pertinentes no han cambiado.
No repitas auditorias caras sin motivo, no relajes tolerancias para obtener
verde y no confundas cantidad de tests con validacion externa.

Hablame en espanol claro. Explica brevemente que estas resolviendo, por que
nos acerca al objetivo y que falta. Evita siglas de fases sin explicar,
porcentajes inventados y promesas de rentabilidad o de plazos no sustentados.
Documenta decisiones importantes, evidencia, comandos relevantes y siguiente
paso para que cualquiera de los dos asistentes retome sin perder contexto.

Tu primera aportacion debe ser una lectura propia, basada en el estado real,
de lo que esta conseguido, de las barreras que quedan y del siguiente bloque
util. Contrasta los supuestos antes de cambiar comportamiento y comunica los
cambios sustanciales de rumbo. Despues avanza con autonomia en el trabajo local
autorizado; no te quedes esperando una confirmacion para cada paso rutinario.
