# Auditoria de habilidades e instrucciones

Fecha: 2026-09-07. Revision documental, no cambio de operativa.

Seguimiento: las correcciones autorizadas posteriormente estan documentadas en
[2026-09-07-instruction-changes.md](2026-09-07-instruction-changes.md).
Los hallazgos y numeros de linea de esta auditoria describen el estado anterior;
los originales se conservan en la copia de seguridad indicada en el seguimiento.

## Dictamen

Hay problemas concretos de alcance, instrucciones contradictorias y procesos demasiado rigidos. No hace falta quitar las pruebas ni los controles del bot: hace falta que se apliquen al trabajo correcto, con evidencia reutilizable y criterios de finalizacion claros.

La prioridad es corregir el contexto de proyecto, las instrucciones que pueden exponer secretos o descartar trabajo, y la ambiguedad entre reconstruir lo ejecutado y simular otra ejecucion. Despues, simplificar la cadena de planificacion, revisiones y verificacion. Instalar mas habilidades no es el siguiente paso util.

No atribuyo automaticamente los fallos historicos de la 555 a estas habilidades. Este examen demuestra riesgos en las instrucciones, no demuestra que una habilidad concreta causase un fallo de ejecucion o un consumo determinado de tokens.

## Alcance y metodo

- Busqueda de archivos de instrucciones en la carpeta de trabajo, incluidas sus copias de trabajo Git; excluidos datos generados, dependencias y directorios internos de Git.
- Encontrados 11 `AGENTS.md`, un `CLAUDE.md` en la raiz y ningun `SKILL.md` dentro del proyecto. Las habilidades personales estan instaladas fuera del repositorio.
- Inventariados y revisados los metadatos y las instrucciones principales de 28 habilidades personales y seis habilidades de sistema en las dos raices personales. Los 34 `SKILL.md` suman 288.624 bytes de archivo; esto NO es su consumo de contexto por turno.
- Revisadas las variantes de `AGENTS.md`, sus diferencias y duplicados. Contrastadas las instrucciones de telemetria del archivo activo con `runtime_paths.py` y `tools/run_bot_watch.py`.
- Leido el texto del articulo original mediante un lector publico, porque X devolvia 403 al acceso directo. Contrastado el enfoque con documentacion oficial de OpenAI y, para errores tecnicos concretos, MetaQuotes y statsmodels.
- Esto NO es una auditoria de todos los scripts, ejemplos ejecutables, dependencias o archivos auxiliares de cada habilidad. Tampoco es una auditoria completa de los cuerpos de habilidades dentro de caches de plugins administrados por terceros. Su existencia no equivale a haberlos certificado.
- No se han ejecutado los comandos de despliegue, borrado, credenciales o MT5 contenidos en los documentos. Tampoco se han alterado permisos, plugins, configuracion global ni procesos de la VM.

Estado observado del repositorio activo: rama `feature/gold-555-live-trial`, HEAD `b4426e6cb5649dee10c3ca57eb95866203d0bd98`, con trabajo local previo pendiente. Esta auditoria conserva esos cambios y solo agrega este informe.

## Articulo y contraste

