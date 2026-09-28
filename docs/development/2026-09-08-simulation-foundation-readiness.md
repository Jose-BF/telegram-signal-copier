# Bases Para La Busqueda Masiva De Estrategias

Estado y plan vigentes desde el relevo a Claude (24/09): [estado-y-plan.md](estado-y-plan.md). La semana 14-18/09 no es representativa (log pesado y reinicios colgados): no calibrar ni concluir con ella.

Relevo vigente para la posible migracion de modelo:
[estado del objetivo y cinco entregas pendientes](2026-09-24-simulator-goal-handoff.md).
La busqueda masiva no se inicia sin aviso y revision conjunta con el usuario.
La recuperacion previa al cambio de asistente se describe en
[punto de restauracion local](2026-09-24-project-restore-point.md).

Continuacion local 23/09: [anclas nativas de precio y reloj de la semana](2026-09-23-week-native-tick-audit.md).
Inventario de 416 senales con el tramo remoto incorporado, 40 cestas nativas
enlazadas, cinco dias por simbolo extraidos de terminal demo aislada y 230
deals con tick causal anterior. El maximo desajuste precio/deal es 15,85
USD/onza; no se disimula con tolerancias. Auditorias posteriores enlazan 114
de 115 cierres al centimo y reconstruyen las 40 cestas/809.620 marcas de
riesgo sobre todos los ticks, con cobertura FX retrospectiva declarada.
No es equity nativo continuo: faltan contratos de estrategia de las sesiones,
sellos de adquisicion, admision UTC y contraste integral con el replay. Todo
diagnostico y local; sin publicar ni certificar simulador/VM.

Continuacion local 23/09: [conciliacion historica en el replay](2026-09-23-replay-entry-reconciliation.md).
La muerte del proceso padre tras el efecto y la recuperacion sin segundo envio
tienen controles offline. El broker virtual ahora ofrece historial temporal y
registra `entry_reconciled` sin inventar una respuesta nativa. Tras corregir
una regresion detectada en la primera corrida, 742 focales y 7.622 globales
aprobadas con fuentes estables y 88 controles sinteticos. Sin publicacion ni
paridad real certificada.

Continuacion local 23/09: [recuperacion historica de entradas con respuesta
perdida](2026-09-23-entry-history-reconciliation.md). El intento conserva su
preparacion nativa, lee historial MT5 acotado y atribuye una ejecucion
inequivoca sin segundo envio. La reentrega incierta lo invoca con limite de
frecuencia. 190 pruebas enfocadas, 727 controles y 7604 pruebas globales
aprobadas con fuentes estables. Falta reinicio de proceso completo, historial
virtual temporal, ventanas largas y contraste real de riesgo/dinero. Todo
local; sin publicacion ni certificacion de VM o simulador completo.

Continuacion 21/09: [lecturas secuenciales de gestion](2026-09-21-sequential-management-reads.md).
Resumen monetario extraido en una cadena compartida por monitor local y ensayo
offline. TICK, POSITIONS e historias conservan relojes separados y se ejecutan
sobre TransportSession; timeout drena sin entregar respuesta tardia. Reparado
incidente de entrega anterior a la confirmacion del transporte. 175 focales
aprobadas; revision final acotada cerrada. General **6.889 aprobadas**, 475,49 s,
fuentes e identidad sin cambios; no quedan ensayos propios activos. Falta conectar este consumidor
con el motor suspendido: no se afirma que las decisiones completas ya usen las
lecturas. Sin publicacion ni certificacion live.

Continuacion 21/09: [cierre terminal durante esperas](2026-09-21-terminal-close-lifecycle.md).
Nuevo perfil opt-in conserva la primera decision de cierre, retira entradas
no enviadas y cierra fills tardios sin repetir cierres ni impedir SL nativos.
Preserva las distintas semanticas locales Dubai/Gold; v1 sigue por defecto.
535 focales aprobadas y revision independiente acotada cerrada. General final
**6.837 aprobadas**, 460,51 s, fuentes e identidad sin cambios y runtime
temporal. No quedan ensayos propios activos. Falta disponibilidad causal de lecturas, gestion
monetaria real y contraste historico. Sin publicacion ni certificacion live.

Continuacion 21/09: [politicas conectadas al transporte](2026-09-21-connected-policy-replay.md).
El driver ya recibe solicitudes generadas por el motor y devuelve concesiones
y respuestas a sus books. 370 focales aprobadas; BUY/SELL congelados repetidos
sin cambiar entradas. Revision de limites completada, 81 controles adicionales.
Suite general **6.661 aprobada**, 476,17 s, fuentes/protocolo sin cambios.
No es paridad live: falta
disponibilidad de lecturas, preparacion nativa y perfiles reales aun bloqueados.
Detectada diferencia de rejilla del riesgo: legacy, fases internas y muestras
post-evento no son intercambiables. [Contrato siguiente](2026-09-21-shared-risk-grid-contract.md)
fija barrera global, origen cero y desconocidos antes de implementar metricas.
Esa rejilla esta ahora implementada localmente. Primera suite: 6.687 aprobados
y fallo de aislamiento de un test de mensajes; evidencia conservada. Corregido
el fixture sin cambiar runtime y corregido P2 del validador de estados antiguos.
294 focales aprobadas, revision acotada cerrada. Nueva general: 6.683 aprobados
y ocho fallos del ensayo (cuatro por pytest.main/stdin en Windows y cuatro por
fixtures dependientes de rutas). Reparados solo los tres archivos de tests
afectados; **471 pruebas de todos los modulos afectados aprobadas** mediante
entrada normal y runtime temporal. Hashes estables, implementacion sin cambios
desde la general. Verificacion combinada explicitada en el contrato; no se
afirma una nueva general totalmente verde ni se suman conteos solapados.
Siguiente entrega: observacion causal de gestion e intencion terminal de cierre
con entradas comprometidas, antes de admitir basket_guard con cliente.
Sin publicacion; objetivo completo abierto. Los bloques inferiores conservan
su estado historico, no describen necesariamente la implementacion actual.

Continuacion explicita 21/09: [entrada y transporte incrementales](2026-09-21-incremental-entry-transport.md).
Primera entrada cliente decidida cotizacion a cotizacion; coordinador ahora
admite solicitudes generadas durante la sesion. 231 focales y revisiones
acotadas aprobadas, 48 controles completos de motor y 64 de transporte
conservados. Primera suite: 6.608 aprobados y un fallo del lanzador por nombre
de log repetido. Reproducido y corregido con identidad independiente del reloj;
108 focales aprobadas. Suite global final **6.617 aprobados**, una advertencia
previa, 466,78 s. Fuentes/identidad confirmadas sin cambios; no quedan procesos
de ensayo. Revisiones independientes acotadas completas. Falta conectar los
books de cada cesta con las concesiones/respuestas globales. Sin publicacion.
El objetivo no esta completado; `usageLimited` del objetivo automatico no
impide esta continuacion local solicitada ni implica trabajo en segundo plano.
La evidencia anterior 6.551 se conserva con sus fuentes, no se atribuye a los
cambios de motor nuevos sin comprobarlos.

Incidente general reparado localmente 21/09:
[cancelacion simultanea a confirmaciones](2026-09-21-runtime-cancellation-races.md).
Suite encontro una cancelacion absorbida; reproducida tambien en conexion,
respuesta y evidencia previa a entrada. Corregida sin liberar trabajo en curso
ni reenviarlo. 97 focales pasan en Python 3.11; siete regresiones tambien en
3.14. Otra ventana durante el guard fue reproducida y corregida con decision
atomica cancelacion/autorizacion; trece regresiones nuevas pasan en ambos
interpretes. Carga final local aprobada: 2.400 lecturas, 60 operaciones ficticias,
bucle max 32 ms, cola drenada. Suite final **6.551 aprobada**, una advertencia
previa, 472,22 s; hashes confirmados sin cambios. Revision adicional del
parche final inicialmente limitada por uso; completada en la continuacion
explicita posterior, sin hallazgos materiales en esa frontera y seis probes
en memoria adicionales. No es revision global ni certificacion de VM.
Sin publicacion; el objetivo sigue abierto.

Integracion actual 21/09: [motor suspendible y fill causal](2026-09-21-resumable-causal-fill.md).
Motor escalar reanudable sin duplicar politica; la ruta cliente resuelve el
precio/fill cuando llega su cotizacion, no al enviar la solicitud. 48 controles
congelados conservan resultados y trazas completos; 192 pruebas focales pasan.
Revision independiente focal sin hallazgos materiales. Primera suite global:
6.537 aprobados y un fallo de runtime que se trata en el bloque anterior;
suite final despues de corregirlo: 6.551 aprobados.
Falta conectar el
coordinador entre cestas; no es cierre de paridad ni del objetivo completo.
Sin publicacion ni cambios de estrategia o VM.

Bloque actual 21/09: [recurso compartido de transporte](2026-09-21-shared-transport-implementation.md).
Reglas de admision/prioridad compartidas con runtime, simulacion de colas entre
ambos canales, plazos y respuestas tardias sin liberacion/reenvio ficticios.
35 pruebas focales y revision acotada aprobadas. Carga local: 2.400 lecturas,
60 operaciones ficticias, bucle maximo 32 ms, cero errores; suite 6.425 aprobada
en 461,20 s, una advertencia previa de pandas.
No sustituye aun el motor de politica por cesta ni levanta
gates de portfolio. Sin publicacion. Continua el objetivo original completo.

Continuacion 21/09: [gestion condicionada y riesgo](2026-09-21-conditioned-management-risk.md).
Control reutilizable para ambos canales usando entradas confirmadas, sin copiar
cierres. 18 senales del 08/09: diez trayectorias comparables y ocho bloqueadas
por FX; minimos iguales en diez, drawdown igual en ocho, divergencias temporales
en las diez. No prueba entradas hipoteticas ni decisiones/colas live completas.
53 pruebas focales y 6.394 globales aprobadas (una advertencia); revision
independiente acotada sin hallazgos materiales. Sin publicacion. Quedan
secuencia de cierres/protecciones, anclas nativas y ampliacion de cohortes.

Bloque actual 21/09: [comparacion reutilizable de trayectorias de riesgo](2026-09-21-risk-trajectory-comparison.md).
Implementado comparador comun para ambos canales con exposicion, flotante,
realizado y drawdown. Aplicado a 18 senales/90 controles historicos archivados:
49 diferencias observables y 41 bloqueos conservados. Gold 2615 ilustra saldo
final igual pero riesgo distinto por el primer fill y sus niveles derivados.
No se reajustan demoras ni estrategias; 72 pruebas focales y 6.371 globales
aprobadas (una advertencia). E5 sigue abierto, sin publicacion ni certificacion general.

Verificacion local 21/09: [almacenamiento y admision de nuevas entradas](2026-09-21-storage-admission-implementation.md).
Se continua hasta implementar el plan; no se considera terminado por haber
cerrado una regresion. 6.340 pruebas generales aprobadas y una adicional del
configurador de prioridad Windows. Checkpoint durable previo a entradas,
reserva de disco, telemetria opcional limitada y sombras incompletas explicitas.
Carga local del servicio+diario: 2.400 lecturas y 60 operaciones ficticias;
checkpoint de entrada max 32 ms. No publicado ni verificado en VM. Retencion
compatible, pico real y contraste E5 amplio siguen abiertos; las pruebas no
certifican rentabilidad ni paridad. Los resultados siguientes conservan su version.

Ultimo cierre local 21/09: [recuperacion del trabajador huerfano](2026-09-21-parent-death-recovery.md).
Vigilante independiente con referencias del SO, sin matar el terminal ni
reenviar ordenes inciertas; corregido tambien el lanzador venv de Windows.
6.313 pruebas globales aprobadas y 14 regresiones adicionales de limpieza.
Carga final de 60 s: 2.400 lecturas y 60 operaciones ficticias correctas;
vigilante estable en 15,91 MiB. Ningun cambio publicado. Cierra esta frontera
de disponibilidad local, no E4/E5 completos: faltan contraste operativo en VM,
almacenamiento/retencion y equivalencia amplia de decisiones y riesgo.

Continuacion 21/09 local: [endurecimiento de ejecucion y recuperacion](2026-09-21-runtime-hardening-continuation.md).
Arranque protegido, propiedad exclusiva, persistencia fuera del bucle,
respuestas tardias y supervision implementadas localmente. Suite final:
6.302 pruebas aprobadas; carga de 60 minutos en snapshot intermedio y diez
minutos en el transporte final aprobadas, sin publicar. El informe conserva las condiciones aun
abiertas, incluida la recuperacion automatica tras muerte forzada del padre.

Actualizacion nocturna 20/09: [recuperacion y validacion local](2026-09-20-night-recovery-validation.md).
Cuatro defectos de recuperacion reproducidos y reparados; pruebas de aislamiento
nativo de 60 s en Python 3.14 y 3.11, mas carga local acotada. Sin publicacion
ni cambio de estrategia. E3-C/E4/E5 no certificados en conjunto; el informe
separa evidencia, revision de arranque pendiente y gates de despliegue.
Las referencias siguientes conservan el historial, no ordenan nuevos cambios
de modelo ni revocan la continuacion autonoma autorizada por el usuario.

Prioridad solicitada el 19/09: recuperar el objetivo economico de ambos
canales tras las incidencias de VM. [Ruta propuesta y lectura preliminar de
la semana](2026-09-19-weekly-diagnosis-roadmap.md): conciliar 14-18/09,
diagnosticar las grandes perdidas Gold, contrastar mejoras sencillas de las
estrategias actuales y ampliar despues segun evidencia. Solo inventario y
diario preliminar revisados en aquel punto; contabilidad semanal ya conciliada
posteriormente: Dubai -104.86 EUR, Gold -278.12 EUR. Sin nueva
busqueda, cambio de estrategia, publicacion ni promocion. Esta prioridad
no rebaja los contratos existentes ni convierte la ultima semana en OOS.

Prioridad actual tras auditoria y cambio de modelo:
[plan de ejecucion y simulacion fiable](2026-09-19-runtime-simulation-validation-plan.md).
Reutiliza las auditorias de 2774, colas y bloqueos nativos ya realizadas.
Recopilacion realizada; [revision de arquitectura MT5](2026-09-19-mt5-isolation-architecture.md)
completada con defectos reproducidos de resultados ambiguos e inventario schema 1.
E1-E2 tienen implementacion local. Tras dos revisiones, la
[tercera ronda de reparaciones](2026-09-19-e1-e2-third-repair-results.md)
cerro sus contraejemplos iniciales y genero el inventario schema 5.
Aislamiento nativo de 5 s medido positivamente: pausa maxima local del bucle
32.30 ms, Python 3.14.2/Windows. La
[revision de la tercera ronda](2026-09-19-e1-e2-third-repair-review.md) confirma
91 pruebas focales y reprodujo transiciones adicionales de recuperacion,
aplicacion, plazo y compatibilidad, junto con huecos de identidad/procedencia.
La [cuarta ronda](2026-09-20-e1-e2-fourth-repair-results.md) los corrige
localmente, genera schema 6 y supera 83 pruebas focales y la suite global de
5.993. Su [revision independiente](2026-09-20-e1-e2-fourth-repair-review.md)
confirma 103 pruebas focales, 32 controles adicionales y reconstruccion exacta
del v6, pero reproduce acuse obsoleto, error tardio y contradicciones de
identidad en latencia; tambien registra un valor numerico malformado que
aborta el informe. La
[quinta ronda](2026-09-20-e1-e2-fifth-repair-results.md) repara F1-F4 con
revision de resultado, conciliacion del error tardio, identidad global y
validacion numerica; conserva el inventario v6 y supera 6.001 pruebas. La
[quinta revision](2026-09-20-e1-e2-fifth-repair-review.md) confirma F1/F2/F4,
145 pruebas existentes y 69 adicionales de transporte; queda una colision de
identidad entre decisiones e intentos en F3. Siguiente accion: Sol/alto repara
solo esa validacion compartida y sus regresiones. La
[sexta reparacion](2026-09-20-e1-e2-sixth-repair-results.md) la completa y
supera 229 pruebas focales y 6.004 globales, conservando los cierres previos y
el inventario v6. La [revision final](2026-09-20-e1-e2-sixth-repair-review.md)
confirma esa frontera con 38 pruebas y sin nuevos defectos materiales. Base
offline E1-E2 aceptada para avanzar; validacion operativa aun pendiente.
E3-A del [plan de integracion](2026-09-20-e3-integration-plan.md) implementado
localmente: [cliente, trabajador y consultas](2026-09-20-e3-a-implementation.md),
6.080 pruebas aprobadas y ensayo nativo de 60 s con pausa maxima de 19,84 ms
del bucle padre. Python 3.11, entorno VM y carga sostenida siguen pendientes.
La [revision E3-A](2026-09-20-e3-a-review.md) reproduce cuatro fronteras pendientes:
bootstrap, cierre con pool saturado, flags de ticks y cuenta de la propia muestra.
La [reparacion E3-A](2026-09-20-e3-a-repair-results.md) corrige las cuatro,
supera 83 pruebas de frontera, 151 focales, 6.087 globales y un ensayo nativo
final de 60 s con p99 16,60 ms. El camino basico tambien pasa con Python 3.12;
Python 3.11, MT5 real y VM siguen pendientes. La
[revision de reparacion](2026-09-20-e3-a-repair-review.md) confirma los
contraejemplos originales, 83 pruebas existentes y diez controles independientes;
reproduce espera del cierre durante arranque y contaminacion del JSON por
stdout nativo. Esa revision dejo R1-R2 como siguiente reparacion y mantuvo
E3-B sin aprobar.
La [segunda reparacion](2026-09-20-e3-a-second-repair-results.md) publica el
proceso antes del bootstrap, permite detener initialize/bootstrap bloqueados y
separa stdout nativo del canal JSON. Pasa 87 pruebas E3-A, 157 focales, 6.091
globales y 60 s nativos con p99 16,51 ms; segunda lectura sin hallazgos.
La [revision formal](2026-09-20-e3-a-acceptance.md) acepta E3-A como base offline:
27 hashes contrastados y seis controles adicionales de cancelacion, creacion
tardia, limpieza y handle nativo Windows, sin nuevos hallazgos materiales.
Siguiente accion: Sol/alto implementa E3-B segun el plan; despliegue pendiente.
E3-B/E3-C conectaran operaciones y
supervision; despues se validaran ambas estrategias en matriz amplia.
VM observada operativa a las 17:29 UTC del 19/09, sin nueva comprobacion remota
en la revision de arquitectura. Los apartados siguientes conservan su historia.

Comparacion Dubai/R05 terminada el 14/09: [resultados y siguiente contraste](../audits/2026-09-14-canal1-current-comparison-results.md).
258 IDs del 05/06 al 07/09; referencia local `dubai_balanced_v1`, sin
verificacion remota nueva. Sobre 153 IDs comunes, escenario adverso y
presupuesto nominal 25 EUR: Dubai -290.27 EUR, R05 +122.01 EUR con 12 entradas.
R05 sin el mejor dia queda en -4.68 EUR. No hay candidata nueva certificada.
Dubai completo conocido: +50.99 EUR referencia / -405.05 EUR adverso.
Extension opt-in `basket_guard_v1`, tres motores, 1062 contrastes sin diferencias
y 316 controles antiguos conservados. 5857 pruebas generales aprobadas;
driver y bordes posteriores verificados por separado. Archivo
`current_strategy_study_v1`, identidad
`0bc8919818174fe8cce320d39cbaa13a3ff0b7e9f2dfb682cd99265b15347581`.
125 fuentes anteriores preservadas; estudios viejos inmutables. Todo local,
sin push, reinicio, cambios live ni ordenes. [Protocolo ejecutado](2026-09-14-canal1-basket-current-run-protocol.md).
Siguiente bloque: comparar causalmente Dubai de tres, dos y una entrada,
conservando las senales perdedoras y las mismas reglas de cesta. El desglose
por fills finales es descriptivo, no permite eliminar retrospectivamente las
44 cestas adversas de tres entradas (-1143.42 EUR). Ablacion aun no ejecutada.
La automatizacion de 30 minutos esta pausada y no debe reactivarse.
Esta comparacion tiene prioridad sobre el inventario adicional propuesto antes.

Cambio de enfoque solicitado por el usuario el 14/09: **comprender primero
las senales y sus rangos**, no continuar por inercia el catalogo de 4217 reglas.
La especificacion amplia y su campana quedan aparcadas, no ejecutadas ni
borradas. Prioridad vigente: [radiografia del canal 1](2026-09-14-canal1-signal-anatomy-plan.md).
Estudiar secuencia aviso/rango/gestion, tipos BUY/SELL, retrocesos, visitas al
rango y recuperacion; despues pocas gestiones sencillas de entrada inicial,
espera del rango y entradas adicionales acotadas con riesgo comparable.
Distribuciones y ejemplos primero, sin exigir meses perfectos. Causalidad,
costes, perdidas y ausencias siguen siendo limites de las conclusiones.
No se presupone rentabilidad ni se cambia produccion. Este alcance prevalece
sobre el catalogo y los siguientes hitos historicos.

Radiografia ya terminada y verificada: [hallazgos del canal 1](../audits/2026-09-14-canal1-signal-anatomy.md),
`signal_anatomy_v2`, identidad `ac0bb365e04b23b76756cafca8bc5e85b70b11919f554d001f83a69e70328e71`.
258 IDs conservadas, 240 recepciones iniciales respaldadas, 232 primeros
mensajes con precio asociados (168 rangos, 64 precios unicos). Mediana hasta
los niveles, 48.15 s. En 158 rangos con una hora completa, 131 visitas antes
de TP1/SL: 83 TP1, 24 SL y 24 pendientes. No son operaciones ni rentabilidad.
Los 24 SL recorren tambien el extremo lejano, frente a 34 de los 83 TP1;
investigar entrada principal y anadidos menores, no carga creciente a ciegas.
74 pruebas focalizadas pasan; fuentes/resultados/resumen verificados; motores
compartidos sin cambios. v1 y fuentes anteriores preservados, mismas 258
trayectorias/contextos. Siguiente pregunta finita:
[comparacion sencilla de entrada y gestion por rango](2026-09-14-canal1-simple-range-study.md).
Sin candidata economica, publicacion ni cambios de produccion en ese bloque.

Comparacion economica terminada y verificada:
[resultados y candidata de investigacion](../audits/2026-09-14-canal1-simple-entry-results.md).
`simple_range_study_v3`, identidad `bb1e7fd0e58234f4a0562ecf932e4c91da8764fe41d0d40502bf4fd38e95fc73`:
24 reglas, dos perfiles, 12384 filas y 328 contrastes de tres motores.
Entrada inmediata supera rango, punto medio y DCA; su positivo de cuatro
horas es sensible a cobertura. v1/v2 preservados; correccion de resumen sin
cambio de las 12384 filas de motor, nuevas evaluaciones rapidas identicas.
5765 pruebas de suite completa y 38 propias de estudio/filtro pasan.

Hipotesis adicional acotada ya calculada: filtro causal recorrido TP1/SL
0.25/0.50/0.75/1.00, ocho combinaciones 60/240 y 4128 filas. Identidad
`3ed6a074e32de1eb8ccb2a591ca7285570fb8ce8dc58ad7769fe6e8c26cdf585`.
Candidata SOLO de investigacion R05-60: ratio minimo 0.50, una entrada, SL/TP1
iniciales, 60 minutos, sin BE/DCA. +911.63 EUR hipoteticos desfavorables en
12 operaciones, pero +899.48 proceden de una sola; sin mejor dia +12.15.
No es robusta ni esta admitida para dinero real. Seleccion certificada nula.
Siguiente pregunta: proteccion sencilla de beneficio/break-even, maximo tres
gestiones con contrato causal y riesgo comparable. No repetir matrices cerradas
ni reactivar el catalogo amplio. Sin commit, push, MT5, VM ni cambios live.

Bloque BE terminado y verificado bajo
[protocolo de tres gestiones](2026-09-14-canal1-break-even-protocol.md): control
sin BE, primera instruccion directa recibida y umbral propio a mitad del TP1.
61 fuentes anteriores conservadas antes de ampliar `absolute_levels_be_v1`.
5822 pruebas de suite completa y siete propias del estudio pasan; todas las
filas calculables se contrastan en los tres motores y el control se compara
con su version anterior. `break_even_study_v1` conserva la incidencia de un
umbral de medio centesimo no representable; se corrigio redondeando hacia
arriba al paso 0.01 con regresion. v2 completo: 1548 filas, 2844 evaluaciones,
948 contrastes de tres motores, identidad
`abbbfc0bfd5e19b691265e4458ee1626c2095403f665e95cdec73497fd06c524`.
[Resultado de BE](../audits/2026-09-14-canal1-break-even-results.md): amplio
desfavorable -561.99 sin BE, -965.93 con aviso directo y -524.88 con mitad
de TP1; R05 +911.63 / +897.38 / +887.58. No mejora convincente ni menor
dependencia de la gran ganadora. Mantener R05-60 sin BE como hipotesis, sin
seleccion certificada ni prueba en papel activa. Siguiente bloque: inventario
local de evidencia adicional posterior al 07/09 y contraste de la regla fija
solo si hay cobertura; no etiquetar OOS si ya se uso en diagnostico/calibracion.

Actualizacion del 14/09, nueva investigacion autorizada en curso:
[fidelidad de entradas y descubrimiento iterativo](2026-09-14-canal1-causal-discovery-plan.md).
El usuario pide profundizar tras contrastar 2500 variantes anteriores con las
106 del cribado anual. Primero cruce de las mismas identidades raw/export y
controles fijos, incluidas las dos finalistas antiguas con sus parametros;
despues descubrimiento mas amplio, condicionado a esa evidencia y con nuevo
protocolo. No se presupone que las ediciones expliquen todas las perdidas ni
que el canal carezca de rentabilidad. No se modifica el archivo rechazado.
Sin cambios operativos ni canal 2. Este estado prevalece sobre los historicos.

Primer hito nuevo verificado: cruce raw/export `causal_clock_audit_v1`;
94 originales, 84 parejas principales (77 directas/7 por grupo), 11 sin
editar; ausencias, ocho primeras revisiones editadas y diagnosticos retenidos.
73 parejas editadas mantienen direccion; no hay hash raw de medios.
Controles pareados v2 terminados y verificados: 179 casos de reloj, cuatro
reglas exactas, 4152 evaluaciones y 1432 filas. Las mismas 72 parejas directas
dan recuperacion raw +48.51 frente a +12.17 EUR exportados con ejecucion
desfavorable; recargo anterior aplicado: +45.31 frente a +9.27. Son sumas
hipoteticas, no cuenta, OOS ni candidata seleccionada. El efecto de reloj
queda acreditado en ese subconjunto, no extrapolado a todo el ano.
39 pruebas propias y 5641 de suite completa pasan (634 avisos). v1 retenido por orden de resumen, prueba roja,
correccion y v2 recalculado: las 1432 filas de motor no cambian.
Ampliacion ya verificada: `receipt_inventory_v2`, 258 IDs junio-septiembre,
240 primeras recepciones respaldadas, 236 rapidas. Tres niveles separados:
115 canonicos, 133 con etiqueta antigua y diez con chat numerico observado
sin revision. No se inventan campos ni se rebaja el validador canonico.
El compilador ampliado conserva las 94 anteriores y anade 17 hasta el 07/09.
Cobertura `receipt_coverage_v1` verificada desde fuentes: 1032 filas, ventanas
validas 230/226/214/186 a 20/60/120/240 minutos. Todos los bloqueos retenidos;
metricas identicas en los 84 casos raw compartidos con los controles v2.
54 pruebas focalizadas y 5687 de suite completa pasan (634 avisos, 334.82 s);
cero estrategias nuevas en esta ampliacion.
Contraste ampliado tambien terminado y verificado: `receipt_controls_v1`,
2064 filas, 5520 evaluaciones, tres motores coincidentes y 672 filas antiguas
exactamente iguales. Recuperacion desfavorable, con el recargo anterior:
111 entradas/230 ventanas, -112.85 EUR; junio -114.67, julio -18.82,
agosto +41.69, septiembre hasta 07/09 -21.05. Son sumas hipoteticas, no cuenta.
Demora es positiva en referencia pero negativa en desfavorable; ninguna de
estas cuatro reglas es positiva en ambos perfiles sobre el total ampliado.
26 pruebas propias, 95 focalizadas y 5713 de suite completa pasan (634 avisos,
339.93 s). No quedan calculos ni pruebas pendientes de este contraste.
Especificacion amplia v1 fijada antes de leer ese P/L: catalogo previsto 4217
reglas, rondas hasta 10000/cuatro horas, con SL/TP/duracion/gestores y criterios
explicitos. Siguiente: generar y probar catalogo/cohortes, sellar archivo de
ejecucion y validar capacidades por familia/horizonte antes de las rondas.
No repetir controles cerrados ni presentar la especificacion como busqueda
ya ejecutada. Continuacion automatizada activa; ninguna promocion live.
[Evidencia de correspondencia y gestion](../audits/2026-09-14-canal1-causal-clocks.md).
[Recepciones ampliadas y duracion](../audits/2026-09-14-canal1-expanded-receipts.md).
[Cuatro referencias ampliadas](../audits/2026-09-14-canal1-expanded-controls.md).
[Especificacion de descubrimiento v1](2026-09-14-canal1-discovery-spec-v1.md).

Actualizacion nocturna del 14/09, bloque cerrado sin candidata admitida:
[plan de candidata para observacion](2026-09-13-canal1-overnight-paper-plan.md).
Prioridad autorizada: cribado anual acotado de 106 reglas de canal 1, con
ventanas moviles, dos relojes y dos perfiles, no busqueda masiva ni activacion.
Primera campana `paper_campaign_v1` detenida y preservada por discrepancia de
oracle ante cotizaciones de igual hora, caso 18185 del 19/03; no hay ranking.
Reparacion opt-in de vinculo hora+indice, contrato anterior conserva bloqueo.
391 pruebas focalizadas pasan y el caso real coincide en los tres motores
para ambos perfiles y modos. 65 fuentes anteriores copiadas y verificadas.
El laboratorio v3 de abajo es ahora evidencia historica. Suite completa: 5602
pruebas pasan. Laboratorio v4 verificado: 5472 resultados iguales a v3 salvo
behavior_digest, cero incidencias. Campana v2 terminada y verificada: 106 reglas,
209668 evaluaciones, 293 pares jornada/escenario, 237652 filas; 1052 fuentes y
295 archivos. Ninguna regla supera el filtro ni es positiva en los cuatro
grupos. No hay finalistas, no se ejecuta su fase ni se relajan criterios.
[Auditoria, rechazo y frontera operativa](../audits/2026-09-14-canal1-overnight-candidate.md).
La politica live actual no implementa estas reglas propias: cambiar parametros
no acredita equivalencia. Cuenta actual y exposicion sin verificar. Todo local,
sin commit, push, VM, reinicios u ordenes; canal 2 intacto en este bloque.
Este estado prevalece sobre los siguientes bloques historicos.

