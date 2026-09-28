# Hechos Y Atribucion De Salidas

## Alcance

Continuacion local de `2026-09-08-simulator-causal-hardening.md`, rama
`feature/gold-555-live-trial`, HEAD `08551e9a86aa716473e43b978c13a65f21003b7a`.
Sin nuevas candidatas, capital elegido, cambios de politica/lotes, commit,
push, despliegue, reinicio, conexion MT5 ni ordenes. Los motores no cambian en
esta fase. Se conserva toda la evidencia anterior.

## Contratos Separados

1. Contabilidad por posicion y cesta: deals/costes completos y conciliados.
2. Hechos por deal: posicion, deal, orden, milisegundos UTC, precios de
   entrada/salida, volumen, dinero y mecanismo nativo TP/SL.
3. Secuencia causal completa: decisiones de politica, solicitudes, intentos,
   reintentos y confirmaciones que desembocan en el deal.

El nuevo certificado usa schema 4 / `ledger_bound_entry_and_exit_deals_v4`.
`live_parity.py` exige los hechos de cada salida; `pipeline_truth.py` los vuelve
a calcular desde el ledger y las filas del motor, sin fiarse de banderas
anidadas de exito. Cuatro regresiones reprodujeron falsas aceptaciones con el
mismo dinero pero distinta hora, precio de entrada/salida o mecanismo.

No se compensan errores entre tickets ni entre parciales del mismo ticket.
Se conserva la multiplicidad; parciales simultaneos se comparan como conjunto
con multiplicidad, sin inventar su orden. Se bloquean motivos desconocidos,
manuales/expertos sin traza, close-by, costes de apertura sin asignacion
declarada y dinero desconocido. Los hechos exportados mantienen la identidad
nativa contradictoria y solo declaran precision temporal si la hora completa
se valido. El llamador debe verificar EUR en la captura de cuenta; un campo
de divisa contrario se rechaza, su ausencia no demuestra EUR.

`exit_execution.py` es un diagnostico puro de atribucion. Para un cierre
experto comprueba identidades distintas de posicion/deal/orden, solicitud,
intento, confirmacion, reloj, direccion, simbolo, magic, cantidades y precio.
Un retcode no sustituye un deal. La cardinalidad de la orden se comprueba
contra todos sus deals retenidos, no solo las posiciones seleccionadas.
Una salida pasiva TP/SL valida no requiere una solicitud del bot. Los datos
malformados se bloquean sin inventar hechos. El contrato parcial/multi-deal
activo no esta modelado y no se certifica por inferencia.

La integridad del diario, esta atribucion y la politica son verificaciones
distintas. La barrera `exit_decision_and_deal_sequence_not_verified` permanece
cerrada; no se acepta este diagnostico como atajo para abrirla.

## Auditoria Causal

En las 6.136 filas retenidas, 3.125 son relevantes para el auditor causal.
Ocho terminales rechazados por posicion ya cerrada preservaban un TP existente
cuando la accion habia pedido modificar SL y no TP. El auditor comparaba
incorrectamente ese TP conservado con `new_tp=None`.

La correccion exige un unico intento valido ligado, TP anterior igual al
efectivo, solicitud/preflight compatibles, retcode y cronologia concordantes.
No permite inventar un TP, omitir un TP explicito o confundir conservar con
borrar a cero. Las mutaciones negativas vuelven a bloquear los ocho casos.
El diario pasa de 526 completos, ocho sin evidencia aceptada y 2.591 bloqueados
por dependencia a 3.125 completos para integridad causal. **Esto no significa
que se ejecutaran 3.125 acciones ni que esos ocho rechazos sean operaciones.**
Los hashes de las fuentes originales se conservan.

## Piloto Congelado

Se reutiliza el corte parcial del 07/09 a las 15:02:33.810634 UTC, no un dia
completo nuevo. La captura acredita EUR y offset de servidor +10.800 segundos
para esta muestra; no se generaliza ese offset a otras capturas.

- Ocho senales, 20 posiciones y 20 salidas nativas seleccionadas por las
  aperturas observadas; no las 70 posiciones de toda la captura historica.
- Nueve intentos de cierre: tres deals expertos vinculados directamente;
  17 salidas pasivas, 15 TP y dos SL. Cinco solicitudes de cierre sobre
  posiciones cerradas por TP/SL no se atribuyen como fills del bot.
