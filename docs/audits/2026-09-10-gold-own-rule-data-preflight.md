# Gold Actual: Preflight De Datos Para Reglas Propias

## Resultado

**Viable preparar un experimento explicito de primera revision NOW recibida;
no esta admitido un estudio monetario ni un intervalo completo de ejecucion.**
El primer dia completo que supera el cribado comun de 60/240 minutos y
caducidad de 15/30 minutos es **28/07/2026: 10 de 10 NOW**. Conserva identidad
canonica y contenido original NOW en las diez. Es candidato para la siguiente
admision por calidad de datos, no una seleccion por resultados ni OOS.

Trabajo local SIDE-CAR, sin dependencia para integrar M7/S1. Rama comprobada:
`feature/gold-555-live-trial`; HEAD `892bc33c8f6c19be7248401ec3cf6a27ba3d0563`.
No se modificaron `research/`, `tests/`, el plan ni inventarios globales. No hubo
estrategias, P/L, busquedas, red, VM, instalaciones, commit, push ni despliegue.
No se hizo una nueva verificacion del bot vivo.

## Conservacion Y Relojes

Gold actual es `-1003908582492`, distinto del historico `-1003828356530`.
Universo de observaciones 22/07-02/09 UTC; corte exclusivo 03/09 00:00 UTC.
Se reutilizo el paquete existente, sin reconstruir el catalogo ni reabrir la
comparacion semantica EURUSD del 23-28/08 ya resuelta.

- **610 registros originales completos** preservados, con hash por registro:
  243 formales, incluidos **242 NOW**; 84 planes de zona siguen fuera de NOW.
- **9.288 recepciones raw** conservadas mediante hash del archivo, numero de
  linea, offset/longitud en bytes y SHA-256 de cada linea exacta. Incluyen el
  contexto previo ya conservado en el paquete; no amplian la cohorte temporal.
- Cada NOW enlaza su texto causal con la recepcion exacta. Ninguna revision se
  adelanta a publicacion ni se utiliza la direccion final para reemplazarla.
- **103 NOW sin original sin editar observado antes del trigger**. En las otras
  139, hay 138 originales NOW y un original no NOW (`canal2_2307`). El escenario
  de contenido original no puede atribuir NOW retroactivamente a esos 104 casos.
- **27 NOW sin identidad canonica inicial**: se conserva el valor ausente. La
  referencia exacta al raw no se presenta como un `message_revision_id` nuevo.
  Su eventual uso exige aceptar explicitamente esa evidencia legacy.
- El escenario independiente de **primera NOW recibida** puede usar un snapshot
  conocido al recibirlo aunque falte el original: **238 cumplen las condiciones
  causales comprobadas**, no la admision del motor ni la de dinero.
- Permanecen bloqueados `canal2_1480` (338 ms), `1487` (8 ms), `1494` (294 ms)
  y `1527` (401 ms): reloj del proveedor posterior al recibido, sin tolerancia
  adicional. El prefijo de todos esos identificadores es `canal2_`.

Publicacion original, timestamp de revision y observacion local son campos
distintos. Conocer despues un original no demuestra que el cliente pudiera
actuar en su instante de publicacion; esa entrada tampoco queda admitida.

## Caducidad Y Horizontes

Antes de cargar Parquet se guardo `frozen.json`: horizontes **60 y 240 minutos**
desde la primera NOW recibida; caducidades **15 y 30 minutos desde publicacion
original raw `date_utc`**, no desde la edicion. No son parametros optimizados.
La igualdad exacta al vencimiento se informa como frontera inclusiva; la regla
ejecutable de cada modo de entrada sigue pendiente. No hay casos eliminados.

| Caducidad | Frescas al recibir | Caducadas | Reloj bloqueado | Total |
| --- | ---: | ---: | ---: | ---: |
| 15 min | 199 | 39 | 4 | 242 |
| 30 min | 201 | 37 | 4 | 242 |

La frescura al recibir no garantiza un tick ejecutable antes de vencer.