Ultima actualizacion del 13/09, extension terminada y verificada:
[extension opt-in BE/parciales](../audits/2026-09-13-canal1-be-partial-extension.md).
Break-even de precio y cierre parcial con remanente implementados por separado
en los tres motores. Perfil base conserva barreras. Corregidas unidades del
catalogo parcial (EUR, no precio XAUUSD) y una discrepancia de umbral decimal.
121 pruebas focalizadas finales y 5539 completas pasan. Laboratorio vigente
`shared_family_lab_v3`, identidad
`55cb522be3239178a23359a791f23e954065796fd4345171b3e22d2837f4b2be`, verificado
contra codigo actual y 1052 fuentes. Mismas nueve fechas, 61 casos y dos perfiles:
16 controles admitidos, 5472 evaluaciones, 416 combinaciones completas y 32
bloqueadas por junio; 1952 filas conservadas. Cero incidencias de motores/cartera.
Las 14 familias anteriores conservan sus resultados respecto a v1; las 16
coinciden entre v2 y v3, sin contar la huella de comportamiento. Volumen y
confirmaciones comprobados en 684 recorridos de las familias nuevas, sin errores.
v1/v2 y sus respectivas copias de 63 fuentes conservados, no reetiquetados.
Siguiente bloque separado: contexto de mercado previo a cada entrada; despues,
revision de alcance y campana por familias/ventanas moviles, no particion rigida.
No hay ganadora, beneficio anual ni busqueda masiva. Capital/costes completos,
cuenta continua, liberacion tras cierre y variantes no representadas siguen
pendientes. Solo canal 1, cambios locales sin publicar, sin MT5, VM u ordenes.
Este estado prevalece sobre los siguientes bloques historicos.

Estado anterior del 13/09, laboratorio anual de familias v1 terminado:
[catalogo, exposicion y control historico](../audits/2026-09-13-canal1-shared-family-lab.md).
Solo canal 1, segun aclaracion del usuario; capital/tolerancia no facilitados.
Puente anual perezoso con 881/240 casos y ventanas moviles conservadas. En el
reloj principal hay 166/184 dias completos a 20 min y 101/184 a cuatro horas;
no se presenta el subconjunto completo como rentabilidad de todo el ano.
Catalogo de todos los campos/opciones actuales, 16 controles fijos y registro
de ampliaciones. Catorce controles admitidos por perfil; break-even y cierre
parcial de una posicion permanecen bloqueados, sin desactivar protecciones.
Mismas nueve fechas mensuales y 61 casos del control previo, dos perfiles:
448 combinaciones, 1952 filas; 364 completadas, 56 bloqueadas por perfil y 28
por la jornada incompleta del 01/06. 4788 evaluaciones coinciden en tres motores,
sin incidencias de cartera. Maximo observado en simulacion: dos senales y
0.04 lotes; reserva fija de horizonte 20 min, incluso si no entra o cierra antes.
5443 pruebas completas pasan; cinco archivos y 1052 fuentes verificados.
Archivo `shared_family_lab_v1`, identidad
`2f2f8e04ba57c43e3a5646fc8bac00f79cbd25b953b25deb8b104e8a108e9b78`.
Siguiente: ampliar break-even/parciales y contexto previo con pruebas propias,
y congelar la campana por familias/ventanas antes de comparar candidatas.
Liberacion al confirmar cierre, cuenta continua y costes/capital siguen
pendientes; este control no certifica todas las combinaciones de parametros.
Cero candidatas optimizadas, sin ganadora, beneficio anual ni busqueda masiva.
No se modifican motores compartidos o politica live en este bloque. Sin MT5,
VM, ordenes, commit ni push; cambios locales sin publicar. Este estado
prevalece sobre los siguientes historicos, que se conservan para trazabilidad.

Ultima actualizacion del 13/09, control mensual del puente al motor terminado:
[61 casos congelados y una incidencia de cobertura](../audits/2026-09-13-canal1-entry-probe.md).
Primer dia UTC completo con entradas por mes, enero-septiembre: 45 casos desde
version conocida y 16 sin editar, sin sustitucion por cobertura o resultado.
60 simulados, 180 evaluaciones scalar/fast/oracle coincidentes; el SELL 20030
del 01/06 queda entre los 45 con tres pausas XAUUSD >5 s, maximo 11.05 s.
Regla anterior sin ajustar: 0.01 lotes de referencia, stop 10/objetivo 5 unidades
de precio, salida a 15 minutos, horizonte 20; perfiles y dinero hipoteticos
declarados. Cada entrada aislada, sin cartera ni suma de rentabilidades.
470 pruebas pasan, 1050 fuentes y resultados verificados contra codigo vigente.
Archivo `entry_probe_v1`, identidad `35d1acdd171da152e897720ad4c8e758b536cacdf609f4dcae25841a9ab5797d`.
Puente comprobado para este control, no certificacion monetaria ni estrategia
seleccionada. El control mensual no sustituye las ventanas moviles acordadas.
Siguiente: dataset anual/ventanas con politica de exposicion compartida y
costes/sensibilidades declarados; escenarios de capital antes de carteras.
No reabrir catalogo/agrupacion ya cerrados. Sin cambio de motores compartidos,
MT5, VM, live, commit ni push; investigacion local, cero busquedas en este bloque.

Ultima actualizacion del 13/09, regla temporal y puente de entradas terminados:
[flujo de mensajes, diferencias y cobertura](../audits/2026-09-13-canal1-entry-stream.md).
Version vigente `entry_stream_v3`: 5494 mensajes por escenario, 1453 IDs
direccionales anteriores asignados sin perdidas. Regla causal condicionada a
snapshots disponibles, sin relaciones manuales futuras: 881 activaciones
hipoteticas desde primera version conocida (863/724 pasan 20 min/cuatro horas),
240 desde versiones sin editar (235/204). Las 736 agrupaciones anteriores
se conservan para trazabilidad: 590 identicas, 145 separadas y una cruzada.
146 diferencias explicadas. No son 881 senales originales probadas: 24
activaciones usan ediciones mas de un dia posteriores a publicacion.
Ventanas moviles recalculadas, 18 prefijos mensuales verificados, 246 pruebas
pasan, 1035 fuentes y 12 resultados con hashes finales intactos. Puente al tipo
CausalSignal probado, cero evaluaciones de estrategias y gates de dinero intactos.
v1 rechazado por duplicados de formato/exportacion; v2 sustituido por un prefijo
textual no reconocido. Resultados, incidencias y codigo previo conservados.
Siguiente: control pequeno y fijo con ejecucion, volumen, costes y exposicion
propia declarados; no seleccion ni busqueda masiva. No hace falta repetir la
definicion de agrupacion como pendiente. Sigue siendo investigacion retrospectiva.
Sin MT5, VM, cambio live, commit ni push; limpieza temporal pendiente sin reintento.

Ultima actualizacion del 13/09, cruce offline de relojes y cobertura terminado:
[auditoria por entrada, mes y ventana](../audits/2026-09-13-canal1-annual-coverage.md).
736 entradas, cuatro relojes y dos horizontes: 5888 filas, sin descartar casos.
Desde publicacion superan cobertura 723/736 a 20 min y 621/736 a cuatro horas;
desde version conocida, 724 y 621. Son diagnosticos bajo horario declarado,
no admision causal. Hay 240 componentes sin editar conservados (235/204 superan
cobertura), de los que 234 coinciden con la referencia; recepcion observada cero.
Horario +2/+3 ligado a Nueva York como hipotesis, nunca offset de verano para
todo el ano. Las 28 referencias directas a fills se reproducen en precios nuevos;
35 referencias restantes son metadatos retenidos, no prueba empirica anual.
Ventanas moviles recalculadas por reloj, 1021 fuentes y cinco resultados con
hashes finales intactos, 5888 identidades/horas conciliadas y 228 pruebas pasan.
Los diez casos de las tres caches antiguas afectadas pasan ambos horizontes
desde publicacion con precios nuevos; impacto sobre estudios viejos pendiente.
Siguiente: regla causal de stickers/textos/ediciones y conexion al motor con
ejecucion y dinero declarados. No nueva telemetria universal por cada senal.
No simulacion, busqueda, cambio compartido, MT5, VM, commit ni push en este bloque.
Limpieza temporal de autenticacion sigue pendiente; no se reintenta el bloqueo.
Este estado actualiza los pendientes de reloj/cobertura conservados debajo.

Ultima actualizacion del 13/09, extraccion MT5 aislada autorizada y terminada:
[precios anuales y tres incidencias de caches previos](../audits/2026-09-13-canal1-annual-prices.md).
150477123 cotizaciones de XAUUSD/EURUSD desde VantageMarkets-Demo, enero a
septiembre parcial; 361 dias/simbolo con datos y 512 estados diarios. Lectura
completa y dos mitades coinciden, hashes y formatos releidos, 94 pruebas pasan.
60/63 referencias previas identicas; en XAUUSD 20/08, 21/08 y 01/09 se recuperan
455766 cotizaciones adicionales y se detecta `time_msc` antiguo en segundos.
`time_utc` antiguo conserva milisegundos. Originales intactos; impacto sobre
estudios previos pendiente. No reutilizar esas tres referencias como equivalentes
sin revisar su columna y cobertura. Precios raw aun no admitidos como reloj UTC
ni por horizonte; siguen pendientes ediciones, costes y politica causal.
Ambas copias MT5 cerradas, trading y API de ordenes verificados desactivados,
22 archivos originales intactos. Limpieza de dos copias temporales de
autenticacion bloqueada por el entorno, pendiente; estan fuera de OneDrive.
Sin ordenes, VM, reinicio del bot, nueva busqueda, commit ni push. La autorizacion
de extraccion queda cumplida y sustituye su estado pendiente anterior.

Ultima preparacion del 13/09, con decisiones delegadas por el usuario:
[universo anual y ventanas moviles](../audits/2026-09-13-canal1-annual-universe.md).
736 hipotesis de entrada (712 raices sticker y 24 textuales), 5494 mensajes
conservados, 50 decisiones de revision y 76 pruebas aprobadas. 736/736 tiempos
conciliados. 234 referencias con snapshot direccional sin edicion; 502 sin ese
respaldo, nunca rellenadas como si fueran originales. Protocolo congelado para
planificar cohortes: ocho semanas de desarrollo y dos de comprobacion, avance
quincenal, 14 ventanas completas y una parcial, purga de cuatro horas.
Se recalculara con el reloj finalmente admitido. No es OOS nuevo ni dataset
ejecutable. Se incluyen textos claros y reentradas; la consulta previa queda
resuelta. Los pares siguen siendo hipotesis retrospectivas, no deduplicacion live.
Precios: 63 caches previas verificadas; archivos nativos enero-septiembre entre
dos servidores Vantage, sin decodificar ni homologar. MT5 cerrado: pendiente
autorizar lectura mediante instancia aislada sin trading. Ningun arranque,
busqueda, commit, push, acceso a VM ni cambio real. Este estado prevalece sobre
las consultas y propuestas anteriores que se conservan debajo como historia.

Acuerdo posterior del 13/09: el usuario rechaza una separacion rigida del ano
y acepta preparar todo el listado, seguido de ventanas moviles cronologicas.
[Catalogo anual preparado](../audits/2026-09-13-canal1-annual-catalog.md): 5494
mensajes, 712 stickers GOLD y 737 textos candidatos conservados; 698 relaciones
texto/sticker propuestas, 39 textos sin asociacion y 43 con advertencias.
Las propuestas no fusionan entradas ni adelantan informacion. Version vigente
`catalog_v2`, 54 pruebas aprobadas y 712/712 IDs/direcciones/horas conciliados.
Consulta pendiente sobre incluir entradas textuales independientes; ediciones,
reenvios y precios siguen pendientes de admision. No hay dataset ejecutable,
ventanas congeladas ni nueva busqueda. Se mantienen fuentes y casos bloqueados.
Este acuerdo sustituye la particion fija propuesta inmediatamente debajo.

Prioridad posterior del 13/09: el usuario retoma el objetivo de usar los JSON
historicos y pide descargar canal 1 desde principios de ano con Computer Use.
[Historial anual actualizado y muestras propuestas](../audits/2026-09-13-canal1-year-history.md):
Telegram exportado del 1 de enero al presente; 5494 mensajes del 02/01 al
13/09, 712 publicaciones BUY/SELL GOLD identificadas visualmente, de ellas
687 no reenviadas y 25 reenviadas. No son aun senales admitidas por el motor.
Mayo recuperado; fuentes anteriores conservadas y union verificada por hash.
Propuesta: enero-abril desarrollo, mayo-junio comprobacion, julio-septiembre
contraste retrospectivo; reserva nueva posterior a congelar las reglas.
La prioridad es admitir este historial y comprobar precios de enero-junio,
no ampliar otra vez la optimizacion sobre solo 39 senales. Ediciones y
reenvios siguen diferenciados; no se inventan recepciones. Ninguna nueva
busqueda, cambio de motor, acceso a VM ni publicacion en este bloque.

Actualizacion mas reciente 13/09: el usuario aprueba el plan y pide empezar
solo por canal 1, con busqueda iterativa y ampliacion a criterio del agente.
[Primera campana Dubai cerrada](../audits/2026-09-13-canal1-recursive-results.md):
2500 candidatas, ampliacion de 500 solo para retroceso, parada por ausencia de
mejora material adicional y unos 40 minutos de calculo. Coordinador adaptativo,
164 pruebas focalizadas y 5278 completas verificados. Dominio de 13104 variantes;
39/39 senales de desarrollo y 50/55 posteriores, con cinco bloqueadas visibles.
Dos finalistas y un control: 1068 casos coincidentes entre tres motores y en
hechos economicos seriales. La demora es fragil; la recuperacion tambien pierde
en el escenario lento del contraste posterior parcial. Ninguna aprobada para
real. Siguiente revision: criterio de desarrollo multi-escenario, metricas de
riesgo pendientes y nueva validacion. Canal 2 y cambios live fuera del bloque.
Esta autorizacion local prevalece sobre las propuestas pendientes inferiores;
no modifica barreras de evidencia ni autoriza operativa/publicacion.

Actualizacion mas reciente 12/09, plan pequeno autorizado:
[primera comparacion de reglas propias](../audits/2026-09-12-small-search-results.md).
Reparados los defectos monetarios identificados y anadido cierre temporal
incondicional opt-in: 5263 pruebas completas aprobadas, 19 controles originales
reejecutados. Fase 2 cierra esas incidencias locales, no certifica toda ejecucion.
133 disparadores raw, admision dependiente del horizonte con seis incidencias
conservadas. Fase 4 inicia solo la familia acotada autorizada: 24 variantes,
primera familia terminada, ninguna Gold y una Dubai apenas positiva que pierde
en desarrollo con otras esperas. 664 casos coinciden en tres motores y en los
hechos economicos del cliente serial; 124 resultados iniciales reproducidos.
No se propone aprobar ninguna. Datos reutilizados no son OOS;
dinero/capital/cliente global siguen con barreras abiertas. Revision conjunta
antes de ampliar; sin busqueda masiva, publicacion ni cambios live.
Esta actualizacion prevalece sobre los estados historicos que siguen.

Aclaracion posterior 12/09:
[reconstruccion observada frente a simulacion independiente](../audits/2026-09-12-2774-why-reality-and-simulation-differ.md).
2774 reproducida nuevamente: -147.50 con hechos nativos, -237.88 con
operaciones hipoteticas y el mismo calculo de dinero. El control usa 250/250,
no todos los tiempos registrados. No se sustituye un fallo de representacion
por incertidumbre generica. Estado de fases y barreras sin cambios.

Actualizacion mas reciente 12/09:
[tiempos, precios y riesgo de error](../audits/2026-09-12-execution-uncertainty-results.md).
533 intentos analizados, 530 envios, 40 entradas Gold conciliadas. Se han
medido llamadas largas, esperas y fusiones de acciones; no identificado cada
componente interno del puente. En 12 cestas completas, el modelo primario
subestima seis, sobrestima cuatro e iguala dos; otras siete senales conservan
su estado sin exposicion o parcial. No es una cota conservadora certificada.
Fase 3 avanza en medicion, no se cierra; fase 2 conserva defectos monetarios.
Motor y bot sin cambios en este bloque, sin publicacion ni busqueda masiva.
La siguiente prioridad es reparar dinero y representar estos mecanismos,
antes de calibrar un margen unilateral sobre datos posteriores.

Actualizacion vigente 12/09:
[primer bloque del cliente simulado](../audits/2026-09-12-client-execution-results.md).
Perfil local y opcional de cola serial por cesta, tandas comprometidas y
precio/tiempo separados. La mecanica tiene regresiones; el contraste de
19 senales NO resuelve la discrepancia principal. No esta habilitado en
motor rapido, oraculo ni cartera. Fase 3 sigue abierta por tiempos reales,
contencion entre tareas y semantica detallada; fase 2 conserva los defectos
monetarios ya identificados. No hay publicacion ni busqueda masiva autorizada.
Esta entrada prevalece sobre estados historicos inferiores.

Entrada vigente del 11/09: [seguimiento simple](2026-09-11-simple-progress.md).
Reparaciones operativas publicadas/activas; investigacion local. El usuario
aclara que las revisiones programadas eran solo para ayer: automatizacion y
capturas de hoy eliminadas antes de correr, bot sin tocar. No repetirlas.
El contraste natural no se cierra solo por recibir mas operaciones: falta
concretar tolerancias de ejecucion antes de validar, ademas de muestra util.
Las ventanas, instrucciones y estados anteriores inferiores son historia.

Diagnostico posterior del 11/09: [causas generales de divergencia](../audits/2026-09-11-execution-divergence-root-cause.md).
La fase 2 tiene defectos monetarios reproducidos pendientes de reparacion;
la fase 3 no representa aun la cola compartida, bloqueo del puente y toda
la semantica de intenciones durante esperas. Los parametros nominales 555
coinciden; eso no acredita equivalencia operativa. El episodio 2774 queda
explicado causalmente, no ajustado ni convertido en validacion intacta.
158 pruebas focales aprobadas no anulan los nuevos contraejemplos. El orden
de reparacion y sus criterios estan en el informe; no hay cambio live,
busqueda masiva autorizada ni captura programada pendiente.

## Mapa Compartido Del Proceso

Entrada vigente del 10/09: seguir
`../audits/2026-09-10-day-review-sequence.md`. El usuario ha autorizado publicar
el arreglo pendiente y revisar operaciones a las 14:00 y 19:00 Madrid de hoy.
El commit `892bc33c8` ya esta publicado y descargado en la VM; la auditoria
registra la recuperacion y la comprobacion final del bot. Los estados fechados
del 09/09 que siguen son antecedentes, no instrucciones de repetir su ventana
de prueba ni de volver a publicar ese arreglo. La busqueda masiva sigue
pendiente de revision conjunta; esta secuencia no declara cerradas las bases.

Actualizado el 09/09/2026 tras la peticion del usuario de conocer el motivo,
estado, resultado esperado y siguiente paso de cada tarea. Esta seccion es la
entrada al plan; los informes tecnicos posteriores aportan evidencia, no
reemplazan el objetivo compartido.

**Objetivo:** poder comparar muchas estrategias de entrada y gestion propias
sobre senales historicas, con un motor comprobado dentro de un alcance claro.
No perseguimos copiar al milimetro el 08/09, ni limitar las simulaciones a
operaciones registradas despues de una futura actualizacion del bot.

El usuario confirma que los JSON historicos son disparadores de compra/venta.
El recorrido objetivo es: **JSON -> senales fechadas -> reglas propias y
precios MT5 -> operaciones simuladas -> resultados y riesgo**. Los SL/TP,
cierres y anuncios de beneficio del canal no gobiernan este estudio. Seguir
esa gestion seria otro experimento, no un requisito de esta primera version.

| Fase | Para que sirve | Que permite darla por terminada | Estado actual |
| --- | --- | --- | --- |
| 1. Preparar datos | Saber que senales, precios y condiciones podemos utilizar sin inventar informacion | Cada senal y horizonte tienen sus fuentes y limitaciones; las ausencias quedan visibles | Parcial: hay inventario y catalogo historico; falta admision completa por uso |
| 2. Comprobar el motor | Asegurar que las reglas de entrada, gestion, ejecucion y dinero se calculan correctamente | Cada capacidad habilitada y sus interacciones pasan pruebas independientes de resultado conocido | Perfil acotado verificado: 4.416 pruebas estables y 33 evaluaciones de once controles Gold concordantes; no admite capacidades fuera del perfil |
| 3. Validar su realismo | Contrastar los mecanismos con MT5 y medir la incertidumbre de ejecucion | Modelo y criterios fijados antes de comprobar datos no usados para ajustarlos; diferencias materiales explicadas o bloqueadas | Perfil y cohorte congelados a las 08:14 Madrid del 09/09; esperando ventana 08:30-10:30. Bot detenido, arranque pendiente de autorizacion |
| 4. Buscar estrategias | Comparar combinaciones sobre la base admitida | Presupuesto finito, resultados reproducibles y candidatas evaluadas con costes y riesgo | No iniciado; revision conjunta obligatoria antes de entrar |
| 5. Validar candidatas | Comprobar si las elegidas funcionan fuera de los datos usados para elegirlas | Evaluacion reservada y, cuando corresponda, prueba demo con parametros fijados | Posterior a la busqueda; no se confunde con validar el motor |

Las fases 1 y 2 pueden avanzar en paralelo. Tener un dato pendiente no obliga
a detener una prueba de motor que no lo necesita. Una capacidad no cubierta
debe repararse o excluirse expresamente del alcance, no bloquear toda
investigacion por defecto ni entrar silenciosamente en la busqueda.

**Donde estamos:** fase 2 comprobada para el perfil local acotado, con su primer
contraste futuro de fase 3 preparado y congelado. La apertura y el cierre a mercado ya
tienen solicitud, ejecucion/rechazo y acuse separados en los tres motores.
El precio confirmado no se usa antes de su acuse; los SL/TP instalados siguen
activos durante las esperas. Las pruebas incluyen rechazos, tramos cancelados,
timestamps repetidos, datos incompletos y limites de registro.

**Siguiente tarea al retomar:** comprobar la salud del bot y el protocolo,
seguir `2026-09-09-morning-forward-runbook.md` y capturar operaciones naturales
sin cambiar el perfil. Freeze realizado a las 06:14:03 UTC. La cohorte natural fijada es
06:30-08:30 UTC (08:30-10:30 Madrid). Estar preparados a las 08:30 Madrid no
significa tener ya dos horas de operaciones comprobadas. Los resultados se
revisan despues, sin ajustar parametros para igualar fills o un dia concreto.

La suite final del 09/09 termino a las 06:13:47 UTC: 4.416 pruebas aprobadas,
cero fallos/errores/omisiones, 395 fuentes y siete helpers estables. El protocolo
esta en `runtime_data/simulation_foundation_20260909/market_forward_v1/forward/protocol.json`.
Dos capturas nativas de lectura verificaron el recorrido operativo, cuenta,
simbolo y reloj. A las 06:12:47 UTC seguia sin proceso el bot; el ultimo snapshot
nativo de las 05:55 estaba flat. Ese snapshot no sustituye uno nuevo antes de
un eventual arranque autorizado. Ver `../audits/2026-09-09-market-morning-preparation.md`.

El perfil sigue siendo una hipotesis de cliente serial y reloj de cotizaciones,
no una replica universal de MT5. Margen/stop-out, fills parciales del broker,
requotes, freeze distinto de cero y concurrencia real quedan fuera. La cartera
comprueba precios de ejecucion y union de posiciones, pero no certifica margen
ni el tratamiento de swap nocturno. Las comisiones del primer control son una
hipotesis cero que debe contrastarse, no contabilidad real supuesta. Ver
`2026-09-09-market-integration-contract.md` y
`../audits/2026-09-09-forward-validation-preparation.md`.

## Cuando Estara Listo De Extremo A Extremo

Estas son condiciones de cierre de las fases 1-3, no nuevas fases ni una
autorizacion de busqueda. La admision siempre tiene un alcance: reglas,
canales, datos, precisiones, horizontes y condiciones de ejecucion declarados.
No se promete fidelidad universal para cualquier estrategia o broker.

| Pieza | Resultado que debe quedar comprobado | Estado |
| --- | --- | --- |
| Del JSON a la senal | Una sola senal por disparador, con canal, identidad, direccion y hora UTC; duplicados, stickers seguidos de niveles y ediciones tratados sin anticipar informacion | Existen catalogos y controles; falta adaptar y admitir estas exportaciones concretas |
| De la senal al mercado | Precios Bid/Ask durante todo el horizonte, reloj, restricciones de simbolo, conversion y costes identificados; ningun hueco rellenado como si fuera observado | Cobertura parcial del historico previo; la de estos JSON aun no esta admitida |
| De las reglas a las operaciones | Estado propio continuo; aperturas, rechazos, acuses, SL/TP, trailing y cierres se producen en orden causal, incluidas sus interacciones | Perfil limitado implementado y verificado con fuentes estables; contraste futuro pendiente, capacidades no admitidas conservadas |
| De las operaciones al resultado | Dinero realizado y abierto, costes, volumen y riesgo conjunto correctos; capital, margen y rechazos cuando el alcance los requiera | Hay componentes y pruebas; falta cerrar la integracion aplicable |
| Del resultado a la evidencia | Pruebas de resultado conocido, controles completos acotados desde mensajes sin inyectar fills y validacion independiente; mismo codigo/datos/supuestos producen el mismo resultado | Controles parciales; no hay admision integral |

La salida verificable enlazara cada mensaje con la decision de entrar o no,
las solicitudes y ejecuciones simuladas, los niveles realmente activos, la
causa de salida y el resultado. Las senales no ejecutadas y bloqueadas siguen
en el informe. Una posicion abierta al final de los datos no se convierte en
un cierre real inventado. Ningun reporte semanal reinicia posiciones pendientes.

El criterio de cierre no es una curva rentable ni acertar todos los fills al
milisegundo. Las reglas y el dinero deben ser correctos bajo inputs identicos;
la incertidumbre de ejecucion debe estar declarada y contrastada. Se fijan los
criterios antes de la validacion, se conservan las divergencias y se bloquean
las que invaliden el uso. El acuerdo entre motores no valida por si solo una
simplificacion compartida frente a MT5.

La prueba completa inicial usa reglas de control fijadas antes de ver su
beneficio y un conjunto acotado de datos admitidos. No elige una ganadora.
Despues se congela la version y se presenta al usuario la admision y sus
limites antes de la fase 4. Una correccion posterior exige nuevos resultados
con identidad distinta; no reescribe las simulaciones historicas archivadas.

## Que Datos Sirven Para Que

El mismo motor debe poder trabajar con estos usos, manteniendo separados los
hechos observados y las ejecuciones hipoteticas:

| Datos | Para que sirven | Limite |
| --- | --- | --- |
| Registros historicos del bot y mensajes antiguos, mas precios | Reconstruir lo observado cuando exista evidencia y simular gestiones o entradas alternativas | Una mejora del motor no recupera mensajes originales, cotizaciones o respuestas nunca guardadas |
| JSON de senales y precios historicos Bid/Ask de MT5, sin registros de ejecucion del bot | Simular como habria actuado una estrategia propia, incluidas senales que el bot nunca opero | Requiere adaptar y validar el formato; no prueba fills ni tiempos reales de recepcion desconocidos |
| Capturas nuevas detalladas de mensajes y MT5 | Detectar fallos, caracterizar ejecucion y validar mecanismos fuera de los casos usados para repararlos | No sustituyen el historico ni exigen registrar en real cada estrategia alternativa |

**Sin logs del bot no significa sin datos.** Se necesitan identidad de senal,
simbolo, direccion y hora con zona y significado claros, ademas del contenido
que use la politica, precios durante todo su horizonte y condiciones de
ejecucion/dinero. Las revisiones posteriores solo se incorporan desde que
pueden conocerse. Un JSON con el texto final editado no prueba el texto que
existia a la hora inicial. Solo fecha y hora no definen una senal completa.

Si falta la recepcion real, se puede definir otro experimento basado en hora
de publicacion mas retrasos declarados. Esa hora simulada no se etiqueta como
recepcion observada. Si la politica usa solo direccion y reglas propias, no
requiere los SL/TP posteriores del proveedor; si los usa, si requiere su
historia causal. Las velas no sustituyen automaticamente a ticks Bid/Ask para
resolver el orden intrabar de entradas, SL y TP.

Ya existen una ruta de catalogo Gold independiente de tickets ejecutados y
controles desde mensajes y ticks que generan sus propias entradas. Eso no
equivale a tener admitido cualquier JSON arbitrario ni todo el historico. Para
un archivo externo hay que comprobar su formato, informacion y disponibilidad.
Aqui MT5 aporta precios y evidencia del broker: no se ha construido una nueva
ruta que ejecute estos estudios dentro de su Strategy Tester.

Como referencia concreta, el catalogo Gold preparado abarca el 22/07-02/09 UTC
y contiene 242 senales NOW; no es un inventario universal de todos los canales
desde el 27/07 hasta hoy. Los controles posteriores, incluido el 08/09, son
otros conjuntos. Hay incidencias de mensajes, precios y dinero que se admiten
por senal y uso. Ver `../audits/2026-09-08-gold-foundation.md`.

Los datos nuevos se necesitan para cubrir evidencia ausente y comprobar sin
reutilizar los ejemplos de reparacion. No hace falta desechar julio/agosto ni
esperar a reconstruir un historico largo desde cero para trabajar sobre ellos.
Un modelo de ejecucion medido recientemente no se supone identico en todo el
pasado: se contrastan condiciones y se declaran escenarios cuando no se conocen.

## Historico Telegram Confirmado

El 09/09 el usuario confirma que The Gold Standard es el historico de Gold
al que se referia. Se ha inspeccionado, pero **no esta incorporado ni admitido
aun como muestra del motor**. Sus fuentes son:

