# Auditoria Offline De Costes Observados

Auditoria local del 10/09/2026. Rama `feature/gold-555-live-trial`, HEAD
`892bc33c8f6c19be7248401ec3cf6a27ba3d0563`. Cambios previos preservados.
Solo se ha escrito en `runtime_data/calculator_delivery_20260910/observed_cost_audit/`
y en esta auditoria. Sin red, VM, MT5, ordenes, commit, push, suite completa ni
cambios de motores, money, proveniencia, productor forward o colector Euclid.

## Fuentes Y Freeze

Exportacion ya existente, no ejecutada de nuevo:
`runtime_data/calculator_delivery_20260910/historical_probe/observed_costs_20260910T094559_5048/`.
Se vinculan `protocol.json`, `deals_raw.json` y `completion.json`, ademas de los
hashes del exportador y su guard. La consulta declarada comprende 21/07-03/09;
una exportacion completa para esa consulta no certifica todo el historial de
cuenta ni recupera hechos no exportados.

Codigo, pruebas, dependencias locales, runtime y fuentes quedaron congelados a
las **10:13:25.404753 UTC**, antes del calculo. Resultado completado a las
**10:13:34.459008 UTC**. Se conservan 143 proofs de archivos, 276.231.761 bytes
referenciados; no se han duplicado los datos nativos. Incluyen las 63 referencias
Parquet y sus 63 sidecars del `source_readiness_manifest.json` existente.
Las 63 parejas de hashes coinciden con el manifiesto. El dia 28 de julio fue
pedido previamente por calidad de datos, no seleccionado por este resultado.
Las cifras previas aportadas por el usuario no se presentan como evidencia OOS.

Se reutilizan, sin editar:

- `broker_money.py`, `BrokerMoneyConverter.convert_leg`.
- `research/dubai_iterative/dataset.py`, `VerifiedParquetTickSource`.
- Contrato `runtime_data/broker_money_contract_vm.json`, anterior al 28/07.
- Cotizaciones seleccionadas del 28/07: 697.777 filas XAUUSD y 112.150 EURUSD.

No se ejecuta ningun motor de estrategias. XAUUSD comprueba la fuente/reloj;
los precios de apertura y cierre de la comparacion son fills nativos, no fills
simulados. La conversion EURUSD usa el contrato existente, sin ampliar edad de
cotizacion, intervalos ni tolerancias para hacer coincidir posiciones.

## Resultado Completo

| Comprobacion | Resultado |
| --- | --- |
| Deals exportados y conservados | 2.508 |
| Identidades de posicion y filas de auditoria | 1.254 |
| Entradas `entry=0` / salidas `entry=1` | 1.254 / 1.254 |
| Pares estructuralmente validos / bloqueados | 1.254 / 0 |
| Deals sin disponibilidad numerica de commission, fee, swap o profit | 0 |
| Deals con comision distinta de cero | 0 |
| Deals con fee distinto de cero | 0 |
| Deals y posiciones con swap distinto de cero | 12 / 12 |
| Inconsistencias de binding de la exportacion comprobadas | 0 |

Se comprueban identidad de deal/orden/posicion, duplicados sin deduplicacion,
unica entrada y salida, volumen positivo y equilibrado, direccion opuesta,
mismo simbolo, precios validos, milisegundos coherentes con segundos nativos y
salida no anterior a entrada. Ordenar el fichero no sustituye esa comprobacion.
Los registros invalidos o sin identidad se conservarian como filas bloqueadas.

Cada posicion conserva hashes de identidad y evidencia, disponibilidad y suma
de cada campo monetario, incluidos costes de apertura y de salida. Un campo
ausente o no finito queda desconocido, no cero. Los ceros de comision y fee son
observaciones de esta exportacion; no certifican un modelo universal de costes.
El swap nocturno no se supone cero ni se extrapola a operaciones alternativas.

## Control Del 28 De Julio

Denominador fijado antes de resultados: **todas las posiciones con cualquier
deal en el dia**, conservando la posicion entera y tambien las que tuviesen
pertenencia temporal desconocida. No se limita a entradas de ese dia ni a
posiciones cuyo calculo coincida. Se usa el escenario de offset 10.800 s
de los sidecars de ticks, explicitamente separado de una prueba independiente
del reloj de cada deal nativo.

| Estado dentro del denominador | Posiciones |
| --- | --- |
| Total declarado del 28/07 | 60 |
| Concordancia monetaria diagnostica | 56 |
| Discrepancia monetaria conservada | 1 |
| Bloqueadas por cruce de dia UTC o del broker | 3 |
| Pertenencia temporal desconocida | 0 |
| Otras posiciones de la exportacion, retenidas fuera de este control | 1.194 |

