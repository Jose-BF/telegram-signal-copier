"""Deterministic post-session settlement for frozen strategy shadows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

import broker_tick_clock
from strategy_shadow_catalog import build_shadow_catalog
from strategy_shadow_contracts import (
    ShadowManagementEvent,
    ShadowSignalState,
    ShadowTick,
    canonical_hash,
)
from strategy_shadow_engine import advance_tick, apply_management, register_signal
from strategy_shadow_manifest import build_catalog_manifest, catalog_manifest_matches
from strategy_shadow_parity import actual_logic_signature, shadow_logic_signature
from strategy_shadow_report import build_report


_TERMINAL = {"closed", "cancelled", "incomplete"}
_PROVIDER_TRANSITIONS = {
    "provider_action_ignored",
    "provider_action_observed",
    "provider_close_pending",
    "provider_protection_applied",
}
_MATERIAL_PROVIDER_TRANSITIONS = {
    "provider_close_pending",
    "provider_protection_applied",
}


@dataclass(frozen=True)
class ShadowTickRead:
    ticks: tuple[ShadowTick, ...]
    complete: bool
    evidence_id: str
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        timestamps = [tick.time_msc for tick in self.ticks]
        if timestamps != sorted(timestamps):
            raise ValueError("settlement ticks must be ordered")
        if not self.evidence_id:
            raise ValueError("tick evidence_id is required")
        if self.complete and self.blockers:
            raise ValueError("complete tick read cannot contain blockers")


@dataclass(frozen=True)
class ShadowRegistrationTickRead:
    normalized_time_msc: int | None
    complete: bool
    evidence_id: str
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.evidence_id:
            raise ValueError("registration tick evidence_id is required")
        if self.normalized_time_msc is not None and self.normalized_time_msc < 0:
            raise ValueError("normalized registration time cannot be negative")
        if self.complete and (
            self.normalized_time_msc is None or self.blockers
        ):
            raise ValueError("complete registration tick evidence is inconsistent")
        if not self.complete and not self.blockers:
            raise ValueError("incomplete registration tick evidence needs blockers")


class ShadowTickReader(Protocol):
    def read(self, start: datetime, end: datetime) -> ShadowTickRead: ...

    def cost_blockers(self, state: ShadowSignalState) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class _ReplayManagement:
    event: ShadowManagementEvent
    transition: str
    after_tick_identity: tuple[int, float, float, float, int, float] | None
    source_sequence: int


def _market_identity(identity: tuple | None) -> tuple | None:
    """Normalize live/cache identities whose MT5 flag bits can differ."""
    if identity is None:
        return None
    return (
        int(identity[0]),
        float(identity[1]),
        float(identity[2]),
        float(identity[3]),
        float(identity[5]),
    )


def _money_contract_evidence(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only fields that can change replay arithmetic or broker identity."""
    return {
        key: contract[key]
        for key in (
            "schema_version",
            "account",
            "instrument",
            "conversion",
            "costs",
        )
        if key in contract
    }


