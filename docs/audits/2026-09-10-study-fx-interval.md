# M7: Intervalo FX Explicito Y Proteccion Del Puente

## Entrega

Implementacion local y estable para revision. Sin commit, push, VM, operaciones,
busqueda ni motores sobre datos reales. No se modificaron CLI, provenance,
forward, `_align_conversion` ni configuraciones reales. No hay admision monetaria.

- `make_path(..., max_fx_interval_ms=None)` conserva por defecto la alineacion
  estricta anterior, incluidas las matrices de edades y las cotizaciones ausentes.
- M7 acepta el campo opcional `max_fx_interval_ms`: entero, no booleano, entre
  `max_fx_age_ms` y 60.000. Omitirlo normaliza el intervalo a la edad; `null`
  explicito en JSON no es valido. La configuracion de disco no se reescribe.
- El opt-in de intervalo mayor reutiliza `_align_conversion`: ultimo Bid/Ask
  anterior; el timestamp siguiente solo acredita el intervalo historico. No usa
  el precio futuro, interpolacion ni una edad generica de 60 segundos.
- Cobertura M7 y construccion del path usan la misma funcion de alineacion.
  La cartera M7 recibe ambos limites. El puente expone
  `StudyDatasetBundle.max_fx_interval_ms` para que CLI/S1 pase ese mismo valor
  como `max_conversion_interval_ms` al construir la cinta canonica.
- Protocolo, resultados, inventario, identidad del bundle y evidencia FX del
  path indican ambos valores y el modo: `strict_prior_quote_age` o
  `historical_bracketed_prior_quote`, siempre `declared_hypothesis`.

La opcion no certifica continuidad, costes historicos ni dinero. Se mantienen
los gates, denominadores, clocks de publicacion original y politica de no OOS.
El caso legacy de edad cero en `make_path` permanece estricto; no habilita el
intervalo historico, cuyo helper exige edad positiva. M7 sigue exigiendo edad
positiva en configuracion, como antes. Las alertas legacy Gold no se corrigen
mediante tolerancias nuevas.

## Dos P2

Los dos casos de Lorentz se reprodujeron antes del arreglo.

1. `_CanonicalSource` conserva copias privadas de frame/evidence y devuelve
   copias independientes, tanto por propiedades como por `load_day`. Un
   fingerprint en memoria detecta cambios internos antes/despues de cargar
   un dia. `bundle.verify_sources()` comprueba ambas cintas contra sus bindings;
   cambiar cotizaciones, evidencia o el intervalo del bundle no queda invisible.
2. El puente comprueba su snapshot y tambien `fixed._IMPORTED_SOURCES` mediante
   `_check_loaded_sources`. Detecta M7 importado antes de cambiar sus archivos,
   aunque el puente se importe despues. M7 sigue prohibiendo importar search;
   solo el puente permite los dos modulos de busqueda offline ya autorizados.
   No hay monkeypatch de gates para ejecutar una busqueda.

## Verificacion

Base previa: 86 pruebas aprobadas. Regresiones independientes de Lorentz:
dos fallos reproducidos. Final: **128 aprobadas en 18,00 segundos**:

```powershell
python -B -m pytest -q tests/test_causal_replay.py tests/test_strategy_study.py tests/test_strategy_study_dataset.py tests/test_study_fx_interval_contract.py tests/test_study_dataset_review.py
```

Incluye las 13 pruebas originales del puente, seis nuevas de aislamiento y
mutacion, 34 nuevas de FX y las dos regresiones independientes sin modificarlas.
Cubren default estricto, ambos precios Bid/Ask, alteracion de precio futuro,
intervalo excesivo, ausencia de siguiente/anterior, timestamps duplicados,
tipos/rangos invalidos, prueba de reloj, paridad M7/puente/cartera y denominadores.
Solo las fixtures sinteticas ejecutan motores. Un aviso Pandas procede del
intento deliberado de modificar una copia en el test independiente; no se oculto.

Inspeccion exclusivamente de inputs del control real 08/09 con
`load_study_dataset`, sin `run_study` ni simulaciones: **117 identidades, 11
triggers, 9 data_ready y los mismos 2 data_blocked por FX**:
`canal2_2612`, `canal2_2640`. Otros 49 admission_blocked y 57 outside_universe
siguen presentes. Edad/intervalo normalizados: **5.000/5.000 ms**.
SHA-256 de `gold_fixed_control_config.json`, igual antes/despues:
`6cd5491a7c9240036e3417656a68eb2c6a55c6989fa757e40f62acb889d8c119`.

No se cargo ni ejecuto el nuevo control real Jul28. Su ejecucion y la suite
completa quedan para el parent tras revision. Nuevos campos normalizados y
codigo nuevo cambian la identidad de ejecucion; no validan retrospectivamente
un archivo de resultados anterior ni autorizan reescribirlo.

## Identidad Entregada

| Archivo | SHA-256 |
| --- | --- |
| `research/causal_replay.py` | `137f229c508af21c102ec416f679727115fb482c91bb52a1363abdf008fb7cdf` |
| `research/strategy_study.py` | `cc1aebff4c008372710e888650e2a1c3e281e42b08757b2477cea12363d7a4ba` |
| `research/strategy_study_dataset.py` | `95e0d8cd9b7bb872907777d55b571d096da4610e162fa342fb4e19eda6084bc2` |
| `tests/test_study_fx_interval_contract.py` | `1b2b4e9696c9fe0784f1d98cdfe102e70c9b9e03deB00aeea4c2bae4954c9c83` |

Interfaz avisada al parent antes de editar y entrega estable notificada para
revision de Lorentz. Se preservaron los cambios de los demas agentes.