| Cribado, siempre sobre las 242 NOW | 60 min | 240 min |
| --- | ---: | ---: |
| Mapa de fuentes seleccionadas disponible | 233 | 233 |
| Fuente ausente: EURUSD 24/07 | 9 | 9 |
| Cruza 00:00 servidor | 0 | 12 |
| Solapa hueco XAUUSD >5 min con minutos abiertos | 0 | 3 |
| Falta cotizacion terminal dentro del dia seleccionado | 1 | 3 |
| FX falla en alguno de los instantes comprobados | 12 | 24 |
| Requiere validacion FX por intervalo, no solo edad <=5 s | 204 | 230 |
| Sin impedimento material detectado en la ventana, no admision | 229 | 212 |
| Ademas causal y fresca a 15 min | 198 | 185 |
| Ademas causal y fresca a 30 min | 200 | 187 |

Los renglones se solapan, no se suman. Los recuentos finales todavia incluyen
casos sin original o sin canonical cuando cumplen el escenario recibido.
Los 242 quedan en ambos horizontes, con mapa diario de paths y hashes exactos.

## Impedimentos Materiales

Las **63 fuentes seleccionadas, 32 XAUUSD y 31 EURUSD**, pasan nuevamente
`VerifiedParquetTickSource.load_day`, hashes de archivo/sidecar, UTC explicito,
normalizacion y orden Bid/Ask, filas, digest semantico y correspondencia entre
epoch del servidor y UTC. No se concatenaron todos los paths de senales: carga
secuencial, conservando solo los frames del dia XAUUSD/EURUSD. Las rutas reales
del manifiesto se mantienen; no se supuso un unico directorio de cache.

Pasar estos contratos **no prueba continuidad de un intervalo**:

- EURUSD 24/07 sigue ausente en las fuentes seleccionadas: nueve NOW preservadas.
- XAUUSD 20/08, **17:23:55.692-22:00:00.342 UTC**, mantiene un hueco con 214
  marcas de minuto abiertas. A 240 min afecta a `canal2_1769`, `1776`, `1781`.
- XAUUSD 31/08, **23:41:32.837-23:57:27.940 UTC**, sigue abierto como incidencia
  (16 marcas de minuto abiertas), aunque no solapa estas ventanas 60/240.
- EURUSD 25/08 **21:44:10.107-21:50:07.254** y 31/08
  **23:41:28.984-23:58:05.255 UTC** siguen documentados, sin solape con estas
  ventanas. No se sustituyen ni se rellenan sus cotizaciones.
- XAUUSD 21/08 termina a **11:08:42.951 UTC**, pese a pasar el contrato de
  adquisicion diaria. Falta final de ventana para `canal2_1826` a 60 min y para
  `canal2_1810`, `1822`, `1826` a 240 min. No se inventa un cierre terminal.
- Las tres NOW recibidas el domingo 23/08 a las 00:18 UTC (`canal2_1836`, `1846`,
  `1849`) no tienen ticks dentro de las ventanas ni FX causal en las sondas de
  entrada/final. Se conservan como recepciones conocidas, no como entradas.

Todos los sidecars seleccionados aportan offset **+10.800 s (+3)**; solo se usa
para convertir el calendario del servidor, sin desplazar otra vez los ticks.
00:00 servidor corresponde a **21:00 UTC**. Las 12 ventanas con rollover tienen
fecha exacta y referencia al sidecar; ninguna pareja de snapshots de swap se
declara comprobada. En las doce falla ademas la sonda FX exacta del rollover.

La conversion reutiliza `_align_conversion` con el contrato real:
**edad maxima 5.000 ms o intervalo positivo entre cotizaciones <=60.000 ms**.
Siempre toma Bid/Ask de la ultima cotizacion anterior, nunca el precio siguiente.
El timestamp siguiente sirve como evidencia historica del intervalo, no como
informacion de precio disponible antes de tiempo. No equivale a permitir
cualquier precio anterior con 60 s de edad. El JSON separa edad estricta,
asistencia por intervalo y fallos; comprueba ticks de mercado mas las sondas de
recepcion, fin y rollover, no todos los futuros eventos de un modelo con latencia.

Quedan sin admitir huecos menores/bordes de sesion, condiciones historicas del
calendario, cotizaciones adyacentes cuando sean necesarias, cola terminal de
ejecucion y eventos monetarios de la futura politica. El umbral de huecos de
60 s es diagnostico, **no una tolerancia de ejecucion aprobada**. Ningun
intervalo obtiene admision automatica porque no se detecte un hueco grande.

