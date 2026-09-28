import json

from research.runtime_simulation_inventory import build_inventory


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_inventory_keeps_prospective_and_recovered_shadow_evidence_separate(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [
        {
            "ev": "live_strategy_contract",
            "sig": "bot",
            "ts": "2026-09-15T09:00:00Z",
            "session_id": "session-a",
            "code_commit": "commit-a",
            "payload_sha256": "contract-a",
        },
        {
            "ev": "signal_received",
            "sig": "canal1_101",
            "channel": "canal1",
            "direction": "BUY",
            "ts": "2026-09-15T10:00:02Z",
            "tg_ts": "2026-09-15T10:00:00Z",
            "session_id": "session-a",
            "raw_text": "never copy message bodies into the inventory",
        },
        {
            "ev": "bot_internal_decision",
            "sig": "canal1_101",
            "ts": "2026-09-15T10:00:02.500Z",
            "session_id": "session-a",
        },
        {
            "ev": "mt5_order_requested",
            "sig": "canal1_101",
            "ts": "2026-09-15T10:00:02.800Z",
            "session_id": "session-a",
        },
        {
            "ev": "mt5_action_attempt",
            "sig": "canal1_101",
            "operation": "OPEN_MARKET",
            "ts": "2026-09-15T10:00:03Z",
            "session_id": "session-a",
            "broker_request_sent": True,
            "broker_request_started_utc": "2026-09-15T10:00:02.900Z",
            "broker_response_received_utc": "2026-09-15T10:00:03Z",
            "timing_schema_version": 1,
            "attempt_started_monotonic_ns": 0,
            "broker_request_started_monotonic_ns": 1_000_000,
            "broker_response_received_monotonic_ns": 4_000_000,
            "attempt_finished_monotonic_ns": 5_000_000,
            "pre_broker_duration_ns": 1_000_000,
            "broker_roundtrip_ns": 3_000_000,
            "post_broker_duration_ns": 1_000_000,
            "attempt_timing_complete": True,
            "duration_ns": 5_000_000,
            "retcode": 10009,
        },
        {
            "ev": "mt5_order_result",
            "sig": "canal1_101",
            "ts": "2026-09-15T10:00:03.100Z",
            "session_id": "session-a",
        },
        {
            "ev": "market_filled",
            "sig": "canal1_101",
            "ts": "2026-09-15T10:00:03.200Z",
            "session_id": "session-a",
        },
        {
            "ev": "mt5_modify_confirmed",
            "sig": "canal1_101",
            "ts": "2026-09-15T10:00:03.300Z",
            "session_id": "session-a",
        },
        {
            "ev": "strategy_shadow_registered",
            "sig": "canal1_101",
            "candidate_id": "candidate-a",
            "ts": "2026-09-15T10:00:04Z",
            "session_id": "session-a",
        },
        {
            "ev": "signal_closed",
            "sig": "canal1_101",
            "ts": "2026-09-15T11:00:00Z",
            "session_id": "session-a",
        },
        {
            "ev": "strategy_shadow_recovered",
            "sig": "canal2_202",
            "candidate_id": "candidate-b",
            "ts": "2026-09-16T12:00:00Z",
            "session_id": "session-b",
        },
        {
            "ev": "telegram_raw",
            "sig": "canal1_999",
            "channel": "canal1",
            "ts": "2026-09-16T13:00:00Z",
            "raw_text": "management context is not a directional signal",
        },
    ]
    events.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n{broken json\n",
        encoding="utf-8",
    )

    weekly_shadow = tmp_path / "weekly-shadow.json"
    _write_json(
        weekly_shadow,
        {
            "rows": [
                {
                    "signal_id": "canal1_101",
                    "channel": "canal1",
                    "candidate_id": "candidate-a",
                    "day": "2026-09-15",
                    "status": "closed",
                    "usable": True,
                    "registration_observed": True,
                    "net_eur": 1.25,
                },
                {
                    "signal_id": "canal2_202",
                    "channel": "canal2",
                    "candidate_id": "candidate-b",
                    "day": "2026-09-16",
                    "status": "closed",
                    "usable": True,
                    "registration_observed": False,
                    "net_eur": 2.5,
                },
                {
                    "signal_id": "canal2_202",
                    "channel": "canal2",
                    "candidate_id": "candidate-c",
                    "day": "2026-09-16",
                    "status": "missing_original_week_state",
                    "usable": False,
                    "registration_observed": False,
                    "net_eur": None,
                },
            ]
        },
    )

    native = tmp_path / "native.json"
    _write_json(
        native,
        {
            "calendar": "native broker calendar; no UTC conversion claimed",
            "baskets": [
                {
                    "signal_id": "canal2_202",
                    "channel": "canal2",
                    "positions": 2,
                    "net_eur": "-3.50",
                    "first_native_calendar": "2026-09-16T14:00:00",
                    "last_native_calendar": "2026-09-16T15:00:00",
                }
            ],
        },
    )

    parity = tmp_path / "parity.json"
    _write_json(
        parity,
        {
            "historical_complete_matrix": {
                "details": [
                    {
                        "signal_id": "canal2_202",
                        "day": "2026-09-16",
                        "channel": "canal2",
                        "candidate_id": "candidate-b",
                        "entry_match": False,
                        "money_match_cent": False,
                        "money_delta_eur": "-1.00",
                    }
                ]
            },
            "current_week_partial": {
                "details": [
                    {
                        "signal_id": "canal1_101",
                        "day": "2026-09-15",
                        "channel": "canal1",
                        "entry_match": True,
                        "money_match_cent": False,
                        "money_delta_eur": "0.25",
                    }
                ]
            }
        },
    )

    market = tmp_path / "market.json"
    _write_json(
        market,
        {
            "expected_symbol": "XAUUSD",
            "required_days": ["2026-09-15", "2026-09-16"],
            "coverage_by_day": {
                "2026-09-15": {"status": "complete"},
                "2026-09-16": {"status": "incomplete"},
            },
        },
    )
    money = tmp_path / "money.json"
    _write_json(
        money,
        {
            "symbol": "EURUSD",
            "required_days": ["2026-09-15", "2026-09-16"],
            "cached_days": ["2026-09-15"],
            "incomplete_days": ["2026-09-16"],
        },
    )

    result = build_inventory(
        event_paths=[events],
        weekly_shadow_path=weekly_shadow,
        weekly_parity_path=parity,
        native_path=native,
        market_status_path=market,
        money_status_path=money,
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-17T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
    )

    assert result["schema_version"] == 6
    assert result["source_quality"]["malformed_event_lines"] == 1
    assert len(result["signals"]) == 2
    first, recovered = result["signals"]
    assert first["signal_id"] == "canal1_101"
    assert first["day_utc"] == "2026-09-15"
    assert first["receipt"]["telegram_utc"] == "2026-09-15T10:00:00+00:00"
    assert first["strategy_contract"]["payload_sha256"] == "contract-a"
    assert first["execution"]["attempts"] == 1
    assert first["execution"]["timed_attempts"] == 1
    assert first["execution"]["max_duration_ms"] == 5.0
    assert first["timeline_first_utc"] == {
        "receipt": "2026-09-15T10:00:02+00:00",
        "decision": "2026-09-15T10:00:02.500000+00:00",
        "queue": "2026-09-15T10:00:02.800000+00:00",
        "send": "2026-09-15T10:00:02.900000+00:00",
        "broker_response": "2026-09-15T10:00:03+00:00",
        "ack": "2026-09-15T10:00:03.100000+00:00",
        "fill": "2026-09-15T10:00:03.200000+00:00",
        "protection": "2026-09-15T10:00:03.300000+00:00",
    }
    assert first["shadow"]["prospective_candidates"] == ["candidate-a"]
    assert first["shadow"]["recovered_candidates"] == []
    assert first["parity"]["money_match_cent"] is False
    assert first["coverage"] == {"market_ticks": "complete", "money_fx": "complete"}

    assert recovered["signal_id"] == "canal2_202"
    assert recovered["receipt"]["observed"] is False
    assert recovered["shadow"]["prospective_candidates"] == []
    assert recovered["shadow"]["recovered_candidates"] == ["candidate-b"]
    assert recovered["shadow"]["unverified_candidates"] == ["candidate-b", "candidate-c"]
    assert recovered["native"]["positions"] == 2
    assert recovered["native"]["net_eur"] == "-3.50"
    assert recovered["native"]["calendar_claim"] == "native broker calendar; no UTC conversion claimed"
    assert recovered["parity"]["cohort"] == "historical_complete_matrix"
    assert recovered["parity"]["entry_match"] is False
    assert recovered["coverage"] == {"market_ticks": "incomplete", "money_fx": "incomplete"}

    assert result["daily"] == [
        {
            "day_utc": "2026-09-15",
            "channel": "canal1",
            "signals": 1,
            "receipts_observed": 1,
            "native_baskets": 0,
            "prospective_shadow_signals": 1,
            "recovered_only_shadow_signals": 0,
            "market_ticks": "complete",
            "money_fx": "complete",
        },
        {
            "day_utc": "2026-09-16",
            "channel": "canal2",
            "signals": 1,
            "receipts_observed": 0,
            "native_baskets": 1,
            "prospective_shadow_signals": 0,
            "recovered_only_shadow_signals": 1,
            "market_ticks": "incomplete",
            "money_fx": "incomplete",
        },
    ]
    serialized = json.dumps(result, sort_keys=True)
    assert "never copy message bodies" not in serialized
    assert "raw_text" not in serialized


def test_inventory_scopes_strategy_contracts_to_the_same_runtime_session(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [
        {
            "ev": "live_strategy_contract",
            "sig": "bot",
            "ts": "2026-09-15T09:00:00Z",
            "session_id": "session-a",
            "payload_sha256": "contract-a",
        },
        {
            "ev": "signal_received",
            "sig": "canal1_201",
            "channel": "canal1",
            "ts": "2026-09-15T10:00:00Z",
            "session_id": "session-b",
        },
        {
            "ev": "live_strategy_contract",
            "sig": "bot",
            "ts": "2026-09-15T10:01:00Z",
            "session_id": "session-b",
            "payload_sha256": "contract-b",
        },
        {
            "ev": "signal_received",
            "sig": "canal1_202",
            "channel": "canal1",
            "ts": "2026-09-15T10:02:00Z",
            "session_id": "session-b",
        },
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = build_inventory(
        event_paths=[events],
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-16T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
    )

    by_signal = {row["signal_id"]: row for row in result["signals"]}
    assert by_signal["canal1_201"]["strategy_contract"] is None
    assert by_signal["canal1_202"]["strategy_contract"]["payload_sha256"] == "contract-b"
    assert by_signal["canal1_202"]["strategy_contract"]["session_id"] == "session-b"


def test_inventory_uses_validated_broker_boundaries_and_not_event_write_time(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [
        {
            "ev": "signal_received",
            "sig": "canal1_301",
            "ts": "2026-09-15T10:00:00Z",
            "session_id": "session-a",
        },
        {
            "ev": "mt5_action_attempt",
            "sig": "canal1_301",
            "ts": "2026-09-15T10:01:00Z",
            "session_id": "session-a",
            "broker_request_sent": True,
            "broker_request_started_utc": "2026-09-15T10:00:01Z",
            "broker_response_received_utc": "2026-09-15T10:00:02Z",
            "duration_ns": 1_000_000_000,
        },
        {
            "ev": "signal_received",
            "sig": "canal1_302",
            "ts": "2026-09-15T11:00:00Z",
            "session_id": "session-a",
        },
        {
            "ev": "mt5_action_attempt",
            "sig": "canal1_302",
            "ts": "2026-09-15T11:01:00Z",
            "session_id": "session-a",
            "broker_request_sent": False,
            "broker_request_started_utc": "2026-09-15T11:00:01Z",
        },
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = build_inventory(
        event_paths=[events],
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-16T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
    )

    by_signal = {row["signal_id"]: row for row in result["signals"]}
    assert by_signal["canal1_301"]["timeline_first_utc"]["send"] == "2026-09-15T10:00:01+00:00"
    assert by_signal["canal1_301"]["timeline_first_utc"]["broker_response"] == "2026-09-15T10:00:02+00:00"
    assert by_signal["canal1_301"]["execution"]["sent_attempts"] == 1
    assert "send" not in by_signal["canal1_302"]["timeline_first_utc"]
    assert by_signal["canal1_302"]["execution"]["known_not_sent_attempts"] == 1


def test_inventory_does_not_invent_utc_scope_from_native_calendar(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text("", encoding="utf-8")
    native = tmp_path / "native.json"
    _write_json(
        native,
        {
            "calendar": "native broker calendar; no UTC conversion claimed",
            "baskets": [
                {
                    "signal_id": "canal2_401",
                    "channel": "canal2",
                    "first_native_calendar": "2026-09-18T10:00:00",
                    "last_native_calendar": "2026-09-18T11:00:00",
                }
            ],
        },
    )

    result = build_inventory(
        event_paths=[events],
        native_path=native,
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-17T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
    )

    assert result["signals"] == []
    assert result["auxiliary_quality"]["unplaced"] == [
        {
            "source": "native",
            "signal_id": "canal2_401",
            "reason": "native_calendar_not_utc",
        }
    ]


def test_inventory_recovers_in_scope_events_that_precede_the_root_event(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [
        {
            "ev": "bot_internal_decision",
            "sig": "canal1_501",
            "ts": "2026-09-15T09:59:59Z",
            "session_id": "session-a",
        },
        {
            "ev": "signal_received",
            "sig": "canal1_501",
            "ts": "2026-09-15T10:00:00Z",
            "session_id": "session-a",
        },
        {
            "ev": "sl_updated",
            "sig": "canal1_501",
            "ts": "2026-09-15T10:00:01Z",
            "session_id": "session-a",
        },
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = build_inventory(
        event_paths=[events],
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-16T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
    )

    row = result["signals"][0]
    assert row["timeline_first_utc"]["decision"] == "2026-09-15T09:59:59+00:00"
    assert "protection" not in row["timeline_first_utc"]
    assert row["event_counts"]["bot_internal_decision"] == 1


def test_inventory_preserves_contracts_and_contract_gaps_for_every_observed_session(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [
        {
            "ev": "live_strategy_contract",
            "sig": "bot",
            "ts": "2026-09-15T09:00:00Z",
            "session_id": "session-a",
            "payload_sha256": "contract-a",
        },
        {
            "ev": "signal_received",
            "sig": "canal1_601",
            "ts": "2026-09-15T10:00:00Z",
            "session_id": "session-a",
        },
        {
            "ev": "live_strategy_contract",
            "sig": "bot",
            "ts": "2026-09-15T11:00:00Z",
            "session_id": "session-b",
            "payload_sha256": "contract-b",
        },
        {
            "ev": "mt5_action_attempt",
            "sig": "canal1_601",
            "ts": "2026-09-15T12:00:00Z",
            "session_id": "session-b",
            "attempt_id": "attempt-b",
            "broker_request_sent": True,
        },
        {
            "ev": "mt5_modify_confirmed",
            "sig": "canal1_601",
            "ts": "2026-09-15T13:00:00Z",
            "session_id": "session-c",
        },
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = build_inventory(
        event_paths=[events],
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-16T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
    )

    row = result["signals"][0]
    contracts = {(item["session_id"], item["payload_sha256"]) for item in row["strategy_contracts"]}
    assert contracts == {("session-a", "contract-a"), ("session-b", "contract-b")}
    assert row["contract_gaps"] == [
        {
            "session_id": "session-c",
            "first_event_utc": "2026-09-15T13:00:00+00:00",
            "event_count": 1,
        }
    ]


def test_inventory_receipt_day_wins_and_auxiliary_conflict_is_visible(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "ev": "signal_received",
                "sig": "canal1_701",
                "ts": "2026-09-16T10:00:00Z",
                "session_id": "session-a",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    weekly = tmp_path / "weekly.json"
    _write_json(
        weekly,
        {"rows": [{"signal_id": "canal1_701", "day": "2026-09-15", "candidate_id": "candidate"}]},
    )
    market = tmp_path / "market.json"
    _write_json(
        market,
        {"coverage_by_day": {"2026-09-15": "complete", "2026-09-16": "missing"}},
    )

    result = build_inventory(
        event_paths=[events],
        weekly_shadow_path=weekly,
        market_status_path=market,
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-17T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
    )

    row = result["signals"][0]
    assert row["day_utc"] == "2026-09-16"
    assert row["coverage"]["market_ticks"] == "missing"
    assert row["day_sources"]["weekly_shadow_utc_days"] == ["2026-09-15"]
    assert "auxiliary_day_conflicts_with_event_day" in row["gaps"]


def test_inventory_native_only_root_keeps_earlier_event_evidence(tmp_path):
    events = tmp_path / "events.jsonl"
    rows = [
        {
            "ev": "bot_internal_decision",
            "sig": "canal1_801",
            "ts": "2026-09-15T10:00:00Z",
            "session_id": "session-a",
        },
        {
            "ev": "mt5_modify_confirmed",
            "sig": "canal1_801",
            "ts": "2026-09-15T10:01:00Z",
            "session_id": "session-a",
        },
    ]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    native = tmp_path / "native.json"
    _write_json(native, {"baskets": [{"signal_id": "canal1_801", "day_utc": "2026-09-15"}]})

    result = build_inventory(
        event_paths=[events],
        native_path=native,
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-16T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
    )

    row = result["signals"][0]
    assert row["event_counts"] == {"bot_internal_decision": 1, "mt5_modify_confirmed": 1}
    assert row["sessions"] == ["session-a"]
    assert row["timeline_first_utc"]["decision"] == "2026-09-15T10:00:00+00:00"
    assert row["timeline_first_utc"]["protection"] == "2026-09-15T10:01:00+00:00"


def test_inventory_deduplicates_event_ids_and_excludes_conflicting_ids(tmp_path):
    events = tmp_path / "events.jsonl"
    duplicate = {
        "ev": "mt5_action_attempt",
        "sig": "canal1_901",
        "ts": "2026-09-15T10:00:00Z",
        "session_id": "session-a",
        "event_id": "duplicate-id",
        "broker_request_sent": True,
    }
    conflict_a = {
        **duplicate,
        "event_id": "conflict-id",
        "attempt_id": "attempt-a",
    }
    conflict_b = {**conflict_a, "attempt_id": "attempt-b"}
    rows = [duplicate, duplicate, conflict_a, conflict_b]
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    result = build_inventory(
        event_paths=[events],
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-16T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
    )

    row = result["signals"][0]
    assert result["source_quality"]["duplicate_event_ids"] == 1
    assert result["source_quality"]["conflicting_event_ids"] == 1
    assert result["source_quality"]["excluded_conflicting_event_ids"] == 1
    assert row["execution"]["attempts"] == 1
    assert row["execution"]["sent_attempts"] == 1


def test_inventory_rejects_negative_and_unvalidated_timing(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "ev": "mt5_action_attempt",
                "sig": "canal1_1001",
                "ts": "2026-09-15T10:00:00Z",
                "session_id": "session-a",
                "attempt_timing_complete": True,
                "duration_ns": -1_000_000,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_inventory(
        event_paths=[events],
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-16T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
    )

    execution = result["signals"][0]["execution"]
    assert execution["timed_attempts"] == 0
    assert execution["invalid_timing_attempts"] == 1
    assert execution["duration_observed_attempts"] == 0
    assert execution["max_duration_ms"] is None


def _inventory_for_rows(tmp_path, rows, **sources):
    tmp_path.mkdir(parents=True, exist_ok=True)
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    paths = {}
    for name, value in sources.items():
        path = tmp_path / f"{name}.json"
        _write_json(path, value)
        paths[f"{name}_path"] = path
    return build_inventory(
        event_paths=[events],
        since="2026-09-15T00:00:00Z",
        until_exclusive="2026-09-17T00:00:00Z",
        generated_at="2026-09-19T17:00:00Z",
        **paths,
    )


def _contract(*, payload="contract-a", event_id="contract-a", hour="09"):
    return {
        "ev": "live_strategy_contract",
        "sig": "bot",
        "ts": f"2026-09-15T{hour}:00:00Z",
        "session_id": "session-a",
        "payload_sha256": payload,
        "event_id": event_id,
    }


def _receipt(*, hour="10", session="session-a", event_id="receipt", direction="BUY"):
    return {
        "ev": "signal_received",
        "sig": "canal1_1101",
        "ts": f"2026-09-15T{hour}:00:00Z",
        "session_id": session,
        "event_id": event_id,
        "direction": direction,
    }


def test_inventory_quarantines_conflicting_signal_identity_without_losing_denominator(tmp_path):
    result = _inventory_for_rows(
        tmp_path,
        [_receipt(direction="BUY"), _receipt(direction="SELL")],
    )

    assert len(result["signals"]) == 1
    row = result["signals"][0]
    assert row["signal_id"] == "canal1_1101"
    assert row["quarantine"][0]["reason"] == "conflicting_event_id"
    assert row["quarantine"][0]["event_id"] == "receipt"
    assert row["quarantine"][0]["occurrences"] == 2
    assert len(row["quarantine"][0]["source_occurrences"]) == 2
    assert "conflicting_event_identity_quarantined" in row["gaps"]
    assert result["daily"][0]["signals"] == 1
    conflict = result["source_quality"]["conflicting_event_details"][0]
    assert conflict["event_id"] == "receipt"
    assert len(conflict["occurrences"]) == 2


def test_inventory_does_not_promote_out_of_scope_conflicts_into_signal_universe(tmp_path):
    first = _receipt(direction="BUY")
    second = _receipt(direction="SELL")
    first["ts"] = "2026-09-14T10:00:00Z"
    second["ts"] = "2026-09-14T10:00:00Z"

    result = _inventory_for_rows(tmp_path, [first, second])

    assert result["signals"] == []
    assert result["daily"] == []
    assert result["source_quality"]["conflicting_event_ids"] == 1


def test_inventory_contract_conflict_invalidates_prior_contract_until_new_evidence(tmp_path):
    later = _contract(payload="contract-b", event_id="later", hour="11")
    result = _inventory_for_rows(
        tmp_path,
        [
            _contract(),
            later,
            {**later, "payload_sha256": "contract-c"},
            _receipt(hour="12"),
        ],
    )

    row = result["signals"][0]
    assert row["strategy_contract"] is None
    assert row["contract_gaps"][0]["session_id"] == "session-a"
    assert "strategy_contract_conflict_prevents_attribution" in row["gaps"]


def test_inventory_same_time_contracts_are_ambiguous_independent_of_file_order(tmp_path):
    first = _contract(payload="contract-a", event_id="contract-a")
    second = _contract(payload="contract-b", event_id="contract-b")
    outcomes = []
    for index, contracts in enumerate(((first, second), (second, first))):
        case = tmp_path / str(index)
        case.mkdir()
        result = _inventory_for_rows(case, [*contracts, _receipt()])
        row = result["signals"][0]
        assert result["source_quality"]["contract_ambiguities"] == [
            {
                "session_id": "session-a",
                "event_utc": "2026-09-15T09:00:00+00:00",
                "contracts": 2,
            }
        ]
        outcomes.append((row["strategy_contract"], row["contract_gaps"], row["gaps"]))

    assert all(contract is None for contract, _gaps, _row_gaps in outcomes)
    assert all(gaps[0]["session_id"] == "session-a" for _contract, gaps, _row_gaps in outcomes)
    assert all(
        "strategy_contract_conflict_prevents_attribution" in row_gaps
        for _contract, _gaps, row_gaps in outcomes
    )


def test_inventory_missing_or_empty_session_is_an_explicit_contract_gap(tmp_path):
    attempt = {
        "ev": "mt5_action_attempt",
        "sig": "canal1_1101",
        "ts": "2026-09-15T11:00:00Z",
        "broker_request_sent": False,
    }
    row = _inventory_for_rows(tmp_path / "missing", [_contract(), _receipt(), attempt])["signals"][0]
    empty = _inventory_for_rows(
        tmp_path / "empty",
        [_receipt(session="", event_id="empty-session")],
    )["signals"][0]

    assert {gap["session_id"] for gap in row["contract_gaps"]} == {None}
    assert "event_session_id_not_observed" in row["gaps"]
    assert empty["receipt"]["observed"] is True
    assert empty["contract_gaps"][0]["session_id"] is None


def test_inventory_rejects_negative_total_even_when_stage_timing_is_coherent(tmp_path):
    attempt = {
        "ev": "mt5_action_attempt",
        "sig": "canal1_1101",
        "ts": "2026-09-15T11:00:00Z",
        "session_id": "session-a",
        "broker_request_sent": True,
        "timing_schema_version": 1,
        "attempt_timing_complete": True,
        "duration_ns": -1_000_000,
        "attempt_started_monotonic_ns": 0,
        "broker_request_started_monotonic_ns": 1_000_000,
        "broker_response_received_monotonic_ns": 4_000_000,
        "attempt_finished_monotonic_ns": 5_000_000,
        "pre_broker_duration_ns": 1_000_000,
        "broker_roundtrip_ns": 3_000_000,
        "post_broker_duration_ns": 1_000_000,
    }
    execution = _inventory_for_rows(tmp_path, [_contract(), _receipt(), attempt])["signals"][0][
        "execution"
    ]

    assert execution["timed_attempts"] == 0
    assert execution["invalid_timing_attempts"] == 1
    assert execution["duration_observed_attempts"] == 0


def test_inventory_earliest_receipt_clears_contract_from_later_receipt(tmp_path):
    row = _inventory_for_rows(
        tmp_path,
        [_contract(), _receipt(hour="10"), _receipt(hour="08", session="session-b", event_id="early")],
    )["signals"][0]

    assert row["receipt"]["event_utc"] == "2026-09-15T08:00:00+00:00"
    assert row["strategy_contract"] is None
    assert any(gap["session_id"] == "session-b" for gap in row["contract_gaps"])


def test_inventory_preserves_multiple_parity_and_native_rows_without_selecting_last(tmp_path):
    parity = {
        "historical_complete_matrix": {
            "details": [
                {"signal_id": "canal1_1101", "day": "2026-09-15", "candidate_id": "a", "joint_match": False},
                {"signal_id": "canal1_1101", "day": "2026-09-15", "candidate_id": "b", "joint_match": True},
            ]
        }
    }
    native = {
        "baskets": [
            {"signal_id": "canal1_1101", "day_utc": "2026-09-15", "net_eur": "-10.00"},
            {"signal_id": "canal1_1101", "day_utc": "2026-09-15", "net_eur": "20.00"},
        ]
    }
    row = _inventory_for_rows(
        tmp_path,
        [_contract(), _receipt()],
        weekly_parity=parity,
        native=native,
    )["signals"][0]

    assert row["parity"] is None
    assert [item["candidate_id"] for item in row["parity_rows"]] == ["a", "b"]
    assert row["native"] is None
    assert [item["net_eur"] for item in row["native_rows"]] == ["-10.00", "20.00"]
    assert "multiple_parity_rows_observed" in row["gaps"]
    assert "multiple_native_baskets_observed" in row["gaps"]


def test_contract_at_conflict_timestamp_does_not_bypass_invalidation(tmp_path):
    conflicted = _contract(payload="contract-b", event_id="conflict", hour="11")
    row = _inventory_for_rows(
        tmp_path,
        [
            conflicted,
            {**conflicted, "payload_sha256": "contract-c"},
            _contract(payload="contract-d", event_id="independent", hour="11"),
            _receipt(hour="12"),
        ],
    )["signals"][0]

    assert row["strategy_contract"] is None
    assert "strategy_contract_conflict_prevents_attribution" in row["gaps"]


def test_conflicting_protection_is_quarantined_on_existing_signal(tmp_path):
    protection = {
        **_receipt(hour="12", event_id="protection"),
        "ev": "protection_confirmed",
        "sl": 2500,
    }
    row = _inventory_for_rows(
        tmp_path,
        [_contract(), _receipt(), protection, {**protection, "sl": 2400}],
    )["signals"][0]

    assert row["quarantine"][0]["event_id"] == "protection"
    assert "conflicting_event_identity_quarantined" in row["gaps"]


def test_tied_conflicting_receipts_are_ambiguous_independent_of_file_order(tmp_path):
    first = _receipt(event_id="receipt-a", session="session-a", direction="BUY")
    second = _receipt(event_id="receipt-b", session="session-b", direction="SELL")
    contracts = [
        _contract(),
        {**_contract(payload="contract-b", event_id="contract-b"), "session_id": "session-b"},
    ]
    outcomes = []
    for index, ordering in enumerate(((first, second), (second, first))):
        row = _inventory_for_rows(tmp_path / str(index), [*contracts, *ordering])["signals"][0]
        outcomes.append((row["direction"], row["strategy_contract"], row["gaps"]))

    assert all(direction is None for direction, _contract, _gaps in outcomes)
    assert all(contract is None for _direction, contract, _gaps in outcomes)
    assert all("ambiguous_earliest_signal_receipt" in gaps for _direction, _contract, gaps in outcomes)


def test_native_and_parity_rows_retain_day_and_source_row_identity(tmp_path):
    parity = {
        "historical_complete_matrix": {
            "details": [
                {"signal_id": "canal1_1101", "day": "2026-09-15", "candidate_id": "a"},
                {"signal_id": "canal1_1101", "day": "2026-09-16", "candidate_id": "b"},
            ]
        }
    }
    native = {
        "baskets": [
            {"signal_id": "canal1_1101", "day_utc": "2026-09-15", "net_eur": "10"},
            {"signal_id": "canal1_1101", "day_utc": "2026-09-16", "net_eur": "-20"},
        ]
    }
    row = _inventory_for_rows(
        tmp_path,
        [_receipt()],
        weekly_parity=parity,
        native=native,
    )["signals"][0]

    assert [(item["day_utc"], item["source_row_index"]) for item in row["parity_rows"]] == [
        ("2026-09-15", 0),
        ("2026-09-16", 1),
    ]
    assert [(item["day_utc"], item["source_row_index"]) for item in row["native_rows"]] == [
        ("2026-09-15", 0),
        ("2026-09-16", 1),
    ]


def test_tied_receipt_channel_conflict_is_ambiguous_independent_of_order(tmp_path):
    first = {**_receipt(event_id="receipt-a"), "channel": "canal1"}
    second = {**_receipt(event_id="receipt-b"), "channel": "canal2"}
    outcomes = []
    for index, ordering in enumerate(((first, second), (second, first))):
        result = _inventory_for_rows(tmp_path / str(index), [_contract(), *ordering])
        row = result["signals"][0]
        outcomes.append((row["channel"], result["daily"][0]["channel"], row["gaps"]))

    assert all(channel is None for channel, _daily, _gaps in outcomes)
    assert all(daily is None for _channel, daily, _gaps in outcomes)
    assert all(
        "ambiguous_earliest_signal_receipt" in gaps
        for _channel, _daily, gaps in outcomes
    )


def test_native_unplaced_rows_do_not_depend_on_source_order(tmp_path):
    unplaced = {"signal_id": "canal1_1101", "net_eur": "-20"}
    dated = {
        "signal_id": "canal1_1101",
        "day_utc": "2026-09-15",
        "net_eur": "10",
    }
    outcomes = []
    for index, baskets in enumerate(((unplaced, dated), (dated, unplaced))):
        result = _inventory_for_rows(
            tmp_path / str(index),
            [],
            native={"baskets": list(baskets)},
        )
        row = result["signals"][0]
        outcomes.append((sorted(item["net_eur"] for item in row["native_rows"]), result))

    assert all(amounts == ["-20", "10"] for amounts, _result in outcomes)
    assert all(result["auxiliary_quality"]["unplaced"] == [] for _amounts, result in outcomes)


def test_weekly_shadow_rows_retain_day_and_source_row_identity(tmp_path):
    weekly = {
        "rows": [
            {"signal_id": "canal1_1101", "day": "2026-09-15", "net_eur": "10"},
            {"signal_id": "canal1_1101", "day": "2026-09-16", "net_eur": "-20"},
        ]
    }

    row = _inventory_for_rows(tmp_path, [], weekly_shadow=weekly)["signals"][0]

    assert [
        (item["day_utc"], item["source_row_index"], item["net_eur"])
        for item in row["shadow"]["diagnostic_rows"]
    ] == [
        ("2026-09-15", 0, "10"),
        ("2026-09-16", 1, "-20"),
    ]


def test_contract_metadata_rejects_nested_values_from_sanitized_inventory(tmp_path):
    marker = "SYNTHETIC_MESSAGE_BODY_NOT_A_SECRET"
    contract = {**_contract(), "code_commit": {"raw_text": marker}}

    result = _inventory_for_rows(tmp_path, [contract, _receipt()])
    row = result["signals"][0]

    assert row["strategy_contract"]["code_commit"] is None
    assert row["strategy_contract"]["invalid_metadata_fields"] == ["code_commit"]
    assert result["source_quality"]["invalid_contract_metadata_fields"] == 1
    assert marker not in json.dumps(result)
