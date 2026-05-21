"""market_context.py — snapshot de mercado al recibir una señal.

Capturado en el handler de entrada para que cada trade del ledger lleve
el contexto al abrirse (ATR M5×14, rango reciente 5m, precio actual).
Permite al análisis posterior separar fallos de EJECUCIÓN del bot vs
REGIMEN de mercado adverso. Sin esto, "trade X perdió" no distingue
entre "el bot ejecutó mal" y "el oro se volvió loco en ese momento".

DEFENSIVO: si MT5 está desconectado o algo falla, devuelve None. El
caller registra contexto null y la apertura sigue su curso.

Spec: docs/superpowers/specs/2026-05-19-registro-anomalias-design.md
Plan: docs/superpowers/plans/2026-05-19-registro-anomalias.md (T4)
"""
from typing import Optional

import MetaTrader5 as mt5


# MT5 timeframe constants no son enums limpios — el valor 5 corresponde
# a TIMEFRAME_M5. Lo extraemos por nombre con fallback al numero literal
# para no romper si la enum cambia de import path.
_TF_M5 = getattr(mt5, "TIMEFRAME_M5", 5)


def compute_market_context(symbol: str) -> Optional[dict]:
    """Snapshot {atr_m5_14, recent_5m_range, current_price_at_signal}.

    ATR simplificado a media de (high-low) sobre las últimas 14 barras M5
    — suficiente para clasificar régimen de volatilidad sin la complejidad
    del True Range con close anterior. Para análisis cualitativo de
    "volatilidad alta vs baja" es equivalente.

    Devuelve None si MT5 está desconectado, no devuelve barras, o falla
    cualquier excepción inesperada.
    """
    try:
        bars = mt5.copy_rates_from_pos(symbol, _TF_M5, 0, 14)
        if not bars:
            return None
        # Lista o array NumPy — len() funciona en ambos.
        if len(bars) < 2:
            return None

        atr = sum((b.high - b.low) for b in bars) / len(bars)
        last = bars[-1]
        recent_range = [round(last.low, 2), round(last.high, 2)]

        # Precio actual: mid bid/ask. Si el tick falla, devolvemos None
        # SOLO en current_price (no en todo el ctx) — ATR + range siguen
        # siendo útiles para el análisis.
        cur = None
        try:
            tick = mt5.symbol_info_tick(symbol)
            if tick is not None:
                cur = round((tick.bid + tick.ask) / 2, 2)
        except Exception:
            pass

        return {
            "atr_m5_14": round(atr, 3),
            "recent_5m_range": recent_range,
            "current_price_at_signal": cur,
        }
    except Exception:
        return None
