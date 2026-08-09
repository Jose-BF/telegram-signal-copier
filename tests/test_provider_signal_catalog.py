import json
from pathlib import Path

import pytest

import provider_signal_catalog
import runtime_paths


def _raw(channel, message_id, text="", **overrides):
    row = {
        "ts": f"2026-07-08T10:00:{message_id % 60:02d}+00:00",
        "sig": f"{channel}_{message_id}",
        "ev": "telegram_raw",
        "channel": channel,
        "message_id": message_id,
        "reply_to_msg_id": None,
        "update_kind": "new",
        "date_utc": "2026-07-08T10:00:00+00:00",
        "edit_date_utc": None,
        "text": text,
        "sticker_id": None,
        "has_photo": False,
        "has_document": False,
        "is_edit": False,
        "is_reply": False,
    }
    row.update(overrides)
    return row


def test_canal2_progressive_edits_are_one_unexecuted_provider_signal():
    events = [
        _raw("canal2", 100, "Sell Gold Now"),
        _raw("canal2", 100, "Sell Gold Now", update_kind="poll_new"),
        _raw(
            "canal2",
            100,
            "Sell Gold Now\n\n4100 - 4105",
            update_kind="edit",
            is_edit=True,
            edit_date_utc="2026-07-08T10:00:05+00:00",
        ),
        _raw(
            "canal2",
            100,
            "Sell Gold Now\n\n4100 - 4105\n\nTargets\n4098\n4096\n4094"
            "\n\nSL/ invalid 4108",
            update_kind="edit",
            is_edit=True,
            edit_date_utc="2026-07-08T10:00:10+00:00",
        ),
        _raw(
            "canal2",
            101,
            "+30 pips from best entry\n\nClose overall profit or make risk free",
            reply_to_msg_id=100,
            is_reply=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])

    assert report["summary"]["provider_signals"] == 1
    assert report["summary"]["unexecuted_signals"] == 1
    signal = report["signals"][0]
    assert signal["provider_signal_id"] == "canal2_100"
    assert signal["direction"] == "SELL"
    assert signal["risk_label"] == "standard"
    assert signal["effective_range"] == [4100.0, 4105.0]
    assert signal["level_timeline"][-1]["observed_ts_utc"] is not None
    assert signal["effective_tps"] == [4098.0, 4096.0, 4094.0]
    assert signal["effective_sl"] == 4108.0
    assert len(signal["revisions"]) == 3
    assert len(signal["management_events"]) == 1
    assert signal["management_events"][0]["classified_action"] == "MANAGEMENT_CHOICE"
    assert signal["management_events"][0]["execution_options"] == [
        {"action": "CLOSE_ALL"},
        {"action": "MOVE_SL_TO_BE"},
    ]
    assert signal["execution_sig_ids"] == []
    assert signal["semantic_status"] == "complete"
    assert signal["entry_contract"] == {
        "status": "ready",
        "trigger_observed_utc": "2026-07-08T10:00:40+00:00",
        "trigger_telegram_utc": "2026-07-08T10:00:00+00:00",
        "trigger_message_id": 100,
        "trigger_kind": "text",
        "direction": "SELL",
        "direction_source": "revision_parser:100",
        "blockers": [],
    }


def test_canal1_sticker_and_text_are_grouped_with_duplicate_executions():
    events = [
        _raw(
            "canal1",
            200,
            sticker_id=12345,
            has_document=True,
            ts="2026-07-08T11:00:02.181+00:00",
            date_utc="2026-07-08T11:00:01+00:00",
        ),
        {
            "ts": "2026-07-08T11:00:02.300+00:00",
            "sig": "canal1_200",
            "ev": "telegram_understood",
            "channel": "canal1",
            "message_id": 200,
            "direction": "BUY",
            "tg_ts": "2026-07-08T11:00:00+00:00",
        },
        _raw(
            "canal1",
            201,
            "BUY GOLD NOW 4100-05\nTP1: 4108\nTP2: 4110\n"
            "TP3: 4112\nTP4: 4115\nSL: 4095",
            ts="2026-07-08T11:00:30+00:00",
            date_utc="2026-07-08T11:00:29+00:00",
        ),
        {
            "ts": "2026-07-08T11:00:30+00:00",
            "sig": "canal1_200",
            "ev": "canal1_text_processing",
            "source_msg_id": 201,
        },
    ]
    replay_trades = [
        {"sig_id": "canal1_200", "channel": "canal1"},
        {"sig_id": "canal1_201", "channel": "canal1"},
    ]

    report = provider_signal_catalog.build_catalog_report(events, replay_trades)

    assert report["summary"]["provider_signals"] == 1
    assert report["summary"]["duplicate_execution_signals"] == 1
    signal = report["signals"][0]
    assert signal["provider_signal_id"] == "canal1_200"
    assert signal["direction"] == "BUY"
    assert signal["source_message_ids"] == [200, 201]
    assert signal["execution_sig_ids"] == ["canal1_200", "canal1_201"]
    assert signal["effective_tps"] == [4108.0, 4110.0, 4112.0, 4115.0]
    assert signal["effective_sl"] == 4095.0
    assert signal["entry_contract"] == {
        "status": "ready",
        "trigger_observed_utc": "2026-07-08T11:00:02.300+00:00",
        "trigger_telegram_utc": "2026-07-08T11:00:00+00:00",
        "trigger_message_id": 200,
        "trigger_kind": "sticker",
        "direction": "BUY",
        "direction_source": "telegram_understood",
        "blockers": [],
    }
    assert not any(key.startswith("_") for key in signal)


def test_same_message_duplicate_delivery_keeps_observed_batches_but_one_canonical():
    events = [
        _raw(
            "canal2",
            266,
            "Sell Gold Now",
            ts="2026-07-22T09:13:30+00:00",
            date_utc="2026-07-22T09:13:28+00:00",
        ),
    ]
    for batch_index, second in enumerate((31, 35), start=1):
        base_ticket = 1000 * batch_index
        events.extend([
            {
                "ts": f"2026-07-22T09:13:{second}.000+00:00",
                "sig": "canal2_266",
                "ev": "signal_received",
                "channel": "canal2",
                "direction": "SELL",
            },
            {
                "ts": f"2026-07-22T09:13:{second}.100+00:00",
                "sig": "canal2_266",
                "ev": "market_filled",
                "ticket": base_ticket,
                "price": 4122.0,
            },
            *[
                {
                    "ts": (
                        f"2026-07-22T09:13:{second}."
                        f"{200 + leg:03d}+00:00"
                    ),
                    "sig": "canal2_266",
                    "ev": "scale_out_leg_filled",
                    "ticket": base_ticket + leg,
                    "price": 4122.0 - (leg * 0.01),
                    "leg": leg,
                }
                for leg in range(1, 5)
            ],
        ])

    report = provider_signal_catalog.build_catalog_report(
        events,
        [{
            "sig_id": "canal2_266",
            "channel": "canal2",
            "tickets": [
                {"ticket": ticket}
                for ticket in [
                    1000, 1001, 1002, 1003, 1004,
                    2000, 2001, 2002, 2003, 2004,
                ]
            ],
        }],
    )

    signal = report["signals"][0]
    assert signal["execution_sig_ids"] == ["canal2_266"]
    assert signal["execution_count"] == 2
    assert signal["duplicate_execution"] is True
    assert [batch["ticket_ids"] for batch in signal["execution_batches"]] == [
        [1000, 1001, 1002, 1003, 1004],
        [2000, 2001, 2002, 2003, 2004],
    ]
    assert signal["canonical_execution_count"] == 1
    assert signal["canonical_execution_batch_ids"] == [
        "canal2_266#exec1"
    ]
    assert signal["canonical_corrections"] == [{
        "type": "exclude_execution_batch",
        "reason": "duplicate_delivery_execution",
        "execution_batch_id": "canal2_266#exec2",
        "sig_id": "canal2_266",
        "ticket_ids": [2000, 2001, 2002, 2003, 2004],
        "preserved_in_observed_replay": True,
    }]


def test_single_execution_batch_is_canonical_without_correction():
    events = [
        _raw(
            "canal2", 380, "Buy Gold Now",
            ts="2026-07-23T15:30:26+00:00",
            date_utc="2026-07-23T15:30:25+00:00",
        ),
        {
            "ts": "2026-07-23T15:30:26.100+00:00",
            "sig": "canal2_380",
            "ev": "signal_received",
            "channel": "canal2",
            "direction": "BUY",
            "entry_source_kind": "zone_explicit_active",
            "zone_plan_message_id": 380,
            "zone_thread_root_message_id": 380,
            "zone_entry_generation": 1,
            "zone_trigger_kind": "explicit_active",
            "zone_trigger_side": "ask",
            "zone_trigger_price": 4056.3,
            "zone_trigger_range": [4051.3, 4056.3],
            "zone_trigger_time": 1785920400,
            "zone_trigger_time_msc": 1785920400123,
        },
        {
            "ts": "2026-07-23T15:30:26.200+00:00",
            "sig": "canal2_380",
            "ev": "market_filled",
            "ticket": 3000,
            "price": 4056.53,
        },
    ]

    signal = provider_signal_catalog.build_catalog_report(
        events,
        [{"sig_id": "canal2_380", "tickets": [{"ticket": 3000}]}],
    )["signals"][0]

    assert signal["execution_count"] == 1
    assert signal["canonical_execution_count"] == 1
    assert signal["canonical_execution_batch_ids"] == [
        "canal2_380#exec1"
    ]
    assert signal["canonical_corrections"] == []
    assert signal["duplicate_execution"] is False
    assert signal["execution_batches"][0]["entry_provenance"] == {
        "source_kind": "zone_explicit_active",
        "zone_plan_message_id": 380,
        "zone_thread_root_message_id": 380,
        "zone_entry_generation": 1,
        "zone_trigger_kind": "explicit_active",
        "zone_trigger_side": "ask",
        "zone_trigger_price": 4056.3,
        "zone_trigger_range": [4051.3, 4056.3],
        "zone_trigger_time": 1785920400,
        "zone_trigger_time_msc": 1785920400123,
    }


