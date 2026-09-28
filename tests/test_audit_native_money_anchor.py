from decimal import Decimal

import numpy as np
import pytest

from tools.audit_native_money_anchor import cents, money_rows


CONTRACT = {"account": {"currency_digits": 2}, "instrument": {"contract_size": 100},
            "conversion": {"max_quote_age_ms": 5000, "orientation": "account_base_profit_quote"}}


def position(position_id, entry_msc, exit_msc, native_profit, *, volume=.1):
    entry = {"ticket": position_id * 2, "position_id": position_id, "symbol": "XAUUSD",
             "entry": 0, "type": 0, "time_msc": entry_msc, "volume": volume, "price": 100,
             "profit": 0., "commission": 0., "swap": 0., "fee": 0.}
    close = {**entry, "ticket": position_id * 2 + 1, "entry": 1, "type": 1,
             "time_msc": exit_msc, "price": 101, "profit": native_profit}
    reconciliation = {"position_id": position_id, "signal_id": f"canal2_{position_id}",
                      "deal_tickets": [entry["ticket"], close["ticket"]],
                      "net_eur": str(cents(native_profit))}
    return [entry, close], reconciliation


def test_native_money_rows_keep_exact_mismatch_stale_and_missing_fx():
    cases = [position(1, 900, 1010, 9.09), position(2, 900, 1020, 9.08),
             position(3, 6900, 7001, 9.09), position(4, 100, 500, 9.09)]
    native = {"deals": [deal for deals, _ in cases for deal in deals]}
    reconciled = {"positions": [row for _, row in cases]}
    rows = money_rows(native, reconciled, np.array([1000], dtype=np.int64),
                      np.array([1.09]), np.array([1.1]), contract=CONTRACT)
    assert [row["status"] for row in rows] == [
        "exact_exit_money", "exit_money_mismatch", "blocked_stale_fx", "blocked_missing_prior_fx"]
    assert rows[1]["delta_eur"] == "0.01"
    assert rows[2]["fx_age_ms"] == 6001
    assert "predicted_profit_eur" not in rows[2]
    assert len(rows) == 4


def test_reconciliation_and_cent_precision_are_not_silently_relaxed():
    deals, row = position(1, 900, 1010, 9.09)
    row["net_eur"] = "9.08"
    with pytest.raises(ValueError, match="differs from reconciliation"):
        money_rows({"deals": deals}, {"positions": [row]}, np.array([1000]),
                   np.array([1.09]), np.array([1.1]), contract=CONTRACT)
    assert cents(9.090000000000002) == Decimal("9.09")
    with pytest.raises(ValueError, match="cent precision"):
        cents("9.091")


def test_retrospective_fx_bracket_is_explicit_and_not_causal():
    deals, row = position(1, 900, 7001, 9.09)
    contract = {**CONTRACT, "conversion": {**CONTRACT["conversion"], "max_quote_interval_ms": 20_000}}
    result = money_rows({"deals": deals}, {"positions": [row]},
                        np.array([1000, 10_000], dtype=np.int64),
                        np.array([1.09, 1.09]), np.array([1.1, 1.1]), contract=contract)[0]
    assert result["status"] == "exact_exit_money"
    assert result["fx_freshness"] == "retrospectively_bracketed"
    assert result["within_strict_causal_age"] is False
    assert result["fx_interval_ms"] == 9000
    contract["conversion"]["max_quote_interval_ms"] = 8000
    blocked = money_rows({"deals": deals}, {"positions": [row]},
                         np.array([1000, 10_000], dtype=np.int64),
                         np.array([1.09, 1.09]), np.array([1.1, 1.1]), contract=contract)[0]
    assert blocked["status"] == "blocked_stale_fx"
