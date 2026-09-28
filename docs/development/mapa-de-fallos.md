# Mapa de fallos: por dónde puede fallar todo esto, y cómo se comprueba (28/09/2026)

Regla de trabajo: **nada cuenta como hecho sin evidencia** (test, fichero con hash o dato medido).
Cada fila tiene una comprobación y un estado. Este documento manda sobre los demás: antes de
cualquier paso con dinero, todas las filas relevantes tienen que estar en verde o aceptadas por Jose
por escrito.

Estados: ✅ comprobado · ⚠️ comprobado a medias · ❌ pendiente · 🔒 depende de Jose.

## A. Datos
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| A1 | Ticks corruptos o con huecos | md5 contra el manifest (913/913 OK) | ✅ |
| A2 | Reloj del broker mal (UTC+2/+3) | NFP y corte diario cuadran | ✅ |
| A3 | Señales perdidas, duplicadas o de más | Universo contra las entradas reales del bot (48/48 en 21–25/09) | ✅ (solo semanas examinadas) |
| A4 | Ediciones posteriores de mensajes que cambian la dirección | 0 cambios de dirección en el diario | ✅ |
| A6 | Rasgos v1 que usan el tick justo en t0 (fallo heredado) | Encontrado y corregido el 29/09; test en ticks reales; R1 y R2 no cambian | ✅ |
| A5 | **El precio de FTMO no es el de Vantage**: el filtro de rango asiático y el RSI dependen del feed | Comparar las barras de 1 min de FTMO (demo) con las de Vantage durante la prueba; ver cuántas señales cambian de decisión | ❌ |

## B. Simulador
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| B1 | Latencias del broker mal modeladas | Examen 21–25/09 (55/55 cestas, neto ±5 %) | ✅ para la 555 y Dubai balanced |
| B2 | **Ejecución tipo R1 (escalera con tope y cierres del cliente) nunca examinada contra operaciones reales** | Sombra, y cuenta demo de FTMO frente al simulador cesta a cesta | ❌ |
| B3 | Comisión 0 en la simulación (FTMO sí cobra en el oro) | V3: R1 +435 € y R2 +543 € con 22,5 $/lote (29/09). Falta leer la cifra real en la demo de FTMO | ⚠️ |
| B4 | Spread distinto en FTMO | V3: con spread +0,2 $ y comisión alta, R1 +279 € y R2 +510 € (29/09). Falta medirlo en la demo | ⚠️ |
| B5 | Margen ("No money") no modelado | Calcular el margen máximo usado (apalancamiento del oro 1:50 Standard) con las cestas abiertas | ❌ |
| B6 | Cestas cortadas al fin de día en la simulación, pero el bot no cierra solo | Regla de cierre antes del corte en la simulación y en el bot | ❌ |
| B7 | La simulación optimista en drawdown (~9 %) | Aplicar un margen del 10 % a los límites de riesgo | ⚠️ |
| B9 | El motor rápido y el oráculo discrepan en `basket_money` sin extensión (test previo roto) | Test antiguo: el tope de Gold se habilitó a propósito el 26/09 y el oráculo no se actualizó. **Motor rápido = motor lento en 78/78 señales de R1, al céntimo** (29/09) | ✅ |
| B10 | 63 tests previos rotos por cambios del motor sin commit (24–26/09) | Clasificados en `auditoria-bloque0.md`; arreglar los que afectan (B9, procedencia) | ⚠️ |
| B8 | Recorrido máximo de 6 h por cesta | Comprobar que R1/R2 no llegan a ese límite (R1 0; R2 4 de 140) | ⚠️ |

