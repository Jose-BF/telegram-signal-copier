"""
test_state.py — Suite de regresión para state.py.

Cubre:
  - Signal.tp_for_position (escalonado, target_tp_index, max_tp_index cap)
  - Signal.be_trigger_price
  - Signal.effective_lot (con multiplier)
  - Signal.all_filled_tickets
  - Signal.magic (por canal)
  - StateManager.add / get / latest_open / close
  - StateManager.alias (mismo signal bajo dos message_ids)

NO toca build_context (depende de MT5 — se cubrirá con mocks en futura
fase si es necesario, hoy no es crítico).
"""

import pytest
from datetime import datetime, timedelta

from state import Signal, StateManager
import config


# ─── Signal.tp_for_position ─────────────────────────────────────────────────

class TestTpForPosition:
    """Calcula el TP de cada posición según el modo (escalonado vs target_tp_index)."""

    def test_escalonado_market_to_tp1(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006, 2008, 2010])
        # market (idx 0) → tps[0] = 2002
        assert sig.tp_for_position(0) == 2002

    def test_escalonado_dca1_to_tp2(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006, 2008, 2010])
        assert sig.tp_for_position(1) == 2004

    def test_escalonado_all_positions(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006, 2008, 2010])
        for i in range(5):
            assert sig.tp_for_position(i) == sig.tps[i]

    def test_escalonado_overflow_uses_last_tp(self):
        # Posición i=5 con solo 5 tps → usa el último (índice 4)
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006, 2008, 2010])
        assert sig.tp_for_position(5) == 2010
        assert sig.tp_for_position(10) == 2010

    def test_no_tps_returns_none(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY", tps=[])
        assert sig.tp_for_position(0) is None

    def test_target_tp_index_uniform(self):
        # Si target_tp_index=3 (TP4), TODAS las posiciones van a tps[3]=2008
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006, 2008, 2010],
                     target_tp_index=3)
        for i in range(5):
            assert sig.tp_for_position(i) == 2008

    def test_target_tp_index_overflow_caps(self):
        # target_tp_index=99 → cap al último TP disponible
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006],
                     target_tp_index=99)
        for i in range(5):
            assert sig.tp_for_position(i) == 2006

    def test_max_tp_index_caps_escalonado(self):
        # max_tp_index=2 → posiciones >=2 todas van a tps[2]=2006
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006, 2008, 2010],
                     max_tp_index=2)
        assert sig.tp_for_position(0) == 2002  # idx 0 < 2 → tps[0]
        assert sig.tp_for_position(1) == 2004  # idx 1 < 2 → tps[1]
        assert sig.tp_for_position(2) == 2006  # idx 2 = max_tp_index
        assert sig.tp_for_position(3) == 2006  # idx 3 capped a 2
        assert sig.tp_for_position(4) == 2006  # idx 4 capped a 2

    def test_n_total_open_param_is_noop(self):
        # n_total_open ya no afecta el cálculo (param mantenido por compat).
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006, 2008, 2010])
        assert sig.tp_for_position(0, n_total_open=1) == 2002
        assert sig.tp_for_position(0, n_total_open=5) == 2002
        assert sig.tp_for_position(0, n_total_open=None) == 2002

    def test_explicit_open_runner_leaves_only_last_position_without_tp(self):
        sig = Signal(
            channel="canal2",
            message_id=2,
            direction="BUY",
            tps=[2002, 2004, 2006],
            has_open_runner=True,
        )

        assert sig.tp_for_position(3, n_total_open=5) == 2006
        assert sig.tp_for_position(4, n_total_open=5) is None

    def test_normal_overflow_still_uses_last_tp_without_open_instruction(self):
        sig = Signal(
            channel="canal2",
            message_id=3,
            direction="BUY",
            tps=[2002, 2004, 2006],
            has_open_runner=False,
        )

        assert sig.tp_for_position(4, n_total_open=5) == 2006


# ─── Signal.be_trigger_price ────────────────────────────────────────────────