Los dos contratos monetarios locales pasan metadatos; eso no certifica costes
del periodo. Comision, fees, swap aplicable y conversion/redondeo por evento
siguen pendientes. Reglas propias que ignoren gestion del canal no necesitan
un fill MT5 por cada NOW. Capital sigue abierto y no bloquea este informe.

## Alertas Para S1

El `_load_scopes` comprobado **ya acepta este catalogo atomico y devuelve las
242 NOW**, sin exigir una operacion MT5 observada por senal. No necesita una
envoltura `provider_catalog` adicional para esta entrada concreta.

Su `_provider_trade` usa `trigger_telegram_utc` como ancla de caducidad: difiere
de la publicacion original en **28 NOW**. Para `canal2_257`, publicacion
06:50:34, revision 07:06:30 y recepcion 07:06:31.469 UTC del 22/07: a 15 min
esta caducada por publicacion pero pareceria fresca por revision. S1 debe
mantener separados esos campos y ligar el ancla al escenario declarado.
El cargador tambien representa las cuatro NOW de reloj futuro sin aplicar
su bloqueo; conservarlas en el denominador no debe convertirlas en admitidas.

Si S1 usa una comprobacion de edad FX simple, no debe sustituir silenciosamente
el contrato 5 s/60 s por `max_fx_age_ms=60000`. Tampoco debe imponer evidencia
MT5 actual como requisito universal del escenario de reglas propias.
No se tocaron esos modulos durante este SIDE-CAR.

## Siguiente Accion

Cerrar la admision temporal y de costes para **el dia completo 28/07**, con
escenario recibido explicito y los limites de ejecucion elegidos, conservando
las diez NOW. Es el primer dia que cumple simultaneamente los cuatro cribados.
Para 60 min/30 min solamente, el primer candidato seria 27/07 (8/8), pero no
cumple los otros tres de forma completa. No se selecciona una estrategia.

Ningun dia se llama OOS: **27 dias tienen reutilizacion previa acreditada**;
el resto conserva uso previo desconocido. El 28/07 es retrospectivo conocido.
Esta entrega no autoriza busquedas ni condiciona la integracion del parent.

## Reproduccion Y Artefactos

Script nuevo: `runtime_data/calculator_delivery_20260910/gold_data_preflight/preflight.py`.
Salida definitiva bajo ese directorio: **`f14b9bbd5f2876f2/`**.
Los borradores anteriores se conservan y no son la entrega definitiva.

Desde la raiz del repositorio:

```powershell
python -B runtime_data/calculator_delivery_20260910/gold_data_preflight/preflight.py
python -B runtime_data/calculator_delivery_20260910/gold_data_preflight/preflight.py --verify runtime_data/calculator_delivery_20260910/gold_data_preflight/f14b9bbd5f2876f2
```

Dos ejecuciones completas de la version final: **25,478 s y 24,672 s**, por
debajo del presupuesto de 300 s. Ambas procesaron exclusivamente las 63 fuentes
seleccionadas y produjeron los mismos bytes. Ocho comprobaciones sinteticas
integradas aprobadas; conservacion verificada de 610 registros, 242 NOW,
9.288 referencias raw, 103 originales ausentes y 27 identidades ausentes.
No se ejecuto la suite del parent. `--verify` comprueba artefactos, codigo,
inputs pequenos y referencias raw; la ejecucion completa revalida los Parquet.

| Artefacto | SHA-256 |
| --- | --- |
| `preflight.py` | `409085d65cf5dcbdaacf4770102f8fa6e6af3277754293dfa0dd961ff2b6ea05` |
| `frozen.json` / identidad de ejecucion | `f14b9bbd5f2876f27e7a1a5f92885a00342d20a29dd9909e8e83a9256c520108` |
| `preflight.json`, 610 registros y 242 NOW | `be3879bd19216e88f58637044257dc7f3e4c03f2498ae123b167b53c9469b09a` |
| `summary.json`, resumen y calidad por dia | `ec45f1596f78879c6c518ef1416361761e973aea85d6fbc9de5577aebc11a44a` |
| `verification.json` | `0514489f3a39479e066285508ab1331cba8507891369dc85c6f461998b9617d5` |