| Canal | Exportaciones bajo Downloads/Telegram Desktop | Mensajes por archivo | Identidades de mensaje unicas en la union |
| --- | --- | --- | --- |
| Dubai, 1642806869 | ChatExport_2026-04-29/result.json y ChatExport_2026-07-05/result.json | 18.541 y 639 | 19.180 |
| Gold historico, 3828356530 | ChatExport_2026-06-30/result.json y ChatExport_2026-07-05 (2)/result.json | 2.669 y 807 | 2.797 |

La base absoluta es `C:/Users/josea/Downloads/Telegram Desktop/`. Los 679 IDs
compartidos entre los dos archivos Gold no son muestra adicional. Estos
recuentos son mensajes, no operaciones ni senales admitidas. Las versiones
distintas de un mensaje deben conservarse con su procedencia; deduplicar
identidad no autoriza a reemplazar su contenido historico por una edicion final.

Dubai contiene mensajes entre el 05/07/2023 y el 03/07/2026 UTC en las dos
exportaciones; Gold historico entre el 28/03/2026 y el 03/07/2026 UTC. Las fechas
extremas no prueban continuidad, precios disponibles ni cambian automaticamente
el periodo 2026 previsto. Gold historico y Gold actual (3908582492) son cohortes
distintas. Se informan por separado; transferir una regla entre ellos es una
prueba adicional, no evidencia intercambiable ni una union silenciosa.

Hashes SHA256 de los cuatro JSON inspeccionados, en el orden de la tabla:

- Dubai abril: `1040dc9aa4bebf7467b930d673ee9cbd03dfb7f2ef89e9585c93591447f61f17`.
- Dubai julio: `945ef1c65ac4d9ed0a589c5227ba25f7f3672a897f3e50c9b77ca629946ab953`.
- Gold junio: `84d6e49cfe1ef5775fdc9711e293ab9ea8d7dbac07241214aee7f6769998ca11`.
- Gold julio: `0aee6de28a3c2a3bfee06433c0a8f24a5b012153d916afa4262e4a2e467c7224`.

No se ha comprobado su uso previo en otras investigaciones. Tener archivos
recien localizados no los convierte en validacion independiente intacta.

## Evaluacion Semanal De Candidatas

Propuesta para las fases 4-5, **no ejecutada ni autorizada ahora**:

1. Antes de buscar, inventariar cobertura y uso previo y fijar bloques por
   tiempo: desarrollo, evaluacion posterior y una reserva final no consultada
   para seleccionar. No prometer una reserva intacta donde no pueda acreditarse.
2. Elegir y fijar la candidata usando solo el desarrollo. Evaluarla despues
   sin cambiar parametros, mostrando todas las semanas elegibles consecutivas.
   Una semana revisada para modificar la regla deja de ser prueba independiente.
3. Ejecutar de forma continua. Las semanas son cortes del informe, no cuentas
   nuevas ni cierres obligatorios. Mantener posiciones, costes y riesgo abierto
   entre semanas; en las fronteras de seleccion/evaluacion, impedir que retornos
   o posiciones compartidos filtren informacion futura al proceso de seleccion.
4. Mostrar beneficio neto, caida de equity abierta, peor perdida por cesta y
   conjunta, exposicion, numero de senales, bloqueos y concentracion del beneficio.
   No exigir ganancias todas las semanas ni tratar una semana con pocas senales
   como una prueba suficiente. Revisar estabilidad en distintos periodos y bajo
   escenarios de ejecucion mas adversos, fijados antes de observar la reserva.
5. Mantener los resultados por canal y epoca. Aplicar una regla reciente a un
   canal antiguo puede ser robustez retrospectiva; no se llama automaticamente
   validacion futura ni se suma como si fueran observaciones independientes.
6. Registrar todas las variantes probadas y limitar el presupuesto. Las pruebas
   de estabilidad y escenarios existentes no sustituyen un control del sesgo de
   seleccionar entre muchas variantes. Antes de una conclusion estadistica, fijar
   el metodo aplicable y comprobar su implementacion; no hay una correccion nueva
   certificada por citar un articulo o por dividir un reporte en semanas.

La reserva final se consulta con candidata y criterios fijados. Si se usa para
retocar o escoger otra candidata, pasa a desarrollo y se requiere otra prueba
independiente para la nueva decision. La comprobacion prospectiva del motor y
la de la candidata son distintas; ninguna requiere modificar el bot o enviar
ordenes sin autorizacion expresa.

