from copy import deepcopy

import pytest

from gold_555_live_candidate import CANDIDATE_FINGERPRINT
from tools.audit_gold_broker_comment_legs import bind_signal


def evidence(direction="BUY"):
    base_ms = 1_790_000_000_000
    prices = (4381.75, 4380.15)
    positions = []
    deals = []
    fills = []
    pairs = []
    for index, (pid, price) in enumerate(zip((101, 102), prices)):
        entry_ms = base_ms + index * 1000
        shadow_ms = entry_ms - 10_800_000 - 50
        positions.append({"signal_id": "canal2_3148", "position_id": pid,
                          "entry_msc": entry_ms, "entry_price": price,
                          "volume": 0.04, "deal_tickets": [pid + 1000]})
        deals.append({"position_id": pid, "comment": "c2_3148_g55" if index == 0
                      else f"c2_3148_B{index}_g55", "entry": 0,
                      "symbol": "XAUUSD", "magic": 20260422, "order": pid,
                      "ticket": pid + 1000, "time_msc": entry_ms,
                      "type": 0 if direction == "BUY" else 1,
                      "volume": 0.04, "price": price})
        fills.append({"ev": "strategy_shadow_transition", "transition": "virtual_fill",
                      "transition_tick_msc": shadow_ms,
                      "transition_details": {"leg_index": index, "volume": 0.04,
                                             "entry_price": price - 0.1}})
        pairs.append({"leg_index": index, "native_position_id": pid,
                      "volume": "0.04", "native_entry_price": str(price),
                      "shadow_entry_price": str(price - 0.1),
                      "native_minus_shadow_ms": 50})
    comparison = {"signal_id": "canal2_3148", "channel": "canal2",
                  "control_candidate": "gold_now_555_v1",
                  "entry_pairing": {"status": "diagnostic_ordinal_pairing", "pairs": pairs}}
    states = fills + [{"state": {"signal_id": "canal2_3148",
                                 "candidate_id": "gold_now_555_v1",
                                 "strategy_fingerprint": CANDIDATE_FINGERPRINT,
                                 "direction": direction}}]
    return comparison, positions, deals, states


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_broker_comments_bind_all_legs_without_upgrading_live_request_chain(direction):
    result = bind_signal(*evidence(direction))
    assert result["leg_count"] == 2
    assert [row["broker_comment"] for row in result["legs"]] == [
        "c2_3148_g55", "c2_3148_B1_g55"]
    assert result["native_leg_identity_verified"] is True
    assert result["live_order_request_chain_verified"] is False


@pytest.mark.parametrize("mutation", [
    lambda e: e[2][1].update(comment="c2_3148_B2_g55"),
    lambda e: e[2][1].update(price=4380.16),
    lambda e: e[2][1].update(time_msc=e[2][1]["time_msc"] + 1),
    lambda e: e[2][1].update(type=1),
    lambda e: e[3][1]["transition_details"].update(leg_index=0),
    lambda e: e[3][1].update(transition_tick_msc=e[3][1]["transition_tick_msc"] + 1),
])
def test_broker_or_shadow_contradiction_rejected(mutation):
    data = list(evidence())
    mutation(data)
    with pytest.raises(ValueError):
        bind_signal(*data)


def test_missing_native_leg_and_changed_strategy_rejected():
    comparison, positions, deals, states = evidence()
    with pytest.raises(ValueError, match="identity incomplete"):
        bind_signal(comparison, positions, deals[:-1], states)
    changed = deepcopy(states)
    changed[-1]["state"]["strategy_fingerprint"] = "other"
    with pytest.raises(ValueError, match="strategy identity"):
        bind_signal(comparison, positions, deals, changed)