class ParquetShadowTickReader:
    """Read verified UTC XAUUSD and conversion ticks without touching MT5."""

    def __init__(
        self,
        *,
        ticks_cache_dir: Path,
        money_ticks_cache_dir: Path,
        money_contract: Mapping[str, Any],
        symbol: str = "XAUUSD",
    ) -> None:
        from broker_money import validate_contract_metadata

        self.ticks_cache_dir = Path(ticks_cache_dir)
        self.money_ticks_cache_dir = Path(money_ticks_cache_dir)
        self.money_contract = dict(money_contract)
        self.symbol = str(symbol)
        blockers = validate_contract_metadata(self.money_contract)
        if blockers:
            raise ValueError(",".join(blockers))
        instrument = self.money_contract.get("instrument") or {}
        if str(instrument.get("symbol") or "") != self.symbol:
            raise ValueError("money contract instrument mismatch")
        self._xau_days: dict[date, Any] = {}
        self._money_days: dict[date, Any] = {}
        self._contracts: dict[tuple[str, date], dict] = {}

    @staticmethod
    def _days(start: datetime, end: datetime) -> tuple[date, ...]:
        if end <= start:
            return ()
        current = start.date()
        last = (end - timedelta(microseconds=1)).date()
        days = []
        while current <= last:
            days.append(current)
            current += timedelta(days=1)
        return tuple(days)

    def _load_day(
        self,
        day: date,
        *,
        cache_dir: Path,
        symbol: str,
        store: dict[date, Any],
    ):
        import pandas as pd
        from tools import ensure_replay_tick_cache

        if day in store:
            return store[day], self._contracts[(symbol, day)], None
        contract = ensure_replay_tick_cache.load_valid_day_contract(
            cache_dir,
            day,
            expected_symbol=symbol,
        )
        if contract is None:
            return None, None, f"invalid_tick_cache:{symbol}:{day.isoformat()}"
        path = cache_dir / f"{day.isoformat()}.parquet"
        try:
            frame = pd.read_parquet(
                path,
                columns=[
                    "time_utc",
                    "bid",
                    "ask",
                    "last",
                    "flags",
                    "volume_real",
                ],
            )
        except Exception as exc:
            return None, contract, (
                f"tick_cache_read_failed:{symbol}:{day.isoformat()}:"
                f"{type(exc).__name__}"
            )
        frame = frame.copy()
        frame["time_utc"] = pd.to_datetime(
            frame["time_utc"], utc=True, errors="coerce",
        )
        for column in ("bid", "ask", "last", "flags", "volume_real"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["time_utc", "bid", "ask"])
        frame = frame.loc[
            (frame["bid"] > 0.0) & (frame["ask"] >= frame["bid"])
        ]
        frame = frame.sort_values("time_utc", kind="stable").reset_index(drop=True)
        store[day] = frame
        self._contracts[(symbol, day)] = contract
        return frame, contract, None

    def registration_tick_evidence(
        self,
        *,
        raw_server_msc: int,
        observed_at_utc: datetime,
        reference_bid: float,
        reference_ask: float,
    ) -> ShadowRegistrationTickRead:
        """Prove that a live reference quote exists in the verified cache."""

        import pandas as pd
        from tools import ensure_replay_tick_cache

        evidence: dict[str, Any] = {
            "raw_server_msc": int(raw_server_msc),
            "observed_at_utc": observed_at_utc.astimezone(
                timezone.utc,
            ).isoformat(),
            "reference_bid": float(reference_bid),
            "reference_ask": float(reference_ask),
        }

        def blocked(reason: str) -> ShadowRegistrationTickRead:
            return ShadowRegistrationTickRead(
                normalized_time_msc=None,
                complete=False,
                evidence_id=canonical_hash({**evidence, "blockers": [reason]}),
                blockers=(reason,),
            )

        try:
            offset = broker_tick_clock.inferred_utc_offset_seconds(
                int(raw_server_msc),
                observed_utc=observed_at_utc,
            )
            normalized_msc = broker_tick_clock.normalize_server_msc(
                int(raw_server_msc), offset,
            )
        except (OverflowError, TypeError, ValueError):
            return blocked("registration_tick_clock_unverified")
        normalized = datetime.fromtimestamp(
            normalized_msc / 1000.0,
            tz=timezone.utc,
        )
        frame, contract, error = self._load_day(
            normalized.date(),
            cache_dir=self.ticks_cache_dir,
            symbol=self.symbol,
            store=self._xau_days,
        )
        evidence.update({
            "normalized_time_msc": normalized_msc,
            "utc_offset_seconds": offset,
            "contract_sha256": (
                None if contract is None else contract.get("contract_sha256")
            ),
        })
        if error or frame is None or contract is None:
            return blocked(error or "registration_tick_cache_invalid")
        if contract.get("utc_offset_seconds") != offset:
            return blocked("registration_tick_clock_mismatch")
        if not ensure_replay_tick_cache.coverage_satisfies_window(
            contract,
            normalized,
            normalized + timedelta(milliseconds=1),
        ):
            return blocked("registration_tick_cache_incomplete")
        matches = frame.loc[
            frame["time_utc"] == pd.Timestamp(normalized)
        ]
        if matches.empty:
            return blocked("registration_tick_missing")
        quote_matches = any(
            math.isclose(
                float(row.bid), float(reference_bid), rel_tol=0.0, abs_tol=1e-9,
            )
            and math.isclose(
                float(row.ask), float(reference_ask), rel_tol=0.0, abs_tol=1e-9,
            )
            for row in matches.itertuples(index=False)
        )
        if not quote_matches:
            return blocked("registration_tick_quote_mismatch")
        return ShadowRegistrationTickRead(
            normalized_time_msc=normalized_msc,
            complete=True,
            evidence_id=canonical_hash(evidence),
        )

    def read(self, start: datetime, end: datetime) -> ShadowTickRead:
        import pandas as pd
        from tools import ensure_replay_tick_cache

        start = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        evidence_seed: dict[str, Any] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "xau_contracts": [],
            "money_contracts": [],
            "money_contract": _money_contract_evidence(self.money_contract),
        }
        blockers: list[str] = []
        xau_frames = []
        for day in self._days(start, end):
            frame, contract, error = self._load_day(
                day,
                cache_dir=self.ticks_cache_dir,
                symbol=self.symbol,
                store=self._xau_days,
            )
            if error:
                blockers.append(error)
                continue
            day_start = datetime.combine(day, time.min, tzinfo=timezone.utc)
            required_from = max(start, day_start)
            required_through = min(end, day_start + timedelta(days=1))
            if not ensure_replay_tick_cache.coverage_satisfies_window(
                contract, required_from, required_through,
            ):
                blockers.append(
                    f"tick_cache_incomplete:{self.symbol}:{day.isoformat()}"
                )
                continue
            evidence_seed["xau_contracts"].append(contract["contract_sha256"])
            subset = frame.loc[
                (frame["time_utc"] > pd.Timestamp(start))
                & (frame["time_utc"] <= pd.Timestamp(end))
            ]
            if not subset.empty:
                xau_frames.append(subset)
        if blockers:
            return ShadowTickRead(
                ticks=(),
                complete=False,
                evidence_id=canonical_hash({**evidence_seed, "blockers": blockers}),
                blockers=tuple(sorted(set(blockers))),
            )
        if not xau_frames:
            return ShadowTickRead(
                ticks=(),
                complete=True,
                evidence_id=canonical_hash({**evidence_seed, "tick_count": 0}),
            )

        xau = pd.concat(xau_frames, ignore_index=True).sort_values(
            "time_utc", kind="stable",
        )
        conversion = self.money_contract.get("conversion") or {}
        orientation = str(conversion.get("orientation") or "")
        contract_size = float(
            (self.money_contract.get("instrument") or {})["contract_size"]
        )
        if orientation == "identity":
            xau["positive_factor"] = contract_size
            xau["negative_factor"] = contract_size
            xau["conversion_time_utc"] = xau["time_utc"]
            xau["conversion_bid"] = 1.0
            xau["conversion_ask"] = 1.0
        else:
            conversion_symbol = str(conversion.get("symbol") or "")
            money_frames = []
            for day in self._days(start, end):
                frame, contract, error = self._load_day(
                    day,
                    cache_dir=self.money_ticks_cache_dir,
                    symbol=conversion_symbol,
                    store=self._money_days,
                )
                if error:
                    blockers.append(error)
                    continue
                day_start = datetime.combine(
                    day, time.min, tzinfo=timezone.utc,
                )
                required_from = max(start, day_start)
                required_through = min(
                    end, day_start + timedelta(days=1),
                )
                if not ensure_replay_tick_cache.coverage_satisfies_window(
                    contract, required_from, required_through,
                ):
                    blockers.append(
                        "tick_cache_incomplete:"
                        f"{conversion_symbol}:{day.isoformat()}"
                    )
                    continue
                evidence_seed["money_contracts"].append(
                    contract["contract_sha256"]
                )
                money_frames.append(frame[["time_utc", "bid", "ask"]])
            if blockers:
                return ShadowTickRead(
                    ticks=(),
                    complete=False,
                    evidence_id=canonical_hash({
                        **evidence_seed, "blockers": blockers,
                    }),
                    blockers=tuple(sorted(set(blockers))),
                )
            money = pd.concat(money_frames, ignore_index=True).sort_values(
                "time_utc", kind="stable",
            ).rename(columns={
                "time_utc": "conversion_time_utc",
                "bid": "conversion_bid",
                "ask": "conversion_ask",
            })
            money["next_conversion_time_utc"] = money[
                "conversion_time_utc"
            ].shift(-1)
            xau = pd.merge_asof(
                xau.sort_values("time_utc", kind="stable"),
                money,
                left_on="time_utc",
                right_on="conversion_time_utc",
                direction="backward",
            )
            age_ms = (
                xau["time_utc"] - xau["conversion_time_utc"]
            ).dt.total_seconds() * 1000.0
            interval_ms = (
                xau["next_conversion_time_utc"]
                - xau["conversion_time_utc"]
            ).dt.total_seconds() * 1000.0
            valid_quote = (
                age_ms.notna()
                & (
                    (age_ms <= int(conversion["max_quote_age_ms"]))
                    | (
                        (xau["time_utc"] < xau["next_conversion_time_utc"])
                        & (interval_ms > 0)
                        & (
                            interval_ms
                            <= int(conversion.get(
                                "max_quote_interval_ms",
                                conversion["max_quote_age_ms"],
                            ))
                        )
                    )
                )
                & (xau["conversion_bid"] > 0)
                & (xau["conversion_ask"] > 0)
            )
            if orientation == "account_base_profit_quote":
                xau["positive_factor"] = (
                    contract_size / xau["conversion_ask"]
                ).where(valid_quote)
                xau["negative_factor"] = (
                    contract_size / xau["conversion_bid"]
                ).where(valid_quote)
            elif orientation == "profit_base_account_quote":
                xau["positive_factor"] = (
                    contract_size * xau["conversion_bid"]
                ).where(valid_quote)
                xau["negative_factor"] = (
                    contract_size * xau["conversion_ask"]
                ).where(valid_quote)
            else:
                raise ValueError("unsupported conversion orientation")

        # Equal-millisecond quotes retain their broker source order. Reordering
        # those prices can move an entry or exit to the wrong side of an event.
        xau = xau.sort_values("time_utc", kind="stable").reset_index(drop=True)
        ticks: list[ShadowTick] = []
        evidence_ids: dict[tuple, str] = {}
        for row in xau.itertuples(index=False):
            positive = float(row.positive_factor)
            negative = float(row.negative_factor)
            factors_available = math.isfinite(positive) and math.isfinite(negative)
            money_evidence_id = None
            if factors_available:
                evidence_key = (
                    str(row.conversion_time_utc),
                    float(row.conversion_bid),
                    float(row.conversion_ask),
                )
                money_evidence_id = evidence_ids.setdefault(
                    evidence_key,
                    canonical_hash({
                        "money_contract": _money_contract_evidence(
                            self.money_contract
                        ),
                        "conversion_time_utc": evidence_key[0],
                        "conversion_bid": evidence_key[1],
                        "conversion_ask": evidence_key[2],
                    }),
                )
            observed = row.time_utc.to_pydatetime()
            time_msc = int(round(observed.timestamp() * 1000))
            ticks.append(ShadowTick(
                time_msc=time_msc,
                bid=float(row.bid),
                ask=float(row.ask),
                last=float(row.last or 0.0),
                flags=int(row.flags or 0),
                volume_real=float(row.volume_real or 0.0),
                observed_at_utc=observed.isoformat(),
                positive_eur_per_move_lot=(
                    positive if factors_available else None
                ),
                negative_eur_per_move_lot=(
                    negative if factors_available else None
                ),
                money_evidence_id=money_evidence_id,
            ))
        evidence_seed["tick_count"] = len(ticks)
        evidence_seed["last_identity"] = (
            None if not ticks else list(ticks[-1].identity)
        )
        return ShadowTickRead(
            ticks=tuple(ticks),
            complete=True,
            evidence_id=canonical_hash(evidence_seed),
        )

    def _broker_date(self, time_msc: int) -> date | None:
        observed = datetime.fromtimestamp(
            int(time_msc) / 1000.0,
            tz=timezone.utc,
        )
        contract = self._contracts.get((self.symbol, observed.date()))
        if not isinstance(contract, Mapping):
            return None
        offset = contract.get("utc_offset_seconds")
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or abs(offset) > 14 * 3600
        ):
            return None
        return (observed + timedelta(seconds=offset)).date()

    def cost_blockers(self, state: ShadowSignalState) -> tuple[str, ...]:
        """Reject paths whose costs could have changed strategy decisions."""

        blockers: set[str] = set()
        for position in state.positions:
            if position.status != "closed":
                continue
            if position.closed_tick_msc is None:
                blockers.add("position_cost_time_missing")
                continue
            opened_day = self._broker_date(position.opened_tick_msc)
            closed_day = self._broker_date(position.closed_tick_msc)
            if opened_day is None or closed_day is None:
                blockers.add("broker_rollover_clock_unverified")
                continue
            if opened_day != closed_day:
                # Price P/L is exact, but accrued swap can alter basket guards
                # between ticks. A terminal-only adjustment would be causal
                # leakage, so the whole strategy path remains unrankable.
                blockers.add("broker_rollover_cost_path_unmodeled")
        return tuple(sorted(blockers))