El recuento de pruebas importa: seleccionar el mejor resultado entre numerosas
variantes puede inflar su rendimiento aparente. Esta cautela se fundamenta en
[Bailey y Lopez de Prado, Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
El protocolo semanal anterior es una propuesta para este proyecto, no una
garantia de eliminar el sobreajuste ni una certificacion estadistica ya ejecutada.

## Como Seguiremos Cada Tarea

- Antes: objetivo general, fase actual, motivo del bloque, resultado esperado
  y paso siguiente, explicados sin depender de recuentos tecnicos de pruebas.
- Durante: informar de un hallazgo que cambie el alcance o la prioridad y de
  las decisiones que necesiten al usuario. No convertir un caso particular en
  una nueva meta sin explicarlo.
- Al cerrar: que quedo demostrado, que sigue sin estarlo y actualizacion del
  estado de la fase. Mantener el trabajo acotado y verificable.
- Antes de la fase 4: revisar juntos capacidades, datos, limites, criterios y
  presupuesto. La publicacion o activacion del bot requiere otra autorizacion.

## Objetivo Vigente

Aclaracion del usuario tras la revision del 08/09: preparar las bases para
simular miles de estrategias con la mayor fidelidad posible a la ejecucion
real, admitiendo pequenas diferencias de ejecucion justificadas y medidas.
La rentabilidad de Gold 555 o Dubai actuales no es el criterio de avance de
esta fase. Sus operaciones proporcionan controles contra MT5.

El usuario autoriza continuar la preparacion local y exige avisar y revisar
juntos el estado **antes del punto 4 y de iniciar la busqueda masiva**. El
presupuesto de nuevas candidatas sigue siendo cero. Progreso del primer bloque:
`../audits/2026-09-09-simulation-foundation-implementation.md`. Es un checkpoint
intermedio; los puntos 1-3 no se declaran completados. La continuacion con
protecciones integradas y contraste MT5 esta en
`../audits/2026-09-09-protection-execution-validation.md`: 4.041 pruebas y
135 evaluaciones fijas, sin abrir la busqueda ni cerrar la fidelidad integral.

Este plan reemplaza la prioridad propuesta en la seccion de continuacion de
`../audits/2026-09-08-end-session-causal-review.md`. Conserva los resultados
historicos y sus limitaciones; no modifica certificados ni declara nuevas
capacidades implementadas. Primero se cierra la base del simulador y despues
se realiza la busqueda. El capital de inversion sigue abierto.

## Para Que Sirvio La Sesion Del 08/09

Se activo la captura antes de las senales para disponer de mensajes, decisiones,
inputs, acciones, respuestas y deals con identidad y cronologia verificables.
Eso permite localizar el primer punto donde la simulacion y MT5 difieren.
El beneficio de la candidata no demuestra la calidad del simulador.

| Evidencia disponible | Lo que demuestra | Lo que queda pendiente |
| --- | --- | --- |
| 59 aperturas y 59 salidas conciliadas | Referencia observada por posicion y EUR | Predecir fills alternativos |
| 328.596 decisiones revaluadas desde inputs y estados live | Coherencia local de la captura y funciones de politica | Simulacion continua que mantenga su propio estado |
| 6.929 filas causales completas | Enlace de los eventos seleccionados | Comparacion integral con la secuencia del motor |
| 108 evaluaciones fijas de tres motores | Acuerdo bajo los mismos supuestos | Verificacion independiente de esos supuestos contra MT5 |
| Control condicionado a 59 fills reales | Gestion posterior y conservacion de las entradas suministradas | Generacion independiente de las 59 entradas |
| 44 TP con precio y mecanismo coincidentes | Concordancia parcial de salidas en esas condiciones | Momento de instalacion, ejecucion y salidas gestionadas |

Revaluar funciones de politica usando el estado vivo en cada decision no
detecta todos los defectos compartidos entre esa funcion y el bot. El acuerdo
entre tres motores tampoco basta para validar una simplificacion comun.
Ninguna de esas comprobaciones sustituye la reproduccion integral.

Persisten 19 cotizaciones live no reproducidas por el historico del broker y
diferencias de cierre de hasta 253,383 segundos. No se aceptan como una pequena
tolerancia general. El control que incorpora las 19 cotizaciones conocidas
es sensibilidad diagnostica; no recupera una cinta completa.

## Criterio De Fidelidad

La semantica de mensajes, identidad, volumen ejecutable, prioridades,
transiciones de estado y contabilidad deben ser exactas bajo inputs y
condiciones iguales. Una operacion rechazada no puede aparecer ejecutada, ni
un nivel solicitado tratarse como instalado antes de su aceptacion por el
broker. Esa aceptacion puede preceder al acuse recibido por el cliente.

El modelo de ejecucion debe representar retrasos, Bid/Ask, spread, restricciones
del broker, confirmaciones, rechazos y reintentos. Latencia, precio ejecutado y
conversion se evaluan juntos para evitar contabilizar dos veces deslizamiento.
Los resultados historicos observados y sus diferencias exactas se conservan.

Las tolerancias son especificas por magnitud y operacion, declaradas antes de
evaluar la muestra de validacion. Se justifican con observaciones de ejecucion
y se contrastan en otra muestra; no se amplian para aprobar una discrepancia.
Una pequena diferencia de fill puede cambiar una decision posterior: se debe
explicar esa bifurcacion desde su primera causa, no compensarla con otro ticket.

No se puede garantizar el precio de ordenes alternativas que nunca se enviaron
ni el comportamiento futuro del broker. La entrega exigible es un modelo
verificado dentro de un dominio explicito, con incertidumbre cuantificada y
casos no cubiertos visibles. No se exigira probar en real cada combinacion de
miles de estrategias para validar sus componentes comunes.

## Hitos De Preparacion

- [x] Disponer de una referencia observada por deal y decisiones capturadas
  para la sesion del 08/09; conservar los controles condicionados y sus limites.
- [x] Definir la matriz de capacidades que la busqueda podra combinar y asociar
  a cada una su contrato, datos requeridos, prueba independiente y estado.
  Ver `2026-09-09-simulation-capability-matrix.md`; definirla no admite las
  capacidades que siguen sin cobertura o sin modelo de broker.
- [ ] Construir y contrastar una reproduccion continua desde mensajes y ticks,
  con entradas y estado propios, sin inyectar los fills finales ni restablecer
  el estado con snapshots live. Comparar entradas, niveles, rechazos y cierres
  en orden y registrar la primera divergencia por senal.
- [ ] Resolver los datos requeridos por cada control y horizonte: revisiones
  causales, Bid/Ask, reloj, conversion EUR, metadatos y costes. Los sidecars v3
  validan procedencia y contratos; no prueban por si solos continuidad total.
- [ ] Cerrar discrepancias del modelo de ejecucion mediante regresiones y
  contraste con evidencia retenida. Priorizar la instalacion de SL/TP y el
  episodio 10016 por su relacion con la discrepancia temporal observada.
- [ ] Comprobar todas las capacidades habilitadas y sus interacciones en los
  motores escalar, rapido y referencia independiente, incluyendo escenarios
  adversos y bordes temporales. Ampliar la cobertura mas alla de Gold 555/Dubai.
- [ ] Verificar contabilidad y cartera para variantes con volumen, entradas o
  salidas distintos: conversion causal, costes, parciales ejecutables,
  simultaneidad, equity, margen y rechazos cuando corresponda.
- [ ] Validar el conjunto en un bloque posterior al usado para reparar o
  calibrar, congelando antes el modelo y sus tolerancias. El beneficio de la
  candidata no es una condicion de exito de esta validacion del simulador.
- [ ] Emitir admision por capacidad, dataset y horizonte; comprobar coste de
  ejecucion, reanudacion e identidad de resultados para preparar la busqueda.

La matriz debe cubrir entrada a mercado, retraso, pullback/momentum y demas
modos ofrecidos por la gramatica; escalonados adversos/favorables; TP/SL,
trailing/BE; cierre temporal/de cesta; gestion del proveedor cuando se utilice;
parciales y finalizacion/reentrada. Las capacidades sin soporte o evidencia
quedan explicitamente fuera del dominio admitido. No se excluyen senales o
incidencias silenciosamente para aprobar el conjunto.

La auditoria de datos y las pruebas de componentes pueden avanzar mientras se
obtiene evidencia adicional. Una limitacion de overnight o niveles del proveedor
solo bloquea las politicas que dependan de ella; no se transforma en bloqueo
universal de diagnosticos que no usen esa capacidad.

## Implementacion Y Evidencia Existentes

- `research/dubai_iterative/contracts.py`: gramatica, limites y presupuesto.
- `research/dubai_iterative/engine.py`, `fast_engine.py` y `oracle.py`: motores
  compartidos; mantener independiente la referencia.
- `research/gold_iterative/dataset.py` y `tools/prepare_gold_study.py`: catalogo
  causal, adaptacion y admision de datos. Inventario Gold diagnostico de 242 NOW.
- `research/gold_iterative/live_parity.py`: control de gestion condicionado.
- `research/gold_iterative/exit_deals.py` y `exit_execution.py`: hechos y
  atribucion observada de salidas, separados de la simulacion independiente.
- `research/gold_iterative/pipeline_truth.py`: barrera de secuencia integral;
  `exit_decision_and_deal_sequence_not_verified` sigue pendiente. No retirar
  esa barrera sin un contrato y verificador de la secuencia completa.
- `tools/audit_management_capture.py`: captura local; no certifica el motor.
- `broker_money.py`, `mt5_tick_cache.py` y las herramientas de replay existentes:
  reutilizar los contratos de dinero, reloj y fuentes antes de crear otros.
- `research/iterative_provenance.py` y los buscadores Gold/Dubai: identidad de
  codigo/entorno/datos, presupuesto finito y reanudacion reproducible.

Las 3.668 pruebas aprobadas acreditaban el cierre anterior. El primer bloque de
implementacion posterior alcanza 3.905 pruebas y 270 ejecuciones de controles
fijos; sus fuentes e inputs quedan identificados en la auditoria del 09/09.
No acreditan los hitos pendientes ni fidelidad integral a MT5.

El incremento posterior integra SL/TP pendiente, aceptado, rechazado y acuse
en los tres motores bajo un perfil declarado. El control B1 de 2640 mejora de
253,383 segundos de adelanto a 6 milisegundos, pero mantiene diferencias de
entradas, reintentos y un cierre a mercado. El nuevo perfil no admite el SL
monetario Dubai ni certifica una cola de ejecucion real. Ver la auditoria de
protecciones del 09/09; los hitos amplios anteriores siguen pendientes.

## Decisiones Posteriores

La preparacion puede continuar con el alcance vigente: senales del canal como
disparador y gestion propia, usando el motor existente. No requiere seleccionar
ahora una candidata ganadora ni volver a pedir capital de inversion.

Antes de la busqueda se fijaran capacidades admitidas, periodos, costes,
escenarios de volumen/capital cuando proceda, presupuesto finito y particion
de descubrimiento/validacion. Se consultaran decisiones de alcance que cambien
materialmente ese experimento. La preparacion local no publica cambios del bot.

## Checkpoint De Reparaciones Del 09/09 Por La Tarde

Tras la revision de las 14:00 y la autorizacion de correccion local, se reparo
la espera acotada de evidencia EURUSD en las observaciones paralelas y se creo
una auditoria que distingue revision de mensaje y recepcion repetida. Se
conservan el colector congelado y sus resultados originales; el bloque de la
manana no se convierte retroactivamente en validacion prospectiva.

Ver `../audits/2026-09-09-afternoon-observation-repairs.md`: 4465 pruebas,
dos incidentes reproducidos con ticks nativos, 68 capturas conservadas como
37 revisiones y 31 recepciones compatibles, siete aperturas conciliadas con
MT5 y cinco cierres tambien cotejados con el diario. Los dos cierres Dubai
son evidencia nativa posterior al corte del diario descargado.

Queda cerrado este bloque de reparacion, no la validacion integral. Los hitos
amplios permanecen abiertos: reproduccion continua independiente, admision
por capacidad/dataset/horizonte y validacion posterior al bloque de reparacion.
Las reparaciones no estan publicadas. La VM seguia en `ed9ede1`, sin exposicion
reportada, a las 14:44:58 UTC. Activarlas requiere autorizacion separada; la
preparacion offline puede continuar. La busqueda masiva sigue pendiente de
revision conjunta y su presupuesto de candidatas nuevas permanece en cero.

## Integracion De Lecturas Del 21/09

La [integracion interquote](2026-09-21-interquote-management-integration.md)
conecta lecturas secuenciales al estado causal del motor y a su transporte.
Corrige picos no observados, bloqueo de entradas adicionales entre lecturas y
fallos ante cotizaciones invalidas. El paquete de evidencia identificado en
esa nota contiene los ensayos nuevos; no sustituye la validacion historica.

Sigue pendiente reproducir el ciclo completo de gestion Dubai/Gold555,
incluidas protecciones, y contrastar varias ventanas reales completas con
exposicion, flotante y drawdown. No hay publicacion, verificacion VM nueva ni
admision automatica de busqueda masiva. Los hitos generales siguen abiertos.

La [conexion de guards canonicos](2026-09-21-canonical-guard-components.md)
reutiliza ahora las decisiones monetarias Dubai/Gold555 sobre esas lecturas y
conserva por separado decision y encolado, incluidos fallos parciales. Es una
integracion de componentes, no admision de ambas estrategias completas: el
ciclo de monitor/protecciones y el contraste historico integral siguen abiertos.

La [extraccion del calculo de stop](2026-09-21-basket-stop-read-transport.md)
comparte la secuencia de lecturas del ejecutor con controles offline y conserva
por separado posiciones conocidas, valoraciones posteriores y entrega de la
respuesta. No instala protecciones ni conecta aun esas valoraciones al estado
causal del motor. El siguiente bloque debe cerrar esa integracion, no considerar
el nivel calculado como un stop confirmado por el broker.

La [integracion de stops en el motor](2026-09-21-basket-stop-engine-integration.md)
conecta ahora ese plan al estado causal y al transporte de protecciones
modeladas: encolado, solicitud, instalacion/rechazo, ACK y observacion posterior.
Conserva por separado el resultado final y el riesgo recorrido, incluidos gaps
y rechazos. Se corrigieron inanicion del guard tras timeout y acoplamiento
indebido de las frecuencias de consulta. Sigue sin admitir la estrategia
completa: valoracion nativa, protecciones iniciales, ciclo entero del monitor,
worker persistente, Gold555 y contraste historico permanecen pendientes.

El [control integrado de protecciones](2026-09-21-runtime-protection-control.md)
conecta helpers Gold555 y la cola/servicio/cliente/worker reales a un unico libro
simulado. Conserva instalacion, confirmacion, rechazo y resultado incierto sin
reenviar a ciegas. Verifica 18 trayectorias sinteticas post-evento completas y
54 alteraciones negativas; 111 pruebas focalizadas pasan. La bateria global
repetida en A: arroja 7066 aprobadas y un fallo de ruta larga en una prueba Git;
el modulo afectado pasa despues 47/47 con ruta corta y codigo sin cambios.
El primer ensayo con C: lleno y su informe truncado permanecen conservados.

Esto valida la integracion acotada de protecciones, no la estrategia completa:
entradas/DCA siguen generadas por el modelo y el trailing se invoca en puntos
elegidos. Faltan aperturas del controlador sobre el mismo libro, monitor
completo, valoracion nativa y contraste historico independiente. No hay
publicacion ni verificacion VM nueva. La busqueda masiva sigue sin admitirse
automaticamente y el objetivo general permanece abierto.

El [control de aperturas del runtime](2026-09-21-runtime-entry-control.md)
conecta ahora EntryWatch/listener Gold555 y la ejecucion durable real al mismo
libro simulado. El modelo deja de generar entradas paralelas y separa fill,
riesgo, proteccion y respuesta. Diez controles sinteticos BUY/SELL comparan
exposicion y dinero en cada cotizacion, incluidos gaps, rechazo y resultado
desconocido. Pasan 351 pruebas focalizadas y 7148 globales, sin fallos,
errores ni omisiones. Evidencia en `A:/ce-kiac8k2a/e`, con fuentes e identidad
de implementacion sin cambios durante la ejecucion y diez trazas conservadas.

Esto no cierra DCA, monitor autonomo completo, cierres activos, historial,
valoracion nativa ni contraste historico. No hay publicacion ni nueva lectura
de la VM. Los hitos generales y el objetivo siguen abiertos.

El [control integrado del monitor Gold555](2026-09-21-runtime-monitor-control.md)
avanza el bloque siguiente: DCA real, bucle del monitor, trailing, cierre
temporal, historial causal y finalizador sobre el mismo libro simulado.
Corrige el total final incompleto/fees omitidos y la clasificacion de deals
empatados en milisegundo. Pasan 401 pruebas focalizadas y 7263 globales,
sin fallos, errores ni omisiones; fuentes e identidad sin cambios durante
la ejecucion. Paquete `A:/cm-ldvsr_q7/e`, 36 trazas conservadas con hashes.

El alcance es sintetico y acotado: los recorridos del monitor completo esperan
reposo/cola entre cotizaciones. Falta integrarlo con precios que avanzan durante
sus esperas, ramas restantes, ciclo Dubai, valoracion nativa y contraste
historico. Costes cero declarados en los recorridos; fees no nulos tienen prueba
contable separada. Ni la estrategia completa ni la busqueda masiva se admiten
automaticamente. No hay commit/push/despliegue ni nueva verificacion de la VM.

El [control con monitor ocupado](2026-09-21-runtime-busy-control.md) incorpora
14 carreras BUY/SELL con cotizaciones que avanzan durante prepare/ACK y dos
recorridos de profit lock realizado+flotante. Corrige atribucion de SL/TP/BE
cuando se adelantan a un cierre pedido por el bot; no cambia politica ni fills.
La continuidad de entradas vigentes tras cesta plana se verifica, no se elimina.
Se conecta tambien valoracion lineal sin redondear, con FX causal y perfil
explicito para preparar el recorrido Dubai: sigue siendo hipotesis, no valoracion
nativa certificada. Pasan 484 focalizadas y 7346 globales, sin fallos/errores/
omisiones; fuentes e identidad sin cambios. Paquete `A:/cb-xaihqvzq/e`, 52 trazas.

Sigue pendiente el recorrido Dubai completo, casos restantes/recuperacion,
contencion entre cestas, valoracion nativa y contraste historico por capacidad.
No se cierra el objetivo ni se admite busqueda masiva o produccion a partir de
estos controles. Sin commit/push/despliegue ni nueva verificacion de la VM.

El [control integrado Dubai y ejecuciones pendientes](2026-09-21-runtime-dubai-control.md)
conecta entrada, DCA, stop agregado, guard, cierre e historial del canal 1 al
libro causal. Ocho controles BUY/SELL incluyen preparacion retrasada y FX ausente.
Corrige continuidad durable de actualizaciones de stop tras cola vacia/reinicio,
finalizacion monetaria incompleta ante entradas sin confirmar y cancelacion de
patas futuras/tardias, incluida la recuperacion al arrancar. No cambia estrategia.

Paquete `A:/cd-7r2evd4b/e`: 705 focalizadas aprobadas, 72 trazas con hashes
verificados; global con 7408 aprobadas y un fallo de sincronizacion de test.
Tras corregir solo ese test, su bloque de gateway/clientes pasa 80/80 en
`A:/cp-u11/green.xml`; no se ha repetido la global completa. La nota conserva
el fallo original y los ensayos intermedios, sin declarar verde retroactivo.

Siguen pendientes recuperacion integral de respuestas desconocidas, contencion
entre cestas, procesos/latencia VM, valoracion nativa y contraste historico
independiente. Las pruebas de cancelacion tardia verifican el comando de cierre;
la de startup comprueba tambien persistencia, no reinicio real ni cierre efectivo.
Objetivo general activo, sin admision de busqueda masiva, commit, push o despliegue.

La [recuperacion de confirmaciones tardias](2026-09-21-runtime-recovery-control.md)
cierra otro hueco: DCA durable confirmada pero ya cerrada no desaparece al
aplicarla en startup. La primera entrada Gold cerrada tambien reconstruye
Signal y contabilidad con historia completa, hora del fill y expiracion original.
No se mandan protecciones/cierres al ticket ausente ni se reinician cancelaciones.
Historia incompleta mantiene dinero desconocido; posiciones desconocidas no
se tratan como cerradas. Seis controles de monitor comparan recorrido completo.

Paquete `A:/cr-sg3xrdtc/e`: 727 focalizadas y 7447 globales aprobadas, sin
fallos/errores/omisiones, fuentes e identidad estables y 86 trazas con hashes
comprobados. La global incluye el test de gateway corregido del bloque anterior.

El alcance es timeout del llamador seguido de DONE del transporte real sobre
broker modelado, no conciliacion automatica de UNKNOWN persistido. Faltan
descubrimiento inequívoco por evidencia broker, cortes del proceso completo
entre eventos/Signal/finalizacion, contencion, valoracion nativa y contraste
historico independiente. Sin commit/push/despliegue ni nueva verificacion VM;
no se cierra el objetivo ni se habilita busqueda masiva automaticamente.

La [conservacion de evidencia previa al envio](2026-09-21-dispatch-preparation-evidence.md)
persiste la preparacion completa del worker en la misma transaccion del intento,
antes del efecto. Guarda cotizacion nativa, pedido e identidad sin nuevas llamadas
MT5 ni registros por tick. Corrige ausencia legacy sin inventar datos y verifica
rollback, muerte/reinicio del worker y reentrega sin segundo envio.

Paquete `A:/cr-ux8i338p/e`: 727 controles integrados y 7472 pruebas globales
aprobados, sin fallos/errores/omisiones; fuentes e identidad sin cambios y 86
trazas conservadas. Ademas, 80 pruebas focalizadas en `A:/cp-p05/green.xml`,
incluidas 25 nuevas. Falta construir la conciliacion historica inequívoca y
su atribucion exclusiva entre intentos; el snapshot por si solo no la realiza.
Se mantienen los pendientes generales y la separacion local/nativo. Sin
commit/push/despliegue, verificacion VM nueva ni admision de busqueda masiva.

El [control semanal de sombra frente a deals nativos](2026-09-23-week-native-tick-audit.md)
anade el cruce del control vivo de ambos canales en 12 de 44 senales recibidas
del 14 al 18/09, sobre 487 marcas de transicion con tick exacto. Los netos
finales Gold coinciden en seis de siete, pero el recorrido de exposicion y
dinero no queda validado por ello; 28 cestas nativas no tienen registro de
control en el sufijo conservado. El informe permanece diagnostico, sin paridad
de decisiones/pedidos ni certificacion del simulador. El reloj de emision de
la sombra revela procesamiento atrasado de ticks (hasta casi 46 h en una
entrada virtual); en `canal2_3086` la cola de archivo se reanudo justo antes
de emitir una entrada virtual basada en un tick 68,469 s anterior. Eso no
prueba que la orden real sufriera tal retraso. La extraccion posterior de
40 primeras aperturas nativas muestra 9 llamadas `mt5.order_send` de mas de
5 s, incluidas tres de mas de 60 s; en `3086` se miden 67,547 s dentro de
la llamada real. Se enlazan por deal, accion e intento, sin intervenir en el
bot vivo. Aun falta aislar la causa interna de esas llamadas y comparar
gestion, cierres y riesgo de trayectorias completas. Ninguna estrategia se
promueve.

El contraste posterior con los logs del terminal MT5 enlaza 40/40 deals y
ordenes de primera apertura, pero solo 226/230 deals de las 40 cestas estan
en esas lineas de terminal; cuatro salidas de una misma cesta se conservan
desde el ledger como ausentes del log. El terminal contabiliza duraciones similares a
Python; en siete de los nueve envios de mas de 5 s la linea `market` aparece
en los primeros 174 ms, y en dos aparece decenas de segundos despues. Esto
situa las esperas en el recorrido de envio, pero no distingue puente, IPC,
red y servidor. Una linea `accepted` posterior al deal y diferencias de
1 ms entre relojes se conservan, sin tolerancias que alteren la identidad.
Siguiente gate: reconstruir por cesta decisiones, ordenes, entradas DCA,
modificaciones y cierres, y comparar exposicion, flotante y drawdown contra
el replay bajo un reloj y cobertura admitidos. El simulador aun no esta
certificado y no se ha tocado produccion.

El piloto de trayectorias de cinco cestas localiza un mecanismo adicional:
`canal2_3086` envio 254 modificaciones al terminal, todas rechazadas con
`10016`, mientras la sombra puede avanzar un stop virtual sin acuse MT5.
Ocurrio despues de la apertura de 67,547 s y no se atribuye a ella. Dos
cestas Gold piloto no tuvieron rechazos `10016`, otra tuvo dos; Dubai muestra
dos aperturas rechazadas entre cinco intentos. La auditoria del 08-09/09 ya
documentaba una tormenta similar en `canal2_2640`. El siguiente comparador
debe tratar cada pedido, intento, rechazo, confirmacion y deal como estados
distintos antes de comparar los recorridos de riesgo de las 40 cestas. Los
pilotos son diagnosticos y las muestras por tipo no certifican trayectoria.

El plan completo de extraccion ya enumera 40 cestas, 115 posiciones y 230
deals en 249 tramos unicos, frente a 331 ventanas por cesta. Se recogieron
con limite de lectura y baja prioridad dos tramos de `canal2_3086` y dos
de `canal1_22732`. Sus 467 y 83 IDs no muestreados coinciden con los pilotos;
el [auditor material parcial](2026-09-23-week-native-tick-audit.md)
mantiene 4/249 tramos, 2/40 cestas completas, 38 pendientes. Las cuatro
aperturas nativas de esas dos cestas ligan con pedido, intento, resultado y
deal exacto. Dos SL Dubai tienen nivel 4369,25 observado en snapshot posterior
a modificacion aceptada; el momento de instalacion en servidor no consta.
La trayectoria de riesgo calculada con fills nativos y ticks esta adjunta;
no es una observacion independiente del flotante ni se ha comparado todavia
con la trayectoria del simulador. Dos cierres expertos
siguen sin enlace exacto al deal en el resultado del terminal.
El extractor local ya puede conservar el deal y orden anidados de cada
intento de cierre; los cuatro tramos guardados usan el esquema anterior y
deben recapturarse con la misma huella que los demas. El nuevo enlace de
identidad queda separado de la validacion completa con historial de ordenes.
La version v2 del extractor preserva ademas las entradas, estado y resultado
de todas las evaluaciones `management_decision_inputs_v1`, tambien las que
no generan acciones. El auditor empareja inicio/final y conserva faltantes;
los cuatro tramos v1 actuales no aportan esa captura y el informe real marca
0/40 cestas emparejadas. Las 21 pruebas focales y la validacion de esquema
de 2.678 eventos locales no sustituyen la
captura real ni el replay de la politica y del drawdown.
Ya hay un comparador puro de guards con prueba de resultado y estado:
en la nueva foto del diario local reproduce 287/290 evaluaciones Gold 555 y
bloquea tres por reloj sin zona, sin asignarla por conveniencia. Es evidencia
diagnostica de la funcion de gestion, no de la sesion de septiembre ni de
entradas, ejecucion, flotante y drawdown. La captura v2 semanal sigue pendiente.
La ruta del comparador semanal exige todos los tramos v2 de una cesta y
rechaza mezcla de versiones antes del replay puro del guard. Tiene prueba
sintetica, aun ningun resultado semanal real.
Las 33 capturas de cuenta existentes solo coinciden con exposicion de cuatro
cestas, ninguna de los pilotos 22732/3086. El comparador v2 ya reduce el
P/L de MT5 observado por el guard a un drawdown muestreado, pero solo sobre
los tickets conocidos por el bot. No equivale al drawdown de toda la cesta
ni a paridad frente al simulador hasta conciliar universo de posiciones,
reloj y muestras contra los ticks y decisiones. La hora registrada es la
evaluacion del guard, posterior a `positions_get`; la hora exacta de lectura
no consta y no puede usarse como punto de coincidencia temporal.
El ejecutor semanal admite ahora el fichero de deals nativos fijado por hash
en el plan y contrasta conjuntos de tickets a la hora del guard, con bloqueo
por reloj no anclado o deal simultaneo. Como la lectura de MT5 fue anterior
en un momento desconocido, el contraste sirve para encontrar diferencias,
no para declarar completo el universo de posiciones ni validar el drawdown.
Hay ademas un gate nuevo de cobertura de origen: la busqueda binaria por
hora del extractor v1/v2 supone orden aproximado en el diario, mientras una
pasada local encontro cuatro retrocesos en 198.084 eventos. Existe un indice
local de bloques por minimo/maximo de hora y extraccion v3 de bloque completo
con hash, probados con desorden sintetico. El lector remoto y el ensamblador
de indice completo estan listos localmente; la agregacion v3 de todos los
bloques seleccionados tambien pasa pruebas, pero aun faltan el indice del
prefijo remoto y capturas v3 reales. No se
atribuye una omision a los cuatro tramos antiguos sin evidencia adicional.
El auditor semanal ya no convierte por si solo 249 archivos v1/v2 presentes
en `full_cohort_coverage_verified=true`; v3 tampoco admite cobertura completa
del origen sin demostrar que no se anexaron eventos tardios. Ese cierre de
origen requerira un contrato posterior. La mejora esta local, no publicada.
No se ha ejecutado la lectura masiva con mercado abierto. Cobertura de eventos materiales no equivale a paridad:
faltan el resto del diario, los demas estados de proteccion por ticket y el
contraste de exposicion/flotante/drawdown sobre reloj admitido. Sin cambio
local de politica, commit, push ni despliegue.

Actualizacion 23-09: SSH volvio a responder y un bloque remoto de 65.960
bytes paso el lector indexado; el diario completo mide unos 9,9 GB y no se
ha lanzado su lectura masiva con el mercado abierto. Hay un colector local
reanudable con presupuesto por lote. Las 15 capturas independientes con
posiciones abiertas siguen sin ancla de reloj directa: no validan el
drawdown. Para capturas futuras, el monitor local registra un intervalo
UTC y duracion monotona alrededor de `positions_get`; el comparador bloquea
saltos de reloj y deals dentro de ese intervalo. Los datos historicos no
adquieren esos sellos retroactivamente. Suite completa: 7.696 pruebas pasan,
una advertencia previa de pandas. Cambio local sin publicacion; faltan la
captura semanal completa, el cierre de origen y el contraste de trayectorias
real/virtual antes de admitir el simulador.

El piloto v2 del 23-09 ya recupero los cuatro tramos reales de 3086 y 22732.
Reproduce todas las decisiones puras del guard capturadas (3.135 y 2.278)
y compara su P/L observado con el rango estricto-causal de ticks nativos:
3.118 Gold y 2.270 Dubai dentro; 16 Gold bloqueadas por FX, seis lecturas
bloqueadas por deals simultaneos y tres Dubai fuera del rango (0,09, 0,09 y
0,01 EUR). No se descartan estas tres ni se ensancha tolerancia. El
[informe del piloto](2026-09-23-week-native-tick-audit.md) detalla hashes y
limitaciones. El gate oficial v15 mantiene solo 4/249 tramos y 2/40 cestas
emparejadas. Siguiente trabajo: completar el indice/captura de origen fuera
de carga critica, investigar las discrepancias y ampliar el contraste de
recorrido a toda la cohorte. La hora exacta de `positions_get` historica no
puede reconstruirse con este piloto; la instrumentacion futura aun no esta
publicada. Este avance diagnostico no certifica el simulador ni autoriza
cambiar la estrategia de produccion.

La revision local del piloto de intervalo, anterior al gate de proteccion
v15, paso 7.702 pruebas (una advertencia conocida de pandas, 645,19 s);
no hubo commit, push ni despliegue.

El gate v15 separa tambien proteccion solicitada de nivel observado: 3086
acumula 254 intentos de modificacion rechazados sin ningun cambio aceptado
en la captura, mientras 22732 tiene tres modificaciones aceptadas con
snapshot posterior. El SL inicial de una orden de mercado no se deduce de
esta serie de modificaciones. Antes de validar la trayectoria virtual,
hay que alimentar el replay con confirmaciones/rechazos reales o bloquear
las acciones sin evidencia; una orden de mover SL no puede contarse como
SL efectivo. La cobertura de origen y las otras 38 cestas siguen abiertas.

El contraste sombra/nativo v8 da un mecanismo general para parte del
desfase de flotante: 145/487 marcas de 12 controles tienen misma exposicion
abierta, sin cierres, y el precio de entrada explica exactamente su
diferencia bajo la cotizacion/FX comun. En 3148, 25 marcas aptas tienen
residuo 0,00 EUR; el valle compartido difiere 3,88 EUR por entradas,
y el distinto pico previo explica que el drawdown muestreado difiera
3,11 EUR. No es una validacion del recorrido completo: las 342 marcas
restantes, 3086 sin marcas aptas, el enlace pierna-orden por ordinal,
la cobertura del diario y la respuesta de proteccion del broker siguen
siendo gates separados. El proximo contraste debe enlazar cada entrada
real con su pierna y hacer que rechazos/confirmaciones de SL/TP formen
parte de la trayectoria comparada antes de admitir riesgo virtual.

El piloto local de proteccion 3148 v2 ya enlaza sus tres entradas con
fill/result/deal/ticket y coteja solicitudes, intentos, confirmaciones y
snapshots con el ultimo estado sombra emitido. Solo 3/11 snapshots permiten
comparar niveles y los tres carecen de `position_exists`; 6 encuentran
la pierna sombra cerrada y 2 fueron registrados tras el cierre nativo.
La diferencia de SL virtual menos observado en los tres cotejos es
-0,44 / -0,35 / -0,44 unidades XAUUSD. No se ha verificado el instante de
lectura del broker ni la instalacion o continuidad del SL. Esto mejora la
atribucion de la divergencia en una cesta, pero no certifica el recorrido
real/virtual ni amplia la cobertura 4/249 tramos y 2/40 cestas completas.
El siguiente gate sigue siendo cobertura de origen y reconstruccion de
aperturas, gestion, cierres y riesgo de todas las cestas admisibles.

El auditor 3148 v3 verifica un mecanismo de cierre de Gold 555 en tres
piernas: los TP reales observados se desplazan exactamente con la
diferencia entre fill real y virtual (+0,44 / +0,33 / +0,57 XAUUSD), y
los deals de salida coinciden con esos niveles. El codigo vivo y sombra
calculan ambos TP relativo a su fill. Esto explica por que una pequena
diferencia inicial puede cambiar el momento de cierre y todo el camino
posterior de exposicion, flotante y drawdown. No demuestra que un retraso
de VM sea la causa del fill diferente ni verifica el tiempo de instalacion
del TP en broker; el caso sigue siendo diagnostico hasta tener esa fuente
y replicar el mecanismo en mas cestas.

El mecanismo ya se repite en 3148, 3168 y 3171: 9/9 piernas con enlace
real por ticket/deal muestran desplazamiento del TP observado igual al
del fill, y la salida real al TP observado. En 3168 una salida real se
adelanta 38.655 ms al tick de salida virtual; no cabe atribuir todos
los desfases a retrasos de VM. El probe de 3168 truncaba las transiciones
sombra (100/125), completadas desde el fragmento sombra local con hash e
IDs coincidentes, no desde un origen semanal integro. Los 37 cotejos de
nivel de estas tres cestas siguen sin `position_exists`. La siguiente
prueba debe extender enlace y orden causal a mas cestas y obtener evidencia
temporal de lectura/instalacion en broker, manteniendo aparte cobertura de
origen y paridad de riesgo/drawdown.

El contraste temporal v5/v2/v3 separa otro vector: en 3171 la primera
modificacion de TP aceptada llego 4.445 ms despues del tick de cierre
virtual, tras dos rechazos `10016`; la orden inicial no llevaba TP. En
las otras ocho piernas auditadas la respuesta aceptada fue anterior al
cierre virtual. El replay debe modelar proteccion pendiente/rechazada
como estado distinto de proteccion efectiva, pero estos logs aun no
sellan la lectura o instalacion exacta en servidor. La diferencia de TP
por fill y esta ventana temporal son mecanismos diferentes; no se debe
adjudicar todo el desfase a uno solo.

El gate nativo de motivo v6/v3/v4 confirma que 9/9 cierres fueron
activaciones de TP segun el deal MT5 (codigo 5), no meras salidas expertas
al mismo precio. La diferencia de fill explica el desplazamiento del
nivel TP observado en estas tres cestas; queda sin resolver la instalacion
temporal exacta, el resto de cierres y el efecto total sobre exposicion y
drawdown de la semana. No cambia la condicion de diagnostico local.

El comparador semanal v10 incorpora esos enlaces por ticket a la
atribucion monetaria sin cambiar las marcas: 132/320 marcas explicadas
pertenecen a 3148/3168/3171 y tienen identidad real de pierna verificada;
las otras 188 conservan pareja ordinal. En las 132, el residuo despues
del efecto de precio de entrada permanece 0,00 EUR bajo la misma
cotizacion/FX. Esto quita una hipotesis para esas tres cestas, pero no
prueba timing contrafactual, instalacion continua de SL/TP o drawdown
real. Las 167 marcas restantes y la cobertura de origen siguen abiertas.

v11 conserva esos resultados y hace invariante de salida los hashes de
las fuentes de los tres informes de identidad; v10 queda como antecedente.

v12 clasifica las 167 marcas de las tres cestas con ticket verificado:
132 comparten piernas abiertas y tienen residuo 0,00 EUR despues de
atribuir precio de entrada; 30 difieren en exposicion (18 en ventanas
de entrada, 12 en ventanas de salida) y 5 estan planas en ambos con
delta 0,00 EUR. Es una clasificacion de intervalos **a posteriori**,
no prediccion causal ni reconstruccion continua. El siguiente gate es
reproducir los tiempos/estados de proteccion y cierres sin conocer de
antemano el resultado y contrastar drawdown real entre marcas.

La revision del comparador v9 extiende el calculo condicionado a cierres
parciales cuando coinciden dinero realizado e identidades de piernas
abiertas/cerradas. Ahora atribuye 320/487 marcas (175 mas que v8), con
residuo 0,00 EUR en las 175 nuevas. Las 86 marcas de exposicion diferente,
77 de realizado diferente y 4 sin pareja de entrada siguen sin explicarse
por precio de entrada. Esto estrecha el diagnostico general: el siguiente
trabajo de paridad debe reconstruir causalmente apertura/cierre y proteccion
en esos grupos, ademas de completar cobertura del diario. No convierte
el simulador en certificado ni da una estimacion continua de drawdown MT5.

El diagnostico de rejilla v5 corrige la comparacion de drawdown entre
informe de todos los ticks y comparador de transiciones: conserva una
sola trayectoria de dinero nativo y reduce sus muestras de tres formas
(transiciones, transiciones mas deals, todos los ticks).
En los cinco controles con reloj directo y FX estrictamente causal,
omitir ticks subestima el drawdown modelado entre 0,84 y 26,34 EUR;
en `3168`, 37,20 EUR en transiciones, 39,31 EUR con limites de deal y
65,65 EUR completos. `3086` no tiene ninguna marca de transicion dentro de su
intervalo nativo abierto, de modo que su cifra muestreada no representa
el trayecto. Esto elimina una ambiguedad metodologica, pero sigue sin
haber una serie independiente y continua de equity MT5. Antes de
certificar el simulador hay que completar la cobertura de origen y
contrastar replay causal y riesgo real en una rejilla comun; no ajustar
politicas a las cifras muestreadas.

La comprobacion de cronologia TP v7/v4/v5 separa hora del tick y hora
de emision de la decision virtual. En 3171 el acuse TP fue 4.445 ms
posterior al tick usado por la sombra, pero 39 ms anterior a la emision
de `virtual_position_closed`; el tick llevaba 4.484 ms de antiguedad.
No se puede afirmar que el motor cerro antes de la aceptacion del TP
ni ubicar con precision la instalacion en servidor. El siguiente gate
temporal debe reconstruir decision, solicitud, intento, respuesta y
estado nativo en ese orden, con cobertura mas amplia que estas nueve
piernas. El comparador semanal v14 y la rejilla v5 conservan los mismos
resultados numericos con procedencia actualizada.

El comparador v14 desglosa la edad tick-emision por tipo de transicion
y evita tomar un cierre terminal con tick antiguo como retardo de una
orden. El runtime sombra usa lotes de historia y da prioridad al live;
la edad de sus eventos no equivale a latencia de la VM/broker medida en
las llamadas reales. Para admitir paridad temporal se deben cotejar
por separado el reloj de mercado, la hora de decision, el intento MT5,
su respuesta y los deals, sin sumar esperas de origen diferente.

La auditoria de solicitudes TP v1 identifica un desacople ejecutable
del control Gold: 9/9 ordenes iniciales verificadas pidieron `tp=null`,
mientras la sombra calcula `target_price` al abrir cada pierna virtual.
En 8/9 el acuse de modificacion llego antes del tick de cierre virtual;
en 3171 habia un pedido en vuelo que luego se rechazo y el primero
aceptado llego 4.445 ms despues del tick. Esto exige representar por
ticket la intencion de politica separada del estado confirmado/observado
de proteccion en el replay. No se debe modelar un retraso fijo ni asumir
que el acuse es el instante exacto de instalacion; el intervalo sin
observacion continua del broker debe quedar como incertidumbre. Aun
faltan trayectorias completas de origen y equity independiente para
certificar el riesgo real/virtual.

La revision de cinco probes historicos detecta una omision de
proyeccion: 56 snapshots tienen `position_exists` en el esquema de
origen, pero ninguno de los eventos extraidos conserva su valor.
El extractor local actual lo admite y tiene regresion de exportacion.
Recapturar con los mismos offsets y hash de ventana puede recuperar
evidencia de existencia/TP sin agregar telemetria al bot; los valores
siguen desconocidos hasta esa lectura y no cambian el gate de equity
continuo. La recaptura remota se reserva para una ventana de baja
carga por la prioridad del bot live.
El verificador de reproyeccion ya esta implementado y probado; exige
el mismo hash de ventana y los mismos registros y deja `true`, `false`
y `null` separados. **Todavia no hay recaptura remota admitida**.
El campo recuperado solo puede subir el cotejo acotado de niveles si
el valor real es `true` y la pierna sigue abierta. El gate forward
integral requiere tambien datos completos de peticion/respuesta,
revision e identidad nativa por ticket; sigue pendiente aunque el
piloto de snapshots mejore.

El diagnostico local de toques TP anteriores al primer acuse aceptado
(`tp_preack_retained_touches_3148_3168_3171_v1.json`) cruza las nueve
piernas con ticks XAUUSD ligados por hash y ancla directa de reloj. En
8/9 no hubo toque del TP nativo antes del acuse; en `3171` hubo dos.
El primero llego 66 ms despues del tick de cierre virtual y durante una
solicitud que luego fue rechazada con `10016`. Esto confirma que la
ventana de proteccion pendiente puede ser material, ademas de la
diferencia de precio de entrada/objetivo. No prueba el instante de
instalacion del TP en servidor ni convierte el control en paridad de
recorrido. El siguiente gate de mecanismo es reproducir por ticket la
secuencia peticion-rechazo-aceptacion y cotejarla con estado nativo
observado, sin usar el resultado posterior para decidir el pasado.

El cruce posterior al acuse (`tp_response_to_native_exit_3148_3168_3171_v1.json`)
encuentra un toque Bid del TP nativo antes de cada uno de los nueve
deals de salida. Siete deals llegan 2-19 ms despues del primer toque;
los otros dos, ambos de `3168`, llegan 476 y 1.614 ms despues y tienen
varios toques intermedios. Por tanto, el replay condicionado no puede
equiparar sin mas primer tick favorable con hora de cierre nativo. El
tiempo de exposicion posterior al toque debe seguir hasta el deal o
quedar como intervalo incierto en una simulacion prospectiva; este
diagnostico tampoco prueba drawdown monetario integral.

Un estado de TP condicionado por recibos y evaluado **solo con eventos
ya conocidos en cada tick** queda probado en las nueve piernas
(`tp_client_receipt_state_3148_3168_3171_v1.json`). `3171` aparece
pendiente/desconocido en el toque preacuse; los nueve toques posteriores
ven un TP aceptado por el cliente, cuatro con otro intento del mismo
nivel en vuelo. Este estado no sustituye la instalacion observada en
MT5; el comparador integral debe conservar ese gate y usar el deal
nativo para la hora de salida observada, no para anticipar una decision.

El contraste de riesgo condicionado
(`tp_first_touch_risk_3148_3168_3171_v1.json`) conserva fills, precios
de salida y dinero nativo y adelanta **solo** cada cierre al primer
toque posterior al acuse. Su lado observado reproduce exactamente la
referencia semanal congelada para las tres cestas; el hipotetico
difiere en 3, 35 y 1 muestras de la rejilla comun, con diferencia
maxima de total 0,13 EUR, 0,76 EUR y 0,00 EUR respectivamente. El
ultimo caso cambia exposicion aun con total identico. El beneficio
final y drawdown maximo coinciden en las tres, demostrando que esos
agregados no certifican fidelidad del recorrido. El siguiente gate es
aplicar el mismo cotejo a ventanas completas, con estados broker
observados y equity independiente; estos tres controles no bastan.

La auditoria de `mt5_account_connected` no da aun un control
independiente de flotante admisible: 15 capturas con posiciones
abiertas proceden de dias sin ancla directa y el `account_info()` se
leyo antes del timestamp del evento sin hora de adquisicion registrada.
Las tres coincidencias al centimo son diagnosticas, no paridad temporal.
En cambio, los probes acotados tienen 51 snapshots Gold y cuatro Dubai
con `price_open`, `price_current` e identidad en el esquema del diario;
`profit` quedo proyectado en los 51 Gold y tres Dubai. La proyeccion
vieja descarto los precios y la identidad. El extractor local y el
verificador v2 permiten
recapturar **los mismos bytes/offsets** y recuperar esos campos sin
alterar los anteriores; 79 pruebas focalizadas pasan, incluido un
control de reproyeccion sobre el mismo diario sintetico. Las cinco
recapturas remotas acotadas constan abajo; ese control por pierna no
sustituye una serie continua de equity de cuenta.

Antes de la recaptura, un control de intervalo con los campos ya
conservados (`native_position_profit_prior_quote_5probes_v1.json`)
contrasta los 56 snapshots contra valoraciones Bid/Ask y EURUSD
**anteriores** al evento, en una ventana declarada de 5 s. Los 54
snapshots con `profit` nativo tienen alguna valoracion exacta al
centimo; los dos sin beneficio quedan bloqueados. En 48/54 el tick
coincidente mas reciente tiene como maximo 1 s y en 54/54, 2 s.
Esto respalda la formula de dinero abierto en esos controles, pero
no identifica el tick usado por `positions_get`, no compara una serie
independiente de equity y no certifica el drawdown continuo.

El 23-09 se recapturaron los mismos 10,26 MB del probe `3171` con
detalles nativos de dos snapshots. La verificacion v3 conserva hash,
offsets, inventario, orden y todos los campos anteriores, separando la
expansion permitida del esquema (269 eventos) de los precios restaurados.
En ambas lecturas, `price_open` corresponde al deal y un mismo tick
causal anterior reproduce `price_current` y `profit` (130 y 211 ms
antes del evento). Es evidencia de valoracion por pierna, no equity
continua ni validacion de la VM. Despues se recapturaron los otros
cuatro probes, todos con hash de ventana, offsets, inventario y valores
antiguos iguales. El informe conjunto
`native_position_mark_profit_5probes_v1.json` reproduce en 54/54
snapshots abiertos `price_current` y `profit` con un mismo tick causal;
dos lecturas cerradas siguen bloqueadas sin beneficio nativo. La
cobertura semanal 2/40, la comparacion integral de trayectorias y la
equity independiente permanecen abiertos.

Indice de origen: se verifico el primer bloque de 64 MB del prefijo
congelado de 9,9 GB, en lectura de prioridad baja (4,7 s). El bloque
contiene 122.499 eventos, fechas de junio a agosto y dos retrocesos
temporales; no avala busqueda por offsets aproximados. Quedan 154
bloques y el indice completo se pospone a baja carga. No se ha
publicado codigo ni cambiado la politica live.

Se anadio localmente, sin publicar, un intervalo UTC y duracion monotona
al `mt5_position_snapshot` existente de la cola: rodea la llamada
`positions_get` ya realizada, incluso si devuelve vacio o falla, sin
otra llamada MT5 ni otro evento. El extractor y verificador futuros
conservan y validan ese intervalo. Las versiones que originaron las
cinco recapturas quedaron archivadas por hash en `runtime_data`.
Suite global: 7.761 aprobadas, una advertencia de pandas no relacionada.
Esto mejora la observabilidad prospectiva del precio/beneficio nativo,
pero la equity independiente y el replay integral siguen sin certificar.

Siguiente contraste de recorrido: el informe local
`shadow_snapshot_exposure_3148_3168_3171_v2.json` cruza 51 lecturas
nativas Gold con el control sombra, con reloj, estados y entradas ligados
por hash. Hay 18 igualdades estables de volumen y tres diferencias
estables en `3148` (0,10 real frente a 0,07 virtual); las otras 30
lecturas permanecen bloqueadas por proximidad de cambios, estado viejo
u orden temporal incierto. Se corrigio un falso positivo del auditor:
dos transiciones virtuales se emitieron despues de la lectura aunque
su tick ya era anterior, por lo que no se pueden contar como igualdad.
La diferencia de `3148` incluye entrada virtual posterior y cierre
virtual anterior a la salida real; la causa general de ambos desfases
queda por establecer. El siguiente gate sigue siendo enlazar decisiones,
ticks y recibos de entrada/salida con ventanas completas y contrastar
exposicion, dinero y drawdown independientes. No hay paridad integral
certificada ni cambio de politica/despliegue.

El control general de ancla Gold (`gold_verified_ladder_anchor_3signals_v2.json`)
enlaza solicitudes, resultados y fills de todas las entradas con
identidad verificada disponibles. De 23 cestas Gold reales, siete tienen
control sombra comparable, tres vinculacion completa y 16 ninguna
comparacion en el fragmento actual. En `3148` el fill inicial nativo
supera al virtual en 0,44 XAUUSD; por la regla fija de escalera, las
solicitudes nativas posteriores cruzan sus niveles, pero no los
virtuales al mismo precio. `3168`, con igual ancla inicial, funciona
como control negativo y sus cuatro solicitudes posteriores cruzan
ambos niveles. `3171` solo tiene primera entrada. Esto identifica una
**cadena causal de entrada** en el subconjunto probado; no avala la
salida, el drawdown ni una conclusion para las 20 cestas restantes.
Para ampliar evidencia hacen falta vinculacion de entradas en los cuatro
controles ordinales y material de origen de las 16 cestas sin control;
las lecturas remotas extensas se reservan para baja carga. Un replay
condicionado a fills observados puede probar igualdad de decisiones,
pero no debe mezclarse con una simulacion contrafactual independiente:
esta necesita modelar ejecucion, retrasos y sensibilidad antes de
validar estrategias nuevas.

El control de doble reloj (`shadow_first_fill_dual_clock_12_v1.json`)
verifica 12/40 primeros fills con dos informes independientes: cinco
Dubai y siete Gold. En los doce, el `virtual_fill` se encolo despues
del fill MT5 aunque usa un tick anterior. El dato no elimina el valor
de un backtest sobre ticks, pero impide presentar esas sombras como
ejecuciones simultaneas a las observadas. El modelo de comparacion debe
tener dos recorridos separados: uno **condicionado a los fills reales**
para probar decisiones y contabilidad observada; otro independiente,
con latencia y precios de ejecucion declarados y sensibilidad, para
evaluar alternativas. Ambos deben cotejar exposicion, flotante,
drawdown, cierres y recuperacion. Quedan 28 primeros fills sin este
contraste y la serie integral de equity sin certificar. No se ha
publicado ni desplegado este trabajo.

Primera comparacion de recorrido tick a tick sobre cestas Gold con
identidad de entradas verificada (`verified_gold_full_tick_path_3signals_v1.json`):
`3148`, `3168` y `3171` reproducen primero por hash sus trayectorias
nativas congeladas y luego contrastan las posiciones virtuales
registradas en la misma rejilla XAUUSD/EURUSD. Los netos finales
coinciden (8,28; 20,09; 1,75 EUR), pero la exposicion difiere en
541/1.403, 449/4.887 y 186/192 marcas. Los drawdowns modelados son
24,43 frente a 21,30; 65,65 frente a 65,95; y 4,22 frente a 0,73 EUR
(nativo frente a sombra). Esto confirma que un neto igual puede ocultar
un recorrido materialmente distinto. No valida una simulacion nueva:
reconstruye estados sombra ya producidos y flotante nativo modelado,
sin equity MT5 independiente. Siguiente paso: ampliar las identidades
y ventanas de origen cuando la VM este en baja carga y, por separado,
pasar la secuencia completa de mensajes/ticks por el motor independiente
con ejecucion causal, contrastando decisiones y trayectorias sin inyectar
los resultados nativos futuros.

Ampliacion de cobertura Gold sin ajustar una entrada individual: el informe
`gold_broker_comment_leg_binding_7controls_v1.json` (SHA256
`D3F1B2975E1986D7D0AE975CB5D41361E5B33FE8A3DF0E330A8B60251D8ED112`)
vincula las piernas de las siete cestas con control sombra a deals nativos
mediante comentario del broker, ticket, hora, volumen y precio. Tres conservan
ademas la traza verificada de solicitudes/resultados del bot; las otras
cuatro **no** adquieren por ello esa verificacion. De las 23 cestas Gold
nativas, 16 siguen sin control comparable. El informe
`gold_broker_bound_full_tick_path_7controls_v2.json` (SHA256
`BE8B591879F9105A01DAEB16BF38ECB0A7FE4A401E908C687D559AFCBF6737C5`)
compara cuatro cestas con FX causal y reloj directo: `2977`, `3148`, `3168`
y `3171`; las cuatro tienen diferencias de recorrido. En `2977`, pese a
igual neto final de 4,33 EUR, la exposicion difiere en 126/7.424 marcas y
el drawdown modelado es 3,88 EUR nativo frente a 3,29 EUR sombra. `3086`,
`3096` y `3156` quedan excluidas explicitamente de esta comparacion porque
solo tienen brackets FX retrospectivos. Esta ampliacion no verifica salidas
del bot, equity MT5 independiente ni replay autonomo desde Telegram. Proximo
bloque: recuperar rutas FX estrictas o clasificar su ausencia, ampliar
controles al resto de la muestra y contrastar decisiones/recibos y trayectoria
en un replay causal independiente. Ninguna politica live ni VM fue modificada.

Sin rebajar el limite de antiguedad FX de 5 s, el informe sucesor
`gold_broker_bound_full_tick_path_7controls_v3.json` (SHA256
`1DE61A5BF61827521FE3D0C0ADB4569C4C1CD8CB9FD3B46D729A0A1FE751B5CF`)
incluye tambien la evidencia **parcial** de las otras tres cestas:
`3086` tiene 15 marcas de riesgo desconocidas de 4.053 y 3.162 diferencias
de exposicion; `3096`, 60/17.096 desconocidas y 10.781 diferencias de
exposicion; `3156`, 7/5.587 y 155. Las diferencias de exposicion y dinero
realizado se pueden contar aun donde FX impide valorar el flotante. En esas
tres no se publica delta de drawdown ni se considera comparable la ruta
integral de riesgo. Por tanto, 7/7 controles aportan alguna evidencia de
recorrido, solo 4/7 riesgo causal completo y 16/23 cestas Gold nativas aun
carecen de control sombra comparable. El v2 permanece como artefacto
historico mas restrictivo; el v3 no sustituye la necesidad de replay causal
independiente ni de equity MT5 observada.

Preparacion del replay independiente de la semana 14-18/09: el fragmento
`shadow_week_20260919.jsonl.gz` conserva 45 eventos `signal_received` en la
ventana, 44 IDs unicos: los 40 con cesta MT5 y cuatro Gold recibidos sin
cesta nativa (`3012`, `3075`, `3108`, `3188`). Esos cuatro tambien deben
quedar en el denominador del replay; no son descartes para encajar en un
limite de 40. El fragmento contiene **cero** `telegram_raw` porque su filtro de
extraccion no los incluyo. El `trade_events.jsonl` local termina sus eventos
crudos el 31/08; la exportacion local mas reciente inspeccionada es de
canal 1 y su ultimo mensaje publicado es del 13/09. No se puede presentar
el fragmento de senales ya interpretadas como
entrada independiente del parser. El extractor de solo lectura
`tools/collect_vm_raw_messages.py` queda preparado y probado (45 pruebas
relacionadas): al ejecutarse sobre el intervalo byte congelado
`[3300001656, 7078987060)` verificara el SHA256 completo
`77301defd86751cd2f25c5e28b5020dffdc2e244b3252bf55de04af1a4d6914b`,
seleccionara solo campos causales `telegram_raw` y no escribira en la VM.
**No se ha ejecutado remotamente** mientras el mercado esta abierto; el
intervalo byte tampoco prueba por si solo cobertura temporal exhaustiva.

El protocolo `raw_message_control_diagnostic_v2` existente no admite sin
adaptacion el nuevo formato de capturas semanales y limita la cohorte a
40 senales. Los 44 IDs de `signal_received` no determinan exactamente el
numero que compilara el parser crudo, pero impiden asumir que cabran todos.
Tras obtener la entrada cruda se debe verificar cobertura de IDs y revisiones
contra el universo semanal, fijar un presupuesto nuevo sin truncar senales,
ejecutar los tres motores sin fills nativos como input y comparar decisiones
y trayectorias bajo escenarios de ejecucion predeclarados. La coleccion de
mensajes no autoriza por si sola replay, certificacion o cambio live.

La compuerta local `tools/audit_week_causal_input_readiness.py` verifica,
una vez exista esa extraccion, que los mensajes crudos compilen por si
mismos los IDs esperados por `signal_received`, sin convertir estos ultimos
en entradas del motor. Preserva IDs crudos adicionales y bloquea una cobertura
aparente si faltan IDs recibidos o el control esta vacio. El auditor vincula
por hash las dos extracciones al mismo intervalo byte y **no** afirma que
fuera del intervalo no hubiera mensajes. Probado con casos sinteticos y
45 pruebas relacionadas aprobadas; pendiente ejecutarlo con mensajes reales.
Ademas, el ancla de reloj independiente de la cinta semanal existe para los
dias 15, 17 y 18/09, pero no para 14 y 16/09. Esos dos dias pueden aportar
diagnostico bajo la hipotesis de offset declarada, no paridad temporal
certificada. Ningun control debe separarse o descartarse en silencio para
eludir estas ausencias.

El ejecutor offline semanal `tools/run_week_causal_controls.py` ya prepara
un protocolo inmutable a partir de la admision de mensajes crudos, los cinco
archivos diarios XAUUSD/EURUSD ligados por hash al ancla de ticks y el contrato
monetario del broker. No recibe deals, posiciones ni sombras como datos de
ejecucion. Congela las dos politicas de control codificadas localmente,
once escenarios de ejecucion, presupuesto de 64 senales y 1.800 s por lote
de ocho senales; rechaza el universo completo si supera el limite, sin
truncarlo. El modo `run` verifica de nuevo fuentes y
codigo, ejecuta motor escalar/rapido/oraculo, conserva sus resultados y
desacuerdos, y distingue entradas bloqueadas de cierres `data_end` censurados.
Cada lote usa la cinta completa desde su senal, no una ventana acortada.
`assemble` rechaza lotes ausentes, duplicados o con una matriz incompleta de
senales/escenarios, y vuelve a comprobar fuentes congeladas antes de producir
el informe semanal.
Registra el ultimo tick real de la cinta, dias sin ancla directa, diagnosticos
de gramatica y el hecho de que la fuente byte no prueba la semana completa.
La prueba integrada con dos senales sinteticas y once escenarios, mas las
regresiones de entrada y ensamblaje, suman 53 pruebas relacionadas aprobadas.
**No se ha ejecutado con mensajes reales de 14-18/09** ni se han comparado sus salidas
con MT5; por tanto no valida el simulador, la rentabilidad o la VM.
El protocolo declara comision hipotetica cero y no modela swap, rollover ni
margen de cuenta. Igualdad entre motores no verifica estos costes ni la
equity independiente de MT5.
La carga local real de los diez archivos diarios, con los hashes del ancla
verificados, termino correctamente: 3.354.354 ticks XAUUSD y 634.256 EURUSD,
ambos bajo el limite de cinco millones por simbolo. La cinta XAUUSD
normalizada cubre desde el 13/09 22:00:00.694 hasta el 18/09 20:56:59.929 UTC;
EURUSD, desde el 13/09 21:02:00.031 hasta el 18/09 20:56:59.129 UTC.
El fin de estas cintas es anterior al corte de mensajes del 19/09 00:00 UTC:
los cierres forzados en `data_end` se conservan como censurados, no como
resultado de broker. Esta comprobacion fue solo lectura local, sin VM.

Revision de alcance del control semanal: `run_week_causal_controls.py` produce
trayectorias **por senal aislada**, con once escenarios de ejecucion por cada
senal, pero todavia no reconstruye el drawdown ni la exposicion conjunta de
una cuenta con senales solapadas. El protocolo y los informes de lote/semana
declaran `risk_scope=isolated_signal_only` y
`shared_account_equity_reconstructed=false`; el ensamblado verifica esa
frontera y sus contadores. `coverage_status` da prioridad a desacuerdos de
motores y bloqueos, mientras `censored_row_count` conserva cierres por fin de
cinta incluso si una fila tambien esta bloqueada. Ninguna coincidencia de
resultado por senal debe usarse como prueba de riesgo de cuenta. El siguiente
bloque, tras recuperar los mensajes crudos, es comparar decisiones/recorridos
por toda la cohorte y reconstruir por separado el riesgo conjunto con la
cinta canonica, antes de hablar de paridad real. La VM sigue sin tocarse.

La fase offline `tools/assess_week_causal_portfolio.py` ya une los resultados
semanales completos con la reconstruccion de equity conjunta existente.
Verifica hashes de protocolo, lotes y cintas; recompila la cohorte cruda,
reconstruye las rutas de ticks hasta los cierres y aplica la misma hipotesis
de latencia para medir drawdown y volumen simultaneo en la cuenta. Un
escenario con una senal bloqueada por una causa distinta al fin limpio de
cinta **no** recibe un drawdown conjunto: enumera tanto la senal excluida como
las validas que quedaron sin evaluar conjuntamente. La prueba sintetica de
dos posiciones solapadas da
0 EUR netos y 4 EUR de drawdown, mostrando por que P/L final no basta. La
integracion protocolo-lote-ensamblado-cuenta conserva los once escenarios;
cuatro tienen cierre hipotetico completo y cinco conservan equity marcada
solo hasta el ultimo tick; dos quedan bloqueados por falta de ticks futuros
en esa fixture. El neto de los cinco marcados es una
liquidacion terminal hipotetica, **no** beneficio realizado ni resultado de
broker. Esta fase aun no ha corrido con los mensajes reales, no incluye
costes/rollover/margen observados y no compara la equity de MT5.
La fase de cuenta vuelve a ligar las filas ensambladas a los lotes originales
por hash y contenido, y verifica la huella de la politica por senal antes de
reconstruir dinero y riesgo. Tambien declara cuantas de las once hipotesis
logran cierre completo, marca terminal censurada o quedan bloqueadas. El
extractor remoto
ahora baja prioridad de CPU tambien en Linux; ello no elimina la carga de
lectura del journal, por lo que la extraccion de 3,78 GB permanece diferida
fuera de la sesion activa del mercado. Verificacion local previa: 80 pruebas
relacionadas aprobadas; sin commit, push ni cambio en la VM.

Desglose transversal de los siete controles Gold existentes, sin seleccionar
uno como objetivo: el informe local
`gold_broker_bound_full_tick_path_7controls_v4.json` (SHA256
`047E1CEA542433F25A705E86B9E512578317CFDC78F91876DCD806612A87D4C6`)
conserva cuatro rutas comparables y tres parciales por FX causal. En las
cuatro completas, el neto final coincide, pero el drawdown y la exposicion
no: las diferencias de exposicion afectan respectivamente 126, 541, 449 y
186 marcas para 2977, 3148, 3168 y 3171. Ademas, 657, 860, 4.002 y 4
marcas presentan distinto flotante **con el mismo estado de posiciones,
volumen y realizado**. Los deals ligados muestran precios de entrada nativos
y virtuales distintos en esas cestas. Eso impide explicar todo el desacuerdo
solo como cambio de momento de entrada; tambien hay un componente de precio
de ejecucion. Las marcas de ticks consecutivos no son observaciones
independientes, y las sombras grabadas no son un replay desde Telegram ni
prueban que MT5 hubiese llenado una orden contrafactual igual.

Antes de ejecutar el nuevo replay con mensajes crudos se congelo una
sensibilidad diagnostica de once hipotesis: las cinco latencias cortas
anteriores, dos costes adversos simetricos de entrada/salida de 0,10 y 0,50
unidades de precio XAUUSD, dos fallos de observacion de 60 y 180 segundos,
y dos demoras solicitud-llenado de 60 y 180 segundos. Los cuatro ultimos
son pruebas de fallo, no retrasos operativos aceptables. Los
valores no se ajustaron para igualar los siete controles, no estiman una
distribucion del broker y esta misma semana sigue siendo desarrollo
retrospectivo, nunca OOS intacto. El control de cuenta conserva los once
escenarios y bloquea resultados sin ruta valorable; un cierre `data_end` no
se convierte en un cierre real aunque sirva para medir flotante y drawdown
hasta el ultimo tick.
La verificacion ampliada despues de este desglose y los nuevos escenarios:
88 pruebas relacionadas aprobadas. Ningun control modifica configuracion live.

La fase de cuenta admite ahora **solo** una censura terminal estricta: el
unico bloqueo por senal debe ser `path_ended_before_strategy_exit` y debe
existir un cierre sintetico `data_end` verificable sobre la cinta. En ese
caso conserva flotante, volumen y drawdown hasta el ultimo tick, pero llama
al neto terminal `hypothetical_terminal_liquidation_not_realized`; no lo
trata como beneficio realizado ni como cierre MT5. Cualquier bloqueo extra,
incluido FX causal incompleto, sigue impidiendo la ruta conjunta. La prueba
integrada sintetica conserva cuatro escenarios con cierre completo, cinco
marcados solo hasta fin de datos y dos bloqueados; la etiqueta global es `partial_censored`,
no una semana cerrada. 89 pruebas relacionadas aprobadas. Sigue sin haber
entrada cruda real de 14-18/09, y la herramienta de UI de Telegram Desktop
no arranco en este equipo; la lectura diferida del journal de VM continua
siendo necesaria para la comparacion historica real.

Preflight remoto ligero 23/09, sin leer el journal: SSH a la VM de LAN con
la identidad existente devolvio `exit=0`, Python remoto es 3.11.9, el archivo
fuente mide 10.243.960.045 bytes (supera el extremo congelado 7.078.987.060)
y ambos extremos del intervalo son saltos de linea. **Esto no verifica el
SHA256 de los 3,78 GB** ni la cobertura temporal; la extraccion completa
permanece pendiente fuera de mercado. Los cuatro `material_segment_*_v2`
locales cubren solo ventanas concretas del 17/09 y proyectan 18 marcas
`telegram_raw` (con duplicados), pero omiten `text`, `channel` y `date_utc`.
No son entradas causales suficientes para compilar la semana. Tampoco hay
exportacion local de Telegram posterior al 13/09 ni un lector historico
autenticado y aislado ya implementado en el proyecto.

Piloto acotado de la extraccion real: se leyo **solo** el intervalo byte
`[6125878156, 6137624254)` (11.746.098 bytes) ya indexado para un tramo
del 17/09, con proceso remoto de prioridad baja, sin escribir en la VM.
Su SHA256 coincidio con el segmento fuente
`d9c81e089ff7b8957a01c17bf56cb497e70494dde99115ae773b13685b1799e3`;
el JSON local `raw_pilot_179.json` tiene SHA256
`3e5216ed9304735c7c6d53bebdc40dc88ca969685636121426a826dabb57c780`.
Se recuperaron 27 eventos `telegram_raw` de canal 2, 16 revisiones unicas.
El parser crudo compilo `canal2_3086` sin diagnosticos; las 11 marcas crudas
proyectadas previamente en `material_segment_179_v2.json` estan presentes
por revision y tiempo en la extraccion. Esto prueba transporte, hash y un
caso real de parser, **no** cobertura de las 44 senales ni validez del replay
monetario/riesgo. El escaneo completo de 3,78 GB continua diferido.

La misma entrada cruda del piloto se ejecuto localmente, sin deals ni sombras
como input, sobre 928.399 ticks posteriores y las cinco cintas diarias
verificadas. En los **once** escenarios congelados coinciden motor escalar,
rapido y oraculo; hay escenarios con 0, 1, 2 o 3 entradas, de modo que la
identidad de decisiones importa tanto como el neto. Esta prueba de tuberia
con un solo trigger no selecciona politica ni acredita paridad semanal.

La separacion de latencias se apoya en `native_week_first_calls_v2.json`
(SHA256 `61F0937D17D2CB1B8FD6BF78736BF03850DEC1671066E7AD98D5BFFC046B8268`):
de 23 primeras ordenes Gold, 11 llamadas MT5 superan 1 s, cinco superan
10 s y dos superan 60 s. Entre las 14 con reloj directo, dos superan 10 s.
Para `canal2_3086`, recepcion-a-solicitud son 67.212 ms y la llamada
`order_send` dura 67.547 ms; el primer tramo puede incluir espera legitima
de la regla de entrada y no prueba por si solo atasco de VM. El segundo
tramo si es una demora medida de la llamada. Por ello el protocolo separa
demora de observacion y demora solicitud-llenado, ambas como sensibilidad
hipotetica y sin tratar las colas reales del broker como ya reproducidas.
89 pruebas relacionadas aprobadas tras agregar los dos escenarios de
llenado; no hay cambio publicado ni configuracion live alterada.

Control transversal de cuenta antes de interpretar el drawdown semanal:
`native_account_snapshots_v2.json` (SHA256
`20840771968B09DAE6BB7A1BDD78FC44FE7499C6F0131B516E210EB57D0756E8`)
contiene 33 capturas. Las 14 capturas con reloj directo son **todas flat**;
las 15 con posiciones abiertas carecen de ancla directa (11 diferencias
modelo-cuenta, tres coincidencias y una sin cotizacion previa reciente).
Otras cuatro son flat fuera de la ventana de precios. Por tanto, estas
capturas no verifican el flotante ni el drawdown real de una posicion con
reloj admitido: tampoco prueban por si solas un fallo general del modelo
monetario, pues la hora de adquisicion de `account_info` no se registro.
El riesgo semanal nativo por ticks cubre las 40 cestas cerradas, pero solo
16 tienen FX causal estricto; las otras rutas emplean cobertura retrospectiva
o quedan fuera de esa categoria. Sigue siendo necesario comparar la cohorte
completa desde Telegram y obtener una referencia independiente de cuenta
durante posiciones abiertas antes de afirmar paridad de drawdown live.

Nuevo control offline de cohorte `tools/compare_week_causal_sequences.py`:
une el protocolo/lotes semanales congelados con los deals nativos conciliados,
sin usar fills observados para ejecutar el replay. Enlaza cada pierna real
por comentario, ticket y posicion; compara por cada una de las once hipotesis
entrada, salida, tiempo, precio, volumen, dinero y mecanismo. Conserva una
fila por **cada senal cruda y escenario** y anade como bloqueadas las cestas
nativas ausentes del universo crudo. Una senal sin cesta nativa solo se compara
como **sin fill XAUUSD** cuando el extracto de deals cubre la ventana con margen
de reloj, coincide la cuenta y todos los deals XAUUSD estan conciliados por
ticket y posicion. Esto no demuestra que no hubiera intento o decision de
orden. Si falta esa cobertura, queda bloqueada; un cierre sintetico en fin de cinta
no se compara como cierre broker. El informe es inmutable y liga hashes de
protocolo, lotes, fuentes nativas y codigo del comparador. El enlace nativo
local, aun sin replay semanal, resolvio 115 posiciones en 40 cestas;
cuatro pruebas nuevas y 93 pruebas relacionadas en total aprobaron.
**No** se ha ejecutado la matriz semanal por falta del extracto crudo completo,
ni este comparador de eventos certifica exposicion, flotante, drawdown o
equity compartida. Ese contraste de trayectorias sigue siendo una fase
separada y obligatoria.

La siguiente pieza offline `tools/compare_week_causal_risk.py` consume el
informe de secuencias y las cintas XAUUSD/EURUSD congeladas. Corre en lotes
de hasta ocho senales y ensambla solo si todos los lotes/escenarios y hashes
coinciden. Para cada ruta cerrada compara deals nativos y replay independiente
en la **misma** malla de ticks mas limites de eventos: exposicion, realizado,
flotante, drawdown por cesta, primera divergencia y muestras no valorables.
No convierte un cierre `data_end` en broker, ni un FX viejo en beneficio
conocido, ni elimina senales bloqueadas. El mayor intervalo de las 40 cestas
nativas tiene 130.803 ticks XAUUSD, dentro del limite por caso de 200.000;
esto es factibilidad de entrada, no prueba de una ejecucion semanal.
Cuatro pruebas nuevas comprueban diferencia de precio con igual exposicion,
FX causal insuficiente, posicion abierta y matriz de lotes incompleta;
97 pruebas relacionadas aprobaron. Aun faltan el extracto crudo completo,
la ejecucion real de la matriz 44 x 11 y la comparacion de **cuenta conjunta**
con evidencia independiente de equity. Solo esta ultima puede contrastar
drawdown live, pues la ruta de deals+ticks es una reconstruccion modelada.

Smoke transversal sin replay contrafactual: se compararon las 40 cestas
nativas **consigo mismas** mediante el mismo comparador de riesgo y las
cintas semanales ligadas al ancla, sin leer la VM. Las 115 posiciones
produjeron 809.580 marcas comunes (ticks mas limites de eventos). Ninguna
ruta identidad presento diferencias: 16/40 finalizaron como
`exact_sampled_path_only` y 24/40 como `blocked`, todas estas ultimas por
`stale_conversion_quote`. Una ruta bloqueada puede tener muestras identicas
en las partes conocidas, pero no recibe drawdown monetario completo. Este
control prueba que el comparador no crea discrepancias espurias con entradas
identicas; **no** prueba aun la paridad de la simulacion independiente ni
autoriza ampliar artificialmente la edad del FX para hacer pasar las 24.

Primera reconstruccion conjunta offline con `research/account_risk_path.py`:
dos cohortes de eventos se valoran en la misma malla XAUUSD mas limites de
deal, con exposicion, realizado, flotante, equity modelada y primera
divergencia marca a marca. Es un calculo de cuenta **desde deals y ticks**,
no una muestra historica de `account_info` de MT5. La prueba sintetica de
dos senales solapadas conserva 0 EUR netos y 4 EUR de drawdown; hay pruebas
de precio de entrada distinto con igual volumen, cierre parcial, coste en
entrada, evento entre ticks, FX viejo y cohorte sin fills.

La primera prueba diferencial encontro 0,01 EUR de error en un cierre parcial:
el calculo nuevo redondeaba el flotante por cada porcion futura cerrada,
en vez de hacerlo una vez sobre el volumen **restante de la posicion**.
Se corrigio el mecanismo general de volumen, sin ajustar esa operacion, y
la prueba contra `reconstruct_risk` decimal ya coincide. Con las 40 cestas
reales, neto, drawdown y maximo volumen por cesta coinciden **40/40** con
`native_week_risk_path_v1.json` bajo el mismo contrato FX retrospectivo;
las 16 cestas de FX estrictamente causal tambien coinciden sin ese modo.

Al unir las 115 posiciones observadas, el neto realizado es -382,98 EUR,
conforme a `native_week_reconciled.json`; en 2.914.239 marcas la exposicion
simultanea maxima modelada es 0,26 lotes. Con FX causal estricto hay 3.129
marcas de dinero desconocido y **no** se admite drawdown completo. Con el
intervalo FX retrospectivo de 60 s congelado en `broker_money_contract_vm.json`,
3.131 marcas abiertas quedan etiquetadas con esa cobertura; la curva modelada queda
completa y su drawdown conjunto es **660,22 EUR**. Se usa siempre el precio
FX anterior: el siguiente tick solo prueba la longitud del intervalo.
Esta cifra **no** es drawdown observado de MT5, no valida el replay
independiente y no corrige dias sin ancla directa de reloj. Falta guardar
un informe inmutable con hashes de todas las fuentes, contrastar despues
la cohorte Telegram completa y obtener equity live independiente.

El informe inmutable `native_account_path_v1.json` ya existe localmente
(SHA256 `904fb3df1f50f31238e15718ccca73ec78da59c8bd15a5d00a36ec4083ae1443`).
`tools/audit_week_native_account_path.py` congela hashes de deals, cestas,
dinero, reloj, contrato, auditor por cesta, diez cintas diarias y codigo.
Repite ambos modos de FX y exige 40/40 coincidencias de neto, drawdown y
volumen maximo frente al auditor decimal previo antes de escribir. La
cobertura estricta permanece bloqueada con 3.129 marcas desconocidas;
la retrospectiva, etiquetada como tal, permite el drawdown **modelado** de
660,22 EUR en 2.914.239 marcas y 3.131 marcas abiertas etiquetadas con
FX retrospectivo. Solo 3.129 marcas daban dinero desconocido en el modo
estricto; las otras dos tenian flotante nulo. No hay equity MT5 independiente
ni replay Telegram comparado.

La prueba diferencial sintetica encontro y corrigio un error de 0,01 EUR
en cierre parcial: ahora el flotante se redondea por posicion con su volumen
restante, no por porciones de salida futuras. Una repeticion real confirmo
40/40 coincidencias de tres metricas contra el auditor existente. La suite
general volvio a correr tras separar dos contaminaciones de pytest: el
extractor remoto escribe JSON directo a stdout aunque `main.py` haya
modificado `print`, y los tests del replay simulan el proceso independiente
sin modulos live preimportados. Resultado final: **7.830 pruebas aprobadas**,
927 avisos; sin commit, push, despliegue ni cambio de VM.

El comparador semanal ya comprueba esa cobertura de fill ausente en el extracto
nativo congelado: las 44 senales recibidas del 14 al 19 de septiembre UTC
incluyen 40 cestas y cuatro IDs sin cesta (`canal2_3012`, `canal2_3075`,
`canal2_3108`, `canal2_3188`). Los 230 deals XAUUSD del extracto se concilian
con las 115 posiciones; consulta, captura, servidor y moneda cubren la ventana
con el margen de reloj congelado. Por tanto, esos cuatro IDs no tienen fill
XAUUSD en **ese extracto de cuenta**, aunque no sabemos aun si hubo intento
de orden. La prueba focal de secuencias/riesgo da 11/11 aprobadas.

`tools/compare_week_causal_account.py` prepara el siguiente control: una
trayectoria de cuenta **conjunta** para cada una de las once hipotesis,
incluyendo todas las senales y solapamientos. Si alguna fila no es comparable,
bloquea la cohorte completa, sin calcular una curva parcial que parezca semanal.
En una malla compartida compara exposicion, realizado, flotante y drawdown
modelados, con FX causal estricto y diagnostico retrospectivo separados. El
informe se liga a protocolo, secuencias, baseline nativo, cintas y codigo;
nunca lo presenta como equity MT5 independiente. Es una **agregacion de
replays por senal**: no reproduce aun decisiones condicionadas por margen,
limites de cuenta compartidos ni colas de ordenes. La prueba focal ampliada
da 23/23 aprobadas, incluida una pareja solapada con neto cero y drawdown
de 4 EUR, una entrada distinta, un caso sin fills y bloqueo por falta de
evidencia. **Aun no hay matriz semanal real 44 x 11**: falta extraer el
Telegram crudo completo, operacion aplazada fuera de horario activo por la
prioridad de latencia de la VM. No hay commit, push ni cambio en produccion.

Tras ampliar el comparador de secuencias, el hash de codigo del informe
`native_account_path_v1.json` quedo antiguo. Se regenero como
`runtime_data/week_native_ticks_20260923_v1/native_account_path_v2.json`
(SHA256 `943a04f39eded6f57d16635161f9fdf64ce49e5e1fb263c5cf35a3efb573fcf7`).
Los dos resultados de trayectoria, pruebas de cintas, hashes de datos y
40/40 metricas por cesta son identicos a v1; v2 es el baseline ligado al
codigo actual para la futura comparacion semanal.
La suite completa posterior aprobo **7.837 pruebas**, con 927 avisos y sin
fallos (573,80 s). Los cambios permanecen locales; no se ha alterado la VM.

Comprobacion local posterior del fragmento `shadow_week_20260919.jsonl.gz`:
en la ventana 14-19/09 UTC hay 45 eventos `signal_received` para 44 IDs.
Ninguna de las **17 senales de canal 1** conserva `raw_text` en ese resumen;
las otras 27 identidades con texto son de canal 2. El piloto crudo
`raw_pilot_179.json` contiene 27 filas `telegram_raw` para solo seis mensajes
de canal 2 (`3086`-`3091`). Ni los textos interpretados del resumen ni ese
piloto pueden sustituir la extraccion completa de Telegram del intervalo
congelado. La siguiente accion material sigue siendo capturar el crudo
completo con baja prioridad fuera de horas activas, comprobar la cobertura
44/44 por parser y revisiones, y entonces ejecutar la matriz 44 x 11 y su
comparacion de cuenta conjunta. Durante mercado abierto no se lanzo el
escaneo de 3,78 GB del journal de la VM.

El comparador de cuenta ahora reconstruye otra vez las piernas observadas
desde los deals y el ancla monetaria, y exige coincidencia evento por evento
en cada fila comparable, incluido el estado de ausencia de fill. Un hash
correcto de los archivos de entrada ya no basta si una fila de secuencias
declara un precio o volumen observado distinto. Una prueba de integracion
con fuentes sinteticas y las once hipotesis comprueba el enlace completo;
las siete pruebas focales del modulo pasan. Las fuentes reales del baseline
v2 se identifican de forma unica como deals, cestas, dinero y contrato.
El historial Telegram de canal 1 no pudo consultarse por UI: Computer Use
fallo al inicializar el kernel en dos intentos, antes de abrir una ventana.

Smoke real de identidad del **adaptador** de cuenta (sin replay Telegram):
se tomaron los 44 IDs `signal_received` de la ventana, se reconstruyeron
las 40 cestas desde `native_money_anchor_v2.json` y los deals, y se admitieron
como vacias las cuatro senales sin fill XAUUSD mediante la prueba exhaustiva
de deals. `verify_observed_rows` comprobo 44/44; al comparar cada ruta nativa
consigo misma sobre las cintas congeladas, ambos modos reprodujeron
**exactamente** `native_account_path_v2.json`, incluso sus resumenes completos:
2.914.239 marcas, FX causal estricto bloqueado por 3.129 marcas monetarias
desconocidas, y drawdown conjunto retrospectivo **modelado** de 660,22 EUR.
Esto valida la integracion del adaptador con la cohorte real y detectaria
errores de ensamblaje; no es un replay independiente ni prueba paridad live.

Revision transversal de los 12 controles de sombra comparados de
`week_shadow_control_path_v14.json` frente a
`vm_order_timing_12_v1.json`: los doce tienen ancla directa de reloj y
primer fill nativo observado. Diez tienen al menos una marca de mismas
piernas abiertas con efecto de precio de entrada atribuido y **residuo
monetario maximo de 0,00 EUR** en esas marcas; esto respalda la formula
monetaria ahi, no la ruta completa. Seis primeras entradas nativas distan
menos de 1 s de la virtual; las otras seis superan 1 s. En tres primeras
ordenes la llamada `order_send` medida supera 10 s (aprox. 19,3, 62,6 y
67,5 s), dos superan 60 s; las tres terminaron con `DONE` y fill nativo.
Por tanto, no es correcto tratar el tramo recepcion-solicitud como un atasco
uniforme de VM ni cargar un delay fijo sobre todas las rutas virtuales.
El tramo solicitud-fill anomalo exige revisar terminal, IPC, broker y salud
de la VM con evidencia remota actual antes de proponer una correccion live;
un timeout ciego de `order_send` podria duplicar operaciones inciertas.

Revision directa del comparador de cuenta (no revision independiente):
se encontro que `compare_account_paths` descartaba diferencias de dinero
**realizado** en marcas con FX demasiado viejo para valorar flotante.
Una regresion de dos cierres parciales con neto final identico reprodujo
el fallo; ahora `booked_difference_marks` y `money_difference_marks` incluyen
ese dinero conocido aunque `unknown_money_marks` siga bloqueando drawdown
completo. La primera divergencia muestra realizado de ambos lados aun cuando
equity sea desconocida. El baseline se regenero sin sobrescribir v1/v2 como
`runtime_data/week_native_ticks_20260923_v1/native_account_path_v3.json`
(SHA256 `da318c6831617f9bdbdfdfca5be5a718ed12857ba525f25a088c82c7b0bedb98`).
Datos, cintas y 40/40 cestas coinciden con v2; los resultados principales
siguen en -382,98 EUR netos, 3.129 marcas FX estrictas desconocidas y
660,22 EUR de drawdown conjunto retrospectivo modelado. Usar **v3** como
baseline ligado al codigo actual en la futura comparacion semanal.
Tras el cambio del motor compartido, `python -m pytest -q` aprobo
**7.842 pruebas**, con 927 avisos (568,64 s). El smoke identidad de la
cohorte real se repitio contra v3: 44/44 observaciones enlazadas, cuatro
sin fill XAUUSD, 2.914.239 marcas, 3.129 marcas FX estrictas desconocidas,
`booked_difference_marks=0` y el mismo drawdown retrospectivo modelado de
660,22 EUR. Ninguno de estos resultados demuestra paridad de decisiones,
fills alternativos o equity MT5 independiente. Sin commit, push ni cambio
en la VM.

Auditoria de interaccion entre senales: las posiciones nativas se agruparon
por los intervalos reales con volumen abierto, uniendo piernas solapadas de
una misma senal pero sin rellenar huecos entre cierres y reaperturas. En las
40 cestas de la semana hay **13 pares con solapamiento positivo en canal 2**
y hasta **cuatro senales simultaneas** en ese canal; canal 1 llego como
maximo a una. El listener vivo usa un lock de entrada por canal y, para
gestion suelta accionable sin reply, no atribuye a ciegas cuando hay varias
senales abiertas. El informe conjunto ahora guarda el inventario de pares
y deja `cross_signal_decision_parity_verified=false`. El replay aislado mas
la suma de trayectorias puede medir exposicion hipotetica, pero no certificar
las decisiones de gestion ni la cronologia de entrada en esos cruces.
En el fragmento local, las recepciones iniciales de los 13 pares estan
separadas entre 199,072 y 9.075,098 segundos; ninguna menos de 60 s y tres
menos de 300 s. Esto reduce la sospecha de colision del lock en **la primera
entrada**, pero no verifica DCA posteriores ni mensajes de gestion sin reply.
El fragmento `shadow_week` no contiene esos eventos de gestion, de modo que
atribuirles una divergencia real concreta exigira el journal crudo completo.

Control de plausibilidad horaria, **no** ancla independiente: se cruzaron las
40 primeras aperturas nativas con el `signal_received` local. Restando el
offset broker-UTC supuesto de 10.800 s, en los dias sin ancla directa el
14/09 hay 4 primeras aperturas entre 45,8 y 167,9 s tras la recepcion;
el 16/09 hay 9 entre 0,0 y 695,6 s. No aparecen aperturas causalmente
anteriores a la senal en esos dos dias. Esto respalda la plausibilidad del
desfase de tres horas a escala de minutos, pero usa outcomes observados,
no prueba el offset preciso ni debe calibrar el replay. Los dias 14 y 16
siguen marcados `no_direct_anchor` hasta disponer de evidencia independiente.

Se cerro tambien el enlace transversal de reloj y dinero en
`tools/compare_week_causal_account.py`: el protocolo semanal debe referirse
al **mismo archivo y SHA256** de ancla de reloj y contrato del broker que el
baseline nativo. Las etiquetas de ancla por dia se reconstruyen desde ese
archivo y se contrastan con el protocolo; el informe explicita
`clock_unanchored_days`. La prueba de integracion rechaza un hash de ancla
cambiado o un dia declarado como directo sin respaldo, sin escribir salida.

La comprobacion previa a la extraccion remota no pudo confirmar salud de la
VM: los destinos SSH publicos conocidos agotan `ConnectTimeout=5`, y la ruta
privada que responde rechaza la clave configurada. Esto **no** demuestra que
el bot este parado; impide verificar exposicion y latencia y, por tanto,
descarta iniciar ahora la lectura larga del journal. Se solicito al usuario
el destino o ruta de acceso vigente, sin pedir credenciales.

Control de orden intramilisegundo en la cinta local: entre los 230 deals
XAUUSD nativos, dos comparten `time_msc` exacto con un tick XAUUSD retenido
(una entrada y una salida), sin multiples ticks en ese milisegundo. La malla
de riesgo trata el evento en el primer mark de ese instante; el orden real
deal/tick dentro del milisegundo no queda probado por esos datos. Es una
incertidumbre puntual que debera comprobarse al contrastar trayectorias;
no explica por si sola el drawdown semanal ni se usa para ajustar fills.

Control de cobertura de sombra en el fragmento local
`runtime_data/weekly_review_20260919/shadow_week_20260919.jsonl.gz`, filtrando
eventos del 14 al 19/09/2026: hay 44 IDs distintos con `signal_received`,
pero `strategy_shadow_registered` solo para **14 IDs** (5 de canal 1 y 9 de
canal 2). Los otros 30 no pueden entrar en un denominador de comparacion
live/sombra basado en ese fragmento; la ausencia de registro ahi no prueba
que jamas se ejecutara sombra en otra fuente. En concreto, `canal2_3012` y
`canal2_3108` tienen recepcion y watch de la 555, pero no registro de sombra
en este extracto. Para `canal2_3075` y `canal2_3188`, si hay tres contratos
registrados: el control `gold_now_555_v1` permanece sin fill virtual y
termina `entry_cancelled/entry_expired`, coherente con la ausencia de fill
XAUUSD nativo en el extracto; las entradas virtuales pertenecen a las
**candidatas diferentes** `gold_now_b210_v1` y `gold_now_c490_v1`. No son
por si mismas un fallo de paridad de la 555. Este control de cobertura no
certifica las decisiones ni el riesgo de las 14 senales cubiertas; tampoco
sustituye el replay causal independiente de las 44 ni su trayectoria conjunta.

Actualizacion de acceso VM (24/09/2026): el bloqueo SSH anterior era un
diagnostico incompleto. La ruta Tailscale al destino privado registrado
responde y la clave local existente autentica con el usuario remoto `bot`;
los intentos previos usaban `josea`. Se verifico por SSH de solo lectura el
nombre de host, el arbol de procesos (supervisor, watcher y `main.py`),
aprox. 3,2 GB de RAM libre y 12,3 GB de disco libre. El journal remoto
mantiene eventos de `telegram_poll_coverage` recientes, pero esto no prueba
ausencia de delay ni estado de posiciones/ordenes. El fichero fuente del
slice congelado sigue en la VM y no existe copia local completa: el
`trade_events.jsonl` local se actualiza, pero sus ultimos eventos
`telegram_raw` son de agosto. La extraccion de 3,78 GB puede prepararse ya
sin pedir otra ruta al usuario; antes de ejecutarla con mercado activo hay
que preservar la latencia del bot y comprobar exposicion/ventana segura.

La ruta SSH se recupero y se ejecuto la extraccion de solo lectura el
24/09/2026, con `bot@` y la clave existente. Antes de leer, el heartbeat
declaraba exposicion plana y cola cero. El worker se modifico localmente
para exigir `PROCESS_MODE_BACKGROUND_BEGIN` en Windows antes del escaneo:
la prioridad CPU `0x4000` previa no rebajaba recursos de I/O. Las 22
pruebas focales de extraccion/readiness/runner pasaron, el modo Windows
se comprobo en un proceso real y un piloto de 11,7 MB reprodujo byte por
byte el archivo anterior de 27 mensajes. La lectura completa genero
`runtime_data/week_native_ticks_20260923_v1/raw_week_20260914_19_bg_v1.json`
(SHA256 `9548ca5ce3fdd27ebe5f488ac97639c7e5cc7001ae0f75c73a40d3828cfd63b0`):
1.958 eventos `telegram_raw`; SHA256 de los 3.778.985.404 bytes fuente
`77301defd86751cd2f25c5e28b5020dffdc2e244b3252bf55de04af1a4d6914b`,
identico al manifest congelado. Durante y despues de la lectura los
heartbeats consultados siguieron recientes, con cola cero y exposicion
plana. Eso no mide todos los delays de ordenes ni prueba ausencia de
impacto en momentos no observados. No se reinicio ni cambio el bot.

La admision causal real quedo **bloqueada**, no preparada para el replay
44 x 11. `raw_week_input_readiness_v1.json` encuentra 44 IDs recibidos,
65 IDs compilados, uno recibido que no compila (`canal1_22738`), 22
compilados que no figuran recibidos y 26 diagnosticos de semantica/raiz
o sticker desconocido. Los 22 extra son entregas atrasadas en tandas de
reconexion; la primera llegada dista entre 246,9 y 94.127,9 segundos
de la publicacion. Todos exceden el corte live por defecto de 120 s
para nuevas entradas y deben conservarse como **candidatos crudos
omitidos por antiguedad**, no eliminarse del denominador ni simularse como
entradas frescas. `canal1_22738` contiene un texto `BUY GOLD NOW` con TP
numericos y SL: el listener admite texto-only cuando no hay sticker
abierto, mientras `compile_signals` solo reconoce stickers de canal 1.
Resolver ese fallback exige reproducir causalmente el estado de senales
abiertas, no usar el hecho observado de que el bot abrio 22738 para
decidirlo en el simulador. El limite actual `MAX_SIGNALS=64` tambien
queda superado por los 65 candidatos crudos; revisar el presupuesto solo
tras separar candidatos stale/accionables. Las diferencias no se corrigen
retocando una entrada ni usando fills nativos como input.

Reparacion local del control de antiguedad: `compile_signals` acepta ahora
un limite explicito, y solo el replay semanal usa 120 s; la VM confirmo
que su `STRATEGY_ENTRY_MAX_TG_DELAY_S` actual es 120. El candidato atrasado
se conserva como diagnostico `stale_entry_candidate`, sin entrar en la
simulacion de una operacion nueva. Una regresion sintetica fallo antes
de la correccion y luego aprobo; 56 pruebas focales de parser/readiness,
runner, portfolio y cuenta pasaron. El informe real nuevo
`raw_week_input_readiness_v2.json` queda aun **blocked**: 43 senales
accionables compiladas de 44 recibidas, 22 candidatos stale distintos
inventariados, cero candidatos frescos crudos sin recepcion, y falta
`canal1_22738`. El informe v1 se conserva. El presupuesto de 64 ya no
es obstaculo para los 43 accionables, pero no autoriza omitir la senal
text-only restante.

La ruta text-only no puede resolverse con un ID permitido a mano ni con
una sola lista fija de senales. El texto `canal1_22738` llego a las
14:37 UTC; el replay aislado de la senal previa `canal1_22732` termina
antes de las 14:23 UTC en los 11 escenarios, lo que respalda admitir
22738 causalmente en esos controles. Pero otro texto cualificado,
`canal1_22648`, llego a las 13:23:01 UTC tras el sticker 22646:
la posicion simulada previa seguia abierta en escenarios 0-6 y estaba
plana en escenarios 7-10. El segundo grupo solo muestra un **posible**
fallback text-only, sujeto a las demas guardas live; no es un fill
observado. Esto prueba que la admision de textos de canal 1 puede variar
por escenario segun el camino previo. El runner 44 x 11 actual agrupa
senales aisladas con identidad fija, por lo que **no certifica** este
enrutamiento ni decisiones compartidas. El siguiente cambio debe ser un
replay causal de flujo por escenario, con estado de canal y guardas de
texto/sticker, seguido de la comparacion de secuencias y cuenta conjunta;
los casos ambiguos deben bloquearse y permanecer en el denominador.
La suite general posterior al cambio de antiguedad y prioridad del
extractor aprobo **7.845 pruebas**, 927 avisos, en 580,22 s. Los cambios
son locales; no hay commit, push, despliegue ni cambio de politica live.

Continuacion local 24/09: `research/causal_text_admission.py` separa los
textos de canal 1 frescos de las entregas atrasadas y consulta el volumen
simulado antes de decidir si el texto actualiza una senal o puede abrir otra.
El inventario crudo contiene 17 candidatos textuales frescos y siete stale.
La admision falla cerrada si no esta completo el universo anterior del
escenario, si el camino tiene bloqueos, o si falta/contradice el instante de
cierre de la senal; volumen plano no equivale a `status=closed`. Son
**38 pruebas focales aprobadas** tras una regresion inicialmente fallida.
Todavia no esta conectado al runner semanal ni existe un modelo independiente
del ciclo de vida que provea `signal_closed_at`; 22738 permanece bloqueada,
sin inyectar su fill observado. Siguiente: flujo conjunto por escenario,
cierre causal de senales y contraste de decisiones y riesgo. Sin publicacion.

Revision del contrato semanal 24/09: se detecto una divergencia general en
Dubai: el control `run_causal_controls.policies()` usado por la semana dejaba
`pending_entry_policy=none` por defecto, pero el contrato
`dubai_balanced_v1` declara `until_expiry`. En una cesta plana antes de
caducar la escalera, esa discrepancia puede cambiar una DCA posterior y
el estado abierto/cerrado que recibe el siguiente texto. El runner semanal
ahora fija `until_expiry` solo para su perfil Dubai; el comparador de
portfolio valida y usa el mismo perfil. Una prueba confronta entrada,
volumen, proteccion, tiempo y terminal con el contrato declarado; 29
pruebas focales del runner, textos y cartera aprobaron. Los controles
semanales previos con el perfil `none` no prueban paridad Dubai y sus
artefactos inmutables no deben reinterpretarse bajo el perfil nuevo.
La suite general posterior aprobo **7.853 pruebas**, 927 avisos, en
571,99 s (Python 3.14.2 en Windows). No se uso como prueba de paridad live.
Ademas, `route_canal1_stream` prueba en sintetico que la misma secuencia
textual produce una entrada nueva o actualiza una senal anterior segun
el resultado del escenario; si faltan estado, cierre causal o semantica
de actualizacion, bloquea las decisiones siguientes. Sigue faltando
conectarlo al motor compartido y resolver el cierre de ciclo de vida
independientemente de los fills observados; no se ha recalculado ni
certificado el recorrido de la semana real. Sin commit, push o cambio VM.

Control observado de ciclo de vida 24/09: se extrajo de solo lectura el
mismo slice congelado de 3.778.985.404 bytes con un worker Windows en
prioridad de recursos de fondo. El piloto de 11,7 MB y el escaneo completo
verificaron sus hashes; el segundo produjo
`lifecycle_week_20260914_19_bg_v1.json` (SHA256
`bbcbe1397570b5ea533f8a612ef874347f68038f816bbaccc18b4bdd2bec5be3`),
con 425 eventos de 73 IDs. Heartbeats consultados durante la lectura:
recientes, exposicion plana y cola cero; esto no prueba ausencia de delay
en todos los instantes. Cero escrituras o reinicios en la VM.

`lifecycle_control_audit_v1.json` cruza este control con el crudo, las
cestas nativas y las anclas horarias sin alimentar al replay con resultados
observados. De 44 senales recibidas, 35 tienen `signal_closed` en el slice
y nueve no; cinco de esas nueve tienen cesta nativa con fills y cuatro no.
Hay 247 aplazamientos de finalizacion repartidos entre 21 IDs. Los 17
textos frescos de canal 1 quedan cubiertos por el journal: 16 pasaron por
`canal1_text_processing` sobre una senal anterior y `22738` registro
`signal_received` con `trigger=text_only`. Estos son **controles de
validacion**, no la lista de destinos permitidos del simulador.

En las 16 cestas de canal 1 con cierre nativo y `signal_closed`, el lapso
bajo la hipotesis de desfase broker-UTC de 10.800 s va de 0,687 a 9,789 s;
13 tienen ancla horaria directa en su dia (mediana 1,617 s; maximo 8,839 s).
Para canal 2, las 13 con ancla directa van de 1,498 a 1.402,738 s
(mediana 231,034 s). Una ancla del dia no verifica cada instante y una
ausencia de `signal_closed` en este slice no demuestra que nunca se cerrara.
El cierre de señal debe modelar politica de pendientes/caducidad,
monitorizacion y fallos o lagunas, no inferirse de volumen plano ni
calibrarse con un retraso fijo. Los tests de extraccion, auditoria, router
de textos y runner semanal aprobaron **23 pruebas focales**; falta el replay
compartido y la comparacion de trayectorias/drawdown independientes.

Continuacion causal local 24/09: `research/causal_lifecycle.py` modela una
hipotesis acotada de cierre de senal a partir de entradas/salidas simuladas,
politica de pendientes y tiempos de caducidad; nunca usa el `signal_closed`
observado para decidir. La admision de un texto consulta un **prefijo** del
camino simulado en el instante de recepcion, no el cierre calculado con
salidas posteriores. Estados ambiguos (volumen, ticket, eventos de ejecucion,
empates temporales, falta de fills o censura) bloquean la decision y mantienen
el caso en el denominador. No es aun una reproduccion del ciclo de vida live.
Ademas, cada candidato text-only fresco conserva su `LEVEL_UPDATE` directo:
en el slice crudo los 17 candidatos lo tienen, mientras siete textos stale
siguen excluidos con motivo explicito. Pasaron 15 tests focales de ciclo de
vida y textos. La suite general aprobo **7.865 pruebas**, 927 avisos, en
579,79 s (Python 3.14.2 en Windows); no certifica paridad live.
El router de flujo aun no esta conectado al replay compartido por escenario:
no existe una trayectoria semanal conjunta certificada ni comparacion de
drawdown independiente, y no se ha publicado ni cambiado la VM.

Control de cobertura textual 24/09: `tools/audit_week_causal_input_readiness.py`
ahora separa senales compiladas directamente, textos candidatos pendientes de
enrutamiento por escenario y senales recibidas sin causa en el crudo. El nuevo
artefacto inmutable `raw_week_input_readiness_v3.json` (SHA256
`b62b22334447152ec4484741b1bcf5e5cd2c5e8f3c9816b9560600b7c5901097`)
mantiene estado **blocked**: 44 senales recibidas, 43 compiladas, una
`canal1_22738` coincide con un candidato textual fresco, cero recibidas sin
rastro entre esos dos conjuntos. Los 17 textos frescos son candidatos, no 17
entradas adicionales; el enrutamiento debe decidir entrada o actualizacion
con el estado causal de cada escenario. La prueba de regresion confirma que
un candidato presente no levanta el bloqueo de preparacion. No se modifico
ningun artefacto semanal anterior ni la VM.

Revision del estado pendiente 24/09: el estado de una senal causal registrada
sin fills previos ya puede ser `open` antes de la caducidad de entrada de su
politica; desde la caducidad queda **bloqueada por cierre desconocido**, no
se declara `closed` por el mero paso del tiempo. Esto permite que un texto
recibido mientras la escalera espera
su rango apunte a esa senal aun con volumen cero. Peticiones de cliente/mercado
en vuelo, empate de reloj, bloqueos y entradas posteriores incompatibles
siguen impidiendo afirmar el cierre. Son hipotesis de ciclo de vida para el
replay, no observaciones ni paridad live; 22 pruebas focales aprobaron.
El control observado contiene cuatro senales sin cesta nativa y sin
`signal_closed` en el slice: no prueba que nunca se cerraran, pero no avala
un cierre automatico al caducar. La regla local se mantuvo conservadora.
La auditoria de cobertura se reemitio como
`raw_week_input_readiness_v4.json` (SHA256
`85205340ba974abe5062e033a40c3de848132b08e0182aa9b477c170fae54469`)
para ligar sus fuentes al router actualizado. Conserva los mismos conteos y
`status=blocked`; v3 permanece inmutable como evidencia del codigo anterior.
Suite general del codigo actual: **7.868 pruebas aprobadas**, 927 avisos,
568,53 s (Python 3.14.2, Windows). Esto valida el cambio local y su
compatibilidad con los contratos comprobados, no demuestra aun la trayectoria
conjunta ni paridad independiente con produccion. Siguiente implementacion:
flujo de mensajes canal 1 por escenario en el motor compartido, sin eventos
textuales preasignados; comparar despues decisiones, exposicion, flotante,
costes y drawdown por intervalos con los controles observados. No publicar
una candidata mientras ese contraste siga bloqueado.

Flujo causal canal 1, continuacion local 24/09: el compilador
`research/causal_canal1_stream.py` genera una cinta cronologica de mensajes
crudos sin fijar de antemano el destino de textos ni gestion. En el slice
semanal produce 110 eventos: 16 stickers, 17 textos candidatos, 69 mensajes
directos de gestion y ocho mensajes no resolubles; retira 71 eventos que el
compilador aislado habia unido estaticamente a cestas. Sus 15 avisos de raiz
estatica no se interpretan como fallos del nuevo flujo. El router sigue las
respuestas a un texto hasta la cesta que ese texto haya tomado en el escenario
y bloquea los eventos no interpretables. Una entrada unica entrega toda la
cinta al router para no omitir los eventos irresueltos. 28 pruebas focales
aprobaron, incluida una secuencia cruda cuyo texto actualiza una cesta en un
camino y abre otra en el alternativo. Esta capa **todavia no ejecuta el motor
de mercado/transport compartido**; no valida fills, flotante, costes ni
drawdown conjunto y no cambia produccion.
Suite general posterior: **7.874 pruebas aprobadas**, 927 avisos,
569,17 s (Python 3.14.2, Windows). El siguiente paso de implementacion es
construir rutas con una cinta Bid/Ask/FX comun y conectar esta cinta de
mensajes al reloj del motor compartido, manteniendo el orden causal y las
decisiones de admision por escenario; despues, comparar la trayectoria de
riesgo independiente con los controles observados. Sin commit, push ni
cambio de VM.

Puente experimental de flujo a riesgo compartido, 24/09: `make_shared_paths`
prepara Bid/Ask y FX una sola vez y genera rutas por senal que comparten los
mismos arrays inmutables, manteniendo hora causal, direccion, volumen y eventos
propios. `research/causal_shared_stream.py` reconstruye un replay
`simulate_shared` tras cada mensaje admitido, refresca **todas** las cestas y
exige que el riesgo anterior al nuevo mensaje permanezca identico. Una prueba
de dos canales presenta posiciones simultaneas en la misma cuadricula de
riesgo; texto y respuesta se resuelven desde ese mundo. Empates exactos entre
mensaje y tick, refrescos incompletos, mutacion del prefijo y exceso de
presupuesto fallan cerrados. Son 54 pruebas focales aprobadas.

Preflight del slice real: 3.354.354 ticks XAUUSD en los Parquet congelados y
110 eventos de canal 1; todos encuentran un tick posterior (espera mediana
54,5 ms, p95 254,1 ms, maximo 401 ms), y un evento irresoluble `22650`
comparte exactamente milisegundo con un tick bajo la hipotesis de desfase
broker-UTC de 10.800 s. Estos son lapsos de cotizacion, **no** mediciones de
latencia VM ni de ejecucion. El perfil compartido actual limita una ejecucion
a un millon de ticks, asi que la semana no cabe en una unica sesion; dividirla
sin trasladar estado de cestas/cola/gestion seria incorrecto. Ademas faltan
prueba de universo previo completo y semantica de ocho eventos irresueltos.
El puente sigue siendo un experimento local acotado, no paridad historica ni
certificacion de simulador para seleccionar estrategia. Siguiente: continuidad
causal incremental del motor y comparacion independiente de trayectorias.
Suite general del puente y constructor compartido: **7.882 pruebas
aprobadas**, 927 avisos, 568,28 s (Python 3.14.2, Windows). La prueba
de prefijo de riesgo y sus regresiones negativas son evidencia de causalidad
del piloto, no de paridad historica de la semana.

Reduccion de riesgo compartido en flujo, 24/09: `simulate_shared` admite un
receptor tipado de cada punto y cada cuadro de riesgo, sin retenerlos en el
resultado cuando `retain_risk=False`; mantiene recuentos y presupuesto. El
reductor `SharedRiskOnlineReducer` reproduce los extremos y el drawdown
conjunto del resumen retenido, con comprobaciones de identidad, cronologia y
continuidad de las cestas cerradas. En una sonda sintetica de 20.000 ticks,
40.005 puntos y 20.000 cuadros, el pico medido por `tracemalloc` fue de
19,92 MB reteniendo riesgo y 0,19 MB descartando las muestras tras reducirlas.
La sonda no mide memoria total del proceso ni la semana real. **Todavia no
existe una trayectoria completa durable para comparar punto por punto con
produccion**, ni se ha elevado el limite de un millon de ticks, ni se han
resuelto los ocho mensajes ambiguos y el universo inicial. Por ello esta
reduccion no cambia el estado de admision historica ni autoriza seleccion o
activacion de estrategia. Las pruebas focales del puente, motor y reductor:
71 aprobadas. La primera suite general encontro una prueba de reconciliacion
MT5 intermitente: dejaba `position_id` aleatorio por `hash()` al fijar los
tickets. Se fijaron ambos identificadores en la prueba sin cambiar codigo de
produccion. Suite general repetida: **7.887 pruebas aprobadas**, 927 avisos,
579,23 s (Python 3.14.2, Windows). Cambios locales sin commit, push ni
comprobacion de VM.

Traza durable de riesgo, 24/09: `research/shared_risk_trace.py` escribe cada
punto y cuadro del motor compartido como JSONL comprimido en un directorio
exclusivo. Publica el manifiesto final solo despues de cerrar los datos;
registra SHA-256, tamano, conteos, scopes, moneda y bloqueos. Una traza
interrumpida no tiene manifiesto y no se admite; una alterada falla la
verificacion. En pruebas de ida y vuelta se recuperaron exactamente todos
los puntos y cuadros de un caso conocido y otro con riesgo desconocido, y el
reductor recalculo los mismos extremos/drawdown que el resumen retenido.
**El archivo no vincula todavia hashes de fuentes ni constituye evidencia
historica certificada**: marca expresamente que requiere esa vinculacion.
43 pruebas focales de traza, cuadricula y puente aprobadas. Sigue pendiente
la ejecucion causal de la semana completa, la admision de sus mensajes y la
comparacion independiente con produccion; ningun cambio en la VM.
Suite general tras la traza durable: **7.892 pruebas aprobadas**, 927 avisos,
564,57 s (Python 3.14.2, Windows). Siguiente bloque: continuidad del motor
entre mensajes en lugar de reconstruir hasta 110 veces el mismo reloj;
despues, vincular la traza a fuentes congeladas y contrastar secuencia,
exposicion, flotante y drawdown contra evidencia observada.

Revision causal de mensajes de canal 1, 24/09: las ocho revisiones ambiguas
del slice congelado correspondian a cuatro mensajes logicos. La gramatica
causal nueva (sin cambiar `provider_signal_catalog.py` ni sus catalogos
versionados) reconoce un cierre imperativo "close this/it now" y no trata
"let's see if..." posterior a una instruccion BE como condicion previa.
La sugerencia "if you don't want ... close now" queda **opcional**, no se
ejecuta automaticamente. Con el mismo crudo, la cinta mantiene 110 eventos:
73 de gestion directa y cuatro revisiones irresueltas, pertenecientes a
dos mensajes: `22590` (sticker nuevo sin direccion visual verificada) y
`22739` (cierre opcional sin eleccion de politica). El antiguo informe
`raw_week_input_readiness_v4.json` y el catalogo historico no se reescriben.
129 pruebas focales aprobaron, incluida la igualdad exacta del catalogo
versionado con su reconstruccion por defecto. Esto mejora la cobertura
causal, pero **no** levanta el bloqueo de la semana, no prueba el destino de
los cierres directos y no certifica paridad de trayectoria.
La gramatica conserva como condicion no resuelta cualquier accion posterior
al comentario especulativo; no la recorta. Revision final: 130 pruebas
focales y **7.900 pruebas generales aprobadas**, 927 avisos, 579,72 s
(Python 3.14.2, Windows). SHA-256 del crudo sin cambios:
`9548ca5ce3fdd27ebe5f488ac97639c7e5cc7001ae0f75c73a40d3828cfd63b0`.
Sin commit, push ni comprobacion o cambio de VM en esta continuacion.

Entrega incremental de gestion, 24/09: el replay compartido acepta una
fuente opt-in de eventos `ProviderEvent` por cesta. Solo admite cada evento
entre la cotizacion anterior y la actual, rechaza empates, entregas tardias,
destinos desconocidos, mezcla con eventos estaticos y exceso de presupuesto;
conserva los eventos entregados y declara la limitacion en el resultado.
El motor actualiza sus relojes de cierre de proveedor antes de procesar la
cotizacion. En controles de una y dos cestas, incluida una entrada en cola
tras un broker ocupado, las cestas, cada punto/cuadro de riesgo, el transporte
y los bloqueos coinciden exactamente con el replay estatico equivalente.
**Esta pieza permite evitar la reconstruccion para gestion soportada de cestas
ya conocidas cuando se conecte el router; el puente actual aun reconstruye.**
No implementa admision dinamica de nuevas cestas ni lecturas
entre cotizaciones.** El BE de proveedor con cliente compartido sigue
bloqueado por el contrato de niveles absolutos/cliente, como confirma una
regresion; no se relajo ese gate. 78 pruebas focales aprobaron. La semana
real y su comparacion contra produccion siguen pendientes.
Suite general tras la entrega incremental: **7.907 pruebas aprobadas**,
927 avisos, 576,74 s (Python 3.14.2, Windows). La fuente dinamica queda
limitada al perfil `shared_quote_rounds_v1`; la entrega de eventos en
`shared_interquote_reads_v2`, la consulta causal del estado parcial y el alta
de cestas durante la sesion requieren implementacion y controles propios.
Sin commit, push ni cambios en produccion o VM.

Prefijo causal bajo demanda, 24/09: el generador del motor expone al
coordinador, sin finalizar la cesta ni reconstruir ticks, una instantanea
inmutable de identidad, entradas, salidas, bloqueos y eventos de ejecucion.
El callback de mensajes recibe tambien el ultimo punto de riesgo asentado;
si no existe, el prefijo es desconocido. Una regresion demuestra que antes
del tick 2 se ve una entrada y ninguna salida, aunque el resultado final
cierra en el tick 3. `signal_state_at` resuelve esa cesta como abierta en
el instante del mensaje usando exclusivamente el prefijo. 129 pruebas
focales de replay compartido, riesgo y runtime aprobaron. **El router aun
no consume este prefijo y no crea cestas dinamicamente**; no se ha validado
una trayectoria semanal real ni la paridad con produccion.
Suite general tras el prefijo: **7.908 pruebas aprobadas**, 927 avisos,
569,80 s (Python 3.14.2, Windows). El siguiente bloque es admitir señales
y entregar gestion en una sola pasada sobre la cinta compartida, conservando
de forma explicita los casos sin direccion o politica verificadas. La cinta
congelada de la semana supera el tope actual de un millon de cotizaciones;
ampliarlo requiere controlar primero coste, memoria y traza de riesgo.
Sin commit, push ni cambios de VM.

Flujo compartido en una pasada, 24/09: `CausalRouteSession` procesa cada
mensaje contra el ultimo prefijo asentado del escenario. El motor compartido
puede mantener cestas candidatas dormidas y activarlas solo en el primer tick
posterior a su mensaje; entrega la gestion al destino decidido por el router.
Un calendario explicito evita copiar prefijos en ticks sin mensajes. El puente
incremental `run_incremental_shared_canal1_stream` conserva el puente anterior
como control y no alimenta decisiones con fills observados. En dos cintas
sinteticas, las decisiones, cestas admitidas, cada punto/cuadro de riesgo
proyectado, transporte y bloqueos coinciden con el control de reconstruccion;
un universo inicial no verificado no abre cestas de canal 1. La segunda cinta
mantiene bloqueado un texto posterior a la salida: el estado plano aun no
demuestra que las acciones de cliente/mercado hayan terminado. El bloqueo se
conserva, no se fuerza una entrada. Pasaron 159 pruebas focales y la suite
general: **7.919 aprobadas**, 927 avisos, 573,45 s (Python 3.14.2, Windows).
Esto **no** certifica paridad real ni permite la semana completa: la cinta
congelada excede `max_quotes`, el presupuesto de riesgo representa tambien
cestas dormidas, no hay traza semanal ligada a fuentes y el universo inicial
permanece incompleto. Siguiente: reconstruir causalmente peticiones pendientes
desde eventos anteriores al mensaje; despues, tratar el riesgo dormido de
forma dispersa y contrastar con trayectorias observadas. Sin commit, push,
despliegue ni comprobacion de la VM en esta continuacion.

Cierre por prefijo de ejecucion, 24/09: `signal_state_at` ya puede demostrar
que las peticiones de mercado llegaron a acuse/reconciliacion y que las
acciones de cliente quedaron liberadas/canceladas **antes** del mensaje.
Identidad, orden, evento futuro o secuencia incompleta siguen bloqueando.
La hora hipotetica de cierre no precede al ultimo asentamiento mas el margen
de finalizacion declarado. En el control corto, el texto tardio abre una
cesta nueva solo en el replay incremental: el puente antiguo consulta un
resultado final censurado y bloquea con `path_ended_before_strategy_exit`.
La trayectoria anterior al texto permanece identica; el informe completo
sigue **blocked** por la censura, no es una operacion validada. 120 pruebas
focales y la suite general aprobaron: **7.928 pruebas**, 927 avisos,
586,21 s (Python 3.14.2, Windows). Siguiente control: mensajes y ticks
congelados del 17/09 en una ventana acotada, con estado inicial y reloj
marcados como hipotesis. Sin commit, push ni cambio de VM.

Control congelado 22732/22738, 24/09: el puente incremental entrega tambien
los eventos causales de canal 2 en el mismo calendario; una regresion con
cierre entre cotizaciones reproduce cesta, riesgo y transporte del control.
`tools/probe_canal1_incremental_window.py` verifica hashes del crudo y de
ambas cintas, limita una ventana y fija perfil/hipotesis. La ejecucion
14:00-14:45 UTC del 17/09, con offset hipotetico de 10.800 s y universo
inicial **asumido**, queda en
`reports/canal1-incremental-22738-20260924/diagnostic-v2.json` (SHA256 del
runner declarado `61fe7efc14424dc20f893f2fa8310bcf83107afd5473cd9b0f07c020f59deb84`,
comprobado contra el fichero actual). Incluye 22732, 22738 y canal2_3106;
ningun caso se descarto del denominador. Resultado **blocked**: tres cestas
candidatas Dubai tienen `protection_policy_unsupported`, 22732 no obtiene
prefijo valido y las seis decisiones siguientes quedan bloqueadas. De
30.914 cuadros de riesgo, cero tienen total monetario conocido; no hay
drawdown conjunto que comparar. El perfil explicito de esta prueba no
representa ejecucion live. El problema general es que `basket_money` de
Dubai requiere `basket_guard_v1`, pero el contrato actual rechaza esa
extension con el cliente compartido; relajar la guarda sin modelar la
contencion seria incorrecto. Siguiente: una via compartida que soporte la
politica Dubai real y los mensajes intercalados, o una demostracion
equivalente bajo un contrato completo, antes de interpretar este control
como paridad. Sin commit, push ni accion en VM.

Capacidad opt-in y segundo control real, 24/09: se anadio el cliente
`single_basket_terminal_guard_v1` para `basket_guard_v1` con enlace
`timestamp_and_ordinal`. Los clientes anteriores siguen rechazados; fast y
oracle no certifican esta via. En controles sinteticos, el escalar y el
compartido coinciden monocesta, y una salida Dubai espera al canal 2 ocupado
con flotante adverso visible hasta el fill tardio. El primer control real
`diagnostic-v3.json` levanto el bloqueo de proteccion, pero una marca
`terminal_requested` del cliente se confundia con una peticion pendiente.
Se registro esa marca como decision unica, manteniendo la exigencia de
acuse de todas las peticiones reales. El artefacto inmutable
`reports/canal1-incremental-22738-20260924/diagnostic-v4.json` declara hashes
del runner, crudo, cintas y nueve modulos; los hashes actuales se verificaron.
Bajo **hipotesis** de offset 10.800 s, universo inicial completo, politica
semanal declarada y ejecucion explicita no calibrada, 22732 abre por sticker,
22733 actualiza esa cesta, dos BE a las 14:24 no tienen cesta abierta,
22738 entra como text-only a las 14:37 y su actualizacion se dirige a ella.
22738 tiene dos entradas modeladas a las 14:37:00.782 y 14:39:53.281 UTC;
queda abierta al corte de las 14:45, por lo que el informe es **blocked**
con `path_ended_before_strategy_exit`. Los 30.914 cuadros tienen total
modelado conocido y su drawdown maximo es 3.976 centimos; **no** es un
drawdown observado ni paridad de cuenta. Falta resolver 22739 opcional,
estado anterior a la ventana, reloj, costos/ejecucion reales y escala de la
semana antes de usar esta ruta para seleccionar una estrategia. Sin commit,
push ni cambio de VM.

Verificacion de esta continuacion, 24/09: las 277 pruebas focales y la suite
general aprobaron: **7.934 pruebas**, 927 avisos, 574,99 s (Python 3.14.2,
Windows). `git diff --check` termino con codigo 0; los avisos de conversion
LF/CRLF de archivos ya modificados no son errores de whitespace. Esto valida
la regresion de codigo, no la paridad historica. El siguiente control debe
declarar antes de comparar si el mensaje opcional 22739 implica mantener o
cerrar, prolongar la cinta hasta la salida observada y contrastar entradas,
exposicion, flotante y drawdown sobre el mismo horizonte. Sin commit, push,
despliegue ni comprobacion de VM en esta continuacion.

Ramas opcionales sin seleccionar por resultado, 24/09: el compilador de
canal 1 admite `optional_close_choice=block|hold|close`; `block` conserva el
comportamiento anterior. Solo una sugerencia `CLOSE_ALL` con opciones
explicitas `CLOSE_ALL`/`HOLD` puede tomar una de las dos ramas; una instruccion
condicional sigue bloqueada. Las ediciones con la misma semantica del mismo
mensaje quedan en la cronologia como repeticion sin enviar otra accion. Cinco
pruebas nuevas cubren eleccion, condicional y duplicacion; 36 pruebas focales
aprobaron. Con las mismas cintas y perfil no calibrado, los informes inmutables
`reports/canal1-incremental-22738-20260924/diagnostic-v5-hold.json` y
`diagnostic-v5-close.json` cubren 17/09 14:00-14:55 UTC, offset 10.800 s e
inventario inicial supuesto completo. Ambas ramas conservan 37.664 cuadros
de riesgo conocidos y ningun bloqueo compartido. La rama hold cierra las dos
posiciones modeladas de 22738 por `basket_stop` a las 14:53:36.814 y
14:53:39.067, neto modelado -25,16 EUR; la rama close envia el cierre por
transporte compartido y sale a las 14:47:20.161 y 14:47:22.269, neto
modelado +0,83 EUR. El control observado termina a las 14:54:07.124 con
-24,96 EUR y maximo drawdown 38,95 EUR. La cercania del neto de hold **no**
valida la trayectoria: el modelo sale unos 28-30 s antes y el drawdown de
3.976 centimos de estos informes es agregado de todas las cestas, no el de
22738. La primera llamada MT5 observada de 22738 duro 5.579 ms frente al
fill inmediato modelado; en 40 primeras llamadas semanales hubo 9 de mas de
5 s y 3 de mas de 60 s. No se adopta una latencia fija a partir de este caso.
El 17/09 tiene dos anclas directas de offset 10.800 s anteriores a esta
ventana, pero los metadatos Parquet no admiten por si solos su reloj y se
mantiene explicita la hipotesis de constancia. Siguiente: contraste de riesgo
por cesta sobre horizonte comun, reloj y ejecucion con distribucion observada,
y ampliacion de muestra antes de certificar simulador o seleccionar estrategia.
La suite general posterior a este cambio aprobo: **7.939 pruebas**, 927
avisos, 577,64 s (Python 3.14.2, Windows). Los hashes declarados en ambos
informes v5 se verificaron contra runner, modulos y crudo actuales. Sin
commit, push, despliegue ni accion sobre VM.

Reduccion por cesta y control de apertura, 24/09: el diagnostico incremental
conserva por cada scope exposicion maxima, flotante, minimo, drawdown desde
origen cero, tiempos de pico/valle, neto y SHA256 de la serie pos-evento.
Los informes `diagnostic-v6-*` de 14:00-14:55 revelaron una omision material:
el ledger reconciliado tenia **cuatro posiciones abiertas** de canal2_3102 a
las 14:00 (0,12 lotes). Esos informes, igual que v5, quedan como diagnosticos
negativos; no prueban transporte ni riesgo compartido. La nueva puerta exige
un ledger reconciliado cuyo SHA256 de deals coincida y rechaza el replay si
conoce posiciones abiertas al inicio. Rechazo de 14:00 comprobado sin crear
artefacto. A las 13:45 el ledger no conoce posiciones abiertas, pero esto no
demuestra ausencia de ordenes pendientes o posiciones aun sin cerrar.

Los informes inmutables `diagnostic-v8-hold-bound-start.json` y
`diagnostic-v8-close-bound-start.json` cubren 17/09 13:45-14:55 UTC con el
ledger de apertura ligado. El comparador fuente-ligado genero
`comparison-v2-hold-leg-path.json` y `comparison-v2-close-leg-path.json`:
cuatro cestas observadas se emparejan, dos scopes candidatos no abren, cero
cestas observadas del intervalo quedan sin scope y cero comparaciones
bloqueadas. Ninguna de las cuatro coincide en trayectoria. En hold, drawdown
modelado/observado EUR: canal2_3102 256,55/178,72; canal2_3106 9,27/6,32;
canal1_22732 32,72/27,27; canal1_22738 33,60/38,95. Las cantidades de
cada entrada coinciden por orden en las cuatro; en las tres primeras difiere
el **maximo volumen simultaneo**, no el perfil de lotes. Para 3102 y 3106
coincide el neto +20,02 EUR, lo cual muestra que el neto no valida el camino.
En 22738 hold, neto modelado/observado -25,75/-24,96 EUR y salida modelada
28,057 s antes; la rama close modela +0,24 EUR y drawdown 20,88 EUR.
Los tiempos de cada pierna se emparejan por ordinal como hipotesis, no por
ticket MT5 comun. En 3102, la primera entrada modelada difiere 246 ms de
la nativa; la primera posicion real sale por TP 2,626 s tras entrar mientras
que el modelo la retiene hasta 14:49. La cinta retenida contiene el precio
del TP temprano. Hay que contrastar el instante real/modelado de activacion
de proteccion en varios casos antes de atribuir causa. El inventario
`material_coverage_partial_v15.json` solo tiene 2 de 12 segmentos esperados
para 3102 y ninguna peticion de modificacion en esos segmentos: la hora
real de instalacion de TP no esta probada. Ni los escalares ni los hashes
de serie constituyen aun comparacion tick a tick ni paridad de cuenta.
58 pruebas focales aprobaron y la suite general cerro con **7.944 pruebas**,
927 avisos, 585,89 s (Python 3.14.2, Windows). Sin commit, push,
despliegue ni accion en VM.

Auditoria del instante efectivo de TP, 24/09: los informes inmutables
`diagnostic-v9-*-protection.json` exponen ahora eventos de proteccion sin
cambiar decisiones, entradas, salidas ni riesgo del v8. Las comparaciones
fuente-ligadas `comparison-v3-*-protection.json` conservan cuatro cestas
observadas emparejadas, dos candidatos sin entrada, tres discrepancias de
exposicion concurrente y una de trayectoria. El primer auditor
`tp-activation-v1-*` usaba erroneamente el acuse de la modificacion como
instante de activacion; queda conservado solo como diagnostico provisional.
El contrato corregido `tp-activation-v2-*-installed.json` utiliza el evento
`installed` del libro de protecciones. De 10 piernas canal2 555 comparables,
una tiene toque de TP retenido antes de esa instalacion y cierre nativo por TP
tambien anterior; ambos brazos del cierre opcional dan el mismo resultado.

En canal2_3102 pierna 0, el modelo abre a 13:45:58.575 UTC, solicita TP
4358,44 a 13:46:00.586, recibe `invalid_stops` a 13:46:01.638 y
13:46:04.822, e instala el TP a 13:46:07.888 tras reintentar. La cinta
registra Ask 4358,35 a 13:46:01.434. El broker abrio su posicion a
13:45:58.821 y la cerro por TP a 13:46:01.447 (reloj fuente menos 10.800 s,
offset respaldado por anclas directas de ese dia). El modelo retiene esa
pierna hasta 14:49:12.165. Para la cesta, el neto coincide en +20,02 EUR,
pero la exposicion maxima es 0,16 lotes modelados/0,12 nativos y el
drawdown 256,55/178,72 EUR. Esto demuestra una divergencia causal real del
modelo y explica la exposicion adicional de 0,04 lotes; no atribuye por si
solo cada centimo del drawdown discrepante. No se conoce la hora nativa de
instalacion del TP: el cierre por TP solo demuestra que estaba activo antes
del cierre. La cobertura material archivada de 3102 es parcial, y una
modificacion de la latencia o reglas del simulador aun seria una hipotesis,
no una calibracion admitida. Siguiente puerta: obtener una muestra mas amplia
de secuencias de proteccion nativas de forma acotada y sin perturbar la VM;
contrastar instalacion/rechazo, exposicion, flotante y drawdown por cesta y
cuenta antes de ajustar parametros, certificar paridad o buscar estrategias.
Verificacion local posterior: 11 pruebas focales y `python -m pytest -q
--disable-warnings` con **7.949 aprobadas**, 927 avisos, 572,27 s (Python
3.14.2, Windows). Los informes v2 validaron hashes actuales de runner,
11 modulos de implementacion y 28 entradas del riesgo nativo. Sin commit,
push, despliegue ni accion en VM.

Ampliacion multiventana y puertas de cobertura, 24/09: se probaron seis
ventanas no solapadas de 15, 17 y 18/09 con cinta XAUUSD/EURUSD congelada,
ancla directa del offset 10.800 s y control de apertura sin posiciones
conocidas en el ledger reconciliado. Los informes actuales `diagnostic-*-v3`
y `comparison-*-v3` en `reports/expanded-native-controls-20260924/`
mantienen cada ventana y sus hashes. De **13 cestas observadas** cubiertas,
**7** permiten comparar escalares por cesta y **6** quedan bloqueadas;
ninguna cesta nativa del intervalo desaparece del denominador. Las siete
comparables discrepan: tres en exposicion simultanea y cuatro en trayectoria
aun con el mismo volumen maximo. El riesgo de cuenta no queda certificado.
Las ventanas del 15 tienen conversion FX retrospectiva o rejilla incompleta;
la de canal2_2970 tiene tres entradas modeladas frente a cuatro nativas.
La ventana 18/09 13:00-13:35 revelo 2.986 ticks sin frame modelado mientras
habia exposicion. El reductor ahora conserva el caso como
`incomplete_post_event_grid` sin publicar drawdown/neto completos; sigue
rechazando indices duplicados o tiempo regresivo. Otro defecto diagnosticado
era propagar un bloqueo de una señal a todas las cestas: los bloqueos con
prefijo de signal_id ahora pertenecen solo a esa cesta, mientras los globales
siguen afectando a todas. Aun asi, canal2_2977 permanece bloqueado porque
su ventana completa pierde frames posteriores por la otra cesta; no se
recorto el intervalo para hacerlo pasar.

Los informes `tp-activation-*-v3` de esas ventanas contienen **18 piernas
canal2 temporizadas** (2 del 15, 10 del 17, 6 del 18) y un caso bloqueado
por identidad de piernas de 2970. Una pierna tuvo tanto toque previo a la
instalacion modelada como cierre nativo previo; otra tuvo toque previo sin
cierre nativo previo. Por tanto el mecanismo de TP temprano es real en la
primera pero no una explicacion universal de los demas recorridos. No se
alteraron latencias, reglas de stops ni estrategia live. Siguiente puerta:
reconstruir muestras nativas y exportar la rejilla pos-evento modelada en
ventanas acotadas para comparar exposicion, flotante y drawdown sobre los
mismos ticks, conservando los seis bloqueos y sin usar escalares o hashes
como sustituto de paridad temporal.
Verificacion posterior: 13 pruebas focales y suite `python -m pytest -q
--disable-warnings` con **7.951 aprobadas**, 927 avisos, 582,98 s
(Python 3.14.2, Windows). Los comparadores y auditores v3 comprobaron
fuentes y hash del runner actual. Todo local; sin commit, push, despliegue
ni accion en VM.

Comparacion punto a punto sobre reloj comun, 24/09: la opcion diagnostica
`--include-risk-grid` de `probe_canal1_incremental_window` conserva de forma
compacta el estado virtual pos-evento por cada tick XAU, con orden de scopes,
realizado, flotante y exposicion. Acepta un estado arrastrado solo si la cesta
ya termino plana y su dinero coincide con el resultado final; frames ausentes,
desordenados o sin valor siguen bloqueados. `compare_incremental_tick_paths`
recalcula el control escalar, verifica ledger y hashes de fuentes, reconstruye
cada curva nativa desde deals/FX/Bid/Ask y exige que su cantidad y SHA256 de
muestras reproduzcan el informe nativo congelado antes de alinear cotizaciones.
Los informes finales inmutables `tick-path-s18-1220-1245-v4.json` y
`tick-path-s17-1345-1455-v4.json` en
`reports/expanded-native-controls-20260924/` comparan respectivamente
15.504 y 48.393 ticks: **7 cestas unicas, 7 trayectorias discrepantes**,
con hashes nativos originales reproducidos. Los otros seis controles del
inventario multiventana siguen bloqueados y no se promovieron al comparador.

En 18/09 canal2_3168, drawdown modelado/nativo 65,95/65,65 EUR parece
cercano, pero difieren 4.441 de 4.876 ticks con alguna posicion abierta y
el maximo desvio puntual de equity es 5,50 EUR. En 17/09 canal2_3102
difiere en 43.746 de 43.746 ticks activos, con desvio puntual maximo
77,52 EUR, exposicion distinta en 43.706 ticks y drawdown 256,55/178,72
EUR. Los informes guardan primer tick discrepante y recuentos separados de
realizado, flotante, numero de posiciones y volumen; un neto igual no borra
la discrepancia. La rama alternativa `tick-path-s17-1345-1455-close-v4.json`
es el **mismo control**, no muestra adicional: para canal1_22738 el maximo
desvio activo sube de 6,05 EUR (hold) a 32,04 EUR (close). No se decide el
significado del mensaje ambiguo por cual rama ajuste mejor retrospectivamente.
Las trayectorias nativas son reconstrucciones de fills observados, no una
serie independiente de equity de cuenta ni prueba de ejecucion servidor; el
perfil de ejecucion virtual e universo inicial siguen siendo hipotesis.
Siguiente puerta: clasificar mecanismos generales de divergencia (entrada,
instalacion TP, salida y conversion), contrastarlos con mas ventanas y
telemetria nativa acotada, y exigir paridad de exposicion/flotante/drawdown
por cesta y cuenta antes de calibrar o buscar estrategias.
Verificacion local: 18 pruebas focales y `python -m pytest -q
--disable-warnings` con **7.956 aprobadas**, 927 avisos, 567,94 s
(Python 3.14.2, Windows). Los tres informes punto a punto v4 tienen hashes
vigentes de runner, modelo y comparador; ninguno declara paridad live.
Sin commit, push, despliegue ni accion en VM.

Portfolio conjunto sobre ticks comunes, 24/09: el comparador exige ahora que
toda posicion del ledger reconciliado que intersecte la ventana pertenezca
a un scope admitido, abra y cierre dentro de sus limites y este en el ancla
monetaria. Suma los estados virtuales de todos los scopes sin sustituir
desconocidos por cero; reconstruye juntos los fills observados y comprueba
neto contabilizado y drawdown virtual contra el reductor original. Los
informes inmutables `tick-path-s18-1220-1245-v5.json` y
`tick-path-s17-1345-1455-v5.json` bajo
`reports/expanded-native-controls-20260924/` admiten respectivamente
7 posiciones/3 cestas y 15 posiciones/4 cestas reales. Sobre los mismos
ticks, el drawdown conjunto virtual/nativo-reconstruido es 65,95/65,65
EUR el 18/09 y 255,07/177,24 EUR el 17/09; maximos desvios puntuales
5,48 y 77,52 EUR. La rama alternativa `*-close-v5` del 17 es el mismo
control, no muestra adicional. Las seis cestas bloqueadas de otras ventanas
siguen fuera de esta comparacion conjunta y en su denominador original.
Esto **no** es la equity historica independiente de MT5: el informe
`native_account_snapshots_v2.json` contiene 33 snapshots semanales pero
ninguno dentro de estas dos ventanas; los del 17/18 cercanos son planos.
No se certifican credito, margen, posiciones no archivadas ni drawdown live.

Separacion de tiempos de entrada en las siete cestas comparables: 22
piernas; en seis de siete primeras entradas el modelo fue anterior al fill
nativo, mediana de las primeras -200 ms, con extremos -5.454 y +41 ms.
En 15 piernas posteriores, cuatro difieren mas de 5 s en valor absoluto y
una llega a -168.588 ms. Son diferencias de reloj de entrada, no una medida
pura de transporte: las entradas posteriores dependen tambien de escalera,
proteccion y cierres previos. Una latencia fija no explica por si sola ese
patron ni el TP temprano documentado; no se cambio ninguna hipotesis live.
Verificacion local: 20 pruebas focales y `python -m pytest -q
--disable-warnings` con **7.958 aprobadas**, 927 avisos, 572,11 s
(Python 3.14.2, Windows). Los tres informes v5 conservan hashes de fuentes,
runner, modelo y comparador; ninguno declara paridad con equity MT5.
Sin commit, push, despliegue ni accion en VM.

Primer fill frente al primer desacuerdo, 24/09: `compare_incremental_tick_paths`
anade una comparacion ordinal de primera entrada vinculada al ledger, ancla
monetaria y control escalar. Los informes `tick-path-s17-1345-1455-v6.json`,
`tick-path-s18-1220-1245-v6.json` y la alternativa `*-close-v6` mantienen
los mismos recuentos y curvas conjuntas v5; no son ventanas adicionales.
En las siete cestas comparables, el volumen de la primera pierna coincide.
El modelo entra antes en seis y despues en una; las diferencias modelo menos
nativo en ms son -246, -200, -31, -5.454, -142, -2.354 y +41. Cinco de
siete precios iniciales difieren: +0,09, +0,06, 0, +0,08, 0, -0,16 y
-0,02 XAUUSD, respectivamente. En `canal1_22768` el primer tick discrepante
tiene igual volumen y distinta valoracion (modelo -0,18 EUR, nativo -0,20
EUR), coherente con fills a 4361,05/4361,07, 0,01 lotes y la conversion
EURUSD; no demuestra por si solo un error del reductor de flotante.
En las otras seis el primer tick discrepante incluye exposicion diferente.
Son comparaciones de peticion/fill entre dos ejecuciones, **no** medidas
puras de latencia ni pruebas de causalidad de todas las divergencias
posteriores. `canal1_22738` tiene ademas una llamada MT5 observada de
5.579 ms y los controles de TP muestran otro mecanismo independiente; no
se ajusta una demora fija para igualar las muestras. Siguiente control:
separar las decisiones con igual estado inicial de los efectos de fills,
protecciones y salidas reales, y validar el mecanismo en ventanas nuevas
sin seleccionar por ajuste retrospectivo.
Verificacion focal: 18 pruebas aprobadas. El cambio es solo de informe
diagnostico local; suite completa 7.958 aprobadas inmediatamente antes de
esta ampliacion, no repetida despues. Sin commit, push, VM ni live.

Cruce de primera entrada con la llamada MT5, 24/09: el informe inmutable
`reports/expanded-native-controls-20260924/first-entry-roundtrip-7controls-v2.json`
une por ticket de deal el primer fill del ancla monetaria, los dos informes
de trayectoria v6 y el log de terminal `terminal_open_log_audit_v5.json`.
Verifica los 13 hashes de origen del informe de terminal y conserva un
denominador de 10 filas: 7 cestas con primera entrada comparable y 3 sin
entrada virtual; las otras seis cestas bloqueadas del inventario multiventana
no son parte de estas dos ventanas. En las siete, el comienzo de la peticion
virtual cae entre -32 y +165 ms del comienzo Python de `order_send` segun el
cruce de relojes, mientras las llamadas MT5 observadas duran 125-5.579 ms.
Para `canal1_22738`, diferencia primera entrada -5.454 ms, llamada 5.579
ms, tramo terminal Market->Accepted 4.934 ms y Accepted->Deal 637 ms;
para `canal2_3171`, -2.354 ms, llamada 2.344 ms, tramos 859 y 1.485 ms.
Esto localiza la mayor parte de esos dos desfases *dentro de la llamada
sincrona a MT5*, no en una espera anterior a ella. No identifica si es
terminal, red o servidor. Solo 2/7 filas pasan el chequeo estricto de
orden de relojes cruzados del auditor de terminal; las siete conservan
orden interno de etapas. No se usa este cruce para calibrar milisegundos
exactos ni se afirma que explique la escalera, TP, salidas o drawdown total.

La evidencia independiente previa del guard (3.135 Gold y 2.278 Dubai)
respalda la logica pura en estados capturados; el contraste de P/L con
intervalos nativos deja 3.118 y 2.270 dentro, 22 bloqueadas y 3 Dubai
fuera por hasta 0,09 EUR. Es solo P/L de posiciones conocidas en puntos
muestreados, no equity continua de cuenta. Junto con la divergencia virtual
por ticks, el proximo control debe aislar respuesta/fill observados de
decisiones hipoteticas, conservar proteccion efectiva y salidas como fases
distintas y probar despues ventanas no usadas para descubrir la causa.
Verificacion: 12 pruebas focales aprobadas, hashes del informe v2 y sus
fuentes actuales comprobados. Solo codigo e informes locales, sin commit,
push, despliegue ni accion en VM.

Control de gestion condicionado por entradas nativas, 24/09: el ejecutor
offline `tools/audit_week_conditioned_controls.py` fija las entradas
observadas, pero deja las salidas fuera del motor. El informe inmutable
`reports/expanded-native-controls-20260924/conditioned-gold-s17-s18-v4.json`
conserva las diez filas de las dos ventanas: cuatro cestas Gold evaluadas,
tres Dubai bloqueadas porque requieren gestion incremental y tres sin entrada
virtual bloqueadas por su gate previo. Las otras seis cestas bloqueadas del
inventario multiventana no pertenecen a estas dos ventanas. Los tres motores
coinciden en las cuatro cestas Gold y el neto/drawdown observados reconstruidos
igualan exactamente el ancla nativa congelada; ese control de origen es un
gate del ejecutor, no una certificacion de recorrido.

Las cuatro trayectorias Gold siguen siendo discrepantes. En `canal2_3102`,
condicionar las entradas reduce la diferencia de drawdown de 256,55 frente a
178,72 EUR a 178,72/178,72 EUR, pero quedan seis marcas discrepantes de
exposicion o flotante. En `canal2_3106`, drawdown simulado/nativo es
5,82/6,32 EUR; en `canal2_3168`, 65,65/65,65 EUR con 35 marcas
discrepantes; en `canal2_3171`, 0/4,22 EUR pese a neto identico de
1,75 EUR. En esta ultima el cierre TP condicionado ocurre a las
12:27:58.962 UTC, 15,187 s antes del deal nativo. El primer acuse TP
aceptado del cliente es a las 12:28:03.341 UTC, 4,379 s despues del
cierre condicionado; la orden inicial no solicitaba TP y hubo dos rechazos
`10016`. Esto identifica una ventana de proteccion no confirmada por el
cliente, no fecha exacta de instalacion en el servidor. En `3168` los cinco
TP ya tenian acuse aceptado mucho antes del cierre condicionado, por lo que
la misma explicacion no cubre sus dos salidas mas lentas.

Esta cohorte es retrospectiva y condicionada en fills reales; no prueba
decisiones de entrada, proteccion efectiva en servidor, hipoteticos fills ni
paridad de cuenta. El siguiente mecanismo a contrastar es la secuencia
solicitud/rechazo/acuse de proteccion por ticket, con estado causal en el
replay y sin calibrar un retraso fijo. Despues hay que comprobarlo en ventanas
intactas y contrastar cuenta/exposicion/drawdown; no se promueve una
estrategia con este informe. Verificacion focal: 32 pruebas aprobadas.
Codigo e informes locales; sin commit, push, despliegue ni accion en VM.

Cruce causal de acuses TP sobre la misma cohorte, 24/09: el informe
`conditioned-gold-s17-s18-v6-tp-receipts.json` conserva las diez filas y
las cuatro discrepancias v4, y anade el estado del cliente en cada cierre
TP condicionado sin leer un acuse futuro. De 16 cierres, cinco tienen TP
aceptado previamente en la traza disponible (los cinco de `3168`), uno
esta pendiente y sin acuse aceptado (`3171`, aceptacion 4.379 ms despues)
y diez carecen de traza de acuses (`3102`/`3106`). El estado aceptado del
cliente **no** confirma el instante de instalacion en servidor ni predice
el fill. No hay motivo para desplazar por igual todos los cierres; para
`3102`/`3106` hace falta primero evidencia de proteccion por ticket.
Una solicitud TP en la orden inicial, sin acuse propio, queda bloqueada
como aceptacion no probada. Verificacion focal: 36 pruebas aprobadas;
22 hashes del informe v6 vinculados a fuentes actuales.
El siguiente cambio de motor, si se justifica, debe conservar estados
solicitado/rechazado/confirmado y contrastarse con salidas en muestras
nuevas; este informe no modifica las salidas ni el riesgo virtual.

Corredor de ticks tras acuse TP, 24/09: el auditor offline
`tools/audit_tp_quote_corridors.py` conserva las 16 salidas condicionadas
y compara, solo cuando hay acuse previo, las cotizaciones ejecutables
retenidas desde el cierre virtual hasta el deal nativo. El informe inmutable
`reports/expanded-native-controls-20260924/tp-quote-corridors-s17-s18-v5.json`
encuentra cinco piernas con traza TP completa anterior al cierre virtual
(todas de `3168`), una primera pierna de `3106` con acuse y snapshot de
posicion en un segmento archivado, una pendiente (`3171`) y nueve sin traza
TP suficiente (`3102` y el resto de `3106`). Entre las aceptadas, tres
cerraron materialmente despues del primer cierre virtual: `3106` pierna 1
4.793 ms tarde con 61/61 ticks Bid retenidos sobre su TP en el intervalo;
`3168` pierna 3, 476 ms con 8/8; `3168` pierna 1, 1.614 ms con 22/24.
Estas cuentas son puntos discretos de la cinta, no precios del servidor
ni prueba de que el TP estuviera instalado exactamente en cada punto.

El segmento de `3106` enlaza el deal de entrada, la orden inicial sin TP,
la solicitud `MODIFY_SLTP`, la respuesta `10009` y un snapshot con el TP
antes del cierre virtual. No obstante, el SHA del lector usado al capturar
ese segmento ya no coincide con el lector actual y los bytes remotos no se
han releido: el informe marca `full_source_verification=false` y mantiene
esa pierna como evidencia archivada separada de las cinco trazas TP.
El mecanismo pendiente no es solamente esperar el acuse: hay que contrastar
reloj/cinta terminal frente al disparo y fill del servidor y conservar un
intervalo de incertidumbre cuando no exista prueba. No se fuerza una
latencia fija ni se certifica paridad de salida, riesgo o equity de cuenta.
Dos anclas directas del 17/09 y cinco del 18/09 respaldan el offset de
10.800 s en esos dias; los metadatos de la cinta no admiten por si solos
el reloj y el informe conserva `full_historical_clock_admitted=false`.
El informe v5 comprueba ademas `flags` del tick: en esas tres piernas hay
55, 8 y 21 ticks favorables, respectivamente, con el bit de actualizacion
del lado ejecutable (Bid para BUY, Ask para SELL). Eso descarta la lectura
simple de un lado estatico dentro de la cinta, pero no demuestra que el
servidor viera o disparase exactamente esos ticks. Un contador conserva las
tres identidades para futuras pruebas de ejecucion, sin relajar tolerancias.
Los 12 hashes propios del informe v5 coinciden con las fuentes locales;
la salvedad del lector archivado de `3106` permanece. Verificacion focal:
29 pruebas aprobadas. Siguiente evidencia necesaria: captura prospectiva,
acotada y sin nuevas consultas MT5 en la ruta critica, de quote recibido,
estado TP observado y deal nativo; separar reloj de origen, recepcion y
acuse antes de elegir un modelo de ejecucion. Solo diagnostico local; sin
commit, push, despliegue ni lectura nueva de la VM.

Instrumentacion local de la lectura en sombra, 24/09: la ruta existente
`_shadow_live_tick_batch` ahora conserva dos relojes UTC del cliente, justo
antes y despues de leer el lote. El runtime los adjunta solo a los eventos
`strategy_shadow_transition` que ya emitia; no cambia el estado, los hashes,
el numero de transiciones ni anade consultas a MT5 o eventos por tick. Los
relojes se validan como pareja ordenada. La prueba focal de runtime y bucle
de sombra aprobo 90 casos; la suite completa aprobo 7.969 casos. Esto
permitira medir cuando el cliente obtuvo el lote para una decision virtual,
pero no certifica la hora de recepcion de cada tick, la instalacion del TP
en el servidor, el disparo del broker ni el fill. Al ser una lectura local
prospectiva, aun no explica retrospectivamente los tres corredores de TP
discrepantes ni valida el drawdown de cuenta. Para avanzar hace falta
evidencia nueva enlazada por ticket, reloj y fuente, seguida de contraste
de decisiones, exposicion, flotante y drawdown en ventanas completas. Codigo
y documento locales; sin commit, push, despliegue ni lectura nueva de la VM.

Auditoria general de disponibilidad de ticks de sombra, 24/09: el auditor
offline `tools/audit_shadow_read_availability.py` conserva transiciones de
control sin registro en el slice como bloqueadas y distingue tick de origen,
fin de lectura del lote y emision del evento. El informe inmutable
`reports/expanded-native-controls-20260924/shadow-read-availability-week-v2.json`
se reconstruye identico con los archivos y codigo actuales: 14 controles
registrados, 508 transiciones, 502 con tick y reloj de emision, seis sin
tick; 191/502 se emitieron mas de 5 s y 161/502 mas de 60 s despues del
tick de origen. De las 161 mayores de un minuto, 131 corresponden a
`canal2_3096` y 18 a `canal2_2977`, compatible con recuperacion de ticks
historicos, no una medicion de latencia de orden. El slice antiguo carece
de relojes de lectura en las 502: el estado es `missing_read_window`, no
paridad. Los dos controles adicionales sin cesta nativa permanecen en el
denominador del auditor; el comparador de cuenta anterior solo tenia 12.
La fuente gzip, su manifiesto y el codigo quedan ligados por SHA-256; 18
pruebas focales del auditor y el comparador anterior aprobaron. El nuevo
auditor no atribuye fills ni acredita disponibilidad en el servidor.

Comprobacion VM de solo lectura a las 15:05 UTC: SSH al host LAN registrado
respondio con la cuenta `bot`, y los dos ultimos eventos del journal eran
`telegram_poll_coverage` a las 15:05:12 UTC con el mismo commit publicado
`7415941a410d18288fa4e08da76809f70b125efa`. La consulta de los ultimos
300 eventos no contenia transiciones de sombra; no implica ausencia de
operaciones ni de transiciones anteriores. Una direccion privada Tailscale
conocida rechazo la clave actual, pero la ruta LAN funciono. No se cambio,
reinicio ni leyo masivamente la VM. Estos datos prueban escritura reciente
del journal, no salud integral, ausencia de retrasos ni paridad live. La
instrumentacion de relojes sigue **solo local e inedita**; un contraste
prospectivo exige publicacion autorizada y preflight de exposicion/seguridad.

Ventanas adicionales fijadas antes del replay, 24/09: para ampliar los
controles retrospectivos sin escoger por P/L, se declaran 15/09 13:50-14:30
UTC y 18/09 10:55-11:55 UTC, opcion de cierre ambiguo `block`, mismo perfil
de ejecucion explicito no calibrado y offset hipotetico de 10.800 s. En
ambos dias hay ancla directa de reloj; el ledger reconciliado conoce cero
posiciones abiertas al inicio, sin probar ausencia de ordenes pendientes.
La cinta congelada contiene respectivamente 25.607 y 30.466 ticks XAUUSD
en estas ventanas. Se conservaran todos los scopes/obstaculos, y ningun
resultado se usara como OOS ni para elegir una estrategia. La prueba a
realizar es replay causal compartido y, donde las entradas y la rejilla
sean comparables, exposicion/flotante/drawdown por tick y por cartera.

Segunda ampliacion fijada antes de ejecutar, 24/09: cubrir todas las nueve
cestas nativas restantes de los dias con ancla directa, no segun su P/L.
Ventanas UTC: 15/09 15:15-15:45, 17:10-18:05 y 18:25-18:50; 17/09
12:25-13:45, 15:00-16:05 y 16:45-17:10. El ledger reconciliado conoce
cero posiciones al inicio de las seis, y sus cintas XAUUSD congeladas
contienen respectivamente 17.152, 24.749, 12.686, 53.879, 39.170 y
13.285 ticks. Se usa el mismo perfil de ejecucion no calibrado, offset
hipotetico 10.800 s y `optional_close_choice=block`; los bloqueos de FX,
mensajes o cortes permanecen en el recuento. Los dias 14/16 carecen de
ancla horaria directa y quedan fuera de esta puerta, no del universo de
40 cestas. Esta ampliacion sigue siendo retrospectiva y no es OOS.

Control de truncamiento izquierdo declarado tras la primera pasada:
la ventana predeclarada 15/09 18:25-18:50 no genero candidato causal,
porque `canal2_2995` llego en el crudo a las 18:15, antes de la primera
entrada nativa a las 18:34. El ledger plano a las 18:25 no certifica
ausencia de senal pendiente. La ventana original se conserva bloqueada;
se fija **como sensibilidad posterior**, no sustitucion ni OOS, 15/09
18:10-18:50 UTC: cero posiciones conocidas al inicio y 20.820 ticks
XAUUSD retenidos. Misma politica, perfil, offset y opcion `block`. La
ventana 17/09 12:25-13:45 permanece bloqueada por un `LEVEL_UPDATE` de
`canal2_3086` exactamente en el milisegundo de un tick; no se inventa
orden entre ambos para hacer pasar el replay.

Resultado de la ampliacion directa, 24/09: las ventanas nuevas conservaron
los resultados bloqueados en vez de desplazar sus limites para ajustar el
modelo. De las dos primeras, 15/09 13:50-14:30 deja `canal2_2982`
censurada al corte; 18/09 10:55-11:55 interrumpe la trayectoria conjunta
por conversion EURUSD causal insuficiente en 3156/22761. La sensibilidad
18:10-18:50 incluye ya el mensaje de `canal2_2995`, pero tambien queda
bloqueada por FX. En la segunda ampliacion, 15/09 17:10-18:05 y 17/09
15:00-16:05/16:45-17:10 quedan bloqueadas por FX/censura; 17/09
12:25-13:45 queda bloqueada por empate exacto del evento `LEVEL_UPDATE`
de 3086 con un tick. No se genero un informe de replay para este ultimo
ni para la ventana original truncada de 2995; ambos fallos quedan aqui
declarados y los scopes nativos siguen en el denominador.

Dos cestas adicionales de 15/09 si admiten control punto a punto con
fuentes y ledger ligados. `canal1_22592`, informe
`tick-path-s15-1350-1430-block-grid-v1.json`, conserva el mismo drawdown
maximo modelado/nativo de 12,69 EUR, pero difiere en exposicion en 32 de
25.607 ticks y en neto 9,40/8,65 EUR; primera entrada 34 ms anterior.
`canal2_2986`, informe `tick-path-s15-1515-1545-block-grid-v1.json`,
tiene neto igual de +4,33 EUR, pero drawdown 4,94/5,16 EUR, exposicion
distinta en 213 de 17.152 ticks y primera entrada 5.941 ms anterior;
la segunda entra 6.524 ms despues. Su cartera del intervalo solo
contiene esa cesta, y la trayectoria conjunta tambien discrepa. El
perfil sigue no calibrado y la cuenta MT5 continua sin curva independiente.

Los seis controles previos y los nueve intervalos nuevos o de sensibilidad
cubren por identidad las 27 cestas nativas de 15, 17 y 18/09 cuyos dias
tienen ancla horaria directa: 27 identidades unicas, ninguna omitida ni
duplicada; nueve comparables y nueve discrepantes, 18 bloqueadas. Las
otras 13 cestas de 14/16 siguen en el universo de 40 sin ancla directa
de reloj; no se presentan como negativas o positivas. El recuento se
recalculo de los informes escalares fuente-ligados, el control de empate
y `native_week_risk_path_v1.json`. Las dos comparaciones nuevas verifican
hashes de runner, implementacion, crudo, cintas, ledger y riesgo nativo;
no se cambio codigo de produccion ni se hizo push o despliegue. Proxima
puerta: resolver por contrato las causas generales de los 18 bloqueos
(contexto previo, empate y FX), sin usar informacion futura para decidir,
y repetir el contraste temporal. Las igualdades de neto o drawdown aislado
no certifican el simulador.

Clasificacion de los 18 bloqueos de dias anclados: 11 cestas tienen
`stale_conversion` causal directo; tres mas (`2977`, `3148`, `22741`)
pierden la rejilla completa por otra cesta con ese bloqueo en la misma
ventana; tres (`3086`, `3096`, `22725`) comparten la ventana del empate
mensaje/tick; una (`2982`) permanece abierta al corte modelado. Son
categorias de cobertura, no operaciones perdedoras ni casos eliminados.
En las ventanas FX revisadas de 15/09 17:10, 18/09 10:55 y 17/09 15:00,
la cinta EURUSD tiene respectivamente 33, cinco y cinco intervalos entre
ticks mayores de cinco segundos, con maximos de 10.989, 7.162 y 6.959 ms.
Unos pocos intervalos pueden interrumpir un replay compartido largo; no
justifican interpolar un precio futuro para tomar decisiones. Contrato
pendiente: contrastar lecturas monetarias reales y, donde falten, conservar
un rango de sensibilidad que solo admita decisiones invariantes bajo la
incertidumbre declarada. No se ha implementado ni validado ese contrato.

Sensibilidad exploratoria de FX, 24/09, **no admitida como paridad**: con las
entradas nativas fijadas, las cuatro cestas Gold con bloqueo FX directo
examinadas (`2970`, `2995`, `3156`, `3180`) quedan bloqueadas con la edad
causal estricta de 5 s. Usando solo el precio EURUSD anterior y la marca
temporal de la siguiente cotizacion para comprobar retrospectivamente que
el intervalo no supera los 60 s declarados en el contrato monetario del
broker, las cuatro completan el control aislado de gestion con los tres
motores concordantes. Sus netos modelados coinciden con los nativos
(13,43; 4,33; 13,53; 4,36 EUR, respectivamente); los drawdowns escalares
modelado/nativo son 23,82/23,82; 7,55/7,60; 35,11/35,84; 4,64/4,64 EUR.
Esto **no** mide trayectoria completa ni acredita la disponibilidad de FX
en vivo: el control fija entradas reales, omite competencia compartida y
consulta una marca temporal futura, aunque nunca un precio futuro. No se
amplia el limite causal online ni se selecciona una estrategia con estos
resultados. Siguiente prueba: rejilla comun de ticks con exposicion,
flotante y drawdown, conservando ambos limites y las cuatro identidades.

Seguimiento de esa prueba: el auditor local
`tools/audit_conditioned_fx_path_sensitivity.py` produjo el informe
`reports/expanded-native-controls-20260924/conditioned-fx-interval-path-4gold-v3.json`
para las cuatro identidades predeclaradas. El limite causal de 5 s bloquea
la gestion en las cuatro; la sensibilidad retrospectiva de 60 s permite
reconstruir sus salidas y valorar sus caminos con la misma rejilla XAUUSD y
marcas de evento. Todos los saldos finales coinciden, y tambien los cuatro
maximos de drawdown en esta rejilla comun, pero **4/4 caminos difieren**:
`2970` tiene 33/3.905 muestras distintas y diferencia instantanea maxima
de 6,32 EUR; `2995`, 88/3.257 y 0,83 EUR; `3156`, 4/5.583 y 0,99 EUR;
`3180`, 9/14.120 y 0,39 EUR. En las cuatro, las muestras distintas
incluyen exposicion y flotante. El primer informe v1 quedo bloqueado por
una puerta monetaria que v2 identifico como comparacion de `Decimal` con
`float` del JSON nativo; v3 usa conversion decimal explicita, sin alterar
los datos ni el motor. Los informes de ventana para `2970` y `3180`
proceden de un runner anterior: aqui solo se usan como limites de ventana
y se verifican de nuevo crudo, cintas, ledger y politica actuales; sus
salidas antiguas no son evidencia de paridad actual. Este resultado aisla
gestion condicionada por fills, **no** valida entradas, transporte
compartido, dinero de cuenta MT5 independiente ni cobertura causal online.
Siguiente puerta: explicar las divergencias de salida por mecanismo y
probar si decisiones siguen siendo invariantes bajo incertidumbre FX
sin adelantar la siguiente marca temporal en el simulador operativo.

Descomposicion de salida TP, 24/09: el mismo auditor, informe inmutable
`conditioned-fx-interval-path-4gold-v5.json`, une cada pierna por ordinal
logico, no por precio cercano. En las doce piernas de las cuatro cestas,
el modelo sale por `per_leg_target`, antes del cierre nativo y al mismo
precio exacto. Las diferencias por cesta abarcan 6-1.737 ms (`2970`),
901-8.392 ms (`2995`), 3-11 ms (`3156`) y 107-507 ms (`3180`).
Cada primera divergencia de trayectoria coincide con un cierre adelantado
del modelo; todas las muestras distintas incluyen exposicion y flotante.
El corredor de cotizaciones terminales entre salida modelada y fill nativo
contiene, por ejemplo, 28/28 ticks que cumplen el objetivo para la pierna
2 de `2970`, frente a 16/78 para la pierna 1 de `2995`. En el primer caso
no cabe explicar los 1.737 ms como una simple espera de que vuelva a
tocarse el precio en la cinta terminal. Esas cotizaciones **no prueban**
el instante de trigger del servidor ni la instalacion previa del TP;
el segundo caso tampoco prueba por si solo latencia de red. El resultado
es un mecanismo general de discrepancia de reloj de ejecucion TP, no una
correccion calibrada para estas cuatro operaciones. Antes de modificar el
motor, contrastar para una muestra mas amplia los recibos de instalacion,
los ticks terminales y los fills broker, y declarar un contrato de
incertidumbre de ejecucion que no use el fill futuro para decidir en vivo.

Poblacion nativa TP de dias con ancla directa, 24/09: el auditor local
`tools/audit_native_tp_touch_population.py` mantiene las 14 cestas Gold
elegibles y las 46 posiciones en el denominador. Hay 45 deals de salida
con motivo MT5 TP, comentario de objetivo y precio coherentes, y un
cierre no TP separado. En las 45 salidas TP aparece al menos una
actualizacion favorable del lado ejecutable del terminal entre entrada y
fill nativo. La mediana descriptiva entre primera actualizacion y fill es
14 ms; nueve superan 1 s, cuatro superan 5 s y el maximo es 18.800 ms.
Son intervalos **desde el primer toque terminal**, no latencias medidas
del servidor: para 36 TP no existe en esta fuente una cadena de recibos
de instalacion validada. De las nueve posiciones con respuesta aceptada
del objetivo trazable, ocho la tienen antes del primer toque terminal y
una despues. La pierna `canal2_3168`/`2047559220` presenta respuesta
aceptada antes, 21 actualizaciones favorables posteriores y 1.614 ms
hasta el fill; ni esto fecha exactamente el trigger interno del servidor.
Fuentes y conteos en
`reports/expanded-native-controls-20260924/native-gold-tp-touch-population-v1.json`.

El comparador de identidad
`tools/compare_conditioned_tp_first_touch.py` une esa poblacion con los
cuatro controles Gold condicionados mediante cesta, ordinal, hora y precio
de cierre nativo; el informe
`reports/expanded-native-controls-20260924/conditioned-tp-first-touch-4gold-v1.json`
verifica 12/12 salidas del modelo exactamente en la primera actualizacion
favorable del terminal. Asi, la diferencia de exposicion/flotante de esas
cuatro cestas tiene un mecanismo mas concreto: la gestion modelada supone
fill TP inmediato al primer toque visible. El resultado no demuestra que
esa sea la unica causa de discrepancia en las 27 cestas ni proporciona un
retardo fijo generalizable. La siguiente puerta es especificar ejecucion
TP con eventos/recibos observables y incertidumbre donde falten, y probar
la trayectoria completa en una cohorte ampliada sin usar fills futuros
para decidir. Ninguna politica live ni tolerancia causal se ha cambiado.

Punto de implementacion identificado: `research/dubai_iterative/engine.py`
consume `protection.hit(...)` y llama a `_close_position(...)` sobre el
mismo tick; `research/dubai_iterative/oracle.py` hace lo mismo tras
`passive_hit(...)`. El modelo separado
`research/dubai_iterative/broker_execution.py` tambien fecha la salida
pasiva en la cotizacion que cruza el nivel. El retardo de procesado de
un cierre solicitado por el bot corresponde a otro flujo y no corrige
un TP alojado en el broker. Contrato requerido antes de tocar motores:
separar (1) nivel TP solicitado/aceptado, (2) primer toque observado en
terminal, (3) trigger/ejecucion del broker desconocidos hasta recibo o
deal, y (4) valoracion/exposicion mientras el fill es incierto. En
reconstruccion nativa se usara el deal real; en contrafactual, escenarios
de ejecucion predeclarados y bandas de riesgo, conservando decisiones
que cambien entre escenarios como inciertas. Un retardo fijo elegido por
encaje retrospectivo no supera la puerta. La cohorte de 14 cestas/46
posiciones es descubrimiento retrospectivo; hace falta una cohorte
intacta o contraste prospectivo para validar cualquier nuevo contrato.

Primer contrato de eventos separado, solo local, 24/09:
`research/dubai_iterative/broker_execution.py` conserva por defecto la
hipotesis previa `immediate_on_quote`, y ofrece ahora el modo explicito
`observed_fill`. Este ultimo emite un `PassiveTouch` al primer cruce
terminal de un nivel instalado, **sin cerrar la posicion**; solo
`confirm_observed_exit` aplica un deal observado posterior. No sintetiza
latencia ni permite ordenar un fill y un tick empatados sin evidencia.
El modo es una pieza aislada del modelo de broker, no esta conectado a
los motores escalar, rapido u oraculo de busqueda y no altera el bot live.
Las pruebas nuevas comprueban exposicion abierta entre toque y deal,
ausencia de toques duplicados, huecos terminales y relojes ambiguos;
la suite completa con este cambio aprobo 7.988 pruebas. El control
posterior del auditor aprobo otras 39 pruebas focales.

`tools/audit_observed_tp_lifecycle.py` reutiliza la poblacion nativa
fuente-ligada de 46 posiciones y reproduce el tramo desde la respuesta
aceptada del TP hasta el deal real en las nueve con cadena de recibo.
Resultado inmutable
`reports/expanded-native-controls-20260924/native-tp-observed-lifecycle-v1.json`:
9/9 conservan la posicion abierta despues del primer toque y cierran al
deal nativo; las otras 36 TP quedan `blocked_missing_accepted_tp_response`
y la no TP queda separada. El primer toque posterior a la respuesta
coincide con el auditor independiente; el intervalo toque-fill oscila
en este subgrupo entre 2 y 1.614 ms. Esto valida reconstruccion
**observada** de ese tramo, no un fill contrafactual, ni riesgo desde
la entrada previa a la respuesta, ni paridad de cuenta compartida.
Siguiente integracion: propagar estados de TP tocado/fill incierto a
los tres motores y a la trayectoria de riesgo con escenarios
predeclarados, manteniendo equivalencia entre motores y sin usar el
deal nativo como entrada de decisiones hipoteticas.

Sensibilidad de ejecucion TP predeclarada antes de correr, 24/09: universo
de las nueve posiciones Gold de 18/09 con respuesta aceptada del TP en
la poblacion fuente-ligada de 46; las 36 TP sin ese recibo y la no TP
se mantienen como bloqueadas/fuera de este contrato, no desaparecen.
Se contrastaran diez mundos hipoteticos: retrasos de 0, 100, 1.000,
5.000 y 30.000 ms desde el primer toque terminal posterior al recibo,
cruzados con fill al nivel instalado o al precio del lado ejecutable
en el tick de vencimiento. Son puntos de estres, no una distribucion
calibrada ni latencias atribuidas al broker. Entrada, volumen y
contrato EUR proceden de fuentes nativas; la conversion EURUSD y el
riesgo usan el limite causal estricto de 5 s. Se guardaran resultados
de los diez mundos, bloqueos, exposicion y trayectoria monetaria, sin
escoger el mundo que mejor explique el deal observado. Este control es
gestion condicionada, no un replay de entradas ni OOS.

Resultado de esa sensibilidad, 24/09: el primer informe v1 conservo
36 TP bloqueadas por falta de recibo y una salida no TP, pero bloqueo
artificialmente 30/90 mundos de las nueve admitidas por extender cada
caso hasta el ultimo cierre del dia. No se uso para conclusiones.
`tools/audit_tp_fill_scenario_risk.py` limita ahora cada caso a su salida
nativa + 31 s y, despues del primer toque del lado ejecutable, admite
cualquier nueva cotizacion como posible momento de fill. La regresion
sintetica cubre un fill tras actualizar solo el lado contrario y rechaza
un primer toque posterior al cierre nativo. El informe v3 exige ademas
que el dinero observado de cada una de las nueve piernas cuadre al
centimo; v1 y v2 quedan como historial inmutable. Informe vigente:
`reports/expanded-native-controls-20260924/tp-fill-scenario-risk-9accepted-v3.json`:
46 posiciones retenidas, nueve con recibo aceptado x diez mundos = 90
trayectorias de riesgo EUR con FX causal de 5 s; 36 TP siguen bloqueadas
para este contrato y la no TP sigue separada. Ningun mundo se bloqueo
por FX o falta de ticks en este subgrupo. Los 90 difieren de la
trayectoria nativa muestreada; 47 conservan el mismo neto final, 86
conservan el mismo maximo drawdown y cuatro lo elevan hasta 3,29 EUR.
La maxima diferencia de equity por posicion en la malla llega a 8,30
EUR. Son trayectorias **por posicion**, no drawdown de cuenta conjunta.
Los diez mundos son estres no calibrado; no se elige uno por encaje. El
paso siguiente es propagar el estado TP pendiente por las decisiones de
los motores escalar, rapido y oraculo, contrastar trayectorias de cesta
y cuenta, y reservar validacion intacta/prospectiva. El contrato de
ejecucion real del broker y la paridad live completa siguen sin probarse.

Sensibilidad a nivel cesta, 24/09: las nueve piernas admitidas cubren
integramente tres cestas Gold (`canal2_3148`, `canal2_3168`, `canal2_3171`).
`tools/audit_tp_fill_scenario_baskets.py` reconstruye primero sus
entradas y cierres nativos sobre la malla comun XAUUSD/EURUSD de 5 s;
reproduce netos y drawdowns base de 8,28/24,43 EUR, 20,09/65,65 EUR
y 1,75/4,22 EUR, respectivamente. Despues sustituye solamente los
nueve fills TP por cada uno de los diez mundos predeclarados y conserva
36 TP sin recibo y una salida no TP fuera de la cohorte admisible. El
informe inmutable
`reports/expanded-native-controls-20260924/tp-fill-scenario-basket-risk-3gold-v1.json`
contiene 30/30 trayectorias monetarias comparables sin huecos FX; 0/30
son iguales al recorrido nativo, aunque 15/30 conservan el mismo neto
final y 25/30 el mismo maximo drawdown. Cinco escenarios elevan el
drawdown de cesta, hasta 8,33 EUR en la malla; la mayor diferencia
puntual de equity modelada es 8,53 EUR. Es sensibilidad condicionada a
entradas y decisiones posteriores reales, no validacion de una politica
ni prueba de latencia broker. Falta integrarla en los tres motores y
comparar luego con equity/exposicion observadas de cuenta y una cohorte
intacta o prospectiva antes de fiarse del simulador para seleccionar.
Verificacion local de esta fase: `python -m pytest -q` aprobo 7.997
pruebas tras el cambio compartido de ejecucion; las cuatro pruebas de
los dos auditores de sensibilidad aprobaron aparte despues de crear el
control de cesta. Los hashes de los diez ficheros fuente ligados por
cada informe vigente coinciden con el arbol local. Todo permanece local:
sin commit, push, despliegue ni comprobacion nueva de la VM.

Integracion de escenarios TP en motores, 24/09: `ProtectionProfile` acepta
ahora opcionalmente `PassiveFillScenario`, ligado a la identidad de
implementacion y supuestos del experimento; sin el escenario, el modo
anterior no cambia. Un primer toque del TP instalado emite `touched`
y deja la posicion y su flotante abiertos hasta el primer tick en o
despues del vencimiento declarado. Ese fill usa nivel instalado o lado
ejecutable segun el mundo elegido. Las modificaciones de proteccion
que se procesan durante la espera se rechazan como
`passive_fill_pending`; un cierre solicitado por la estrategia puede
ganar antes del vencimiento, y el TP gana cuando ambos vencen en el
mismo tick segun el orden de eventos del motor. Esto modela la
hipotesis explicita de que el primer toque terminal compromete el TP:
**no** prueba el trigger ni la latencia internos del broker.

La semantica esta implementada en el motor escalar, el Numba rapido
(con y sin cola de mercado) y el oraculo independiente. Las regresiones
verifican BUY/SELL, retardo cero, fill al nivel o al quote, ausencia
de quote de fill, rechazo de modificaciones, competencia con cierres
de mercado, dos piernas con vencimientos independientes y 32 recorridos
deterministas adicionales con direcciones/colas/precios variados.
`research/iterative_provenance.py` incluye el contrato transitivo
`passive_fill_contract.py` para impedir reutilizar checkpoints cuando
cambie. Comandos locales: 250 pruebas focales de proteccion/mercado
aprobaron; tras estabilizar el arbol, `python -m pytest -q` aprobo
8.044 pruebas. Una ejecucion anterior de la suite, iniciada antes de
corregir la proveniencia, se detuvo porque mezclaba hashes de dos
estados del codigo; sus fallos de archivo congelado no se usaron como
evidencia de verificacion.

Esto valida consistencia **interna** de motores para el contrato de
estres, no paridad real. La gestion condicionada historica todavia
invoca los motores sin perfil de TP y no reproduce causalmente cada
solicitud/respuesta de instalacion. Siguiente puerta: unir el replay
de gestion con el flujo de recibos disponible en cada instante,
comparar decisiones y trayectorias completas (exposicion, flotante,
drawdown y neto) contra controles nativos de una cohorte mas amplia,
manteniendo bloqueadas las TP sin recibo. Luego reservar contraste
intacto/prospectivo y comprobar la latencia/operatividad real de la VM
antes de usar simulaciones para decidir estrategias. Sin cambios live,
commit, push ni despliegue en esta fase.

Recibos observados, 24/09: el modo `observed_fill` del modelo de broker
ya no instala automaticamente una modificacion en el siguiente quote.
Una solicitud permanece pendiente hasta `confirm_observed_modify` con
respuesta aceptada o rechazada observada; las rechazadas no cambian el
nivel y los eventos sin orden causal demostrable en el mismo instante
se bloquean. Las 44 pruebas focales de ejecucion de broker aprobaron y,
tras estabilizar el arbol, la suite completa aprobo 8.046 pruebas.
La respuesta aceptada fija cuando el TP es conocido por el cliente,
**no** la hora exacta de instalacion en el servidor. En la cohorte de
46 posiciones examinadas, nueve tienen respuesta TP aceptada trazable y 36 no
tienen recibo suficiente (una salida no fue TP). Ocho de las nueve
solo muestran toques relevantes despues de la respuesta; la restante
presenta toques anteriores, el primero mientras estaba en vuelo una
solicitud que luego fue rechazada. Esa ambiguedad no se resuelve
asignando un fill retrospectivo. Todavia faltan el replay causal desde
la entrada con el flujo de recibos, la comparacion de trayectorias
real/virtual y la validacion intacta o prospectiva. Todo sigue local.
