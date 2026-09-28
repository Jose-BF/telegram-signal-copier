# Continuacion de pendientes operativos

Actualizacion posterior: el usuario autorizo activar el ajuste probado. Su
preflight, commit/push `08551e9a8` y verificacion en la VM se documentan en
`2026-09-08-telemetry-activation-and-study-preparation.md`. El registro siguiente
conserva el estado de la fase anterior, cuando el cambio aun era local.

## Alcance

Continuacion pedida por el usuario: terminar pendientes antes de centrarse en
candidatas de estrategias. No se ejecutan simulaciones ni se cambian parametros,
lotes, cuenta o politica del bot. Ningun cambio de codigo se publica en esta fase.

## Estado operativo comprobado

La VM sigue en `9765758ed84bcdcb5e2ca690ec954a2dce154bc6`, checkout limpio,
bot 6796, supervisor 6044 y terminal 8968. La publicacion automatica comprobada
a las 00:12:19 UTC del 8 de septiembre termina correctamente, con cero archivos
pendientes, en el commit de telemetria `a36c0dacef1728df4cffb3244b13944ceeed0e22`.
No hay pausa ni bloqueo de publicacion retenido en la inspeccion inicial.

A las 00:16:00 UTC se comprueba el prefijo original del diario por SHA256 y se
recuperan 26 eventos completos nuevos respecto de la copia anterior. El analizador
habitual encuentra cero senales recibidas/cerradas, ordenes, desconexiones y
anomalias. Son heartbeats, comprobaciones de cobertura de Telegram y la recuperacion
de telemetria ya conocida. El heartbeat v3 tiene 13.3 segundos, estado plano y
cero posiciones del bot, senales abiertas y entradas pendientes.

Recomprobacion a las 00:32:06 UTC: mismos procesos y codigo, heartbeat de dos
segundos, sin pausa ni bloqueos retenidos. Otra publicacion automatica termino
a las 00:27:22 UTC con cuatro archivos y cero pendientes en
`cd661160e790ea6771fa8800d1a30c95b0a2b1ef`. Git de la VM es 2.54.0.windows.1;
el usado en las pruebas locales es 2.53.0.windows.2.

La captura final de las 00:33:26 UTC valida de nuevo el prefijo e incorpora
siete eventos: seis comprobaciones de cobertura y un heartbeat, sin senales
nuevas ni errores JSON. Heartbeat plano de 6.8 s, sin pausa ni bloqueos. La
publicacion de las 00:32:21 UTC vuelve a terminar sin pendientes, en
`99e5213ea0862357eb19469f74656b3c31b32283`. Prueba: `vm_final.json`.

La ausencia de senales nuevas NO certifica el comportamiento reparado. No se
simulan senales ni se abren operaciones para forzar esa comprobacion.

## Historicos recuperados

La busqueda se amplio solo a cuatro carpetas conocidas del mismo proyecto en la VM:
`runtime_data/ticks_cache`, `runtime_data/money_ticks_cache`, `data/ticks_cache`
y `data/money_ticks_cache`. No se examinaron otros proyectos.

Se inventariaron 140 Parquet con sus metadatos y hashes. De ellos, 85 declaran
verificacion independiente de origen; 55 son copias antiguas sin esa prueba.
El texto v3 de una etiqueta no basta para aceptar un archivo.

Se recuperaron 58 archivos diarios que no tenian una copia local consistente
para el mismo activo y fecha: 42 de la carpeta de precios y 16 de conversion.
El traslado contiene tambien sus 58 metadatos, en un archivo de 281202005 bytes.
Todos los contenidos coinciden por SHA256 con la fuente inventariada. Ninguna
cache previa, parcial ni copia original de la VM fue sobrescrita.

| Resultado | Archivos diarios | Alcance |
| --- | ---: | --- |
| Contrato y contenido consistentes | 17 XAUUSD + 16 EURUSD | Fechas existentes del 6 al 24 de julio; no significa cobertura continua ni actividad todos los dias |
| Solo diagnostico | 25 | Del 5 de junio al 3 de julio; activo sugerido por carpeta, no acreditado por metadatos |

