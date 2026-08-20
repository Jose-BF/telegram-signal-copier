"""Auditor de operaciones vivas.

Cruza el estado interno del bot, las posiciones reales de MT5 y la cola de
acciones pendientes. No envia ordenes ni modifica posiciones. Si MT5 confirma
una posicion del bot que el estado perdio, pero el comentario la mapea de forma
inequivoca a una Signal abierta, la adopta para que la gestion normal no la
deje fuera.
"""

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable

import MetaTrader5 as mt5

import config
import executor
import journal as default_journal
import pending_actions
from state import Signal, state


@dataclass
class AuditSettings:
    interval_s: float = 5.0
    snapshot_every_s: float = 60.0
    orphan_adoption_grace_s: float = 2.0
    orphan_confirmation_s: float = 2.0
    expected_legs_after_s: float = 15.0
    level_apply_grace_s: float = 15.0
    naked_after_s: float = 120.0
    no_position_after_s: float = 90.0
    no_position_missing_grace_s: float = 45.0
    pending_stuck_after_s: float = 30.0


def _sig_id(sig: Signal) -> str:
    return f"{sig.channel}_{sig.message_id}"


def _unique_signals(signals: Iterable[Signal]) -> list[Signal]:
    seen: set[int] = set()
    out: list[Signal] = []
    for sig in signals:
        obj_id = id(sig)
        if obj_id in seen:
            continue
        seen.add(obj_id)
        out.append(sig)
    return out


def _position_has_sl(pos) -> bool:
    return float(getattr(pos, "sl", 0.0) or 0.0) > 0


def _position_has_tp(pos) -> bool:
    return float(getattr(pos, "tp", 0.0) or 0.0) > 0


def _position_levels(pos) -> dict[str, float]:
    return {
        "sl": float(getattr(pos, "sl", 0.0) or 0.0),
        "tp": float(getattr(pos, "tp", 0.0) or 0.0),
    }


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="seconds")


def _levels_equal(left, right, tolerance: float = 1e-6) -> bool:
    try:
        return abs(float(left or 0.0) - float(right or 0.0)) <= tolerance
    except (TypeError, ValueError):
        return False


def _ticket_mapping_value(mapping, ticket: int):
    if not isinstance(mapping, dict):
        return None
    if ticket in mapping:
        return mapping[ticket]
    return mapping.get(str(ticket))


def _expected_ticket_levels(
    sig: Signal,
    ticket: int,
    state_tickets: list[int],
) -> dict[str, float]:
    sl = _ticket_mapping_value(getattr(sig, "sl_by_ticket", {}), ticket)
    if sl is None:
        sl = sig.sl

    tp = _ticket_mapping_value(getattr(sig, "tp_by_ticket", {}), ticket)
    if tp is None:
        try:
            position_index = state_tickets.index(ticket)
        except ValueError:
            position_index = 0
        override = _ticket_mapping_value(
            getattr(sig, "tp_overrides", {}), ticket)
        if override is None:
            tp = sig.tp_for_position(position_index)
        else:
            try:
                tp = sig.tps[int(override)]
            except (IndexError, TypeError, ValueError):
                tp = sig.tp_for_position(position_index)

    return {
        "sl": float(sl or 0.0),
        "tp": float(tp or 0.0),
    }


def _pending_modify_explains_change(
    ticket: int,
    changed_fields: list[str],
    current: dict[str, float],
    pending_actions_snapshot: list[dict],
) -> bool:
    for action in pending_actions_snapshot:
        if (
            action.get("kind") != "MODIFY_SLTP"
            or int(action.get("ticket") or 0) != ticket
        ):
            continue
        requested = {
            "sl": action.get("new_sl"),
            "tp": (
                action.get("new_tp")
                if action.get("new_tp") is not None
                else action.get("applied_tp")
            ),
        }
        if all(
            requested[field] is not None
            and _levels_equal(requested[field], current[field])
            for field in changed_fields
        ):
            return True
    return False


def _position_signal_id(pos) -> str | None:
    try:
        parsed = executor._parse_signal_id_from_comment(
            getattr(pos, "comment", "") or "")
    except Exception:
        parsed = None
    if not parsed:
        return None
    channel, message_id = parsed
    return f"{channel}_{message_id}"


def _scale_out_leg_number(pos) -> int | None:
    comment = str(getattr(pos, "comment", "") or "")
    match = re.search(r"_B(\d+)\b", comment)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _position_open_price(pos):
    try:
        price = getattr(pos, "price_open", None)
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def _expected_scale_out_legs(sig: Signal) -> int | None:
    if getattr(sig, "entry_mode", None) != "scale_out":
        return None
    if sig.channel == "canal1":
        return int(config.STRATEGY_C1_NUM_ENTRIES)
    if sig.channel == "canal2":
        return int(config.STRATEGY_C2_NUM_ENTRIES)
    return None


