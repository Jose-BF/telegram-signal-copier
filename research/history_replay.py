"""Replay historical entry triggers (time + direction) with any own-management strategy.

Clock: observed_at = Telegram publication + a Telegram->bot delay drawn
deterministically per trigger from the live journal sample of that channel
(research only reads the past tape; the draw never looks at prices).
Broker server time is UTC+2 before the US DST switch (2026-03-08) and UTC+3
after it: the daily gold break sits at 00:00 server time all year.
Rules mirrored from the live bot (listener.py): a canal1 direction STICKER always
opens a basket (duplicates <5 s are merged upstream); a canal1 TEXT entry opens one
only when no canal1 basket is open, otherwise it is that signal's companion;
canal2 baskets may overlap. Baskets still open at the day's tape end are
reported censored.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from research.causal_replay import CausalSignal, make_path, time_ns, utc

DST_SWITCH_UTC = datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc)
MAX_BASKET_H = 6


def server_offset_s(moment: datetime) -> int:
    return 7200 if moment < DST_SWITCH_UTC else 10800


def _draw(seed: int, key: str) -> float:
    digest = hashlib.sha256(f"{seed}|tg_delay|{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2 ** 64


@dataclass(frozen=True)
class Trigger:
    trigger_id: str
    channel: str  # canal1 | canal2
    direction: str
    published_at: datetime
    source: str
    kind: str = "sticker"  # canal1: sticker | text_only | text_then_sticker; canal2: text_now


TEXT_KINDS = frozenset({"text_only", "text_then_sticker"})


def load_universe(path: Path) -> list[Trigger]:
    """signals.jsonl from tools/build_signal_universe.py (both channels)."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out.append(Trigger(r["id"], r["channel"], r["direction"], utc(datetime.fromisoformat(r["published_utc"])),
                           r["source"], r["kind"]))
    return sorted(out, key=lambda t: t.published_at)


def load_triggers(dubai: Path, gold_paths: list[Path], dedupe_s: int = 10) -> list[Trigger]:
    out = []
    for line in Path(dubai).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out.append(Trigger(f"c1h_{r['chat_id']}_{r['message_id']}", "canal1", r["direction"],
                           utc(datetime.fromisoformat(r["published_utc"].replace("Z", "+00:00"))), r["source"]))
    gold = []
    for path in gold_paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            gold.append(Trigger(f"c2h_{abs(r['chat_id'])}_{r['message_id']}", "canal2", r["direction"],
                                utc(datetime.fromisoformat(r["published_utc"])), r.get("source", r["channel"])))
    gold.sort(key=lambda t: t.published_at)
    kept = []
    for t in gold:  # double posts: same direction within dedupe_s
        if kept and kept[-1].direction == t.direction and (t.published_at - kept[-1].published_at).total_seconds() <= dedupe_s:
            continue
        kept.append(t)
    return sorted(out + kept, key=lambda t: t.published_at)


class History:
    def __init__(self, tick_root: Path, triggers: list[Trigger], delay_samples_s: dict[str, list[float]], seed: int = 0):
        self.root = Path(tick_root)
        self.triggers = triggers
        self.seed = seed
        self.delays = {ch: np.sort(np.asarray(v, dtype=float)) for ch, v in delay_samples_s.items()}

    def observed_at(self, t: Trigger) -> datetime:
        sample = self.delays[t.channel]
        u = _draw(self.seed, t.trigger_id)
        delay = float(sample[min(int(u * len(sample)), len(sample) - 1)])
        return t.published_at + timedelta(seconds=delay)

    def days(self, only=None):
        """Yield (day, market, conversion, [(trigger, signal)]) for days with ticks."""
        from tools.probe_canal1_incremental_window import load_day
        by_day: dict[str, list] = {}
        for t in self.triggers:
            obs = self.observed_at(t)
            off = server_offset_s(obs)
            server_day = (obs + timedelta(seconds=off)).date().isoformat()
            by_day.setdefault(server_day, []).append((t, obs, off))
        for day in sorted(by_day):
            if (only is not None and day not in only) or not (self.root / "XAUUSD" / f"{day}.parquet").exists():
                continue
            off = by_day[day][0][2]
            start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) - timedelta(seconds=off)
            end = start + timedelta(days=1)
            market, _ = load_day(self.root, "XAUUSD", day, start_ns=time_ns(start), cutoff_ns=time_ns(end), offset_seconds=off)
            conversion, _ = load_day(self.root, "EURUSD", day, start_ns=time_ns(start), cutoff_ns=time_ns(end),
                                     offset_seconds=off, initial_padding_ns=60_000_000_000)
            tape_end = datetime.fromtimestamp(int(market[0][-1]) / 1e9, timezone.utc)
            items = []
            for t, obs, _ in by_day[day]:
                if obs >= tape_end:
                    continue
                items.append((t, CausalSignal(t.trigger_id, t.channel, t.direction, obs, t.published_at, "hist"), tape_end))
            yield day, market, conversion, items


def make_trigger_path(signal: CausalSignal, genome, market, conversion, tape_end: datetime, max_fx_age_ms=30000):
    cutoff = min(tape_end, signal.observed_at + timedelta(hours=MAX_BASKET_H))
    start = signal.observed_at - timedelta(seconds=5)
    return make_path(signal, genome, market=market, conversion=conversion, cutoff=cutoff, contract_size=100.0,
                     currency_digits=2, max_fx_age_ms=max_fx_age_ms, market_sha256="hist", conversion_sha256="hist",
                     tape_start=start), cutoff


def simulate_day(items, genomes, evaluators, market, conversion):
    """items: [(trigger, signal, tape_end)]; genomes/evaluators by channel. canal1 one-at-a-time."""
    rows, open_until = [], []
    for trigger, signal, tape_end in sorted(items, key=lambda x: x[1].observed_at):
        ch = signal.channel
        if ch == "canal1" and trigger.kind in TEXT_KINDS and any(u > signal.observed_at for u in open_until):
            rows.append({"id": trigger.trigger_id, "ch": ch, "status": "companion"}); continue
        try:
            path, cutoff = make_trigger_path(signal, genomes[ch], market, conversion, tape_end)
            r = evaluators[ch](path, genomes[ch])
        except Exception as exc:  # noqa: BLE001 - reported, never silently dropped
            rows.append({"id": trigger.trigger_id, "ch": ch, "status": "error", "error": str(exc)[:120]}); continue
        filled = float(r.filled_volume or 0)
        closed = max((x.closed_at for x in r.exits), default=None)
        censored = bool(filled) and (r.pnl_eur is None or closed is None or closed >= cutoff - timedelta(seconds=1))
        if ch == "canal1" and filled:
            open_until.append(closed if closed is not None else cutoff)
        rows.append({"id": trigger.trigger_id, "ch": ch, "status": "filled" if filled else "unfilled",
                     "observed": signal.observed_at.isoformat(), "pnl": None if r.pnl_eur is None else float(r.pnl_eur),
                     "dd": None if r.max_floating_drawdown_eur is None else float(r.max_floating_drawdown_eur),
                     "censored": censored, "blockers": list(r.blockers)[:2]})
    return rows