De las 56 concordancias, **28 son importes cero sin necesidad de cotizacion de
conversion**. Los otros 29 casos computados requieren conversion: 28 concuerdan
y uno discrepa. Todos usan cotizacion dentro de la edad maxima; no se uso el
caso de intervalo posterior para justificar cotizaciones antiguas. Las tres
posiciones nocturnas mantienen sus costes observados, pero no se habilita
`allow_overnight` para el control de precio.

La discrepancia queda identificada unicamente por el hash de posicion:
`e9d2a4c3118ab55ea86c6513d3916fc158cdc43876a94084d0be9a0f90b1a791`.
Motivo `native_profit_conversion_mismatch`. Su importe y diferencias por campo
permanecen en el artefacto local privado. No se ha cambiado el motor, el contrato,
la fecha ni la tolerancia despues de observarla; no se ha filtrado esa posicion.
Los tres bloqueos nocturnos tambien conservan sus hashes y motivos individuales.

## Limites De La Afirmacion

La salida es **contabilidad observada diagnostica, no validacion del motor**.
El contrato monetario usado no permite vincular inequivocamente la identidad de
cuenta exportada. Tampoco esta demostrada independientemente la continuidad
historica de sus condiciones de simbolo ni el reloj UTC de cada deal.
La moneda EUR procede de la finalizacion nativa exportada, no de asumirla a
partir del resultado. Los matches no cierran esas limitaciones.

El control compara P/L de precio a precision de moneda sin tolerancia. Cuando
se muestra una diferencia ajustada por costes, usa los costes nativos de ambos
deals, no una prediccion de comision/fee/swap. Ese ajuste contable no es evidencia
independiente de que los costes contrafactuales esten modelados correctamente.
Los datos solo estan disponibles aqui como exportacion retrospectiva: no se
atribuye disponibilidad causal en el momento de decidir una operacion.

No hay suma de rentabilidad de estrategias, ranking, seleccion ni certificado
contrafactual. No se infieren canal, senal o estrategia a partir de comentarios
o magic. Cuenta, tickets, ordenes, posiciones y comentarios no se publican:
se usan hashes; los valores contables por posicion quedan en archivos locales.

## Reproduccion Y Checks

Directorio base: `runtime_data/calculator_delivery_20260910/observed_cost_audit/`.
Contiene el script, pruebas, informes RED/GREEN, `frozen_v1/protocol.json`,
`results_v1/audit.json`, `results_v1/completion.json` y `verification_checkpoint.json`.

Comprobaciones realizadas:

- 30 pruebas sinteticas aprobadas, cero fallos/errores/omisiones. Cubren pares,
  ausencias, duplicados, costes de apertura, swap observado, diferencia de un
  centimo, cotizaciones futuras/antiguas, medianoche del broker, todos los
  denominadores, sidecars invalidos, mutacion de fuentes, privacidad e inmutabilidad.
- `verify` recalculo toda la auditoria con los mismos bytes y produjo el mismo
  SHA-256. Volver a usar `results_v1` para escribir fue rechazado sin modificarlo.
- El CLI bloquea conexiones, resolucion de red, procesos hijos e importacion
  de MetaTrader5. El exportador y su guard solo se hashean; no se importan.
- No se ejecuto la suite completa, reservada al coordinador.

Desde la raiz del repositorio, para verificar el archivo existente:

```powershell
$base = 'runtime_data/calculator_delivery_20260910/observed_cost_audit'
python -B "$base/audit_observed_costs.py" verify --protocol "$base/frozen_v1/protocol.json" --out "$base/results_v1"
python -B "$base/test_observed_cost_audit.py"
```

Para otro archivo de resultados con exactamente el mismo protocolo, `run`
requiere un directorio nuevo dentro de `$base`. `freeze` tambien exige uno
nuevo. Los sources se comprueban antes y despues; codigo/runtime/datos cambiados
invalidan la reproduccion de este archivo. No sobrescribir ni reutilizar el
protocolo como si fuese una nueva evaluacion prospectiva.

```text
Native raw SHA-256: 058b643cecc0aa74f99e17464ca7363af60fd6fd45afa6eb8e19985cad67a93a
Readiness SHA-256:  a76effdefbe0fcf8f0b997c836d1e35e744239aecddca15101a83779a0dc536b
Script SHA-256:     d6b9ac7f26e0aa0dac1e00ab4549d34c240ad03cff8e733e5c8c2712a8e915d3
Protocol SHA-256:   cdb128e54badb3528e5aaea3075f97fcd67079f37b2c49f66f8fb1244c51b9ef
Audit SHA-256:      e3b1a813b3106e50faa7f08c148e276792853e7162102786ccc5fe347ff0114b
```