def test_reply_to_missing_root_is_preserved_as_management_only_record():
    events = [
        _raw(
            "canal2",
            301,
            "Target 3 hit",
            reply_to_msg_id=300,
            is_reply=True,
        )
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])

    assert report["summary"]["provider_signals"] == 0
    assert report["summary"]["records"] == 1
    signal = report["signals"][0]
    assert signal["provider_signal_id"] == "canal2_300"
    assert signal["record_type"] == "management_only"
    assert signal["semantic_status"] == "classified"
    assert signal["management_events"][0]["message_id"] == 301
    assert signal["signal_ts_utc"] == "2026-07-08T10:00:00+00:00"
    assert signal["first_observed_utc"] is not None
    assert signal["entry_contract"] == {
        "status": "blocked",
        "trigger_observed_utc": None,
        "trigger_telegram_utc": None,
        "trigger_message_id": None,
        "trigger_kind": None,
        "direction": None,
        "direction_source": None,
        "blockers": [
            "missing_direction",
            "provider_record_not_formal_signal",
        ],
    }


def test_revision_sequence_a_b_a_preserves_the_restoration_event():
    original = (
        "Sell Gold Now\n4100 - 4105\nTargets\n4098\n4096\nSL 4108"
    )
    events = [
        _raw("canal2", 350, original),
        _raw(
            "canal2",
            350,
            "Sell Gold Now\n4101 - 4106\nTargets\n4098\n4096\nSL 4108",
            update_kind="edit",
            is_edit=True,
            edit_date_utc="2026-07-08T10:01:00+00:00",
        ),
        _raw(
            "canal2",
            350,
            original,
            update_kind="edit",
            is_edit=True,
            edit_date_utc="2026-07-08T10:02:00+00:00",
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert len(signal["revisions"]) == 3
    assert [row["range"] for row in signal["entry_zone_timeline"]] == [
        [4100.0, 4105.0],
        [4101.0, 4106.0],
        [4100.0, 4105.0],
    ]
    assert signal["effective_range"] == [4100.0, 4105.0]


def test_poll_edit_sequence_a_b_a_preserves_same_edit_date():
    original = "Sell Gold Now\n4100 - 4105\nTargets\n4098\nSL 4108"
    changed = "Sell Gold Now\n4101 - 4106\nTargets\n4097\nSL 4109"
    shared_edit_date = "2026-07-08T10:10:00+00:00"
    events = [
        _raw(
            "canal2",
            351,
            original,
            ts="2026-07-08T10:10:01+00:00",
            date_utc="2026-07-08T10:00:00+00:00",
            edit_date_utc=shared_edit_date,
            update_kind="poll_edit",
        ),
        _raw(
            "canal2",
            351,
            changed,
            ts="2026-07-08T10:11:01+00:00",
            date_utc="2026-07-08T10:00:00+00:00",
            edit_date_utc=shared_edit_date,
            update_kind="poll_edit",
        ),
        _raw(
            "canal2",
            351,
            original,
            ts="2026-07-08T10:12:01+00:00",
            date_utc="2026-07-08T10:00:00+00:00",
            edit_date_utc=shared_edit_date,
            update_kind="poll_edit",
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert len(signal["revisions"]) == 3
    assert [row["telegram_ts_utc"] for row in signal["revisions"]] == [
        shared_edit_date,
        shared_edit_date,
        shared_edit_date,
    ]
    assert [row["range"] for row in signal["entry_zone_timeline"]] == [
        [4100.0, 4105.0],
        [4101.0, 4106.0],
        [4100.0, 4105.0],
    ]
    assert [row["tps"] for row in signal["level_timeline"]] == [
        [4098.0],
        [4097.0],
        [4098.0],
    ]
    assert [row["sl"] for row in signal["level_timeline"]] == [
        4108.0,
        4109.0,
        4108.0,
    ]
    assert signal["effective_range"] == [4100.0, 4105.0]
    assert signal["effective_tps"] == [4098.0]
    assert signal["effective_sl"] == 4108.0


def test_edit_and_poll_edit_duplicate_of_same_revision_are_fused():
    text = "Buy Gold Now\n4100 - 4105\nTargets\n4108\nSL 4095"
    common = {
        "date_utc": "2026-07-08T10:00:00+00:00",
        "has_document": True,
        "media_sha256": "same-media",
        "media_path": "media/same.bin",
    }
    events = [
        _raw(
            "canal2",
            352,
            text,
            ts="2026-07-08T10:20:01+00:00",
            update_kind="edit",
            is_edit=True,
            edit_date_utc="2026-07-08T10:20:00+00:00",
            **common,
        ),
        _raw(
            "canal2",
            352,
            text,
            ts="2026-07-08T10:20:02+00:00",
            update_kind="poll_edit",
            edit_date_utc="2026-07-08T10:21:00+00:00",
            **common,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert len(signal["revisions"]) == 1
    assert signal["revisions"][0]["update_kinds"] == ["edit", "poll_edit"]
    assert signal["revisions"][0]["observed_ts_utc"] == events[0]["ts"]
    assert signal["revisions"][0]["telegram_ts_utc"] == events[0]["edit_date_utc"]


def test_revisions_and_derived_timelines_follow_observed_time():
    earlier = _raw(
        "canal2",
        353,
        "Buy Gold Now\n4100 - 4105\nTargets\n4108\nSL 4095",
        ts="2026-07-08T12:30:00+02:00",
        date_utc="2026-07-08T11:00:00+00:00",
    )
    later = _raw(
        "canal2",
        353,
        "Buy Gold Now\n4101 - 4106\nTargets\n4109\nSL 4096",
        ts="2026-07-08T10:31:00+00:00",
        date_utc="2026-07-08T09:00:00+00:00",
        edit_date_utc="2026-07-08T09:01:00+00:00",
        update_kind="edit",
        is_edit=True,
    )

    report = provider_signal_catalog.build_catalog_report([later, earlier], [])
    signal = report["signals"][0]

    assert [row["text"] for row in signal["revisions"]] == [
        earlier["text"],
        later["text"],
    ]
    assert [row["range"] for row in signal["entry_zone_timeline"]] == [
        [4100.0, 4105.0],
        [4101.0, 4106.0],
    ]
    assert [row["tps"] for row in signal["level_timeline"]] == [
        [4108.0],
        [4109.0],
    ]
    assert signal["effective_range"] == [4101.0, 4106.0]
    assert signal["effective_tps"] == [4109.0]
    assert signal["effective_sl"] == 4096.0


def test_canal1_single_entry_price_is_a_complete_entry_zone():
    events = [
        _raw(
            "canal1",
            360,
            "SELL GOLD NOW 4166\nTP1: 4162\nTP2: 4158\n"
            "TP3: 4154\nTP4: 4150\nSL: 4180",
        )
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert signal["effective_range"] == [4166.0, 4166.0]
    assert signal["entry_zone_timeline"][0]["range"] == [4166.0, 4166.0]
    assert signal["semantic_status"] == "complete"


def test_recent_sticker_and_text_only_recovery_are_one_provider_signal():
    events = [
        _raw(
            "canal1",
            400,
            sticker_id=12345,
            has_document=True,
            ts="2026-07-08T12:00:00+00:00",
            date_utc="2026-07-08T12:00:00+00:00",
        ),
        {
            "ts": "2026-07-08T12:00:01+00:00",
            "sig": "canal1_400",
            "ev": "telegram_understood",
            "channel": "canal1",
            "message_id": 400,
            "direction": "SELL",
        },
        _raw(
            "canal1",
            401,
            "SELL GOLD NOW 4100-05\nTP1: 4098\nTP2: 4096\n"
            "TP3: 4094\nTP4: 4090\nSL: 4108",
            ts="2026-07-08T12:00:50+00:00",
            date_utc="2026-07-08T12:00:50+00:00",
        ),
        {
            "ts": "2026-07-08T12:00:51+00:00",
            "sig": "canal1_401",
            "ev": "canal1_text_processing",
            "source_msg_id": 401,
        },
    ]
    replay_trades = [
        {"sig_id": "canal1_400", "channel": "canal1"},
        {"sig_id": "canal1_401", "channel": "canal1"},
    ]

    report = provider_signal_catalog.build_catalog_report(events, replay_trades)

    assert report["summary"]["provider_signals"] == 1
    signal = report["signals"][0]
    assert signal["provider_signal_id"] == "canal1_400"
    assert signal["source_message_ids"] == [400, 401]
    assert signal["duplicate_execution"] is True
    assert signal["entry_contract"] == {
        "status": "ready",
        "trigger_observed_utc": "2026-07-08T12:00:01+00:00",
        "trigger_telegram_utc": "2026-07-08T12:00:00+00:00",
        "trigger_message_id": 400,
        "trigger_kind": "sticker",
        "direction": "SELL",
        "direction_source": "telegram_understood",
        "blockers": [],
    }


def test_entry_contract_uses_observed_order_and_marks_first_direction_edit():
    initial = _raw(
        "canal2",
        610,
        "Gold levels pending",
        ts="2026-07-08T10:00:01+00:00",
        date_utc="2026-07-08T10:00:10+00:00",
    )
    direction_edit = _raw(
        "canal2",
        610,
        "Buy Gold Now\n4100 - 4105\nTargets\n4108\n4110\nSL 4095",
        ts="2026-07-08T10:00:02+00:00",
        update_kind="edit",
        is_edit=True,
        edit_date_utc="2026-07-08T10:00:00+00:00",
    )

    report = provider_signal_catalog.build_catalog_report(
        [direction_edit, initial],
        [{"sig_id": "canal2_610", "channel": "canal2"}],
    )
    signal = report["signals"][0]

    assert signal["entry_contract"] == {
        "status": "ready",
        "trigger_observed_utc": "2026-07-08T10:00:02+00:00",
        "trigger_telegram_utc": "2026-07-08T10:00:00+00:00",
        "trigger_message_id": 610,
        "trigger_kind": "edit",
        "direction": "BUY",
        "direction_source": "revision_parser:610",
        "blockers": [],
    }
    assert [revision["text"] for revision in signal["revisions"]] == [
        initial["text"],
        direction_edit["text"],
    ]


def test_understood_direction_does_not_replace_parser_source_for_same_trigger():
    events = [
        _raw(
            "canal1",
            620,
            "BUY GOLD NOW 4100-05\nTP1: 4108\nSL: 4095",
            sticker_id=12345,
            has_document=True,
            ts="2026-07-08T13:00:02+00:00",
            date_utc="2026-07-08T13:00:01+00:00",
        ),
        {
            "ts": "2026-07-08T13:00:03+00:00",
            "sig": "canal1_620",
            "ev": "telegram_understood",
            "channel": "canal1",
            "message_id": 620,
            "direction": "BUY",
        },
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert signal["entry_contract"]["direction_source"] == "revision_parser:620"
    assert signal["entry_contract"]["trigger_message_id"] == 620
    assert signal["entry_contract"]["trigger_kind"] == "sticker"


def test_trigger_kind_uses_how_the_actionable_revision_first_arrived():
    text = "Sell Gold Now\n4100 - 4105\nTargets\n4098\nSL 4108"
    events = [
        _raw(
            "canal2",
            630,
            text,
            ts="2026-07-08T14:00:01+00:00",
            date_utc="2026-07-08T14:00:00+00:00",
        ),
        _raw(
            "canal2",
            630,
            text,
            ts="2026-07-08T14:00:02+00:00",
            update_kind="edit",
            is_edit=True,
            edit_date_utc="2026-07-08T14:00:00+00:00",
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert signal["revisions"][0]["update_kinds"] == ["new", "edit"]
    assert signal["entry_contract"]["trigger_kind"] == "text"


def test_entry_contract_freezes_direction_from_first_causal_revision():
    initial = _raw(
        "canal2",
        640,
        "Buy Gold Now\n4100 - 4105\nTargets\n4108\n4110\nSL 4095",
        ts="2026-07-08T15:00:01+00:00",
        date_utc="2026-07-08T15:00:00+00:00",
    )
    direction_edit = _raw(
        "canal2",
        640,
        "Sell Gold Now\n4100 - 4105\nTargets\n4098\n4096\nSL 4110",
        ts="2026-07-08T15:00:02+00:00",
        update_kind="edit",
        is_edit=True,
        edit_date_utc="2026-07-08T15:00:02+00:00",
    )

    report = provider_signal_catalog.build_catalog_report(
        [direction_edit, initial],
        [],
    )
    signal = report["signals"][0]

    assert signal["direction"] == "SELL"
    assert signal["entry_contract"] == {
        "status": "ready",
        "trigger_observed_utc": "2026-07-08T15:00:01+00:00",
        "trigger_telegram_utc": "2026-07-08T15:00:00+00:00",
        "trigger_message_id": 640,
        "trigger_kind": "text",
        "direction": "BUY",
        "direction_source": "revision_parser:640",
        "blockers": [],
    }


def test_canal1_pairing_rejects_text_observed_before_sticker():
    events = [
        _raw(
            "canal1",
            650,
            sticker_id=12345,
            has_document=True,
            ts="2026-07-08T16:00:02+00:00",
            date_utc="2026-07-08T16:00:00+00:00",
        ),
        {
            "ts": "2026-07-08T16:00:03+00:00",
            "sig": "canal1_650",
            "ev": "telegram_understood",
            "channel": "canal1",
            "message_id": 650,
            "direction": "BUY",
        },
        _raw(
            "canal1",
            651,
            "BUY GOLD NOW 4100-05\nTP1: 4108\nTP2: 4110\nSL: 4095",
            ts="2026-07-08T16:00:01+00:00",
            date_utc="2026-07-08T16:00:10+00:00",
        ),
        {
            "ts": "2026-07-08T16:00:04+00:00",
            "sig": "canal1_650",
            "ev": "canal1_text_processing",
            "source_msg_id": 651,
        },
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signals = {
        signal["provider_signal_id"]: signal
        for signal in report["signals"]
    }

    assert set(signals) == {"canal1_650", "canal1_651"}
    assert signals["canal1_650"]["source_message_ids"] == [650]
    assert signals["canal1_651"]["source_message_ids"] == [651]
    assert signals["canal1_650"]["entry_contract"]["trigger_kind"] == "sticker"
    assert signals["canal1_651"]["entry_contract"]["trigger_kind"] == "text"
    assert "identity_links" not in signals["canal1_650"]
    assert "identity_links" not in signals["canal1_651"]


def test_processing_fallback_groups_delayed_companion_without_backdating_levels():
    sticker_ts = "2026-07-08T16:30:00+00:00"
    understood_ts = "2026-07-08T16:30:00.100+00:00"
    text_ts = "2026-07-08T16:42:00+00:00"
    events = [
        _raw(
            "canal1",
            655,
            sticker_id=12345,
            has_document=True,
            ts=sticker_ts,
            date_utc="2026-07-08T16:29:59+00:00",
        ),
        {
            "ts": understood_ts,
            "sig": "canal1_655",
            "ev": "telegram_understood",
            "channel": "canal1",
            "message_id": 655,
            "direction": "BUY",
            "tg_ts": "2026-07-08T16:29:59+00:00",
        },
        _raw(
            "canal1",
            656,
            "BUY GOLD NOW 4100-05\nTP1: 4108\nSL: 4095",
            ts=text_ts,
            date_utc="2026-07-08T16:41:59+00:00",
        ),
        {
            "ts": "2026-07-08T16:42:00.010+00:00",
            "sig": "canal1_655",
            "ev": "canal1_text_processing",
            "source_msg_id": 656,
        },
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert report["summary"]["provider_signals"] == 1
    assert signal["provider_signal_id"] == "canal1_655"
    assert signal["source_message_ids"] == [655, 656]
    assert signal["entry_contract"]["trigger_message_id"] == 655
    assert signal["entry_contract"]["trigger_observed_utc"] == understood_ts
    assert signal["entry_contract"]["trigger_kind"] == "sticker"
    assert signal["entry_zone_timeline"][-1]["source_message_id"] == 656
    assert signal["entry_zone_timeline"][-1]["observed_ts_utc"] == text_ts
    assert signal["level_timeline"][-1]["observed_ts_utc"] == text_ts
    assert signal["identity_links"] == [{
        "source": "processing_fallback",
        "root_message_id": 655,
        "companion_message_id": 656,
        "observed_gap_ms": 720000,
    }]


def test_canal1_pairing_ignores_sticker_text_direction_disagreement():
    events = [
        _raw(
            "canal1",
            700,
            sticker_id=12345,
            has_document=True,
            ts="2026-07-08T18:00:00+00:00",
            date_utc="2026-07-08T17:59:59+00:00",
        ),
        {
            "ts": "2026-07-08T18:00:01+00:00",
            "sig": "canal1_700",
            "ev": "telegram_understood",
            "channel": "canal1",
            "message_id": 700,
            "direction": "BUY",
            "tg_ts": "2026-07-08T17:59:59+00:00",
        },
        _raw(
            "canal1",
            701,
            "SELL GOLD NOW 4100-05\nTP1: 4098\nSL: 4108",
            ts="2026-07-08T18:00:30+00:00",
            date_utc="2026-07-08T18:00:29+00:00",
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])

    assert report["summary"]["provider_signals"] == 1
    signal = report["signals"][0]
    assert signal["provider_signal_id"] == "canal1_700"
    assert signal["source_message_ids"] == [700, 701]
    assert signal["direction"] == "SELL"
    assert signal["entry_contract"]["direction"] == "BUY"
    assert signal["entry_contract"]["direction_source"] == "telegram_understood"


def test_canal1_duplicate_associations_choose_nearest_root_invariantly():
    sticker_far = _raw(
        "canal1",
        800,
        sticker_id=12345,
        has_document=True,
        ts="2026-07-08T18:20:00+00:00",
    )
    sticker_near = _raw(
        "canal1",
        801,
        sticker_id=12346,
        has_document=True,
        ts="2026-07-08T18:25:00+00:00",
    )
    text = _raw(
        "canal1",
        802,
        "BUY GOLD NOW 4100-05\nTP1: 4108\nSL: 4095",
        ts="2026-07-08T18:31:30+00:00",
    )
    association_far = {
        "ts": "2026-07-08T18:31:31+00:00",
        "sig": "canal1_800",
        "ev": "canal1_text_processing",
        "source_msg_id": 802,
    }
    association_near = {
        **association_far,
        "sig": "canal1_801",
    }

    ordered = provider_signal_catalog.build_catalog_report(
        [sticker_far, sticker_near, text, association_far, association_near],
        [],
    )
    permuted = provider_signal_catalog.build_catalog_report(
        [sticker_far, sticker_near, text, association_near, association_far],
        [],
    )

    def text_root(report):
        return next(
            signal["provider_signal_id"]
            for signal in report["signals"]
            if 802 in signal["source_message_ids"]
        )

    assert text_root(ordered) == "canal1_801"
    assert text_root(permuted) == "canal1_801"
    for report in (ordered, permuted):
        signal = next(
            row for row in report["signals"]
            if row["provider_signal_id"] == "canal1_801"
        )
        assert signal["identity_links"] == [{
            "source": "processing_fallback",
            "root_message_id": 801,
            "companion_message_id": 802,
            "observed_gap_ms": 390000,
        }]


@pytest.mark.parametrize(
    ("older_sticker_id", "nearest_sticker_id", "text_id"),
    [
        (20688, 20689, 20690),
        (20700, 20701, 20702),
    ],
)
def test_canal1_raw_nearest_sticker_beats_processing_association(
    older_sticker_id,
    nearest_sticker_id,
    text_id,
):
    events = [
        _raw(
            "canal1",
            older_sticker_id,
            sticker_id=12345,
            has_document=True,
            ts="2026-07-08T20:00:00+00:00",
        ),
        _raw(
            "canal1",
            nearest_sticker_id,
            sticker_id=12346,
            has_document=True,
            ts="2026-07-08T20:00:02+00:00",
        ),
        _raw(
            "canal1",
            text_id,
            "BUY GOLD NOW 4100-05\nTP1: 4108\nSL: 4095",
            ts="2026-07-08T20:01:00+00:00",
        ),
        {
            "ts": "2026-07-08T20:01:01+00:00",
            "sig": f"canal1_{older_sticker_id}",
            "ev": "canal1_text_processing",
            "source_msg_id": text_id,
        },
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    text_record = next(
        signal
        for signal in report["signals"]
        if text_id in signal["source_message_ids"]
    )

    assert text_record["provider_signal_id"] == f"canal1_{nearest_sticker_id}"
    assert text_record["source_message_ids"] == [nearest_sticker_id, text_id]
    assert text_record["identity_links"] == [{
        "source": "raw_nearest",
        "root_message_id": nearest_sticker_id,
        "companion_message_id": text_id,
        "observed_gap_ms": 58000,
    }]


def test_canal1_pairing_is_invariant_for_equal_observed_timestamps():
    sticker_710 = _raw(
        "canal1",
        710,
        sticker_id=12345,
        has_document=True,
        ts="2026-07-08T19:00:00+00:00",
        date_utc="2026-07-08T19:00:01+00:00",
    )
    sticker_711 = _raw(
        "canal1",
        711,
        sticker_id=12346,
        has_document=True,
        ts="2026-07-08T19:00:00+00:00",
        date_utc="2026-07-08T19:00:00+00:00",
    )
    text_720 = _raw(
        "canal1",
        720,
        "BUY GOLD NOW 4100-05\nTP1: 4108\nSL: 4095",
        ts="2026-07-08T19:00:30+00:00",
        date_utc="2026-07-08T19:00:31+00:00",
    )
    text_721 = _raw(
        "canal1",
        721,
        "BUY GOLD NOW 4100-05\nTP1: 4108\nSL: 4095",
        ts="2026-07-08T19:00:30+00:00",
        date_utc="2026-07-08T19:00:30+00:00",
    )

    ordered = provider_signal_catalog.build_catalog_report(
        [sticker_710, sticker_711, text_720, text_721],
        [],
    )
    permuted = provider_signal_catalog.build_catalog_report(
        [sticker_711, sticker_710, text_721, text_720],
        [],
    )

    ordered_sources = {
        signal["provider_signal_id"]: signal["source_message_ids"]
        for signal in ordered["signals"]
    }
    permuted_sources = {
        signal["provider_signal_id"]: signal["source_message_ids"]
        for signal in permuted["signals"]
    }
    assert ordered_sources == permuted_sources
    assert ordered_sources == {
        "canal1_710": [710, 721],
        "canal1_711": [711, 720],
    }


def test_canal1_same_timestamp_pairing_uses_message_id_under_permutation():
    sticker = _raw(
        "canal1",
        810,
        sticker_id=12345,
        has_document=True,
        ts="2026-07-08T20:30:00+00:00",
    )
    text = _raw(
        "canal1",
        811,
        "BUY GOLD NOW 4100-05\nTP1: 4108\nSL: 4095",
        ts="2026-07-08T20:30:00+00:00",
    )

    ordered = provider_signal_catalog.build_catalog_report([sticker, text], [])
    permuted = provider_signal_catalog.build_catalog_report([text, sticker], [])

    for report in (ordered, permuted):
        text_record = next(
            signal
            for signal in report["signals"]
            if 811 in signal["source_message_ids"]
        )
        assert text_record["provider_signal_id"] == "canal1_810"
        assert text_record["source_message_ids"] == [810, 811]


def test_first_actionable_poll_edit_has_edit_trigger_kind():
    events = [
        _raw(
            "canal2",
            660,
            "Sell Gold Now\n4100 - 4105\nTargets\n4098\n4096\nSL 4110",
            ts="2026-07-08T17:00:01+00:00",
            update_kind="poll_edit",
            date_utc="2026-07-08T16:59:00+00:00",
            edit_date_utc="2026-07-08T17:00:00+00:00",
        )
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert signal["entry_contract"]["trigger_kind"] == "edit"
    assert signal["entry_contract"]["trigger_telegram_utc"] == (
        "2026-07-08T17:00:00+00:00"
    )


def test_runtime_direction_and_execution_do_not_invent_provider_signal():
    events = [
        _raw(
            "canal2",
            670,
            ts="2026-07-08T18:00:01+00:00",
            date_utc="2026-07-08T18:00:00+00:00",
        ),
        {
            "ts": "2026-07-08T18:00:02+00:00",
            "sig": "canal2_670",
            "ev": "telegram_understood",
            "channel": "canal2",
            "message_id": 670,
            "direction": "BUY",
            "kind": "entry_signal",
        },
    ]

    report = provider_signal_catalog.build_catalog_report(
        events,
        [{"sig_id": "canal2_670", "channel": "canal2"}],
    )
    signal = report["signals"][0]

    assert signal["record_type"] == "unknown_candidate"
    assert signal["entry_contract"] == {
        "status": "blocked",
        "trigger_observed_utc": None,
        "trigger_telegram_utc": None,
        "trigger_message_id": None,
        "trigger_kind": None,
        "direction": "BUY",
        "direction_source": "telegram_understood",
        "blockers": ["provider_record_not_formal_signal"],
    }


def test_text_trigger_wins_before_late_sticker_understanding():
    events = [
        _raw(
            "canal1",
            680,
            sticker_id=12345,
            has_document=True,
            ts="2026-07-08T11:00:00+00:00",
            date_utc="2026-07-08T10:59:59+00:00",
        ),
        _raw(
            "canal1",
            681,
            "BUY GOLD NOW 4100-05\nTP1: 4108\nSL: 4095",
            ts="2026-07-08T11:01:00+00:00",
            date_utc="2026-07-08T11:00:59+00:00",
        ),
        {
            "ts": "2026-07-08T11:01:01+00:00",
            "sig": "canal1_680",
            "ev": "canal1_text_processing",
            "source_msg_id": 681,
        },
        {
            "ts": "2026-07-08T11:02:00+00:00",
            "sig": "canal1_680",
            "ev": "telegram_understood",
            "channel": "canal1",
            "message_id": 680,
            "direction": "SELL",
            "tg_ts": "2026-07-08T10:59:59+00:00",
        },
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert signal["direction"] == "SELL"
    assert signal["entry_contract"] == {
        "status": "ready",
        "trigger_observed_utc": "2026-07-08T11:01:00+00:00",
        "trigger_telegram_utc": "2026-07-08T11:00:59+00:00",
        "trigger_message_id": 681,
        "trigger_kind": "text",
        "direction": "BUY",
        "direction_source": "revision_parser:681",
        "blockers": [],
    }


def test_first_sticker_understanding_wins_before_text_and_future_contradiction():
    events = [
        _raw(
            "canal1",
            690,
            sticker_id=12345,
            has_document=True,
            ts="2026-07-08T12:00:00+00:00",
            date_utc="2026-07-08T11:59:59+00:00",
        ),
        {
            "ts": "2026-07-08T12:00:01+00:00",
            "sig": "canal1_690",
            "ev": "telegram_understood",
            "channel": "canal1",
            "message_id": 690,
            "direction": "SELL",
            "tg_ts": "2026-07-08T11:59:59+00:00",
        },
        _raw(
            "canal1",
            691,
            "SELL GOLD NOW 4100-05\nTP1: 4098\nSL: 4108",
            ts="2026-07-08T12:01:00+00:00",
            date_utc="2026-07-08T12:00:59+00:00",
        ),
        {
            "ts": "2026-07-08T12:01:01+00:00",
            "sig": "canal1_690",
            "ev": "canal1_text_processing",
            "source_msg_id": 691,
        },
        {
            "ts": "2026-07-08T12:02:00+00:00",
            "sig": "canal1_690",
            "ev": "telegram_understood",
            "channel": "canal1",
            "message_id": 690,
            "direction": "BUY",
            "tg_ts": "2026-07-08T11:59:59+00:00",
        },
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert signal["direction"] == "BUY"
    assert signal["entry_contract"] == {
        "status": "ready",
        "trigger_observed_utc": "2026-07-08T12:00:01+00:00",
        "trigger_telegram_utc": "2026-07-08T11:59:59+00:00",
        "trigger_message_id": 690,
        "trigger_kind": "sticker",
        "direction": "SELL",
        "direction_source": "telegram_understood",
        "blockers": [],
    }
    assert not any(key.startswith("_") for key in signal)


def test_simultaneous_understanding_uses_event_order_not_telegram_time():
    first_understanding = {
        "ts": "2026-07-08T19:30:01+00:00",
        "sig": "canal1_695",
        "ev": "telegram_understood",
        "channel": "canal1",
        "message_id": 695,
        "direction": "BUY",
        "tg_ts": "2026-07-08T19:30:10+00:00",
    }
    telegram_earlier = {
        **first_understanding,
        "tg_ts": "2026-07-08T19:29:50+00:00",
    }
    events = [
        _raw(
            "canal1",
            695,
            sticker_id=12345,
            has_document=True,
            ts="2026-07-08T19:30:00+00:00",
            date_utc="2026-07-08T19:29:59+00:00",
        ),
        first_understanding,
        telegram_earlier,
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    contract = report["signals"][0]["entry_contract"]

    assert contract["trigger_observed_utc"] == first_understanding["ts"]
    assert contract["trigger_telegram_utc"] == first_understanding["tg_ts"]


def test_simultaneous_revision_directions_use_source_order():
    observed = "2026-07-08T21:00:00+00:00"
    first = _raw(
        "canal2",
        820,
        "Sell Gold Now\n4100 - 4105\nTargets\n4098\nSL 4108",
        ts=observed,
    )
    second = _raw(
        "canal2",
        820,
        "Buy Gold Now\n4100 - 4105\nTargets\n4108\nSL 4095",
        ts=observed,
        update_kind="edit",
        is_edit=True,
        edit_date_utc="2026-07-08T21:00:01+00:00",
    )

    report = provider_signal_catalog.build_catalog_report([first, second], [])
    signal = report["signals"][0]

    assert signal["entry_contract"]["direction"] == "SELL"
    assert signal["direction"] == "BUY"


def test_simultaneous_understood_directions_use_source_order():
    first = {
        "ts": "2026-07-08T21:10:01+00:00",
        "sig": "canal1_830",
        "ev": "telegram_understood",
        "channel": "canal1",
        "message_id": 830,
        "direction": "SELL",
    }
    second = {**first, "direction": "BUY"}
    events = [
        _raw(
            "canal1",
            830,
            sticker_id=12345,
            has_document=True,
            ts="2026-07-08T21:10:00+00:00",
        ),
        first,
        second,
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert signal["entry_contract"]["direction"] == "SELL"
    assert signal["direction"] == "BUY"


def test_simultaneous_understanding_and_revision_use_source_order():
    observed = "2026-07-08T21:20:00+00:00"
    understanding = {
        "ts": observed,
        "sig": "canal1_840",
        "ev": "telegram_understood",
        "channel": "canal1",
        "message_id": 840,
        "direction": "BUY",
    }
    revision = _raw(
        "canal1",
        840,
        "SELL GOLD NOW 4100-05\nTP1: 4098\nSL: 4108",
        sticker_id=12345,
        has_document=True,
        ts=observed,
    )

    report = provider_signal_catalog.build_catalog_report(
        [understanding, revision],
        [],
    )
    signal = report["signals"][0]

    assert signal["entry_contract"]["direction"] == "BUY"
    assert signal["direction"] == "SELL"
    assert "_source_order" not in json.dumps(report, sort_keys=True)


def test_understanding_before_sticker_triggers_at_sticker_observation():
    understanding = {
        "ts": "2026-07-08T21:30:00+00:00",
        "sig": "canal1_850",
        "ev": "telegram_understood",
        "channel": "canal1",
        "message_id": 850,
        "direction": "BUY",
        "tg_ts": "2026-07-08T21:29:59+00:00",
    }
    sticker = _raw(
        "canal1",
        850,
        sticker_id=12345,
        has_document=True,
        ts="2026-07-08T21:30:01+00:00",
        date_utc="2026-07-08T21:30:00+00:00",
    )

    report = provider_signal_catalog.build_catalog_report(
        [understanding, sticker],
        [],
    )
    contract = report["signals"][0]["entry_contract"]

    assert contract["trigger_observed_utc"] == sticker["ts"]
    assert contract["trigger_kind"] == "sticker"
    assert contract["direction"] == "BUY"


def test_earlier_understanding_uses_sticker_source_order_for_causal_max():
    observed = "2026-07-08T21:40:01+00:00"
    understanding = {
        "ts": "2026-07-08T21:40:00+00:00",
        "sig": "canal1_851",
        "ev": "telegram_understood",
        "channel": "canal1",
        "message_id": 851,
        "direction": "BUY",
    }
    direction_before_sticker = _raw(
        "canal1",
        851,
        "SELL GOLD NOW 4100-05\nTP1: 4098\nSL: 4108",
        ts=observed,
    )
    sticker = _raw(
        "canal1",
        851,
        sticker_id=12345,
        has_document=True,
        ts=observed,
    )

    report = provider_signal_catalog.build_catalog_report(
        [understanding, direction_before_sticker, sticker],
        [],
    )
    signal = report["signals"][0]

    assert signal["entry_contract"]["direction"] == "SELL"
    assert signal["entry_contract"]["trigger_observed_utc"] == observed


def test_irrelevant_reply_does_not_create_a_provider_signal():
    events = [
        _raw(
            "canal2",
            501,
            "Good morning everyone",
            reply_to_msg_id=500,
            is_reply=True,
        )
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])

    assert report["summary"]["provider_signals"] == 0


def test_context_photo_and_progress_replies_do_not_inflate_formal_signals():
    events = [
        _raw(
            "canal2",
            600,
            "4HR support lines. Experienced members can use these levels.",
            has_photo=True,
        ),
        _raw(
            "canal2",
            601,
            "+50 pips from the 4hr line",
            reply_to_msg_id=600,
            is_reply=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])

    assert report["summary"]["records"] == 1
    assert report["summary"]["provider_signals"] == 0
    assert report["summary"]["record_types"] == {"context_setup": 1}
    record = report["signals"][0]
    assert record["record_type"] == "context_setup"
    assert record["semantic_status"] == "classified"
    assert record["media"]["availability"] == "metadata_only"
    assert record["media"]["extraction_status"] == "not_extracted"
    event = record["management_events"][0]
    assert event["classified_action"] == "PROGRESS_UPDATE"
    assert event["modality"] == "informational"


def test_zone_plan_is_preserved_without_becoming_a_live_market_trigger():
    events = [
        _raw(
            "canal2",
            3597,
            "These are all the zones I would buy from. Target is 4125",
            has_photo=True,
        ),
        _raw(
            "canal2",
            3598,
            "Buy Zones Marked Out\n\n4075-4073\n4070-4069\n"
            "4066-4064\n4062-4060\n4059-4057",
            reply_to_msg_id=3597,
            is_reply=True,
        ),
        _raw(
            "canal2",
            3599,
            "First Zone\n\n+40 pips",
            reply_to_msg_id=3598,
            is_reply=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])

    assert report["summary"]["provider_signals"] == 0
    assert report["summary"]["record_types"] == {"zone_plan": 1}
    assert len(report["signals"]) == 1
    plan = report["signals"][0]
    assert plan["provider_signal_id"] == "canal2_3597"
    assert plan["record_type"] == "zone_plan"
    assert plan["direction"] == "BUY"
    assert plan["zone_target"] == 4125.0
    assert plan["entry_zones"] == [
        [4073.0, 4075.0],
        [4069.0, 4070.0],
        [4064.0, 4066.0],
        [4060.0, 4062.0],
        [4057.0, 4059.0],
    ]
    assert plan["source_message_ids"] == [3597, 3598]
    assert plan["management_events"][0]["message_id"] == 3599
    assert plan["entry_contract"]["status"] == "blocked"
    assert plan["entry_contract"]["blockers"] == [
        "provider_zone_plan_not_live_trigger"
    ]


def test_single_price_future_zone_is_preserved_as_zone_plan():
    events = [
        _raw(
            "canal2",
            612,
            "Next Sell Zone at 4030\n\n"
            "Bear in mind FOMC at 7\n\n"
            "Look for a quick reaction",
        ),
        _raw(
            "canal2",
            615,
            "Take profit from layers",
            reply_to_msg_id=612,
            is_reply=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])

    assert report["summary"]["provider_signals"] == 0
    assert report["summary"]["record_types"] == {"zone_plan": 1}
    plan = report["signals"][0]
    assert plan["provider_signal_id"] == "canal2_612"
    assert plan["direction"] == "SELL"
    assert plan["entry_zones"] == [[4030.0, 4030.0]]
    assert plan["management_events"][0]["classified_action"] == "CLOSE_PARTIAL"


def test_zone_revision_is_preserved_when_same_message_becomes_live_entry():
    events = [
        _raw(
            "canal2",
            700,
            "Next Buy Zone at 4040",
            ts="2026-07-29T12:00:01+00:00",
            date_utc="2026-07-29T12:00:00+00:00",
        ),
        _raw(
            "canal2",
            700,
            "Buy Gold Now",
            update_kind="edit",
            is_edit=True,
            ts="2026-07-29T12:01:01+00:00",
            date_utc="2026-07-29T12:00:00+00:00",
            edit_date_utc="2026-07-29T12:01:00+00:00",
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])

    assert report["summary"]["provider_signals"] == 1
    signal = report["signals"][0]
    assert signal["provider_signal_id"] == "canal2_700"
    assert signal["record_type"] == "formal_signal"
    assert signal["direction"] == "BUY"
    assert signal["entry_zones"] == [[4040.0, 4040.0]]
    assert signal["zone_plan_timeline"] == [
        {
            "message_id": 700,
            "observed_ts_utc": "2026-07-29T12:00:01+00:00",
            "telegram_ts_utc": "2026-07-29T12:00:00+00:00",
            "direction": "BUY",
            "zones": [[4040.0, 4040.0]],
            "target": None,
        }
    ]


def test_chart_reply_with_bare_sell_areas_is_preserved_as_zone_plan():
    events = [
        _raw(
            "canal2",
            598,
            has_photo=True,
            ts="2026-07-29T15:06:14+00:00",
            date_utc="2026-07-29T15:06:13+00:00",
        ),
        _raw(
            "canal2",
            599,
            "4007\n4010\n4017\n\n"
            "These are all strong areas we can expect gold to sell from",
            reply_to_msg_id=598,
            is_reply=True,
            ts="2026-07-29T15:06:48+00:00",
            date_utc="2026-07-29T15:06:46+00:00",
        ),
        _raw(
            "canal2",
            603,
            "Next Sell Zone we can expect a reaction is 4017",
            reply_to_msg_id=598,
            is_reply=True,
            ts="2026-07-29T15:29:09+00:00",
            date_utc="2026-07-29T15:29:08+00:00",
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])

    assert report["summary"]["record_types"] == {"zone_plan": 1}
    plan = report["signals"][0]
    assert plan["provider_signal_id"] == "canal2_598"
    assert plan["source_message_ids"] == [598, 599, 603]
    assert plan["zone_plan_timeline"][0]["zones"] == [
        [4007.0, 4007.0],
        [4010.0, 4010.0],
        [4017.0, 4017.0],
    ]
    assert plan["zone_plan_timeline"][1]["zones"] == [[4017.0, 4017.0]]


def test_executed_context_comment_is_not_promoted_to_provider_signal():
    context = (
        "As mentioned on the live call I will start to send charts so you "
        "can see what I see & where I mark my zones!\n\n"
        "Right now we are between 2 key areas\n\n"
        "Potential sell at 4051"
    )
    events = [
        _raw(
            "canal2",
            562,
            context,
            ts="2026-07-29T07:29:29+00:00",
            date_utc="2026-07-29T07:29:28+00:00",
        ),
        {
            "ts": "2026-07-29T07:29:29.100+00:00",
            "sig": "canal2_562",
            "ev": "signal_received",
            "channel": "canal2",
            "direction": "SELL",
        },
        {
            "ts": "2026-07-29T07:29:29.200+00:00",
            "sig": "canal2_562",
            "ev": "market_filled",
            "ticket": 562001,
            "price": 4049.5,
        },
    ]
    replay = [{
        "sig_id": "canal2_562",
        "channel": "canal2",
        "tickets": [{"ticket": 562001, "open_price": 4049.5}],
    }]

    report = provider_signal_catalog.build_catalog_report(events, replay)

    assert report["summary"]["provider_signals"] == 0
    record = report["signals"][0]
    assert record["record_type"] == "context_setup"
    assert record["execution_count"] == 1
    assert record["execution_sig_ids"] == ["canal2_562"]
    assert record["entry_contract"]["status"] == "blocked"
    assert record["entry_contract"]["blockers"] == [
        "provider_record_not_formal_signal"
    ]


def test_executed_potential_comment_stays_unknown_not_formal_signal():
    events = [
        _raw(
            "canal2",
            563,
            "Potential sell at 4051",
            ts="2026-07-29T07:30:01+00:00",
            date_utc="2026-07-29T07:30:00+00:00",
        ),
        {
            "ts": "2026-07-29T07:30:01.100+00:00",
            "sig": "canal2_563",
            "ev": "signal_received",
            "channel": "canal2",
            "direction": "SELL",
        },
        {
            "ts": "2026-07-29T07:30:01.200+00:00",
            "sig": "canal2_563",
            "ev": "market_filled",
            "ticket": 563001,
            "price": 4049.5,
        },
    ]
    replay = [{
        "sig_id": "canal2_563",
        "channel": "canal2",
        "tickets": [{"ticket": 563001, "open_price": 4049.5}],
    }]

    report = provider_signal_catalog.build_catalog_report(events, replay)

    record = report["signals"][0]
    assert record["record_type"] == "unknown_candidate"
    assert record["execution_count"] == 1
    assert record["entry_contract"]["blockers"] == [
        "provider_record_not_formal_signal"
    ]


def test_reply_reentry_and_standalone_repeat_share_one_provider_identity():
    events = [
        _raw(
            "canal2",
            585,
            "Sell Gold Now",
            ts="2026-07-29T14:00:45.266+00:00",
            date_utc="2026-07-29T14:00:39+00:00",
            reply_to_msg_id=580,
            is_reply=True,
        ),
        _raw(
            "canal2",
            586,
            "Sell gold now",
            ts="2026-07-29T14:00:48.604+00:00",
            date_utc="2026-07-29T14:00:47+00:00",
        ),
        {
            "ts": "2026-07-29T14:00:48.606+00:00",
            "sig": "canal2_586",
            "ev": "signal_received",
            "channel": "canal2",
            "direction": "SELL",
        },
        {
            "ts": "2026-07-29T14:00:48.907+00:00",
            "sig": "canal2_586",
            "ev": "market_filled",
            "ticket": 586001,
            "price": 4002.81,
        },
    ]
    replay = [{
        "sig_id": "canal2_586",
        "channel": "canal2",
        "tickets": [{"ticket": 586001, "open_price": 4002.81}],
    }]

    report = provider_signal_catalog.build_catalog_report(events, replay)

    assert report["summary"]["provider_signals"] == 1
    signal = report["signals"][0]
    assert signal["provider_signal_id"] == "canal2_585"
    assert signal["source_message_ids"] == [585, 586]
    assert signal["execution_sig_ids"] == ["canal2_586"]
    assert signal["identity_links"] == [{
        "source": "near_duplicate_immediate_command",
        "root_message_id": 585,
        "companion_message_id": 586,
        "telegram_gap_ms": 8000,
    }]


def test_runtime_alias_and_raw_timing_evidence_are_both_preserved():
    events = [
        _raw(
            "canal2",
            585,
            "Sell Gold Now",
            ts="2026-07-29T14:00:45.266+00:00",
            date_utc="2026-07-29T14:00:39+00:00",
            reply_to_msg_id=580,
            is_reply=True,
        ),
        _raw(
            "canal2",
            586,
            "Sell gold now",
            ts="2026-07-29T14:00:48.604+00:00",
            date_utc="2026-07-29T14:00:47+00:00",
        ),
        {
            "ts": "2026-07-29T14:00:48.605+00:00",
            "sig": "canal2_585",
            "ev": "canal2_duplicate_alias_registered",
            "alias_message_id": 586,
        },
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]

    assert {
        link["source"] for link in signal["identity_links"]
    } == {
        "runtime_duplicate_alias",
        "near_duplicate_immediate_command",
    }
    timing = next(
        link for link in signal["identity_links"]
        if link["source"] == "near_duplicate_immediate_command"
    )
    assert timing["telegram_gap_ms"] == 8000


def test_execution_quality_is_rechecked_against_final_provider_range():
    events = [
        _raw(
            "canal2",
            810,
            "Buy Gold Now\n4000-4002\nTP1 4005\nSL 3996",
        )
    ]
    replay = [{
        "sig_id": "canal2_810",
        "decisions": {
            "entry_quality": {
                "case": "A_inside",
                "entry": 4004.0,
                "range_low": 3999.0,
                "range_high": 4005.0,
            }
        },
        "tickets": [{
            "ticket": 123,
            "open_price": 4004.0,
        }],
    }]

    signal = provider_signal_catalog.build_catalog_report(events, replay)[
        "signals"
    ][0]

    assert signal["execution_range_assessments"] == [{
        "sig_id": "canal2_810",
        "canonical_range": [4000.0, 4002.0],
        "runtime_entry_quality": replay[0]["decisions"]["entry_quality"],
        "tickets": [{
            "ticket": 123,
            "open_price": 4004.0,
            "inside_final_provider_range": False,
            "distance_outside": 2.0,
        }],
        "all_entries_inside": False,
        "max_distance_outside": 2.0,
    }]


def test_zone_commentary_does_not_overwrite_formal_signal_levels():
    events = [
        _raw(
            "canal2",
            820,
            "Sell Gold Now\n4040-4045\nTP1 4036\nTP2 4032\nSL 4060",
        ),
        _raw(
            "canal2",
            821,
            "SL got hit after price broke the 4055-4060 zone. "
            "A retest may offer buy continuation; below 4055 we wait "
            "for a new sell confirmation.",
            reply_to_msg_id=820,
            is_reply=True,
        ),
    ]

    signal = provider_signal_catalog.build_catalog_report(events, [])["signals"][0]

    assert signal["record_type"] == "formal_signal"
    assert signal["effective_range"] == [4040.0, 4045.0]
    assert signal["effective_tps"] == [4036.0, 4032.0]
    assert signal["effective_sl"] == 4060.0
    assert signal["semantic_status"] == "complete"


def test_numeric_move_sl_survives_informational_classifier_disagreement():
    events = [
        _raw(
            "canal2",
            700,
            "Sell Gold Now\n4100 - 4105\nTargets\n4098\n4096\nSL 4108",
        ),
        _raw(
            "canal2",
            701,
            "Move SL to 4061",
            reply_to_msg_id=700,
            is_reply=True,
            classified="INFORMATIONAL",
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    signal = report["signals"][0]
    event = report["signals"][0]["management_events"][0]

    assert event["classified_action"] == "MOVE_SL_TO_PRICE"
    assert event["price"] == 4061.0
    assert event["modality"] == "direct"
    assert event["semantic_source"] == "deterministic_parser"
    assert event["classifier_action"] == "INFORMATIONAL"
    assert signal["effective_sl"] == 4061.0
    assert signal["level_timeline"][-1]["tps"] == [4098.0, 4096.0]
    assert signal["level_timeline"][-1]["sl"] == 4061.0
    assert signal["level_timeline"][-1]["raw_sl"] == 4061.0
    assert signal["level_timeline"][-1]["source_kind"] == (
        "management_sl_move"
    )


def test_short_numeric_move_sl_is_expanded_in_canonical_level_timeline():
    events = [
        _raw(
            "canal2",
            702,
            "Sell Gold Now\n4040 - 4044\nTargets\n4038\n4035\nSL 4048",
        ),
        _raw(
            "canal2",
            703,
            "Move SL to 45",
            reply_to_msg_id=702,
            is_reply=True,
        ),
    ]

    signal = provider_signal_catalog.build_catalog_report(events, [])[
        "signals"
    ][0]

    assert signal["effective_sl"] == 4045.0
    assert signal["level_timeline"][-1]["sl"] == 4045.0
    assert signal["level_timeline"][-1]["raw_sl"] == 45.0
    assert signal["level_timeline"][-1]["source_kind"] == (
        "management_sl_move"
    )
    assert any(
        issue["field"] == "sl"
        and issue["decision"] == "expanded_short_price"
        and issue["raw"] == 45.0
        and issue["canonical"] == 4045.0
        for issue in signal["canonicalization_issues"]
    )


def test_optional_close_preserves_choice_instead_of_forcing_execution():
    events = [
        _raw(
            "canal2",
            710,
            "Buy Gold Now\n4030 - 4032\nTargets\n4035\n4038\nSL 4025",
        ),
        _raw(
            "canal2",
            711,
            "Close TP7 when happy",
            reply_to_msg_id=710,
            is_reply=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    event = report["signals"][0]["management_events"][0]

    assert event["classified_action"] == "CLOSE_AT_TP"
    assert event["target_tp_index"] == 7
    assert event["modality"] == "optional"
    assert event["execution_options"] == [
        {"action": "CLOSE_AT_TP", "target_tp_index": 7},
        {"action": "HOLD"},
    ]


def test_fused_tp_sl_reply_is_preserved_as_level_update():
    events = [
        _raw(
            "canal2",
            720,
            "Buy Gold Now\n4030 - 4032\nTargets\n4035\n4038\nSL 4025",
        ),
        _raw(
            "canal2",
            721,
            "TP1 4035\nSL 4025",
            reply_to_msg_id=720,
            is_reply=True,
            classified="INFORMATIONAL",
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    event = report["signals"][0]["management_events"][0]

    assert event["classified_action"] == "LEVEL_UPDATE"
    assert event["levels"] == {"tps": [4035.0], "sl": 4025.0}
    assert event["modality"] == "direct"
    assert event["semantic_source"] == "deterministic_parser"


def test_daily_summary_is_retained_but_not_counted_as_formal_signal():
    events = [
        _raw(
            "canal2",
            730,
            "Monday Summary\n9 Signals Sent\n7 Wins\n2 Stop Loss\n"
            "Pips gained + 600",
        )
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])

    assert report["summary"]["records"] == 1
    assert report["summary"]["provider_signals"] == 0
    assert report["signals"][0]["record_type"] == "daily_summary"
    assert report["signals"][0]["semantic_status"] == "classified"


def test_close_or_risk_free_keeps_both_provider_options():
    events = [
        _raw(
            "canal2",
            740,
            "Sell Gold Now\n4100 - 4105\nTargets\n4098\n4096\nSL 4108",
        ),
        _raw(
            "canal2",
            741,
            "+25 pips. Close overall profit or make your trade risk free",
            reply_to_msg_id=740,
            is_reply=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    event = report["signals"][0]["management_events"][0]

    assert event["classified_action"] == "MANAGEMENT_CHOICE"
    assert event["modality"] == "optional"
    assert event["execution_options"] == [
        {"action": "CLOSE_ALL"},
        {"action": "MOVE_SL_TO_BE"},
    ]


def test_provider_be_price_is_retained_as_entry_reference_not_sl_action():
    events = [
        _raw(
            "canal1",
            20887,
            "BUY GOLD NOW 4035.00\nTP1 4040\nTP2 4045\nSL 4015",
        ),
        _raw(
            "canal1",
            20888,
            "Move SL to BE at 4030.00",
            reply_to_msg_id=20887,
            is_reply=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    event = report["signals"][0]["management_events"][0]

    assert event["classified_action"] == "MOVE_SL_TO_BE"
    assert event.get("price") is None
    assert event["provider_stated_be_price"] == 4030.0
    assert event["execution_options"] == [{
        "action": "MOVE_SL_TO_BE",
        "provider_stated_be_price": 4030.0,
    }]


def test_duplicate_management_versions_collapse_but_restoration_is_retained():
    events = [
        _raw(
            "canal2",
            750,
            "Buy Gold Now\n4030 - 4032\nTargets\n4035\n4038\nSL 4025",
        ),
        _raw(
            "canal2",
            751,
            "Move SL to 4030",
            reply_to_msg_id=750,
            is_reply=True,
            update_kind="poll_new",
        ),
        _raw(
            "canal2",
            751,
            "Move SL to 4030",
            reply_to_msg_id=750,
            is_reply=True,
            update_kind="new",
            edit_date_utc="2026-07-08T10:00:01+00:00",
            is_edit=True,
        ),
        _raw(
            "canal2",
            751,
            "Move SL to 4031",
            reply_to_msg_id=750,
            is_reply=True,
            update_kind="edit",
            edit_date_utc="2026-07-08T10:00:02+00:00",
            is_edit=True,
        ),
        _raw(
            "canal2",
            751,
            "Move SL to 4030",
            reply_to_msg_id=750,
            is_reply=True,
            update_kind="edit",
            edit_date_utc="2026-07-08T10:00:03+00:00",
            is_edit=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    timeline = report["signals"][0]["management_events"]

    assert [row["price"] for row in timeline] == [4030.0, 4031.0, 4030.0]
    assert timeline[0]["raw_versions"] == 2


def test_management_timeline_uses_observed_time_then_message_id():
    events = [
        _raw(
            "canal2",
            900,
            "Buy Gold Now\n4030 - 4032\nTargets\n4035\nSL 4025",
            ts="2026-07-08T10:00:00+00:00",
        ),
        _raw(
            "canal2",
            904,
            "Move SL to 4034",
            reply_to_msg_id=900,
            is_reply=True,
            ts="2026-07-08T10:03:00+00:00",
            date_utc="2026-07-08T07:00:00+00:00",
        ),
        _raw(
            "canal2",
            903,
            "Move SL to 4033",
            reply_to_msg_id=900,
            is_reply=True,
            ts="2026-07-08T10:03:00+00:00",
            date_utc="2026-07-08T13:00:00+00:00",
        ),
        _raw(
            "canal2",
            902,
            "Move SL to 4032",
            reply_to_msg_id=900,
            is_reply=True,
            ts="2026-07-08T10:02:00+00:00",
            date_utc="2026-07-08T08:00:00+00:00",
        ),
        _raw(
            "canal2",
            901,
            "Move SL to 4031",
            reply_to_msg_id=900,
            is_reply=True,
            ts="2026-07-08T12:01:00+02:00",
            date_utc="2026-07-08T12:00:00+00:00",
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    timeline = report["signals"][0]["management_events"]

    assert [row["message_id"] for row in timeline] == [901, 902, 903, 904]
    assert [row["price"] for row in timeline] == [4031.0, 4032.0, 4033.0, 4034.0]


def test_catalog_records_are_ordered_by_first_observed_time():
    observed_first = _raw(
        "canal2",
        910,
        "Buy Gold Now\n4030 - 4032\nTargets\n4035\nSL 4025",
        ts="2026-07-08T10:00:00+00:00",
        date_utc="2026-07-08T12:00:00+00:00",
    )
    telegram_first = _raw(
        "canal2",
        911,
        "Sell Gold Now\n4040 - 4042\nTargets\n4038\nSL 4045",
        ts="2026-07-08T10:01:00+00:00",
        date_utc="2026-07-08T09:00:00+00:00",
    )

    report = provider_signal_catalog.build_catalog_report(
        [telegram_first, observed_first],
        [],
    )

    assert [row["provider_signal_id"] for row in report["signals"]] == [
        "canal2_910",
        "canal2_911",
    ]


@pytest.mark.parametrize(
    ("text", "action", "modality"),
    [
        ("Target 4 ✅", "TP_HIT_ANNOUNCEMENT", "informational"),
        ("TP5 was KISSED", "TP_HIT_ANNOUNCEMENT", "informational"),
        ("TP2 HOT", "TP_HIT_ANNOUNCEMENT", "informational"),
        ("SL HIT", "SL_HIT_ANNOUNCEMENT", "informational"),
        ("All entries in profit", "PROGRESS_UPDATE", "informational"),
        ("Take partials when happy", "CLOSE_PARTIAL", "optional"),
        ("Zone failed", "ZONE_INVALIDATED", "informational"),
        ("TPs corrected", "LEVEL_CORRECTION", "informational"),
    ],
)
def test_repeated_provider_vocabulary_has_deterministic_semantics(
    text,
    action,
    modality,
):
    events = [
        _raw(
            "canal2",
            760,
            "Buy Gold Now\n4030 - 4032\nTargets\n4035\n4038\nSL 4025",
        ),
        _raw(
            "canal2",
            761,
            text,
            reply_to_msg_id=760,
            is_reply=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    event = report["signals"][0]["management_events"][0]

    assert event["classified_action"] == action
    assert event["modality"] == modality
    assert event["semantic_source"] == "deterministic_parser"


@pytest.mark.parametrize(
    ("text", "levels"),
    [
        ("TP1 4134", {"tps": [4134.0], "sl": None}),
        ("SL 4095", {"tps": [], "sl": 4095.0}),
    ],
)
def test_single_level_replies_are_canonical_level_updates(text, levels):
    events = [
        _raw(
            "canal2",
            770,
            "Buy Gold Now\n4030 - 4032\nTargets\n4035\n4038\nSL 4025",
        ),
        _raw(
            "canal2",
            771,
            text,
            reply_to_msg_id=770,
            is_reply=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    event = report["signals"][0]["management_events"][0]

    assert event["classified_action"] == "LEVEL_UPDATE"
    assert event["levels"] == levels
    assert event["modality"] == "direct"


def test_media_only_reply_is_explicitly_non_actionable():
    events = [
        _raw(
            "canal1",
            780,
            "BUY GOLD NOW 4100\nTP1 4105\nSL 4095",
        ),
        _raw(
            "canal1",
            781,
            "",
            reply_to_msg_id=780,
            is_reply=True,
            has_photo=True,
        ),
    ]

    report = provider_signal_catalog.build_catalog_report(events, [])
    event = report["signals"][0]["management_events"][0]

    assert event["classified_action"] == "MEDIA_COMPANION"
    assert event["modality"] == "informational"
    assert event["semantic_source"] == "deterministic_parser"


def test_weekly_summary_uses_summary_record_type_not_management_signal():
    report = provider_signal_catalog.build_catalog_report([
        _raw(
            "canal2",
            790,
            "Weekly Summary 08/06/26 - 12/06/26\n"
            "33 Trades Sent\n30 Winning Trades\n3 Stop Loss\n"
            "Pips gained +3730",
        )
    ], [])

    assert report["summary"]["provider_signals"] == 0
    assert report["signals"][0]["record_type"] == "daily_summary"


def test_canonical_catalog_has_no_volatile_generation_timestamp():
    events = [_raw(
        "canal2",
        800,
        "Buy Gold Now\n4030 - 4032\nTargets\n4035\nSL 4025",
    )]

    first = provider_signal_catalog.build_catalog_report(events, [])
    second = provider_signal_catalog.build_catalog_report(events, [])

    assert "generated_at" not in first
    assert first == second


def test_versioned_catalog_uses_current_schema_and_public_entry_contract():
    catalog_path = (
        Path(provider_signal_catalog.__file__).parent
        / "data"
        / "provider_signal_catalog.json"
    )
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    expected_contract_fields = {
        "status",
        "trigger_observed_utc",
        "trigger_telegram_utc",
        "trigger_message_id",
        "trigger_kind",
        "direction",
        "direction_source",
        "blockers",
    }

    def private_paths(value, path="record"):
        found = []
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if str(key).startswith("_"):
                    found.append(child_path)
                found.extend(private_paths(child, child_path))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                found.extend(private_paths(child, f"{path}[{index}]"))
        return found

    assert provider_signal_catalog.SCHEMA_VERSION == 7
    assert catalog["schema_version"] == provider_signal_catalog.SCHEMA_VERSION
    assert catalog["signals"]
    for record in catalog["signals"]:
        assert record["schema_version"] == provider_signal_catalog.SCHEMA_VERSION
        assert set(record["entry_contract"]) == expected_contract_fields
        assert private_paths(record) == []


def test_default_corpus_uses_hybrid_canal1_identity_links():
    report = provider_signal_catalog.build_catalog_report(
        provider_signal_catalog.load_jsonl(provider_signal_catalog.DEFAULT_EVENTS),
        provider_signal_catalog.load_jsonl(provider_signal_catalog.DEFAULT_REPLAY),
    )

    def record_for(message_id):
        return next(
            record
            for record in report["signals"]
            if message_id in record["source_message_ids"]
        )

    for root_id, companion_id, observed_gap_ms in (
        (20303, 20304, 719878),
        (20380, 20382, 179696),
        (20611, 20612, 224403),
    ):
        record = record_for(companion_id)
        assert record["provider_signal_id"] == f"canal1_{root_id}"
        assert {
            "source": "processing_fallback",
            "root_message_id": root_id,
            "companion_message_id": companion_id,
            "observed_gap_ms": observed_gap_ms,
        } in record["identity_links"]

    for root_id, companion_id in ((20689, 20690), (20701, 20702)):
        record = record_for(companion_id)
        assert record["provider_signal_id"] == f"canal1_{root_id}"
        assert next(
            link["source"]
            for link in record["identity_links"]
            if link["companion_message_id"] == companion_id
        ) == "raw_nearest"


def test_versioned_catalog_exactly_matches_default_corpus_rebuild():
    tracked_data = runtime_paths.legacy_data_dir()
    versioned = json.loads(
        (tracked_data / "provider_signal_catalog.json").read_text(
            encoding="utf-8"
        )
    )
    rebuilt = provider_signal_catalog.build_catalog_report(
        provider_signal_catalog.load_jsonl(
            tracked_data / "trade_events.jsonl"
        ),
        provider_signal_catalog.load_jsonl(
            tracked_data / "replay_trades.jsonl"
        ),
    )

    assert versioned == rebuilt


def test_canonical_timeline_keeps_valid_reply_over_later_malformed_edits():
    events = [
        _raw(
            "canal2",
            3331,
            "Buy Gold Now",
            ts="2026-07-16T12:57:55.720+00:00",
            date_utc="2026-07-16T12:57:51+00:00",
        ),
        _raw(
            "canal2",
            3331,
            "Buy Gold Now\n\n3994 - 4988",
            ts="2026-07-16T12:58:04.223+00:00",
            update_kind="edit",
            is_edit=True,
            edit_date_utc="2026-07-16T12:58:03+00:00",
        ),
        _raw(
            "canal2",
            3332,
            "TP1 3997\nSL 3986",
            reply_to_msg_id=3331,
            is_reply=True,
            ts="2026-07-16T12:58:16.876+00:00",
            date_utc="2026-07-16T12:58:16+00:00",
        ),
        _raw(
            "canal2",
            3331,
            "Buy Gold Now\n\n3994 - 4988\n\n"
            "TP1 3997\nTP2 3999\nTP3 4001\nTP4 4003\n"
            "TP5 4005\nTP6 4007\nSL 4034",
            ts="2026-07-16T12:58:46.592+00:00",
            update_kind="edit",
            is_edit=True,
            edit_date_utc="2026-07-16T12:58:45+00:00",
        ),
        _raw(
            "canal2",
            3331,
            "Buy Gold Now\n\n3994 - 4988\n\n"
            "TP1 3997\nTP2 3999\nTP3 4001\nTP4 4003\n"
            "TP5 4005\nTP6 4007\nSL 4086",
            ts="2026-07-16T13:02:44.320+00:00",
            update_kind="edit",
            is_edit=True,
            edit_date_utc="2026-07-16T13:02:43+00:00",
        ),
    ]

    signal = provider_signal_catalog.build_catalog_report(events, [
        {"sig_id": "canal2_3331"},
    ])["signals"][0]

    assert signal["effective_range"] == [3988.0, 3994.0]
    assert signal["effective_tps"] == [
        3997.0, 3999.0, 4001.0, 4003.0, 4005.0, 4007.0,
    ]
    assert signal["effective_sl"] == 3986.0
    assert signal["semantic_status"] == "complete"
    assert any(
        row.get("source_kind") == "management_level_update"
        and row.get("source_message_id") == 3332
        and row.get("sl") == 3986.0
        for row in signal["level_timeline"]
    )
    assert signal["level_timeline"][-1]["sl"] == 3986.0
    assert any(
        issue.get("field") == "sl"
        and issue.get("raw") in {4034.0, 4086.0}
        and issue.get("decision") == "rejected_keep_previous"
        for issue in signal["canonicalization_issues"]
    )


def test_canonical_timeline_repairs_high_confidence_sl_prefix_typo():
    events = [
        _raw(
            "canal2",
            3340,
            "High Risk\nBuy Gold Now\n3993 - 3987\n"
            "TP1 3996\nTP2 3999\nSL 4083",
        ),
    ]

    signal = provider_signal_catalog.build_catalog_report(events, [
        {"sig_id": "canal2_3340"},
    ])["signals"][0]

    assert signal["effective_range"] == [3987.0, 3993.0]
    assert signal["effective_sl"] == 3983.0
    assert any(
        issue.get("field") == "sl"
        and issue.get("raw") == 4083.0
        and issue.get("canonical") == 3983.0
        and issue.get("decision") == "repaired_prefix_typo"
        for issue in signal["canonicalization_issues"]
    )


def test_complete_provider_correction_replaces_the_previous_price_generation():
    events = [
        _raw(
            "canal2",
            1170,
            "Gold Sell Zone\n\n4059 - 4064\n\nTargets\n"
            "4057\n4055\n4047\n\nSL 4067",
            ts="2026-08-06T07:00:51.164+00:00",
        ),
        _raw(
            "canal2",
            1170,
            "Gold Sell Zone\n\n4259 - 4264\n\nTargets\n"
            "4257\n4255\n4247\n\nSL 4267",
            ts="2026-08-06T07:07:25.266+00:00",
            update_kind="edit",
            is_edit=True,
            edit_date_utc="2026-08-06T07:07:24+00:00",
        ),
    ]

    signal = provider_signal_catalog.build_catalog_report(events, [])["signals"][0]

    assert signal["effective_range"] == [4259.0, 4264.0]
    assert signal["effective_tps"] == [4257.0, 4255.0, 4247.0]
    assert signal["effective_sl"] == 4267.0


def test_market_context_prefix_repair_is_canonical_but_inference_is_not():
    events = [
        _raw(
            "canal2",
            1167,
            "Gold Sell Zone\n\n4062 - 4067\n\nTargets\n"
            "4060\n4058\n4047\n\nSL 4070",
            ts="2026-08-06T06:17:15.484+00:00",
        ),
        {
            "ts": "2026-08-06T06:17:15.500+00:00",
            "sig": "canal2_1167",
            "ev": "entry_levels_interpreted",
            "channel": "canal2",
            "reference_price": 4259.8,
            "original": {
                "direction": "SELL",
                "zones": [[4062.0, 4067.0]],
                "tps": [4060.0, 4058.0, 4047.0],
                "sl": 4070.0,
            },
            "interpreted": {
                "direction": "SELL",
                "zones": [[4262.0, 4267.0]],
                "tps": [4260.0, 4258.0, 4247.0],
                "sl": 4270.0,
            },
            "corrections": [{
                "field": "plan",
                "kind": "market_context_shift",
                "offset": 200.0,
                "reference_price": 4259.8,
                "shifted_fields": ["range", "tps", "sl"],
            }],
            "provisional": True,
        },
    ]

    signal = provider_signal_catalog.build_catalog_report(events, [])["signals"][0]

    assert signal["effective_range"] == [4262.0, 4267.0]
    assert signal["effective_tps"] == [4260.0, 4258.0, 4247.0]
    assert signal["effective_sl"] == 4270.0
    assert any(
        issue.get("decision") == "market_context_prefix_repair"
        and issue.get("source_message_id") == 1167
        for issue in signal["canonicalization_issues"]
    )


def test_runtime_inferred_levels_remain_separate_provider_evidence():
    events = [
        _raw(
            "canal2",
            800,
            "Buy Gold Now\n4000 - 4002\nTargets\n4005\n4008",
        ),
        {
            "ts": "2026-07-08T10:00:21+00:00",
            "sig": "canal2_800",
            "ev": "entry_levels_interpreted",
            "interpreted": {
                "direction": "BUY",
                "range": [4000.0, 4002.0],
                "tps": [4005.0, 4008.0],
                "sl": 3996.0,
            },
            "corrections": [{
                "field": "sl",
                "kind": "inferred",
                "original": None,
                "corrected": 3996.0,
                "reason": "missing_sl",
            }],
            "provisional": True,
        },
    ]

    signal = provider_signal_catalog.build_catalog_report(events, [])[
        "signals"
    ][0]

    assert signal["effective_sl"] is None
    assert "missing_sl" in signal["semantic_gaps"]
    assert signal["runtime_level_timeline"] == [{
        "observed_ts_utc": "2026-07-08T10:00:21+00:00",
        "telegram_ts_utc": None,
        "range": [4000.0, 4002.0],
        "tps": [4005.0, 4008.0],
        "sl": 3996.0,
        "provisional": True,
        "corrections": [{
            "field": "sl",
            "kind": "inferred",
            "original": None,
            "corrected": 3996.0,
            "reason": "missing_sl",
        }],
        "source_kind": "runtime_entry_interpreter",
        "source_event": "entry_levels_interpreted",
    }]
