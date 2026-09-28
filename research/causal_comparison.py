"""Compare independently produced logical-leg facts, never nearest fills."""

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from research.causal_replay import utc


@dataclass(frozen=True)
class SequenceEvent:
    slot: int
    kind: str
    at: datetime
    direction: str
    price: Decimal
    volume: Decimal
    money: Decimal
    mechanism: str

    def __post_init__(self):
        if type(self.slot) is not int or self.slot < 1 or self.kind not in {"entry", "exit"}:
            raise ValueError("invalid logical event identity")
        object.__setattr__(self, "at", utc(self.at))
        if self.direction not in {"BUY", "SELL"} or not self.mechanism:
            raise ValueError("missing direction or mechanism")
        for name in ("price", "volume", "money"):
            raw = getattr(self, name)
            if isinstance(raw, bool):
                raise ValueError(f"invalid {name}")
            value = Decimal(str(raw))
            if not value.is_finite() or (name != "money" and value <= 0):
                raise ValueError(f"invalid {name}")
            object.__setattr__(self, name, value)


def compare_sequences(observed, simulated):
    """Exact diagnostic deltas; no timing tolerance or execution admission.

    The logical slot comes from strategy metadata, never price/time matching.
    Same-millisecond partial fills compare as multisets within that slot.
    """
    def index(events):
        grouped = defaultdict(list)
        for row in events:
            if not isinstance(row, SequenceEvent):
                raise ValueError("invalid sequence event")
            grouped[(row.slot, row.kind)].append(row)
        indexed = {}
        for (slot, kind), rows in grouped.items():
            if kind == "entry" and len(rows) != 1:
                raise ValueError("multiple entries for one logical slot")
            rows.sort(key=lambda row: (row.at, row.volume, row.price, row.money, row.mechanism))
            for ordinal, row in enumerate(rows):
                indexed[(slot, kind, ordinal)] = row
        return indexed

    actual, replayed = index(observed), index(simulated)
    comparisons = []
    for key in actual.keys() | replayed.keys():
        left, right = actual.get(key), replayed.get(key)
        differences = ["missing_event"] if left is None or right is None else [
            name for name in ("at", "direction", "price", "volume", "money", "mechanism")
            if getattr(left, name) != getattr(right, name)
        ]
        comparisons.append({
            "slot": key[0], "kind": key[1], "ordinal": key[2],
            "first_possible_time": min(row.at for row in (left, right) if row is not None),
            "observed": asdict(left) if left else None,
            "simulated": asdict(right) if right else None, "differences": differences,
            "time_delta_ms": (right.at - left.at).total_seconds() * 1000 if left and right else None,
            "price_delta": right.price - left.price if left and right else None,
            "money_delta": right.money - left.money if left and right else None,
        })
    comparisons.sort(key=lambda row: (row["first_possible_time"], row["slot"], row["kind"], row["ordinal"]))
    mismatches = [row for row in comparisons if row["differences"]]
    structural = [row for row in comparisons if set(row["differences"]) & {
        "missing_event", "direction", "volume", "mechanism"}]
    return {
        "status": "mismatch" if mismatches else "exact_facts_only",
        "observed_event_count": len(actual), "simulated_event_count": len(replayed),
        "first_divergence": mismatches[0] if mismatches else None,
        "first_structural_divergence": structural[0] if structural else None,
        "structural_definition": "missing event, direction, volume or broker mechanism; not a tolerance exemption",
        "comparisons": comparisons, "full_live_parity_verified": False,
        "unverified": ["decision_sequence", "modify_reject_confirm_sequence", "alternative_fill_realizability"],
    }