class TestBeTriggerPrice:
    def test_be_at_tp_index_0_returns_tp1(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006],
                     be_at_tp_index=0)
        assert sig.be_trigger_price() == 2002

    def test_be_at_tp_index_2_returns_tp3(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006],
                     be_at_tp_index=2)
        assert sig.be_trigger_price() == 2006

    def test_be_at_tp_index_overflow_caps(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004],
                     be_at_tp_index=10)
        assert sig.be_trigger_price() == 2004

    def test_no_be_returns_none(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004])
        # be_at_tp_index = None default
        assert sig.be_trigger_price() is None

    def test_no_tps_returns_none(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     be_at_tp_index=0)
        assert sig.be_trigger_price() is None


# ─── Signal.effective_lot ───────────────────────────────────────────────────

class TestEffectiveLot:
    def test_default_multiplier(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY")
        # config.LOT_SIZE × 1.0
        assert sig.effective_lot == max(0.01, round(config.LOT_SIZE * 1.0, 2))

    def test_high_risk_half_lot(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     lot_multiplier=0.5)
        expected = max(0.01, round(config.LOT_SIZE * 0.5, 2))
        assert sig.effective_lot == expected

    def test_min_lot_cap(self):
        # Mult muy bajo nunca cae bajo 0.01
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     lot_multiplier=0.0)
        assert sig.effective_lot == 0.01

    def test_double_lot(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     lot_multiplier=2.0)
        expected = max(0.01, round(config.LOT_SIZE * 2.0, 2))
        assert sig.effective_lot == expected


# ─── Signal.all_filled_tickets ──────────────────────────────────────────────