El paquete fuente mantiene identidad
`76bf3764195d99a5e16440e251f4900d93aab0e90b781d75437052eb0dfc3fc9`.
El raw mantiene SHA-256
`da816a254154685a49278543c1b2e038f56a5212d553b4cae53270b5ac91bb17`.
Codigo e inputs permanecieron estables durante ambas ejecuciones. Todo queda
local y sin publicar; la verificacion de hashes no certifica fills alternativos.

## Continuacion: Viabilidad De Tres Dias

**Primer bloque continuo por calidad de inputs: 28, 29 y 30 de julio de 2026.
No cabe en el limite M7/S1 de 2.000.000 cotizaciones.** Se mantienen
`max_signals=40`, `max_quotes=2000000` y `search_candidates=0`. No se ejecuto
ningun motor ni se modifico core, CLI, configuracion de estudio o limites.

Esta continuacion responde a la preparacion de una sesion exploratoria, no a
la validacion de una estrategia. Usa un horizonte minimo fijado de **60 min**,
hueco de mercado **5.000 ms** y FX **5.000 ms / 60.000 ms**, iguales a la
configuracion de inputs del control del 28/07. No lee sus resultados.
El cribado anterior de 240 min no se impone al nuevo requisito de 60 min.

"Como las diez del 28/07" significa aqui **la misma calidad para todas las
NOW de cada dia**, no imponer diez senales ni recortar un dia que tenga once.
Se exige original NOW observado antes o al disparador, identidad canonica,
primera NOW raw disponible causalmente, frescura desde publicacion y todas las
ventanas de 60 min completas en el cribado. Las 27 cumplen tambien la frescura
de **un minuto** de la configuracion de referencia. Se verifican otra vez el
raw exacto, las horas de publicacion/revision/recepcion y los originales; no
se adelanta una edicion ni se utiliza la direccion de una revision posterior.

Se inspeccionan los dias cronologicamente, sin saltar dias intermedios con
triggers. Un fin de semana vacio podria quedar dentro del periodo; en el
primer bloque no hay ninguno. El 22-24/07 no tiene identidades canonicas NOW
completas y el 24/07 carece ademas de la fuente FX seleccionada. El 27/07
conserva sus ocho NOW: `canal2_469` carece de original observado al trigger y
supera 15 minutos desde publicacion. Por eso no sustituye al 28/07 como
inicio; no se ha elegido el periodo por beneficio o comportamiento de precios.

| Dia UTC | Todas las NOW | XAUUSD, filas completas | EURUSD, filas completas | Total fuentes | Ticks en paths de 60 min |
| --- | ---: | ---: | ---: | ---: | ---: |
| 28/07 | 10 | 697.777 | 112.150 | 809.927 | 347.378 |
| 29/07 | 11 | 736.387 | 144.521 | 880.908 | 401.817 |
| 30/07 | 6 | 757.969 | 161.164 | 919.133 | 221.307 |
| **Total** | **27** | **2.192.133** | **417.835** | **2.609.968** | **970.502** |

Periodo unico propuesto, no configuracion ejecutable creada:
**`[2026-07-28T00:00:00+00:00, 2026-07-31T00:00:00+00:00)`**.
Contiene **1.094 recepciones raw, todas canonicas, de 239 identidades de
mensaje raw**. Estas identidades no equivalen a 239 senales: los disparadores
son los 27 NOW. El informe conserva ademas las 610 identidades de catalogo,
242 NOW y referencias verificadas a las 9.288 recepciones del universo previo,
incluidos los dias bloqueados y los casos fuera del universo NOW.

Las seis fuentes mantienen sus paths reales y hashes de archivo/sidecar;
pasan de nuevo contrato, orden Bid/Ask, filas, digest semantico y reloj UTC
frente a epoch de servidor (+10.800 s). La funcion existente `_coverage` de
M7, llamada solo como comprobacion de inputs, no devuelve bloqueos para
ninguna de las 27 ventanas. Los mayores huecos interiores por dia son
4.054, 4.702 y 2.230 ms. Las sondas FX adicionales de trigger/final tampoco
fallan. Esto da **tres dias completos en la capa de cobertura**, no una
admision de dinero/fills ni un dataset o folds ejecutados.

### Bloqueo Cuantificado

- Las **27 senales caben** en 40. Las seis fuentes completas necesitan un
  minimo de **2.609.968** en `max_quotes`, **609.968 por encima** del limite
  actual (+30,4984%). El valor se informa, **no se cambia ni se recomienda
  adoptarlo automaticamente**.
