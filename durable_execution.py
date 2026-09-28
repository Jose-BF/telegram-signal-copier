"""Application-facing durable execution boundary for MT5 broker effects."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
from functools import partial
from enum import Enum
import sqlite3
from typing import Any, Mapping
import uuid

from entry_history_reconciliation import EntryHistoryBlocked
from execution_intents import CurrentIntentSnapshot, EffectProjection, IntentRecord, ReconciliationConflictError
from mt5_protocol import BrokerOutcome, BrokerRequest, IntentKey, IntentState, LookupState
from mt5_trade_protocol import ENTRY_OPERATIONS
from mt5_read_protocol import MAX_HISTORY_SECONDS, ReadOperation, ReadRequest, finite_number


async def run_storage_call(service, operation, *args, **kwargs):
    """Await durable I/O without occupying the application's event-loop thread."""
    client = getattr(service, "client", None)
    executor = getattr(client, "_storage_executor", None)
    return await asyncio.get_running_loop().run_in_executor(
        executor, partial(operation, *args, **kwargs),
    )


class ExecutionDisposition(str, Enum):
    NOT_SENT = "NOT_SENT"
    RECONCILE = "RECONCILE"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


class EntryEvidenceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DurableExecutionResult:
    record: IntentRecord
    disposition: ExecutionDisposition
    effect: Mapping[str, Any] | None
    predispatch_error: str | None = None


@dataclass(frozen=True)
class EntryReinspectionResult:
    record: IntentRecord
    recovered: bool
    reason: str | None = None