class TestAllFilledTickets:
    def test_only_market(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     market_ticket=100)
        assert sig.all_filled_tickets == [100]

    def test_market_and_dcas(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     market_ticket=100,
                     dca_tickets=[101, 102, 103])
        assert sig.all_filled_tickets == [100, 101, 102, 103]

    def test_no_market_only_dcas(self):
        # Caso raro: market falló pero DCAs sí (no debería pasar pero defensive)
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     dca_tickets=[101, 102])
        assert sig.all_filled_tickets == [101, 102]

    def test_no_tickets(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY")
        assert sig.all_filled_tickets == []

    # ─── Double Market: extra_market_tickets en all_filled_tickets ─────
    def test_double_market_includes_extra(self):
        """Cuando hay un Market B (double_market), all_filled_tickets lo
        incluye despues del market_ticket y antes de las DCAs."""
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     market_ticket=100,
                     extra_market_tickets=[200],
                     dca_tickets=[101, 102])
        assert sig.all_filled_tickets == [100, 200, 101, 102]

    def test_double_market_only(self):
        """Market A + Market B sin DCAs aun rellenos."""
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     market_ticket=100,
                     extra_market_tickets=[200])
        assert sig.all_filled_tickets == [100, 200]

    def test_duplicate_tickets_are_deduplicated_preserving_order(self):
        """Una adopcion huerfana no debe duplicar PnL ni cierres."""
        sig = Signal(channel="canal1", message_id=20637, direction="SELL",
                     market_ticket=1518373761,
                     extra_market_tickets=[
                         1518373810,
                         1518373914,
                         1518373914,
                         1518374849,
                     ],
                     dca_tickets=[1518374849])

        assert sig.all_filled_tickets == [
            1518373761,
            1518373810,
            1518373914,
            1518374849,
        ]

    def test_double_market_with_tp_override(self):
        """Market B usa tp_overrides para asignar TP escalonado distinto.
        Aqui validamos que el override funciona como en rescue_market."""
        sig = Signal(channel="canal2", message_id=1, direction="BUY",
                     tps=[2002, 2004, 2006, 2008, 2010],
                     market_ticket=100,
                     extra_market_tickets=[200],
                     tp_overrides={200: 2})  # Market B → TP3 (idx=2)
        # Pos market (i=0): escalonado → tps[0] = 2002 (TP1)
        assert sig.tp_for_position(0) == 2002
        # tp_overrides actua a nivel de ticket en _apply_sl_tp del listener,
        # NO directamente en tp_for_position. Aqui validamos solo que el dict
        # esta correctamente almacenado.
        assert sig.tp_overrides[200] == 2

    def test_pending_correction_field_default_empty(self):
        """signal.pending_correction defaults to empty dict (no rejection active)."""
        sig = Signal(channel="canal2", message_id=1, direction="BUY")
        assert sig.pending_correction == {}

    def test_double_market_dca_skip_b_slot_idx(self):
        """Logica del calculo de override para DCAs cuando hay doble market.

        Replica `position_lifecycle_monitor` (lineas que asignan tp_override_idx):
        Sin doble market: DCA[0]=tps[1]=TP2, DCA[1]=tps[2]=TP3.
        Con doble market (B en TP3=idx2): DCA[0]=tps[1]=TP2, DCA[1]=tps[3]=TP4
        (saltamos el slot de B para evitar duplicado).
        """
        # Replica de la logica de position_lifecycle_monitor _compute_tp_idx (inline):
        def compute_dca_override(tps, position_index, b_idx, has_double_market):
            if not has_double_market or not tps:
                return None
            target_idx = position_index
            if target_idx >= b_idx:
                target_idx += 1
            return min(target_idx, len(tps) - 1)

        tps = [2002, 2004, 2006, 2008, 2010]
        b_idx = 2  # Market B en TP3

        # Sin doble market → None (escalonado natural)
        assert compute_dca_override(tps, 1, b_idx, False) is None

        # Con doble market:
        # DCA[0] (position_index=1): 1 < 2 → target=1 → tps[1]=TP2 ✓
        assert compute_dca_override(tps, 1, b_idx, True) == 1
        # DCA[1] (position_index=2): 2 >= 2 → target=3 → tps[3]=TP4 ✓
        assert compute_dca_override(tps, 2, b_idx, True) == 3
        # DCA[2] (position_index=3): 3 >= 2 → target=4 → tps[4]=TP5
        assert compute_dca_override(tps, 3, b_idx, True) == 4
        # DCA[3] (position_index=4): 4 >= 2 → target=5 → cap a tps[4]=TP5
        assert compute_dca_override(tps, 4, b_idx, True) == 4


# ─── Signal.magic ───────────────────────────────────────────────────────────

class TestMagic:
    def test_canal1_magic(self):
        sig = Signal(channel="canal1", message_id=1, direction="BUY")
        assert sig.magic == config.MT5_MAGIC_CANAL1

    def test_canal2_magic(self):
        sig = Signal(channel="canal2", message_id=1, direction="BUY")
        assert sig.magic == config.MT5_MAGIC_CANAL2

    def test_canal1_canal2_magics_distinct(self):
        # Crítico para que el bot no cruce posiciones entre canales
        assert config.MT5_MAGIC_CANAL1 != config.MT5_MAGIC_CANAL2


# ─── StateManager.add / get ─────────────────────────────────────────────────

class TestStateManagerBasic:
    def test_add_and_get(self, fresh_state):
        sig = Signal(channel="canal2", message_id=42, direction="BUY")
        fresh_state.add(sig)
        retrieved = fresh_state.get("canal2", 42)
        assert retrieved is sig  # mismo objeto

    def test_get_missing_returns_none(self, fresh_state):
        assert fresh_state.get("canal2", 99999) is None

    def test_get_wrong_channel_returns_none(self, fresh_state):
        sig = Signal(channel="canal2", message_id=42, direction="BUY")
        fresh_state.add(sig)
        # Mismo message_id pero canal distinto → no coincide
        assert fresh_state.get("canal1", 42) is None


# ─── StateManager.alias ─────────────────────────────────────────────────────