- Cinco controles Gold, 16 posiciones, una unica ejecucion por los tres
  motores con sus parametros originales: ningun desacuerdo entre motores.
- Coinciden los 16 precios de entrada, 16 precios de salida y 16 volumenes.
  Coinciden los 15 importes comparables; el restante conserva la conversion
  demasiado antigua como desconocida. No se sustituye con una cotizacion futura.
- Las 16 horas de cierre difieren: el broker figura entre 2 y 380 ms despues
  del motor. Se conserva el desfase, sin ampliar tolerancias ni calibrar
  latencias usando esta muestra. Un mecanismo experto queda sin prueba de
  decision equivalente. Ninguna cesta obtiene hechos de salida exactos.

Estos controles estan condicionados a entradas observadas y semantica live,
no son simulaciones independientes de entrada ni evidencia OOS. Los 24
escenarios independientes y 32 controles de la fase anterior no se repiten
porque los motores e inputs permanecen iguales. Tras endurecer validadores,
solo se vuelven a verificar las trazas retenidas, sin ejecutar motores.

## Verificacion

Evidencia en `runtime_data/simulator_exit_sequence_20260908/`, sin sobrescribir
XML rojos, resultados del piloto ni pruebas previas. La revision independiente
reprodujo los casos de orden multi-deal oculta, hechos pasivos invalidos,
entradas malformadas y exportaciones contradictorias. Se incluyen regresiones.

- `pytest_exit_red.xml`: cuatro falsas aceptaciones reproducidas antes de
  incorporar los hechos de salida.
- `pytest_ledger_exports_red.xml`: nueve fallos esperados y un control positivo.
  `pytest_ledger_exports_green.xml`: 163 pruebas aprobadas entre exportaciones,
  comparador, certificador e informe de conjunto.
- `review_binding/binding_20260908T051933200068Z/`: 143 fallos rojos en 145
  nuevos casos; verde final con 182 pruebas aprobadas. Ejecucion focal sin
  red, imports live ni motores.
- `causal_audit/tp_preserved_20260908T044817624308Z/`: regresion roja, 144
  controles offline aprobados y delta del diario con ocho mutaciones negativas.
  La prueba de integracion antes deseleccionada esta incluida en la suite final.
- `pilot/`: las 15 ejecuciones originales de los cinco controles, ledger,
  atribucion y manifiesto de hashes. `pilot_metrics.json` conserva los desfases.
- `pilot_reverification.json`: comprobadores actuales aplicados a esas trazas
  retenidas; mismos estados/diferencias y tres cierres vinculados. Cero
  ejecuciones nuevas de motores y fuentes/artefactos originales intactos.
- `pytest_full_final.xml`: **3.551 pruebas aprobadas**, cero fallos, errores
  u omisiones; 633 avisos, 167,03 s. Python 3.14.2 local, incluida ejecucion
  compilada e integracion con dependencias simuladas, no servicios externos.
- `verification_start.json` / `verification_final.json`: 358 fuentes de
  codigo, pruebas y configuracion sin cambios durante la suite; identidad de
  implementacion/entorno estable. Estado `local_exit_sequence_hardening_verified`;
  paridad integral, OOS nuevo, comprobacion live y publicacion siguen falsas.

Comando: `python -m pytest -q
--junitxml=runtime_data/simulator_exit_sequence_20260908/pytest_full_final.xml`,
ejecutado por el wrapper inmutable `verify_final.py` de este directorio.
SHA-256 del XML final:
`b978242c6f0fdb77d5d273d66ec453e0378f5cfce90e6bee0bbe76934859a875`.
La identidad de los motores compartidos se conserva:
`be66ac9f54d0c70f1e119b09e7ca9cbf155d66ebbbfd841f3a5f06ab44b616d6`.
Los validadores nuevos tienen sus propios hashes en los registros de esta fase.
La suite sustituye la aplicabilidad de las 3.256 pruebas anteriores al codigo
actual; no borra su resultado ni los XML rojos de reproduccion.

## Limites

No existe todavia una comparacion completa de decisiones intermedias y niveles
del motor contra todas las acciones observadas, reintentos/coalescencias y
confirmaciones. Algunos eventos de nivel no llevan identificadores causales;
no se completan por cercania temporal. Tampoco hay una nueva jornada completa
ni validacion futura intacta. El acuerdo de precios de esta muestra y la suite
no garantizan fills alternativos, rentabilidad o ausencia de todo defecto.