class DurableExecutionService:
    """Maps causal application operations onto the durable MT5 client."""

    def __init__(self, client):
        if getattr(client, "store", None) is None:
            raise ValueError("durable execution client must have an intent store")
        self.client = client
        self.store = client.store
        self.entry_guard = None
        self.entry_evidence_probe = None

    def request(
        self,
        *,
        channel: str,
        signal_root: str,
        generation: int,
        leg: str,
        operation: str,
        revision: int,
        payload: Mapping[str, Any],
        action_id: str | None = None,
        request_id: str | None = None,
        attempt_id: str | None = None,
    ) -> BrokerRequest:
        config = self.client.config
        account_fingerprint = f"{config.expected_server}/{config.expected_login}"
        key = IntentKey(
            account_fingerprint,
            channel,
            signal_root,
            generation,
            leg,
            operation,
            revision,
        )
        return BrokerRequest.create(
            key,
            payload,
            action_id=action_id or uuid.uuid4().hex,
            request_id=request_id or uuid.uuid4().hex,
            attempt_id=attempt_id or uuid.uuid4().hex,
        )

    async def execute(
        self,
        request: BrokerRequest,
        *,
        reservation_key: str,
        projection_key: str,
        expires_utc: str | None = None,
        timeout: float = 2.0,
        dispatch_guard=None,
        release_on_terminal: bool = False,
    ) -> DurableExecutionResult:
        finite_number(timeout, "timeout", positive=True)
        effective_guard = dispatch_guard
        is_entry = request.intent_key.operation in ENTRY_OPERATIONS
        evidence_confirmed = True
        if is_entry and self.entry_evidence_probe is not None:
            try:
                async with asyncio.timeout(timeout):
                    evidence_confirmed = await self.entry_evidence_probe(request, timeout=timeout)
            except Exception:
                evidence_confirmed = False
        if is_entry and (self.entry_guard is not None or self.entry_evidence_probe is not None):
            entry_guard = self.entry_guard

            def effective_guard(current_request):
                if evidence_confirmed is not True:
                    raise EntryEvidenceUnavailable("entry evidence was not durably confirmed")
                if entry_guard is not None:
                    entry_guard(current_request)
                if dispatch_guard is not None:
                    dispatch_guard(current_request)

        record = await self.client.execute(
            request,
            reservation_key=reservation_key,
            policy_revision=request.intent_key.revision,
            expires_utc=expires_utc,
            timeout=timeout,
            dispatch_guard=effective_guard,
            projection_key=projection_key,
            release_on_terminal=release_on_terminal,
        )
        predispatch_error = None
        if record.state is IntentState.PREPARED:
            predispatch_error = await run_storage_call(self, self.store.get_predispatch_failure, request.request_id)
        return await run_storage_call(
            self, self._project,
            request,
            record,
            projection_key=projection_key,
            release_on_terminal=release_on_terminal,
            predispatch_error=predispatch_error,
        )

    async def retry_rejected(
        self,
        previous_request: BrokerRequest,
        *,
        retryable_retcodes: set[int] | frozenset[int],
        reason: str,
        reservation_key: str,
        projection_key: str,
        expires_utc: str | None = None,
        timeout: float = 2.0,
        dispatch_guard=None,
        release_on_terminal: bool = False,
    ) -> DurableExecutionResult:
        retry = BrokerRequest.create(
            previous_request.intent_key,
            dict(previous_request.payload),
            action_id=previous_request.action_id,
        )
        await run_storage_call(
            self, self.store.prepare_retry,
            retry,
            previous_attempt_id=previous_request.attempt_id,
            reason=reason,
            retryable_retcodes=retryable_retcodes,
        )
        return await self.execute(
            retry,
            reservation_key=reservation_key,
            projection_key=projection_key,
            expires_utc=expires_utc,
            timeout=timeout,
            dispatch_guard=dispatch_guard,
            release_on_terminal=release_on_terminal,
        )

    def reconstruct(self, *, projection_key: str | None = None) -> list[EffectProjection]:
        return self.store.list_projections(projection_key=projection_key)

    def unresolved(self) -> list[IntentRecord]:
        return self.store.list_unresolved()

    def current_intents(self) -> list[CurrentIntentSnapshot]:
        return self.store.list_current_intents()

    async def reinspect_entry(self, intent_id: str, *, timeout: float = 2.0) -> EntryReinspectionResult:
        """Bounded broker reads only; recovery never submits another trade."""
        finite_number(timeout, "timeout", positive=True)
        record = await run_storage_call(self, self.store.get, intent_id)
        if record.state not in {IntentState.UNKNOWN, IntentState.PLACED, IntentState.DONE_PARTIAL}:
            return EntryReinspectionResult(record, False, "entry_not_awaiting_reinspection")
        try:
            async with asyncio.timeout(timeout):
                snapshot = await run_storage_call(self, self.store.get_current_dispatch, intent_id)
                request, preparation = snapshot.request, snapshot.preparation
                config = self.client.config
                account = f"{config.expected_server}/{config.expected_login}"
                if request.intent_key.account_fingerprint != account:
                    return EntryReinspectionResult(record, False, "entry_account_mismatch")
                if request.intent_key.operation != "OPEN_MARKET" or preparation is None:
                    return EntryReinspectionResult(record, False, "entry_preparation_unavailable")
                source_tick = preparation.evidence.get("source_tick")
                start = source_tick.get("time_msc") if isinstance(source_tick, Mapping) else None
                if type(start) is not int or start <= 0:
                    return EntryReinspectionResult(record, False, "entry_source_clock_unavailable")
                reads = []

                async def read(operation, params):
                    query = ReadRequest(operation, params)
                    response = await self.client.read(query, timeout=timeout)
                    if response.request_id != query.request_id or response.operation is not operation:
                        raise ValueError("entry history response identity mismatch")
                    reads.append({"request": query.to_dict(), "response": response.to_dict()})
                    if response.state is LookupState.UNKNOWN:
                        raise ValueError("entry history read unknown")
                    return response.value

                tick = await read(ReadOperation.TICK, {"symbol": request.payload["symbol"]})
                end = tick.get("time_msc") if isinstance(tick, Mapping) else None
                if type(end) is not int or end < start:
                    return EntryReinspectionResult(record, False, "entry_broker_clock_regressed")
                # Integer-second query envelopes cover the millisecond interval.
                lower, upper = start // 1000, (end + 999) // 1000
                if upper - lower > MAX_HISTORY_SECONDS:
                    return EntryReinspectionResult(record, False, "entry_history_window_budget_exceeded")
                params = {"date_from": lower, "date_to": upper, "symbol": request.payload["symbol"]}
                deals = await read(ReadOperation.DEALS, params)
                orders = await read(ReadOperation.HISTORY_ORDERS, params)
                history = {"account_fingerprint": account, "date_from_msc": start, "date_to_msc": end,
                           "deals": deals, "orders": orders, "reads": reads}
                recovered = await run_storage_call(
                    self, self.store.reconcile_entry_history, request, history=history,
                    projection_key=f"signal:{request.intent_key.signal_root}",
                )
                return EntryReinspectionResult(recovered, True)
        except TimeoutError:
            return EntryReinspectionResult(record, False, "entry_reinspection_timeout")
        except EntryHistoryBlocked as exc:
            return EntryReinspectionResult(record, False, f"entry_history_blocked:{exc.reason}")
        except (ValueError, KeyError, ReconciliationConflictError, sqlite3.Error) as exc:
            return EntryReinspectionResult(record, False, f"entry_reinspection_blocked:{type(exc).__name__}")

    def reconcile(
        self,
        request: BrokerRequest,
        outcome: BrokerOutcome,
        *,
        evidence: Mapping[str, Any],
        projection_key: str,
        release_on_terminal: bool = False,
    ) -> DurableExecutionResult:
        record = self.store.reconcile_outcome(
            request,
            outcome,
            evidence=evidence,
            projection_key=projection_key,
            release_reservation_reason=(
                f"{outcome.state.value.lower()}_effect_projected"
                if release_on_terminal and outcome.state is IntentState.DONE
                else None
            ),
        )
        return self._project(
            request,
            record,
            projection_key=projection_key,
            release_on_terminal=release_on_terminal,
        )

    def resolve_predispatch(
        self,
        request: BrokerRequest,
        outcome: BrokerOutcome,
        *,
        projection_key: str,
        release_on_terminal: bool = False,
    ) -> DurableExecutionResult:
        record = self.store.resolve_predispatch_outcome(
            request,
            outcome,
            projection_key=projection_key,
            release_reservation_reason=(
                f"{outcome.state.value.lower()}_effect_projected"
                if release_on_terminal and outcome.state is IntentState.DONE
                else None
            ),
        )
        return self._project(
            request,
            record,
            projection_key=projection_key,
            release_on_terminal=release_on_terminal,
        )

    def _project(
        self,
        request: BrokerRequest,
        record: IntentRecord,
        *,
        projection_key: str,
        release_on_terminal: bool,
        predispatch_error: str | None = None,
    ) -> DurableExecutionResult:
        disposition = _disposition(record.state)
        if record.outcome_revision <= 0 or record.outcome is None:
            return DurableExecutionResult(
                record,
                disposition,
                None,
                predispatch_error=predispatch_error,
            )
        effect = {
            "intent_id": record.intent_id,
            "outcome_revision": record.outcome_revision,
            "state": record.state.value,
            "channel": request.intent_key.channel,
            "signal_root": request.intent_key.signal_root,
            "generation": request.intent_key.generation,
            "leg": request.intent_key.leg,
            "operation": request.intent_key.operation,
            "policy_revision": request.intent_key.revision,
            "request_payload": dict(request.payload),
            "outcome": dict(record.outcome),
        }
        release_reason = None
        if release_on_terminal and record.state is IntentState.DONE:
            release_reason = f"{record.state.value.lower()}_effect_projected"
        if record.applied_utc is None:
            self.store.apply_projection(
                record.intent_id,
                outcome_revision=record.outcome_revision,
                projection_key=projection_key,
                projection=effect,
                event_id=f"{record.intent_id}:{record.outcome_revision}",
                release_reservation_reason=release_reason,
            )
        return DurableExecutionResult(
            self.store.get(record.intent_id),
            disposition,
            effect,
            predispatch_error=predispatch_error,
        )


def _disposition(state: IntentState) -> ExecutionDisposition:
    if state is IntentState.PREPARED:
        return ExecutionDisposition.NOT_SENT
    if state in {
        IntentState.DISPATCHING,
        IntentState.UNKNOWN,
        IntentState.PLACED,
        IntentState.DONE_PARTIAL,
    }:
        return ExecutionDisposition.RECONCILE
    if state is IntentState.REJECTED:
        return ExecutionDisposition.REJECTED
    return ExecutionDisposition.APPLIED
