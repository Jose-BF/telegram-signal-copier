# Primera búsqueda masiva — resultados (27/09/2026)

## En una frase
De 500 gestiones × 448 filtros por canal, **dos combinaciones pasaron todos los controles**:
una de Dubai que es sólida pero gana poco, y una de Gold que gana muy regular pero
esconde un riesgo grande. **Ninguna va a producción todavía.**

## Qué se hizo (sin tecnicismos)
1. **Búsqueda** (Dubai ene–jun, Gold abr–jun): se probaron todas las combinaciones de gestión + filtro de señales.
2. **Listón de suerte**: lo mismo con señales falsas (hora al azar). Solo cuenta lo que supera a lo mejor que sale por pura suerte.
3. **Vecinos**: si se mueve un poco cada parámetro, tiene que seguir ganando (≥70 %).
4. **Validación** (1 jul – 11 sep, meses que no se usaron para elegir): tiene que ganar con broker rápido, lento y atascado, y en ≥50 % de las semanas.
5. **Congelado** (huella `ec51013c…`) y después **examen final** 14–25 sep.

## Resultados
Importes con el lote base de cada estrategia (sin multiplicar). Broker lento salvo que se diga.

| | Estrategia actual Dubai | **Candidata Dubai (#3)** | Estrategia actual Gold (555) | **Candidata Gold (#1)** |
|---|---|---|---|---|
| Qué hace | la de ahora | espera retroceso de 2$; hasta 3 entradas cada 5$ en contra (0.01/0.01/0.02); corta la cesta en −50 €; asegura beneficio a +5 € (cierra si devuelve 3 €); a 120 min cierra si pierde | la 555 | espera 1,5$ en contra y rebote de 2$; 3 entradas cada 1$ (0.04/0.02/0.02); objetivos +0,5/+1/+1,5$; **sin stop** |
| Qué señales coge | todas | solo si RSI 15m < 45 y el rango de la última hora < 25$ (~1 de cada 9) | todas | solo si RSI 5m < 30 y ATR 15m > 8$ (~1 de cada 9) |
| Búsqueda | −481 € | +208 € | −219 € | +289 € |
| Validación (rápido / lento / atascado) | +147 / +276 / −108 € | **+74 / +33 / +58 €** | +272 / −62 / +172 € | **+252 / +254 / +189 €** |
| Examen final 14–25 sep | −12 € | **+37 €** (13 cestas) | −181 € | **+18 €** (solo 3 cestas) |
| Meses en positivo (ene–sep) | 3 de 9 | **9 de 9** | 4 de 6 | **6 de 6** |
| Cestas perdedoras | ~40 % | 10 de 87 | 60 de 836 | **0 de 97** |
| Peor cesta cerrada | −65 € | −25 € (−66 € con broker atascado) | −419 € | +2 € |
| **Peor momento dentro de una cesta** | −68 € | **−47 €** | −419 € | **−304 €** (−340 € con broker rápido) |

## Lo que significan
**Candidata Dubai (#3): la más fiable.**
- Gana todos los meses, con los tres brokers, y también en el examen final.
- La pérdida está acotada: como mucho unos 50 € por cesta al lote base.
- Hace pocas operaciones: unas 10 al mes y +30 €/mes al lote base.
- **Ojo:** con señales a hora aleatoria (mismo día y dirección), esta forma de filtrar y gestionar también gana. La ventaja viene del *momento del mercado* (RSI bajo y mercado tranquilo) y de la gestión, **no del proveedor**. No es malo, pero hay que saberlo.

**Candidata Gold (#1): bonita por fuera, peligrosa por dentro.**
- 97 cestas y ninguna perdida, pero algunas estuvieron hasta **−300/−340 € en flotante** para ganar ~6 € de media. Es el mismo patrón que la 555: gana porque aguanta.
- Con la misma gestión y **todas** las señales pierde −2.790 €, con cestas de hasta −905 €. Todo depende de que el filtro siga esquivando el día malo.
- Le probé un stop de emergencia (100–300 €) y un cierre por tiempo. **Nunca mejora** y en abr–jun empeora mucho (de +289 € a entre −326 € y +70 €): corta cestas que luego se recuperaban.
- Veredicto: no la recomiendo para fondeo. Un solo día malo se come meses de ganancia y rompe la regla de pérdida diaria.

## ¿Y para cuentas de fondeo?
Con un lote seguro (usar solo la mitad de los límites de pérdida), la candidata Dubai daría **~1,5 % al mes** en una cuenta de 10k, con una caída máxima de ~2–3 %. (La Gold, a lote seguro, apenas ~0,8 % al mes.)
- **Un reto tipo FTMO (10 % en 30–60 días) no se pasa a este ritmo.** En la simulación, ningún arranque llega al objetivo en 60 días.
- Subir el lote para llegar antes rompe la regla de pérdida diaria.
- Puede encajar en fondeos **sin límite de tiempo**, o como base estable en cuenta propia.

## Control de honestidad del propio método
Repetí todo el procedimiento con señales falsas:
- **Dubai:** 9 de 10 finalistas falsas también pasaban la validación. Confirma que en Dubai lo que funciona es "cuándo está el mercado" y no la señal.
- **Gold:** 0 de 10 pasaban. En Gold, que la candidata pase es algo, pero no quita su riesgo.

## Límites que hay que tener presentes
- El drawdown del modo histórico sale ~9 % optimista.
- El periodo de búsqueda de Gold es corto (3 meses).
- El examen final es pequeño: 13 cestas en Dubai y 3 en Gold.
- Todo es 2026 y oro: si el mercado cambia de carácter, esto puede cambiar.

## Propuesta de siguientes pasos (a decidir juntos)
1. Poner la **candidata Dubai en DEMO** unas semanas, en paralelo a la actual, para ver si en vivo se parece.
2. Como en Dubai la ventaja viene del mercado, la siguiente búsqueda puede apuntar a eso: más filtros de contexto y gestión.
3. Gold: seguir con la actual o buscar gestiones con **pérdida máxima acotada desde el diseño**, aunque ganen menos.

Archivos: `runtime_data/search_run1/` (datos) y `search/` (scripts: `make_report.py`, `stress.py`, `safety_variants.py`, `placebo_procedure.py`).