Runtime congelado: CPython 3.14.2, numpy 2.4.4, pandas 3.0.2, pyarrow 23.0.1,
win32. Quedan para el coordinador la fuente historica del 28, la revision de la
discrepancia sin exclusiones, las limitaciones de binding y la verificacion
conjunta del conjunto estable, incluido el nuevo colector. Nada se ha publicado.

## Apendice: Diagnostico De La Discrepancia, Sin Reclasificacion

Continuacion autorizada exclusivamente para la posicion `e9d2...1a791` ya
identificada arriba y para el posible binding de cuenta. Archivos nuevos en
`runtime_data/calculator_delivery_20260910/observed_cost_audit/diagnosis_v1/`.
No se modifican el script original, protocolo, resultado, flags ni conteos.
Los tres casos nocturnos no se recalculan ni se amplia su alcance.

**Mecanismo del flag demostrado:** `reconcile_pair` del auditor original
resta directamente el decimal nativo del valor ya redondeado por el conversor,
sin cuantizar primero el nativo a la precision de cuenta declarada. La fuente
JSON conserva `2.2800000000000002`; el calculo devuelve `2.28`. La diferencia
exacta calculado menos nativo es **`-2E-16 EUR`**, no un centimo.
Los dos valores corresponden a floats binary64 adyacentes. A los dos decimales
declarados por el contrato ambos se expresan como `2.28`; esta observacion
aritmetica diagnostica no cambia la tolerancia ni convierte el flag en match.

Se reconstruye la formula con los mismos fills y volumen de entrada/salida,
sin identificar tickets en el informe publico. SELL XAUUSD, 0.01 lotes, factor
contractual 100: resultado de precio positivo de USD 2.59. Por la orientacion
EURUSD del contrato, ese signo requiere division por **Ask**, no Bid.
Se recupera la ultima cotizacion no posterior al fill: antiguedad **292 ms**,
dentro de los 5.000 ms originales. Bid y Ask exactos, precios de ambos fills,
formula sin redondear, resultados binarios y costes quedan en `diagnosis.json`.
Comision, fee y swap de este par son cero observados, no supuestos nuevos.

La cotizacion reproduce exactamente la huella original:
`a24e9c9b39533b59014c218b1169065150cffa23034c118db400762e4e9af728`.
La formula independiente vuelve a `2.28` usando el mismo `ROUND_HALF_UP` y
precision de moneda del contrato. **Cotizaciones alternativas probadas: 0.**
El origen interno de la cola binaria del importe nativo no esta observable en
esta exportacion. Tampoco se identifica la cotizacion interna usada por el
broker; reproducir la nuestra no prueba que el broker usara esa misma.

**Binding de cuenta:** se inspecciona exactamente `broker_money_contract_vm.json`
con SHA-256 `9c78647697b0c34c29816cbf482bd265008679786d2379eaaf65fc4bd36b04f0`.
Su esquema superior es `account`, `captured_at_utc`, `conversion`, `costs`,
`instrument`, `live_validation`, `schema_version`.
El esquema de `account` es `currency: str`, `currency_digits: int`, `server: str`.
`broker_metadata_present=false`; no hay campos `login`/`account_login` en el
documento, ni fingerprint de cuenta. El servidor existe, pero no se imprime.
No puede calcularse `sha256(server:login)` sin inventar el login.
Por ello `comparison_performed=false`, `positive_binding_established=false`,
estado `unproven_missing_server_login_pair`. No se declara una cuenta distinta:
la comparacion simplemente no es posible con estas fuentes. Solo se conservan
esquema, booleanos y hashes, sin nombres, login ni credenciales.

Verificacion: 10 pruebas sinteticas aprobadas, incluida diferencia real de un
centimo que no se clasifica como residuo, frontera de redondeo, identidades
incompletas/completas y ausencia de secretos en el resultado. Recalculo del
diagnostico identico byte a byte. `sources.json` vincula codigo y fuentes antes
del calculo diagnostico; es analisis posterior del caso, no evidencia OOS.

```powershell
python -B runtime_data/calculator_delivery_20260910/observed_cost_audit/diagnosis_v1/diagnose.py verify
```

SHA-256 del diagnostico:
`258c5609d1660f5c1cabc760b3bf99d9d9d50ed8774edd2926f2cee69108d402`.
El resultado original mantiene SHA-256 `e3b1a813b3106e50faa7f08c148e276792853e7162102786ccc5fe347ff0114b`
y conserva **56 matches, 1 mismatch, 3 bloqueos**. Sin cambios al nucleo,
money, flags, tolerancias ni seleccion de cotizacion; sin red, VM o publicacion.
