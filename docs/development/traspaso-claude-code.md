# Traspaso chat → Claude Code (27/09/2026, 10:30)

Hasta aquí el trabajo se hizo desde el chat de Claude, en un espacio en la nube. Este documento deja
todo listo para seguir **exactamente desde este punto** con Claude Code en el portátil.
Léelo entero antes de hacer nada. Después, sigue con `estado-y-plan.md` (secciones 16–19b)
y `resultados-busqueda-1.md`.

## 1. Primera vez en este portátil (preparación, unos 5 minutos)
Desde la raíz del repo:
1. `pip install -r requirements.txt` (hacen falta numpy, pandas, pyarrow, numba y pytest).
   - En la nube se usó Python 3.11 con numba 0.67, numpy 2.4, pandas 3.0 y pyarrow 25.
2. `python tools/build_ticks_all.py`
   - Reconstruye `runtime_data/ticks_all/` con 913 ficheros de ticks, de enero al 25 de septiembre, oro y EURUSD.
   - Usa enlaces duros, así que no ocupa disco extra. Los saca de lo que ya hay en el repo: `runtime_data/canal1_history_20260913/raw_mt5_v2` y las carpetas de semana.
   - Comprueba cada fichero por md5 contra `runtime_data/ticks_all_manifest/manifest.json`.
   - Si falla el enlace (OneDrive), usa `--copy` (ocupa ~1 GB).
   - Debe terminar con `913 / 913 files OK`.
3. Tests: `python -m pytest -q tests/test_history_replay.py tests/test_gold_basket_cap.py tests/test_run_week_exam_score.py tests/test_latency_model.py`. En la nube salen 24 correctos.
4. Prueba de humo, que debe salir idéntica a la nube:

   `python search/stage2.py search/pipeline/selection_stage2.json smoke.json 2026-09-15:2026-09-17`

   Los resultados deben coincidir fila a fila con los días 15–16/09 de `search/pipeline/stage2_final.json`: 37 filas por escenario. En la nube se verificó también en modo `HS_START=spawn`, que es como funciona Windows.

**Cambio hecho para Windows.** Los scripts de `search/` estaban escritos sin la protección `if __name__ == '__main__'`, y en Windows eso rompe los procesos paralelos.
- Ya la tienen. El contenido del código es el mismo, solo se ha envuelto en `main()`.
- `research/history_search.py` acepta `HS_START=spawn|fork` para elegir cómo arrancan los procesos.
- Los scripts usan `workers=2` porque la nube solo tenía 2 CPU. En el portátil se puede subir para ir más rápido: `evaluate(..., workers=N)`.

**Carpetas.** `search/` en la raíz es la copia buena y completa: código, datos de la búsqueda y resultados, igual que en la nube.
- `tools/search/` era una copia parcial anterior; se borró el 27/09 con el OK de Jose.
- `runtime_data/search_run1/` es un duplicado de los resultados, hecho para Jose.
- `exam/` y `out/` son resultados de los exámenes del simulador, guardados como evidencia.

## 2. Dónde estamos
- **Simulador validado:**
  - semana 21–25/09: 55 de 55 cestas, neto con error de 3–5 %;
  - modo histórico: Dubai agosto 28/28 y septiembre 18/18, Gold septiembre 35/37.
  - Su drawdown sale ~9 % optimista.
- **Las señales no tienen ventaja de dirección por sí mismas**: las señales placebo dan lo mismo. Con las estrategias actuales, el histórico completo pierde.
- **Primera búsqueda masiva, ya terminada** (detalle en `resultados-busqueda-1.md`):
  - Dubai #3 aprobada: gana todos los meses y tiene la pérdida acotada, pero gana poco (~1,5 %/mes a lote seguro de fondeo).
  - Gold #1 aprobada: 0 cestas perdidas, pero flotante de hasta −300 € sin stop. No se recomienda.
  - Ninguna pasa un reto tipo FTMO en 60 días.
- **Hallazgo clave** (`search/pipeline/placebo_procedure.*`): se repitió el procedimiento con señales a hora aleatoria.
  - En Dubai, 9 de 10 finalistas placebo pasan la validación de julio a septiembre. En Gold, 0 de 10.
  - Es decir: en Dubai hay patrones de mercado + gestión que sí aguantan en el tiempo.
  - Pero el "listón de suerte" (comparar con placebo) los tiraba, porque también ganan con señales falsas.
  - Para Jose lo que importa es ganar dinero de forma sostenida, venga la ventaja del proveedor o del mercado. **El juez era demasiado estricto para ese objetivo.**

## 3. Último mensaje de Jose (sin responder del todo) — retomar aquí
> "Me niego a pensar que no hay ninguna combinación posible para conseguir un beneficio a veces más
> a veces menos pero sostenido en el tiempo, tanto para canal 1 como para el 2. Tampoco sé si es cosa
> de la simulación o de con los datos que estamos trabajando, y si probamos a reducir la muestra pero
> teniendo en cuenta pérdidas máximas que podríamos soportar en situaciones adversas (aunque una
> situación así no se viera en la muestra por x o por y). Es un ejemplo solo."

