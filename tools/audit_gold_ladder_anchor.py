"""Explain verified Gold entry timing through first-fill ladder anchors.

The native request quote is a counterfactual input to the shadow threshold,
not evidence that the shadow processed that quote at the same wall-clock time.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gold_555_live_candidate import CANDIDATE_FINGERPRINT, Gold555Policy
from tools.audit_native_money_anchor import digest, read
from tools.verify_lifecycle_probe_projection import compare_projection


def _bound(report, path):
    hashes = [value for name, value in report.get("inputs_sha256", {}).items()
              if Path(name).resolve() == Path(path).resolve()]
    if hashes != [digest(path)]:
        raise ValueError(f"source hash binding missing or changed: {path}")


def analyze_ladder(comparison, events, *, policy=None):
    policy = Gold555Policy() if policy is None else policy
    pairing = comparison.get("entry_pairing", {})
    if (comparison.get("channel") != "canal2"
            or comparison.get("control_candidate") != "gold_now_555_v1"
            or pairing.get("status") != "verified_journal_entry_binding"
            or pairing.get("full_entry_identity_verified") is not True):
        raise ValueError("verified Gold live-control entry binding required")
    pairs = sorted(pairing["pairs"], key=lambda row: row["leg_index"])
    if not pairs or [row["leg_index"] for row in pairs] != list(range(len(pairs))):
        raise ValueError("verified legs must be contiguous from first entry")
    if len(pairs) > len(policy.entry_volumes):
        raise ValueError("verified leg count exceeds policy")
    results = {row["order"]: row for row in events
               if row.get("ev") == "mt5_order_result" and row.get("order") is not None}
    if len(results) != sum(row.get("ev") == "mt5_order_result" and row.get("order") is not None
                           for row in events):
        raise ValueError("duplicate native order result")
    requests = {row["action_id"]: row for row in events
                if row.get("ev") == "mt5_order_requested"}
    if len(requests) != sum(row.get("ev") == "mt5_order_requested" for row in events):
        raise ValueError("duplicate native order request")
    first_native = Decimal(pairs[0]["native_entry_price"])
    first_shadow = Decimal(pairs[0]["shadow_entry_price"])
    direction = None
    rows = []
    for pair in pairs:
        index = pair["leg_index"]
        result = results.get(pair["native_position_id"])
        if result is None or result.get("retcode") != 10009:
            raise ValueError("verified native leg lacks successful order result")
        request = requests.get(result.get("action_id"))
        if request is None or request.get("order_kind") != "market":
            raise ValueError("native result lacks bound market request")
        if (Decimal(str(result["price"])) != Decimal(pair["native_entry_price"])
                or Decimal(str(result["volume"])) != Decimal(pair["volume"])
                or Decimal(str(request["lot"])) != Decimal(pair["volume"])):
            raise ValueError("native request/result contradict verified fill")
        side = result["direction"]
        if side not in {"BUY", "SELL"} or request.get("direction") != side:
            raise ValueError("native order direction mismatch")
        if direction is None:
            direction = side
        elif direction != side:
            raise ValueError("mixed basket direction")
        quote = Decimal(str(request["requested_price"]))
        native_level = Decimal(str(policy.entry_levels(direction, float(first_native))[index]))
        shadow_level = Decimal(str(policy.entry_levels(direction, float(first_shadow))[index]))
        crossed_native = quote <= native_level if direction == "BUY" else quote >= native_level
        crossed_shadow = quote <= shadow_level if direction == "BUY" else quote >= shadow_level
        if index and not crossed_native:
            raise ValueError("native request quote does not cross native ladder level")
        rows.append({"leg_index": index, "native_position_id": pair["native_position_id"],
                     "native_request_utc": request["ts"],
                     "native_request_quote": str(quote),
                     "native_fill_price": str(Decimal(pair["native_entry_price"])),
                     "shadow_fill_price": str(Decimal(pair["shadow_entry_price"])),
                     "native_trigger": str(native_level),
                     "shadow_trigger_at_same_quote": str(shadow_level),
                     "native_trigger_crossed": crossed_native,
                     "shadow_trigger_crossed_at_native_quote": crossed_shadow,
                     "native_minus_shadow_fill_ms": pair["native_minus_shadow_ms"],
                     "classification": ("first_fill_anchor" if index == 0 else
                                        "anchor_separates_triggers" if not crossed_shadow else
                                        "both_triggers_crossed_at_native_quote")})
    return {"signal_id": comparison["signal_id"], "direction": direction,
            "verified_leg_count": len(rows), "first_fill_anchor_delta": str(first_native - first_shadow),
            "rows": rows}


def coverage_summary(report, analyzed_signals):
    native = {row["signal_id"] for row in report["coverage"]
              if row["signal_id"].startswith("canal2_")
              and row["native_basket_in_extract"] is True}
    comparisons = {row["signal_id"]: row for row in report["comparisons"]
                   if row["channel"] == "canal2"}
    if (len(comparisons) != sum(row["channel"] == "canal2" for row in report["comparisons"])
            or not set(comparisons) <= native):
        raise ValueError("Gold comparison denominator inconsistent with native coverage")
    verified = {signal for signal, row in comparisons.items()
                if row.get("entry_pairing", {}).get("status") == "verified_journal_entry_binding"
                and row["entry_pairing"].get("full_entry_identity_verified") is True}
    if set(analyzed_signals) != verified:
        raise ValueError("all identity-verified Gold comparisons must be analyzed")
    return {"native_gold_basket_count": len(native),
            "gold_live_control_comparison_count": len(comparisons),
            "gold_verified_entry_binding_count": len(verified),
            "gold_analyzed_count": len(analyzed_signals),
            "native_gold_without_comparison": sorted(native - set(comparisons)),
            "gold_comparison_without_verified_entry_binding": [
                {"signal_id": signal, "status": row.get("entry_pairing", {}).get("status")}
                for signal, row in sorted(comparisons.items()) if signal not in verified]}


def audit(comparison_path, old_probes, new_probes, verifications):
    old_probes, new_probes, verifications = (
        tuple(map(Path, values)) for values in (old_probes, new_probes, verifications))
    if not 0 < len(old_probes) == len(new_probes) == len(verifications) <= 10:
        raise ValueError("invalid verified probe count")
    comparison_path = Path(comparison_path)
    report = read(comparison_path)
    if report.get("contract") != "week_shadow_live_control_path_diagnostic_v1":
        raise ValueError("unexpected comparison contract")
    comparisons = {row["signal_id"]: row for row in report["comparisons"]}
    if len(comparisons) != len(report["comparisons"]):
        raise ValueError("duplicate comparison signal")
    results = []
    for old_path, new_path, verification_path in zip(
            old_probes, new_probes, verifications, strict=True):
        old, new, verification = map(read, (old_path, new_path, verification_path))
        current = compare_projection(old, new, restore_native_details=True,
                                     allow_schema_expansion=True)
        _bound(report, old_path)
        _bound(verification, old_path)
        _bound(verification, new_path)
        signal = current["signal_id"]
        if (verification.get("status") != "projection_verified"
                or verification.get("signal_id") != signal
                or verification.get("source_window_sha256") != current["source_window_sha256"]):
            raise ValueError("native probe projection verification mismatch")
        if signal not in comparisons:
            raise ValueError("verified probe lacks shadow comparison")
        results.append(analyze_ladder(comparisons[signal], new["events"]))
    watched = {str(path): digest(path) for path in
               (comparison_path, *old_probes, *new_probes, *verifications,
                Path(__file__), Path(__file__).resolve().parents[1] / "gold_555_live_candidate.py")}
    return {"contract": "gold_verified_ladder_anchor_diagnostic_v1",
            "status": "diagnostic_only", "policy_fingerprint": CANDIDATE_FINGERPRINT,
            "signals": results,
            "coverage": coverage_summary(report, [item["signal_id"] for item in results]),
            "inputs_sha256": watched,
            "limitations": ["The native quote is not proof the shadow processed that quote at the same time.",
                            "This threshold test does not reproduce broker execution or exits.",
                            "Only identity-verified Gold entries are included; this is not full path parity."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--old-probe", type=Path, action="append", required=True)
    parser.add_argument("--new-probe", type=Path, action="append", required=True)
    parser.add_argument("--verification", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit(args.comparison, args.old_probe, args.new_probe, args.verification)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"signals": len(result["signals"]),
                      "anchor_separated": sum(row["classification"] == "anchor_separates_triggers"
                                              for item in result["signals"] for row in item["rows"])}))


if __name__ == "__main__":
    main()
