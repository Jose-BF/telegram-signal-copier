"""
test_reconcile.py — Suite de regresion para reconcile.py.

Cubre las funciones puras del reconciliador:
  - parse_sig_role: del comment de un deal MT5 → (sig_id, role)
  - close_reason_from_comment: motivo de cierre
  - reconcile_signal: cruce journal + MT5 → fila del ledger

Y el sync-wait contra la race del historial MT5:
  - _fetch_deals_synced: reintenta history_deals_get hasta que el
    terminal MT5 termina de sincronizar (mocks de mt5 + time.sleep).

reconcile.py es la fuente de verdad del sistema de logs — su correccion
es critica. Estos tests usan casos REALES de comments MT5.
"""

import json
from types import SimpleNamespace

import pytest

import reconcile
from reconcile import (
    parse_sig_role,
    close_reason_from_comment,
    reconcile_signal,
)


# ─── parse_sig_role ─────────────────────────────────────────────────────────

class TestParseSigRole:
    def test_market_a(self):
        assert parse_sig_role("c2_12497") == ("canal2_12497", "market_a")

    def test_market_b(self):
        # Caso real del doble market
        assert parse_sig_role("c2_12497_B") == ("canal2_12497", "market_b")

    def test_rescue(self):
        assert parse_sig_role("c2_12015_rescue") == ("canal2_12015", "rescue")

    def test_dca_nuevo(self):
        # DCA formato nuevo: lleva sig_id
        assert parse_sig_role("DCA_c1_19569_4593.5") == ("canal1_19569", "dca")

    def test_canal1(self):
        assert parse_sig_role("c1_19717") == ("canal1_19717", "market_a")

    def test_basura_descartada(self):
        # comments de pruebas viejas con message_id corto → None
        assert parse_sig_role("c2_55") == (None, None)
        assert parse_sig_role("c2_57") == (None, None)

    def test_dca_viejo_sin_sigid(self):
        # DCA formato viejo: NO tiene sig_id → no se puede parsear
        assert parse_sig_role("DCA_4572.0") == (None, None)

    def test_cierre_no_es_sig(self):
        assert parse_sig_role("[tp 4682.00]") == (None, None)
        assert parse_sig_role("[sl 4694.00]") == (None, None)
        assert parse_sig_role("bot_close") == (None, None)

    def test_vacio(self):
        assert parse_sig_role("") == (None, None)
        assert parse_sig_role(None) == (None, None)

    def test_scale_out_legs(self):
        # Legs del modo scale_out: c2_<id>_B1..B4 → rol 'scale_out_leg',
        # con el sig_id correcto.
        assert parse_sig_role("c2_12497_B1") == ("canal2_12497", "scale_out_leg")
        assert parse_sig_role("c2_12497_B4") == ("canal2_12497", "scale_out_leg")
        assert parse_sig_role("c1_19717_B3") == ("canal1_19717", "scale_out_leg")

    def test_market_b_legacy_no_es_leg(self):
        # El _B sin numero (doble market legacy) NO debe confundirse con una
        # leg del scale_out.
        assert parse_sig_role("c2_12497_B") == ("canal2_12497", "market_b")


# ─── close_reason_from_comment ──────────────────────────────────────────────

class TestCloseReason:
    def test_sl(self):
        assert close_reason_from_comment("[sl 4694.00]") == "sl"

    def test_tp(self):
        assert close_reason_from_comment("[tp 4682.00]") == "tp"

    def test_be(self):
        assert close_reason_from_comment("[be 4700.00]") == "be"

    def test_bot_close(self):
        assert close_reason_from_comment("bot_close") == "bot_close"

    def test_other(self):
        assert close_reason_from_comment("algo raro") == "other"
        assert close_reason_from_comment("") == "other"


# ─── reconcile_signal ───────────────────────────────────────────────────────

