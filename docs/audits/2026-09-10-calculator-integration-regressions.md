# Regresiones De Integracion De La Calculadora

## Alcance

Cambios locales del 10/09, sin editar parser, listener, executor, configuracion
live, cuenta o estrategia. Pruebas sobre casos sinteticos y entradas congeladas,
no una busqueda de rentabilidad. Los archivos antiguos no se reescriben.

## Entrada Causal

El compilador raw tenia una expresion regular independiente y mas limitada que
`parser.is_canal2_entry`. Omitia `Sell zone Now`, `Sell zone again now` y otras
variantes admitidas, y aceptaba negaciones/condicionales que no son una orden.
Siete regresiones fallaron antes del arreglo; 164 pruebas afectadas pasan despues.
Se reutiliza el predicado existente, sin cambiar el parser del bot.

Las identidades de mensajes distintos no se fusionan por proximidad temporal.
Las redeliveries de la misma revision si se deduplican. La convencion del
control Jul28-30 se congela antes de sus resultados: 28 triggers raw distintos,
frente a 27 cestas del catalogo que aplica una heuristica de duplicado.
El informe original del desacuerdo 26/27 y todas las fuentes se conservan.

## Fuentes De La Ventana Futura

La revision independiente reprodujo aceptacion de pruebas de reloj/metadatos
antiguas o desacopladas de la fuente conservada. La lectura del dataset ahora
revalida fecha, semantica, bytes, productor y proyeccion exacta. En la ruta
nativa se vuelve a obtener la proyeccion mediante el validador de la captura;
no se compara un esquema nativo con otro proyectado como si fueran iguales.
Se mantiene el limite existente de 120 s, sin ajustarlo al resultado.

Otra revision demostro que identidades invalidas coincidentes, incluido null
frente a campos ausentes, podian aceptarse. El productor exige cadenas
hexadecimales de longitud exacta: commit 40 y hashes 64, y objetos de identidad.
Los 15 repros publicos se conservan y pasan. Total afectado: 312 PASS.
Revision independiente final: 43 pruebas y 32 verificaciones adicionales,
fuentes estables; sin hallazgos reproducibles abiertos en ese alcance.

## Aislamiento De La Suite

Primer conjunto completo `verification_v1`: 5030 pruebas, 4954 aprobadas y 76
fallos, cero errores/omisiones. Las 423 fuentes y wrappers se mantuvieron
estables. No es evidencia de bateria aprobada y no autoriza el freeze.

Todos los fallos nuevos estaban en la guarda de importacion offline: la
coleccion global de pytest importa tambien tests del bot en el mismo proceso.
No se modifica la guarda de produccion. El fixture compartido `case` elimina
temporalmente esas importaciones previas mediante monkeypatch y las restaura
al terminar, para representar el interprete limpio de la CLI offline.

Se agregan siete pruebas que introducen cada modulo prohibido dentro del
proceso de estudio y exigen rechazo sin archivo de salida. La prueba del
bundle cubre los cinco modulos live y mantiene permitidos los buscadores
offline. Sigue existiendo la prueba de importacion real en un subproceso nuevo.

Verificacion conjunta con las pruebas del clasificador: 340 PASS. Segundo
conjunto completo `verification_v2`: **5041 PASS**, cero fallos, errores u
omisiones, 287.16 s; finalizado 10:49:54 UTC, 423 fuentes y wrappers estables.
XML SHA-256 `3564fe0040cb36264d2d10bd98af9233c816e166e9f89f2a6f28cbc15e4094ff`.
El freeze posterior verifica la misma implementacion, fuentes y XML antes de
admitir el control futuro. La primera bateria fallida permanece archivada.

## Programacion De Capturas

El script operativo solo registra tareas propias con nombres ligados al
protocolo. Precaptura por demanda una sola vez; final en hora UTC convertida
al huso de la VM, sin ejecucion atrasada y con expiracion dos minutos despues.
Terminal existente, usuario interactivo y procesos ocultos. Los logs se
conservan; el limite externo no amplia la frescura exigida por el consumidor.

Revision previa al uso: se agrega rollback sobre la tarea exacta recien creada
si falla el readback o el recibo; no afecta otras tareas. Se exigen rutas
absolutas. Sintaxis PowerShell verificada y construccion CIM probada en memoria.
La hora y estado registrados se verifican otra vez en la VM despues de instalar.

## Cierre Del Lanzador Windows

El primer recibo de precaptura tenia ExitCode null. Captura y ZIP completos,
hashes y terminal correctos, pero ese valor no permite fiarse de una tarea 0.
Reproduccion con Windows PowerShell y salida redirigida: un hijo que termina
con 7 devuelve null sin retener su handle; al retenerlo antes de esperar devuelve
7. El wrapper conserva ahora ese handle y rechaza un ExitCode no disponible.

`tests/test_collect_simulator_wrapper.py` extrae con el parser AST el bloque
real Start-Process/espera del wrapper y ejecuta hijos Python que terminan 0/7,
sin iniciar MT5 ni cargar el bot. Ambas pruebas pasan. Bateria final v3:
**5043 PASS**, cero fallos/errores/omisiones, 424 fuentes y wrappers estables,
292.32 s, terminada 11:17:49 UTC. XML SHA-256
`5127b346646d3d3ab007ce15f41b3ba1892df39b36191a42734709acefdbc64a`.
La implementacion de los calculos conserva 12e072...: los controles fijos no
se invalidan por modificar solo el wrapper/test operativo.

Se crea protocolo futuro nuevo `forward_thursday_v2`, sin cambiar la ventana
ni modificar v1. Copia original del wrapper preservada junto con la precaptura.
La tarea de cierre queda registrada y verificada para 17:00 Madrid, recibo
retenido. La captura de cierre no se ha ejecutado todavia.