El articulo de Eric Provencher, publicado el 4 de septiembre, recomienda revisar las instrucciones acumuladas para modelos anteriores: descripciones precisas, procedimientos cargados cuando hacen falta, menos recetas universales y autonomia delimitada por acciones seguras. Tambien cuestiona las comprobaciones y aprobaciones repetitivas que ya no aportan evidencia. No propone eliminar seguridad, pruebas ni juicio tecnico. [Articulo original](https://x.com/pvncher/status/2095991462416490862).

La guia oficial de Astra coincide en revisar el exceso de instrucciones y repetir verificaciones cuando hay cambios, fallos o dudas pendientes, no como un ritual independiente del estado del trabajo. Eso apoya una simplificacion controlada, no una garantia de que el modelo vaya a acertar por si solo. [Guia oficial](https://developers.openai.com/api/docs/guides/latest-model).

Dos precisiones importantes para este equipo:

- Las descripciones de habilidades participan en su seleccion; el cuerpo se carga al usar la habilidad. No se cargan automaticamente todos los 288 KB en cada respuesta. [Habilidades de Codex](https://learn.chatgpt.com/docs/build-skills).
- Los `AGENTS.md` de las distintas copias Git no se suman todos: importa la ruta de trabajo y el alcance aplicable. En esta conversacion se ha suministrado expresamente el archivo de la raiz, que describe otro proyecto. Un trabajo abierto directamente en la raiz Git del bot no tiene necesariamente el mismo contexto heredado. El `AGENTS.md` global encontrado esta vacio. [Alcance de AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

## Hallazgos prioritarios

### P1. La raiz identifica otro proyecto como activo

Evidencia: [AGENTS.md de la raiz](<C:/Users/josea/OneDrive/Escritorio/proyectos claude/AGENTS.md:3>), [CLAUDE.md](<C:/Users/josea/OneDrive/Escritorio/proyectos claude/CLAUDE.md:3>).

El proyecto declarado es `XAUUSDMomentumEA v2.07`, con validacion de abril, capital de 500 EUR, parametros de Londres, librerias fijadas y rutas de otro EA. El trabajo de esta conversacion es el copiador de Telegram. Ademas, la fuente maestra apunta a `proyectos Codex` en AGENTS y a `proyectos claude` en CLAUDE.

Impacto: puede orientar una nueva tarea hacia otro motor, otros criterios o una fuente equivocada. Los resultados historicos del EA no certifican al copiador.

Correccion recomendada: convertir la raiz en un mapa breve de proyectos y mover las instrucciones del EA a su ambito. Mantener el bot como proyecto separado con sus propias reglas. Las versiones instaladas, estado de la VM y resultados publicados deben verificarse en sus fuentes, no presumirse por una declaracion estatica.

### P1. Una habilidad imprime secretos para comprobar su existencia

Evidencia: [vercel-cli-with-tokens](<C:/Users/josea/.agents/skills/vercel-cli-with-tokens/SKILL.md:20>), comprobaciones adicionales en las lineas 28 y 317.

Propone mostrar el token de Vercel y buscar variables completas en `.env` o el entorno. Esto puede llevar secretos a las salidas y al historial de la tarea. No he ejecutado esos comandos ni constatado una filtracion en este examen.

Correccion recomendada: comprobar presencia sin revelar valores; usar consultas autenticadas que no impriman secretos. Revisar tambien ejemplos de diagnostico de entorno en otras habilidades antes de ejecutarlos. Mantener esta habilidad limitada a tareas reales de Vercel, no a cualquier peticion de hacer push.

### P1. Existen instrucciones de descarte y limpieza contradictorias

Evidencia: [test-driven-development](<C:/Users/josea/.agents/skills/test-driven-development/SKILL.md:37>) y [finishing-a-development-branch](<C:/Users/josea/.agents/skills/finishing-a-development-branch/SKILL.md:138>).

TDD ordena borrar la implementacion si se escribio antes que la prueba. La habilidad de cierre manda eliminar la copia de trabajo en las opciones 1, 2 y 4, pero despues dice conservarla en la opcion 2. Eso es especialmente inadecuado al retomar una sesion con cambios pendientes.

Correccion recomendada: preservar siempre el trabajo existente; demostrar el fallo mediante una prueba reproducible sin borrar codigo por un requisito ceremonial. Resolver la contradiccion de limpieza y requerir autorizacion explicita para descartar trabajo. Una habilidad nunca puede imponerse a las restricciones superiores de seguridad.

### P1. El contrato nuevo de igualdad con MT5 necesita delimitarse

Evidencia: [AGENTS.md activo](<C:/Users/josea/OneDrive/Escritorio/proyectos claude/telegram-signal-copier-gold-live/AGENTS.md:144>) y reglas sobre reparacion a partir de la linea 154. Este bloque forma parte de cambios locales previos; tambien corresponde revisar criticamente las reglas que hemos ido incorporando nosotros.

Es correcto exigir conciliacion exacta del dinero cuando se reconstruye lo ocurrido con las ejecuciones y costes reales. Pero el texto no distingue suficientemente ese caso de una sombra que genera precios de ejecucion propios. No se puede prometer igualdad al centimo de cualquier ejecucion hipotetica usando solo ticks.

Ademas, coincidir en numero de entradas y beneficio neto no demuestra por si solo equivalencia: dos errores de signos opuestos pueden compensarse. La comprobacion debe abarcar identidad de estrategia, version, secuencia de decisiones, cantidades, precios, costes y transiciones relevantes por posicion y cesta.

Correccion recomendada: explicitar tres contratos separados:

1. Reconstruccion contable con ejecuciones observadas: igualdad a la precision de la moneda, incluyendo costes y reconciliacion por operacion.
2. Reproduccion de decisiones: mismas reglas versionadas, entradas disponibles y secuencia causal; comparar tambien ordenes y cambios de proteccion, no solo el total final.
3. Simulacion alternativa: mismas reglas causales, pero ejecuciones modeladas. Declarar incertidumbre, limites y sensibilidad de ejecucion previamente definidos; no ampliar tolerancias despues para obtener un aprobado.

La reparacion de un defecto manteniendo el mismo contrato se comprueba sobre el caso original. Un cambio intencional de estrategia no puede clasificarse automaticamente como fallo porque ya no replica la estrategia antigua. Conservar el incidente, su alcance y la evidencia; no ocultarlo ni contaminar resultados prospectivos con reconstrucciones retrospectivas.

Este es un hallazgo sobre la precision del contrato escrito. No demuestra, sin examinar cada ruta y sus entradas, que todas las comparaciones actuales del codigo esten mal.

### P2. La cadena de habilidades convierte tareas pequenas en procesos largos

Evidencia: [using-superpowers](<C:/Users/josea/.agents/skills/using-superpowers/SKILL.md:11>), [brainstorming](<C:/Users/josea/.agents/skills/brainstorming/SKILL.md:13>), [writing-plans](<C:/Users/josea/.agents/skills/writing-plans/SKILL.md:10>).

Se exige invocar habilidades incluso con un 1% de posibilidad de aplicacion, aprobar un diseno para cualquier cambio y redactar planes exhaustivos con codigo antes de implementar. Despues se vuelve a preguntar como ejecutar. Otras habilidades agregan revisiones y agentes obligatorios por tarea.

Correccion recomendada: seleccion por pertinencia real. Diseno y preguntas cuando cambien decisiones importantes o falten datos; ejecucion directa de correcciones locales ya autorizadas. Plan breve por objetivos y pruebas, no duplicacion anticipada del codigo. Delegacion solo con independencia util y herramientas disponibles.

La declaracion de `using-superpowers` de que sus habilidades prevalecen sobre comportamiento del sistema es incorrecta y debe retirarse. Las instrucciones de una habilidad no cambian la jerarquia real de permisos.

### P2. Se repiten verificaciones por tiempo o mensaje, no por riesgo

Evidencia: [verification-before-completion](<C:/Users/josea/.agents/skills/verification-before-completion/SKILL.md:22>), [verification-loop](<C:/Users/josea/.agents/skills/verification-loop/SKILL.md:112>), [AGENTS.md activo](<C:/Users/josea/OneDrive/Escritorio/proyectos claude/telegram-signal-copier-gold-live/AGENTS.md:209>).

Una habilidad exige una ejecucion fresca en el mensaje actual; otra, verificaciones cada 15 minutos. Combinadas con pruebas antes y despues de integraciones, pueden repetir trabajo sin cambio relevante. La suite completa para un refactor importante si es razonable; no es necesario extrapolarla a cada lectura o ajuste documental.

Correccion recomendada: guardar evidencia ligada al estado del codigo, dependencias, datos y comando; reutilizarla si todo ello sigue igual. Repetir por cambio relevante, fallo o resultado desconocido. Prueba focalizada para un defecto local; integracion y suite amplia cuando afecte a ejecucion, dinero, estado compartido o despliegue. No rebajar controles para ahorrar tokens.

### P2. El enrutamiento de investigacion puede elegir un motor inadecuado o limitar las preguntas

Evidencia: [backtesting-trading-strategies](<C:/Users/josea/.agents/skills/backtesting-trading-strategies/SKILL.md:40>) y [AGENTS.md activo](<C:/Users/josea/OneDrive/Escritorio/proyectos claude/telegram-signal-copier-gold-live/AGENTS.md:164>).

La habilidad generica conduce a su propio motor, ejemplos diarios de BTC, datos de Yahoo y costes predeterminados. Es util para otros ejercicios, pero no sustituye las cotizaciones Bid/Ask, ejecuciones, Telegram, contratos de dinero y motores existentes del proyecto. Sus archivos auxiliares principales existen: el problema observado es de alcance, no de rutas inexistentes.

En sentido contrario, la regla de conservar precio, momento y volumen de las entradas reales sirve para comparar gestion sobre entradas fijas, pero no puede gobernar universalmente investigaciones que cambien el momento de entrada, el numero de posiciones o el lotaje.

Correccion recomendada: una entrada contextual corta hacia los motores existentes del canal correspondiente, distinguiendo gestion con entradas fijas, nuevas politicas de entrada y reconstruccion del proveedor. Separar sus universos y no presentar sus resultados como intercambiables. Los mensajes de resultados del proveedor son evidencia a contrastar, no una obligacion de encontrar parametros que reproduzcan sus pips.

### P2. Persisten instrucciones de publicar datos mediante el flujo antiguo de Git

Evidencia: [AGENTS.md activo](<C:/Users/josea/OneDrive/Escritorio/proyectos claude/telegram-signal-copier-gold-live/AGENTS.md:36>), [runtime_paths.py](<C:/Users/josea/OneDrive/Escritorio/proyectos claude/telegram-signal-copier-gold-live/runtime_paths.py:24>) y [watcher](<C:/Users/josea/OneDrive/Escritorio/proyectos claude/telegram-signal-copier-gold-live/tools/run_bot_watch.py:2566>).

El texto ordena que el watcher prepare el catalogo de `data/` junto a informes para Git. El codigo distingue el directorio de ejecucion `runtime_data`, admite configuracion explicita y publica mediante la telemetria. Esto no significa que haya que borrar los datos historicos versionados: significa que el procedimiento general descrito esta desactualizado.

Correccion recomendada: documentar fuente y publicacion reales, con distincion entre datos de ejecucion, copias de investigacion y artefactos versionados deliberadamente. Mantener integridad, retencion y recuperacion de logs; no volver a ligar la recogida rutinaria a cambios de codigo ni regenerar todo el corpus ante cualquier consulta.

### P2. Hay afirmaciones estadisticas y de MT5 imprecisas en las reglas antiguas

Evidencia: [AGENTS.md de la raiz](<C:/Users/josea/OneDrive/Escritorio/proyectos claude/AGENTS.md:46>), lineas 55 y 72.

- La particion escrita situa OOS antes del tramo de ajuste. Puede definir una comprobacion historica retrospectiva, pero no equivale a demostrar una validacion cronologica hacia adelante. Debe quedar fijado que experimento se esta realizando y que fechas permanecen sin usar para seleccion.
- No detectar autocorrelacion con Ljung-Box no demuestra ausencia de seleccion oportunista o sobreajuste. El test contrasta autocorrelacion; no certifica todo el proceso investigador. [statsmodels](https://www.statsmodels.org/stable/generated/statsmodels.stats.diagnostic.acorr_ljungbox.html).
- En configuracion de arranque de MT5, `Model=1` es OHLC de un minuto, no Control Points. [MetaQuotes](https://www.metatrader5.com/fr/terminal/help/start_advanced/start).
- Los ticks reales mejoran el modelo de mercado; no garantizan replicar todas las ejecuciones del broker. La configuracion del tester trata por separado, entre otras cosas, el retraso de ejecucion. [MetaQuotes](https://www.metatrader5.com/en/terminal/help/algotrading/testing).

Correccion recomendada: conservar prohibicion de contaminar el periodo reservado, pero retirar conclusiones absolutas y criterios del EA aplicados fuera de su alcance. No declarar edge por un umbral aislado, un Sharpe favorable o una bateria de pruebas unitarias.

### P2. Compatibilidad y autorizaciones requieren menos ambiguedad

Evidencia: [source-command-xeladash](<C:/Users/josea/.agents/skills/source-command-xeladash/SKILL.md:15>) y cadenas de habilidades que requieren `Skill`, `Task` o `TodoWrite` aunque no esten disponibles en esta sesion.

El comando del dashboard mezcla convenciones Bash con el entorno PowerShell actual. No debe ejecutarse literalmente como receta universal. Las herramientas y permisos se descubren en la sesion, no se inventan ni se eluden porque lo ordene un documento.

Tambien conviene persistir de forma breve las preferencias expresas del usuario: no enviar correos sin permiso, distinguir local/pusheado/desplegado/verificado, y no provocar cambios en el bot o reinicios inesperados con operaciones abiertas. La prohibicion de despliegue en un modulo investigador ya existe; falta una politica operativa general tan clara como ella.

Autonomia para leer, analizar y probar localmente no equivale a permiso permanente para publicar, enviar mensajes o alterar operaciones. Una autorizacion explicita vigente tampoco debe convertirse en preguntas repetidas para cada paso inocuo.

## Inventario de instrucciones del proyecto

Las rutas de esta tabla son relativas a la carpeta de trabajo, solo para inventario. Las referencias de los hallazgos anteriores abren archivos concretos.

| Archivo | Lineas | Dictamen |
| --- | ---: | --- |
| `AGENTS.md` | 167 | Reencuadrar: describe el EA antiguo, no el copiador de esta tarea. |
| `telegram-signal-copier-gold-live/AGENTS.md` | 213 | Activo. Corregir telemetria y contratos; reducir mapa extenso mediante referencias. |
| `telegram-signal-copier-dubai-live/AGENTS.md` | 169 | Otra copia Git. Mantener su alcance; no tratarla como estado actual de Gold. |
| `telegram-signal-copier-risk-hardening/AGENTS.md` | 169 | Identico en bytes al de dubai-live observado. No requiere duplicar esta auditoria ni editarse en masa. |
| `telegram-signal-copier-strategy-discovery/AGENTS.md` | 169 | Mismo contenido que dubai-live observado. |
| `task2-verification-2d62aa3e/AGENTS.md` | 169 | Mismo contenido que dubai-live observado. |
| `telegram-signal-copier-robustness-pack/AGENTS.md` | 169 | Mismo contenido logico que dubai-live; difiere la representacion del archivo. |
| `telegram-signal-copier-git-sync/AGENTS.md` | 88 | Variante historica; pipeline y contratos de investigacion de aquel estado. |
| `telegram-signal-copier-replay-validator/AGENTS.md` | 88 | Identico en bytes al anterior observado. |
| `telegram-signal-copier-july14-audit/AGENTS.md` | 80 | Variante historica; publicacion y evidencias antiguas. |
| `telegram-signal-copier-july13-audit/AGENTS.md` | 55 | Variante historica mas corta, incluso con contrato de cache antiguo. |

Los 11 archivos representan siete contenidos distintos por hash y menos variantes logicas por diferencias de finales de linea. Su antiguedad no convierte automaticamente en incorrecta una copia historica para su propio commit. No recomiendo borrar copias de trabajo ni sincronizar todas a ciegas.

`CLAUDE.md` duplica en gran parte la guia del EA con una ruta distinta. No se presupone su carga automatica por Codex; algunas habilidades si indican consultarlo. El `C:/Users/josea/.codex/AGENTS.md` encontrado esta vacio y no se encontro un override global.

## Inventario de habilidades personales

Todas las filas siguientes se refieren a `SKILL.md`. Se propone conservar referencias utiles, no borrar bibliotecas por no usarlas hoy. La columna de lineas mide longitud, no gravedad.

| Habilidad | Lineas | Accion recomendada |
| --- | ---: | --- |
| [using-superpowers](C:/Users/josea/.agents/skills/using-superpowers/SKILL.md) | 118 | Retirar activacion universal, regla del 1% y jerarquia ficticia. |
| [brainstorming](C:/Users/josea/.agents/skills/brainstorming/SKILL.md) | 165 | Acotar a decisiones ambiguas o de arquitectura. Sin aprobaciones repetidas para toda correccion autorizada. |
| [writing-plans](C:/Users/josea/.agents/skills/writing-plans/SKILL.md) | 153 | Planes breves con contratos, hitos y pruebas; no escribir toda la implementacion dos veces. |
| [executing-plans](C:/Users/josea/.agents/skills/executing-plans/SKILL.md) | 71 | Continuar dentro del alcance autorizado; resolver fallos abordables sin parada automatica. |
| [using-git-worktrees](C:/Users/josea/.agents/skills/using-git-worktrees/SKILL.md) | 219 | Aislar cuando sea necesario, reutilizar copia adecuada, no crear otra por todo plan. Adaptar shell. |
| [finishing-a-development-branch](C:/Users/josea/.agents/skills/finishing-a-development-branch/SKILL.md) | 201 | Corregir contradiccion de limpieza; respetar decision de integracion ya dada y trabajo pendiente. |
| [test-driven-development](C:/Users/josea/.agents/skills/test-driven-development/SKILL.md) | 372 | Mantener regresion que falla antes del arreglo; eliminar orden universal de borrar implementacion. |
| [verification-before-completion](C:/Users/josea/.agents/skills/verification-before-completion/SKILL.md) | 140 | Conservar evidencia antes de afirmar; permitir reutilizar evidencia del mismo estado sin cambios. |
| [verification-loop](C:/Users/josea/.agents/skills/verification-loop/SKILL.md) | 127 | Unificar con la anterior; quitar disparador temporal y plantillas de stack ajeno. |
| [systematic-debugging](C:/Users/josea/.agents/skills/systematic-debugging/SKILL.md) | 297 | Conservar causa raiz; usar registros existentes antes de instrumentar todo; acotar iteraciones por informacion nueva. |
| [receiving-code-review](C:/Users/josea/.agents/skills/receiving-code-review/SKILL.md) | 214 | Conservar contraste tecnico; retirar guion de personalidad y paradas globales por dudas locales. |
| [requesting-code-review](C:/Users/josea/.agents/skills/requesting-code-review/SKILL.md) | 106 | Revisar segun riesgo; fallback sin subagentes cuando no existan. No crear tareas de usuario como sustituto. |
| [dispatching-parallel-agents](C:/Users/josea/.agents/skills/dispatching-parallel-agents/SKILL.md) | 183 | Conservar independencia de trabajos, integracion y limites claros. Aplicar solo con beneficio real. |
| [subagent-driven-development](C:/Users/josea/.agents/skills/subagent-driven-development/SKILL.md) | 278 | Evitar agente mas dos revisores por cada tarea como obligacion; limites de iteracion y seleccion por riesgo. |
| [writing-skills](C:/Users/josea/.agents/skills/writing-skills/SKILL.md) | 656 | Reducir o sustituir su flujo por skill-creator actual; no exigir experimentos costosos para cada ajuste editorial. |
| [backtesting-trading-strategies](C:/Users/josea/.agents/skills/backtesting-trading-strategies/SKILL.md) | 179 | Acotar: no sustituir motores ni datos del bot por su ejemplo generico. Crear ruta contextual corta. |
| [pandas-pro](C:/Users/josea/.agents/skills/pandas-pro/SKILL.md) | 179 | Conservar carga selectiva de referencias; no imponer vectorizacion/categorias/estilo cuando cambia semantica causal. |
| [python-performance-optimization](C:/Users/josea/.agents/skills/python-performance-optimization/SKILL.md) | 438 | Conservar medir antes de optimizar y equivalencia; mover recetario basico a referencias opcionales. |
| [python-testing-patterns](C:/Users/josea/.agents/skills/python-testing-patterns/SKILL.md) | 623 | Conservar fixtures, aislamiento y casos relevantes; cargar ejemplos extensos solo cuando aporten. |
| [source-command-xeladash](C:/Users/josea/.agents/skills/source-command-xeladash/SKILL.md) | 17 | Corregir comando para shell real; no lanzar dashboard fuera de peticion pertinente. |
| [deploy-to-vercel](C:/Users/josea/.agents/skills/deploy-to-vercel/SKILL.md) | 297 | Acotar a webs Vercel. Mantener preview por defecto y consentimiento para produccion; usar permisos reales. |
| [vercel-cli-with-tokens](C:/Users/josea/.agents/skills/vercel-cli-with-tokens/SKILL.md) | 354 | Prioritario: eliminar salida de secretos y recetas de entorno no seguras. |
| [vercel-composition-patterns](C:/Users/josea/.agents/skills/vercel-composition-patterns/SKILL.md) | 90 | Conservar condicional para React; buen uso de referencias. No aplicar al motor Python. |
| [vercel-react-best-practices](C:/Users/josea/.agents/skills/vercel-react-best-practices/SKILL.md) | 150 | Conservar para React/Next, comprobar version real; no obligar a aplicar todo el catalogo. |
| [vercel-react-native-skills](C:/Users/josea/.agents/skills/vercel-react-native-skills/SKILL.md) | 122 | Conservar para apps moviles; fuera del alcance habitual de este bot. |
| [vercel-react-view-transitions](C:/Users/josea/.agents/skills/vercel-react-view-transitions/SKILL.md) | 321 | Reducir receta obligatoria y descripcion extensa; patrones solo si los necesita la interfaz pedida. |
| [web-design-guidelines](C:/Users/josea/.agents/skills/web-design-guidelines/SKILL.md) | 40 | Conservar activacion por revision UI; reutilizar alcance ya dado, sin preguntas redundantes. |
| [emil-design-eng](C:/Users/josea/.codex/skills/emil-design-eng/SKILL.md) | 675 | Precisar disparador. Quitar saludo promocional bloqueante sin pregunta y formato universal de respuesta; separar recetario. |

## Habilidades de sistema

Estan bajo `C:/Users/josea/.codex/skills/.system/`. Son administradas por la aplicacion: no recomiendo parchearlas directamente, ya que una actualizacion puede reemplazarlas. Las restricciones reales de herramientas y permisos prevalecen.

| Habilidad | Lineas | Dictamen de instrucciones principales |
| --- | ---: | --- |
| [openai-docs](C:/Users/josea/.codex/skills/.system/openai-docs/SKILL.md) | 39 | Util para documentacion oficial. Aplicar el orden de consulta de las instrucciones superiores, incluida evidencia local cuando corresponda. |
| [skill-creator](C:/Users/josea/.codex/skills/.system/skill-creator/SKILL.md) | 230 | Mejor base actual para concision, alcance y carga progresiva; evitar mantener dos metodologias obligatorias paralelas. |
| [skill-installer](C:/Users/josea/.codex/skills/.system/skill-installer/SKILL.md) | 59 | Util bajo solicitud. Sus indicaciones genericas de escalacion no sustituyen la politica vigente de permisos. |
| [plugin-creator](C:/Users/josea/.codex/skills/.system/plugin-creator/SKILL.md) | 250 | Especifica para plugins; no activarla para refactorizar el bot. No cambiar cache/configuracion por esta auditoria. |
| [imagegen](C:/Users/josea/.codex/skills/.system/imagegen/SKILL.md) | 316 | Util para imagenes generativas, no para inventar graficas de ticks o resultados. Las figuras de datos deben ser reproducibles. |
| [review-agent](C:/Users/josea/.codex/skills/.system/review-agent/SKILL.md) | 58 | Alcance de revision concreto. Encontrada en disco; no asumir que este anunciada o invocable como herramienta en cada sesion. |

## Lo que conservaria sin rebajar

- Datos originales inmutables, trazabilidad de versiones y operaciones excluidas con motivo explicito.
- Separacion entre investigacion y ejecucion de ordenes; nunca desplegar automaticamente una ganadora de una busqueda.
- Moneda, zona horaria, costes, Bid/Ask, latencias y secuencia causal explicitos.
- Periodos de prueba reservados y resultados negativos visibles. Un periodo ya usado para elegir parametros deja de ser prueba independiente.
- Pruebas permanentes para defectos reales; incidencias que no desaparecen al omitir una cesta del siguiente informe.
- Diagnostico incremental de logs, verificacion de integridad y respaldo independiente del codigo.
- Preservacion del trabajo local y distincion entre cambios hechos, publicados y realmente activos en la VM.

## Plan de correccion propuesto, no ejecutado

1. Resolver el alcance del directorio raiz y las instrucciones peligrosas de secretos/descartes. No borrar ni actualizar masivamente copias Git historicas.
2. Aclarar los contratos de reconstruccion, decisiones y simulacion en AGENTS activo antes de publicar el paquete previo de paridad. Contrastar esas definiciones con las rutas de codigo afectadas; no cambiar tolerancias para hacer pasar pruebas.
3. Reducir el AGENTS principal a identidad, limites, evidencia y un mapa corto. Mover procedimientos extensos a referencias de investigacion, telemetria y despliegue. No crear AGENTS anidados contradictorios.
4. Reescribir disparadores de habilidades y consolidar planificacion/verificacion. Mantener autonomia local dentro del encargo, con aprobacion solo para decisiones y efectos externos que realmente la requieran.
5. Mantener una ruta de investigacion del copiador que reuse motores existentes. El canal, estrategia, periodo, datos y tipo de experimento se fijan antes de ejecutar; no se infieren del ejemplo de una habilidad generica.
6. Validar el comportamiento con casos pequenos y comparables, registrando tiempo, herramientas, lecturas y tokens cuando sean medibles. No prometer un porcentaje de ahorro sin esa medicion.

Casos de aceptacion para el cambio de instrucciones:

| Peticion | Comportamiento esperado |
| --- | --- |
| Aclarar una etiqueta o corregir una errata | Respuesta/edicion corta; sin plan formal, subagentes ni suite del bot. |
| Corregir un defecto contable | Evidencia del caso, regresion, arreglo focalizado, conciliacion e integracion acorde al alcance. |
| Simular otra estrategia | Motor y datos del proyecto, contrato causal, separacion de seleccion/prueba, presupuesto y criterio de parada. |
| Retomar una tarea interrumpida | Leer estado y evidencia disponible; no borrar implementacion ni repetir pruebas validas sin motivo. |
| Publicar un cambio autorizado | Revisar alcance y riesgo de reinicio, comprobar despliegue permitido y version activa; informar de cualquier pendiente. |
| Pedir una revision documental | Informe de hallazgos; no enviar correos, tocar operaciones ni desplegar codigo. |

No ejecutar experimentos de instrucciones sobre una VM que este operando. Los casos de despliegue y efectos externos se prueban primero con dobles de prueba o un entorno aislado.

## Limites de esta conclusion

No se ha medido cuanto consumo historico procede de cada habilidad. La longitud del archivo no equivale a tokens cobrados ni prueba que una habilidad se activara en una tarea concreta. Tampoco se ha vuelto a certificar la 555, el simulador o la operativa de la VM en esta revision.

El articulo no elimina mi responsabilidad: cargar material irrelevante, repetir salidas truncadas o no acotar una investigacion tambien son decisiones de ejecucion que debo mejorar, no problemas que se puedan atribuir solo a AGENTS.md.

Resultado de esta tarea: auditoria escrita, sin cambiar habilidades ni instrucciones activas. Sin commit, sin push y sin despliegue.