def _pos(role, pnl, closed=True):
    """Helper: construye un position dict minimo."""
    return {
        "position_id": hash(role) % 10000,
        "ticket": hash(role) % 10000,
        "role": role,
        "open_price": 4700.0, "open_dt_utc": "2026-05-15T12:00:00",
        "close_price": 4702.0,
        "close_dt_utc": "2026-05-15T12:10:00" if closed else None,
        "close_reason": "tp" if closed else None,
        "is_closed": closed,
        "pnl_net": pnl, "volume": 0.01,
    }


class TestReconcileSignal:
    def test_trade_normal_reconciliado(self):
        """Journal y MT5 coinciden → reconciled_ok=True, sin flags."""
        journal = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-05-15T12:00:00",
            "journal_total_pl": 8.0, "has_signal_closed": True,
            "n_market_filled": 1, "n_market_b_filled": 1, "n_dca_filled": 0,
            "tp_hit_indices": {0, 1},
        }
        mt5_pos = [_pos("market_a", 3.0), _pos("market_b", 5.0)]
        row = reconcile_signal("canal2_99999", journal, mt5_pos)
        assert row["status"] == "closed"
        assert row["pnl_real_mt5"] == 8.0
        assert row["reconciled_ok"] is True
        assert row["pnl_mt5_complete"] is True
        assert row["max_tp_idx_touched"] == 1
        assert row["flags"] == []

    def test_discrepancia_pnl(self):
        """Journal dice un P&L distinto al de MT5 → flag de discrepancia.
        Replica canal2_12497 (journal -1.27, MT5 +4.78)."""
        journal = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-05-15T12:39:00",
            "journal_total_pl": -1.27, "has_signal_closed": True,
            "n_market_filled": 1, "n_market_b_filled": 1, "n_dca_filled": 0,
            "tp_hit_indices": set(),
        }
        mt5_pos = [_pos("market_a", -1.27), _pos("market_b", 6.05)]
        row = reconcile_signal("canal2_12497", journal, mt5_pos)
        assert row["pnl_real_mt5"] == 4.78
        assert row["reconciled_ok"] is False
        assert row["pnl_discrepancy"] == 6.05
        assert any("PNL_DISCREPANCY" in f for f in row["flags"])

    def test_huerfano(self):
        """Journal sin signal_closed pero MT5 muestra cierre → HUERFANO."""
        journal = {
            "channel": "canal1", "direction": "SELL",
            "signal_dt_utc": "2026-05-14T13:00:00",
            "journal_total_pl": None, "has_signal_closed": False,
            "n_market_filled": 1, "n_market_b_filled": 1, "n_dca_filled": 0,
            "tp_hit_indices": set(),
        }
        mt5_pos = [_pos("market_a", 1.5), _pos("market_b", 2.2)]
        row = reconcile_signal("canal1_19684", journal, mt5_pos)
        assert row["status"] == "closed"
        assert row["pnl_real_mt5"] == 3.7
        assert any("HUERFANO" in f for f in row["flags"])

    def test_formato_viejo_pnl_parcial(self):
        """Journal dice 5 posiciones pero MT5 solo identifica 1 (DCAs
        formato viejo sin sig_id) → pnl_mt5_complete=False, no se evalua
        discrepancia."""
        journal = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-04-29T11:55:00",
            "journal_total_pl": 26.19, "has_signal_closed": True,
            "n_market_filled": 1, "n_market_b_filled": 0, "n_dca_filled": 4,
            "tp_hit_indices": set(),
        }
        mt5_pos = [_pos("market_a", 2.12)]   # solo 1 de 5
        row = reconcile_signal("canal2_12015", journal, mt5_pos)
        assert row["pnl_mt5_complete"] is False
        assert row["reconciled_ok"] is None  # no se evalua
        assert any("PNL_PARCIAL" in f for f in row["flags"])

    def test_market_b_perdido(self):
        """Journal registro market_b pero MT5 no lo tiene → flag especifico."""
        journal = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-05-15T12:00:00",
            "journal_total_pl": -1.27, "has_signal_closed": True,
            "n_market_filled": 1, "n_market_b_filled": 1, "n_dca_filled": 0,
            "tp_hit_indices": set(),
        }
        mt5_pos = [_pos("market_a", -1.27)]   # falta el market_b
        row = reconcile_signal("canal2_12497", journal, mt5_pos)
        # n_pos (1) < n_pos_journal (2) → pnl parcial
        assert row["pnl_mt5_complete"] is False
        assert any("MARKET_B_PERDIDO" in f for f in row["flags"])

    def test_sin_posicion(self):
        """Senal recibida pero nunca abrio en MT5."""
        journal = {
            "channel": "canal2", "direction": "SELL",
            "signal_dt_utc": "2026-05-14T05:57:00",
            "journal_total_pl": None, "has_signal_closed": False,
            "n_market_filled": 0, "n_market_b_filled": 0, "n_dca_filled": 0,
            "tp_hit_indices": set(),
        }
        row = reconcile_signal("canal2_12359", journal, [])
        assert row["status"] == "no_position"
        assert row["n_positions"] == 0

    def test_scale_out_5_legs_completas(self):
        """scale_out: el journal cuenta 1 market + 4 legs = 5 posiciones;
        MT5 tiene las 5 → P&L completo, n_positions_journal = 5."""
        journal = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-05-19T10:00:00",
            "journal_total_pl": 13.0, "has_signal_closed": True,
            "n_market_filled": 1, "n_market_b_filled": 0, "n_dca_filled": 0,
            "n_scale_out_legs": 4,
            "tp_hit_indices": set(),
        }
        mt5_pos = [_pos("m", 3.0), _pos("l1", 3.0), _pos("l2", 3.0),
                   _pos("l3", 2.0), _pos("l4", 2.0)]
        row = reconcile_signal("canal2_99999", journal, mt5_pos)
        assert row["n_positions_journal"] == 5
        assert row["pnl_mt5_complete"] is True

    def test_scale_out_leg_perdida_detectada(self):
        """REGRESION: si una leg del scale_out no llega a MT5, el journal
        cuenta 5 y MT5 solo 4 → pnl_mt5_complete=False y flag PNL_PARCIAL.
        Antes del fix reconcile contaba 1 (ignoraba scale_out_leg_filled)
        y la leg perdida pasaba desapercibida."""
        journal = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-05-19T10:00:00",
            "journal_total_pl": 9.0, "has_signal_closed": True,
            "n_market_filled": 1, "n_market_b_filled": 0, "n_dca_filled": 0,
            "n_scale_out_legs": 4,
            "tp_hit_indices": set(),
        }
        mt5_pos = [_pos("m", 3.0), _pos("l1", 3.0),
                   _pos("l2", 2.0), _pos("l3", 1.0)]   # solo 4 de 5
        row = reconcile_signal("canal2_99999", journal, mt5_pos)
        assert row["n_positions_journal"] == 5
        assert row["pnl_mt5_complete"] is False
        assert any("PNL_PARCIAL" in f for f in row["flags"])


