"""Unassigned channel-one message tape for scenario-time replay decisions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Mapping

from research.causal_replay import CausalSignal, compile_signals, raw_message, utc
from research.causal_management_semantics import causal_management_semantics
from research.causal_text_admission import candidate_text_entries, route_canal1_stream
from research.dubai_iterative.dataset import ProviderEvent


@dataclass(frozen=True)
class CausalManagementMessage:
    message_id: int
    message_revision_id: str
    observed_at: datetime
    published_at: datetime
    reply_to_msg_id: int | None
    event: ProviderEvent


@dataclass(frozen=True)
class CausalUnresolvedMessage:
    message_id: int
    message_revision_id: str
    observed_at: datetime
    reason: str


@dataclass(frozen=True)
class CausalOptionalHoldMessage:
    message_id: int
    message_revision_id: str
    observed_at: datetime


@dataclass(frozen=True)
class CausalOptionalRepeatMessage:
    message_id: int
    message_revision_id: str
    observed_at: datetime


@dataclass(frozen=True)
class CausalStreamItem:
    kind: str
    message_id: int
    observed_at: datetime
    value: (CausalSignal | CausalManagementMessage | CausalUnresolvedMessage
            | CausalOptionalHoldMessage | CausalOptionalRepeatMessage)


@dataclass(frozen=True)
class CausalCanal1Stream:
    stickers: tuple[CausalSignal, ...]
    texts: tuple[CausalSignal, ...]
    management: tuple[CausalManagementMessage, ...]
    unresolved: tuple[CausalUnresolvedMessage, ...]
    timeline: tuple[CausalStreamItem, ...]
    diagnostics: tuple[dict, ...]
    static_provider_events_detached: int
    legacy_static_root_diagnostic_count: int
    optional_holds: tuple[CausalOptionalHoldMessage, ...] = ()
    optional_repeats: tuple[CausalOptionalRepeatMessage, ...] = ()


def compile_canal1_stream(
    messages, *, start, cutoff, sticker_directions: Mapping[str, str],
    max_entry_age_s: float,
    optional_close_choice: str = "block",
) -> CausalCanal1Stream:
    """Keep all direct management messages without freezing their target root."""
    start, cutoff = utc(start), utc(cutoff)
    if not start < cutoff:
        raise ValueError("invalid canal1 stream window")
    if optional_close_choice not in {"block", "hold", "close"}:
        raise ValueError("invalid optional close choice")
    rows = [raw_message(row) for row in messages]
    rows.sort(key=lambda row: utc(row["ts"]))
    unique, by_revision = [], {}
    for row in rows:
        if utc(row["ts"]) >= cutoff:
            break
        revision = row["message_revision_id"]
        if not isinstance(revision, str) or not revision:
            raise ValueError("raw message revision identity missing")
        previous = by_revision.get(revision)
        if previous is not None:
            if any(row[key] != previous[key] for key in row if key not in {"ts", "is_edit"}):
                raise ValueError(f"revision identity conflict: {revision}")
            continue
        by_revision[revision] = row
        unique.append(row)

    def selectable_optional_close(semantic):
        return (semantic is not None and semantic.get("modality") == "optional"
                and semantic.get("action") == "CLOSE_ALL"
                and semantic.get("execution_options") == [
                    {"action": "CLOSE_ALL"}, {"action": "HOLD"}])

    chosen_optional_revisions, first_optional_revision = set(), {}
    if optional_close_choice != "block":
        for row in unique:
            if (row["channel"] != "canal1" or not start <= utc(row["ts"]) < cutoff
                    or not selectable_optional_close(
                        causal_management_semantics(row["text"] or ""))):
                continue
            chosen_optional_revisions.add(row["message_revision_id"])
            first_optional_revision.setdefault(row["message_id"],
                                               row["message_revision_id"])

    compiled, diagnostics = compile_signals(
        unique, start=start, cutoff=cutoff, sticker_directions=sticker_directions,
        max_entry_age_s=max_entry_age_s,
        management_semantics=causal_management_semantics)
    diagnostics = tuple(row for row in diagnostics if row["channel"] == "canal1")
    texts, text_exclusions = candidate_text_entries(
        unique, start=start, cutoff=cutoff, max_entry_age_s=max_entry_age_s)
    candidates = {row.message_revision_id: row for row in texts}
    stickers = tuple(replace(row, provider_events=()) for row in compiled
                     if row.channel == "canal1")
    detached = sum(len(row.provider_events) for row in compiled
                   if row.channel == "canal1")
    items = []
    for signal in stickers:
        source = by_revision.get(signal.message_revision_id)
        if (source is None or source["channel"] != "canal1"
                or source["sticker_id"] is None):
            raise ValueError("canal1 trigger lacks a raw sticker revision")
        items.append(CausalStreamItem("sticker", source["message_id"],
                                      signal.observed_at, signal))
    for signal in texts:
        source = by_revision.get(signal.message_revision_id)
        if source is None or source["message_id"] != int(signal.signal_id.split("_")[1]):
            raise ValueError("text candidate lacks its raw revision")
        items.append(CausalStreamItem("text_candidate", source["message_id"],
                                      signal.observed_at, signal))

    unresolved_reasons = {
        "conditional_management_unsupported", "unresolved_management_semantics",
        "unknown_sticker_direction",
    }
    unresolved, unresolved_revisions = [], set()
    for diagnostic in diagnostics:
        if (diagnostic["channel"] != "canal1"
                or diagnostic["reason"] not in unresolved_reasons
                or not start <= utc(diagnostic["observed_at"]) < cutoff):
            continue
        revision = diagnostic["message_revision_id"]
        if revision in unresolved_revisions or revision in chosen_optional_revisions:
            continue
        source = by_revision[revision]
        item = CausalUnresolvedMessage(source["message_id"], revision,
                                       utc(source["ts"]), diagnostic["reason"])
        unresolved.append(item)
        unresolved_revisions.add(revision)
        items.append(CausalStreamItem("unresolved", item.message_id,
                                      item.observed_at, item))

    management, optional_holds, optional_repeats, extra_diagnostics = [], [], [], []
    for row in unique:
        if (row["channel"] != "canal1"
                or row["message_revision_id"] in candidates
                or row["message_revision_id"] in unresolved_revisions):
            continue
        observed = utc(row["ts"])
        if observed < start:
            continue
        semantic = causal_management_semantics(row["text"] or "")
        chosen_optional = row["message_revision_id"] in chosen_optional_revisions
        if (semantic is None or semantic.get("modality") != "direct"
                and not chosen_optional):
            continue
        published = utc(row["date_utc"])
        revised = utc(row["edit_date_utc"]) if row["edit_date_utc"] else published
        if published > observed or revised > observed:
            extra_diagnostics.append({"channel": "canal1", "message_id": row["message_id"],
                                      "message_revision_id": row["message_revision_id"],
                                      "observed_at": row["ts"],
                                      "reason": "management_clock_after_receipt"})
            continue
        if (chosen_optional and first_optional_revision[row["message_id"]]
                != row["message_revision_id"]):
            repeated = CausalOptionalRepeatMessage(
                row["message_id"], row["message_revision_id"], observed)
            optional_repeats.append(repeated)
            items.append(CausalStreamItem("optional_repeat", repeated.message_id,
                                          repeated.observed_at, repeated))
            continue
        if chosen_optional and optional_close_choice == "hold":
            held = CausalOptionalHoldMessage(
                row["message_id"], row["message_revision_id"], observed)
            optional_holds.append(held)
            items.append(CausalStreamItem("optional_hold", held.message_id,
                                          held.observed_at, held))
            continue
        if chosen_optional:
            semantic = {**semantic, "modality": "direct",
                        "source_modality": "optional", "chosen_option": "CLOSE_ALL"}
        event = ProviderEvent(observed, semantic["action"], dict(semantic))
        item = CausalManagementMessage(
            row["message_id"], row["message_revision_id"], observed, published,
            row["reply_to_msg_id"], event)
        management.append(item)
        items.append(CausalStreamItem("management", row["message_id"], observed, item))

    items.sort(key=lambda row: row.observed_at)
    if any(left.observed_at == right.observed_at
           for left, right in zip(items, items[1:])):
        raise ValueError("ambiguous canal1 message order")
    static_root_issues = sum(row["reason"] == "unresolved_management_root"
                             for row in diagnostics)
    relevant_diagnostics = tuple(row for row in diagnostics
                                 if row["reason"] != "unresolved_management_root"
                                 and row["message_revision_id"]
                                 not in chosen_optional_revisions)
    return CausalCanal1Stream(stickers, texts, tuple(management),
                              tuple(unresolved), tuple(items),
                              relevant_diagnostics + tuple(text_exclusions)
                              + tuple(extra_diagnostics), detached,
                              static_root_issues, tuple(optional_holds),
                              tuple(optional_repeats))


def route_compiled_canal1_stream(stream: CausalCanal1Stream, *, replay_entry,
                                 apply_text_update=None, apply_management=None,
                                 resolve_state=None, refresh_world=None,
                                 initial_universe_complete=False):
    """Route every compiled event, including unknown messages that must block."""
    if not isinstance(stream, CausalCanal1Stream):
        raise ValueError("typed causal canal1 stream required")
    return route_canal1_stream(
        stream.stickers, stream.texts, management=stream.management,
        unresolved=stream.unresolved, optional_holds=stream.optional_holds,
        optional_repeats=stream.optional_repeats,
        replay_entry=replay_entry,
        apply_text_update=apply_text_update, apply_management=apply_management,
        resolve_state=resolve_state, refresh_world=refresh_world,
        initial_universe_complete=initial_universe_complete)