## C. Estadística (¿es suerte?)
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| C1 | Elegir la mejor de miles | Walk-forward (elige solo con meses anteriores) | ✅ |
| C2 | **Sesgo del procedimiento**: la variante del juez se eligió tras ver resultados; placebo solo en las familias buenas | Placebo de toda la búsqueda con 2 semillas y la misma forma de elegir | ❌ |
| C3 | Un solo placebo por familia | 2 semillas más para R1/R2 | ❌ |
| C4 | Receta frágil a pequeños cambios | V2: R1 8/9 y R2 13/14 vecinos positivos (29/09) | ✅ |
| C5 | Depende de pocas operaciones | V2: sin su 5 % mejor, R1 +390 € y R2 +509 € (29/09) | ✅ |
| C6 | Muestra corta (Gold 6 meses) | Examen semanal en semanas nuevas | ❌ (4 semanas) |
| C7 | Topes en racha (no al azar) | Estrés con topes seguidos en los peores días | ❌ |
| C8 | Cambio de régimen del mercado | Resultado por meses y por volatilidad; parar si el examen semanal se tuerce (regla de parada) | ❌ |

## D. Reglas de FTMO
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| D1 | Pérdida diaria con flotante, día CE(S)T | V4: `prop_sim.daily_table_cet` con test; peor día igual con los dos horarios (29/09) | ✅ |
| D2 | Regla del mejor día (≤ 50 %) y 4 días mínimos | En el simulador | ✅ |
| D3 | **Prácticas prohibidas: "sobreapalancamiento, sobreexposición, apuestas a un solo lado, account rolling" y "posiciones sustancialmente mayores que tus otras operaciones"** | El lote del reto debe ser el mismo que el de la cuenta fondeada (nada de "modo casino" en el examen); lote constante | ❌ (regla nueva de diseño) |
| D4 | Noticias en la cuenta fondeada Standard (no abrir ni cerrar ±2 min) | Bloqueo en el bot y comprobación en la simulación | ❌ |
| D5 | Posiciones de fin de semana (Standard fondeada) | Cierre antes del viernes | ❌ |
| D6 | Comisión y spread reales | = B3/B4 | ❌ |
| D7 | Uso de señales de un canal | FTMO respondió que sí (según Jose) | 🔒 guardar la respuesta escrita |
| D8 | Inactividad (30 días sin operar = cuenta cerrada en algunas empresas) | Mirar la frecuencia: R1 opera ~16 días al mes | ✅ |

## E. Implementación en el bot
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| E1 | Filtros calculados distinto que en la simulación (rango asiático en UTC; el servidor de FTMO va en GMT+2+DST) | Test con los mismos ticks: bot = simulador en el 100 % de las señales | ❌ |
| E2 | Escalera, tope y "asegurar beneficio" ejecutados distinto | Sombra y demo contra el simulador, cesta a cesta | ❌ |
| E3 | Reinicio del bot con una cesta abierta | Recuperación de estado (ya existe en el bot); probarlo con R1 | ❌ |
| E4 | Freno diario y bloqueo de noticias sin probar | Tests con días sintéticos | ❌ |
| E5 | Lotes y redondeos (0,02 por pata en 10k) | Test | ❌ |
| E6 | Nombre del símbolo en FTMO (XAUUSD u otro sufijo) | Mirarlo en la demo | ❌ |
| E7 | Dos cuentas a la vez en la VM (Vantage y FTMO): el bot hoy se conecta a un único terminal y usa una sesión de Telegram | Decidir la arquitectura (una o dos instancias) | 🔒 |

## F. Operación
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| F1 | VM caída o desconectada | Heartbeat y avisos (existen) | ⚠️ |
| F2 | Log gigante (incidente de 7 GB del 14–18/09) | Vigilar el tamaño del disco y rotar | ⚠️ |
| F3 | Un push despliega sin querer (el watcher sigue origin/main) | Protocolo: OK de Jose, exposición plana, verificación | ✅ (norma) |
| F4 | Portátil suspendido en cálculos largos | Mantener despierto durante el cálculo | ✅ |

## G. Sombra
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| G1 | **La sombra de la VM está parada desde el 30/08** y se desactiva sola en cada arranque | Arreglarla o sustituirla por la sombra local | ❌ |
| G2 | La sombra de la VM usa el simulador antiguo (no validado) | Usar el simulador validado | ❌ |
| G3 | Resultados de sombra no comparables con lo real | Examen semanal: sombra de la estrategia en vivo frente a su resultado real | ❌ |

