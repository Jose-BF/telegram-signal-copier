"""
test_async_loop_detectors.py — Helpers PUROS de Batch E.

Batch E cubre fallos silenciosos de los async loops (pending_actions y
dca_monitor) — sitios donde el bot puede quedar girando indefinidamente
sin ejecutar nada y sin alerta:

  - Null-tick streak: si mt5.symbol_info_tick devuelve None repetidamente
    (broker desconectado, simbolo deslistado, etc.), los loops giran sin
    progresar. Helper PURO decide cuando emitir anomaly.

  - Stuck transient retcode: si una pending action tiene un retcode
    TRANSIENT que persiste >5min (autotrading desactivado permanentemente,
    mercado cerrado por dias), el queue se queda atascado. Helper PURO
    decide cuando emitir warning.
"""
import pytest

from pending_actions import _should_alert_null_tick_streak
from dca_monitor import _should_alert_null_tick_streak as _dca_should_alert


class TestPendingActionsNullTickStreak:
    """Mismo patron que main._should_alert_sustained_disconnect:
       True solo si — vamos por encima del threshold Y aun no alertamos.
    """

    def test_streak_bajo_no_alerta(self):
        assert _should_alert_null_tick_streak(
            streak=50, already_alerted=False, threshold=300) is False

    def test_streak_alto_no_alertado(self):
        assert _should_alert_null_tick_streak(
            streak=400, already_alerted=False, threshold=300) is True

    def test_streak_alto_ya_alertado_no_re_alerta(self):
        assert _should_alert_null_tick_streak(
            streak=500, already_alerted=True, threshold=300) is False

    def test_streak_exacto_en_threshold(self):
        assert _should_alert_null_tick_streak(
            streak=300, already_alerted=False, threshold=300) is True


class TestDcaMonitorNullTickStreak:
    """Mismo patron — el helper en dca_monitor es identico (extraido
    compartido). Tests aqui para confirmar."""

    def test_streak_bajo_no_alerta(self):
        assert _dca_should_alert(
            streak=10, already_alerted=False, threshold=100) is False

    def test_streak_alto_no_alertado(self):
        assert _dca_should_alert(
            streak=150, already_alerted=False, threshold=100) is True

    def test_ya_alertado_no_re_alerta(self):
        assert _dca_should_alert(
            streak=200, already_alerted=True, threshold=100) is False


class TestStuckTransientRetcode:
    """Si una pending action tiene retcode TRANSIENT y lleva >X seg en cola
    sin avanzar, es senal de algo estructural (autotrading off, broker
    desconectado). Helper decide si emitir warning (no DROP — queremos
    seguir reintentando, solo notificar al user).
    """

    def test_recien_creada_no_warning(self):
        from pending_actions import _stuck_transient_severity
        # 10s con threshold 300 → no warning
        assert _stuck_transient_severity(
            retcode=10027, age_s=10, threshold_s=300,
            already_warned=False) is None

    def test_atascada_por_encima_threshold_warning(self):
        from pending_actions import _stuck_transient_severity
        assert _stuck_transient_severity(
            retcode=10027, age_s=400, threshold_s=300,
            already_warned=False) == "warning"

    def test_atascada_ya_warned_no_re_warning(self):
        from pending_actions import _stuck_transient_severity
        assert _stuck_transient_severity(
            retcode=10027, age_s=500, threshold_s=300,
            already_warned=True) is None

    def test_retcode_no_transient_no_warning(self):
        # 10016 INVALID_STOPS no es TRANSIENT (es STOPS) — tiene su propio
        # threshold (STOPS_STRUCTURAL_THRESHOLD_S=30s) que ya se aplica
        # como DROP. Aqui solo cubrimos retcodes que estarian
        # indefinidamente reintentandose sin esa proteccion.
        from pending_actions import _stuck_transient_severity
        assert _stuck_transient_severity(
            retcode=10016, age_s=500, threshold_s=300,
            already_warned=False) is None
