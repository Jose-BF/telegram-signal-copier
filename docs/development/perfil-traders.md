# Perfil de los traders (bloque P, 29/09/2026 — en curso)

Método: comparar el mercado en el momento real de cada señal con momentos al azar del mismo día y
con la misma dirección (placebos s0–s2), usando solo rasgos conocidos antes de la señal (con test de
causalidad). Código: `research/trader_profile.py`, `research/levels.py`, `search2/p3_niveles.py`.
Datos: `out/bloqueP/`.

## Lo que se ve (en llano)
**Los dos publican igual: "comprar la caída / vender el rebote".**

| | Dubai (canal 1, 776 señales) | Gold (canal 2, 947 señales) |
|---|---|---|
| Movimiento previo en contra de la señal | 60 min: −4,7 $ (azar 0); 15 min: −1,9 $ | 5 min: −1,4 $; 15 min: −1,7 $ (azar 0) |
| RSI 5 min orientado | 45 (azar 50): compra tras bajar | 49 (azar 50): poco |
| Posición en el rango de 4 h | 0,37 (azar 0,49): compra cerca del mínimo del rango | sin diferencia clara |
| Volatilidad de la última hora | mayor (27 $ frente a 20 $) | mayor (20 $ frente a 17 $) |
| Rachas | publica más si ya ha publicado en la última hora | igual |
| Horas (UTC) | 12–15 (apertura de Nueva York) | 7–9 (Londres) y 12–14 (Nueva York) |
| Direcciones | 58 % ventas | 56 % compras |

**Hipótesis de soportes y resistencias (canal 2), con los niveles definidos en `research/levels.py`:**
- sus zonas, stops y objetivos **no** caen sobre máximos y mínimos de giro, ni sobre el máximo y
  mínimo de ayer o de la semana, más que un precio cualquiera (21 % frente a 20 % a ≤ 1 $, por ejemplo);
- **sí prefiere números redondos**: el último objetivo cae a ≤ 1 $ de un múltiplo de 10 el 36 % de
  las veces (azar 18 %), y el stop el 28 % (azar 19 %). Encaja con una plantilla (zona de 5 $,
  objetivos cada 2 $, stop a ~10 $) más que con análisis de niveles;
- el precio en el momento de la señal tampoco está más cerca de niveles que en momentos al azar
  (en ninguno de los dos canales);
- **matiz:** esto descarta los niveles tal como se han definido. Si el trader usa zonas dibujadas en
  temporalidades mayores, no se captura. Queda como "no demostrado", no como "falso".

## Qué implica para operar
- Las señales "típicas" de Gold (contra el movimiento a corto plazo) son las que peor salen. Las que
  funcionan son la excepción: las que llegan **a favor** del movimiento (ruptura del rango asiático:
  R1; filtros de impulso para la 555).
- En Dubai funciona su estilo llevado al extremo: tras una caída muy fuerte en contra (RSI 5 min < 30: R2).
- Las rachas y la cercanía a noticias son rasgos del proveedor que conviene vigilar (filtros o avisos).

## P4: ¿cuándo aciertan? (29/09)
Medida neutra: ¿llega antes a +5 $ que a −5 $ en la dirección de la señal?
- **Total:** Dubai 47,0 % (azar 47,8 %); Gold 47,7 % (azar 48,6 %).
- Por tramos de cada rasgo (movimiento previo, RSI, rangos de 4 h, rango asiático, rango de ayer,
  volatilidad, sesión, rachas), el acierto va del 40 % al 53 % y casi siempre coincide con el azar.
  Solo hay tramos sueltos algo peores (Gold con el precio a mitad del rango asiático: 37 % frente a 47 %;
  Dubai en sesión de Londres: 42 % frente a 50 %), nada que se sostenga como regla.
- **Conclusión:** ninguno de los dos acierta la dirección mejor que el azar en ninguna situación.
  **La ventaja de R1 y R2 no viene de acertar la dirección, sino de la forma del recorrido tras la
  señal combinada con la gestión.** Consecuencia para el bloque O: centrar el descubrimiento en los
  arquetipos de recorrido y en las decisiones durante la operación, no en "predecir si acierta".

## Pendiente del bloque P
- Árbol pequeño "momento de señal frente a momento al azar" validado por meses (regla aproximada de cada trader).
- Más indicadores (MACD, Bollinger, ADX, VWAP) y niveles en temporalidades mayores, para la hipótesis de niveles.