# ─── _fetch_deals_synced (sync-wait contra la race del historial MT5) ───────

def _deal(comment):
    """Deal MT5 falso. _fetch_deals_synced solo lee el campo .comment."""
    return SimpleNamespace(comment=comment)


class TestFetchDealsSynced:
    """Sync-wait de reconcile contra la race de sincronizacion de MT5.

    Tras mt5.initialize() el terminal baja el historial reciente del
    servidor de forma ASINCRONA; history_deals_get consultado de inmediato
    puede devolver un historial INCOMPLETO. _fetch_deals_synced reintenta
    hasta que (1) las senales que el journal dice rellenadas aparecen en el
    historial y (2) el conteo de deals se estabiliza.

    Los tests mockean mt5.history_deals_get (la lista CRECE entre llamadas,
    simulando el sync) y time.sleep (para no esperar de verdad).
    """

    @staticmethod
    def _patch(monkeypatch, snapshots):
        """Mockea history_deals_get: devuelve snapshots[i] en cada llamada
        (la ultima se repite indefinidamente) y anula time.sleep.
        Devuelve la lista de llamadas para poder contarlas."""
        calls = []

        def fake_get(_t_from, _t_to):
            i = min(len(calls), len(snapshots) - 1)
            calls.append(i)
            return snapshots[i]

        monkeypatch.setattr(reconcile.mt5, "history_deals_get", fake_get)
        monkeypatch.setattr(reconcile.time, "sleep", lambda *_a: None)
        return calls

    def test_race_espera_y_devuelve_el_set_completo(self, monkeypatch):
        """REGRESION del bug 2026-05-18: la 1a consulta NO trae la senal del
        dia. _fetch_deals_synced debe esperar y devolver el historial
        COMPLETO — nunca el parcial que reconciliaria la senal como
        'no_position'."""
        old = _deal("c1_11111")          # senal vieja, ya en cache local
        today = _deal("c1_99999")        # senal de hoy, el sync la trae tarde
        calls = self._patch(monkeypatch, [
            (old,),                      # call 1: falta la senal de hoy
            (old, today),                # call 2: aparece, pero conteo crecio
            (old, today),                # call 3: conteo estable → se acepta
        ])
        result = reconcile._fetch_deals_synced(
            None, None, {"canal1_99999"}, quiet=True)
        assert len(result) == 2          # set COMPLETO, no el parcial de 1
        assert {parse_sig_role(d.comment)[0] for d in result} == {
            "canal1_11111", "canal1_99999"}
        assert len(calls) == 3           # 1 inicial + 2 reintentos: espero

    def test_ya_sincronizado_devuelve_rapido(self, monkeypatch):
        """Historial ya completo → devuelve tras una sola lectura de
        confirmacion (oraculo cumplido + conteo estable)."""
        full = (_deal("c1_11111"), _deal("c2_22222"))
        calls = self._patch(monkeypatch, [full, full])
        result = reconcile._fetch_deals_synced(
            None, None, {"canal2_22222"}, quiet=True)
        assert len(result) == 2
        assert len(calls) == 2           # 1 inicial + 1 confirmacion

    def test_sin_oraculo_corta_por_estabilidad_de_conteo(self, monkeypatch):
        """Sin expected_sigs (ninguna senal reciente que esperar) el unico
        criterio de corte es que el conteo de deals deje de crecer."""
        deals = (_deal("c1_11111"),)
        calls = self._patch(monkeypatch, [deals, deals])
        result = reconcile._fetch_deals_synced(None, None, None, quiet=True)
        assert len(result) == 1
        assert len(calls) == 2

    def test_sync_nunca_completa_degrada_sin_colgarse(self, monkeypatch):
        """Si MT5 nunca trae la senal esperada (conexion caida, posicion
        realmente perdida) agota los reintentos y devuelve lo disponible —
        sin excepcion ni bucle infinito."""
        partial = (_deal("c1_11111"),)   # canal1_99999 no aparece JAMAS
        calls = self._patch(monkeypatch, [partial])
        result = reconcile._fetch_deals_synced(
            None, None, {"canal1_99999"}, quiet=True)
        assert len(result) == 1          # devuelve lo que hay, no se cuelga
        assert len(calls) >= 12          # agoto los reintentos


