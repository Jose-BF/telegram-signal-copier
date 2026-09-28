"""Parse MT5 terminal trade logs into bound execution events and timing evidence.

The MT5 terminal writes one UTF-16 log per local day. A request that leaves
the terminal is logged before its answer; a request rejected by the terminal's
own checks (for example "No money" or "Invalid stops") is logged only as a
failure. This audit keeps both kinds, pairs requests with their answers, binds
deal lines to the broker ledger when one is supplied, brackets the VM-broker
clock per day and reports roundtrip distributions.

It is diagnostic evidence about observed execution. It does not simulate,
does not change any live setting and does not read the bot's own journal.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo


CONTRACT = "mt5_terminal_trade_log_audit_v1"
DEFAULT_ZONE = "Europe/Madrid"
DEFAULT_BROKER_OFFSET_SECONDS = 10_800
MAX_LOG_BYTES = 64 * 1024 * 1024
BURST_GAP_MS = 5_000
NUM = r"\d+(?:\.\d+)?"
SYMBOL = r"[^\s,\[\]]+"

LOGIN = re.compile(r"^'(?P<login>\d+)': (?P<body>.*)$")
MARKET = re.compile(
    rf"^(?P<prefix>accepted |failed )?market (?P<side>buy|sell) (?P<volume>{NUM}) (?P<symbol>{SYMBOL})"
    rf"(?:, close #(?P<close_position>\d+) (?P<close_side>buy|sell) (?P<close_volume>{NUM}) "
    rf"(?P<close_symbol>{SYMBOL}) (?P<close_price>{NUM}))?"
    rf"(?: sl: (?P<sl>{NUM}))?(?: tp: (?P<tp>{NUM}))?(?: \[(?P<reason>[^\]]*)\])?$")
ORDER_DONE = re.compile(
    rf"^order #(?P<order>\d+) (?P<side>buy|sell) (?P<filled>{NUM}) / (?P<requested>{NUM}) "
    rf"(?P<symbol>{SYMBOL}) at (?P<price>{NUM}) done in (?P<ms>{NUM}) ms$")
DEAL = re.compile(
    rf"^deal #(?P<deal>\d+) (?P<side>buy|sell) (?P<volume>{NUM}) (?P<symbol>{SYMBOL}) "
    rf"at (?P<price>{NUM}) done \(based on order #(?P<order>\d+)\)$")
MODIFY = re.compile(
    rf"^(?P<prefix>accepted |failed )?modify #(?P<position>\d+) (?P<side>buy|sell) (?P<volume>{NUM}) ?"
    rf"(?P<symbol>[^\s,\[\]]*) +sl: (?P<from_sl>{NUM}), tp: (?P<from_tp>{NUM}) -> "
    rf"sl: (?P<to_sl>{NUM}), tp: (?P<to_tp>{NUM})(?: \[(?P<reason>[^\]]*)\])?$")
MODIFY_DONE = re.compile(
    rf"^modify #(?P<position>\d+) (?P<side>buy|sell) (?P<volume>{NUM}) (?P<symbol>{SYMBOL}) -> "
    rf"sl: (?P<to_sl>{NUM}), tp: (?P<to_tp>{NUM}) done in (?P<ms>{NUM}) ms$")
VPS_HINT = re.compile(
    rf"^use MetaTrader VPS Hosting Service .*: (?P<vps_ms>{NUM}) ms via '(?P<vps>[^']+)' "
    rf"instead of (?P<current_ms>{NUM}) ms$")
DISCONNECTED = re.compile(r"^disconnected from (?P<server>.+)$")
LOST = re.compile(r"^connection to (?P<server>.+) lost$")
AUTHORIZED = re.compile(
    rf"^authorized on (?P<server>\S+) through (?P<access>\S+)"
    rf"(?: \(ping: (?P<ping>{NUM}) ms, build (?P<build>\d+)\))?$")
SYNCHRONIZED = re.compile(r"^terminal synchronized with .*: (?P<positions>\d+) positions, (?P<orders>\d+) orders")
PREVIOUS_AUTH = re.compile(r"^previous successful authorization performed from (?P<ip>\S+) on (?P<when>.+)$")
SYSTEM_INFO = re.compile(
    rf"^(?P<os>.+?) on (?P<host>\S+), (?P<cpus>\d+) x (?P<cpu>.+?), .*?(?P<mem_free>{NUM}) / "
    rf"(?P<mem_total>{NUM}) Gb memory, (?P<disk_free>{NUM}) / (?P<disk_total>{NUM}) Gb disk")
TERMINAL_STARTED = re.compile(r"^MetaTrader \d+ x64 build (?P<build>\d+) started")

EXIT_REASONS = {0: "client", 1: "mobile", 2: "web", 3: "expert", 4: "sl", 5: "tp",
                6: "stop_out", 7: "rollover", 8: "vmargin", 9: "split"}


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def local_to_utc_ms(day: date, clock: str, zone: ZoneInfo) -> tuple[int, str | None]:
    """Convert a terminal local clock to UTC epoch ms and flag DST problems."""
    naive = datetime.strptime(f"{day.isoformat()} {clock}", "%Y-%m-%d %H:%M:%S.%f")
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    flag = None
    if first.utcoffset() != second.utcoffset():
        flag = "ambiguous_local_time"
    roundtrip = first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
    if roundtrip != naive:
        flag = "nonexistent_local_time"
    utc = first.astimezone(timezone.utc)
    return int(round(utc.timestamp() * 1000)), flag


def _num(value):
    return None if value is None else float(value)


def parse_body(source: str, body: str) -> dict:
    """Classify one log message. Unknown messages are kept, never dropped."""
    event: dict = {"source": source}
    login = LOGIN.match(body)
    if login:
        event["login_sha12"] = short_hash(login["login"])
        body = login["body"]
    if source == "Trades":
        if match := MARKET.match(body):
            prefix = (match["prefix"] or "").strip()
            event.update(kind={"": "market_request", "accepted": "market_accepted",
                               "failed": "market_failed"}[prefix],
                         side=match["side"], volume=_num(match["volume"]), symbol=match["symbol"],
                         sl=_num(match["sl"]), tp=_num(match["tp"]), reason=match["reason"])
            if match["close_position"]:
                event.update(close_position=int(match["close_position"]),
                             close_side=match["close_side"],
                             close_volume=_num(match["close_volume"]),
                             close_price=_num(match["close_price"]))
            return event
        if match := ORDER_DONE.match(body):
            event.update(kind="order_done", order=int(match["order"]), side=match["side"],
                         filled=_num(match["filled"]), volume=_num(match["requested"]),
                         symbol=match["symbol"], price=_num(match["price"]), done_ms=_num(match["ms"]))
            return event
        if match := DEAL.match(body):
            event.update(kind="deal", deal=int(match["deal"]), order=int(match["order"]),
                         side=match["side"], volume=_num(match["volume"]), symbol=match["symbol"],
                         price=_num(match["price"]))
            return event
        if match := MODIFY_DONE.match(body):
            event.update(kind="modify_done", position=int(match["position"]), side=match["side"],
                         volume=_num(match["volume"]), symbol=match["symbol"],
                         to_sl=_num(match["to_sl"]), to_tp=_num(match["to_tp"]), done_ms=_num(match["ms"]))
            return event
        if match := MODIFY.match(body):
            prefix = (match["prefix"] or "").strip()
            event.update(kind={"": "modify_request", "accepted": "modify_accepted",
                               "failed": "modify_failed"}[prefix],
                         position=int(match["position"]), side=match["side"],
                         volume=_num(match["volume"]), symbol=match["symbol"] or None,
                         from_sl=_num(match["from_sl"]), from_tp=_num(match["from_tp"]),
                         to_sl=_num(match["to_sl"]), to_tp=_num(match["to_tp"]), reason=match["reason"])
            return event
        if match := VPS_HINT.match(body):
            event.update(kind="vps_hint", vps=match["vps"], vps_ms=_num(match["vps_ms"]),
                         current_ms=_num(match["current_ms"]))
            return event
    elif source == "Network":
        if match := DISCONNECTED.match(body):
            event.update(kind="disconnected", server=match["server"])
            return event
        if match := LOST.match(body):
            event.update(kind="connection_lost", server=match["server"])
            return event
        if match := AUTHORIZED.match(body):
            event.update(kind="authorized", server=match["server"], access_point=match["access"],
                         ping_ms=_num(match["ping"]))
            return event
        if match := SYNCHRONIZED.match(body):
            event.update(kind="synchronized", positions=int(match["positions"]),
                         orders=int(match["orders"]))
            return event
        if match := PREVIOUS_AUTH.match(body):
            event.update(kind="previous_authorization", ip_sha12=short_hash(match["ip"]))
            return event
    elif source == "Terminal":
        if match := SYSTEM_INFO.match(body):
            event.update(kind="system_info", os=match["os"], host=match["host"], cpus=int(match["cpus"]),
                         cpu=match["cpu"].strip(), memory_free_gb=_num(match["mem_free"]),
                         memory_total_gb=_num(match["mem_total"]), disk_free_gb=_num(match["disk_free"]),
                         disk_total_gb=_num(match["disk_total"]))
            return event
        if match := TERMINAL_STARTED.match(body):
            event.update(kind="terminal_started", build=int(match["build"]))
            return event
    event.update(kind="other", text=body[:300])
    return event


def read_log(path: Path, zone: ZoneInfo) -> list[dict]:
    path = Path(path)
    if path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError(f"terminal log exceeds size guard: {path.name}")
    day = datetime.strptime(path.stem, "%Y%m%d").date()
    text = path.read_bytes().decode("utf-16")
    events = []
    for number, line in enumerate(text.splitlines(), 1):
        fields = line.split("\t", 4)
        if len(fields) != 5:
            continue
        try:
            utc_ms, clock_flag = local_to_utc_ms(day, fields[2], zone)
        except ValueError:
            continue
        event = parse_body(fields[3], fields[4])
        event.update(day=path.stem, line=number, local_clock=fields[2], utc_ms=utc_ms)
        if clock_flag:
            event["clock_flag"] = clock_flag
        events.append(event)
    return events


def quantiles(values) -> dict | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None

    def rank(q):
        return values[min(len(values) - 1, max(0, int(round(q * (len(values) - 1)))))]

    return {"n": len(values), "min": values[0], "p50": rank(0.5), "p90": rank(0.9),
            "p99": rank(0.99), "max": values[-1]}


def _same(a, b) -> bool:
    return a is not None and b is not None and abs(a - b) < 1e-9


def build_market_chains(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pair market requests, acceptances, failures, done lines and deal lines."""
    chains, local_rejections = [], []
    pending: list[dict] = []
    by_order: dict[int, dict] = {}
    deals_by_order: dict[int, list[dict]] = defaultdict(list)
    for event in events:
        if event["kind"] == "deal":
            deals_by_order[event["order"]].append(event)

    def key(event):
        return (event.get("side"), event.get("volume"), event.get("symbol"), event.get("close_position"))

    for event in events:
        kind = event["kind"]
        if kind == "market_request":
            chain = {"request": event, "accepted": None, "failed": None, "done": None, "deals": []}
            pending.append(chain)
            chains.append(chain)
        elif kind == "market_accepted":
            for chain in pending:
                if chain["accepted"] is None and key(chain["request"]) == key(event):
                    chain["accepted"] = event
                    break
        elif kind == "market_failed":
            match = next((c for c in pending if key(c["request"]) == key(event)), None)
            if match is None:
                local_rejections.append(event)
            else:
                match["failed"] = event
                pending.remove(match)
        elif kind == "order_done":
            target = event["utc_ms"] - (event["done_ms"] or 0)
            candidates = [c for c in pending
                          if (c["request"]["side"], c["request"]["volume"], c["request"]["symbol"])
                          == (event["side"], event["volume"], event["symbol"])
                          and c["request"]["utc_ms"] <= event["utc_ms"]]
            if candidates:
                chain = min(candidates, key=lambda c: (abs(c["request"]["utc_ms"] - target),
                                                       c["request"]["utc_ms"]))
                pending.remove(chain)
            else:
                chain = {"request": None, "accepted": None, "failed": None, "done": None, "deals": []}
                chains.append(chain)
            chain["done"] = event
            chain["deals"] = deals_by_order.get(event["order"], [])
            by_order[event["order"]] = chain
    for chain in pending:
        chain["unanswered"] = True
    return chains, local_rejections


