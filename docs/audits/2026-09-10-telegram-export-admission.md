# Preparacion Causal De Exportaciones Telegram

Estado: implementado y verificado SOLO LOCAL, sin commit, push, VM, red, ordenes,
mensajes externos ni nueva busqueda de candidatas. No certifica precios,
rentabilidad ni disponibilidad total del historico para el motor.

Base inspeccionada: `892bc33c8f6c19be7248401ec3cf6a27ba3d0563`, rama
`feature/gold-555-live-trial`, con cambios previos preservados. Se leyeron
`AGENTS.md`, `docs/development/research-contracts.md` y la seccion
Historico Telegram Confirmado del mapa de preparacion. No se editaron esos
documentos, engines, datasets ni otros frentes.

## Contrato Temporal

Periodo declarado: `[2026-01-01T00:00:00Z, 2027-01-01T00:00:00Z)`, UTC.
Se usan exclusivamente `date_unixtime` y `edited_unixtime`, con precision de
segundos. El campo `date` sin zona, los nombres de carpeta y los mtimes no
establecen ni publicacion UTC ni fecha de exportacion.

Tres salidas distintas; ninguna se elige automaticamente para una estrategia:

- `receipt`: exige recepcion observada. Estas fuentes no la contienen;
  todos los IDs quedan bloqueados con `receipt_unknown`.
- `publication_initial`: hipotesis de disponibilidad a la publicacion y latencia
  cero, SOLO para snapshots sin marca de edicion. Ausencia de marca se declara
  como supuesto del experimento, no como recepcion medida. Un snapshot editado
  sin version inicial queda bloqueado con `initial_version_missing`.
- `revision_time`: otro experimento, que supone disponible el contenido
  exportado a la hora de su ultima edicion, o de publicacion si no hay marca.
  No demuestra el contenido original, una recepcion real ni la historia completa
  de ediciones. Los dos relojes, publicado y revision, permanecen separados.

La admision textual reutiliza `parser.is_canal2_entry` y `parse_canal2`, puros,
para ambos canales confirmados. Solo se admiten comandos textuales NOW
reconocidos sin tokens BUY y SELL contradictorios. No se exige SL/TP del
proveedor, no se usa su gestion y no se predicen niveles. Otras instrucciones
con direccion, zonas, comentarios, medios y stickers quedan inventariados con
motivos; no se afirma que el parser cubra toda la gramatica historica.

Se selecciona el primer disparador reconocido de cada identidad segun el
escenario, nunca se reinicia una entrada fuera de periodo usando una revision
posterior. Conflictos de publicacion o contenido al mismo reloj bloquean esa
identidad. Este veto conservador usa el inventario retrospectivo completo; no
se presenta como clasificador online que conoce futuras ediciones.

## Inventario Exacto

Fuentes bajo `C:/Users/josea/Downloads/Telegram Desktop/`, siempre read-only.
Cada nombre de la tabla designa el archivo `result.json` de esa carpeta.

| Fuente | Chat | Filas | En 2026 | Fuera de 2026 | Fecha desconocida |
| --- | --- | ---: | ---: | ---: | ---: |
| ChatExport_2026-04-29 | 1642806869 | 18541 | 2295 | 16246 | 0 |
| ChatExport_2026-07-05 | 1642806869 | 639 | 639 | 0 | 0 |
| ChatExport_2026-06-30 | 3828356530 | 2669 | 2669 | 0 | 0 |
| ChatExport_2026-07-05 (2) | 3828356530 | 807 | 807 | 0 | 0 |
| Total de filas fuente | ambos, sin deduplicar | 22656 | 6410 | 16246 | 0 |

| Union por identidad | IDs | En 2026 | Fuera de 2026 |
| --- | ---: | ---: | ---: |
| Dubai 1642806869 | 19180 | 2934 | 16246 |
| Gold historico 3828356530 | 2797 | 2797 | 0 |
| Total | 21977 | 5731 | 16246 |

Gold actual `3908582492` NO esta unido ni admitido por este adaptador. Un chat
desconocido se inventaria pero bloquea sus disparadores.

Extremos de publicacion UTC por fuente, en orden de la primera tabla:

- `2023-07-05T22:23:30Z` a `2026-04-29T13:23:13Z`.
- `2026-06-03T12:42:12Z` a `2026-07-03T17:12:54Z`.
- `2026-03-28T10:32:49Z` a `2026-06-30T09:24:12Z`.
- `2026-06-08T06:53:39Z` a `2026-07-03T16:00:15Z`.

Los extremos no prueban continuidad ni cobertura de precios. El periodo anual
declarado no implica que se disponga de mensajes hasta diciembre.

SHA256 comprobados contra las cuatro fuentes confirmadas, en el mismo orden:

1. `1040dc9aa4bebf7467b930d673ee9cbd03dfb7f2ef89e9585c93591447f61f17`
2. `945ef1c65ac4d9ed0a589c5227ba25f7f3672a897f3e50c9b77ca629946ab953`
3. `84d6e49cfe1ef5775fdc9711e293ab9ea8d7dbac07241214aee7f6769998ca11`
4. `0aee6de28a3c2a3bfee06433c0a8f24a5b012153d916afa4262e4a2e467c7224`

## Duplicados Y Revisiones

Los 679 solapamientos pertenecen a Gold. Se conserva cada ocurrencia con indice
en su fuente, SHA256 del archivo, ID original y snapshot JSON completo.

- 664 ocurrencias repetidas son snapshots JSON estructuralmente identicos.
- 678 son repeticiones de la misma revision normalizada; no son nuevas entradas.
- Hay 21978 revisiones exportadas conocidas para 21977 identidades.
- Una identidad, Gold `2674`, tiene dos relojes de edicion: `09:23:00Z` y
  `09:25:02Z` del 30/06/2026. Ambas conservan el mismo texto normalizado; no se
  inventa una version textual diferente. Publicacion: `09:20:10Z`.
- No hay identidades con dos textos normalizados distintos en estas fuentes.
- 1005 grupos de texto no vacio se repiten entre IDs distintos del mismo chat.
  Se registran, pero esos IDs no se fusionan ni se eliminan del denominador.

El hash de revision incluye texto normalizado, descriptores de medios, tipo,
reply, publicacion y reloj de edicion. No depende de rutas locales de archivos
ni contadores de reacciones. El snapshot completo SI conserva todos esos
campos. Igualdad de descriptores o de texto no demuestra igualdad de bytes de
medios; no se han leido, descifrado ni clasificado stickers.

## Admision Del Periodo

Admitido significa exclusivamente disparador textual preparado bajo el
escenario indicado; NO una operacion, precio ejecutable o dataset certificado.

| Cohorte, IDs dentro de 2026 | Recepcion admitidos/bloqueados | Publicacion inicial admitidos/bloqueados | Revision admitidos/bloqueados |
| --- | ---: | ---: | ---: |
| Dubai, 2934 | 0 / 2934 | 36 / 2898 | 441 / 2493 |
| Gold historico, 2797 | 0 / 2797 | 0 / 2797 | 453 / 2344 |
| Total, 5731 | 0 / 5731 | 36 / 5695 | 894 / 4837 |

Ademas permanecen 16246 IDs Dubai fuera del periodo, bloqueados en las tres
salidas. En el denominador completo de 21977 IDs: recepcion 0/21977,
publicacion inicial 36/21941, revision 894/21083.

Hay 894 identidades con comando NOW reconocido en 2026: 441 Dubai y 453 Gold.
De ellas, 858 no tienen version inicial disponible: 405 Dubai y las 453 Gold.
Fuera del periodo hay otras 215 identidades Dubai con comando reconocido.
Estos recuentos tampoco se denominan operaciones.

Motivos de no admision de contenido en 2026, escenario `revision_time`:

| Motivo | Dubai | Gold historico |
| --- | ---: | ---: |
| `unknown_sticker_direction` | 431 | 0 |
| `media_without_text` | 443 | 5 |
| `ambiguous_direction` | 16 | 8 |
| `unresolved_directional_text` | 142 | 11 |
| `zone_plan_not_immediate` | 52 | 4 |
| `service_or_unknown_message_type` | 7 | 5 |
| `no_explicit_entry` | 1402 | 2311 |

En publicacion inicial, `initial_version_missing` afecta a 2108 IDs Dubai y
2790 Gold dentro de 2026, incluyendo mensajes que no son entradas. Los motivos
pueden solaparse: nunca se suman como si fueran casos independientes.

Disparadores seleccionados respaldados por cada fuente, publicacion/revision:
32/343, 4/98, 0/439 y 0/105 respectivamente. Las membresias de fuente Gold se
solapan; no se suman para obtener la union. `sources.json` identifica tanto
estas membresias como la admision de las identidades presentes en cada fuente.

## API Y Artefactos

Nuevos archivos exclusivos de este frente:

- `research/telegram_export.py`: `prepare_exports`, `write_admission`,
  `load_admission` y `to_causal_signals`.
- `tools/prepare_telegram_exports.py`: CLI con periodo explicito, fuentes y
  hashes esperados opcionales en el mismo orden.
- `tests/test_telegram_exports.py`: pruebas enfocadas.
- Este documento.
- `runtime_data/historical_admission_20260910/`: archivo local ignorado por Git.

El archivo contiene `manifest.json`, `sources.json`, `summary.json`,
`occurrences.jsonl`, `identities.jsonl`, `text_duplicate_groups.jsonl` y
`triggers_receipt.jsonl`, `triggers_publication_initial.jsonl`,
`triggers_revision_time.jsonl`. Las salidas vacias se conservan.

