"""
test_strategies.py — Suite de regresión para strategies.py.

Cubre:
  - is_reenter_signal (varios formatos)
  - is_high_risk_signal
  - should_skip_signal (RE-ENTER, otros)
  - lot_multiplier_for_signal (normal vs HIGH RISK)
  - record_sl_hit (con cuidado del test mode)
  - is_post_sl_momentum (sin events / con event reciente / con event viejo)
  - max_tp_index_for_signal
  - time_stop_for_signal
  - _purge_old (limpieza de eventos viejos)

NOTA: record_sl_hit chequea journal.is_test_mode() y skipea bajo test_context.
Para los tests de momentum manipulamos _recent_sl_hits directamente para
evitar la dependencia.
"""

import pytest
from datetime import datetime, timedelta
from collections import deque

import config
import strategies
from strategies import (
    is_reenter_signal,
    is_high_risk_signal,
    should_skip_signal,
    lot_multiplier_for_signal,
    is_post_sl_momentum,
    record_sl_hit,
    max_tp_index_for_signal,
    time_stop_for_signal,
    _purge_old,
    _SLEvent,
    _recent_sl_hits,
)


# ─── is_reenter_signal ──────────────────────────────────────────────────────

class TestIsReenterSignal:
    @pytest.mark.parametrize("text", [
        "Re-enter Gold here",
        "RE-ENTER",
        "re enter",
        "re-entry now",
        "reentry on this level",
        "reenter at 4700",
        "Enter again at 4700",
        "Another entry at 4700",
        "second entry incoming",
        "2nd entry",
    ])
    def test_detects_reenter_variants(self, text):
        assert is_reenter_signal(text) is True

    @pytest.mark.parametrize("text", [
        "XAU USD BUY NOW",
        "TP1 hit",
        "Move SL to BE",
        "Close all positions",
        "first entry",  # NO es re-enter, es la primera entrada
    ])
    def test_does_not_match_normal_signals(self, text):
        assert is_reenter_signal(text) is False

    def test_empty_text(self):
        assert is_reenter_signal("") is False

    def test_none_text(self):
        # Defensivo
        assert is_reenter_signal(None) is False


# ─── is_high_risk_signal ────────────────────────────────────────────────────

class TestIsHighRiskSignal:
    @pytest.mark.parametrize("text", [
        "high risk trade 🚨",
        "HIGH RISK",
        "High-risk entry",
        "high risky setup",
        "risky entry incoming",
        "⚠ high risk warning",
    ])
    def test_detects_high_risk_variants(self, text):
        assert is_high_risk_signal(text) is True

    @pytest.mark.parametrize("text", [
        "XAU USD BUY NOW",
        "TP1 hit",
        "normal entry",
    ])
    def test_does_not_match_normal_signals(self, text):
        assert is_high_risk_signal(text) is False

    def test_empty_text(self):
        assert is_high_risk_signal("") is False


# ─── should_skip_signal ─────────────────────────────────────────────────────