def build_modify_chains(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pair modify requests with acceptance and done/failed answers by target levels."""
    chains, local_rejections = [], []
    pending: dict[tuple, list[dict]] = defaultdict(list)
    for event in events:
        kind = event["kind"]
        if kind not in {"modify_request", "modify_accepted", "modify_done", "modify_failed"}:
            continue
        key = (event["position"], event["to_sl"], event["to_tp"])
        if kind == "modify_request":
            chain = {"request": event, "accepted": None, "done": None, "failed": None}
            pending[key].append(chain)
            chains.append(chain)
        elif kind == "modify_accepted":
            for chain in pending[key]:
                if chain["accepted"] is None:
                    chain["accepted"] = event
                    break
        elif kind == "modify_done":
            if pending[key]:
                chain = pending[key].pop(0)
            else:
                chain = {"request": None, "accepted": None, "done": None, "failed": None}
                chains.append(chain)
            chain["done"] = event
        elif kind == "modify_failed":
            if pending[key]:
                chain = pending[key].pop(0)
                chain["failed"] = event
            else:
                local_rejections.append(event)
    for items in pending.values():
        for chain in items:
            chain["unanswered"] = True
    return chains, local_rejections


def rejection_bursts(rejections: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for event in rejections:
        groups[(event["kind"], event.get("reason"), event.get("position"), event.get("side"),
                event.get("volume"))].append(event)
    bursts = []
    for (kind, reason, position, side, volume), items in groups.items():
        items.sort(key=lambda e: e["utc_ms"])
        current = [items[0]]
        for event in items[1:]:
            if event["utc_ms"] - current[-1]["utc_ms"] <= BURST_GAP_MS:
                current.append(event)
            else:
                bursts.append((kind, reason, position, side, volume, current))
                current = [event]
        bursts.append((kind, reason, position, side, volume, current))
    rows = []
    for kind, reason, position, side, volume, items in bursts:
        gaps = [b["utc_ms"] - a["utc_ms"] for a, b in zip(items, items[1:])]
        rows.append({"kind": kind, "reason": reason, "position": position, "side": side,
                     "volume": volume, "count": len(items), "start_utc_ms": items[0]["utc_ms"],
                     "end_utc_ms": items[-1]["utc_ms"], "day": items[0]["day"],
                     "median_gap_ms": sorted(gaps)[len(gaps) // 2] if gaps else None})
    return sorted(rows, key=lambda r: (-r["count"], r["start_utc_ms"]))


def network_episodes(events: list[dict]) -> list[dict]:
    episodes, open_episode = [], None
    for event in events:
        if event["kind"] in {"disconnected", "connection_lost"} and open_episode is None:
            open_episode = {"start_utc_ms": event["utc_ms"], "start_kind": event["kind"], "day": event["day"]}
        elif event["kind"] in {"authorized", "synchronized"} and open_episode is not None:
            open_episode["end_utc_ms"] = event["utc_ms"]
            open_episode["duration_ms"] = event["utc_ms"] - open_episode["start_utc_ms"]
            episodes.append(open_episode)
            open_episode = None
    if open_episode is not None:
        open_episode["end_utc_ms"] = None
        open_episode["duration_ms"] = None
        episodes.append(open_episode)
    return episodes


def load_deals(paths: list[Path]) -> tuple[dict[int, dict], dict]:
    deals, meta = {}, {}
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = payload["deals"] if isinstance(payload, dict) else payload
        for row in rows:
            ticket = int(row["ticket"])
            if ticket in deals and deals[ticket] != row:
                raise ValueError(f"conflicting broker deal {ticket}")
            deals[ticket] = row
        if isinstance(payload, dict):
            meta[str(path)] = {key: payload.get(key) for key in ("server", "currency", "timestamp_semantics")}
    return deals, meta


def broker_utc_ms(deal: dict, offset_seconds: int) -> int:
    return int(deal["time_msc"]) - offset_seconds * 1000


def clock_brackets(market_chains, events, deals, offset_seconds) -> dict:
    per_day = defaultdict(lambda: {"lower": [], "upper": []})
    for chain in market_chains:
        request = chain["request"]
        if request is None or not chain["deals"]:
            continue
        first = min(chain["deals"], key=lambda d: d["utc_ms"])
        deal = deals.get(first["deal"])
        if deal is None:
            continue
        per_day[request["day"]]["lower"].append(
            (request["utc_ms"] - broker_utc_ms(deal, offset_seconds), request["line"]))
    for event in events:
        if event["kind"] == "deal" and event["deal"] in deals:
            per_day[event["day"]]["upper"].append(
                (event["utc_ms"] - broker_utc_ms(deals[event["deal"]], offset_seconds), event["line"]))
    result = {}
    for day, bounds in sorted(per_day.items()):
        lower = max(bounds["lower"]) if bounds["lower"] else None
        upper = min(bounds["upper"]) if bounds["upper"] else None
        status = ("insufficient" if lower is None or upper is None
                  else "consistent" if lower[0] <= upper[0] else "inconsistent")
        result[day] = {"status": status, "lower_ms": lower[0] if lower else None,
                       "lower_line": lower[1] if lower else None,
                       "upper_ms": upper[0] if upper else None,
                       "upper_line": upper[1] if upper else None,
                       "lower_count": len(bounds["lower"]), "upper_count": len(bounds["upper"])}
    return result


def position_timelines(events, modify_chains, deals, offset_seconds) -> list[dict]:
    """Protection timeline per broker position, using deal identities as anchors."""
    deal_lines = {e["deal"]: e for e in events if e["kind"] == "deal"}
    positions = defaultdict(lambda: {"entries": [], "exits": []})
    for ticket, deal in deals.items():
        position = int(deal.get("position_id") or 0)
        if not position or ticket not in deal_lines:
            continue
        side = "entries" if int(deal.get("entry", -1)) == 0 else "exits"
        positions[position][side].append((ticket, deal))
    chains_by_position = defaultdict(list)
    for chain in modify_chains:
        anchor = chain["request"] or chain["done"]
        if anchor is not None:
            chains_by_position[anchor["position"]].append(chain)
    rows = []
    for position, parts in sorted(positions.items()):
        if not parts["entries"]:
            continue
        entry_ticket, entry = min(parts["entries"], key=lambda item: item[1]["time_msc"])
        entry_line = deal_lines[entry_ticket]
        chains = sorted(chains_by_position.get(position, []),
                        key=lambda c: (c["request"] or c["done"])["utc_ms"])
        first_tp = next((c for c in chains if c["done"] and (c["done"]["to_tp"] or 0) > 0), None)
        row = {"position": position, "day": entry_line["day"],
               "entry_deal": entry_ticket, "entry_price": float(entry["price"]),
               "entry_volume": float(entry["volume"]),
               "entry_broker_utc_ms": broker_utc_ms(entry, offset_seconds),
               "entry_line_utc_ms": entry_line["utc_ms"],
               "modify_requests": sum(1 for c in chains if c["request"] is not None),
               "modify_done": sum(1 for c in chains if c["done"] is not None),
               "modify_server_rejected": sum(1 for c in chains if c["failed"] is not None),
               "first_tp_done_after_entry_ms": (first_tp["done"]["utc_ms"] - entry_line["utc_ms"])
               if first_tp else None, "exits": []}
        for exit_ticket, exit_deal in sorted(parts["exits"], key=lambda item: item[1]["time_msc"]):
            exit_line = deal_lines[exit_ticket]
            reason = EXIT_REASONS.get(int(exit_deal.get("reason", -1)), str(exit_deal.get("reason")))
            done_before = [c for c in chains if c["done"] and c["done"]["utc_ms"] <= exit_line["utc_ms"]]
            active = max(done_before, key=lambda c: c["done"]["utc_ms"]) if done_before else None
            level = None
            if active is not None:
                level = active["done"]["to_tp"] if reason == "tp" else active["done"]["to_sl"] if reason == "sl" else None
            installing = None
            if level is not None:
                field = "to_tp" if reason == "tp" else "to_sl"
                installing = next((c for c in chains if c["done"] and _same(c["done"][field], level)), None)
            price = float(exit_deal["price"])
            exit_row = {"deal": exit_ticket, "reason": reason, "price": price,
                        "volume": float(exit_deal["volume"]),
                        "broker_utc_ms": broker_utc_ms(exit_deal, offset_seconds),
                        "line_utc_ms": exit_line["utc_ms"], "active_level": level,
                        "level_matches_price": _same(level, price) if level is not None else None,
                        "install_request_utc_ms": installing["request"]["utc_ms"]
                        if installing and installing["request"] else None,
                        "install_done_utc_ms": installing["done"]["utc_ms"] if installing else None}
            row["exits"].append(exit_row)
        rows.append(row)
    return rows


def summarize(events, market_chains, market_local, modify_chains, modify_local, positions, clock):
    days = sorted({e["day"] for e in events})
    per_day = {}
    for day in days:
        day_market = [c for c in market_chains if (c["done"] or c["request"] or {}).get("day") == day]
        day_modify = [c for c in modify_chains if (c["done"] or c["request"] or {}).get("day") == day]
        day_events = [e for e in events if e["day"] == day]
        per_day[day] = {
            "events": len(day_events),
            "market_roundtrip_ms": quantiles([c["done"]["done_ms"] for c in day_market if c["done"]]),
            "modify_roundtrip_ms": quantiles([c["done"]["done_ms"] for c in day_modify if c["done"]]),
            "modify_accepted_to_done_ms": quantiles(
                [c["done"]["utc_ms"] - c["accepted"]["utc_ms"] for c in day_modify
                 if c["done"] and c["accepted"]]),
            "server_rejections": dict(Counter(
                f"{c['failed']['kind']}:{c['failed'].get('reason')}" for c in day_market + day_modify
                if c.get("failed"))),
            "local_rejections": dict(Counter(
                f"{e['kind']}:{e.get('reason')}" for e in market_local + modify_local if e["day"] == day)),
            "network": dict(Counter(e["kind"] for e in day_events
                                    if e["kind"] in {"disconnected", "connection_lost", "authorized"})),
            "ping_ms": quantiles([e.get("ping_ms") for e in day_events if e["kind"] == "authorized"]),
            "clock": clock.get(day),
        }
    return per_day


def audit(log_paths, deal_paths, zone_name=DEFAULT_ZONE, offset_seconds=DEFAULT_BROKER_OFFSET_SECONDS):
    zone = ZoneInfo(zone_name)
    log_paths = sorted(Path(p) for p in log_paths)
    if not log_paths:
        raise ValueError("no terminal logs supplied")
    if len({p.stem for p in log_paths}) != len(log_paths):
        raise ValueError("duplicate terminal log day")
    events = []
    for path in log_paths:
        events.extend(read_log(path, zone))
    events.sort(key=lambda e: (e["utc_ms"], e["day"], e["line"]))
    deals, deal_meta = load_deals(deal_paths) if deal_paths else ({}, {})
    market_chains, market_local = build_market_chains(events)
    modify_chains, modify_local = build_modify_chains(events)
    clock = clock_brackets(market_chains, events, deals, offset_seconds) if deals else {}
    positions = position_timelines(events, modify_chains, deals, offset_seconds) if deals else []
    per_day = summarize(events, market_chains, market_local, modify_chains, modify_local, positions, clock)
    deal_lines = {e["deal"] for e in events if e["kind"] == "deal"}
    covered = {p.stem for p in log_paths}
    missing = []
    if deals:
        for ticket, deal in deals.items():
            if int(deal.get("entry", -1)) not in (0, 1) or not deal.get("symbol"):
                continue
            local_day = datetime.fromtimestamp(broker_utc_ms(deal, offset_seconds) / 1000, timezone.utc) \
                .astimezone(zone).strftime("%Y%m%d")
            if local_day in covered and ticket not in deal_lines:
                missing.append(ticket)
    exits = [x for p in positions for x in p["exits"]]
    tp_exits = [x for x in exits if x["reason"] == "tp"]
    return {
        "contract": CONTRACT, "status": "diagnostic_only",
        "parameters": {"local_zone": zone_name, "broker_offset_seconds_hypothesis": offset_seconds,
                       "burst_gap_ms": BURST_GAP_MS},
        "days": sorted(covered),
        "counts": dict(Counter(e["kind"] for e in events)),
        "market_chains": {"total": len(market_chains),
                          "with_request": sum(1 for c in market_chains if c["request"]),
                          "done": sum(1 for c in market_chains if c["done"]),
                          "server_rejected": sum(1 for c in market_chains if c["failed"]),
                          "unanswered": sum(1 for c in market_chains if c.get("unanswered")),
                          "done_without_request_line": sum(1 for c in market_chains
                                                           if c["done"] and not c["request"])},
        "modify_chains": {"total": len(modify_chains),
                          "done": sum(1 for c in modify_chains if c["done"]),
                          "server_rejected": sum(1 for c in modify_chains if c["failed"]),
                          "unanswered": sum(1 for c in modify_chains if c.get("unanswered")),
                          "done_without_request_line": sum(1 for c in modify_chains
                                                           if c["done"] and not c["request"])},
        "local_rejections": {"market": len(market_local), "modify": len(modify_local)},
        "overall": {
            "market_roundtrip_ms": quantiles([c["done"]["done_ms"] for c in market_chains if c["done"]]),
            "modify_roundtrip_ms": quantiles([c["done"]["done_ms"] for c in modify_chains if c["done"]]),
            "first_tp_done_after_entry_ms": quantiles([p["first_tp_done_after_entry_ms"] for p in positions]),
            "tp_exit_after_install_done_ms": quantiles(
                [x["line_utc_ms"] - x["install_done_utc_ms"] for x in tp_exits if x["install_done_utc_ms"]]),
        },
        "per_day": per_day,
        "rejection_bursts": rejection_bursts(market_local + modify_local)[:50],
        "network_episodes": network_episodes(events),
        "system_info": [{k: e[k] for k in ("day", "cpus", "cpu", "memory_free_gb", "memory_total_gb",
                                             "disk_free_gb", "disk_total_gb")}
                        for e in events if e["kind"] == "system_info"],
        "vps_hints": [{k: e[k] for k in ("day", "vps", "vps_ms", "current_ms")}
                      for e in events if e["kind"] == "vps_hint"],
        "deal_binding": {"deal_lines": len(deal_lines), "bound": len(deal_lines & set(deals)),
                         "unbound_deal_lines": sorted(deal_lines - set(deals)) if deals else None,
                         "broker_deals_missing_in_logs": sorted(missing) if deals else None,
                         "deal_sources": deal_meta},
        "tp_exits": {"total": len(tp_exits),
                     "level_matches_price": sum(1 for x in tp_exits if x["level_matches_price"]),
                     "install_done_before_exit_line": sum(1 for x in tp_exits if x["install_done_utc_ms"])},
        "limitations": [
            "Terminal clock is the VM's local clock; broker times use the declared offset hypothesis.",
            "Market request lines carry no order id; pairing uses side, volume, symbol and the terminal's own duration.",
            "A failure without a pending request line is classified as a local terminal rejection.",
            "Terminal logs do not separate network transit from trade-server processing.",
            "This is observed execution evidence, not a simulation or a live policy change.",
        ],
        "_events": events, "_positions": positions,
    }


def _display(path: Path) -> str:
    """Stable, machine-independent name for a hashed input."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def write_outputs(result: dict, output_dir: Path, inputs: list[Path]) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    events = result.pop("_events")
    positions = result.pop("_positions")
    events_path = output_dir / "events.jsonl.gz"
    with gzip.open(events_path, "wt", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=True) + "\n")
    positions_path = output_dir / "positions.json"
    positions_path.write_text(json.dumps(positions, indent=1, sort_keys=True, ensure_ascii=True) + "\n",
                              encoding="utf-8")
    result["inputs_sha256"] = {_display(path): digest(path) for path in inputs}
    result["outputs_sha256"] = {events_path.name: digest(events_path),
                                positions_path.name: digest(positions_path)}
    summary_path = output_dir / "summary.json"
    with summary_path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=1, sort_keys=True, ensure_ascii=True)
        handle.write("\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, action="append", default=[])
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--glob", default="*.log")
    parser.add_argument("--deals", type=Path, action="append", default=[])
    parser.add_argument("--local-zone", default=DEFAULT_ZONE)
    parser.add_argument("--broker-offset-seconds", type=int, default=DEFAULT_BROKER_OFFSET_SECONDS)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    logs = list(args.log)
    if args.log_dir is not None:
        logs.extend(sorted(args.log_dir.glob(args.glob)))
    result = audit(logs, args.deals, args.local_zone, args.broker_offset_seconds)
    inputs = sorted(set(logs)) + list(args.deals) + [Path(__file__)]
    result = write_outputs(result, args.output_dir, inputs)
    print(json.dumps({"days": result["days"], "market": result["market_chains"],
                      "modify": result["modify_chains"], "tp_exits": result["tp_exits"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
