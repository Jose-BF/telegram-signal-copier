from copy import deepcopy
import hashlib

import numpy as np
import pytest

from research.dubai_signal_anatomy import (
    associate, barrier_reasons, completeness, geometry, message_kind, path_anatomy,
    range_anatomy, run_anatomy, summarize,
)
from research.strategy_study import _sha


BASE = 1780660800000  # 2026-06-05T12:00:00Z


def stamp(seconds):
    from research.dubai_annual_coverage import iso_msc
    return iso_msc(BASE + int(seconds * 1000))


def receipt(mid, seconds, text="", sticker=None, reply=None, edit=None):
    return {"ev": "telegram_raw", "channel": "canal1", "message_id": mid,
        "ts": stamp(seconds), "date_utc": stamp(seconds if edit is None else 1),
        "edit_date_utc": stamp(edit) if edit is not None else None,
        "update_kind": "edit" if edit is not None else "new",
        "is_edit": edit is not None, "is_reply": reply is not None, "reply_to_msg_id": reply,
        "has_text": bool(text), "text": text, "text_len": len(text),
        "text_sha1": hashlib.sha1(text.encode()).hexdigest() if text else None,
        "has_media": sticker is not None, "sticker_id": sticker}


def entry(row, line, direction="BUY", supported=True):
    return {"signal_id": f"canal1_{row['message_id']}", "message_id": row["message_id"],
        "direction": direction, "received_utc": row["ts"], "first_source_line_1based": line,
        "first_source_row_sha256": _sha(row), "initial_receipt_supported": supported}


BUY = "BUY GOLD NOW 4000-4004\nTP1 4008\nTP2 4012\nSL 3990"
SELL = "SELL GOLD NOW 4000-4004\nTP1 3995\nTP2 3990\nSL 4014"


def tape(seconds, bid, spread=.2):
    return (np.array([BASE + int(s * 1000) for s in seconds], dtype=np.int64) * 1_000_000,
            np.array(bid, dtype=float), np.array(bid, dtype=float) + spread)


def priced(text=BUY, seconds=0):
    kind, parsed = message_kind(text)
    assert kind == "operational_range"
    return {"message_id": 2, "received_utc": stamp(seconds), "parsed": parsed,
            "geometry_reasons": geometry(parsed), "barrier_reasons": barrier_reasons(parsed)}


def test_commentary_range_is_not_an_operational_order():
    kind, _ = message_kind("XAUUSD bearish outlook. Resistance 4040-4090. Wait for confirmation before SELL.")
    assert kind != "operational_range"
    assert message_kind(BUY)[0] == "operational_range"
    assert message_kind(SELL)[0] == "operational_range"


def test_abbreviated_reversed_range_and_invalid_targets_are_retained():
    msg = priced("SELL GOLD NOW 4315-12\nTP1 4310\nTP2 4305\nSL 4325")
    assert tuple(msg["parsed"]["range"]) == (4312, 4315)
    assert msg["geometry_reasons"] == []
    bad = priced("SELL GOLD NOW 4302-4305\nTP1 4398\nTP2 4394\nSL 4320")
    assert "targets_not_beyond_near_edge" in bad["geometry_reasons"]


def test_explicit_reply_wins_over_newer_sticker_and_edits_preserve_first_levels():
    a, b = receipt(1, 0, sticker=6255969549976339155), receipt(3, 3, sticker=6256057072819896994)
    lines = [(1, a), (2, receipt(2, 1, BUY)), (3, b),
             (4, receipt(4, 4, "Move SL to 3998", reply=2)),
             (5, receipt(2, 5, BUY.replace("4008", "4009"), edit=5))]
    roots, messages, _ = associate(lines, [entry(a, 1), entry(b, 3, "SELL")])
    assert messages[-2]["signal_id"] == "canal1_1"
    assert messages[-2]["association"] == "explicit_reply"
    assert roots[0]["first_range"]["parsed"]["tps"][0] == 4008
    assert roots[0]["range_revision_count"] == 2
    assert roots[0]["messages"][-1]["received_utc"] == stamp(5).replace("Z", "+00:00")