## H. Mercado y proveedor
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| H1 | El proveedor cambia de estilo, de horario o de volumen de señales | Contar señales y horas cada semana; alerta si cambian mucho | ❌ |
| H2 | Días extremos (desplome del 29/01) | En la muestra ✅; freno diario | ⚠️ |

## J. Taller 555 → 666 y régimen (añadido el 28/09)
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| J1 | El filtro de impulso "funciona" por casualidad (se eligió tras mirar) | Definir el filtro compuesto **antes**; walk-forward; placebo con 3 semillas; placebo de la búsqueda entera | ❌ |
| J2 | Solo funciona en un régimen de volatilidad | V2: R1 y R2 positivas en los 3 regímenes y en todos los trimestres con datos (29/09). Para lo que salga del bloque O sigue pendiente | ⚠️ |
| J3 | La protección del beneficio (break-even escalonado) mejora en muestra y empeora fuera | Mismo juez; comparar con la 555 sin cambios en los mismos meses | ❌ |
| J4 | El break-even tras TP1 usa la extensión no validada contra la realidad | Marcar; no llevar a dinero sin validarla en sombra y demo | ❌ |
| J5 | Salidas por volatilidad o tiempo mal calculadas en vivo (ATR del feed de FTMO) | Test: bot = simulador con los mismos ticks | ❌ |
| J6 | Dar tanto peso a los meses recientes que se sobreajuste a pocos datos | Peso fijado antes (p. ej. doble a los últimos 3 meses) y resultado también sin pesos | ❌ |

## K. Descubrimiento señal a señal (bloque O)
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| K1 | La "mejor acción" de cada señal se conoce solo mirando el futuro (engaño seguro) | Solo se usan rasgos anteriores a la señal para elegir; el aprendizaje siempre en meses anteriores | ❌ |
| K2 | Optimizar señal a señal premia aguantar y esconde las colas (el caso de la 555) | Penalizar el flotante en O2; juzgar el dinero total con colas | ❌ |
| K3 | Árboles o reglas demasiado finos (se aprenden el ruido) | Profundidad ≤ 3, mínimo de señales por hoja, walk-forward | ❌ |
| K4 | Demasiadas acciones y reglas probadas → suerte | Placebo del procedimiento entero (3 semillas) y PBO | ❌ |
| K5 | La calculadora se equivoca en cestas (±0,5 €/cesta) | Toda candidata se confirma en el motor exacto | ❌ |
| K6 | Rasgos con información del futuro | Test de causalidad de cada rasgo nuevo | ❌ |
| K7 | Recursión infinita sin criterio de parada | Presupuesto fijo de iteraciones, registrado | ❌ |

## L. Trabajo heredado y mecanismos nuevos (28/09)
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| L1 | El buscador recursivo heredado (el que encontró la 555) tiene fallos de diseño o de código | Diseño revisado (29/09): juzga con días posteriores ✅; fallos de uso (pliegues diminutos, 1 señal basta, sin placebo). Falta la prueba con una estrategia plantada | ⚠️ |
| L2 | Otras piezas heredadas no auditadas (partes del motor que no se usaron en los exámenes) | Solo se usan piezas con test o examen contra la realidad; el resto, auditar antes | ⚠️ |
| L3 | Recuperación con más lote (martingala): cola enorme o incumplimiento de FTMO | Lote total máximo, tope en €, estrés y revisión contra las prácticas prohibidas | ❌ |
| L4 | Indicadores con información del futuro, o demasiados | Test de causalidad por indicador; combinaciones con una idea común; placebo | ❌ |

## I. Proceso
| # | Fallo posible | Comprobación | Estado |
|---|---|---|---|
| I1 | Declarar algo "listo" sin haber ejecutado todas las comprobaciones (pasó el 28/09) | Este mapa: nada con dinero sin todas las filas relevantes en ✅ | ✅ (desde hoy) |
| I2 | Cambiar la receta después de ver semanas nuevas | Recetas congeladas con hash | ✅ |
| I3 | Umbrales decididos tras ver resultados | Escribir los umbrales antes de ejecutar | ✅ (norma) |
