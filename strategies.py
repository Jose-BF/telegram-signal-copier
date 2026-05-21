"""
strategies.py — Lógica central de filtros y modificadores de señal.

Concentra TODAS las decisiones derivadas del análisis del JSON (1492 señales 2025+):

  • Skip RE-ENTER          → +$328 EV vs baseline (point 2)
  • HIGH RISK lot/2        → mantiene EV positivo con menor variance (point 1)
  • TIME-STOP 60min        → +$3.842 EV vs baseline, corta perdedoras (point 7)
  • Post-SL momentum TP4   → P(TP5)=29% pero EV mejor capando en TP4 (point 4)

Todas son toggleables en config.py para poder desactivar individualmente
en caso de necesidad (rollback rápido sin tocar código).
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import config


# ─── Memoria global ligera de eventos recientes ──────────────────────────────

@dataclass
class _SLEvent:
    timestamp: datetime
    duration_s: float       # segundos desde apertura hasta SL
    channel: str


# Deque con tamaño máximo para no crecer infinitamente
_recent_sl_hits: deque[_SLEvent] = deque(maxlen=20)


def _purge_old(events: deque, window_min: int):
    """Elimina eventos más viejos que `window_min` minutos."""
    cutoff = datetime.utcnow() - timedelta(minutes=window_min)
    while events and events[0].timestamp < cutoff:
        events.popleft()


# ─── Detección de tipos de señal ──────────────────────────────────────────────

# Patrones aprendidos del análisis de mensajes Canal 2 (1492 señales 2025+).
# Coinciden con frases reales detectadas en bursts/HIGH RISK/re-enter.

_REENTER_PATTERNS = [
    r"\bre[-\s]?enter\b",
    r"\bre[-\s]?entry\b",
    r"\breentry\b",
    r"\breenter\b",
    r"\benter\s+again\b",
    r"\b(?:another|second|2nd)\s+entry\b",
]

_HIGH_RISK_PATTERNS = [
    r"\bhigh[-\s]?risk\b",
    r"\bhigh[-\s]?risky\b",
    r"\b⚠.*risk\b",
    r"\brisky\s+entry\b",
]


def is_reenter_signal(text: str) -> bool:
    """True si el mensaje contiene marcador de re-entry."""
    if not text:
        return False
    t = text.lower()
    return any(re.search(p, t) for p in _REENTER_PATTERNS)


def is_high_risk_signal(text: str) -> bool:
    """True si el mensaje marca la señal como HIGH RISK."""
    if not text:
        return False
    t = text.lower()
    return any(re.search(p, t) for p in _HIGH_RISK_PATTERNS)


# ─── Filtros de entrada ───────────────────────────────────────────────────────

def should_skip_signal(text: str, direction: str, channel: str,
                       now: Optional[datetime] = None) -> tuple[bool, str]:
    """Decide si saltarse la señal completamente. Devuelve (skip, motivo)."""
    if now is None:
        now = datetime.utcnow()

    # 1. Re-enter: WR baja un 13pp y SL sube un 14pp (data 2025+)
    if config.STRATEGY_SKIP_REENTER and is_reenter_signal(text):
        return True, "RE-ENTER (WR 52% vs 65% baseline)"

    return False, ""


def lot_multiplier_for_signal(text: str) -> tuple[float, str]:
    """Devuelve el multiplicador de lotaje para esta señal y el motivo.

    HIGH RISK → lot/2 (config.STRATEGY_HIGH_RISK_LOT_MULT, default 0.5)
                Avg $+5.88/sig en sample HIGH RISK vs $+11.75 con lot completo.
                Mantenemos el conservador por sample size (n=16).

    Default → 1.0 (lot base sin cambios).
    """
    if is_high_risk_signal(text):
        mult = config.STRATEGY_HIGH_RISK_LOT_MULT
        return mult, f"HIGH RISK (lot x{mult})"
    return 1.0, ""


# ─── Post-SL momentum (point 4) ───────────────────────────────────────────────

def is_post_sl_momentum(channel: str,
                        window_min: Optional[int] = None,
                        max_sl_duration_s: Optional[int] = None,
                        now: Optional[datetime] = None) -> bool:
    """True si la señal anterior cerró por SL en <N segundos hace <M minutos.

    Datos: P(TP5 | SL<10min) = 29% vs base 14% (2x). Pero TP5 directo no funciona
    (ver POST-SL del análisis, da -$400). La estrategia óptima es:
      - Abrir DCA escalonado normal
      - Capar el TP máximo en TP4 (no usar TP5 nunca en post-SL)
      → +$1.078 vs +$422 sin filtro (Avg $+18.28/sig, MaxDD solo $56)

    Esta función solo informa el estado; el listener decide el max_tp_index.
    """
    if window_min is None:
        window_min = config.STRATEGY_POST_SL_WINDOW_MIN
    if max_sl_duration_s is None:
        max_sl_duration_s = config.STRATEGY_POST_SL_MAX_DURATION_S
    if now is None:
        now = datetime.utcnow()

    _purge_old(_recent_sl_hits, window_min)

    for sl in reversed(_recent_sl_hits):
        if sl.channel != channel:
            continue
        if sl.duration_s <= max_sl_duration_s:
            return True
    return False


def record_sl_hit(channel: str, duration_s: float,
                  timestamp: Optional[datetime] = None):
    """Registra un evento de SL hit. Llamado desde el monitor de cierres.

    Si test mode → no se registra (no contaminar el post-SL momentum tracker).
    """
    import journal as _j
    if _j.is_test_mode():
        return
    if timestamp is None:
        timestamp = datetime.utcnow()
    _recent_sl_hits.append(_SLEvent(
        timestamp=timestamp,
        duration_s=duration_s,
        channel=channel,
    ))
    print(f"[Strategies] SL registrado en {channel} (duracion {duration_s:.0f}s)")


def max_tp_index_for_signal(channel: str) -> Optional[int]:
    """TP máximo permitido (índice). None = sin límite.

    Post-SL momentum → cap en TP4 (índice 3).
    Resto → sin límite.
    """
    if not config.STRATEGY_POST_SL_TP_CAP_ENABLED:
        return None
    if is_post_sl_momentum(channel):
        cap = config.STRATEGY_POST_SL_TP_CAP_INDEX
        print(f"[Strategies] Post-SL momentum detectado -> cap TP en indice {cap}")
        return cap
    return None


# ─── Time-stop ─────────────────────────────────────────────────────────────────

def time_stop_for_signal(opened_at: datetime) -> Optional[datetime]:
    """Devuelve el timestamp en el que esta señal debe cerrarse forzosamente.

    Si STRATEGY_TIME_STOP_MIN <= 0 → None (desactivado).
    """
    if config.STRATEGY_TIME_STOP_MIN <= 0:
        return None
    return opened_at + timedelta(minutes=config.STRATEGY_TIME_STOP_MIN)
