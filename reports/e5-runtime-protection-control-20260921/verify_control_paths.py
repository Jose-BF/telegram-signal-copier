"""Check frozen synthetic controls against explicit quote-by-quote expectations."""

from decimal import Decimal
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(name, trace):
    path = trace["input"]["path"]
    times = path["times_ns"]
    assert trace["native_broker_verified"] is False
    assert trace["complete_strategy_admitted"] is False
    assert trace["full_live_parity_verified"] is False
    assert all(a["quote_index"] <= b["quote_index"]
               for a, b in zip(trace["backend_calls"], trace["backend_calls"][1:]))
    for call in trace["backend_calls"]:
        assert call["time_ns"] == times[call["quote_index"]]
    settled = {}
    for row in trace["risk"]:
        assert row["time_ns"] == times[row["tick_index"]]
        if row["phase"] == "settled":
            settled[row["tick_index"]] = row
    indexes = list(settled)
    assert indexes == list(range(len(indexes)))
    assert indexes[-1] == trace["broker"]["quote_index"]
    grid = [(index, row["realized_minor"], row["floating_minor"],
             str(sum((Decimal(str(p[1])) for p in row["positions"]), Decimal(0))))
            for index, row in settled.items()]
    expected = None
    if name.startswith("test_real_gold_additional_leg_"):
        expected = [(0, 0, -80, "0.04"), (1, 0, -940, "0.07"),
                    (2, 0, -940, "0.07"), (3, -24040, 0, "0")]
        second = 98. if path["direction"] == "BUY" else 102.
        positions = [["sim_1", .04, 100.], ["sim_ladder_2", .03, second]]
        assert settled[0]["positions"] == positions[:1]
        assert settled[1]["positions"] == settled[2]["positions"] == positions
        assert settled[3]["positions"] == []
    elif name.startswith("test_identical_final_loss_does"):
        middle = -80 if path["ask"][1] == 100. else 1920
        expected = [(0, 0, -80, "0.04"), (1, 0, middle, "0.04"), (2, -8080, 0, "0")]
    elif name.startswith("test_canonical_gold_trailing_c"):
        expected = [(0, 0, -80, "0.04"), (1, 0, 80, "0.04"),
                    (2, 0, -80, "0.04"), (3, -11960, 0, "0")]
    elif name.startswith(("test_real_queue_worker_and_gol", "test_native_exit_precedes_held",
                          "test_native_close_between_real")):
        expected = [(0, 0, -80, "0.04"), (1, 0, -80, "0.04"),
                    (2, 0, -80, "0.04"), (3, 200, 0, "0")]
        if not name.startswith("test_real_queue_worker_and_gol"):
            # These scripts advance once more after releasing a held response.
            expected.append((4, 200, 0, "0"))
    elif name.startswith("test_real_worker_defers_invali"):
        expected = [(0, 0, -80, "0.04"), (1, 0, 320, "0.04"),
                    (2, 0, 720, "0.04"), (3, 4000, 0, "0")]
    elif name.startswith(("test_changed_revision_before_c", "test_new_revision_survives_old",
                          "test_unknown_response_keeps_in")):
        expected = [(0, 0, -80, "0.04"), (1, 0, -80, "0.04")]
    elif name.startswith(("test_real_worker_rejects_posit", "test_spool_failure_prevents_na")):
        expected = [(0, 0, -80, "0.04")]
    assert expected is not None, name
    assert grid == expected, (name, grid, expected)
    completed = trace["result"] is not None
    if completed:
        assert not trace["result"]["blockers"] and not trace["broker"]["positions"]
        assert trace["result"]["pnl_eur"] is not None
        assert len(trace["result"]["entries"]) == len(trace["result"]["exits"]) > 0
    peak = drawdown = 0
    for _, realized, floating, _ in grid:
        equity = realized + floating
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"post_event_grid": grid, "max_drawdown_minor": drawdown,
            "grid_matches_explicit_expectation": True,
            "completed_without_blockers_and_flat": completed,
            "final_pnl": trace["result"]["pnl_eur"] if trace["result"] else None}


if __name__ == "__main__":
    manifest = json.loads((ROOT / "control-manifest.json").read_text())
    results, negative_controls = {}, []
    for name, expected_hash in manifest["controls"].items():
        source = ROOT / "controls" / name
        assert digest(source) == expected_hash
        trace = json.loads(source.read_text())
        results[name] = check(name, trace)
        for mutation in ("missing_quote", "wrong_floating", "wrong_clock"):
            broken = deepcopy(trace)
            if mutation == "missing_quote":
                index = results[name]["post_event_grid"][-1][0]
                broken["risk"] = [row for row in broken["risk"]
                                  if not (row["phase"] == "settled" and row["tick_index"] == index)]
            elif mutation == "wrong_floating":
                for row in broken["risk"]:
                    if row["phase"] == "settled" and row["tick_index"] == 0:
                        row["floating_minor"] += 1
            else:
                broken["backend_calls"][0]["time_ns"] += 1
            try:
                check(name, broken)
            except (AssertionError, IndexError):
                negative_controls.append({"case": name, "mutation": mutation, "rejected": True})
            else:
                raise AssertionError(f"undetected path mutation: {name}/{mutation}")
    payload = {"contract": "explicit_synthetic_post_event_grid_control_v1",
               "verifier_sha256": digest(Path(__file__)),
               "manifest_sha256": digest(ROOT / "control-manifest.json"),
               "cases": results, "negative_controls": negative_controls, "native_broker_verified": False,
               "historical_path_parity_verified": False}
    with (ROOT / "path-verification-v3.json").open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"verified_controls": len(results), "rejected_mutations": len(negative_controls),
                      "native_broker_verified": False}))
