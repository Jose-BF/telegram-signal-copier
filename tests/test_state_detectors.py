"""
test_state_detectors.py — Helpers PUROS de los detectores Batch C
(fallos silenciosos a nivel de ESTADO interno del bot).

Tres categorías de fallos:

  C1. scale_out partial fill: si algunas legs del modo scale_out fallan al
      abrir, la señal queda con menos posiciones de las esperadas. Antes
      solo se logueaba `scale_out_leg_fill_failed` por leg. Ahora calculamos
      un resumen y emitimos anomaly según severidad:
        - 0 legs OK   → critical (señal degenerada)
        - <50% legs   → critical (degradación severa)
        - 50-99%      → warning  (parcial razonable, pero merece review)
        - 100%        → no anomaly (caso normal)

  C2. Position lifecycle monitor crash: position_lifecycle_monitor.start crea una asyncio.Task que corre
      indefinidamente. Si run() lanza unhandled exception, la task muere
      en silencio (asyncio.create_task no re-lanza). Helper decide si una
      task terminada merece anomaly.

  C3. state.add overwrite: si añadimos un Signal con (channel, msg_id) ya
      existente, el viejo se reemplaza. Antes silencio total; ahora
      detectamos el overwrite. No es un escenario común pero pasa con
      bugs sutiles (ej. re-add tras resync incorrecto).
"""
import asyncio
import pytest
from datetime import datetime

from listener import (
    _scale_out_fill_summary,
    _position_lifecycle_monitor_task_anomaly_severity,
    _detect_state_add_overwrite,
)
from state import Signal, StateManager


# ───────────────────── C1 — Scale-out partial fill ──────────────────────

class TestScaleOutFillSummary:
    """Severidad según fracción de legs llenadas."""

    def test_todas_llenan_no_anomaly(self):
        result = _scale_out_fill_summary(n_legs_attempted=5, n_legs_filled=5)
        assert result["severity"] is None
        assert result["fill_ratio"] == 1.0

    def test_ninguna_llena_es_critical(self):
        result = _scale_out_fill_summary(n_legs_attempted=5, n_legs_filled=0)
        assert result["severity"] == "critical"
        assert result["fill_ratio"] == 0.0

    def test_menos_50_pct_es_critical(self):
        # 2/5 = 40% → degradación severa
        result = _scale_out_fill_summary(n_legs_attempted=5, n_legs_filled=2)
        assert result["severity"] == "critical"

    def test_entre_50_y_99_es_warning(self):
        # 3/5 = 60% → parcial razonable
        result = _scale_out_fill_summary(n_legs_attempted=5, n_legs_filled=3)
        assert result["severity"] == "warning"

    def test_4_de_5_es_warning(self):
        # 4/5 = 80% → parcial razonable
        result = _scale_out_fill_summary(n_legs_attempted=5, n_legs_filled=4)
        assert result["severity"] == "warning"

    def test_n_legs_attempted_0_no_anomaly(self):
        # No se intentó nada (e.g., n_entries=1 → no hay extra legs). No
        # debe alertar.
        result = _scale_out_fill_summary(n_legs_attempted=0, n_legs_filled=0)
        assert result["severity"] is None


# ────────────── C2 — Position lifecycle monitor task crash detector ──────────────

class TestPositionLifecycleMonitorTaskAnomalySeverity:
    """Cuando una task de position_lifecycle_monitor.run() termina, decidir si emitir
    anomaly. Solo emitimos si HUBO excepción NO esperada (CancelledError
    se ignora porque es shutdown ordenado)."""

    def test_normal_completion_no_anomaly(self):
        # No exception → run() terminó normalmente. La señal cerró bien.
        assert _position_lifecycle_monitor_task_anomaly_severity(None) is None

    def test_cancelled_es_normal(self):
        # CancelledError es shutdown del bot, no es bug.
        exc = asyncio.CancelledError()
        assert _position_lifecycle_monitor_task_anomaly_severity(exc) is None

    def test_otras_excepciones_son_critical(self):
        # Cualquier otra excepción durante run() = bug grave, el monitor
        # murió y la señal queda sin vigilancia (TPs/SL/time-stop).
        exc = RuntimeError("MT5 connection refused")
        assert _position_lifecycle_monitor_task_anomaly_severity(exc) == "critical"

    def test_value_error_critical(self):
        exc = ValueError("bad ticket id")
        assert _position_lifecycle_monitor_task_anomaly_severity(exc) == "critical"


# ───────── C3 — state.add overwrite detector ─────────

class TestDetectStateAddOverwrite:
    """state.add(s) con (s.channel, s.message_id) ya en _signals reemplaza
    silenciosamente. Detector compara contra el dict actual ANTES de añadir.
    """

    def test_primera_vez_no_overwrite(self):
        st = StateManager()
        new_sig = Signal(channel="canal1", message_id=10, direction="BUY")
        assert _detect_state_add_overwrite(new_sig, st) is False

    def test_misma_key_distinto_objeto_es_overwrite(self):
        st = StateManager()
        existing = Signal(channel="canal1", message_id=10, direction="BUY",
                          status="open")
        st.add(existing)
        # Otro Signal con la misma key → overwrite
        new_sig = Signal(channel="canal1", message_id=10, direction="SELL")
        assert _detect_state_add_overwrite(new_sig, st) is True

    def test_misma_key_mismo_objeto_no_es_overwrite(self):
        # re-add del mismo objeto (idempotente) → no es overwrite real
        st = StateManager()
        sig = Signal(channel="canal1", message_id=10, direction="BUY")
        st.add(sig)
        assert _detect_state_add_overwrite(sig, st) is False

    def test_canal_distinto_misma_msg_id_no_overwrite(self):
        # canal1_10 y canal2_10 son keys distintas
        st = StateManager()
        sig_c1 = Signal(channel="canal1", message_id=10, direction="BUY")
        st.add(sig_c1)
        sig_c2 = Signal(channel="canal2", message_id=10, direction="BUY")
        assert _detect_state_add_overwrite(sig_c2, st) is False