- Los **970.502 ticks de paths** son un segundo contador que puede repetir
  filas por ventanas solapadas. La union sin repetir contiene 707.869 filas
  XAUUSD. Ninguno sustituye el cobro de todas las filas XAUUSD+EURUSD de las
  fuentes declaradas. Reducir el horizonte no cambia ese cobro diario.
- Se comprueba el rechazo real de `_load_tapes` con las seis referencias y
  el presupuesto original: `quote budget exceeded before loading Parquet rows`.
  La prueba impide cualquier decodificacion M7 de frames; no construye el
  dataset combinado ni elude el limite con cargas parciales.
- El unico otro triple de igual calidad en este preflight es **29-31/07**:
  22 NOW y **2.566.583** cotizaciones completas. Es el minimo medido entre
  ambos triples, tambien superior al limite por 566.583. No se sustituye el
  primer bloque por este ni se saltan triggers para fabricar otro periodo.

**Memoria de frames:** se miden las cuatro columnas exactas que carga M7,
sin reducir precision (`time_utc`, `source_time_msc`, `bid`, `ask`): 32 bytes
por fila. Los seis frames separados suman 83.519.768 bytes; los dos frames
concatenados se estiman en **83.519.240 bytes, 79,650 MiB**, con sus indices.
Durante la creacion de las copias privadas canonicas, originales mas copias
sumarian **167.038.480 bytes, 159,300 MiB**. No es una medida de RSS maximo:
faltan arrays y paths, temporales pandas/Arrow, hashes y memoria de los motores.
El analisis carga solo un dia XAUUSD/FX cada vez y no crea el conjunto M7.

El JSON deja el periodo y las seis pruebas de fuente preparados como
**especificacion no ejecutable**. Falta un archivo `source_study` combinado,
conservando todo su denominador, y no se ha creado una configuracion unica
valida mientras el limite lo impide. La sesion exploratoria no queda abierta.
Los tres dias tienen reutilizacion retrospectiva conocida, nunca OOS nuevo.

### Reproduccion De La Continuacion

Solo se anade este apartado y el script/informe aislados bajo
`runtime_data/calculator_delivery_20260910/gold_data_preflight/continuation_*`.
Entrega definitiva: **`continuation_acfa62f6708490dd/`**; el primer borrador
`continuation_d2b0aed33e14f445/` se conserva pero no es la entrega.

```powershell
python -B runtime_data/calculator_delivery_20260910/gold_data_preflight/continuation_three_day_feasibility.py
python -B runtime_data/calculator_delivery_20260910/gold_data_preflight/continuation_three_day_feasibility.py --verify runtime_data/calculator_delivery_20260910/gold_data_preflight/continuation_acfa62f6708490dd
```

Dos ejecuciones de la version definitiva: **2,782 s y 2,815 s**, identicas en
bytes. Ocho comprobaciones sinteticas integradas aprobadas, rechazo del
presupuesto comprobado antes de asignar frames y hashes de inputs/codigo
estables antes y despues. Se revalidan ocho fuentes, incluidas las dos del
31/07 solo para medir el otro triple; no se ejecutan estrategias en ellas.
No se ha corrido la suite del parent ni alterado las entregas M7/S1.

| Artefacto de continuacion | SHA-256 |
| --- | --- |
| `continuation_three_day_feasibility.py` | `ba6a6d14da22522f8f0faa8b0ce677378dd1c6a79365a8b65a99062f36da91bb` |
| `continuation_frozen.json` | `acfa62f6708490dd19b3eeed361279eb11ea383a47ad55503e1e2ab593064f3b` |
| `continuation_report.json` | `c7a68ecbf9505bd6884e7461fa7abc66a802e5668f1b8f62bae6732747f64fa2` |
| `continuation_verification.json` | `a64dc7786832debf4442114c6c645be5d8e5b5cf822c65daaa9e6a99aa8f27fd` |

El preflight original mantiene sus bytes e identidad historica; no se
presenta como una nueva ejecucion bajo el core actual. Todo sigue **local,
sin commit/push/despliegue** y sin nueva verificacion de la VM. Los limites,
el presupuesto de busqueda cero y las configuraciones originales permanecen
intactos.