Respuesta que se le iba a dar, en corto y en español llano:
1. **Simulación:** no parece el problema. Reproduce la realidad cesta a cesta.
2. **Datos:** tampoco. Son los ticks reales del broker y las señales reales.
3. **Tiene razón en lo importante.** Sí hay combinaciones que ganan de forma sostenida, al menos en Dubai. El juez las descartaba por exigir que la ventaja viniera de la señal.
4. **Propuesta para la búsqueda 2** (acordarla con Jose ANTES de lanzar nada):
   - **Juez nuevo:** no "supera al placebo", sino "gana dinero sostenido con riesgo acotado":
     - beneficio por mes positivo en la mayoría de meses;
     - pérdida máxima por cesta *por diseño* (stop obligatorio), no la que se vio en la muestra;
     - puntuación = beneficio / peor caída.
   - **Prueba de estrés (idea de Jose):** a cada candidata se le mete una situación adversa que no está en la muestra, por ejemplo un movimiento brusco de X $ en contra justo tras la entrada, o un día tipo NFP/crash. Se exige que la pérdida quede dentro del límite que Jose pueda soportar (lo define él, por ejemplo el 5 % diario del fondeo).
   - **Muestra más corta y reciente:** Jose cree que las señales de los últimos meses son mejores. Se puede buscar en abr–jun y validar en jul–sep para los dos canales. Así se compara con lo anterior sin hacer trampa.
   - Mantener los periodos de validación y examen final **fijados antes de mirar resultados**.
5. **Comprobación rápida pendiente, que Jose paró para cambiar de herramienta** (`search/placebo_on_real.py`, sin resultados guardados): coger las 10 finalistas placebo de Dubai y pasarlas por las señales reales de enero a septiembre, con los 3 brokers. Tarda ~5 minutos y responde si esas combinaciones "de mercado" también ganan con las señales reales. **Pregúntale a Jose antes de lanzarla.** Se paró porque él lo pidió.

## 4. Mapa de herramientas
| Qué | Dónde |
|---|---|
| Motor de simulación (exacto y rápido con Numba) | `research/dubai_iterative/engine.py`, `fast_engine.py` |
| Reproducción histórica (señal → retraso Telegram → broker) | `research/history_replay.py`, `research/history_search.py` (`evaluate`) |
| Espacio de gestiones (genomas) | `research/history_space.py` (`live()`, `population(n, seed)`) |
| Filtros de señal (hora, RSI, ATR, rango 4h…) | `research/signal_features.py`, `research/signal_filters.py` (448 filtros) |
| Juez, listón de suerte, vecinos | `research/search_judge.py`, `research/neighbors.py` |
| Cuenta / fondeo / Monte Carlo | `research/account_sim.py`, `research/report_stage2.py` |
| Descripción en español de una gestión | `research/describe_genome.py` |
| Búsqueda: etapa 1 → pipeline → congelar y examen final → informe | `search/stage1.py N SEED BATCH`, `search/pipeline.py`, `search/finalize.py`, `search/make_report.py` |
| Controles extra de la búsqueda 1 | `search/stress.py`, `search/safety_variants.py`, `search/placebo_procedure.py` |
| Examen semanal (simulador frente a realidad) | `tools/run_week_exam.py` |
| Señales (1.723) y placebos | `runtime_data/signal_universe_v1/` |
| Calibraciones de broker (A_p50 rápido, C_p50 lento, C_p90 atascado) | `runtime_data/execution_calibration_v2/*_q450.json` |
| Ticks enero – 25 sep | `runtime_data/ticks_all/` (ver punto 1) |

Tiempos en la nube (2 CPU): la etapa 1 con 500 gestiones × 3 universos tardó ~5 h. Cada re-ejecución exacta tarda ~1 min por escenario.

## 5. Reglas que siguen en pie
- Con Jose: español, corto y sencillo. El detalle va en los documentos.
- **Ninguna búsqueda masiva sin acordarla antes con él.** Se enfadó cuando se lanzó una por iniciativa propia.
- El bot no se toca: sin despliegues, reinicios, cambios de política ni órdenes sin su OK explícito para ese cambio concreto.
  - Cada despliegue: exposición plana antes y verificación después.
  - Ninguna estrategia va a producción sin revisarla juntos; antes, demo.
- No se introducen contraseñas ni credenciales. No se usa su clave SSH personal. No se lanzan órdenes de trading (las mediciones las lanza Jose con los .bat).
- El repo sigue público por decisión de Jose. Se conserva la evidencia en bruto.
- La semana 14–18/09 no es representativa: no calibrar con ella.

## 6. Otros pendientes
- **Lunes, al abrir el oro:** Jose lanza `latency_test/medir_AMBOS_ORO.bat` (VT frente a Vantage en oro). Luego se analizan los CSV.
- Integrar el worktree local con origin/main (hay cambios del watcher de Codex sin commitear). Hay que conservarlos.
- Pausado: "aprender la estrategia del proveedor".
