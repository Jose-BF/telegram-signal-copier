"""One fixed own-rule diagnostic, never search, admission or observed accounting.

The public API checks deadlines between bounded operations. The CLI additionally
enforces a process timeout, including time spent inside native engine calls.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
import math
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from broker_tick_clock import normalize_server_msc
from research.causal_replay import (
    RAW_FIELDS, align_conversion_quotes, compile_signals, fx_validation_contract, make_path, time_ns, utc,
)
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import simulate
from research.dubai_iterative.fast_engine import FastEvaluator
from research.dubai_iterative.oracle import oracle_simulate
from research.dubai_iterative.portfolio import build_portfolio_tape, reconstruct_portfolio
from research.execution_profile import execution_from_mapping, execution_to_scenario
from research.iterative_provenance import implementation_identity
from research.telegram_export import CHATS, SCENARIOS, load_admission, to_causal_signals


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "own_rule_study_v1"
LIMITS = {"max_signals": 40, "max_evaluations": 120, "max_wall_seconds": 600, "max_quotes": 4_000_000}
EXTRA_SOURCES = ("research/strategy_study.py", "tools/run_strategy_study.py",
                 "research/telegram_export.py", "research/causal_replay.py", "parser.py",
                 "provider_signal_catalog.py", "interpretation_firewall.py")
FORBIDDEN_IMPORTS = {"MetaTrader5", "listener", "executor", "telethon", "classifier",
                     "research.dubai_iterative.search", "research.gold_iterative.search"}
ARTIFACTS = {"protocol.json", "results.json", "manifest.json"}
CONTROL_COHORTS = {"canal1": (1642806869, "dubai_control"), "canal2": (3908582492, "gold_current_control")}
LIMITATIONS = [
    "Execution is hypothetical; export scenario clocks are not observed receipts. Control receipts come only from frozen raw ts.",
    "Exports admit explicit NOW triggers in one exact historical chat; controls use the existing NOW/declared-sticker compiler.",
    "Control numeric chat IDs are declared channel mappings, not raw chat evidence; namespaces never merge with exports.",
    "Own rules ignore provider management, SL, TP, BE and reward/risk levels.",
    "EUR two-digit account / USD profit / EURUSD account-base conversion only; declared intraday zero commission and swap.",
    "No margin, stop-out, capital adequacy or unlimited-capital assumption; capital, if declared, is not simulated.",
    "Schema2 serial quote-clock profiles; unsupported capabilities remain engine blockers, not legacy fallbacks.",
    "No observed state, overnight, partial broker fills, requotes or independent broker-realism certification.",
    "Complete quote endpoints and bounded gaps are a diagnostic coverage rule, not proof of complete broker history.",
    "FX defaults to strict prior-quote age; explicit historical interval validation uses the next timestamp only as coverage evidence, never its price.",
    "FX bracketing does not certify a tape, period costs, monetary accounting or real-time availability of coverage evidence.",
    "Fixed retrospective diagnostic only; no search, optimization, reserved OOS access, selection or automatic admission.",
]


def _digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _default(value):
    if isinstance(value, (datetime, Decimal, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def _encode(value):
    return (json.dumps(value, default=_default, sort_keys=True, ensure_ascii=True,
                       allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")


def _sha(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid(value):
    raise ValueError(f"nonfinite JSON value: {value}")


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"),
                      object_pairs_hook=_unique, parse_constant=_invalid)


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"{label} requires exactly these fields: {sorted(expected)}")


def _positive(value, label, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0 or (maximum and value > maximum):
        raise ValueError(f"invalid {label}")


def _utc_explicit(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("study timestamps require explicit UTC (+00:00 or Z)")
    return parsed.astimezone(timezone.utc)


def _path(value, base):
    if not isinstance(value, str) or not value.strip() or "://" in value or value.startswith(("\\\\", "//")):
        raise ValueError("only explicit local file paths are allowed")
    path = Path(value)
    return (base / path).resolve()


def _proof(value, base, *, symbol=None):
    _keys(value, {"path", "sha256"} | ({"symbol"} if symbol else set()), "source proof")
    digest = value["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("source SHA256 must be lowercase hex")
    if symbol and value["symbol"] != symbol:
        raise ValueError(f"unsupported source symbol; expected {symbol}")
    value["path"] = str(_path(value["path"], base))


def load_config(config_path):
    """Strict JSON configuration; all data paths resolve against its directory."""
    path = Path(config_path).resolve()
    if path.stat().st_size > 131_072:
        raise ValueError("study config exceeds 128 KiB")
    config = _read(path)
    if not isinstance(config, dict) or config.get("input_kind") not in {"telegram_export", "causal_control"}:
        raise ValueError("input_kind must be telegram_export or causal_control")
    export = config["input_kind"] == "telegram_export"
    input_key = "admission" if export else "source_study"
    _keys(config, {"schema_version", "input_kind", "data_use", "search_candidates", input_key, "cohort", "scenario",
                  "period", "horizon_seconds", "max_market_gap_ms", "max_fx_age_ms", "broker_clock",
                  "sources", "strategy", "execution", "money", "budget"}
          | (set(config) & {"max_fx_interval_ms"}), "study config")
    if config["schema_version"] != SCHEMA or config["data_use"] != "retrospective_development_only":
        raise ValueError("only fixed retrospective development diagnostics are supported; no reserved OOS")
    if type(config["search_candidates"]) is not int or config["search_candidates"] != 0:
        raise ValueError("search candidate budget must be exactly zero")
    _keys(config["budget"], LIMITS, "budget")
    for key, limit in LIMITS.items():
        value = config["budget"][key]
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError(f"budget {key} must be an integer in [1, {limit}]")
    _keys(config["cohort"], {"chat_id", "name"} | (set() if export else {"channel"}), "cohort")
    chat = config["cohort"]["chat_id"]
    if export and (type(chat) is not int or chat not in CHATS or config["cohort"]["name"] != CHATS[chat][0]):
        raise ValueError("select an exact supported historical chat/cohort; current Gold is outside this universe")
    if not export and (type(chat) is not int or CONTROL_COHORTS.get(config["cohort"]["channel"]) != (chat, config["cohort"]["name"])):
        raise ValueError("select an exact control channel/chat declaration; do not merge historical/current chats")
    if config["scenario"] not in (SCENARIOS if export else {"observed_raw_receipt"}):
        raise ValueError("select an explicit admission scenario")
    _keys(config["period"], {"start_utc", "end_exclusive_utc"}, "period")
    start, end = (_utc_explicit(config["period"][key]) for key in ("start_utc", "end_exclusive_utc"))
    if end <= start:
        raise ValueError("period end must follow start")
    for key, maximum in (("horizon_seconds", 14_400), ("max_market_gap_ms", 60_000), ("max_fx_age_ms", 60_000)):
        if type(config[key]) is not int or not 1 <= config[key] <= maximum:
            raise ValueError(f"{key} must be a positive integer <= {maximum}")
    interval = config.setdefault("max_fx_interval_ms", config["max_fx_age_ms"])
    if type(interval) is not int:
        raise ValueError("max_fx_interval_ms must be an integer")
    fx_validation_contract(config["max_fx_age_ms"], interval)
    _keys(config["broker_clock"], {"utc_offset_seconds", "status"}, "broker clock")
    normalize_server_msc(0, config["broker_clock"]["utc_offset_seconds"])
    if config["broker_clock"]["status"] != "declared_hypothesis":
        raise ValueError("broker clock must be explicitly declared_hypothesis, not auto-certified")
    _proof(config[input_key], path.parent)
    _keys(config["sources"], {"market", "conversion"}, "sources")
    source_paths = []
    for role, symbol in (("market", "XAUUSD"), ("conversion", "EURUSD")):
        proofs = config["sources"][role]
        if not isinstance(proofs, list) or not 1 <= len(proofs) <= 40:
            raise ValueError("provide 1..40 explicit Parquet sources per role")
        for proof in proofs:
            _proof(proof, path.parent, symbol=symbol)
            if Path(proof["path"]).suffix.lower() != ".parquet":
                raise ValueError("quotes must be explicit Parquet files")
            source_paths.append(proof["path"])
    if len(set(source_paths)) != len(source_paths):
        raise ValueError("duplicate or mixed source paths")
    try:
        genome = StrategyGenome.from_dict(config["strategy"])
        errors = genome.validation_errors()
    except (TypeError, AttributeError, OverflowError) as exc:
        raise ValueError(f"invalid genome: {exc}") from exc
    if errors:
        raise ValueError(f"invalid own-rule genome: {errors}")
    if (genome.schema_version != 2 or genome.entry_mode == "actual_mt5" or genome.provider_management_mode != "ignore"
            or genome.stop_mode == "provider" or genome.be_mode == "provider"
            or genome.target_mode in {"provider_per_leg", "provider_target_all"}
            or genome.context_filter_mode == "min_reward_risk"):
        raise ValueError("own-rule genome cannot depend on observed entries or provider management/levels")
    if genome.leg_count > 12:
        raise ValueError("own-rule diagnostic is bounded to 12 legs")
    for name in ("schema_version", "leg_count", "entry_expiry_min", "time_exit_min", "lineage_depth"):
        if type(getattr(genome, name)) is not int:
            raise ValueError(f"genome {name} must be an integer")
    execution = execution_from_mapping(config["execution"])
    if execution.market is None or execution.protection is None:
        raise ValueError("explicit integrated market and protection profiles are required")
    if execution.protection.initial_protections:
        raise ValueError("observed initial protection injection is prohibited")
    money = config["money"]
    fixed_money = {"mode": "hypothetical_intraday_zero_cost", "account_currency": "EUR", "profit_currency": "USD",
                   "currency_digits": 2, "conversion_symbol": "EURUSD", "conversion_orientation": "account_base_profit_quote",
                   "commission_per_lot": 0, "swap_per_lot": 0}
    _keys(money, set(fixed_money) | {"contract_size", "rollover_hour_server", "capital_eur"}, "money")
    if any(money[key] != value or isinstance(money[key], bool) for key, value in fixed_money.items()):
        raise ValueError("unsupported money contract; only explicit EUR intraday zero-cost hypothesis")
    if type(money["currency_digits"]) is not int or any(type(money[key]) not in (int, float) for key in ("commission_per_lot", "swap_per_lot")):
        raise ValueError("money precision and cost types must be explicit")
    _positive(money["contract_size"], "contract size", 1_000_000)
    if type(money["rollover_hour_server"]) is not int or not 0 <= money["rollover_hour_server"] <= 23:
        raise ValueError("explicit broker rollover hour required")
    if money["capital_eur"] is not None:
        _positive(money["capital_eur"], "reference capital")
    config["strategy"] = genome.to_dict()
    config["execution"] = asdict(execution)
    return config


def current_identity():
    return {"iterative": implementation_identity(),
            "runner_sources": {name: _digest(ROOT / name) for name in EXTRA_SOURCES}}


# Detect edits after imports as well as edits between freeze and completion.
# A fresh interpreter is required after changing any bound source.
_IMPORTED_SOURCES = {**implementation_identity()["source_sha256"],
                     **{name: _digest(ROOT / name) for name in EXTRA_SOURCES if (ROOT / name).is_file()}}


def _check_loaded_sources(actual):
    sources = {**actual["iterative"]["source_sha256"], **actual["runner_sources"]}
    if any(sources.get(name) != value for name, value in _IMPORTED_SOURCES.items()):
        raise ValueError("loaded implementation changed; use a fresh interpreter")


def _check_current(identity):
    actual = current_identity()
    if actual != identity:
        raise ValueError("implementation or runtime changed during study; use a new output")
    _check_loaded_sources(actual)
    if FORBIDDEN_IMPORTS.intersection(sys.modules):
        raise ValueError("offline import boundary violated")


def _deadline(started, config):
    if time.monotonic() - started >= config["budget"]["max_wall_seconds"]:
        raise TimeoutError("study wall-time budget exhausted; archive is not complete")


def _watch(path, expected, watched):
    path = Path(path).resolve()
    if _digest(path) != expected:
        raise ValueError(f"source hash mismatch or changed: {path.name}")
    prior = watched.setdefault(str(path), expected)
    if prior != expected:
        raise ValueError("mixed source hashes")


def _verify_sources(watched):
    for path, expected in watched.items():
        if _digest(path) != expected:
            raise ValueError(f"frozen source changed: {Path(path).name}")


def _load_tapes(config, watched):
    frames, total = {}, 0
    offset_ms = -normalize_server_msc(0, config["broker_clock"]["utc_offset_seconds"])
    # Inspect both footers before allocating either tape; count every source row.
    for proofs in config["sources"].values():
        for proof in proofs:
            _watch(proof["path"], proof["sha256"], watched)
            total += pq.read_metadata(proof["path"]).num_rows
    if total > config["budget"]["max_quotes"]:
        raise ValueError("quote budget exceeded before loading Parquet rows")
    for role, proofs in config["sources"].items():
        pieces = []
        for proof in proofs:
            data = Path(proof["path"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != proof["sha256"]:
                raise ValueError("quote source changed before decode")
            frame = pd.read_parquet(io.BytesIO(data), columns=["time_utc", "source_time_msc", "bid", "ask"])
            del data
            dtype = frame["time_utc"].dtype
            if not isinstance(dtype, pd.DatetimeTZDtype) or str(dtype.tz) != "UTC":
                raise ValueError("Parquet time_utc requires an explicit UTC timestamp type")
            ns = frame["time_utc"].array.as_unit("ns").asi8
            raw = frame["source_time_msc"]
            if raw.dtype.kind not in "iu" or raw.isna().any():
                raise ValueError("broker source_time_msc must be integer milliseconds")
            expected = (raw.to_numpy(dtype=np.int64) - offset_ms) * 1_000_000
            if not np.array_equal(ns, expected):
                raise ValueError("broker quote clock mismatch")
            values = frame[["bid", "ask"]].to_numpy(dtype=float)
            if (frame["time_utc"].isna().any() or not frame["time_utc"].is_monotonic_increasing
                    or not np.isfinite(values).all() or (values <= 0).any()
                    or (values[:, 1] < values[:, 0]).any()):
                raise ValueError("invalid or unsorted Bid/Ask tape")
            if pieces and len(frame) and len(pieces[-1]) and frame["time_utc"].iloc[0] <= pieces[-1]["time_utc"].iloc[-1]:
                raise ValueError("overlapping or unordered Parquet shards; no silent deduplication")
            pieces.append(frame)
        frames[role] = pd.concat(pieces, ignore_index=True)
    _verify_sources(watched)
    return frames, total


def _arrays(frame):
    return (frame["time_utc"].array.as_unit("ns").asi8,
            frame["bid"].to_numpy(dtype=float), frame["ask"].to_numpy(dtype=float))


class _CanonicalSource:
    """Protect canonical input memory; mutation guards are not history certification."""

    def __init__(self, frame, evidence):
        self._frame, self._evidence = frame.copy(deep=True), deepcopy(evidence)
        self._content_identity = self._fingerprint()

    def _fingerprint(self):
        rows = pd.util.hash_pandas_object(self._frame, index=True).to_numpy(dtype="<u8")
        return _sha({"rows": hashlib.sha256(rows.tobytes()).hexdigest(),
                     "columns": list(self._frame.columns), "dtypes": [str(d) for d in self._frame.dtypes],
                     "evidence": self._evidence})

    def verify_source(self):
        if self._fingerprint() != self._content_identity:
            raise ValueError("canonical source frame or evidence changed in memory")

    @property
    def content_identity(self):
        self.verify_source()
        return self._content_identity

    @property
    def frame(self):
        self.verify_source()
        return self._frame.copy(deep=True)

    @property
    def evidence(self):
        self.verify_source()
        return deepcopy(self._evidence)

    def load_day(self, day):
        self.verify_source()
        frame = self._frame.loc[self._frame["time_utc"].dt.date == day].copy(deep=True)
        evidence = deepcopy(self._evidence)
        self.verify_source()
        return frame, evidence, ()


def _coverage(signal, cutoff, market, conversion, config):
    start_ns, end_ns = time_ns(signal.observed_at), time_ns(cutoff)
    times = market[0]
    selected = times[(times >= start_ns) & (times <= end_ns)]
    reasons = []
    if not len(selected) or selected[0] - start_ns > config["max_market_gap_ms"] * 1_000_000:
        reasons.append("market_start_coverage_missing")
    if not len(times) or times[-1] < end_ns:
        reasons.append("market_horizon_coverage_missing")
    if len(selected) and (np.diff(selected) > config["max_market_gap_ms"] * 1_000_000).any():
        reasons.append("market_gap_exceeds_contract")
    if len(selected) and end_ns - selected[-1] > config["max_market_gap_ms"] * 1_000_000:
        reasons.append("market_end_gap_exceeds_contract")
    if not len(conversion[0]):
        reasons.append("conversion_coverage_missing")
    elif len(selected):
        _, _, _, valid = align_conversion_quotes(selected, conversion, max_fx_age_ms=config["max_fx_age_ms"],
                                                max_fx_interval_ms=config.get("max_fx_interval_ms"))
        if not valid.all():
            reasons.append("conversion_gap_or_stale")
    shift = timedelta(seconds=config["broker_clock"]["utc_offset_seconds"], hours=-config["money"]["rollover_hour_server"])
    if (signal.observed_at + shift).date() != (cutoff + shift).date():
        reasons.append("overnight_outside_money_universe")
    return reasons


def _archive_check(output):
    if output.exists():
        if not output.is_dir() or any(p.name not in ARTIFACTS or not p.is_file() or p.is_symlink() for p in output.iterdir()):
            raise ValueError("mixed or unexpected archive artifacts")


def _publish(output, payloads):
    _archive_check(output)
    encoded = {name: _encode(value) for name, value in payloads.items()}
    for name, value in encoded.items():
        target = output / name
        if target.exists() and target.read_bytes() != value:
            raise ValueError(f"immutable archive conflict: {name}")
    output.mkdir(parents=True, exist_ok=True)
    for name, value in encoded.items():
        target = output / name
        if not target.exists():
            with target.open("xb") as stream:
                stream.write(value)


def _row_inventory(bundle, config):
    rows = []
    start, end = (_utc_explicit(config["period"][key]) for key in ("start_utc", "end_exclusive_utc"))
    triggers = {row["signal_id"]: row for row in bundle["triggers"][config["scenario"]]}
    for identity in bundle["identities"]:
        admission = identity["admission"][config["scenario"]]
        trigger = triggers.get(identity["identity"])
        reasons, status = list(admission["reasons"]), "admission_blocked"
        inside = identity["chat_id"] == config["cohort"]["chat_id"]
        clocks = [utc(revision["published_utc"]) for revision in identity["revisions"] if revision["published_utc"]]
        if not inside:
            status, reasons = "outside_universe", [*reasons, "other_chat_not_selected"]
        elif clocks and all(not start <= clock < end for clock in clocks):
            status, reasons = "outside_universe", [*reasons, "publication_outside_study_period"]
        elif trigger and not start <= utc(trigger["trigger_utc"]) < end:
            status, reasons = "outside_universe", [*reasons, "scenario_trigger_outside_study_period"]
        elif admission["status"] == "admitted":
            if trigger is None:
                raise ValueError("admitted identity is missing its trigger")
            status = "pending"
        rows.append({"signal_id": identity["identity"], "chat_id": identity["chat_id"],
            "message_id": identity["message_id"], "cohort": identity["cohort"],
            "scenario": config["scenario"], "admission": admission, "trigger": trigger,
            "recognized_text_trigger": identity["has_recognized_text_trigger"],
            "status": status, "reasons": sorted(set(reasons)), "engines": {}, "mismatches": {}})
    return rows


def _export_input(config, watched):
    admission_dir = Path(config["admission"]["path"])
    _watch(admission_dir / "manifest.json", config["admission"]["sha256"], watched)
    bundle = load_admission(admission_dir)
    manifest = _read(admission_dir / "manifest.json")
    for name, proof in manifest["artifacts"].items():
        _watch(admission_dir / name, proof["sha256"], watched)
    for name in ("telegram_export.py", "parser.py"):
        source = ROOT / ("research" if name == "telegram_export.py" else "") / name
        if bundle["implementation"]["files"].get(name) != _digest(source):
            raise ValueError("admission implementation changed; prepare a new archive")
    start, end = (_utc_explicit(config["period"][key]) for key in ("start_utc", "end_exclusive_utc"))
    if start < utc(bundle["contract"]["start_utc"]) or end > utc(bundle["contract"]["end_exclusive_utc"]):
        raise ValueError("study period exceeds the admission period")
    signals = {signal.signal_id: signal for signal in to_causal_signals(
        bundle, scenario=config["scenario"], chat_id=config["cohort"]["chat_id"])}
    return _row_inventory(bundle, config), signals, {
        "admission_archive_identity": manifest["archive_identity_sha256"],
        "source_admission_blockers": bundle["contract"].get("open_gates", []), "input_diagnostics": []}


def _control_input(config, watched):
    source = Path(config["source_study"]["path"])
    source_sha = config["source_study"]["sha256"]
    _watch(source / "protocol.json", source_sha, watched)
    protocol = _read(source / "protocol.json")
    if (protocol.get("contract") != "raw_message_control_diagnostic_v2"
            or protocol.get("search_candidate_budget") != 0 or protocol.get("mass_search_authorized") is not False):
        raise ValueError("unsupported or non-diagnostic causal control source")
    _watch(source / "raw_messages.json", protocol["raw_messages_sha256"], watched)
    for role, symbol in (("market", "XAUUSD"), ("conversion", "EURUSD")):
        declared = protocol["tapes"][symbol]
        declared = [declared] if isinstance(declared, dict) else declared
        if (not isinstance(declared, list) or not 1 <= len(declared) <= 40
                or any(not isinstance(item, dict) for item in declared)):
            raise ValueError("control source tape requires 1..40 ordered source proofs")
        proofs = []
        for item in declared:
            proof = dict(item)
            _proof(proof, ROOT)
            proofs.append(proof | {"symbol": symbol})
        if config["sources"][role] != proofs:
            raise ValueError("control source tape differs from frozen protocol")
    if config["broker_clock"]["utc_offset_seconds"] != protocol["broker_epoch_offset_seconds"]:
        raise ValueError("control broker clock differs from frozen protocol")
    if any(config["money"][name] != protocol[name] for name in ("account_currency", "currency_digits", "contract_size")):
        raise ValueError("control money inputs differ from frozen source contract")
    start, end = (_utc_explicit(config["period"][key]) for key in ("start_utc", "end_exclusive_utc"))
    source_start, source_end = utc(protocol["start_utc"]), utc(protocol["cutoff_utc"])
    if start < source_start or end > source_end:
        raise ValueError("study period exceeds the frozen control period")
    messages = _read(source / "raw_messages.json")
    if not isinstance(messages, list) or len(messages) > 100_000:
        raise ValueError("raw control requires a bounded message array")
    if any(not isinstance(row, dict) or set(row) - set(RAW_FIELDS) for row in messages):
        raise ValueError("control raw messages may contain only causal input fields, not observed fills/state")
    compiled, diagnostics = compile_signals(messages, start=source_start, cutoff=source_end,
                                           sticker_directions=protocol["sticker_directions"])
    if [signal.signal_id for signal in compiled] != protocol["expected_signal_ids"]:
        raise ValueError("control expected signal IDs/order mismatch; no subset fallback")
    original = {signal.signal_id: signal for signal in compiled}
    groups = {}
    for index, message in enumerate(messages):
        key = f"{message['channel']}_{message['message_id']}"
        groups.setdefault(key, []).append((index, message))
    selected_channel = config["cohort"]["channel"]
    rows, signals = [], {}
    for key, occurrences in groups.items():
        channel = occurrences[0][1]["channel"]
        selected = channel == selected_channel
        signal = original.get(key)
        scoped_id = f"causal_control:{source_sha}:{key}"
        row_diagnostics = [item for item in diagnostics if item["channel"] == channel
                           and item["message_id"] == occurrences[0][1]["message_id"]]
        reasons = sorted({item["reason"] for item in row_diagnostics})
        status, trigger = "admission_blocked", None
        if not selected:
            status, reasons = "outside_universe", [*reasons, "other_channel_not_selected"]
        elif signal and not (start <= signal.observed_at < end and start <= signal.published_at < end):
            status, reasons = "outside_universe", [*reasons, "control_trigger_outside_study_period"]
        elif signal:
            status = "pending"
            # Provider events are compiled for source integrity, then discarded.
            signals[scoped_id] = replace(signal, signal_id=scoped_id, provider_events=())
            trigger = {"signal_id": scoped_id, "source_signal_id": key, "direction": signal.direction,
                "published_utc": signal.published_at.isoformat(), "trigger_utc": signal.observed_at.isoformat(),
                "received_utc": signal.observed_at.isoformat(), "message_revision_id": signal.message_revision_id,
                "scenario": config["scenario"], "clock_is_hypothesis": False, "provider_events": []}
        else:
            reasons.append("no_compiled_trigger_own_rule_control")
        rows.append({"signal_id": scoped_id, "source_signal_id": key,
            "chat_id": config["cohort"]["chat_id"] if selected else None, "channel": channel,
            "chat_identity_basis": "declared_channel_mapping_not_raw_chat_evidence",
            "message_id": occurrences[0][1]["message_id"],
            "cohort": config["cohort"]["name"] if selected else "other_control_channel",
            "scenario": config["scenario"], "trigger": trigger, "recognized_text_trigger": signal is not None,
            "source_occurrence_indices": [index for index, _ in occurrences],
            "source_revision_ids": [message["message_revision_id"] for _, message in occurrences],
            "input_diagnostics": row_diagnostics, "status": status, "reasons": sorted(set(reasons)),
            "engines": {}, "mismatches": {}})
    return rows, signals, {"source_protocol_sha256": source_sha,
        "source_admission_blockers": protocol["admission_blockers"], "input_diagnostics": list(diagnostics),
        "source_expected_signal_ids": protocol["expected_signal_ids"],
        "source_implementation_is_historical_not_current": True}


def run_study(config_path, output_dir):
    """Run one configured genome through three engines; retain every archive ID.

    Invalid configurations/integrity abort. Missing per-signal data or engine
    capabilities produce explicit blocked rows. No blocked subset is aggregated
    as a complete portfolio. Identical reruns are byte-idempotent.
    """
    started = time.monotonic()
    config_path, output = Path(config_path).resolve(), Path(output_dir).resolve()
    config_sha = _digest(config_path)
    config = load_config(config_path)
    watched = {}
    _watch(config_path, config_sha, watched)
    identity = current_identity()
    _check_current(identity)
    _archive_check(output)
    if (output / "manifest.json").exists():
        verify_study(output)
    rows, signals, input_info = (_export_input if config["input_kind"] == "telegram_export" else _control_input)(config, watched)
    pending = [row for row in rows if row["status"] == "pending"]
    frames, quotes = _load_tapes(config, watched)
    _deadline(started, config)
    if any(path == str(output) or output in Path(path).parents for path in watched):
        raise ValueError("output cannot contain a read-only source")
    protocol = {"schema_version": SCHEMA, "config": config, "config_path": str(config_path),
                "config_sha256": config_sha, "implementation": identity, "sources": watched,
                "input_info": input_info,
                "fx_validation": fx_validation_contract(config["max_fx_age_ms"], config["max_fx_interval_ms"]),
                "candidate_search_count": 0, "limitations": LIMITATIONS}
    protocol["identity_sha256"] = _sha(protocol)
    _verify_sources(watched)
    _check_current(identity)
    _publish(output, {"protocol.json": protocol})
    genome = StrategyGenome.from_dict(config["strategy"])
    execution = execution_from_mapping(config["execution"])
    scenario = execution_to_scenario(execution)
    fast = FastEvaluator(execution=execution)
    market, conversion = _arrays(frames["market"]), _arrays(frames["conversion"])
    paths, scalar_results, evaluations = [], [], 0
    over_budget = len(pending) > config["budget"]["max_signals"] or 3 * len(pending) > config["budget"]["max_evaluations"]
    path_quotes = sum(int(np.searchsorted(market[0], time_ns(signals[row["signal_id"]].observed_at
                        + timedelta(seconds=config["horizon_seconds"])), side="right")
                        - np.searchsorted(market[0], time_ns(signals[row["signal_id"]].observed_at), side="left"))
                      for row in pending)
    over_budget = over_budget or path_quotes > config["budget"]["max_quotes"]
    for row in pending:
        _deadline(started, config)
        if over_budget:
            row.update(status="budget_blocked", reasons=["declared_cohort_exceeds_budget_no_truncation"])
            continue
        signal = signals[row["signal_id"]]
        cutoff = signal.observed_at + timedelta(seconds=config["horizon_seconds"])
        row["cutoff_utc"] = str(cutoff)
        reasons = _coverage(signal, cutoff, market, conversion, config)
        if reasons:
            row.update(status="data_blocked", reasons=reasons)
            continue
        try:
            path = make_path(signal, genome, market=market, conversion=conversion, cutoff=cutoff,
                contract_size=config["money"]["contract_size"], currency_digits=config["money"]["currency_digits"],
                max_fx_age_ms=config["max_fx_age_ms"], market_sha256=_sha(config["sources"]["market"]),
                max_fx_interval_ms=config["max_fx_interval_ms"],
                conversion_sha256=_sha(config["sources"]["conversion"]))
        except ValueError as exc:
            row.update(status="data_blocked", reasons=[str(exc)])
            continue
        scalar = None
        for name, engine, profile in (("scalar", simulate, execution), ("fast", fast, execution),
                                       ("oracle", oracle_simulate, scenario)):
            _deadline(started, config)
            result = engine(path, genome) if name == "fast" else engine(path, genome, execution=profile)
            evaluations += 1
            _deadline(started, config)
            if name == "scalar":
                scalar = result
            row["engines"][name] = json.loads(_encode(asdict(result)))
        reference = row["engines"]["oracle"]
        row["mismatches"] = {name: sorted(key for key in set(value) | set(reference)
            if key != "behavior_digest" and (key not in value or key not in reference or value[key] != reference[key]))
            for name, value in row["engines"].items() if name != "oracle"}
        blockers = sorted({reason for value in row["engines"].values() for reason in value["blockers"]})
        row.update(status="engine_disagreement" if any(row["mismatches"].values()) else
                   "engine_blocked" if blockers else "unfilled" if scalar.unfilled else "simulated",
                   reasons=blockers)
        paths.append(path)
        scalar_results.append(scalar)
    portfolio = {"canonical_tape": False, "assessment": None, "blockers": []}
    if pending and all(row["status"] in {"simulated", "unfilled"} for row in pending):
        _deadline(started, config)
        tape = build_portfolio_tape(paths,
            market_tick_source=_CanonicalSource(frames["market"], config["sources"]["market"]),
            conversion_tick_source=_CanonicalSource(frames["conversion"], config["sources"]["conversion"]),
            max_conversion_age_ms=config["max_fx_age_ms"], max_conversion_interval_ms=config["max_fx_interval_ms"])
        assessment = reconstruct_portfolio(paths, scalar_results, execution=execution, portfolio_tape=tape)
        _deadline(started, config)
        portfolio.update(canonical_tape=True, assessment=asdict(assessment), blockers=list(assessment.blockers))
    else:
        portfolio["blockers"] = ["no_complete_evaluated_trigger_cohort"]
    report = {"status": "hypothetical_only_diagnostic", "protocol_sha256": _sha(protocol),
        "rows": rows, "portfolio": portfolio, "engine_evaluations": evaluations, "quotes_loaded": quotes,
        "planned_path_quotes": path_quotes,
        "denominator": {"all_archive_identities": len(rows), "selected_trigger_identities": len(pending),
            "status_counts": dict(sorted(Counter(row["status"] for row in rows).items()))},
        "input_kind": config["input_kind"], "source_admission_blockers": input_info["source_admission_blockers"],
        "input_diagnostics": input_info["input_diagnostics"],
        "scenario": config["scenario"], "cohort": config["cohort"], "period": config["period"],
        "money_contract": config["money"], "money_contract_verified": False,
        "fx_validation": fx_validation_contract(config["max_fx_age_ms"], config["max_fx_interval_ms"]),
        "account_currency_money_verified": False, "full_live_parity_verified": False,
        "automatic_admission": False, "search_candidates": 0,
        "selection": {"selected_policy": None, "promotion_eligible": False}, "limitations": LIMITATIONS}
    report = json.loads(_encode(report))
    manifest = {"schema_version": SCHEMA, "protocol_identity_sha256": protocol["identity_sha256"],
                "artifacts": {"protocol.json": _sha(protocol), "results.json": _sha(report)}}
    _verify_sources(watched)
    _check_current(identity)
    _deadline(started, config)
    _publish(output, {"protocol.json": protocol, "results.json": report, "manifest.json": manifest})
    return report


def verify_study(output_dir):
    """Verify complete retained bytes AND current code/runtime/source identities."""
    output = Path(output_dir).resolve()
    _archive_check(output)
    if not output.is_dir() or {p.name for p in output.iterdir()} != ARTIFACTS:
        raise ValueError("incomplete archive artifacts")
    manifest = _read(output / "manifest.json")
    if manifest.get("schema_version") != SCHEMA or set(manifest.get("artifacts", {})) != {"protocol.json", "results.json"}:
        raise ValueError("invalid archive manifest")
    for name, expected in manifest["artifacts"].items():
        if _digest(output / name) != expected:
            raise ValueError(f"artifact hash mismatch: {name}")
    protocol, report = _read(output / "protocol.json"), _read(output / "results.json")
    identity = protocol["identity_sha256"]
    if (identity != _sha({key: value for key, value in protocol.items() if key != "identity_sha256"})
            or identity != manifest["protocol_identity_sha256"] or report["protocol_sha256"] != _sha(protocol)):
        raise ValueError("mixed protocol/results identity")
    _check_current(protocol["implementation"])
    _verify_sources(protocol["sources"])
    return {"status": "verified_current_hypothetical_archive", "identity_sha256": identity}
