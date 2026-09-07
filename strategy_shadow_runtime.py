"""Failure-isolated coordinator for prospective strategy shadows."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime
import inspect
import time
from typing import Awaitable, Callable, Iterable, Mapping, Sequence

from strategy_shadow_catalog import build_shadow_catalog, policy_by_id
from strategy_shadow_contracts import (
    ShadowAdvance,
    ShadowManagementEvent,
    ShadowPolicy,
    ShadowSignalState,
    ShadowTick,
)
from strategy_shadow_engine import advance_tick, apply_management, register_signal


JournalSink = Callable[..., object]
HistoryReader = Callable[
    ["ShadowTickCursor"],
    "ShadowTickHistory | Awaitable[ShadowTickHistory]",
]


_installed_runtime: "ShadowRuntime | None" = None


def install_runtime(runtime: "ShadowRuntime | None") -> None:
    global _installed_runtime
    _installed_runtime = runtime


def installed_runtime() -> "ShadowRuntime | None":
    return _installed_runtime


@dataclass(frozen=True)
class ShadowTickHistory:
    ticks: tuple[ShadowTick, ...]
    complete: bool
    evidence_id: str
    blocker: str | None = None
    pending_reason: str | None = None

    def __post_init__(self) -> None:
        timestamps = [item.time_msc for item in self.ticks]
        if timestamps != sorted(timestamps):
            raise ValueError("history ticks must be ordered")
        if not self.evidence_id:
            raise ValueError("history evidence_id is required")
        if self.complete and self.blocker is not None:
            raise ValueError("complete history cannot have a blocker")
        if not self.complete and self.pending_reason is not None:
            raise ValueError("incomplete history cannot expose a pending prefix")


@dataclass(frozen=True)
class ShadowTickCursor:
    from_msc: int
    after_identity: tuple[int, float, float, float, int, float] | None

    def __post_init__(self) -> None:
        if int(self.from_msc) < 0:
            raise ValueError("from_msc must be non-negative")
        if (
            self.after_identity is not None
            and int(self.after_identity[0]) != int(self.from_msc)
        ):
            raise ValueError("cursor identity timestamp must match from_msc")


async def _resolve(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("runtime timestamp must include timezone")
    return parsed


class ShadowRuntime:
    def __init__(
        self,
        *,
        catalog: Mapping[str, tuple[ShadowPolicy, ...]] | None = None,
        journal_sink: JournalSink | None = None,
        journal_confirmer: Callable[[object], object] | None = None,
        engine_advance: Callable[
            [ShadowPolicy, ShadowSignalState, ShadowTick], ShadowAdvance
        ] = advance_tick,
        checkpoint_seconds: int = 300,
        slowdown_threshold_ms: float = 20.0,
    ) -> None:
        if checkpoint_seconds <= 0:
            raise ValueError("checkpoint_seconds must be positive")
        if slowdown_threshold_ms <= 0:
            raise ValueError("slowdown_threshold_ms must be positive")
        self._catalog = catalog or build_shadow_catalog()
        self._journal_sink = journal_sink or (lambda *_args, **_kwargs: None)
        self._journal_confirmer = journal_confirmer
        self._engine_advance = engine_advance
        self._checkpoint_seconds = int(checkpoint_seconds)
        self._slowdown_threshold_ms = float(slowdown_threshold_ms)
        self._states: dict[tuple[str, str], ShadowSignalState] = {}
        self._disabled_candidates: set[str] = set()
        self._degradation_reported: set[str] = set()
        self._persisted_state_hashes: dict[tuple[str, str], str] = {}
        self._pending_receipts: dict[tuple[str, str], deque[object]] = {}
        self._receipt_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._last_selected_cursor: ShadowTickCursor | None = None
        self._quarantining_keys: set[tuple[str, str]] = set()
        self._last_checkpoint_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def _emit(self, signal_id: str, event: str, **fields):
        receipt = await _resolve(self._journal_sink(signal_id, event, **fields))
        if fields.get("state_hash") and fields.get("candidate_id") and (
            receipt is not None or self._journal_confirmer is not None
        ):
            key = (str(signal_id), str(fields["candidate_id"]))
            self._pending_receipts.setdefault(key, deque()).append(receipt)
        return receipt

    async def _confirm_pending(self, key: tuple[str, str]) -> None:
        async with self._receipt_locks.setdefault(key, asyncio.Lock()):
            pending = self._pending_receipts.get(key)
            while pending:
                receipt = pending[0]
                if self._journal_confirmer is None:
                    raise RuntimeError("shadow journal receipt requires a confirmer")
                confirmed = await _resolve(self._journal_confirmer(receipt))
                if confirmed is not True:
                    raise RuntimeError("shadow journal write not confirmed")
                pending.popleft()
            self._pending_receipts.pop(key, None)

    async def _emit_confirmed(self, signal_id: str, event: str, **fields) -> None:
        key = (str(signal_id), str(fields["candidate_id"]))
        await self._confirm_pending(key)
        await self._emit(signal_id, event, **fields)
        await self._confirm_pending(key)

    def _policy(self, candidate_id: str) -> ShadowPolicy:
        for policies in self._catalog.values():
            for policy in policies:
                if policy.candidate_id == candidate_id:
                    return policy
        return policy_by_id(candidate_id)

    def active_candidate_ids(self) -> set[str]:
        return {
            state.candidate_id
            for state in self._states.values()
            if state.status not in {"closed", "cancelled", "incomplete"}
        }

    def active_tick_cursor(self) -> ShadowTickCursor | None:
        cursors: list[ShadowTickCursor] = []
        for state in self._states.values():
            if state.status in {"closed", "cancelled", "incomplete"}:
                continue
            cursors.append(self._state_tick_cursor(state))
        if not cursors:
            return None
        earliest_msc = min(cursor.from_msc for cursor in cursors)
        oldest = list(dict.fromkeys(c for c in cursors if c.from_msc == earliest_msc))
        # Prices do not encode order within a millisecond. Give each cursor its
        # own archive query, including when another cohort already equals latest.
        index = 0
        if self._last_selected_cursor in oldest:
            index = (oldest.index(self._last_selected_cursor) + 1) % len(oldest)
        self._last_selected_cursor = oldest[index]
        return oldest[index]

    @staticmethod
    def _state_tick_cursor(state: ShadowSignalState) -> ShadowTickCursor:
        if state.last_tick_identity is not None:
            return ShadowTickCursor(int(state.last_tick_identity[0]), state.last_tick_identity)
        registered_msc = state.registered_tick_msc
        if registered_msc is None:
            registered_msc = int(_utc_datetime(state.registered_at_utc).timestamp() * 1000)
        return ShadowTickCursor(int(registered_msc), None)

    async def quarantine_tick_cursor(
        self,
        cursor: ShadowTickCursor,
        *,
        history: ShadowTickHistory,
        consecutive_failures: int = 0,
    ) -> tuple[ShadowSignalState, ...]:
        if history.complete or history.blocker not in {
            "historical_tick_cursor_unavailable", "historical_tick_cursor_ambiguous",
        }:
            raise ValueError("only a missing or ambiguous historical cursor can be quarantined")
        affected: list[ShadowSignalState] = []
        async with self._lock:
            keys = tuple(self._states)
        for key in keys:
            # Slow storage must not keep an unrelated live entry waiting for
            # the observation lock. Reserve only the failed pair while writing.
            await self._confirm_pending(key)
            async with self._lock:
                state = self._states[key]
                if state.status in {"closed", "cancelled", "incomplete"}:
                    continue
                if key in self._quarantining_keys or self._state_tick_cursor(state) != cursor:
                    continue
                self._quarantining_keys.add(key)
                incomplete = self._mark_incomplete(state, "tick_gap")
                incomplete = self._mark_incomplete(incomplete, history.blocker)
            try:
                await self._emit(
                    state.signal_id, "strategy_shadow_tick_gap",
                    channel=state.channel, candidate_id=state.candidate_id,
                    strategy_fingerprint=state.strategy_fingerprint,
                    execution_fingerprint=state.execution_fingerprint,
                    evidence_id=history.evidence_id, blocker=history.blocker,
                    consecutive_failures=consecutive_failures,
                    cursor_from_msc=cursor.from_msc,
                    cursor_after_identity=cursor.after_identity,
                    previous_state_hash=self._persisted_state_hashes.get(key, state.state_hash),
                    state_hash=incomplete.state_hash, state=incomplete.to_dict(),
                )
                await self._confirm_pending(key)
                async with self._lock:
                    self._states[key] = incomplete
                    self._persisted_state_hashes[key] = incomplete.state_hash
                    affected.append(incomplete)
            finally:
                self._quarantining_keys.discard(key)
        return tuple(affected)

    def earliest_active_tick_identity(
        self,
    ) -> tuple[int, float, float, float, int, float] | None:
        cursor = self.active_tick_cursor()
        return None if cursor is None else cursor.after_identity

    def status(self, candidate_id: str) -> str:
        return (
            "disabled"
            if candidate_id in self._disabled_candidates
            else "running"
        )

    def state(
        self,
        signal_id: str,
        candidate_id: str,
    ) -> ShadowSignalState:
        return self._states[(str(signal_id), str(candidate_id))]

    def states_for_signal(self, signal_id: str) -> tuple[ShadowSignalState, ...]:
        return tuple(
            state
            for (stored_signal, _candidate), state in self._states.items()
            if stored_signal == str(signal_id)
        )

    async def register_signal(
        self,
        *,
        channel: str,
        signal_id: str,
        source_message_id: int,
        direction: str,
        registered_at_utc: str,
        registered_tick_msc: int | None,
        reference_price: float | None = None,
    ) -> tuple[ShadowSignalState, ...]:
        policies = self._catalog.get(str(channel), ())
        if not policies:
            return ()
        created: list[ShadowSignalState] = []
        async with self._lock:
            for policy in policies:
                key = (str(signal_id), policy.candidate_id)
                if key in self._states or policy.candidate_id in self._disabled_candidates:
                    continue
                try:
                    state = register_signal(
                        policy,
                        signal_id=str(signal_id),
                        source_message_id=int(source_message_id),
                        direction=direction,
                        registered_at_utc=registered_at_utc,
                        registered_tick_msc=registered_tick_msc,
                        reference_price=reference_price,
                    )
                    self._states[key] = state
                    created.append(state)
                    await self._emit(
                        state.signal_id,
                        "strategy_shadow_registered",
                        channel=state.channel,
                        candidate_id=state.candidate_id,
                        role=policy.role,
                        strategy_fingerprint=state.strategy_fingerprint,
                        execution_fingerprint=state.execution_fingerprint,
                        state_hash=state.state_hash,
                        previous_state_hash=None,
                        state=state.to_dict(),
                    )
                    self._persisted_state_hashes[key] = state.state_hash
                except Exception as exc:
                    await self._disable_candidate(
                        policy.candidate_id,
                        signal_id=str(signal_id),
                        error=exc,
                    )
        return tuple(created)

    async def _disable_candidate(
        self,
        candidate_id: str,
        *,
        signal_id: str,
        error: Exception,
    ) -> None:
        self._disabled_candidates.add(candidate_id)
        affected = [
            (key, state)
            for key, state in self._states.items()
            if state.candidate_id == candidate_id
            and key not in self._quarantining_keys
        ]
        for key, state in affected:
            blockers = state.evidence_blockers
            if "candidate_exception" not in blockers:
                blockers = blockers + ("candidate_exception",)
            incomplete = replace(
                state,
                status="incomplete",
                complete=False,
                evidence_blockers=blockers,
                exit_reason="candidate_exception",
            )
            self._states[key] = incomplete
            await self._emit(
                state.signal_id,
                "strategy_shadow_candidate_disabled",
                candidate_id=candidate_id,
                strategy_fingerprint=state.strategy_fingerprint,
                execution_fingerprint=state.execution_fingerprint,
                error_type=type(error).__name__,
                error_message=str(error)[:500],
                state_hash=incomplete.state_hash,
                previous_state_hash=self._persisted_state_hashes.get(
                    key, state.state_hash,
                ),
                state=incomplete.to_dict(),
            )
            self._persisted_state_hashes[key] = incomplete.state_hash
        if not affected:
            await self._emit(
                signal_id,
                "strategy_shadow_candidate_disabled",
                candidate_id=candidate_id,
                error_type=type(error).__name__,
                error_message=str(error)[:500],
            )

    async def _record_advance(
        self,
        policy: ShadowPolicy,
        previous: ShadowSignalState,
        advanced: ShadowAdvance,
        tick: ShadowTick | None,
        management_event: ShadowManagementEvent | None = None,
    ) -> None:
        key = (advanced.state.signal_id, policy.candidate_id)
        chain_hash = self._persisted_state_hashes.get(
            key, previous.state_hash,
        )
        for transition in advanced.transitions:
            await self._emit(
                advanced.state.signal_id,
                "strategy_shadow_transition",
                channel=advanced.state.channel,
                candidate_id=policy.candidate_id,
                strategy_fingerprint=policy.strategy_fingerprint,
                execution_fingerprint=policy.execution_fingerprint,
                policy_schema_version=policy.schema_version,
                management_event=(None if management_event is None else management_event.to_dict()),
                transition=transition.event,
                reason=transition.reason,
                transition_tick_msc=transition.tick_msc,
                decision_state_hash=transition.state_hash,
                transition_details=dict(transition.details),
                tick=(None if tick is None else tick.to_dict()),
                state_hash=advanced.state.state_hash,
                previous_state_hash=chain_hash,
                state=advanced.state.to_dict(),
            )
            chain_hash = advanced.state.state_hash
            self._persisted_state_hashes[key] = chain_hash

    async def process_tick(self, tick: ShadowTick) -> None:
        async with self._lock:
            await self._process_tick_locked(tick)
        for key in tuple(self._pending_receipts):
            await self._confirm_pending(key)

    async def process_tick_batch(
        self,
        cursor: ShadowTickCursor,
        history: ShadowTickHistory,
    ) -> None:
        if not history.complete:
            raise ValueError("tick batches require complete history")
        async with self._lock:
            keys = tuple(
                key for key, state in self._states.items()
                if state.status not in {"closed", "cancelled", "incomplete"}
                and key not in self._quarantining_keys
                and self._state_tick_cursor(state) == cursor
            )
        for key in keys:
            await self._confirm_pending(key)
        for observed in history.ticks:
            await asyncio.sleep(0)
            async with self._lock:
                for key in keys:
                    await self._process_tick_for_key_locked(key, observed)
                await self._checkpoint_if_due(observed.observed_at_utc)
        if keys and history.ticks:
            last = history.ticks[-1]
            self._last_selected_cursor = ShadowTickCursor(last.time_msc, last.identity)
        # Receipt waits stay outside the live bridge's state lock.
        for key in tuple(self._pending_receipts):
            await self._confirm_pending(key)

    async def _process_tick_locked(self, tick: ShadowTick) -> None:
        for key in tuple(self._states):
            await self._process_tick_for_key_locked(key, tick)
        await self._checkpoint_if_due(tick.observed_at_utc)

    async def _process_tick_for_key_locked(
        self,
        key: tuple[str, str],
        tick: ShadowTick,
    ) -> None:
        previous = self._states[key]
        if (
            previous.status in {"closed", "cancelled", "incomplete"}
            or previous.candidate_id in self._disabled_candidates
            or key in self._quarantining_keys
        ):
            return
        policy = self._policy(previous.candidate_id)
        started = time.perf_counter()
        try:
            advanced = self._engine_advance(policy, previous, tick)
        except Exception as exc:
            await self._disable_candidate(
                previous.candidate_id,
                signal_id=previous.signal_id,
                error=exc,
            )
            return
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._states[key] = advanced.state
        if advanced.transitions:
            await self._record_advance(policy, previous, advanced, tick)
        if (
            elapsed_ms > self._slowdown_threshold_ms
            and previous.candidate_id not in self._degradation_reported
        ):
            self._degradation_reported.add(previous.candidate_id)
            await self._emit(
                previous.signal_id,
                "strategy_shadow_degraded",
                candidate_id=previous.candidate_id,
                elapsed_ms=round(elapsed_ms, 3),
                threshold_ms=self._slowdown_threshold_ms,
            )

    async def process_management(
        self,
        event: ShadowManagementEvent,
    ) -> tuple[ShadowSignalState, ...]:
        changed: list[ShadowSignalState] = []
        async with self._lock:
            for key in tuple(self._states):
                previous = self._states[key]
                if (
                    previous.signal_id != event.signal_id
                    or previous.status in {"closed", "cancelled", "incomplete"}
                    or previous.candidate_id in self._disabled_candidates
                    or key in self._quarantining_keys
                ):
                    continue
                policy = self._policy(previous.candidate_id)
                try:
                    advanced = apply_management(policy, previous, event)
                except Exception as exc:
                    await self._disable_candidate(
                        previous.candidate_id,
                        signal_id=previous.signal_id,
                        error=exc,
                    )
                    continue
                self._states[key] = advanced.state
                if advanced.state != previous:
                    changed.append(advanced.state)
                if advanced.transitions:
                    await self._record_advance(
                        policy, previous, advanced, tick=None, management_event=event,
                    )
        return tuple(changed)

    async def _checkpoint_if_due(self, observed_at_utc: str) -> None:
        observed = _utc_datetime(observed_at_utc)
        if self._last_checkpoint_at is None:
            self._last_checkpoint_at = observed
            return
        if (
            observed - self._last_checkpoint_at
        ).total_seconds() < self._checkpoint_seconds:
            return
        self._last_checkpoint_at = observed
        for state in self._states.values():
            key = (state.signal_id, state.candidate_id)
            if state.status in {"closed", "cancelled", "incomplete"}:
                continue
            if key in self._quarantining_keys:
                continue
            await self._emit(
                state.signal_id,
                "strategy_shadow_checkpoint",
                channel=state.channel,
                candidate_id=state.candidate_id,
                strategy_fingerprint=state.strategy_fingerprint,
                execution_fingerprint=state.execution_fingerprint,
                state_hash=state.state_hash,
                previous_state_hash=self._persisted_state_hashes.get(
                    key, state.state_hash,
                ),
                state=state.to_dict(),
            )
            self._persisted_state_hashes[key] = state.state_hash

    @staticmethod
    def _mark_incomplete(
        state: ShadowSignalState,
        blocker: str,
    ) -> ShadowSignalState:
        blockers = state.evidence_blockers
        if blocker not in blockers:
            blockers = blockers + (blocker,)
        return replace(
            state,
            status="incomplete",
            complete=False,
            evidence_blockers=blockers,
            exit_reason=blocker,
        )

    async def recover(
        self,
        records: Sequence[Mapping[str, object]],
        *,
        history_reader: HistoryReader,
    ) -> tuple[ShadowSignalState, ...]:
        recoverable_events = {
            "strategy_shadow_registered",
            "strategy_shadow_transition",
            "strategy_shadow_checkpoint",
            "strategy_shadow_candidate_disabled",
            "strategy_shadow_recovered",
            "strategy_shadow_tick_gap",
        }
        restored: dict[tuple[str, str], ShadowSignalState] = {}
        expected_hashes: dict[tuple[str, str], str | None] = {}
        corrupt: set[tuple[str, str]] = set()
        identity_corrupt: set[tuple[str, str]] = set()
        malformed: set[tuple[str, str]] = set()
        incompatible: dict[tuple[str, str], str] = {}
        recovery_evidence: dict[tuple[str, str], dict[str, object]] = {}

        async with self._lock:
            for record in records:
                if str(record.get("ev") or "") not in recoverable_events:
                    continue
                payload = record.get("state")
                candidate_id = str(record.get("candidate_id") or "")
                signal_id = str(record.get("sig") or "")
                if not isinstance(payload, Mapping) or not candidate_id or not signal_id:
                    continue
                try:
                    state = ShadowSignalState.from_dict(payload)
                    key = (state.signal_id, state.candidate_id)
                except Exception:
                    malformed.add((signal_id, candidate_id))
                    continue
                if (
                    signal_id != state.signal_id
                    or candidate_id != state.candidate_id
                    or (
                        record.get("channel") is not None
                        and str(record.get("channel")) != state.channel
                    )
                ):
                    identity_corrupt.add(key)
                recorded_hash = str(record.get("state_hash") or "")
                previous_hash = record.get("previous_state_hash")
                expected_previous = expected_hashes.get(key)
                if recorded_hash != state.state_hash:
                    corrupt.add(key)
                legacy_self_link = (
                    str(record.get("ev") or "")
                    == "strategy_shadow_checkpoint"
                    and previous_hash == recorded_hash
                    and recorded_hash == state.state_hash
                )
                if (
                    expected_previous is not None
                    and previous_hash != expected_previous
                    and not legacy_self_link
                ):
                    corrupt.add(key)
                restored[key] = state
                expected_hashes[key] = state.state_hash

            # Validate the original journal before comparing it with today's catalog.
            for key, state in restored.items():
                recovery_evidence[key] = {
                    "source_state_hash": state.state_hash,
                    "source_status": state.status,
                    "source_exit_reason": state.exit_reason,
                }
                try:
                    policy = self._policy(state.candidate_id)
                except KeyError:
                    incompatible[key] = "candidate_policy_unavailable"
                    continue
                recovery_evidence[key].update({
                    "expected_strategy_fingerprint": policy.strategy_fingerprint,
                    "expected_execution_fingerprint": policy.execution_fingerprint,
                })
                if state.channel != policy.channel:
                    identity_corrupt.add(key)
                if state.strategy_fingerprint != policy.strategy_fingerprint:
                    incompatible[key] = "strategy_contract_mismatch"
                elif state.execution_fingerprint != policy.execution_fingerprint:
                    incompatible[key] = "execution_contract_mismatch"

            for key in corrupt:
                if key in restored:
                    restored[key] = self._mark_incomplete(
                        restored[key], "journal_hash_mismatch",
                    )
            for key in identity_corrupt:
                if key in restored:
                    restored[key] = self._mark_incomplete(
                        restored[key], "journal_identity_mismatch",
                    )
            for key in malformed:
                if key in restored:
                    restored[key] = self._mark_incomplete(
                        restored[key], "journal_state_invalid",
                    )
            for key, blocker in incompatible.items():
                restored[key] = self._mark_incomplete(restored[key], blocker)
            self._states.update(restored)
            self._persisted_state_hashes.update({
                key: state_hash
                for key, state_hash in expected_hashes.items()
                if state_hash is not None
            })

            pending = [
                (key, state)
                for key, state in restored.items()
                if state.status not in {"closed", "cancelled", "incomplete"}
            ]
            for key, state in pending:
                cursor = self._state_tick_cursor(state)
                try:
                    history = await _resolve(history_reader(cursor))
                    if not isinstance(history, ShadowTickHistory):
                        raise TypeError("history_reader must return ShadowTickHistory")
                except Exception as exc:
                    self._states[key] = self._mark_incomplete(
                        self._states[key], "history_reader_exception",
                    )
                    recovery_evidence[key]["history_error_type"] = type(exc).__name__
                    continue
                recovery_evidence[key]["history_evidence_id"] = history.evidence_id
                if not history.complete:
                    incomplete = self._mark_incomplete(
                        self._states[key], "tick_gap",
                    )
                    if history.blocker:
                        incomplete = self._mark_incomplete(
                            incomplete, history.blocker,
                        )
                    self._states[key] = incomplete
                else:
                    for tick_index, observed_tick in enumerate(
                        history.ticks, start=1,
                    ):
                        await self._process_tick_for_key_locked(
                            key, observed_tick,
                        )
                        if tick_index % 256 == 0:
                            await asyncio.sleep(0)

            for key, state in self._states.items():
                previous_hash = self._persisted_state_hashes.get(
                    key, state.state_hash,
                )
                await self._emit_confirmed(
                    state.signal_id,
                    "strategy_shadow_recovered",
                    channel=state.channel,
                    candidate_id=state.candidate_id,
                    strategy_fingerprint=state.strategy_fingerprint,
                    execution_fingerprint=state.execution_fingerprint,
                    status=state.status,
                    complete=state.complete,
                    blockers=list(state.evidence_blockers),
                    recovery_schema_version=2,
                    recovery_evidence=recovery_evidence.get(key, {}),
                    state_hash=state.state_hash,
                    previous_state_hash=previous_hash,
                    state=state.to_dict(),
                )
                self._persisted_state_hashes[key] = state.state_hash
        return tuple(self._states.values())