@pytest.mark.parametrize("unsupported", [True, False])
def test_unknown_or_unsupported_sticker_breaks_inferred_link(unsupported):
    a = receipt(1, 0, sticker=6255969549976339155)
    b = receipt(3, 3, sticker=6255969549976339155 if unsupported else 999)
    entries = [entry(a, 1)] + ([entry(b, 2, supported=False)] if unsupported else [])
    roots, messages, _ = associate([(1, a), (2, b), (3, receipt(4, 4, BUY))], entries)
    assert messages[0]["signal_id"] is None
    assert roots[0]["first_range"] is None


def test_unresolved_reply_does_not_fall_back_and_identical_new_ids_are_not_deduped():
    a = receipt(1, 0, sticker=6255969549976339155)
    lines = [(1, a), (2, receipt(2, 1, BUY, reply=999)), (3, receipt(3, 2, BUY)), (4, receipt(4, 3, BUY))]
    roots, messages, _ = associate(lines, [entry(a, 1)])
    assert messages[0]["association"] == "unresolved_reply"
    assert messages[0]["signal_id"] is None
    assert roots[0]["distinct_range_messages"] == 2


def test_future_publication_is_rejected_not_used_at_earlier_receipt():
    a = receipt(1, 0, sticker=6255969549976339155)
    b = receipt(2, 1, BUY)
    b["date_utc"] = stamp(2)
    _, messages, rejected = associate([(1, a), (2, b)], [entry(a, 1)])
    assert messages == []
    assert rejected[0]["reasons"] == ["publication_after_receipt"]


def test_trigger_source_hash_is_checked():
    a = receipt(1, 0, sticker=6255969549976339155)
    frozen = entry(a, 1)
    frozen["first_source_row_sha256"] = "incorrect"
    with pytest.raises(ValueError, match="inventory trigger"):
        associate([(1, a)], [frozen])


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_executable_sides_and_recovery_order_are_symmetric(direction):
    values = [100, 98, 100.4, 103, 94, 101] if direction == "BUY" else [100, 102, 99.6, 97, 106, 99]
    result = path_anatomy(tape(range(6), values), BASE, BASE + 5000, direction, 5000)
    assert result["coverage"]["complete"]
    assert result["initial_spread"] == pytest.approx(.2)
    assert result["barriers"]["2"]["order"] == "adverse_first"
    assert result["recoveries"]["2"]["recovery"]["seconds"] == 2
    assert result["recoveries"]["5"]["recovery"]["seconds"] == 5
    assert result["adverse_excursion"] == pytest.approx(6.2)


def test_partial_tape_reports_observed_hits_without_claiming_complete():
    result = path_anatomy(tape([0, 100], [100, 110]), BASE, BASE + 100_000, "BUY", 5000)
    assert not result["coverage"]["complete"]
    assert result["coverage"]["reasons"] == ["internal_gap"]
    assert result["barriers"]["5"]["favorable"] is not None
    empty = path_anatomy(tape([], []), BASE, BASE + 1000, "SELL", 5000)
    assert empty["coverage"]["reasons"] == ["no_quotes"]


def test_range_cannot_be_visited_before_message_arrives():
    result = range_anatomy(tape([0, 1, 2, 3], [4002, 4005, 4006, 4008]), priced(seconds=1), BASE + 3000, 5000)
    assert result["first_zone"] is None
    assert result["first_tp1"]["seconds"] == 2
    assert result["position_at_availability"] == "favorable_outside"
    assert not result["zone_before_barrier"]


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_range_penetration_and_stop_are_not_erased_by_later_target(direction):
    if direction == "BUY":
        quotes, text = [4005, 4003, 4001, 3999.8, 3989, 4009], BUY
    else:
        quotes, text = [3998, 4001, 4003, 4004, 4014, 3990], SELL
    result = range_anatomy(tape(range(6), quotes), priced(text), BASE + 5000, 5000)
    assert result["zone_before_barrier"]
    assert result["barrier_order"] == "adverse_first"
    assert result["penetration"]["midpoint"]["before_barrier"]
    assert result["penetration"]["far_edge"]["before_barrier"]
    assert not result["penetration"]["far_edge"]["already_at_first_zone_quote"]