Identidad del archivo auditable:
`7f4dd933b3d0b6cd9f674c2ab6a326f7934de8bf58cf6edf78b8be9743f49234`.
Los ocho artefactos de datos suman 72075517 bytes, mas el manifiesto.
El manifiesto incluye hashes de todos ellos, de las fuentes, del adaptador y
del parser, contrato temporal y Python. Repeticion identica permitida;
contenido diferente en la misma salida produce error, sin sobrescribirlo.
Cambios de implementacion o datos requieren un directorio nuevo.

Siguiente interfaz, sin cambiar ningun engine:

```python
from research.telegram_export import load_admission, to_causal_signals

bundle = load_admission("runtime_data/historical_admission_20260910")
signals = to_causal_signals(
    bundle, scenario="revision_time", chat_id=3828356530,
)
```

`load_admission` verifica manifiesto y bytes retenidos. No afirma que haya
vuelto a verificar las fuentes originales. `to_causal_signals` exige escenario
y chat explicitos y produce `research.causal_replay.CausalSignal`, entrada de
`research.causal_replay.make_path`. La identidad conserva el chat real, por
ejemplo `telegram_export:3828356530:2674`; no colisiona con Gold actual.
El campo del engine `observed_at` contiene el reloj HIPOTETICO elegido, no una
recepcion medida. El manifiesto y este escenario deben acompanarlo en cada run.
`published_at` sigue siendo la publicacion original. En Gold, el engine conserva
su ancla de caducidad en esa publicacion; una revision tardia puede caducar
segun la politica. No se refecha para eludir esa regla.

El bridge entrega `provider_events=()`. El siguiente paso requiere fijar el
escenario y aportar Bid/Ask, cobertura, conversion, costes, ejecucion y reglas
propias al constructor existente. La prueba con cinta sintetica verifica esta
interfaz, no esos datos historicos. No se pasa una recepcion falsa a loaders de
logs observados ni se fabrica un catalogo certificado o fills MT5.

## Verificacion

TDD de comportamiento nuevo: 23 fallos esperados y 1 prueba de aislamiento
pasando con el contrato sin implementar; despues 24/24 verdes. La ampliacion
de recarga inmutable y grupos de texto produjo 2 fallos y 28 pases antes de
implementarse; despues 30/30 verdes.

Comando enfocado, ejecutado con Python 3.14.2:

```powershell
py -3.14 -m pytest -q tests/test_telegram_exports.py tests/test_causal_replay.py
```

Resultado: **50 passed**. No suite completa ni procesos costosos simultaneos.
Incluye ediciones, conflictos, duplicados, faltantes, JSON ambiguo, periodos,
cohortes, proteccion de fuentes, CLI/hash incorrecto, archivo inmutable,
recarga verificada y conexion al constructor de paths con precios sinteticos.
La importacion del adaptador y del bridge no carga MT5, listener, classifier
ni Telethon.

Preparacion real ejecutada con salida 0:

```powershell
py -3.14 tools/prepare_telegram_exports.py `
  --source 'C:/Users/josea/Downloads/Telegram Desktop/ChatExport_2026-04-29/result.json' `
  --source 'C:/Users/josea/Downloads/Telegram Desktop/ChatExport_2026-07-05/result.json' `
  --source 'C:/Users/josea/Downloads/Telegram Desktop/ChatExport_2026-06-30/result.json' `
  --source 'C:/Users/josea/Downloads/Telegram Desktop/ChatExport_2026-07-05 (2)/result.json' `
  --expected-sha256 1040dc9aa4bebf7467b930d673ee9cbd03dfb7f2ef89e9585c93591447f61f17 `
  --expected-sha256 945ef1c65ac4d9ed0a589c5227ba25f7f3672a897f3e50c9b77ca629946ab953 `
  --expected-sha256 84d6e49cfe1ef5775fdc9711e293ab9ea8d7dbac07241214aee7f6769998ca11 `
  --expected-sha256 0aee6de28a3c2a3bfee06433c0a8f24a5b012153d916afa4262e4a2e467c7224 `
  --start 2026-01-01T00:00:00Z --end 2027-01-01T00:00:00Z `
  --output-dir runtime_data/historical_admission_20260910
```

Verificacion adicional ejecutada con salida 0: `load_admission` verifico los
hashes; cada una de las 22656 filas retenidas se comparo estructuralmente y en
orden con el JSON original de su fuente; los cuatro SHA256 originales
seguian identicos; nueva `prepare_exports` produjo un bundle igual; nueva
`write_admission` devolvio el mismo archivo; los seis pares cohorte/escenario
produjeron exactamente sus recuentos al cruzar el bridge.

Quedan abiertos precios y continuidad, semantica no cubierta, recepciones,
versiones iniciales ausentes, validacion de ejecucion/dinero y uso previo de
cohortes. Recien localizar estas fuentes no acredita OOS intacto. No se
certifica el historico entero ni se inicia la busqueda masiva.
