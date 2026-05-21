# Hallazgo crítico — sesgo en el simulador y números reales

**Fecha:** 2026-04-22
**Disparador:** El usuario preguntó "¿cómo aseguramos que las simulaciones son correctas?"

## TL;DR

Mi simulador anterior (`analyze_real_price_2026.py` y similares) era **muy optimista**.
Asumía que el market entry siempre se hacía en el extremo más favorable del rango,
cuando en realidad el bot entra al precio actual (open de la vela M1 del minuto del mensaje).

Cuando ese precio queda fuera del rango (escenario muy frecuente), el bot **cierra
todo inmediatamente** (safety check, líneas 173-184 de `listener.py`) — no opera.

## Comparativa cifras (TP3 / extremes 2pos / TS60 — la "recomendación" del análisis previo)

| Canal | Versión | IS         | OOS        |
|-------|---------|------------|------------|
| 1     | v1 optimista | **+$1187** | **+$1509** |
| 1     | v2 realista  | **-$468**  | **-$481**  |
| 1     | v3 limit-first (no usado por el bot) | -$453 | -$502 |

**Diferencia: ~$1900 de delta solo por el sesgo de entrada.**

| Canal | Versión | IS  | OOS |
|-------|---------|-----|-----|
| 2 (intra_dca/4p TP4+BE) | v2 realista | -$74 | +$87 |
| 2 (intra_dca/4p TP4 NO BE) | v2 realista | +$13 | +$96 |

**En canal 2, el 94% de señales (217/230 IS, 245/260 OOS) caen en safety y no operan.**
Solo 13-15 señales de cada split realmente se ejecutan → muestra demasiado pequeña
para declarar edge.

## Causa raíz del sesgo

**v1 (mal):**
```python
market_price = rng[1] if direction == "BUY" else rng[0]  # extremo cercano del rango
```
Asume que el bot entra al borde "barato" del rango → entrada óptima.

**Realidad (v2):**
```python
real_market_price = m1_open(message_time)  # precio del minuto del mensaje
if not (rng[0] <= real_market_price <= rng[1]):
    return SKIPPED  # safety check del bot lo cancelaría
```
El bot entra al precio que tenga el mercado cuando llega el mensaje. Si está fuera
del rango → safety lo cierra (sin colocar tampoco limits).

## Implicaciones operativas

1. **NO desplegar live** la configuración actual. Las cifras de v1 eran irreales.
2. **Canal 1**: sin edge real con la lógica actual del bot. Pierde $5/señal media.
3. **Canal 2**: muestra demasiado pequeña (solo 5-6% de señales operan). No
   estadísticamente significativo.

## Posibles caminos a explorar

1. **Modo "limit-only" para canales que mandan rangos antes del precio**:
   no abrir market nunca; solo colocar limits a los extremos / DCA. Esperar
   pull-back. Si nunca entra → P/L=0, sin pérdida. Esto requiere modificar
   la lógica del bot (eliminar la apertura inmediata por sticker).

2. **Tolerancia en safety check**: aceptar entry hasta X% fuera del rango antes
   de cancelar. Probablemente empeora aún más (peores entradas).

3. **Validación con señales reales del trader humano**: pedir al usuario captura
   de MT5 mostrando qué hace el trader profesional con cada señal:
   - ¿Entra a market o a limit?
   - ¿Cierra cuando entry está fuera del rango?
   - ¿Modifica TPs/SL después?

4. **Aceptar que el edge del canal está en EJECUCIÓN HUMANA**, no en seguir
   ciegamente. Si el trader profesional filtra señales por contexto que el bot
   no ve, copiar todo es una mala idea.

## Próximos pasos requeridos

- [ ] Usuario envía capturas de MT5 con operativa real del trader
- [ ] Usuario envía 1-2 señales reales recientes para validar end-to-end con
      `validate_signal.py`
- [ ] Decidir si seguir desarrollando este copier o pivotar a otro enfoque