def _parse_utc(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _window(since: date, until: date) -> tuple[datetime, datetime]:
    if until < since:
        raise ValueError("until cannot precede since")
    start = datetime.combine(since, time.min, tzinfo=timezone.utc)
    end = datetime.combine(until + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start, end


def _registration_state(record: Mapping[str, Any]) -> ShadowSignalState | None:
    payload = record.get("state")
    if not isinstance(payload, Mapping):
        return None
    try:
        return ShadowSignalState.from_dict(payload)
    except (TypeError, ValueError):
        return None


def _eligible_signal_expectations(
    records: Iterable[Mapping[str, Any]],
    *,
    start: datetime,
    end: datetime,
) -> dict[tuple[str, str], datetime]:
    """Find accepted signals from sessions where shadows were enabled."""

    source = list(records)
    started_sessions: dict[str, datetime] = {}
    for record in source:
        if str(record.get("ev") or "") != "strategy_shadow_runtime_started":
            continue
        session_id = str(record.get("session_id") or "")
        observed = _parse_utc(record.get("ts"))
        if not session_id or observed is None:
            continue
        started_sessions[session_id] = min(
            observed,
            started_sessions.get(session_id, observed),
        )

    expected: dict[tuple[str, str], datetime] = {}
    for record in source:
        if str(record.get("ev") or "") != "signal_received":
            continue
        session_id = str(record.get("session_id") or "")
        session_started = started_sessions.get(session_id)
        observed = _parse_utc(record.get("ts"))
        channel = str(record.get("channel") or "")
        signal_id = str(record.get("sig") or "")
        if (
            session_started is None
            or observed is None
            or observed < session_started
            or not (start <= observed < end)
            or channel not in {"canal1", "canal2"}
            or not signal_id
        ):
            continue
        if (
            channel == "canal2"
            and str(record.get("entry_source_kind") or "")
            != "telegram_now"
        ):
            continue
        key = (channel, signal_id)
        expected[key] = min(observed, expected.get(key, observed))
    return expected


def eligible_signal_ids(
    records: Iterable[Mapping[str, Any]],
    *,
    since: date,
    until: date,
) -> set[str]:
    """Return only signal IDs belonging to the requested shadow cohort."""

    source = [dict(record) for record in records]
    start, end = _window(since, until)
    signal_ids = {
        signal_id
        for _channel, signal_id in _eligible_signal_expectations(
            source,
            start=start,
            end=end,
        )
    }
    for record in source:
        if str(record.get("ev") or "") != "strategy_shadow_registered":
            continue
        state = _registration_state(record)
        registered = None if state is None else _parse_utc(
            state.registered_at_utc
        )
        if (
            state is not None
            and registered is not None
            and start <= registered < end
        ):
            signal_ids.add(state.signal_id)
    return signal_ids


def reconstruct_registration_records(
    records: Iterable[Mapping[str, Any]],
    *,
    provider_catalog: Mapping[str, Any],
    tick_reader: Any,
    trusted_source_commits: Mapping[str, str],
    since: date,
    until: date,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Recover missing Gold registrations only from redundant exact evidence."""

    source = [dict(record) for record in records]
    start, end = _window(since, until)
    catalog = build_shadow_catalog()
    gold_policies = catalog["canal2"]
    expected_candidates = [policy.candidate_id for policy in gold_policies]
    expected_controls = {
        channel: next(
            policy.candidate_id
            for policy in policies
            if policy.role == "live_control"
        )
        for channel, policies in catalog.items()
    }
    expected_candidate_map = {
        channel: [policy.candidate_id for policy in policies]
        for channel, policies in catalog.items()
    }
    current_manifest = build_catalog_manifest(catalog)
    provider_signals = (
        provider_catalog.get("signals")
        if isinstance(provider_catalog, Mapping)
        else None
    )
    if not isinstance(provider_signals, list):
        provider_signals = []
    eligible_signals = set(_eligible_signal_expectations(
        source,
        start=start,
        end=end,
    ))

    existing_by_signal: dict[str, dict[str, Mapping[str, Any]]] = {}
    for record in source:
        if str(record.get("ev") or "") != "strategy_shadow_registered":
            continue
        state = _registration_state(record)
        if state is None or state.channel != "canal2":
            continue
        existing_by_signal.setdefault(state.signal_id, {})[
            state.candidate_id
        ] = record

    signal_rows: dict[str, list[Mapping[str, Any]]] = {}
    for record in source:
        if (
            str(record.get("ev") or "") != "signal_received"
            or str(record.get("channel") or "") != "canal2"
            or str(record.get("entry_source_kind") or "") != "telegram_now"
        ):
            continue
        observed = _parse_utc(record.get("ts"))
        signal_id = str(record.get("sig") or "")
        if (
            observed is not None
            and start <= observed < end
            and ("canal2", signal_id) in eligible_signals
        ):
            signal_rows.setdefault(signal_id, []).append(record)

    reconstructed: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for signal_id in sorted(signal_rows):
        existing = existing_by_signal.get(signal_id, {})
        missing = [
            policy for policy in gold_policies
            if policy.candidate_id not in existing
        ]
        if not missing:
            continue
        blockers: set[str] = set()
        rows = signal_rows[signal_id]
        signal = rows[0]
        if len(rows) != 1:
            blockers.add("reconstruction_signal_received_ambiguous")

        session_id = str(signal.get("session_id") or "")
        source_commit = str(signal.get("code_commit") or "")
        revision_id = str(signal.get("message_revision_id") or "")
        decision_id = str(signal.get("decision_id") or "")
        direction = str(signal.get("direction") or "").upper()
        telegram_at = _parse_utc(signal.get("tg_ts"))
        signal_observed = _parse_utc(signal.get("ts"))
        if (
            not session_id
            or not source_commit
            or not revision_id
            or not decision_id
            or direction not in {"BUY", "SELL"}
            or telegram_at is None
            or signal_observed is None
        ):
            blockers.add("reconstruction_signal_lineage_incomplete")
        if (
            str(signal.get("live_strategy_id") or "")
            != expected_controls["canal2"]
            or str(signal.get("live_strategy_fingerprint") or "")
            != gold_policies[0].strategy_fingerprint
        ):
            blockers.add("reconstruction_live_strategy_mismatch")

        watches = [
            record for record in source
            if str(record.get("ev") or "") == "gold_555_entry_watch_started"
            and str(record.get("sig") or "") == signal_id
        ]
        watch = watches[0] if watches else {}
        if len(watches) != 1:
            blockers.add("reconstruction_entry_watch_ambiguous")
        intent = watch.get("intent") if isinstance(watch, Mapping) else None
        watch_state = watch.get("watch") if isinstance(watch, Mapping) else None
        if not isinstance(intent, Mapping) or not isinstance(watch_state, Mapping):
            blockers.add("reconstruction_entry_watch_incomplete")
            intent = {}
            watch_state = {}
        if (
            str(watch.get("session_id") or "") != session_id
            or str(watch.get("code_commit") or "") != source_commit
            or str(watch.get("message_revision_id") or "") != revision_id
            or str(watch.get("decision_id") or "") != decision_id
        ):
            blockers.add("reconstruction_entry_watch_lineage_mismatch")
        if (
            str(watch.get("strategy_id") or "")
            != expected_controls["canal2"]
            or str(watch.get("strategy_fingerprint") or "")
            != gold_policies[0].strategy_fingerprint
        ):
            blockers.add("reconstruction_entry_watch_strategy_mismatch")

        try:
            source_message_id = int(intent.get("message_id"))
            raw_server_msc = int(watch.get("reference_tick_time_msc"))
            reference_price = float(watch.get("reference_price"))
            reference_bid = float(watch.get("reference_bid"))
            reference_ask = float(watch.get("reference_ask"))
        except (TypeError, ValueError):
            source_message_id = 0
            raw_server_msc = 0
            reference_price = math.nan
            reference_bid = math.nan
            reference_ask = math.nan
            blockers.add("reconstruction_entry_watch_values_invalid")
        watch_telegram_at = _parse_utc(intent.get("telegram_timestamp"))
        watch_observed_at = _parse_utc(watch_state.get("observed_at"))
        try:
            watch_reference = float(watch_state.get("reference"))
        except (TypeError, ValueError):
            watch_reference = math.nan
        if (
            source_message_id <= 0
            or signal_id != f"canal2_{source_message_id}"
            or str(intent.get("direction") or "").upper() != direction
            or str(watch_state.get("direction") or "").upper() != direction
            or str(intent.get("source_kind") or "") != "telegram_now"
            or telegram_at is None
            or watch_telegram_at != telegram_at
            or watch_observed_at != telegram_at
        ):
            blockers.add("reconstruction_entry_watch_identity_mismatch")
        expected_reference = reference_ask if direction == "BUY" else reference_bid
        if (
            not math.isfinite(reference_price)
            or not math.isfinite(expected_reference)
            or not math.isclose(
                reference_price,
                expected_reference,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            or not math.isclose(
                watch_reference,
                reference_price,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ):
            blockers.add("reconstruction_reference_price_mismatch")

        runtimes = [
            record for record in source
            if str(record.get("ev") or "") == "strategy_shadow_runtime_started"
            and str(record.get("session_id") or "") == session_id
            and (
                signal_observed is None
                or (
                    _parse_utc(record.get("ts")) is not None
                    and _parse_utc(record.get("ts")) <= signal_observed
                )
            )
        ]
        runtime = max(
            runtimes,
            key=lambda record: _parse_utc(record.get("ts")) or start,
            default={},
        )
        if not runtime:
            blockers.add("reconstruction_shadow_runtime_missing")
        elif (
            runtime.get("candidates") != expected_candidate_map
            or runtime.get("controls") != expected_controls
            or str(runtime.get("code_commit") or "") != source_commit
        ):
            blockers.add("reconstruction_shadow_runtime_mismatch")
        trusted_contract = str(trusted_source_commits.get(source_commit) or "")
        runtime_manifest = runtime.get("catalog_manifest")
        if (
            runtime_manifest is not None
            and not catalog_manifest_matches(runtime_manifest, catalog)
        ):
            blockers.add("reconstruction_catalog_manifest_mismatch")
        if not trusted_contract:
            blockers.add("reconstruction_source_code_unverified")

        matching_provider = [
            item for item in provider_signals
            if isinstance(item, Mapping)
            and str(item.get("provider_signal_id") or "") == signal_id
        ]
        provider = matching_provider[0] if matching_provider else {}
        if len(matching_provider) != 1:
            blockers.add("reconstruction_provider_signal_ambiguous")
        entry_contract = (
            provider.get("entry_contract")
            if isinstance(provider, Mapping)
            else None
        )
        if not isinstance(entry_contract, Mapping):
            entry_contract = {}
        provider_trigger_at = _parse_utc(
            entry_contract.get("trigger_telegram_utc")
        )
        if (
            str(provider.get("record_type") or "") != "formal_signal"
            or str(provider.get("channel") or "") != "canal2"
            or provider.get("root_message_id") != source_message_id
            or str(provider.get("direction") or "").upper() != direction
            or str(provider.get("semantic_status") or "") != "complete"
            or provider.get("semantic_gaps") not in ([], ())
            or provider.get("canonicalization_issues") not in ([], ())
            or str(entry_contract.get("status") or "") != "ready"
            or entry_contract.get("blockers") not in ([], ())
            or entry_contract.get("trigger_message_id") != source_message_id
            or str(entry_contract.get("direction") or "").upper() != direction
            or provider_trigger_at != telegram_at
        ):
            blockers.add("reconstruction_provider_entry_contract_mismatch")
        for management in provider.get("management_events") or ():
            if not isinstance(management, Mapping):
                blockers.add("reconstruction_provider_management_invalid")
                continue
            management_at = _parse_utc(
                management.get("observed_ts_utc")
                or management.get("telegram_ts_utc")
            )
            if management_at is None:
                blockers.add("reconstruction_provider_management_invalid")
                continue
            if management_at >= end:
                continue
            if (
                str(management.get("modality") or "") != "informational"
                or bool(management.get("execution_options"))
            ):
                blockers.add(
                    "reconstruction_provider_management_requires_replay"
                )

        tick_evidence = None
        if not blockers:
            try:
                tick_evidence = tick_reader.registration_tick_evidence(
                    raw_server_msc=raw_server_msc,
                    observed_at_utc=watch_observed_at,
                    reference_bid=reference_bid,
                    reference_ask=reference_ask,
                )
            except (AttributeError, TypeError, ValueError):
                blockers.add("registration_tick_validation_unavailable")
            else:
                if not tick_evidence.complete:
                    blockers.update(
                        tick_evidence.blockers
                        or ("registration_tick_validation_failed",)
                    )

        normalized_msc = (
            None if tick_evidence is None
            else tick_evidence.normalized_time_msc
        )
        if not blockers and normalized_msc is not None and telegram_at is not None:
            for policy in gold_policies:
                record = existing.get(policy.candidate_id)
                if record is None:
                    continue
                state = _registration_state(record)
                if (
                    state is None
                    or str(record.get("state_hash") or "") != state.state_hash
                    or state.signal_id != signal_id
                    or state.source_message_id != source_message_id
                    or state.channel != "canal2"
                    or state.direction != direction
                    or _parse_utc(state.registered_at_utc) != telegram_at
                    or state.registered_tick_msc != normalized_msc
                    or state.reference_price is None
                    or not math.isclose(
                        state.reference_price,
                        reference_price,
                        rel_tol=0.0,
                        abs_tol=1e-9,
                    )
                    or state.strategy_fingerprint
                    != policy.strategy_fingerprint
                    or state.execution_fingerprint
                    != policy.execution_fingerprint
                ):
                    blockers.add(
                        "reconstruction_existing_registration_conflict"
                    )

        if blockers or normalized_msc is None or telegram_at is None:
            audits.append({
                "channel": "canal2",
                "signal_id": signal_id,
                "status": "blocked",
                "reconstructed_candidates": [],
                "blockers": sorted(blockers),
                "evidence_id": None,
                "source_commit": source_commit,
                "registered_tick_msc": normalized_msc,
            })
            continue

        evidence_id = canonical_hash({
            "schema_version": 1,
            "signal_id": signal_id,
            "source_commit": source_commit,
            "source_contract": trusted_contract,
            "catalog_manifest_hash": current_manifest["manifest_hash"],
            "runtime_event_id": runtime.get("event_id"),
            "signal_event_id": signal.get("event_id"),
            "watch_event_id": watch.get("event_id"),
            "provider_signal": provider,
            "tick_evidence_id": tick_evidence.evidence_id,
            "registered_tick_msc": normalized_msc,
            "reference_price": reference_price,
        })
        created_ids: list[str] = []
        for policy in missing:
            state = register_signal(
                policy,
                signal_id=signal_id,
                source_message_id=source_message_id,
                direction=direction,
                registered_at_utc=telegram_at.isoformat(),
                registered_tick_msc=normalized_msc,
                reference_price=reference_price,
            )
            event_payload = {
                "sig": signal_id,
                "ev": "strategy_shadow_registered",
                "channel": "canal2",
                "candidate_id": policy.candidate_id,
                "role": policy.role,
                "strategy_fingerprint": policy.strategy_fingerprint,
                "execution_fingerprint": policy.execution_fingerprint,
                "state_hash": state.state_hash,
                "previous_state_hash": None,
                "state": state.to_dict(),
                "registration_source": "reconstructed_from_upstream_evidence",
                "reconstruction_evidence_id": evidence_id,
                "message_revision_id": revision_id,
                "decision_id": decision_id,
                "schema_version": int(signal.get("schema_version") or 2),
                "session_id": session_id,
                "ts": str(watch.get("ts") or signal.get("ts")),
                "code_commit": source_commit,
            }
            event_id = "reconstructed_" + canonical_hash({
                "evidence_id": evidence_id,
                "candidate_id": policy.candidate_id,
            })[:32]
            event_payload["event_id"] = event_id
            event_payload["payload_sha256"] = canonical_hash(event_payload)
            reconstructed.append(event_payload)
            created_ids.append(policy.candidate_id)
        audits.append({
            "channel": "canal2",
            "signal_id": signal_id,
            "status": "reconstructed",
            "reconstructed_candidates": created_ids,
            "blockers": [],
            "evidence_id": evidence_id,
            "source_commit": source_commit,
            "registered_tick_msc": normalized_msc,
        })

    return tuple(reconstructed), tuple(audits)


def _management_events(
    records: Iterable[Mapping[str, Any]],
    signal_id: str,
    candidate_id: str,
) -> tuple[tuple[_ReplayManagement, ...], tuple[str, ...]]:
    by_id: dict[str, _ReplayManagement] = {}
    blockers: set[str] = set()
    for sequence, record in enumerate(records):
        if (
            str(record.get("sig") or "") != signal_id
            or str(record.get("candidate_id") or "") != candidate_id
            or str(record.get("ev") or "") != "strategy_shadow_transition"
            or str(record.get("transition") or "") not in _PROVIDER_TRANSITIONS
        ):
            continue
        state = _registration_state(record)
        if state is None:
            blockers.add("management_transition_invalid")
        processed = () if state is None else state.processed_management_ids
        event_id = str(processed[-1] if processed else "")
        observed = _parse_utc(record.get("ts"))
        try:
            observed_tick_msc = int(record.get("transition_tick_msc"))
        except (TypeError, ValueError):
            observed_tick_msc = (
                None if observed is None else int(observed.timestamp() * 1000)
            )
        if observed_tick_msc is not None and observed_tick_msc >= 0:
            observed = datetime.fromtimestamp(
                observed_tick_msc / 1000.0,
                tz=timezone.utc,
            )
        action = str(record.get("reason") or "").upper()
        if not event_id:
            event_id = canonical_hash({
                "signal_id": signal_id,
                "message_revision_id": record.get("message_revision_id"),
                "decision_id": record.get("decision_id"),
                "action": action,
                "ts": record.get("ts"),
            })
        if observed is None or not action:
            blockers.add("management_transition_time_missing")
            continue
        details = record.get("transition_details")
        price = details.get("price") if isinstance(details, Mapping) else None
        candidate = _ReplayManagement(
            event=ShadowManagementEvent(
                event_id=event_id,
                signal_id=signal_id,
                action=action,
                observed_at_utc=observed.isoformat(),
                observed_tick_msc=observed_tick_msc,
                price=None if price is None else float(price),
            ),
            transition=str(record.get("transition") or ""),
            after_tick_identity=(
                None if state is None else state.last_tick_identity
            ),
            source_sequence=sequence,
        )
        existing = by_id.get(event_id)
        if existing is None:
            by_id[event_id] = candidate
        elif (
            existing.event != candidate.event
            or existing.transition != candidate.transition
            or existing.after_tick_identity != candidate.after_tick_identity
        ):
            blockers.add("management_event_conflict")
    ordered = tuple(sorted(
        by_id.values(),
        key=lambda item: (
            item.event.observed_tick_msc or 0,
            item.source_sequence,
            item.event.event_id,
        ),
    ))
    return ordered, tuple(sorted(blockers))


def _blocked_row(
    *,
    signal_id: str,
    channel: str,
    policy,
    role: str,
    registered_at: datetime,
    blockers: Iterable[str],
    state: ShadowSignalState | None = None,
    registration_source: str | None = None,
    source_commit: str | None = None,
) -> dict[str, Any]:
    normalized = sorted(set(str(value) for value in blockers if str(value)))
    reason = normalized[0] if normalized else "settlement_incomplete"
    outcome = registered_at
    if state is not None and state.last_tick_identity is not None:
        outcome = datetime.fromtimestamp(
            int(state.last_tick_identity[0]) / 1000.0,
            tz=timezone.utc,
        )
    return {
        "channel": channel,
        "signal_id": signal_id,
        "candidate_id": policy.candidate_id,
        "role": role,
        "strategy_fingerprint": policy.strategy_fingerprint,
        "execution_fingerprint": policy.execution_fingerprint,
        "registration_source": registration_source,
        "source_commit": source_commit,
        "registered_at_utc": registered_at.isoformat(),
        "outcome_at_utc": outcome.isoformat(),
        "day": registered_at.date().isoformat(),
        "entry_count": 0 if state is None else len(state.positions),
        "exit_reason": reason,
        "status": "incomplete",
        "observed_status": None if state is None else state.status,
        "observed_exit_reason": None if state is None else state.exit_reason,
        "net_eur": None,
        "mfe_eur": None,
        "mae_eur": None,
        "complete": False,
        "evidence_blockers": normalized,
    }


def _result_row(
    state: ShadowSignalState,
    *,
    policy,
    role: str,
    horizon: datetime,
    lineage_complete: bool,
    registration_source: str,
    source_commit: str | None,
) -> dict[str, Any]:
    blockers = list(state.evidence_blockers)
    if not lineage_complete:
        blockers.append("candidate_registration_lineage_missing")
    if state.status not in {"closed", "cancelled"}:
        blockers.append("open_at_horizon")
    blockers = sorted(set(blockers))
    outcome = horizon
    if state.last_tick_identity is not None:
        outcome = datetime.fromtimestamp(
            int(state.last_tick_identity[0]) / 1000.0,
            tz=timezone.utc,
        )
    return {
        "channel": state.channel,
        "signal_id": state.signal_id,
        "candidate_id": state.candidate_id,
        "role": role,
        "strategy_fingerprint": state.strategy_fingerprint,
        "execution_fingerprint": state.execution_fingerprint,
        "registration_source": registration_source,
        "source_commit": source_commit,
        "registered_at_utc": state.registered_at_utc,
        "outcome_at_utc": outcome.isoformat(),
        "day": _parse_utc(state.registered_at_utc).date().isoformat(),
        "entry_count": len(state.positions),
        "exit_reason": state.exit_reason or (
            "open_at_horizon" if blockers else "settled"
        ),
        "status": state.status,
        "net_eur": round(state.realized_eur + state.floating_eur, 2),
        "mfe_eur": round(state.max_favourable_eur, 2),
        "mae_eur": round(state.max_adverse_eur, 2),
        "complete": state.complete and not blockers,
        "evidence_blockers": blockers,
        "logic_signature": shadow_logic_signature(state, policy),
    }


def actual_rows_from_ledger(
    ledger_rows: Iterable[Mapping[str, Any]],
    registration_records: Iterable[Mapping[str, Any]],
    *,
    since: date,
    until: date,
) -> list[dict[str, Any]]:
    """Build MT5 calibration evidence without pre-claiming mirror parity."""

    start, end = _window(since, until)
    source_records = [dict(record) for record in registration_records]
    registrations: dict[str, list[Mapping[str, Any]]] = {}
    for record in source_records:
        if str(record.get("ev") or "") != "strategy_shadow_registered":
            continue
        state = _registration_state(record)
        if state is not None:
            registrations.setdefault(state.signal_id, []).append(record)
    eligible_ids = set(registrations)
    eligible_ids.update(
        signal_id
        for _channel, signal_id in _eligible_signal_expectations(
            source_records,
            start=start,
            end=end,
        )
    )
    policies_by_id = {
        policy.candidate_id: policy
        for policies in build_shadow_catalog().values()
        for policy in policies
    }

    actual: list[dict[str, Any]] = []
    for source in ledger_rows:
        signal_id = str(source.get("sig_id") or source.get("signal_id") or "")
        channel = str(source.get("channel") or "")
        registered = _parse_utc(
            source.get("signal_dt_utc") or source.get("open_dt_utc")
        )
        if (
            signal_id not in eligible_ids
            or channel not in {"canal1", "canal2"}
            or registered is None
            or not (start <= registered < end)
        ):
            continue
        value = source.get("pnl_real_mt5")
        try:
            net_eur = float(value)
        except (TypeError, ValueError):
            net_eur = None
        else:
            if not math.isfinite(net_eur):
                net_eur = None
        positions = source.get("positions")
        if not isinstance(positions, list):
            positions = []
        reasons = sorted({
            str(position.get("close_reason") or "")
            for position in positions
            if isinstance(position, Mapping)
            and str(position.get("close_reason") or "")
        })
        lineage_complete = any(
            record.get("message_revision_id") and record.get("decision_id")
            for record in registrations.get(signal_id, ())
        )
        live_control_records = [
            record
            for record in registrations.get(signal_id, ())
            if str(record.get("role") or "") == "live_control"
            and _registration_state(record) is not None
        ]
        live_control_record = (
            live_control_records[0]
            if len(live_control_records) == 1
            else None
        )
        live_control_state = (
            _registration_state(live_control_record)
            if live_control_record is not None
            else None
        )
        strategy_snapshot = source.get("strategy_snapshot")
        strategy_id = (
            str(strategy_snapshot.get("live_strategy_id") or "")
            if isinstance(strategy_snapshot, Mapping)
            else ""
        )
        if not strategy_id and live_control_state is not None:
            strategy_id = live_control_state.candidate_id
        policy = policies_by_id.get(strategy_id)
        if policy is None:
            logic_signature = None
            logic_blockers = ["actual_strategy_identity_unknown"]
        else:
            signature_source = dict(source)
            if not isinstance(strategy_snapshot, Mapping):
                strategy_snapshot = {
                    "live_strategy_id": live_control_state.candidate_id,
                    "live_strategy_fingerprint": (
                        live_control_state.strategy_fingerprint
                    ),
                    "code_commit": live_control_record.get("code_commit"),
                }
                signature_source["strategy_snapshot"] = strategy_snapshot
            logic_signature, raw_logic_blockers = actual_logic_signature(
                signature_source, policy
            )
            logic_blockers = list(raw_logic_blockers)
        entry_count = int(source.get("n_positions") or len(positions))
        no_position_zero_exposure = bool(
            str(source.get("status") or "").strip().lower() == "no_position"
            and entry_count == 0
            and not positions
            and net_eur == 0.0
            and source.get("pnl_mt5_complete") is True
        )
        standard_reconciliation = bool(
            source.get("reconciled_ok") is True
            and source.get("pnl_mt5_complete") is True
        )
        row = {
            "channel": channel,
            "signal_id": signal_id,
            "day": registered.date().isoformat(),
            "entry_count": entry_count,
            "exit_reason": (
                "+".join(reasons)
                or str(source.get("status") or "unknown")
            ),
            "net_eur": net_eur,
            "logic_signature": logic_signature,
            "logic_signature_blockers": logic_blockers,
            "source_commit": (
                strategy_snapshot.get("code_commit")
                if isinstance(strategy_snapshot, Mapping)
                else None
            ),
            "telegram_lineage_complete": bool(lineage_complete),
            "mt5_reconciled": bool(
                standard_reconciliation or no_position_zero_exposure
            ),
            "reconciliation_basis": (
                "mt5_deal_reconciliation"
                if standard_reconciliation
                else "no_position_zero_exposure"
                if no_position_zero_exposure
                else "unverified"
            ),
        }
        actual.append(row)
    return sorted(
        actual,
        key=lambda row: (
            str(row.get("channel") or ""),
            str(row.get("signal_id") or ""),
            canonical_hash(row),
        ),
    )


def settle_shadow_records(
    records: Iterable[Mapping[str, Any]],
    *,
    tick_reader: ShadowTickReader,
    since: date,
    until: date,
    actual_rows: Iterable[Mapping[str, Any]] = (),
    provider_catalog: Mapping[str, Any] | None = None,
    trusted_source_commits: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Rebuild every frozen candidate from causal registration evidence."""

    source_records = [dict(record) for record in records]
    actual_list = sorted(
        (dict(row) for row in actual_rows),
        key=lambda row: (
            str(row.get("channel") or ""),
            str(row.get("signal_id") or ""),
            canonical_hash(row),
        ),
    )
    start, end = _window(since, until)
    catalog = build_shadow_catalog()
    reconstructed_records: tuple[dict[str, Any], ...] = ()
    reconstruction_audits: tuple[dict[str, Any], ...] = ()
    if provider_catalog is not None:
        reconstructed_records, reconstruction_audits = (
            reconstruct_registration_records(
                source_records,
                provider_catalog=provider_catalog,
                tick_reader=tick_reader,
                trusted_source_commits=trusted_source_commits or {},
                since=since,
                until=until,
            )
        )
        source_records.extend(reconstructed_records)
    registrations: dict[tuple[str, str], dict[str, Any]] = {}
    registration_conflicts: set[tuple[str, str]] = set()
    signal_registration: dict[tuple[str, str], datetime] = {}
    reconstruction_by_signal = {
        str(audit.get("signal_id") or ""): audit
        for audit in reconstruction_audits
    }

    for record in source_records:
        if str(record.get("ev") or "") != "strategy_shadow_registered":
            continue
        state = _registration_state(record)
        if state is None:
            continue
        registered = _parse_utc(state.registered_at_utc)
        if registered is None or not (start <= registered < end):
            continue
        signal_key = (state.channel, state.signal_id)
        key = (state.signal_id, state.candidate_id)
        signal_registration[signal_key] = min(
            registered,
            signal_registration.get(signal_key, registered),
        )
        existing = registrations.get(key)
        if existing is None:
            registrations[key] = record
        elif existing.get("state_hash") != record.get("state_hash"):
            registration_conflicts.add(key)

    expectations = _eligible_signal_expectations(
        source_records,
        start=start,
        end=end,
    )
    for signal_key, observed in expectations.items():
        signal_registration.setdefault(signal_key, observed)

    candidate_rows: list[dict[str, Any]] = []
    tick_evidence: dict[str, dict[str, Any]] = {}
    ordered_signals = sorted(
        signal_registration,
        key=lambda key: (signal_registration[key], key[0], key[1]),
    )

    for channel, signal_id in ordered_signals:
        policies = catalog[channel]
        registered_at = signal_registration[(channel, signal_id)]
        states: dict[str, ShadowSignalState] = {}
        roles: dict[str, str] = {}
        lineage: dict[str, bool] = {}
        registration_sources: dict[str, str] = {}
        registration_commits: dict[str, str | None] = {}
        blocked: dict[str, list[str]] = {}
        has_any_registration = any(
            (signal_id, policy.candidate_id) in registrations
            for policy in policies
        )

        for policy in policies:
            key = (signal_id, policy.candidate_id)
            record = registrations.get(key)
            if record is None:
                missing_blockers = [
                    "candidate_registration_missing"
                    if has_any_registration
                    else "signal_registration_missing"
                ]
                reconstruction = reconstruction_by_signal.get(signal_id)
                if reconstruction and reconstruction.get("status") == "blocked":
                    missing_blockers.extend(reconstruction.get("blockers") or ())
                blocked[policy.candidate_id] = missing_blockers
                roles[policy.candidate_id] = policy.role
                continue
            roles[policy.candidate_id] = str(record.get("role") or policy.role)
            registration_sources[policy.candidate_id] = str(
                record.get("registration_source") or "observed_runtime"
            )
            registration_commits[policy.candidate_id] = (
                str(record.get("code_commit"))
                if record.get("code_commit")
                else None
            )
            lineage[policy.candidate_id] = bool(
                record.get("message_revision_id") and record.get("decision_id")
            )
            state = _registration_state(record)
            reasons: list[str] = []
            if key in registration_conflicts:
                reasons.append("candidate_registration_conflict")
            if state is None:
                reasons.append("candidate_registration_invalid")
            else:
                if str(record.get("state_hash") or "") != state.state_hash:
                    reasons.append("candidate_registration_hash_mismatch")
                if (
                    str(record.get("sig") or "") != state.signal_id
                    or str(record.get("candidate_id") or "")
                    != state.candidate_id
                    or str(record.get("channel") or "") != state.channel
                ):
                    reasons.append("candidate_registration_identity_mismatch")
                if (
                    state.strategy_fingerprint != policy.strategy_fingerprint
                    or state.execution_fingerprint
                    != policy.execution_fingerprint
                    or record.get("strategy_fingerprint")
                    != policy.strategy_fingerprint
                    or record.get("execution_fingerprint")
                    != policy.execution_fingerprint
                ):
                    reasons.append("candidate_fingerprint_changed")
            if reasons:
                blocked[policy.candidate_id] = reasons
                continue
            states[policy.candidate_id] = register_signal(
                policy,
                signal_id=state.signal_id,
                source_message_id=state.source_message_id,
                direction=state.direction,
                registered_at_utc=state.registered_at_utc,
                registered_tick_msc=state.registered_tick_msc,
                reference_price=state.reference_price,
            )

        management: dict[str, tuple[_ReplayManagement, ...]] = {}
        management_index: dict[str, int] = {}
        seen_tick_identities: dict[str, set[tuple]] = {}
        for policy in policies:
            candidate_id = policy.candidate_id
            queue, queue_blockers = _management_events(
                source_records,
                signal_id,
                candidate_id,
            )
            management[candidate_id] = tuple(
                item
                for item in queue
                if _parse_utc(item.event.observed_at_utc) < end
            )
            management_index[candidate_id] = 0
            seen_tick_identities[candidate_id] = set()
            if queue_blockers:
                blocked.setdefault(candidate_id, []).extend(queue_blockers)

        registration_boundaries = {
            int(state.registered_tick_msc)
            if state.registered_tick_msc is not None
            else int(_parse_utc(state.registered_at_utc).timestamp() * 1000)
            for candidate_id, state in states.items()
            if candidate_id not in blocked
        }
        if len(registration_boundaries) > 1:
            for candidate_id in states:
                blocked.setdefault(candidate_id, []).append(
                    "candidate_registration_boundary_mismatch"
                )
        cursor_msc = (
            min(registration_boundaries)
            if registration_boundaries
            else int(registered_at.timestamp() * 1000)
        )
        cursor = datetime.fromtimestamp(cursor_msc / 1000.0, tz=timezone.utc)
        chunk_evidence: list[str] = []
        tick_count = 0
        tick_blockers: list[str] = []
        while cursor < end and any(
            policy.candidate_id not in blocked
            and states[policy.candidate_id].status not in _TERMINAL
            for policy in policies
        ):
            chunk_end = min(cursor + timedelta(hours=4), end)
            tick_read = tick_reader.read(cursor, chunk_end)
            chunk_evidence.append(tick_read.evidence_id)
            tick_count += len(tick_read.ticks)
            if not tick_read.complete:
                tick_blockers.extend(
                    tick_read.blockers or ("tick_history_incomplete",)
                )
                for policy in policies:
                    blocked.setdefault(policy.candidate_id, []).extend(
                        tick_blockers
                    )
                break
            cursor_msc = int(cursor.timestamp() * 1000)
            chunk_end_msc = int(chunk_end.timestamp() * 1000)
            for observed in tick_read.ticks:
                if not cursor_msc < observed.time_msc <= chunk_end_msc:
                    continue
                for policy in policies:
                    candidate_id = policy.candidate_id
                    state = states.get(candidate_id)
                    if state is None or state.status in _TERMINAL:
                        continue
                    queue = management[candidate_id]
                    index = management_index[candidate_id]
                    while index < len(queue):
                        item = queue[index]
                        anchor = item.after_tick_identity
                        due = (
                            _market_identity(anchor)
                            == _market_identity(state.last_tick_identity)
                            if anchor is not None
                            else int(item.event.observed_tick_msc or 0)
                            <= observed.time_msc
                        )
                        if not due:
                            break
                        state = apply_management(
                            policy,
                            state,
                            item.event,
                        ).state
                        index += 1
                    management_index[candidate_id] = index
                    states[candidate_id] = advance_tick(
                        policy, state, observed,
                    ).state
                    seen_tick_identities[candidate_id].add(
                        _market_identity(observed.identity)
                    )
                if all(
                    policy.candidate_id in blocked
                    or states[policy.candidate_id].status in _TERMINAL
                    for policy in policies
                ):
                    break
            # Management is asynchronous to prices. Preserve an event that
            # arrived after the final tick of this chunk even when the next
            # quote belongs to a later chunk (or never arrives before cutoff).
            for policy in policies:
                candidate_id = policy.candidate_id
                state = states.get(candidate_id)
                if state is None or state.status in _TERMINAL:
                    continue
                queue = management[candidate_id]
                index = management_index[candidate_id]
                while index < len(queue):
                    item = queue[index]
                    anchor = item.after_tick_identity
                    due = (
                        _market_identity(anchor)
                        == _market_identity(state.last_tick_identity)
                        if anchor is not None
                        else int(item.event.observed_tick_msc or 0)
                        <= chunk_end_msc
                    )
                    if not due:
                        break
                    state = apply_management(
                        policy,
                        state,
                        item.event,
                    ).state
                    index += 1
                management_index[candidate_id] = index
                states[candidate_id] = state
            cursor = chunk_end

        for policy in policies:
            candidate_id = policy.candidate_id
            if candidate_id in blocked or candidate_id not in states:
                continue
            unresolved = management[candidate_id][
                management_index[candidate_id]:
            ]
            state = states[candidate_id]
            terminal_tick_msc = (
                None
                if state.last_tick_identity is None
                else int(state.last_tick_identity[0])
            )
            material = [
                item
                for item in unresolved
                if (
                    item.transition in _MATERIAL_PROVIDER_TRANSITIONS
                    and (
                        state.status not in _TERMINAL
                        or terminal_tick_msc is None
                        or int(item.event.observed_tick_msc or 0)
                        <= terminal_tick_msc
                    )
                )
            ]
            if not material:
                continue
            if any(
                item.after_tick_identity is not None
                and _market_identity(item.after_tick_identity)
                not in seen_tick_identities[candidate_id]
                for item in material
            ):
                blocked.setdefault(candidate_id, []).append(
                    "management_anchor_missing"
                )
            else:
                blocked.setdefault(candidate_id, []).append(
                    "management_event_unapplied"
                )

        for policy in policies:
            candidate_id = policy.candidate_id
            state = states.get(candidate_id)
            if (
                state is None
                or candidate_id in blocked
                or state.status not in _TERMINAL
            ):
                continue
            try:
                cost_blockers = tick_reader.cost_blockers(state)
            except (AttributeError, TypeError, ValueError):
                cost_blockers = ("strategy_cost_validation_unavailable",)
            if cost_blockers:
                blocked.setdefault(candidate_id, []).extend(cost_blockers)
        tick_evidence[signal_id] = {
            "complete": not tick_blockers,
            "evidence_id": canonical_hash({
                "signal_id": signal_id,
                "chunks": chunk_evidence,
                "tick_count": tick_count,
                "blockers": sorted(set(tick_blockers)),
            }),
            "blockers": sorted(set(tick_blockers)),
            "tick_count": tick_count,
            "chunk_count": len(chunk_evidence),
        }

        for policy in policies:
            candidate_id = policy.candidate_id
            if candidate_id in blocked:
                candidate_rows.append(_blocked_row(
                    signal_id=signal_id,
                    channel=channel,
                    policy=policy,
                    role=roles.get(candidate_id, policy.role),
                    registered_at=registered_at,
                    blockers=blocked[candidate_id],
                    state=states.get(candidate_id),
                    registration_source=registration_sources.get(candidate_id),
                    source_commit=registration_commits.get(candidate_id),
                ))
                continue
            candidate_rows.append(_result_row(
                states[candidate_id],
                policy=policy,
                role=roles[candidate_id],
                horizon=end,
                lineage_complete=lineage.get(candidate_id, False),
                registration_source=registration_sources[candidate_id],
                source_commit=registration_commits.get(candidate_id),
            ))

    report = build_report(candidate_rows, actual_list)
    evidence = {
        "schema_version": 1,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "candidate_rows": candidate_rows,
        "actual_rows": actual_list,
        "tick_evidence": tick_evidence,
        "registration_reconstructions": list(reconstruction_audits),
        "report": report,
    }
    return {
        **evidence,
        "settlement_hash": canonical_hash(evidence),
    }