# ─── reconcile_signal enriquecido (T5 — Capa 2 Pt.1) ────────────────────────

class TestReconcileSignalEnrichedV1:
    """Rollup de anomalies+health+entry_quality+market_context+bot_version
    en cada fila del ledger. El journal dict ya viene enriquecido por
    load_journal_index (cubierto por integración al correr reconcile.py)."""

    def _base_journal(self, **overrides):
        base = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-05-20T10:00:00",
            "journal_total_pl": None, "has_signal_closed": False,
            "n_market_filled": 1, "n_market_b_filled": 0,
            "n_dca_filled": 0, "tp_hit_indices": set(),
            "anomalies": [],
        }
        base.update(overrides)
        return base

    def test_health_ok_sin_anomalias(self):
        row = reconcile_signal("canal2_99", self._base_journal(), [])
        assert row["health"] == "ok"
        assert row["anomalies"] == []

    def test_health_failed_con_critical(self):
        anomalies = [{"ts": "2026-05-20T10:00:05", "category": "naked",
                      "severity": "critical", "detail": "no SL"}]
        row = reconcile_signal(
            "canal2_88", self._base_journal(anomalies=anomalies), [])
        assert row["anomalies"] == anomalies
        assert row["health"] == "failed"

    def test_health_degraded_con_warning(self):
        anomalies = [{"ts": "...", "category": "sl_be",
                      "severity": "warning", "detail": "BE imposible"}]
        row = reconcile_signal(
            "canal2_77", self._base_journal(anomalies=anomalies), [])
        assert row["health"] == "degraded"

    def test_entry_quality_se_propaga(self):
        eq = {"case": "A_inside", "distance_to_zone_usd": 0.0}
        row = reconcile_signal(
            "canal2_55", self._base_journal(entry_quality=eq), [])
        assert row["entry_quality"] == eq

    def test_market_context_se_propaga(self):
        mc = {"atr_m5_14": 1.85, "recent_5m_range": [2000.0, 2002.0],
              "current_price_at_signal": 2001.0}
        row = reconcile_signal(
            "canal2_44", self._base_journal(market_context=mc), [])
        assert row["market_context"] == mc

    def test_bot_version_se_propaga(self):
        bv = {"git_commit": "abc1234", "git_branch": "main",
              "git_dirty": False,
              "session_started_utc": "2026-05-20T09:00:00"}
        row = reconcile_signal(
            "canal2_33", self._base_journal(bot_version=bv), [])
        assert row["bot_version"] == bv

    def test_defaults_para_journals_viejos(self):
        """Compat con journals viejos (sin los campos nuevos): defaults limpios."""
        old = {"channel": "canal2", "direction": "BUY",
               "signal_dt_utc": "2026-05-20T10:00:00",
               "journal_total_pl": None, "has_signal_closed": False,
               "n_market_filled": 0, "n_market_b_filled": 0,
               "n_dca_filled": 0, "tp_hit_indices": set()}
        row = reconcile_signal("canal2_22", old, [])
        assert row["anomalies"] == []
        assert row["health"] == "ok"
        assert row["entry_quality"] is None
        assert row["market_context"] is None
        assert row["bot_version"] is None


