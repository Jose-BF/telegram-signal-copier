"""Independent ticket-by-ticket certification for strategy replay results."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Mapping


SCHEMA_VERSION = 1
REQUIRED_SOURCE_FINGERPRINTS = {
    "market_ticks_sha256",
    "market_tick_contract_sha256",
    "conversion_ticks_sha256",
    "conversion_tick_contract_sha256",
    "money_contract_sha256",
    "replay_trade_sha256",
    "provider_signal_sha256",
    "policy_sha256",
}


def _json_safe(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def canonical_json_bytes(value) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _utc(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _ticket_id(ticket: Mapping) -> str:
    value = ticket.get("ticket") or ticket.get("position_id")
    if value in (None, ""):
        return ""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


def _row_identity(row: Mapping) -> tuple[str, str]:
    return (
        str(row.get("sig_id") or ""),
        str(
            row.get("strategy")
            or (row.get("policy") or {}).get("policy_id")
            or ""
        ),
    )


def _valid_sha256(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(
        character in "0123456789abcdef" for character in text
    )


def _source_blockers(source_evidence: Mapping) -> list[str]:
    blockers: list[str] = []
    for role in sorted(REQUIRED_SOURCE_FINGERPRINTS):
        if not _valid_sha256(source_evidence.get(role)):
            blockers.append(f"invalid_source_fingerprint:{role}")
    return blockers


def _ordered_tick_evidence(
    records: Iterable[Mapping],
    *,
    role: str,
) -> list[dict]:
    prepared: list[dict] = []
    seen_days: set[str] = set()
    for record in records:
        day = str(record.get("day") or "")
        parquet = record.get("parquet_sha256")
        contract = record.get("contract_sha256")
        if not day or day in seen_days:
            raise ValueError(f"invalid_{role}_tick_day")
        if not _valid_sha256(parquet):
            raise ValueError(f"invalid_{role}_tick_fingerprint:{day}")
        if not _valid_sha256(contract):
            raise ValueError(f"invalid_{role}_contract_fingerprint:{day}")
        seen_days.add(day)
        prepared.append({
            "day": day,
            "parquet_sha256": str(parquet),
            "contract_sha256": str(contract),
        })
    return sorted(prepared, key=lambda record: record["day"])


def build_source_evidence(
    *,
    trade: Mapping,
    provider_signal: Mapping | None,
    policy: Mapping,
    market_tick_evidence: Iterable[Mapping],
    conversion_tick_evidence: Iterable[Mapping],
    money_contract_sha256: str,
) -> dict[str, str]:
    """Bind one policy-trade proof to all immutable source artifacts."""
    if not _valid_sha256(money_contract_sha256):
        raise ValueError("invalid_money_contract_fingerprint")
    market = _ordered_tick_evidence(
        market_tick_evidence,
        role="market",
    )
    conversion = _ordered_tick_evidence(
        conversion_tick_evidence,
        role="conversion",
    )
    return {
        "market_ticks_sha256": sha256_json([
            {"day": row["day"], "sha256": row["parquet_sha256"]}
            for row in market
        ]),
        "market_tick_contract_sha256": sha256_json([
            {"day": row["day"], "sha256": row["contract_sha256"]}
            for row in market
        ]),
        "conversion_ticks_sha256": sha256_json([
            {"day": row["day"], "sha256": row["parquet_sha256"]}
            for row in conversion
        ]),
        "conversion_tick_contract_sha256": sha256_json([
            {"day": row["day"], "sha256": row["contract_sha256"]}
            for row in conversion
        ]),
        "money_contract_sha256": str(money_contract_sha256),
        "replay_trade_sha256": sha256_json(trade),
        "provider_signal_sha256": sha256_json(provider_signal),
        "policy_sha256": sha256_json(policy),
    }


def _same_price(left: object, right: object, tick_size: float) -> bool:
    try:
        return math.isclose(
            float(left),
            float(right),
            rel_tol=0.0,
            abs_tol=tick_size / 10.0,
        )
    except (TypeError, ValueError):
        return left is None and right is None


def _same_volume(left: object, right: object) -> bool:
    try:
        return math.isclose(
            float(left),
            float(right),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    except (TypeError, ValueError):
        return left is None and right is None


def _same_time(left: object, right: object) -> bool:
    left_time = _utc(left)
    right_time = _utc(right)
    return (
        left_time == right_time
        if left_time is not None and right_time is not None
        else left in (None, "") and right in (None, "")
    )


def _same_money(left: object, right: object, currency_digits: int) -> bool:
    left_value = _decimal(left)
    right_value = _decimal(right)
    if left_value is None or right_value is None:
        return left is None and right is None
    quantum = Decimal(1).scaleb(-currency_digits)
    return left_value.quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    ) == right_value.quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )


def _same_decimal_places(left: object, right: object, digits: int) -> bool:
    left_value = _decimal(left)
    right_value = _decimal(right)
    if left_value is None or right_value is None:
        return left is None and right is None
    quantum = Decimal(1).scaleb(-digits)
    return left_value.quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    ) == right_value.quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )


def _same_optional_mapping(left: object, right: object) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    return _json_safe(left) == _json_safe(right)


def _index_tickets(
    tickets: Iterable[Mapping],
) -> tuple[dict[str, Mapping], list[str], list[int]]:
    indexed: dict[str, Mapping] = {}
    duplicates: list[str] = []
    invalid_indexes: list[int] = []
    for index, ticket in enumerate(tickets):
        ticket_id = _ticket_id(ticket)
        if not ticket_id:
            invalid_indexes.append(index)
            continue
        if ticket_id in indexed:
            duplicates.append(ticket_id)
            continue
        indexed[ticket_id] = ticket
    return indexed, sorted(set(duplicates)), invalid_indexes


def _ticket_snapshot(ticket: Mapping) -> dict:
    fields = (
        "ticket",
        "leg_action",
        "open_time_utc",
        "open_price",
        "volume",
        "close_reason",
        "close_time_utc",
        "close_price",
        "strategy_pnl",
        "profit_currency_pnl",
        "touch_side",
        "touch_side_price",
        "money_conversion",
        "money_formula",
    )
    return {
        field: _json_safe(ticket.get(field))
        for field in fields
        if field in ticket
    }


def certify_trade(
    *,
    candidate: Mapping,
    oracle: Mapping,
    tick_size: float,
    currency_digits: int,
    source_evidence: Mapping,
) -> dict:
    """Compare one candidate policy-trade row with the independent oracle."""
    sig_id, policy_id = _row_identity(oracle)
    candidate_identity = _row_identity(candidate)
    blockers = _source_blockers(source_evidence)
    if not sig_id or not policy_id:
        blockers.append("invalid_oracle_row_identity")
    if candidate_identity != (sig_id, policy_id):
        blockers.append(
            f"row_identity_mismatch:{sig_id or '<empty>'}:"
            f"{policy_id or '<empty>'}"
        )
    if (
        isinstance(tick_size, bool)
        or not isinstance(tick_size, (int, float))
        or not math.isfinite(float(tick_size))
        or float(tick_size) <= 0
    ):
        blockers.append("invalid_certificate_tick_size")
    if (
        isinstance(currency_digits, bool)
        or not isinstance(currency_digits, int)
        or not 0 <= currency_digits <= 8
    ):
        blockers.append("invalid_certificate_currency_digits")

    if oracle.get("status") == "blocked":
        oracle_blockers = list(oracle.get("blockers") or ["unknown"])
        blockers.extend(
            f"oracle_blocked:{sig_id}:{policy_id}:{blocker}"
            for blocker in oracle_blockers
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": sig_id,
            "strategy": policy_id,
            "status": "blocked",
            "certified_tickets": 0,
            "mismatched_tickets": 0,
            "blocked_tickets": len(oracle.get("tickets") or []) or 1,
            "blockers": list(dict.fromkeys(blockers)),
            "ticket_proofs": [],
            "proof_sha256": None,
        }
    if candidate.get("status") == "blocked":
        blockers.extend(
            f"candidate_blocked:{sig_id}:{policy_id}:{blocker}"
            for blocker in candidate.get("blockers") or ["unknown"]
        )
    if blockers:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": sig_id,
            "strategy": policy_id,
            "status": "blocked",
            "certified_tickets": 0,
            "mismatched_tickets": 0,
            "blocked_tickets": 1,
            "blockers": list(dict.fromkeys(blockers)),
            "ticket_proofs": [],
            "proof_sha256": None,
        }

    candidate_tickets, candidate_duplicates, candidate_invalid = (
        _index_tickets(candidate.get("tickets") or [])
    )
    oracle_tickets, oracle_duplicates, oracle_invalid = _index_tickets(
        oracle.get("tickets") or []
    )
    oracle_structure_blockers = [
        *(
            f"duplicate_oracle_ticket:{sig_id}:{policy_id}:{ticket_id}"
            for ticket_id in oracle_duplicates
        ),
        *(
            f"invalid_oracle_ticket_identity:{sig_id}:{policy_id}:{index}"
            for index in oracle_invalid
        ),
    ]
    if oracle_structure_blockers:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": sig_id,
            "strategy": policy_id,
            "status": "blocked",
            "certified_tickets": 0,
            "mismatched_tickets": 0,
            "blocked_tickets": max(1, len(oracle.get("tickets") or [])),
            "blockers": oracle_structure_blockers,
            "ticket_proofs": [],
            "proof_sha256": None,
        }
    candidate_structure_blockers = [
        *(
            f"duplicate_candidate_ticket:{sig_id}:{policy_id}:{ticket_id}"
            for ticket_id in candidate_duplicates
        ),
        *(
            f"invalid_candidate_ticket_identity:{sig_id}:{policy_id}:{index}"
            for index in candidate_invalid
        ),
    ]
    if candidate_structure_blockers:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": sig_id,
            "strategy": policy_id,
            "status": "mismatch",
            "certified_tickets": 0,
            "mismatched_tickets": max(
                1,
                len(candidate_structure_blockers),
            ),
            "blocked_tickets": 0,
            "blockers": candidate_structure_blockers,
            "ticket_proofs": [],
            "proof_sha256": sha256_json({
                "source_evidence": source_evidence,
                "candidate_tickets": candidate.get("tickets") or [],
            }),
        }
    if not oracle_tickets and not candidate_tickets:
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": sig_id,
            "strategy": policy_id,
            "status": "blocked",
            "certified_tickets": 0,
            "mismatched_tickets": 0,
            "blocked_tickets": 1,
            "blockers": [f"empty_ticket_set:{sig_id}:{policy_id}"],
            "ticket_proofs": [],
            "proof_sha256": None,
        }
    if set(candidate_tickets) != set(oracle_tickets):
        blocker = f"ticket_set_mismatch:{sig_id}:{policy_id}"
        return {
            "schema_version": SCHEMA_VERSION,
            "sig_id": sig_id,
            "strategy": policy_id,
            "status": "mismatch",
            "certified_tickets": 0,
            "mismatched_tickets": max(
                len(candidate_tickets),
                len(oracle_tickets),
            ),
            "blocked_tickets": 0,
            "blockers": [blocker],
            "ticket_proofs": [],
            "proof_sha256": sha256_json({
                "source_evidence": source_evidence,
                "candidate_tickets": sorted(candidate_tickets),
                "oracle_tickets": sorted(oracle_tickets),
            }),
        }

    trade_comparisons = {
        "direction": str(candidate.get("direction") or "").upper()
        == str(oracle.get("direction") or "").upper(),
        "entry_authority": candidate.get("entry_authority")
        == oracle.get("entry_authority"),
        "strategy_pnl": _same_money(
            candidate.get("strategy_pnl"),
            oracle.get("strategy_pnl"),
            currency_digits,
        ),
    }
    blockers.extend(
        f"trade_mismatch:{sig_id}:{policy_id}:{field}"
        for field, matched in trade_comparisons.items()
        if not matched
    )

    ticket_proofs: list[dict] = []
    mismatched_tickets = 0
    for ticket_id in sorted(oracle_tickets):
        candidate_ticket = candidate_tickets[ticket_id]
        oracle_ticket = oracle_tickets[ticket_id]
        comparisons = {
            "open_time_utc": _same_time(
                candidate_ticket.get("open_time_utc"),
                oracle_ticket.get("open_time_utc"),
            ),
            "open_price": _same_price(
                candidate_ticket.get("open_price"),
                oracle_ticket.get("open_price"),
                float(tick_size),
            ),
            "volume": _same_volume(
                candidate_ticket.get("volume"),
                oracle_ticket.get("volume"),
            ),
            "leg_action": candidate_ticket.get("leg_action")
            == oracle_ticket.get("leg_action"),
            "close_reason": candidate_ticket.get("close_reason")
            == oracle_ticket.get("close_reason"),
            "close_time_utc": _same_time(
                candidate_ticket.get("close_time_utc"),
                oracle_ticket.get("close_time_utc"),
            ),
            "close_price": _same_price(
                candidate_ticket.get("close_price"),
                oracle_ticket.get("close_price"),
                float(tick_size),
            ),
            "strategy_pnl": _same_money(
                candidate_ticket.get("strategy_pnl"),
                oracle_ticket.get("strategy_pnl"),
                currency_digits,
            ),
            "profit_currency_pnl": _same_decimal_places(
                candidate_ticket.get("profit_currency_pnl"),
                oracle_ticket.get("profit_currency_pnl"),
                8,
            ),
            "touch_side": candidate_ticket.get("touch_side")
            == oracle_ticket.get("touch_side"),
            "touch_side_price": _same_price(
                candidate_ticket.get("touch_side_price"),
                oracle_ticket.get("touch_side_price"),
                float(tick_size),
            ),
            "money_conversion": _same_optional_mapping(
                candidate_ticket.get("money_conversion"),
                oracle_ticket.get("money_conversion"),
            ),
            "money_formula": _same_optional_mapping(
                candidate_ticket.get("money_formula"),
                oracle_ticket.get("money_formula"),
            ),
        }
        mismatches = [
            field for field, matched in comparisons.items() if not matched
        ]
        if mismatches:
            mismatched_tickets += 1
            blockers.extend(
                f"ticket_mismatch:{sig_id}:{policy_id}:{ticket_id}:{field}"
                for field in mismatches
            )
        ticket_proofs.append({
            "schema_version": SCHEMA_VERSION,
            "sig_id": sig_id,
            "strategy": policy_id,
            "ticket": ticket_id,
            "source_evidence": dict(source_evidence),
            "candidate": _ticket_snapshot(candidate_ticket),
            "oracle": _ticket_snapshot(oracle_ticket),
            "comparisons": comparisons,
            "status": "certified" if not mismatches else "mismatch",
        })

    proof_payload = {
        "sig_id": sig_id,
        "strategy": policy_id,
        "source_evidence": dict(source_evidence),
        "trade_comparisons": trade_comparisons,
        "ticket_proofs": ticket_proofs,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "sig_id": sig_id,
        "strategy": policy_id,
        "status": "certified" if not blockers else "mismatch",
        "certified_tickets": len(ticket_proofs) - mismatched_tickets,
        "mismatched_tickets": mismatched_tickets,
        "blocked_tickets": 0,
        "blockers": list(dict.fromkeys(blockers)),
        "trade_comparisons": trade_comparisons,
        "ticket_proofs": ticket_proofs,
        "proof_sha256": sha256_json(proof_payload),
    }


def summarize_run(
    *,
    certificates: Iterable[Mapping],
    expected_pairs: set[tuple[str, str]],
    expected_proof_sha256: str | None = None,
) -> dict:
    certificates = list(certificates)
    by_pair: dict[tuple[str, str], list[Mapping]] = {}
    for certificate in certificates:
        pair = (
            str(certificate.get("sig_id") or ""),
            str(certificate.get("strategy") or ""),
        )
        by_pair.setdefault(pair, []).append(certificate)

    blockers: list[str] = []
    for sig_id, policy_id in sorted(expected_pairs - set(by_pair)):
        blockers.append(f"missing_certificate:{sig_id}:{policy_id}")
    for sig_id, policy_id in sorted(set(by_pair) - expected_pairs):
        blockers.append(f"unexpected_certificate:{sig_id}:{policy_id}")
    for pair, rows in sorted(by_pair.items()):
        if len(rows) != 1:
            blockers.append(f"duplicate_certificate:{pair[0]}:{pair[1]}")
    blockers.extend(
        blocker
        for certificate in certificates
        for blocker in certificate.get("blockers") or []
    )

    proof_records = [
        {
            "sig_id": certificate.get("sig_id"),
            "strategy": certificate.get("strategy"),
            "status": certificate.get("status"),
            "proof_sha256": certificate.get("proof_sha256"),
        }
        for certificate in sorted(
            certificates,
            key=lambda row: (
                str(row.get("sig_id") or ""),
                str(row.get("strategy") or ""),
            ),
        )
    ]
    proof_sha256 = sha256_json(proof_records)
    deterministic = (
        expected_proof_sha256 is None
        or proof_sha256 == expected_proof_sha256
    )
    if not deterministic:
        blockers.append("nondeterministic_proof_fingerprint")

    certified = sum(
        certificate.get("status") == "certified"
        for certificate in certificates
    )
    mismatched = sum(
        certificate.get("status") == "mismatch"
        for certificate in certificates
    )
    blocked = sum(
        certificate.get("status") == "blocked"
        for certificate in certificates
    )
    ticket_expected = sum(
        int(certificate.get("certified_tickets") or 0)
        + int(certificate.get("mismatched_tickets") or 0)
        + int(certificate.get("blocked_tickets") or 0)
        for certificate in certificates
    )
    ticket_certified = sum(
        int(certificate.get("certified_tickets") or 0)
        for certificate in certificates
    )
    ticket_mismatched = sum(
        int(certificate.get("mismatched_tickets") or 0)
        for certificate in certificates
    )
    ticket_blocked = sum(
        int(certificate.get("blocked_tickets") or 0)
        for certificate in certificates
    )
    complete = bool(
        not blockers
        and len(certificates) == len(expected_pairs)
        and certified == len(expected_pairs)
        and ticket_certified == ticket_expected
        and ticket_mismatched == 0
        and ticket_blocked == 0
        and deterministic
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "rows_expected": len(expected_pairs),
        "rows_checked": len(certificates),
        "certified_rows": certified,
        "mismatched_rows": mismatched,
        "blocked_rows": blocked,
        "tickets_expected": ticket_expected,
        "certified_tickets": ticket_certified,
        "mismatched_tickets": ticket_mismatched,
        "blocked_tickets": ticket_blocked,
        "proof_sha256": proof_sha256,
        "deterministic": deterministic,
        "complete": complete,
        "conclusions_allowed": complete,
        "blockers": list(dict.fromkeys(blockers)),
    }