class TestShouldSkipSignal:
    def test_reenter_canal2_skipped(self, monkeypatch):
        # Por defecto STRATEGY_SKIP_REENTER=True (default config)
        monkeypatch.setattr(config, "STRATEGY_SKIP_REENTER", True)
        skip, reason = should_skip_signal("Re-enter Gold", "BUY", "canal2")
        assert skip is True
        assert "RE-ENTER" in reason

    def test_reenter_with_skip_disabled(self, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_SKIP_REENTER", False)
        skip, reason = should_skip_signal("Re-enter Gold", "BUY", "canal2")
        assert skip is False
        assert reason == ""

    def test_normal_signal_not_skipped(self, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_SKIP_REENTER", True)
        skip, reason = should_skip_signal("XAU USD BUY NOW", "BUY", "canal2")
        assert skip is False
        assert reason == ""

    def test_canal1_normal_not_skipped(self, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_SKIP_REENTER", True)
        # Canal 1 no manda re-enters explícitos, pero la función igualmente
        # se llama sin texto desde el sticker handler con text=""
        skip, reason = should_skip_signal("", "BUY", "canal1")
        assert skip is False


# ─── lot_multiplier_for_signal ──────────────────────────────────────────────

class TestLotMultiplier:
    def test_normal_signal_returns_1(self):
        mult, reason = lot_multiplier_for_signal("XAU USD BUY NOW")
        assert mult == 1.0
        assert reason == ""

    def test_high_risk_uses_full_lot_even_if_config_is_half(self, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_HIGH_RISK_LOT_MULT", 0.5)
        mult, reason = lot_multiplier_for_signal("HIGH RISK trade incoming")
        assert mult == 1.0
        assert "HIGH RISK" in reason

    def test_high_risk_zero_config_no_longer_skips(self, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_HIGH_RISK_LOT_MULT", 0.0)
        mult, reason = lot_multiplier_for_signal("HIGH RISK")
        assert mult == 1.0
        assert "HIGH RISK" in reason

    def test_high_risk_full_lot(self, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_HIGH_RISK_LOT_MULT", 1.0)
        mult, reason = lot_multiplier_for_signal("HIGH RISK")
        assert mult == 1.0


# ─── _purge_old ─────────────────────────────────────────────────────────────

class TestPurgeOld:
    def test_purges_events_older_than_window(self):
        events: deque[_SLEvent] = deque()
        now = datetime.utcnow()
        old = _SLEvent(timestamp=now - timedelta(minutes=60),
                       duration_s=300, channel="canal2")
        recent = _SLEvent(timestamp=now - timedelta(minutes=5),
                          duration_s=300, channel="canal2")
        events.append(old)
        events.append(recent)

        _purge_old(events, window_min=30)
        assert len(events) == 1
        assert events[0] is recent

    def test_keeps_all_when_within_window(self):
        events: deque[_SLEvent] = deque()
        now = datetime.utcnow()
        events.append(_SLEvent(timestamp=now - timedelta(minutes=5),
                                duration_s=300, channel="canal2"))
        events.append(_SLEvent(timestamp=now - timedelta(minutes=10),
                                duration_s=300, channel="canal2"))
        _purge_old(events, window_min=30)
        assert len(events) == 2

    def test_empty_deque_noop(self):
        events: deque[_SLEvent] = deque()
        _purge_old(events, window_min=30)
        assert len(events) == 0


# ─── is_post_sl_momentum ────────────────────────────────────────────────────

class TestIsPostSlMomentum:
    """Manipulamos _recent_sl_hits directamente para evitar el guard de
    test_mode en record_sl_hit."""

    def test_no_events_returns_false(self, fresh_strategies_history):
        assert is_post_sl_momentum("canal2") is False

    def test_recent_sl_short_duration_returns_true(self, fresh_strategies_history,
                                                    monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_POST_SL_WINDOW_MIN", 30)
        monkeypatch.setattr(config, "STRATEGY_POST_SL_MAX_DURATION_S", 600)
        # SL hit hace 5min con duración 300s → cumple ambos criterios
        _recent_sl_hits.append(_SLEvent(
            timestamp=datetime.utcnow() - timedelta(minutes=5),
            duration_s=300,
            channel="canal2",
        ))
        assert is_post_sl_momentum("canal2") is True

    def test_old_sl_event_purged(self, fresh_strategies_history, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_POST_SL_WINDOW_MIN", 30)
        monkeypatch.setattr(config, "STRATEGY_POST_SL_MAX_DURATION_S", 600)
        # Event de hace 60min, fuera de ventana 30min → NO momentum
        _recent_sl_hits.append(_SLEvent(
            timestamp=datetime.utcnow() - timedelta(minutes=60),
            duration_s=300,
            channel="canal2",
        ))
        assert is_post_sl_momentum("canal2") is False

    def test_long_duration_sl_not_momentum(self, fresh_strategies_history,
                                            monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_POST_SL_WINDOW_MIN", 30)
        monkeypatch.setattr(config, "STRATEGY_POST_SL_MAX_DURATION_S", 600)
        # SL hace 5min PERO duración 1200s (>10min) → NO es post-SL momentum
        # (criterio: SL rápido <10min indica fuerte movimiento contrario)
        _recent_sl_hits.append(_SLEvent(
            timestamp=datetime.utcnow() - timedelta(minutes=5),
            duration_s=1200,
            channel="canal2",
        ))
        assert is_post_sl_momentum("canal2") is False

    def test_other_channel_event_does_not_trigger(self, fresh_strategies_history,
                                                   monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_POST_SL_WINDOW_MIN", 30)
        monkeypatch.setattr(config, "STRATEGY_POST_SL_MAX_DURATION_S", 600)
        # Event en canal1 → NO debe disparar para canal2
        _recent_sl_hits.append(_SLEvent(
            timestamp=datetime.utcnow() - timedelta(minutes=5),
            duration_s=300,
            channel="canal1",
        ))
        assert is_post_sl_momentum("canal2") is False
        assert is_post_sl_momentum("canal1") is True


# ─── max_tp_index_for_signal ────────────────────────────────────────────────

class TestMaxTpIndexForSignal:
    def test_disabled_returns_none(self, fresh_strategies_history,
                                    monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_POST_SL_TP_CAP_ENABLED", False)
        assert max_tp_index_for_signal("canal2") is None

    def test_enabled_no_momentum_returns_none(self, fresh_strategies_history,
                                               monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_POST_SL_TP_CAP_ENABLED", True)
        # Sin SL hit reciente → no momentum → no cap
        assert max_tp_index_for_signal("canal2") is None

    def test_enabled_with_momentum_returns_cap_index(
            self, fresh_strategies_history, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_POST_SL_TP_CAP_ENABLED", True)
        monkeypatch.setattr(config, "STRATEGY_POST_SL_TP_CAP_INDEX", 3)
        monkeypatch.setattr(config, "STRATEGY_POST_SL_WINDOW_MIN", 30)
        monkeypatch.setattr(config, "STRATEGY_POST_SL_MAX_DURATION_S", 600)
        _recent_sl_hits.append(_SLEvent(
            timestamp=datetime.utcnow() - timedelta(minutes=5),
            duration_s=300,
            channel="canal2",
        ))
        assert max_tp_index_for_signal("canal2") == 3


# ─── time_stop_for_signal ───────────────────────────────────────────────────

class TestTimeStopForSignal:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_TIME_STOP_MIN", 0)
        opened_at = datetime(2026, 5, 6, 14, 0, 0)
        assert time_stop_for_signal(opened_at) is None

    def test_negative_returns_none(self, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_TIME_STOP_MIN", -1)
        opened_at = datetime(2026, 5, 6, 14, 0, 0)
        assert time_stop_for_signal(opened_at) is None

    def test_60min_returns_correct_offset(self, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_TIME_STOP_MIN", 60)
        opened_at = datetime(2026, 5, 6, 14, 0, 0)
        expected = datetime(2026, 5, 6, 15, 0, 0)
        assert time_stop_for_signal(opened_at) == expected

    def test_30min_returns_correct_offset(self, monkeypatch):
        monkeypatch.setattr(config, "STRATEGY_TIME_STOP_MIN", 30)
        opened_at = datetime(2026, 5, 6, 14, 0, 0)
        expected = datetime(2026, 5, 6, 14, 30, 0)
        assert time_stop_for_signal(opened_at) == expected