Para los 33 consistentes se ejecuta el cargador vigente de contratos, se comprueba
el numero de filas del Parquet y se recalcula la huella de la secuencia ordenada
de hora/Bid/Ask contra las dos pruebas de captura almacenadas. Coinciden todas.
Esto no es una reconciliacion nueva de anclas contra operaciones MT5, una consulta
nueva al broker ni una certificacion de rentabilidad o de un replay concreto.
Las capturas vacias se conservan; no cuentan como sesiones negociadas.

Combinando el inventario local anterior y esta recuperacion, sin contar copias
repetidas, hay 46 fechas de XAUUSD y 45 de EURUSD con metadatos consistentes entre
el 6 de julio y el 2 de septiembre. Las 58 fechas/activo anteriores mantienen el
alcance de comprobacion del inventario original; la nueva comprobacion adicional
de secuencia ordenada se hizo sobre los 33 archivos recuperados, no sobre todos.

Los 25 diagnosticos carecen de simbolo declarado, cobertura y verificacion
independiente; el cargador vigente los rechaza. El 9 de junio tambien consta sin
validacion semantica/anclas correcta. Se conservan sin reetiquetar ni relajar
los controles. Las alternativas no seleccionadas siguen en la VM y figuran en
el manifiesto; no se destruyo ni mezclo su identidad.

La exportacion de Telegram sigue teniendo las mismas cinco carpetas conocidas,
sin una exportacion nueva. Se ha pedido al usuario JSON con fotos/archivos de
Dubai y Gold para completar mensajes y medios. En particular siguen pendientes
mayo de Dubai en las copias auditadas y una exportacion del Gold actual; las tres
generaciones de Gold deben conservar identificadores separados. Una exportacion
nueva tampoco recupera automaticamente versiones editadas o borradas antiguas.

## Publicacion robusta

La recuperacion anterior uso lotes en un auxiliar puntual. El ajuste local del
publicador limita cada ciclo a 100 archivos, con hasta 50 parejas completas de
payload y manifiesto; los ciclos posteriores continuan con la cola restante.
Tambien limita cada comando de preparacion a 16000 caracteres para Windows y
desactiva los avisos `safecrlf` solo en los comandos que normalizan contenido,
no en la configuracion Git.
Solo elimina de la cola archivos cuya publicacion termino correctamente.

Las regresiones usan Git real con remotos locales aislados, sin red externa.
El caso original falla por timeout preparando 242 archivos de golpe. Con el
ajuste, la misma cola se drena en 100 + 100 + 42, conservando parejas completas
y reconstruccion exacta. Otra prueba reproduce un fallo con el indice ya
preparado: no hay commit, push ni borrado, y el reintento continua sin recopia.
Tambien se comprueba el limite de longitud del comando.

La revision independiente detecto dos regresiones en la primera version local.
Se reprodujeron antes de corregirlas con cuatro pruebas Git reales:

- Una interrupcion retirando archivos ya confirmados podia dejar solo payload
  o manifiesto y bloquear el reintento. Ahora se conserva la pareja en la
  seleccion y solo se recupera el miembro ausente si la copia del checkout
  coincide con su objeto registrado en un commit de Git. Una copia solo preparada o
  modificada despues no vale; hay dos pruebas negativas adicionales.
- Un indice parcial de un intento anterior podia colarse en el nuevo commit.
  La regresion reprodujo 149 archivos publicados cuando el lote tenia 100.
  Ahora el commit se limita a las rutas seleccionadas mediante `--only` y una
  lista NUL, manteniendo las otras rutas preparadas sin publicarlas ni borrarlas.

La misma revision amplia el caso de limite: un par incompleto en la siguiente
posicion no bloquea los primeros 50 pares completos, pero queda retenido y
bloqueado en su propio turno si no tiene una copia comprobable en un commit.

