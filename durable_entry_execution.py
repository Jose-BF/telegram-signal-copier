"""Typed application boundary for durable market-entry effects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Mapping

from durable_execution import ExecutionDisposition, run_storage_call
from execution_intents import IntentConflictError
from mt5_protocol import IntentKey, IntentState


_REINSPECTION_INTERVAL_SECONDS = 30.0
_MAX_REINSPECTION_TIMESTAMPS = 512


class EntryDispatchState(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_SENT = "NOT_SENT"
    REJECTED = "REJECTED"
    RECONCILE = "RECONCILE"


@dataclass(frozen=True)
class EntryDispatchResult:
    state: EntryDispatchState
    intent_id: str
    ticket: int | None = None
    fill_price: float | None = None
    retcode: int | None = None
    reason: str | None = None
    outcome: Mapping[str, Any] | None = None
    requested_sl: float | None = None


@dataclass(frozen=True)
class EntryRecoveryRecord:
    intent_id: str
    outcome_revision: int
    state: EntryDispatchState
    channel: str
    signal_root: str
    generation: int
    leg: str
    policy_revision: int
    payload: Mapping[str, Any]
    ticket: int | None
    fill_price: float | None
    retcode: int | None
    reason: str | None = None


class DurableEntryExecutor:
    """Create one causal OPEN_MARKET intent and expose only confirmed fills."""

    def __init__(self, service, *, symbol: str):
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("symbol must be a non-empty string")
        self.service = service
        self.symbol = symbol
        self._last_reinspection: dict[str, float] = {}

    def reconstruct(self) -> list[EntryRecoveryRecord]:
        """Rebuild one current state per market-entry intent."""
        recovered = []
        config = self.service.client.config
        expected_account = (
            f"{config.expected_server}/{config.expected_login}"
        )
        for current in self.service.current_intents():
            key = current.intent_key
            record = current.record
            if (
                key.operation != "OPEN_MARKET"
                or key.account_fingerprint != expected_account
            ):
                continue
            recovered.append(self._recovery_record(key, record))
        return recovered

    def lookup(
        self,
        *,
        channel: str,
        signal_root: str,
        generation: int,
        leg: str,
        revision: int,
    ) -> EntryRecoveryRecord | None:
        """Return the durable state for one deterministic entry identity."""
        config = self.service.client.config
        key = IntentKey(
            f"{config.expected_server}/{config.expected_login}",
            channel,
            signal_root,
            generation,
            leg,
            "OPEN_MARKET",
            revision,
        )
        try:
            record = self.service.store.get(key.intent_id)
        except KeyError:
            return None
        return self._recovery_record(key, record)

    @staticmethod
    def _recovery_record(key, record) -> EntryRecoveryRecord:
        outcome = dict(record.outcome or {})
        raw_state = record.state.value
        ticket = outcome.get("order")
        price = outcome.get("price")
        if raw_state == "DONE" and ticket and price not in (None, 0, 0.0):
            state = EntryDispatchState.CONFIRMED
            reason = (
                "terminal_outcome_missing_projection"
                if record.applied_utc is None
                else None
            )
            ticket = int(ticket)
            price = float(price)
        elif raw_state == "REJECTED":
            state = EntryDispatchState.REJECTED
            reason = outcome.get("error") or "broker_rejected"
            ticket = None
            price = None
        elif raw_state == "PREPARED":
            state = EntryDispatchState.NOT_SENT
            reason = "durable_prepared"
            ticket = None
            price = None
        else:
            state = EntryDispatchState.RECONCILE
            reason = (
                "confirmed_open_missing_ticket_or_price"
                if raw_state == "DONE"
                else f"durable_{raw_state.lower()}"
            )
            ticket = None
            price = None
        return EntryRecoveryRecord(
            intent_id=record.intent_id,
            outcome_revision=int(record.outcome_revision),
            state=state,
            channel=key.channel,
            signal_root=key.signal_root,
            generation=key.generation,
            leg=key.leg,
            policy_revision=key.revision,
            payload=dict(record.payload),
            ticket=ticket,
            fill_price=price,
            retcode=outcome.get("retcode"),
            reason=reason,
        )

    async def open_market(
        self,
        *,
        channel: str,
        signal_root: str,
        generation: int,
        leg: str,
        revision: int,
        direction: str,
        volume: float,
        sl: float | None,
        tp: float | None,
        loss_budget: float | None,
        protection_policy: str,
        magic: int,
        comment: str,
        action_id: str,
        expires_utc: str | None = None,
        timeout: float = 2.0,
        dispatch_guard=None,
    ) -> EntryDispatchResult:
        request = self.service.request(
            channel=channel,
            signal_root=signal_root,
            generation=generation,
            leg=leg,
            operation="OPEN_MARKET",
            revision=revision,
            payload={
                "symbol": self.symbol,
                "direction": direction,
                "volume": volume,
                "sl": sl,
                "tp": tp,
                "loss_budget": loss_budget,
                "protection_policy": protection_policy,
                "magic": magic,
                "comment": comment[:31],
                "deviation": 30,
            },
            action_id=action_id,
        )
        existing = None
        get_record = getattr(self.service.store, "get", None)
        if get_record is not None:
            try:
                existing = await run_storage_call(self.service, get_record, request.intent_id)
            except KeyError:
                pass
            else:
                existing_payload = dict(existing.payload)
                requested_payload = dict(request.payload)
                frozen_fields = (
                    "symbol",
                    "direction",
                    "volume",
                    "loss_budget",
                    "protection_policy",
                    "magic",
                    "comment",
                    "deviation",
                )
                changed_fields = [
                    name for name in frozen_fields
                    if existing_payload.get(name) != requested_payload.get(name)
                ]
                if changed_fields:
                    raise IntentConflictError(
                        "entry redelivery changed frozen fields: "
                        + ",".join(changed_fields)
                    )
                request = self.service.request(
                    channel=channel,
                    signal_root=signal_root,
                    generation=generation,
                    leg=leg,
                    operation="OPEN_MARKET",
                    revision=revision,
                    payload=existing_payload,
                    action_id=action_id,
                )
        config = self.service.client.config
        account = f"{config.expected_server}/{config.expected_login}"
        reservation_key = (
            f"{account}/{channel}/{signal_root}/g{generation}/{leg}"
        )
        result = await self.service.execute(
            request,
            reservation_key=reservation_key,
            projection_key=f"signal:{signal_root}",
            expires_utc=expires_utc,
            timeout=timeout,
            dispatch_guard=dispatch_guard,
        )
        record = result.record
        disposition = result.disposition
        reinspection_reason = None
        uncertain = {IntentState.UNKNOWN, IntentState.PLACED, IntentState.DONE_PARTIAL}
        if getattr(existing, "state", None) in uncertain and record.state in uncertain:
            reinspect = getattr(self.service, "reinspect_entry", None)
            now = time.monotonic()
            last = self._last_reinspection.get(request.intent_id)
            if reinspect is not None and (last is None or now - last >= _REINSPECTION_INTERVAL_SECONDS):
                self._last_reinspection.pop(request.intent_id, None)
                if len(self._last_reinspection) >= _MAX_REINSPECTION_TIMESTAMPS:
                    self._last_reinspection.pop(next(iter(self._last_reinspection)))
                self._last_reinspection[request.intent_id] = now
                inspection = await reinspect(request.intent_id, timeout=timeout)
                record = inspection.record
                reinspection_reason = inspection.reason
                if record.state is IntentState.DONE and record.applied_utc is not None:
                    disposition = ExecutionDisposition.APPLIED
        outcome = dict(record.outcome or {})
        common = {
            "intent_id": request.intent_id,
            "retcode": outcome.get("retcode"),
            "outcome": outcome or None,
        }
        if disposition is ExecutionDisposition.NOT_SENT:
            return EntryDispatchResult(
                EntryDispatchState.NOT_SENT,
                reason=result.predispatch_error or "not_dispatched",
                **common,
            )
        if disposition is ExecutionDisposition.REJECTED:
            await run_storage_call(
                self.service, self.service.store.release_reservation,
                request.intent_id,
                outcome_revision=record.outcome_revision,
                reason="non_retryable_entry_rejection_projected",
            )
            return EntryDispatchResult(
                EntryDispatchState.REJECTED,
                reason=outcome.get("error") or "broker_rejected",
                **common,
            )
        if disposition is ExecutionDisposition.RECONCILE:
            return EntryDispatchResult(
                EntryDispatchState.RECONCILE,
                reason=reinspection_reason or f"durable_{record.state.value.lower()}",
                **common,
            )

        ticket = outcome.get("order")
        price = outcome.get("price")
        if not ticket or price in (None, 0, 0.0):
            return EntryDispatchResult(
                EntryDispatchState.RECONCILE,
                reason="confirmed_open_missing_ticket_or_price",
                **common,
            )
        return EntryDispatchResult(
            EntryDispatchState.CONFIRMED,
            ticket=int(ticket),
            fill_price=float(price),
            requested_sl=request.payload.get("sl"),
            **common,
        )
