"""
test_mt5_errors.py — Suite de regresión para mt5_errors.classify().

Cubre TODOS los retcodes MT5 conocidos por el clasificador. Esto es
crítico porque pending_actions usa la categoría devuelta para decidir
RETRY / DROP / DONE, y una clasificación errónea puede causar:
  - Reintentos infinitos (categoría TRANSIENT/STOPS para algo permanente)
  - Pérdida de oportunidad (categoría DROP para algo temporal)
  - Bloqueo del event loop (visto en sesión 2026-05-06 con retcode 10016
    sin cap → ya fixeado, pero el clasificador es el primer filtro)

NO cubre adjust_sl_to_legal ni get_stops_level_pts (requieren MT5 mock).
"""

import pytest

import MetaTrader5 as mt5
from mt5_errors import classify, TRANSIENT, STOPS_RELATED, POSITION_GONE, PERMANENT


# ─── Categoría OK ───────────────────────────────────────────────────────────

class TestOk:
    def test_done_is_ok(self):
        # 10009 = TRADE_RETCODE_DONE
        assert classify(mt5.TRADE_RETCODE_DONE) == "OK"

    def test_no_changes_is_ok(self):
        # 10025 = TRADE_RETCODE_NO_CHANGES — la posición ya tenía esos
        # valores. NO es fallo, el objetivo está logrado. Tratar como OK.
        # Sin esto, modify_sltp idempotente generaba mt5_action_failed.
        assert classify(10025) == "OK"


# ─── Categoría STOPS ────────────────────────────────────────────────────────

class TestStops:
    """SL/TP inválidos respecto al precio actual. pending_actions reintenta
    tick-a-tick. Si lleva >30s atascado → DROP_STOPS_STRUCTURAL + notify."""

    @pytest.mark.parametrize("retcode,name", [
        (10016, "TRADE_RETCODE_INVALID_STOPS"),
        (10017, "TRADE_RETCODE_TRADE_DISABLED"),
        (10015, "TRADE_RETCODE_INVALID_PRICE"),
    ])
    def test_classified_as_stops(self, retcode, name):
        assert classify(retcode) == "STOPS", \
            f"esperado STOPS para {name} ({retcode})"

    def test_stops_set_membership(self):
        assert STOPS_RELATED == {10016, 10017, 10015}


# ─── Categoría TRANSIENT ────────────────────────────────────────────────────

class TestTransient:
    """Errores que justifican reintento inmediato (requote, mercado cerrado
    temporalmente, etc.)."""

    @pytest.mark.parametrize("retcode,name", [
        (10004, "TRADE_RETCODE_REQUOTE"),
        (10008, "TRADE_RETCODE_PLACED"),
        (10021, "TRADE_RETCODE_PRICE_OFF"),
        (10018, "TRADE_RETCODE_MARKET_CLOSED"),
        (10027, "TRADE_RETCODE_CLIENT_DISABLES_AT"),
    ])
    def test_classified_as_transient(self, retcode, name):
        assert classify(retcode) == "TRANSIENT", \
            f"esperado TRANSIENT para {name} ({retcode})"

    def test_transient_set_membership(self):
        assert TRANSIENT == {10004, 10008, 10021, 10018, 10027}


# ─── Categoría POSITION_GONE ────────────────────────────────────────────────

class TestPositionGone:
    """La posición ya no existe (cerrada por SL/TP/manual). Tratar como
    DONE en pending_actions — nada que hacer."""

    @pytest.mark.parametrize("retcode,name", [
        (10013, "TRADE_RETCODE_INVALID"),
        (10011, "TRADE_RETCODE_ERROR"),
        (10036, "TRADE_RETCODE_POSITION_CLOSED"),
    ])
    def test_classified_as_position_gone(self, retcode, name):
        assert classify(retcode) == "POSITION_GONE", \
            f"esperado POSITION_GONE para {name} ({retcode})"