# ─── reconcile_signal enriquecido (T6 — Capa 2 Pt.2) ────────────────────────

class TestReconcileSignalEnrichedV2:
    """Rollup de signal_text + management + timeline en cada fila del ledger.
    Completa el expediente por trade (T6 del plan)."""

    def _base(self, **overrides):
        base = {
            "channel": "canal2", "direction": "BUY",
            "signal_dt_utc": "2026-05-20T10:00:00",
            "journal_total_pl": None, "has_signal_closed": False,
            "n_market_filled": 1, "n_market_b_filled": 0,
            "n_dca_filled": 0, "tp_hit_indices": set(),
            "anomalies": [],
            "signal_text": None,
            "management": [],
            "timeline": [],
        }
        base.update(overrides)
        return base

    def test_signal_text_se_propaga(self):
        row = reconcile_signal("c2_44", self._base(
            signal_text="XAU USD BUY NOW"), [])
        assert row["signal_text"] == "XAU USD BUY NOW"

    def test_timeline_se_propaga(self):
        timeline = [
            {"ts": "2026-05-20T10:00:00", "event": "signal_received"},
            {"ts": "2026-05-20T10:00:01", "event": "market_filled"},
            {"ts": "2026-05-20T10:00:30", "event": "tp_hit"},
            {"ts": "2026-05-20T10:00:31", "event": "signal_closed"},
        ]
        row = reconcile_signal("c2_55", self._base(timeline=timeline), [])
        assert row["timeline"] == timeline
        assert len(row["timeline"]) == 4

    def test_management_se_propaga(self):
        mgmt = [{"ts": "2026-05-20T10:05:00",
                 "raw_text": "Move SL to BE",
                 "classified": "MOVE_SL_TO_BE", "confidence": 0.95,
                 "applied": True, "skip_reason": None}]
        row = reconcile_signal("c2_33", self._base(management=mgmt), [])
        assert row["management"] == mgmt
        assert row["management"][0]["classified"] == "MOVE_SL_TO_BE"

    def test_defaults_T6_para_journals_viejos(self):
        old = {"channel": "canal2", "direction": "BUY",
               "signal_dt_utc": "2026-05-20T10:00:00",
               "journal_total_pl": None, "has_signal_closed": False,
               "n_market_filled": 0, "n_market_b_filled": 0,
               "n_dca_filled": 0, "tp_hit_indices": set()}
        row = reconcile_signal("c2_22", old, [])
        assert row["signal_text"] is None
        assert row["management"] == []
        assert row["timeline"] == []