Otra regresion reproducida encontro que el commit parcial vuelve a normalizar
archivos y podia fallar con `core.autocrlf=true` y `core.safecrlf=true`. El ajuste
temporal se aplica tambien en ese paso y en la comprobacion del objeto Git,
preservando ambas opciones del checkout. Las pruebas de limpieza interrumpida
se repiten con esas opciones estrictas. Documentacion de referencia:
[Git commit](https://git-scm.com/docs/git-commit).

No es una recuperacion automatica de bloqueos abandonados: un corte externo de
Git aun puede dejar `index.lock`, que requiere comprobar propiedad/concurrencia.
Tampoco hay un plazo global nuevo, ni se amplia el limite de 240 s del supervisor.
Una cola de 4634 archivos necesita al menos 47 ciclos exitosos; la prioridad es
progreso acotado con originales conservados, no vaciarlo todo en un solo ciclo.
El cambio no debe describirse como desplegado mientras la VM siga en 9765758.

Resultado final local: **3035 pruebas pasan**, 624 advertencias, cero fallos,
errores o pruebas omitidas, en 153.71 s (Python 3.14.2). Incluye las 38 pruebas
del publicador. Revision independiente final sin nuevos hallazgos P1/P2.
Los siete casos del auxiliar de recuperacion tambien pasan. No se ha ejecutado
esta revision del codigo en la VM; su activacion y preflight quedan separados.
No hubo commit ni push de codigo. Las huellas exactas de los dos archivos
probados y de las pruebas se conservan en `verification_final.json`.

## Lista de cierre

- [x] Revalidar estado de la VM y publicacion automatica sin reiniciar.
- [x] Revisar incrementalmente si hay evidencia prospectiva nueva.
- [x] Inventariar las cuatro caches de la VM con identidad y hashes.
- [x] Recuperar y verificar 58 archivos diarios adicionales y sus metadatos.
- [x] Separar 33 consistentes de 25 diagnosticos sin alterar controles.
- [x] Terminar y verificar la proteccion permanente local del publicador.
- [ ] Publicar ese cambio solo con autorizacion explicita y controles de reinicio.
- [ ] Validar la reparacion con nuevas senales demo y ciclos completos reales.
- [ ] Recibir/exportar los historicos de Telegram y medios que faltan.
- [ ] Reobtener/verificar datos antiguos y fechas aun no cubiertas del broker.

La calibracion de ejecucion, la congelacion de supuestos y validacion no utilizada,
y la busqueda de estrategias corresponden a la fase posterior. No se da por
completo el historico anual ni se confunde inventario con certificacion.

## Evidencia local

Directorio `runtime_data/pending_cleanup_20260908_0014/`:

- `vm_evidence.json`, `events_delta.jsonl` y cursor con prefijo verificado.
- `cache_selection.json`, `cache_package.json`, `recovered_caches.zip`.
- `recovered/` contiene solo copias nuevas con la estructura de origen.
- `cache_recovery_verified.json` conserva cada excepcion.
- Siete pruebas del auxiliar de seleccion/recuperacion pasan.
- `pytest_review_red.xml`: cuatro fallos reproducidos, luego corregidos.
- `pytest_review_green.xml`: los cuatro pasan en 23.07 s.
- `pytest_crlf_red.xml`: reproduce el fallo de normalizacion.
- `pytest_review_final.xml`: seis casos finales pasan en 19.12 s.
- `pytest_full.xml`: primera version, 3028 pasan; no sustituye la comprobacion
  de la revision final, que contiene los cambios de reintento posteriores.
- `pytest_full_final.xml` y `verification_final.json`: evidencia final de 3035
  pruebas y huellas de los archivos; estos son los resultados vigentes.

ZIP SHA256: `f3a0a51777668a1002dc8679ef180d6bccb1a064792a93655df0f25ec22584cb`.
Seleccion SHA256: `44c29b3e5ad2d768e407deac2afa363ad1bedba1ff7763db7f1b36a7245f008f`.

Auxiliares y pruebas de transporte quedan locales, fuera del codigo de produccion.
