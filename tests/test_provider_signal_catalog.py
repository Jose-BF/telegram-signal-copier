import pytest

import provider_signal_catalog


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
        "trigger_observed_utc": "2026-07-08T11:00:02.181+00:00",
        "trigger_telegram_utc": "2026-07-08T11:00:01+00:00",
        "trigger_message_id": 200,
        "trigger_kind": "sticker",
        "direction": "BUY",
        "direction_source": "telegram_understood",
        "blockers": [],
    }
    assert not any(key.startswith("_") for key in signal)


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
            "missing_actionable_entry_trigger",
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
        "trigger_observed_utc": "2026-07-08T12:00:00+00:00",
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
    event = report["signals"][0]["management_events"][0]

    assert event["classified_action"] == "MOVE_SL_TO_PRICE"
    assert event["price"] == 4061.0
    assert event["modality"] == "direct"
    assert event["semantic_source"] == "deterministic_parser"
    assert event["classifier_action"] == "INFORMATIONAL"


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