class TestStateManagerAlias:
    """Crítico para canal 1: el sticker (msg_id A) abre la posición, pero
    el texto posterior (msg_id B) y los mgmt msgs son reply a B, no a A.
    Sin alias, los mgmts de B no encuentran el signal."""

    def test_alias_makes_signal_findable_under_two_ids(self, fresh_state):
        sig = Signal(channel="canal1", message_id=100, direction="BUY")
        fresh_state.add(sig)
        fresh_state.alias(sig, 200)
        # Ambos message_ids devuelven el mismo objeto
        assert fresh_state.get("canal1", 100) is sig
        assert fresh_state.get("canal1", 200) is sig

    def test_alias_mutations_visible_under_both_ids(self, fresh_state):
        sig = Signal(channel="canal1", message_id=100, direction="BUY")
        fresh_state.add(sig)
        fresh_state.alias(sig, 200)
        # Mutación bajo un ID se ve bajo el otro (no hay copia)
        sig.market_ticket = 9999
        assert fresh_state.get("canal1", 100).market_ticket == 9999
        assert fresh_state.get("canal1", 200).market_ticket == 9999

    def test_close_via_alias(self, fresh_state):
        sig = Signal(channel="canal1", message_id=100, direction="BUY")
        fresh_state.add(sig)
        fresh_state.alias(sig, 200)
        fresh_state.close("canal1", 200)  # cerrar via alias
        # Ambos quedan cerrados (es el mismo objeto)
        assert fresh_state.get("canal1", 100).status == "closed"
        assert fresh_state.get("canal1", 200).status == "closed"


# ─── StateManager.latest_open ───────────────────────────────────────────────

class TestStateManagerLatestOpen:
    def test_no_signals_returns_none(self, fresh_state):
        assert fresh_state.latest_open("canal1") is None

    def test_returns_only_open(self, fresh_state):
        s1 = Signal(channel="canal2", message_id=1, direction="BUY",
                    timestamp=datetime(2026, 1, 1))
        s2 = Signal(channel="canal2", message_id=2, direction="SELL",
                    timestamp=datetime(2026, 1, 2))
        s2.status = "closed"
        fresh_state.add(s1)
        fresh_state.add(s2)
        # s2 cerrada → devuelve s1 aunque s2 sea más reciente
        assert fresh_state.latest_open("canal2") is s1

    def test_returns_most_recent_open(self, fresh_state):
        s1 = Signal(channel="canal2", message_id=1, direction="BUY",
                    timestamp=datetime(2026, 1, 1))
        s2 = Signal(channel="canal2", message_id=2, direction="SELL",
                    timestamp=datetime(2026, 1, 2))
        s3 = Signal(channel="canal2", message_id=3, direction="BUY",
                    timestamp=datetime(2026, 1, 3))
        fresh_state.add(s1)
        fresh_state.add(s2)
        fresh_state.add(s3)
        assert fresh_state.latest_open("canal2") is s3

    def test_only_returns_signals_of_specified_channel(self, fresh_state):
        s1 = Signal(channel="canal1", message_id=1, direction="BUY",
                    timestamp=datetime(2026, 1, 1))
        s2 = Signal(channel="canal2", message_id=2, direction="SELL",
                    timestamp=datetime(2026, 1, 2))
        fresh_state.add(s1)
        fresh_state.add(s2)
        # Pedir canal1 devuelve s1 aunque s2 sea más reciente
        assert fresh_state.latest_open("canal1") is s1
        assert fresh_state.latest_open("canal2") is s2


# ─── StateManager.close ─────────────────────────────────────────────────────

class TestStateManagerClose:
    def test_close_changes_status(self, fresh_state):
        sig = Signal(channel="canal2", message_id=42, direction="BUY")
        fresh_state.add(sig)
        assert sig.status == "open"
        fresh_state.close("canal2", 42)
        assert sig.status == "closed"

    def test_close_nonexistent_does_not_raise(self, fresh_state):
        # No-op silencioso
        fresh_state.close("canal2", 99999)

    def test_close_does_not_remove_from_dict(self, fresh_state):
        # close marca como closed pero no quita del dict
        # (queda en memoria para histórico hasta proceso muere)
        sig = Signal(channel="canal2", message_id=42, direction="BUY")
        fresh_state.add(sig)
        fresh_state.close("canal2", 42)
        assert fresh_state.get("canal2", 42) is sig