class TestReconcileForensicLifecycle:
    """Black-box fields for per-ticket replay and outcome analysis."""

    def _base(self, **overrides):
        base = {
            "channel": "canal1", "direction": "BUY",
            "signal_dt_utc": "2026-05-21T11:23:11+00:00",
            "journal_total_pl": None, "has_signal_closed": True,
            "n_market_filled": 1, "n_market_b_filled": 0,
            "n_dca_filled": 0, "n_scale_out_legs": 0,
            "tp_hit_indices": set(),
            "anomalies": [],
            "signal_text": "BUY GOLD NOW",
            "management": [],
            "timeline": [],
        }
        base.update(overrides)
        return base

    def test_ticket_sl_tp_history_is_attached_to_matching_position(self):
        journal = self._base(ticket_level_history={
            111: {
                "sl_history": [
                    {"ts": "2026-05-21T11:24:00+00:00",
                     "sl": 4525.0, "source": "SL #111",
                     "status": "confirmed"}
                ],
                "tp_history": [
                    {"ts": "2026-05-21T11:24:00+00:00",
                     "tp": 4548.0, "source": "TP[0]->4548 #111",
                     "status": "confirmed"}
                ],
            }
        })
        mt5_pos = [_pos("market_a", -10.0)]
        mt5_pos[0]["ticket"] = 111
        row = reconcile_signal("canal1_19822", journal, mt5_pos)
        assert row["positions"][0]["sl_history"][0]["sl"] == 4525.0
        assert row["positions"][0]["tp_history"][0]["tp"] == 4548.0

    def test_strategy_snapshot_is_propagated_to_ledger_row(self):
        snapshot = {"entry_mode": "scale_out", "num_entries": 4,
                    "time_stop_min": 60, "adverse_action": "rescue_market"}
        row = reconcile_signal("canal1_19822",
                               self._base(strategy_snapshot=snapshot), [])
        assert row["strategy_snapshot"] == snapshot

    def test_sl_after_time_stop_adds_derived_outcome_anomaly(self):
        timeline = [
            {"ts": "2026-05-21T12:23:11+00:00",
             "event": "time_stop_notified"}
        ]
        mt5_pos = [_pos("market_a", -68.81)]
        mt5_pos[0]["ticket"] = 222
        mt5_pos[0]["close_reason"] = "sl"
        mt5_pos[0]["close_dt_utc"] = "2026-05-21T12:55:00+00:00"
        row = reconcile_signal("canal1_19822",
                               self._base(timeline=timeline), mt5_pos)
        assert row["post_time_stop_outcome"] == "sl_after_time_stop"
        assert any(a.get("category") == "outcome"
                   and a.get("derived") is True
                   for a in row["anomalies"])

    def test_post_time_stop_outcome_tolerates_naive_close_timestamp(self):
        timeline = [
            {"ts": "2026-05-21T12:23:11+00:00",
             "event": "time_stop_notified"}
        ]
        mt5_pos = [_pos("market_a", -12.5)]
        mt5_pos[0]["close_reason"] = "sl"
        mt5_pos[0]["close_dt_utc"] = "2026-05-21T12:55:00"
        row = reconcile_signal("canal1_19822",
                               self._base(timeline=timeline), mt5_pos)
        assert row["post_time_stop_outcome"] == "sl_after_time_stop"

    def test_mt5_server_time_offset_is_normalized_from_market_fill(self):
        timeline = [
            {"ts": "2026-05-29T16:30:12+00:00",
             "event": "market_filled"}
        ]
        mt5_pos = [_pos("market_a", 8.5)]
        mt5_pos[0]["open_dt_utc"] = "2026-05-29T19:30:12+00:00"
        mt5_pos[0]["close_dt_utc"] = "2026-05-29T19:42:12+00:00"

        row = reconcile_signal(
            "canal1_19822",
            self._base(signal_dt_utc="2026-05-29T16:30:11+00:00",
                       timeline=timeline),
            mt5_pos,
        )

        assert row["mt5_time_offset_s"] == 10800
        assert row["open_dt_utc"] == "2026-05-29T16:30:12+00:00"
        assert row["close_dt_utc"] == "2026-05-29T16:42:12+00:00"
        assert row["positions"][0]["open_dt_mt5_raw"] == "2026-05-29T19:30:12+00:00"
        assert row["positions"][0]["close_dt_mt5_raw"] == "2026-05-29T19:42:12+00:00"

    def test_autotrading_disabled_marks_trade_excluded_from_analysis(self):
        order_lifecycle = [
            {"ts": "2026-06-01T07:20:00+00:00",
             "ev": "mt5_order_result",
             "retcode": 10027,
             "comment": "AutoTrading disabled by client"},
        ]

        row = reconcile_signal(
            "canal2_13111",
            self._base(order_lifecycle=order_lifecycle),
            [],
        )

        assert row["analysis_excluded"] is True
        assert row["analysis_exclusions"][0]["code"] == "mt5_client_autotrading_disabled"
        assert row["analysis_exclusions"][0]["retcode"] == 10027
        assert any("MT5_AUTOTRADING_DISABLED" in f for f in row["flags"])