def _age_seconds(sig: Signal, now: datetime) -> float:
    try:
        return max(0.0, (now - sig.timestamp).total_seconds())
    except Exception:
        return 0.0


class LiveAuditor:
    def __init__(
        self,
        *,
        settings: AuditSettings | None = None,
        journal=default_journal,
        now_fn: Callable[[], datetime] | None = None,
    ):
        self.settings = settings or AuditSettings()
        self.journal = journal
        self.now_fn = now_fn or datetime.utcnow
        self._levels_seen_at: dict[str, datetime] = {}
        self._last_snapshot_at: dict[str, datetime] = {}
        self._missing_positions_since: dict[str, datetime] = {}
        self._active_issues: dict[tuple, tuple[str, str]] = {}
        self._last_position_levels: dict[tuple[str, int], dict] = {}
        self._orphan_first_seen: dict[int, datetime] = {}

    def audit_cycle(
        self,
        *,
        signals: Iterable[Signal],
        positions: Iterable,
        pending_actions: Iterable[dict],
        now: datetime | None = None,
    ) -> None:
        now = now or self.now_fn()
        open_signals = [
            sig for sig in _unique_signals(signals)
            if getattr(sig, "status", "open") == "open"
        ]
        positions = list(positions or [])
        pending_actions = list(pending_actions or [])
        positions_by_ticket = {
            int(getattr(pos, "ticket")): pos
            for pos in positions
            if getattr(pos, "ticket", None) is not None
        }
        state_by_ticket: dict[int, str] = {}
        state_sig_ids: set[str] = set()
        signals_by_id: dict[str, Signal] = {}
        for sig in open_signals:
            sig_id = _sig_id(sig)
            signals_by_id[sig_id] = sig
            state_sig_ids.add(sig_id)
            for ticket in sig.all_filled_tickets:
                state_by_ticket[int(ticket)] = sig_id

        current_issues: set[tuple] = set()
        bot_magics = {config.MT5_MAGIC_CANAL1, config.MT5_MAGIC_CANAL2}
        suppress_orphan_issue_tickets: set[int] = set()

        for pos in positions:
            magic = getattr(pos, "magic", None)
            ticket = int(getattr(pos, "ticket", 0) or 0)
            if magic not in bot_magics or ticket in state_by_ticket:
                continue
            parsed_sig_id = _position_signal_id(pos)
            sig = signals_by_id.get(parsed_sig_id or "")
            if sig and _scale_out_leg_number(pos) is not None:
                if getattr(sig, "opening_extra_legs", False):
                    suppress_orphan_issue_tickets.add(ticket)
                    continue
                if _age_seconds(sig, now) < self.settings.orphan_adoption_grace_s:
                    suppress_orphan_issue_tickets.add(ticket)
                    continue
                if self._adopt_orphan_position(sig, pos):
                    state_by_ticket[ticket] = parsed_sig_id or _sig_id(sig)

        for sig in open_signals:
            for key, sig_id, category, severity, detail, ctx in self._audit_signal(
                    sig, positions_by_ticket, pending_actions, now):
                current_issues.add(key)
                self._emit_issue_once(
                    key, sig_id, category, severity, detail, **ctx)

        current_orphan_tickets: set[int] = set()
        for pos in positions:
            magic = getattr(pos, "magic", None)
            ticket = int(getattr(pos, "ticket", 0) or 0)
            if magic not in bot_magics or ticket in state_by_ticket:
                continue
            current_orphan_tickets.add(ticket)
            first_seen = self._orphan_first_seen.setdefault(ticket, now)
            confirmation_age_s = max(
                0.0, (now - first_seen).total_seconds())
            if confirmation_age_s < self.settings.orphan_confirmation_s:
                continue
            if ticket in suppress_orphan_issue_tickets:
                continue
            parsed_sig_id = _position_signal_id(pos)
            key = ("bot", "mt5_orphan_position", ticket)
            current_issues.add(key)
            self._emit_issue_once(
                key, "bot", "mt5", "critical",
                "MT5 tiene una posicion del bot sin Signal viva en memoria",
                code="mt5_orphan_position",
                ticket=ticket,
                magic=magic,
                comment=getattr(pos, "comment", None),
                parsed_signal_id=parsed_sig_id,
                known_open_signals=sorted(state_sig_ids),
            )
        for ticket in set(self._orphan_first_seen) - current_orphan_tickets:
            self._orphan_first_seen.pop(ticket, None)


        for act in pending_actions:
            if act.get("state") in {"waiting_market", "confirmed_recent"}:
                continue
            age_s = float(act.get("age_s") or 0.0)
            if age_s < self.settings.pending_stuck_after_s:
                continue
            sig_id = act.get("sig_id") or "bot"
            ticket = int(act.get("ticket") or 0)
            key = (sig_id, "pending_action_stuck", ticket, act.get("kind"))
            current_issues.add(key)
            self._emit_issue_once(
                key, sig_id, "mt5", "warning",
                "Accion pendiente lleva demasiado tiempo en cola",
                code="pending_action_stuck",
                kind=act.get("kind"),
                ticket=ticket,
                age_s=age_s,
                attempts=act.get("attempts"),
                last_retcode=act.get("last_retcode"),
                label=act.get("label"),
            )

        live_level_keys = {
            (sig_id, ticket)
            for ticket, sig_id in state_by_ticket.items()
            if ticket in positions_by_ticket
        }
        for key in set(self._last_position_levels) - live_level_keys:
            self._last_position_levels.pop(key, None)

        self._emit_resolved_issues(current_issues, now)

    def _adopt_orphan_position(self, sig: Signal, pos) -> bool:
        ticket = int(getattr(pos, "ticket", 0) or 0)
        if not ticket:
            return False
        if ticket in [int(t) for t in sig.all_filled_tickets]:
            return False

        leg_num = _scale_out_leg_number(pos)
        if leg_num is None:
            return False

        insert_at = max(0, leg_num - 1)
        insert_at = min(insert_at, len(sig.extra_market_tickets))
        fill_price = _position_open_price(pos)
        sig.extra_market_tickets.insert(insert_at, ticket)
        sig.extra_market_fill_prices.insert(insert_at, fill_price)

        sig_id = _sig_id(sig)
        self.journal.event(
            sig_id,
            "mt5_orphan_position_adopted",
            ticket=ticket,
            adopted_as="extra_market_ticket",
            leg=leg_num,
            insert_at=insert_at,
            comment=getattr(pos, "comment", None),
            magic=getattr(pos, "magic", None),
            price_open=fill_price,
            state_tickets_after=list(sig.all_filled_tickets),
        )
        self.journal.anomaly(
            sig_id,
            "mt5",
            "warning",
            "MT5 tenia una leg scale_out fuera del estado; adoptada en Signal",
            code="mt5_orphan_position_adopted",
            ticket=ticket,
            leg=leg_num,
            comment=getattr(pos, "comment", None),
        )
        return True

    def _audit_signal(
        self,
        sig: Signal,
        positions_by_ticket: dict[int, object],
        pending_actions_snapshot: list[dict],
        now: datetime,
    ) -> list[tuple]:
        sig_id = _sig_id(sig)
        age_s = _age_seconds(sig, now)
        state_tickets = [int(t) for t in sig.all_filled_tickets]
        mt5_positions = [
            positions_by_ticket[t] for t in state_tickets
            if t in positions_by_ticket
        ]
        mt5_open_tickets = [int(getattr(p, "ticket")) for p in mt5_positions]
        missing_tickets = [t for t in state_tickets if t not in mt5_open_tickets]
        tickets_without_sl = [
            int(getattr(p, "ticket")) for p in mt5_positions
            if not _position_has_sl(p)
        ]
        tickets_without_tp = [
            int(getattr(p, "ticket")) for p in mt5_positions
            if not _position_has_tp(p)
        ]
        pending_for_signal = [
            a for a in pending_actions_snapshot
            if a.get("sig_id") == sig_id
        ]
        active_pending_for_signal = [
            action for action in pending_for_signal
            if action.get("state") != "confirmed_recent"
        ]
        mt5_levels = [
            {
                "ticket": int(getattr(position, "ticket")),
                **_position_levels(position),
            }
            for position in mt5_positions
        ]
        self._audit_level_changes(
            sig,
            sig_id,
            state_tickets,
            mt5_positions,
            pending_for_signal,
            now,
        )

        has_state_levels = bool(sig.tps) or sig.sl is not None
        if has_state_levels and sig_id not in self._levels_seen_at:
            self._levels_seen_at[sig_id] = now
        if mt5_open_tickets:
            self._missing_positions_since.pop(sig_id, None)
        elif state_tickets:
            self._missing_positions_since.setdefault(sig_id, now)
        else:
            self._missing_positions_since.pop(sig_id, None)
        missing_since = self._missing_positions_since.get(sig_id)
        missing_for_s = (
            (now - missing_since).total_seconds()
            if missing_since is not None else 0.0
        )

        self._maybe_emit_snapshot(
            sig_id, now,
            channel=sig.channel,
            direction=sig.direction,
            age_s=round(age_s, 1),
            state_tickets=state_tickets,
            mt5_open_tickets=mt5_open_tickets,
            missing_tickets=missing_tickets,
            mt5_levels=mt5_levels,
            tickets_without_sl=tickets_without_sl,
            tickets_without_tp=tickets_without_tp,
            has_state_tps=bool(sig.tps),
            has_state_sl=sig.sl is not None,
            pending_actions_count=len(active_pending_for_signal),
            pending_actions=[
                {
                    "kind": a.get("kind"),
                    "ticket": a.get("ticket"),
                    "age_s": a.get("age_s"),
                    "attempts": a.get("attempts"),
                    "last_retcode": a.get("last_retcode"),
                    "label": a.get("label"),
                }
                for a in active_pending_for_signal[:10]
            ],
        )

        issues: list[tuple] = []
        expected_legs = _expected_scale_out_legs(sig)
        if (expected_legs is not None
                and age_s >= self.settings.expected_legs_after_s
                and not getattr(sig, "opening_extra_legs", False)
                and len(state_tickets) < expected_legs):
            key = (sig_id, "scale_out_missing_expected_legs")
            issues.append((
                key, sig_id, "fill", "critical",
                "Signal scale_out tiene menos tickets registrados de los esperados",
                {
                    "code": "scale_out_missing_expected_legs",
                    "expected_legs": expected_legs,
                    "state_legs": len(state_tickets),
                    "missing_legs": expected_legs - len(state_tickets),
                    "state_tickets": state_tickets,
                    "mt5_open_tickets": mt5_open_tickets,
                    "age_s": round(age_s, 1),
                },
            ))

        if (state_tickets and not mt5_open_tickets
                and age_s >= self.settings.no_position_after_s
                and missing_for_s >= self.settings.no_position_missing_grace_s):
            key = (sig_id, "signal_without_mt5_position")
            issues.append((
                key, sig_id, "mt5", "warning",
                "Signal sigue abierta en memoria pero MT5 no muestra posiciones abiertas",
                {
                    "code": "signal_without_mt5_position",
                    "state_tickets": state_tickets,
                    "missing_tickets": missing_tickets,
                    "age_s": round(age_s, 1),
                    "missing_for_s": round(missing_for_s, 1),
                },
            ))

        levels_seen_at = self._levels_seen_at.get(sig_id)
        levels_age_s = (
            (now - levels_seen_at).total_seconds()
            if levels_seen_at is not None else None
        )
        if (has_state_levels and mt5_open_tickets
                and levels_age_s is not None
                and levels_age_s >= self.settings.level_apply_grace_s
                and (tickets_without_sl or tickets_without_tp)):
            key = (sig_id, "levels_not_applied")
            issues.append((
                key, sig_id, "levels", "warning",
                "La signal tiene niveles en memoria pero MT5 no los refleja en todas las posiciones",
                {
                    "code": "levels_not_applied",
                    "tickets_without_sl": tickets_without_sl,
                    "tickets_without_tp": tickets_without_tp,
                    "mt5_open_tickets": mt5_open_tickets,
                    "age_s": round(age_s, 1),
                    "levels_age_s": round(levels_age_s, 1),
                    "sl": sig.sl,
                    "tps": list(sig.tps),
                },
            ))

        naked_tickets = sorted(set(tickets_without_sl) & set(tickets_without_tp))
        if mt5_open_tickets and naked_tickets and age_s >= self.settings.naked_after_s:
            key = (sig_id, "mt5_position_naked")
            issues.append((
                key, sig_id, "naked", "critical",
                "MT5 tiene posiciones abiertas del bot sin SL ni TP aplicados",
                {
                    "code": "mt5_position_naked",
                    "naked_tickets": naked_tickets,
                    "age_s": round(age_s, 1),
                },
            ))

        return issues

    def _audit_level_changes(
        self,
        sig: Signal,
        sig_id: str,
        state_tickets: list[int],
        mt5_positions: list[object],
        pending_for_signal: list[dict],
        now: datetime,
    ) -> None:
        for position in mt5_positions:
            ticket = int(getattr(position, "ticket"))
            key = (sig_id, ticket)
            current = _position_levels(position)
            previous_observation = self._last_position_levels.get(key)
            self._last_position_levels[key] = {
                "levels": current,
                "observed_at": now,
            }
            if previous_observation is None:
                continue
            previous = previous_observation["levels"]

            changed_fields = [
                field for field in ("sl", "tp")
                if not _levels_equal(previous[field], current[field])
            ]
            if not changed_fields:
                continue

            expected = _expected_ticket_levels(sig, ticket, state_tickets)
            expected_explains = all(
                _levels_equal(current[field], expected[field])
                for field in ("sl", "tp")
            )
            pending_explains = _pending_modify_explains_change(
                ticket,
                changed_fields,
                current,
                pending_for_signal,
            )
            if expected_explains or pending_explains:
                continue

            fields = {
                "code": "mt5_level_change_unattributed",
                "ticket": ticket,
                "changed_fields": changed_fields,
                "previous": previous,
                "current": current,
                "expected": expected,
                "sl": current["sl"],
                "tp": current["tp"],
                "observed_interval_start_utc": _utc_iso(
                    previous_observation["observed_at"]
                ),
                "observed_interval_end_utc": _utc_iso(now),
                "matching_pending_modify": False,
            }
            self.journal.event(
                sig_id,
                "mt5_level_change_unattributed",
                **fields,
            )
            self.journal.anomaly(
                sig_id,
                "levels",
                "warning",
                "MT5 cambio el SL/TP sin una accion atribuible al bot",
                **fields,
            )

    def _maybe_emit_snapshot(self, sig_id: str, now: datetime, **fields) -> None:
        last = self._last_snapshot_at.get(sig_id)
        if (last is not None
                and (now - last).total_seconds() < self.settings.snapshot_every_s):
            return
        self._last_snapshot_at[sig_id] = now
        self.journal.event(sig_id, "audit_snapshot", **fields)

    def _emit_issue_once(
        self,
        key: tuple,
        sig_id: str,
        category: str,
        severity: str,
        detail: str,
        **ctx,
    ) -> None:
        if key in self._active_issues:
            return
        self._active_issues[key] = (sig_id, str(ctx.get("code", key[1])))
        self.journal.event(sig_id, "audit_issue_detected",
                           category=category, severity=severity,
                           detail=detail, **ctx)
        self.journal.anomaly(sig_id, category, severity, detail, **ctx)

    def _emit_resolved_issues(self, current_issues: set[tuple],
                              now: datetime) -> None:
        for key, (sig_id, code) in list(self._active_issues.items()):
            if key in current_issues:
                continue
            self.journal.event(sig_id, "audit_issue_resolved",
                               code=code,
                               resolved_utc=now.isoformat(timespec="seconds"))
            del self._active_issues[key]

    async def run_forever(self) -> None:
        print(f"[LiveAuditor] activo. Check cada {self.settings.interval_s}s.")
        while True:
            try:
                await self.audit_once()
            except Exception as e:
                self.journal.anomaly(
                    "bot", "mt5", "warning",
                    f"live_auditor crasheo en ciclo: {type(e).__name__}: {str(e)[:200]}",
                    exc_type=type(e).__name__,
                )
            await asyncio.sleep(self.settings.interval_s)

    async def audit_once(self) -> None:
        positions = await asyncio.to_thread(mt5.positions_get)
        if positions is None:
            self.journal.anomaly(
                "bot", "mt5", "warning",
                "live_auditor: mt5.positions_get() devolvio None",
            )
            positions = []
        self.audit_cycle(
            signals=list(state._signals.values()),
            positions=list(positions or []),
            pending_actions=pending_actions.snapshot(),
            now=self.now_fn(),
        )


def settings_from_config() -> AuditSettings:
    return AuditSettings(
        interval_s=float(config.LIVE_AUDITOR_INTERVAL_S),
        snapshot_every_s=float(config.LIVE_AUDITOR_SNAPSHOT_EVERY_S),
        level_apply_grace_s=float(config.LIVE_AUDITOR_LEVEL_APPLY_GRACE_S),
        naked_after_s=float(config.LIVE_AUDITOR_NAKED_AFTER_S),
        no_position_after_s=float(config.LIVE_AUDITOR_NO_POSITION_AFTER_S),
        no_position_missing_grace_s=float(
            config.LIVE_AUDITOR_NO_POSITION_MISSING_GRACE_S),
        pending_stuck_after_s=float(config.LIVE_AUDITOR_PENDING_STUCK_AFTER_S),
        expected_legs_after_s=float(
            config.LIVE_AUDITOR_EXPECTED_LEGS_AFTER_S),
    )


def start() -> asyncio.Task | None:
    if not config.LIVE_AUDITOR_ENABLED:
        print("[LiveAuditor] desactivado por config.")
        return None
    auditor = LiveAuditor(settings=settings_from_config())
    return asyncio.create_task(auditor.run_forever())