# ─── StateManager.open_signals ──────────────────────────────────────────────

class TestStateManagerOpenSignals:
    """open_signals: señales abiertas de un canal, DEDUPLICADAS.

    Crítico: un Signal con alias (canal 1 lo registra bajo el sticker Y el
    texto) aparece bajo 2 keys en _signals. Sin deduplicar por identidad de
    objeto se contaría 2 veces y el listener creería que hay 2 señales
    abiertas → marcaría como 'ambiguo' un destino que en realidad es único.
    """

    def test_empty_returns_empty_list(self, fresh_state):
        assert fresh_state.open_signals("canal1") == []

    def test_excludes_closed(self, fresh_state):
        s1 = Signal(channel="canal1", message_id=1, direction="BUY",
                    timestamp=datetime(2026, 1, 1))
        s2 = Signal(channel="canal1", message_id=2, direction="SELL",
                    timestamp=datetime(2026, 1, 2))
        s2.status = "closed"
        fresh_state.add(s1)
        fresh_state.add(s2)
        assert fresh_state.open_signals("canal1") == [s1]

    def test_filters_by_channel(self, fresh_state):
        s1 = Signal(channel="canal1", message_id=1, direction="BUY")
        s2 = Signal(channel="canal2", message_id=2, direction="BUY")
        fresh_state.add(s1)
        fresh_state.add(s2)
        assert fresh_state.open_signals("canal1") == [s1]
        assert fresh_state.open_signals("canal2") == [s2]

    def test_aliased_signal_counted_once(self, fresh_state):
        """REGRESION: canal 1 registra el Signal bajo sticker Y texto. Sin
        dedup, open_signals lo devolvería 2 veces y el listener trataría un
        destino único como 'ambiguo'."""
        sig = Signal(channel="canal1", message_id=100, direction="BUY")
        fresh_state.add(sig)
        fresh_state.alias(sig, 200)          # mismo objeto, 2 message_ids
        result = fresh_state.open_signals("canal1")
        assert result == [sig]               # UNA sola vez
        assert len(result) == 1

    def test_two_distinct_signals_sorted_recent_first(self, fresh_state):
        s1 = Signal(channel="canal1", message_id=1, direction="BUY",
                    timestamp=datetime(2026, 1, 1))
        s2 = Signal(channel="canal1", message_id=2, direction="SELL",
                    timestamp=datetime(2026, 1, 2))
        fresh_state.add(s1)
        fresh_state.add(s2)
        result = fresh_state.open_signals("canal1")
        assert len(result) == 2
        assert result[0] is s2               # más reciente primero
        assert result[1] is s1
def test_causal_source_fields_do_not_shift_legacy_positional_arguments():
    timestamp = datetime(2026, 7, 26, 10, 0, 0)

    signal = Signal(
        "canal1",
        20700,
        "BUY",
        timestamp,
        4300.0,
        4302.0,
        [4305.0, 4308.0],
        4290.0,
    )

    assert signal.timestamp == timestamp
    assert signal.range_low == 4300.0
    assert signal.range_high == 4302.0
    assert signal.tps == [4305.0, 4308.0]
    assert signal.sl == 4290.0
    assert signal.source_message_revision_id is None
    assert signal.source_decision_id is None


def test_zone_entry_evidence_has_safe_defaults():
    signal = Signal("canal2", 800, "BUY")

    assert signal.entry_source_kind == "telegram_now"
    assert signal.zone_plan_message_id is None
    assert signal.zone_trigger_kind is None
    assert signal.zone_trigger_price is None
    assert signal.zone_trigger_time_msc is None
    assert signal.zone_entry_generation == 0