class TestLoadJournalForensicEvents:
    def test_position_snapshot_enters_timeline_and_level_history(
            self, tmp_path):
        path = tmp_path / "events.jsonl"
        rows = [
            {"ts": "2026-05-21T11:00:00+00:00", "sig": "canal2_12747",
             "ev": "signal_received", "direction": "BUY"},
            {"ts": "2026-05-21T11:01:02+00:00", "sig": "canal2_12747",
             "ev": "mt5_position_snapshot", "ticket": 111,
             "after_action": "MODIFY_SLTP", "label": "BE #111",
             "position_exists": True, "sl": 4525.0, "tp": 4548.0},
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows),
                        encoding="utf-8")

        idx = reconcile.load_journal_index(path)
        row = idx["canal2_12747"]
        hist = row["ticket_level_history"]["111"]

        assert row["timeline"][-1] == {
            "ts": "2026-05-21T11:01:02+00:00",
            "event": "mt5_position_snapshot",
        }
        assert row["order_lifecycle"][-1]["ev"] == "mt5_position_snapshot"
        assert hist["sl_history"][0]["status"] == "snapshot"
        assert hist["sl_history"][0]["sl"] == 4525.0
        assert hist["tp_history"][0]["tp"] == 4548.0

    def test_failed_modify_keeps_legacy_last_retcode_in_level_history(
            self, tmp_path):
        path = tmp_path / "events.jsonl"
        rows = [
            {"ts": "2026-05-21T11:00:00+00:00", "sig": "canal2_12747",
             "ev": "signal_received", "direction": "BUY"},
            {"ts": "2026-05-21T11:01:00+00:00", "sig": "canal2_12747",
             "ev": "mt5_modify_requested", "ticket": 111,
             "new_sl": 4525.0, "label": "BE #111"},
            {"ts": "2026-05-21T11:01:02+00:00", "sig": "canal2_12747",
             "ev": "mt5_action_failed", "ticket": 111,
             "new_sl": 4525.0, "last_retcode": 10016,
             "label": "BE #111"},
        ]
        path.write_text("\n".join(json.dumps(r) for r in rows),
                        encoding="utf-8")

        idx = reconcile.load_journal_index(path)
        hist = idx["canal2_12747"]["ticket_level_history"]["111"]

        assert hist["sl_history"][0]["status"] == "requested"
        assert hist["sl_history"][1]["status"] == "failed"
        assert hist["sl_history"][1]["retcode"] == 10016
