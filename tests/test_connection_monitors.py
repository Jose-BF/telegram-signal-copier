"""
test_connection_monitors.py — Decisión pura de los monitores de conexión.

Los monitores de Telegram y MT5 detectan disconnect sostenido y emiten
anomaly crítica. La lógica de "¿debo alertar ya?" la encapsulamos en una
función pura para testearla sin async loops ni mocks de Telethon/MT5.
"""
import pytest

import main


class TestShouldAlertSustainedDisconnect:
    """Reglas del alerter:
       True solo si — estoy desconectado AHORA, lo estaba también la
       observación anterior (sustained, no transición), llevo >= threshold
       en este estado, y aún no he alertado para este disconnect."""

    def test_alert_cuando_desconectado_largo_sin_alertar(self):
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=False, age_s=400,
            already_alerted=False, threshold_s=300) is True

    def test_no_alert_si_conectado(self):
        assert main._should_alert_sustained_disconnect(
            connected=True, last_state=True, age_s=400,
            already_alerted=False, threshold_s=300) is False

    def test_no_alert_si_recien_desconectado(self):
        # Aún por debajo del umbral
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=False, age_s=100,
            already_alerted=False, threshold_s=300) is False

    def test_no_alert_si_ya_alertado(self):
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=False, age_s=400,
            already_alerted=True, threshold_s=300) is False

    def test_no_alert_durante_la_transicion(self):
        # Estaba CONECTADO y ahora aparece desconectado: es la primera
        # detección, no es sustained — last_state debe ser False también.
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=True, age_s=400,
            already_alerted=False, threshold_s=300) is False

    def test_no_alert_si_last_state_none(self):
        # Primer ciclo del loop: last_state es None hasta la primera lectura.
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=None, age_s=400,
            already_alerted=False, threshold_s=300) is False

    def test_alert_exacto_en_threshold(self):
        # >= threshold → True
        assert main._should_alert_sustained_disconnect(
            connected=False, last_state=False, age_s=300.0,
            already_alerted=False, threshold_s=300.0) is True
