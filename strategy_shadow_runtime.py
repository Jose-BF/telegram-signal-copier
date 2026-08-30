"""Failure-isolated coordinator for prospective strategy shadows."""

from __future__ import annotations

import asyncio
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
        self._engine_advance = engine_advance
        self._checkpoint_seconds = int(checkpoint_seconds)
        self._slowdown_threshold_ms = float(slowdown_threshold_ms)
        self._states: dict[tuple[str, str], ShadowSignalState] = {}
        self._disabled_candidates: set[str] = set()
        self._degradation_reported: set[str] = set()
        self._persisted_state_hashes: dict[tuple[str, str], str] = {}
        self._last_checkpoint_at: datetime | None = None
        self._lock = asyncio.Lock()

    async def _emit(self, signal_id: str, event: str, **fields) -> None:
        await _resolve(self._journal_sink(signal_id, event, **fields))

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
            if state.last_tick_identity is not None:
                cursors.append(ShadowTickCursor(
                    from_msc=int(state.last_tick_identity[0]),
                    after_identity=state.last_tick_identity,
                ))
                continue
            registered_msc = state.registered_tick_msc
            if registered_msc is None:
                registered_msc = int(
                    _utc_datetime(state.registered_at_utc).timestamp() * 1000
                )
            cursors.append(ShadowTickCursor(
                from_msc=int(registered_msc),
                after_identity=None,
            ))
        if not cursors:
            return None
        return min(
            cursors,
            key=lambda cursor: (
                cursor.from_msc,
                0 if cursor.after_identity is not None else 1,
                cursor.after_identity or (),
            ),
        )

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
                        policy, previous, advanced, tick=None,
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
            if state.status in {"closed", "cancelled", "incomplete"}:
                continue
            key = (state.signal_id, state.candidate_id)
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
        }
        restored: dict[tuple[str, str], ShadowSignalState] = {}
        expected_hashes: dict[tuple[str, str], str | None] = {}
        corrupt: set[tuple[str, str]] = set()
        identity_corrupt: set[tuple[str, str]] = set()
        malformed: set[tuple[str, str]] = set()

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
                    policy = self._policy(state.candidate_id)
                    if (
                        state.strategy_fingerprint != policy.strategy_fingerprint
                        or state.execution_fingerprint != policy.execution_fingerprint
                    ):
                        raise ValueError("candidate fingerprint mismatch")
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
                if state.last_tick_identity is not None:
                    cursor = ShadowTickCursor(
                        from_msc=int(state.last_tick_identity[0]),
                        after_identity=state.last_tick_identity,
                    )
                else:
                    registered_msc = state.registered_tick_msc
                    if registered_msc is None:
                        registered_msc = int(
                            _utc_datetime(state.registered_at_utc).timestamp()
                            * 1000
                        )
                    cursor = ShadowTickCursor(
                        from_msc=int(registered_msc),
                        after_identity=None,
                    )
                history = await _resolve(history_reader(cursor))
                if not isinstance(history, ShadowTickHistory):
                    raise TypeError("history_reader must return ShadowTickHistory")
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
                await self._emit(
                    state.signal_id,
                    "strategy_shadow_recovered",
                    candidate_id=state.candidate_id,
                    strategy_fingerprint=state.strategy_fingerprint,
                    execution_fingerprint=state.execution_fingerprint,
                    status=state.status,
                    complete=state.complete,
                    blockers=list(state.evidence_blockers),
                    state_hash=state.state_hash,
                    previous_state_hash=previous_hash,
                    state=state.to_dict(),
                )
                self._persisted_state_hashes[key] = state.state_hash
        return tuple(self._states.values())
