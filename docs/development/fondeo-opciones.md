# Fondeo: qué hace falta para pasar FTMO y qué otras opciones hay (28/09/2026)

Fuentes oficiales: [ftmo.com/2-step-challenge](https://ftmo.com/en/2-step-challenge/),
[trading-objectives](https://ftmo.com/en/trading-objectives/),
[noticias](https://ftmo.com/en/faq/can-i-trade-news/).
Fuentes de terceros, a confirmar en la web de FTMO al comprar:
[precios 2026](https://propfirmcircle.com/challenges/ftmo-100k-2-step-standard),
[cambios de febrero de 2026: 1-Step y apalancamiento del oro](https://thepayoutreport.com/ftmo-february-2026-updates/).
Reglas como datos, con su fuente: `research/prop_rules.py`.

## FTMO 2 fases (la recomendada para R1 + R2)
| Regla | Valor | Qué significa para nosotros |
|---|---|---|
| Objetivo | +10 % en la fase 1, +5 % en la fase 2 | Con R1 ×20 + R2 ×30: ~4–5 meses en total (simulación) |
| Pérdida diaria | 5 % del capital inicial, **contando el flotante**, desde las 00:00 CE(S)T | Peor día mediano simulado −3.300 € de 5.000 € |
| Pérdida total | 10 % fija (no se mueve con las ganancias) | Holgura amplia |
| Días mínimos | 4 días con operaciones por fase | Operamos casi todos los días |
| Límite de tiempo | Ninguno | Sin prisas |
| Regla del mejor día | Ningún día > 50 % del beneficio de los días positivos | No suspende; solo obliga a seguir operando. Ya está en el simulador |
| Noticias | Libre en el reto; en la cuenta fondeada **Standard**, nada de abrir ni cerrar de 2 min antes a 2 min después de noticias fuertes (la **Swing** no tiene esa restricción) | Añadir al bot un bloqueo de noticias (calendario ya hecho) o elegir Swing |
| Fin de semana / noche | Solo restringido en la cuenta fondeada Standard | Nuestras cestas cierran el mismo día: sin problema |
| Apalancamiento del oro | Standard 1:50, Swing 1:15 (desde el 01/02/2026) | 1 lote de oro ≈ 9.000 $ de margen a 1:50. R1 ×20 (1,2 lotes) + R2 ×30 (0,3) con 2–3 cestas abiertas ≈ 25–35 k$: cabe en 100k. **Con Swing (1:15) sería el triple: no cabe tan holgado** |
| Precio 100k | ~540 € (a veces con descuento) | Se devuelve tras superar el reto |
| Reparto | 80 % para ti (hasta 90 %); pagos cada 14 días | ~2.100 €/mes brutos → ~1.700 €/mes para ti (estimación honesta) |
| EA y señales | EA permitidos; **FTMO confirmó a Jose que se puede operar con las señales indicadas** | Guardar la respuesta escrita |

**Cuenta a elegir: Standard** (por el margen). En la cuenta fondeada hay que respetar la ventana de ±2 min de noticias.

**FTMO 1 fase: no nos conviene.** Pérdida diaria del 3 % y pérdida máxima que se va moviendo con
las ganancias. El peor día simulado de la cartera (−3.300 €) la rompería.

## Calidad / precio (28/09, precios de terceros; confirmar en cada web al comprar)
| Empresa, reto de 2 fases | Precios por tamaño | €/100k de capital | Reparto | Devolución | Notas |
|---|---|---|---|---|---|
| FTMO | 10k 89 $ · 25k 250 $ · 50k 345 $ · 100k 540 $ · 200k 1.080 $ | ~0,54 % | 80 % (90 % en 100k–200k según fuentes) | Sí, con el primer pago | La más veterana y documentada; señales confirmadas |
| The5ers High Stakes | 5k ~35–39 $ … 100k ~495 $ | ~0,50 % | — | — | Tamaños pequeños muy baratos. Una fuente dice pérdida diaria del 4 % (la web oficial leída el 27/09 decía 5 %: verificar). 3 días con beneficio. Solo MT5 |
| FundedNext Stellar | 6k 60 $ · 15k 120 $ · 25k 200 $ · 50k 300 $ · 100k 550 $ (a menudo −25 %) | ~0,41–0,55 % | — | — | Te llevas el 15 % del beneficio del reto aunque suspendas; EA con complemento de pago |

Fuentes: [precios FTMO](https://propfirmkey.com/en/blog/ftmo-challenge-cost-2026),
[The5ers](https://propfirmbridge.com/education/the5ers-high-stakes-review-new-classic-prices-rules-bridge),
[FundedNext](https://proptradingvibes.com/blog/fundednext-stellar-2-step),
[fiabilidad de pagos](https://traderssecondbrain.com/guides/best-prop-firms).

**Lectura:**
- el precio es parecido en todas (~0,5 % del capital); FTMO es algo más cara, pero se devuelve si
  pasas y es la que más historial tiene pagando;
- **nuestras recetas escalan en línea con el lote, así que la probabilidad de pasar no depende del
  tamaño de la cuenta.** Por eso lo más barato es **empezar con una cuenta pequeña** (FTMO 10k por
  89 $), que valida el reto con reglas reales, y subir después;
- lotes a 10k: R1 ×2 (0,02 por pata, 0,12 por cesta) y R2 ×3 (0,03). Por encima del mínimo de 0,01.

## Qué falta para presentarse al reto
1. Examen en papel de 4 semanas con las recetas congeladas.
2. Bot:
   - filtros de R1 (rango asiático) y R2 (RSI 5m);
   - gestión de R1 (parecida a la b210) y de R2 (una entrada con objetivo y stop);
   - bloqueo de noticias de ±2 min;
   - todo probado en sombra y en demo con los lotes del reto.
3. Cuenta MT5 de FTMO en la VM (o una VM aparte): el bot hoy opera en Vantage; habría que conectarlo al servidor de FTMO.

## ¿Es la única opción? No
| Opción | Encaje | Pros | Contras |
|---|---|---|---|
| **FTMO 2 fases** | ✅ el mejor | Sin límite de tiempo, reglas claras, confirmado que acepta las señales, oro a 1:50 | Precio del reto; hay que pasar dos fases |
| **The5ers High Stakes** | ✅ bueno | Mismos objetivos (10 % + 5 %), sin límite de tiempo | Exige 3 días con beneficio ≥ 0,5 %; ventana de noticias ±2 min **también en el reto**; **habría que preguntarles lo de las señales** |
| **FundedNext Stellar 2 fases** | ⚠️ posible | Objetivo más bajo en la fase 1 (8 %) | EA solo pagando un complemento; no dice si la pérdida diaria cuenta el flotante; preguntar lo de las señales |
| **Varias cuentas o más capital** | Tras pasar la primera | FTMO permite escalar (hasta 2 M$ con el plan de escalado) | Mismo riesgo multiplicado; FTMO limita el capital total de una misma estrategia |
| **Cuenta propia** (como ahora, ~700–1.150 €) | Para probar en real pequeño | Sin reglas externas | Con 1.000 €, R1 + R2 a lote mínimo darían ~20–30 €/mes: sirve para validar, no para ganar dinero |
| **Vender las señales o copytrading** (MQL5 Signals, etc.) | Más adelante | Ingreso por suscriptores | Hace falta historial real verificado de meses; y **reenviar señales de otro canal puede tener problemas legales o de derechos**: no recomendado sin revisarlo |

**Recomendación:**
- FTMO 2 fases, cuenta Standard de 100k, con R1 ×20 + R2 ×30, tras el examen en papel y la demo;
- en paralelo, R1 + R2 en la cuenta propia al lote mínimo, como prueba en dinero real pequeño
  (con tu OK y siguiendo el protocolo de despliegue);
- The5ers como segunda empresa si se quiere diversificar, preguntándoles antes lo de las señales.
