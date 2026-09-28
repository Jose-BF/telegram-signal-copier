# Búsqueda 2: resultados (28/09/2026)

## En una frase
Haciendo **R1 (canal 2, escalera solo en rupturas del rango asiático) + R2 (canal 1, seguir solo
tras caída fuerte)** en una cuenta de fondeo de 100k, el reto se pasa en la simulación casi siempre en
4–5 meses. Después daría **~2.100 €/mes según la estimación honesta** (hasta ~3.000–4.000 € según
la muestra). El riesgo principal es que R1 toque su tope de cesta más a menudo de lo visto.
**Antes de dinero real: 4 semanas de examen en papel y preguntar a FTMO por escrito.**

## Qué se hizo
- 10 familias con el motor exacto (1.512 gestiones), con señales reales y placebo, más 30 variantes
  de cobertura con la calculadora (36 vertientes del catálogo).
- Juez honesto (walk-forward): cada mes se elige usando solo los meses anteriores. Lo que gana ese
  procedimiento es lo que se promete, no lo mejor de la muestra.
- Control de placebo (señales a hora al azar), 3 brokers y estrés con topes inyectados.

## Lo que NO funciona (y por qué es importante saberlo)
- Coger "la mejor de miles" de reglas: fuera de muestra gana el 31 % de las veces. Es suerte.
- La 555 y sus variantes: pierden en el walk-forward (−548 €). Ganan casi siempre, pero las cestas
  malas se lo comen todo.
- Invertir las señales de Dubai (la pista del mapa, +740 € mirando todo el año): ruido al elegir de forma honesta.
- Entrar tras confirmación, esperar N min, escalera a favor, parciales, break-even, dirección del
  mercado, las dos direcciones, selector aprendido: todo en la zona del ruido.

## Las recetas (congeladas en `search2/frozen_v1.json`, sha256 532661a9…)

| | R1 · canal 2 (Gold) | R2 · canal 1 (Dubai) | R3 · canal 2 |
|---|---|---|---|
| Qué señales | solo si el precio está en el extremo del rango asiático (00–07 UTC) del lado de la señal: a favor de la ruptura (~1 de cada 3) | solo si el RSI de 5 min < 30 en su contra: la señal llega tras una caída fuerte (~1 de cada 5) | solo con volatilidad > 1,2 veces lo normal |
| Cómo | 6 entradas de 0,01 a mercado, en escalera en contra cada 2 $; tope de cesta −100 €; asegurar beneficio a +15 € (devuelve 5 € → cierra); pasados 3 min, cerrar si hay beneficio | 1 entrada, objetivo +10 $, stop −40 $, cierre a las 4 h | **invertir** la señal: objetivo +5 $, stop −20 $, cierre a 30 min |
| Neto (motor exacto, lote base, brokers rápido/lento/atascado) | +474 / +553 / +518 € en 6 meses | +546 / +545 / +535 € en 9 meses | +166 / +166 / +150 € en 5 meses |
| Meses positivos | 6/6 con los tres brokers | 8/9 | 5/5 |
| Walk-forward (honesto) | +334 € en 4 meses (placebo +32 €) | +90 € en 6 meses (placebo −10 €) | +124 … +197 € (placebo −158 … −188 €) |
| Placebo, misma receta | −177 € y 4 topes (real: 0–1 topes) | — | — |
| Peor cesta / peor día | −100 € (tope) / −93 € | −37 € / −72 € | −17 € / −18 € |
| Riesgo | **gana aguantando** (flotante hasta −94 €); deja de ganar si más del ~1,8 % de las cestas tocan el tope | stop fijo; pocas operaciones | poca muestra (19 días con operaciones) |

## Cartera R1 + R2 en FTMO 100k (Monte Carlo por bloques de días, flotante incluido)

| Lotes | Topes de R1 | Pasa | Suspende | Días | €/mes (mediana de la simulación) |
|---|---|---|---|---|---|
| R1 ×20 (1,2 lotes/cesta) + R2 ×30 (0,3 lotes) | 0 % | 100 % | 0 % | 116 | 4.006 |
| | 0,5 % | 100 % | 0 % | 130 | 3.493 |
| | 1 % | 99 % | 1 % | 148 | 2.978 |
| R1 ×30 + R2 ×40 | 0,5 % | 93 % | 7 % | 91 | 4.892 |
| | 1 % | 84 % | 16 % | 101 | 4.135 |

**Estimación honesta** (walk-forward, no la muestra) con R1 ×20 + R2 ×30:
- R1 ≈ 83 €/mes × 20 ≈ 1.670 €;
- R2 ≈ 15 €/mes × 30 ≈ 450 €;
- total **≈ 2.100 €/mes** en la cuenta de 100k, antes del reparto con la empresa de fondeo.

**Recomendación:** R1 ×20 + R2 ×30. Aguanta hasta un 1 % de topes con un 99 % de probabilidad de
pasar y un peor día mediano de −3.300 € (el límite diario es de −5.000 €).

## Límites honestos
- Se probaron ~40 hipótesis. Que 2–3 pasen todos los controles puede incluir algún falso positivo:
  por eso hacen falta el examen en semanas futuras (M9) y la demo.
- R1 depende de que el filtro siga evitando los topes. Con la frecuencia de topes sin filtro (2,4 %) pierde.
- Muestra: Gold abril–septiembre, Dubai enero–septiembre de 2026. Mercado de oro muy volátil.
- La calculadora no se usó para elegir: todo lo de arriba sale del motor exacto.
- Por confirmar con FTMO (por escrito): uso de señales de un canal con EA propio (`pregunta-soporte-fondeo.md`).
- Las escaleras necesitan que el bot las ejecute. La R1 es muy parecida a la b210 del bot (escalera
  0,01 × n, tope, asegurar beneficio), así que es configurable. R2 y R3 son una sola entrada con
  objetivo y stop. Los filtros (rango asiático, RSI 5m, volatilidad) **sí necesitan código nuevo en el bot**.

## Siguientes pasos propuestos
1. **Examen semanal en papel (M9)** desde esta semana: cada fin de semana, las recetas congeladas
   sobre la semana nueva (datos de la VM en solo lectura).
2. Enviar la pregunta a FTMO y guardar la respuesta.
3. Si tras 4 semanas aguantan: añadir los filtros al bot (con tu OK), demo en paralelo y luego el reto.