# ─── Categoría PERMANENT ────────────────────────────────────────────────────

class TestPermanent:
    """Errores estructurales sin solución por reintento (sin margen, lote
    inválido, etc.). DROP inmediato."""

    @pytest.mark.parametrize("retcode,name", [
        (10019, "TRADE_RETCODE_NO_MONEY"),
        (10014, "TRADE_RETCODE_INVALID_VOLUME"),
        (10020, "TRADE_RETCODE_PRICE_CHANGED"),
        (10030, "TRADE_RETCODE_INVALID_FILL"),
    ])
    def test_classified_as_permanent(self, retcode, name):
        assert classify(retcode) == "PERMANENT", \
            f"esperado PERMANENT para {name} ({retcode})"


# ─── Categoría UNKNOWN ──────────────────────────────────────────────────────

class TestUnknown:
    """Retcodes no mapeados. pending_actions los trata como DROP (no reintenta)
    para evitar loops infinitos en errores no esperados."""

    def test_zero_is_unknown(self):
        assert classify(0) == "UNKNOWN"

    def test_negative_is_unknown(self):
        assert classify(-1) == "UNKNOWN"

    def test_large_unmapped_is_unknown(self):
        assert classify(99999) == "UNKNOWN"

    def test_close_unmapped_codes(self):
        # 10010, 10012, 10022, etc. no están mapeados (huecos en el rango)
        for rc in (10010, 10012, 10022, 10023, 10024, 10026):
            assert classify(rc) == "UNKNOWN", \
                f"retcode {rc} unexpectedly classified as {classify(rc)}"


# ─── Categorías son disjuntas ────────────────────────────────────────────

class TestCategoriesDisjoint:
    """Verifica que ningún retcode pertenece a dos categorías.
    Si dos sets se solapan, el resultado de classify() depende del orden
    de los if (silent bug)."""

    def test_no_overlap(self):
        all_sets = {
            "TRANSIENT": TRANSIENT,
            "STOPS_RELATED": STOPS_RELATED,
            "POSITION_GONE": POSITION_GONE,
            "PERMANENT": PERMANENT,
        }
        seen: dict[int, str] = {}
        for name, codes in all_sets.items():
            for rc in codes:
                assert rc not in seen, (
                    f"retcode {rc} en {name} y en {seen[rc]} — ambiguo"
                )
                seen[rc] = name

    def test_done_and_no_changes_not_in_any_set(self):
        # 10009 (DONE) y 10025 (NO_CHANGES) son OK por código duro,
        # NO deben aparecer en ninguno de los sets.
        for rc in (mt5.TRADE_RETCODE_DONE, 10025):
            assert rc not in TRANSIENT
            assert rc not in STOPS_RELATED
            assert rc not in POSITION_GONE
            assert rc not in PERMANENT


# ─── Comportamiento esperado por pending_actions ────────────────────────

class TestPendingActionsContract:
    """Resumen del contrato que pending_actions._try_once espera:

      OK            → DONE
      POSITION_GONE → DONE (éxito implícito)
      TRANSIENT     → RETRY
      STOPS         → RETRY si <30s, DROP_STOPS_STRUCTURAL si ≥30s
      PERMANENT     → DROP
      UNKNOWN       → DROP
    """

    def test_ok_actionable(self):
        assert classify(mt5.TRADE_RETCODE_DONE) == "OK"

    def test_position_gone_actionable(self):
        # POSITION_GONE → pending_actions trata como DONE (no reintenta)
        for rc in POSITION_GONE:
            assert classify(rc) == "POSITION_GONE"

    def test_transient_actionable(self):
        for rc in TRANSIENT:
            assert classify(rc) == "TRANSIENT"

    def test_stops_actionable(self):
        for rc in STOPS_RELATED:
            assert classify(rc) == "STOPS"

    def test_permanent_actionable(self):
        for rc in PERMANENT:
            assert classify(rc) == "PERMANENT"