def test_range_jump_does_not_create_synthetic_touch():
    result = range_anatomy(tape([0, 1, 2], [4005, 3998, 3989]), priced(), BASE + 2000, 5000)
    assert result["first_zone"] is None
    assert result["barrier_order"] == "adverse_first"


def test_invalid_or_late_range_is_not_a_trade():
    invalid = deepcopy(priced())
    invalid["geometry_reasons"] = ["inconsistent"]
    invalid["barrier_reasons"] = ["inconsistent"]
    assert range_anatomy(tape([0], [4002]), invalid, BASE + 1000, 5000)["status"] == "invalid_levels"
    assert range_anatomy(tape([0], [4002]), priced(seconds=2), BASE + 1000, 5000)["status"] == "range_after_cutoff"


def test_boundary_gaps_remain_separate_from_internal_gaps():
    times = tape([10, 11], [100, 100])[0]
    result = completeness(times, BASE, BASE + 20_000, 5000)
    assert result["reasons"] == ["initial_gap", "final_gap"]


def test_summary_keeps_unsupported_and_partial_out_of_complete_rates():
    a = receipt(1, 0, sticker=6255969549976339155)
    roots, messages, rejected = associate([(1, a)], [entry(a, 1)])
    roots[0].update(paths={"300": path_anatomy(tape([0, 300], [4000, 4010]), BASE, BASE + 300_000, "BUY", 5000)}, ranges={}, single_prices={})
    result = summarize(roots, messages, rejected)["groups"]["all"]
    assert result["identities"] == 1
    assert result["paths"]["300"]["complete"] == 0
    assert result["paths"]["300"]["partial_or_empty"] == 1
    assert result["paths"]["300"]["favorable_excursion"] == {"n": 0}


def test_existing_archive_is_never_overwritten(tmp_path):
    with pytest.raises(ValueError, match="immutable"):
        run_anatomy(tmp_path, tmp_path, tmp_path, tmp_path)


def test_single_price_is_not_a_missing_range_or_replaced_by_later_range():
    a = receipt(1, 0, sticker=6255969549976339155)
    text = BUY.replace("4000-4004", "4002")
    roots, _, rejected = associate([(1, a), (2, receipt(2, 1, text)),
        (3, receipt(2, 3, BUY, edit=3))], [entry(a, 1)])
    assert not rejected
    initial = roots[0]["first_priced"]
    assert initial["kind"] == "operational_price"
    assert initial["parsed"]["entry_price"] == 4002
    assert initial["parsed"].get("range") is None
    observed = range_anatomy(tape([1, 2, 3], [4003, 4001.5, 4009]), initial, BASE + 3000, 5000)
    assert observed["entry_type"] == "single_price"
    assert observed["opportunity_rule"] == "quote_at_or_better_than_published_price"
    assert observed["first_zone"]["seconds"] == 1
    assert observed["penetration"] == {}


def test_later_target_error_does_not_block_tp1_observation():
    msg = priced(BUY.replace("4012", "3900"))
    assert msg["geometry_reasons"]
    assert msg["barrier_reasons"] == []
    result = range_anatomy(tape([0, 1], [4002, 4009]), msg, BASE + 1000, 5000)
    assert result["barrier_order"] == "favorable_first"


def test_tp1_inside_range_is_flagged_not_discarded():
    msg = priced(SELL.replace("3995", "4001"))
    assert msg["geometry_reasons"] == ["targets_not_beyond_near_edge"]
    assert msg["barrier_reasons"] == []
    result = range_anatomy(tape([0, 1], [4003, 4000]), msg, BASE + 1000, 5000)
    assert result["zone_before_barrier"]
    assert result["barrier_order"] == "favorable_first"
