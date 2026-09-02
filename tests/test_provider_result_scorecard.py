import base64
import hashlib
import json

import pytest

from provider_result_scorecard import (
    ProviderMediaEvidenceError,
    build_scorecard,
    load_hash_bound_media_summaries,
    parse_provider_summary,
)


def test_daily_summary_uses_previous_named_weekday_and_keeps_uncertainty():
    claim = parse_provider_summary(
        "Friday Summary\n7 Signals Sent\n6 Wins\n0 Loss\n1 B/E\n"
        "Pips gained +835",
        observed_at_utc="2026-08-01T05:23:40+00:00",
    )

    assert claim["period_kind"] == "daily"
    assert claim["period_start"] == "2026-07-31"
    assert claim["period_end"] == "2026-07-31"
    assert claim["signals_sent"] == 7
    assert claim["wins"] == 6
    assert claim["losses"] == 0
    assert claim["breakeven"] == 1
    assert claim["pips_gained"] == 835
    assert claim["arithmetic_consistent"] is True
    assert "provider_timezone_unverified" in claim["blockers"]


def test_weekly_summary_parses_explicit_period_and_metrics():
    claim = parse_provider_summary(
        "Weekly Summary\n10/08/26 - 14/08/26\n"
        "40 Signals Sent\n38 Wins\n1 Loss\n1 B/E\n"
        "Pips gained +4000\nWin rate 95%",
        observed_at_utc="2026-08-14T19:00:00+00:00",
    )

    assert claim["period_kind"] == "weekly"
    assert claim["period_start"] == "2026-08-10"
    assert claim["period_end"] == "2026-08-14"
    assert claim["pips_gained"] == 4000
    assert claim["win_rate_percent"] == 95.0
    assert claim["blockers"] == []
    assert claim["calibration_ready"] is True


def test_summary_parser_accepts_provider_pips_without_gained_word():
    claim = parse_provider_summary(
        "Monday Summary\n6 Signals Sent\n5 Wins\n1 Loss\nPips +550",
        observed_at_utc="2026-08-31T18:47:32+00:00",
    )

    assert claim["period_start"] == "2026-08-31"
    assert claim["pips_gained"] == 550
    assert claim["calibration_ready"] is False
    assert claim["blockers"] == ["provider_timezone_unverified"]


def test_summary_parser_infers_only_arithmetically_forced_zero_outcomes():
    claim = parse_provider_summary(
        "Wednesday Summary\n9 Signals Sent\n8 Wins\n1 B/E\nPips +960",
        observed_at_utc="2026-08-26T21:14:40+00:00",
    )

    assert claim["losses"] == 0
    assert claim["breakeven"] == 1
    assert claim["inferred_metrics"] == ["losses"]
    assert "summary_metrics_incomplete" not in claim["blockers"]


def test_hash_bound_media_summary_is_parsed_and_keeps_source_evidence(tmp_path):
    payload = b"provider-summary-image"
    digest = hashlib.sha256(payload).hexdigest()
    media_path = tmp_path / "telegram_media.jsonl"
    media_path.write_text(
        json.dumps({
            "schema_version": 1,
            "captured_at_utc": "2026-08-31T18:47:32.872+00:00",
            "channel": "canal2",
            "message_id": 2268,
            "message_revision_id": "msgrev_verified",
            "media_type": "photo",
            "mime_type": "image/jpeg",
            "sha256": digest,
            "size_bytes": len(payload),
            "payload_encoding": "base64",
            "payload_base64": base64.b64encode(payload).decode("ascii"),
        }) + "\n",
        encoding="utf-8",
    )
    annotations_path = tmp_path / "annotations.json"
    annotations_path.write_text(
        json.dumps({
            "schema_version": 1,
            "provider": "Gold Signals",
            "channel": "canal2",
            "annotations": [{
                "message_id": 2268,
                "message_revision_id": "msgrev_verified",
                "media_sha256": digest,
                "transcription_method": "visual_transcription",
                "transcribed_text": (
                    "Monday Summary\n6 Signals Sent\n5 Wins\n1 Loss\n"
                    "Pips +550"
                ),
            }],
        }),
        encoding="utf-8",
    )

    records = load_hash_bound_media_summaries(
        annotations_path,
        media_path,
    )
    scorecard = build_scorecard({"signals": []}, supplemental_records=records)

    row = scorecard["summaries"][0]
    assert row["provider_signal_id"].startswith("canal2_media_summary_2268_")
    assert row["claim"]["period_start"] == "2026-08-31"
    assert row["claim"]["pips_gained"] == 550
    assert row["media_evidence"]["sha256"] == digest
    assert row["media_evidence"]["payload_sha256_verified"] is True


def test_media_summary_rejects_tampered_payload(tmp_path):
    expected_payload = b"expected-image"
    digest = hashlib.sha256(expected_payload).hexdigest()
    media_path = tmp_path / "telegram_media.jsonl"
    media_path.write_text(
        json.dumps({
            "schema_version": 1,
            "captured_at_utc": "2026-08-31T18:47:32.872+00:00",
            "channel": "canal2",
            "message_id": 2268,
            "message_revision_id": "msgrev_verified",
            "sha256": digest,
            "size_bytes": len(expected_payload),
            "payload_encoding": "base64",
            "payload_base64": base64.b64encode(b"tampered-image").decode("ascii"),
        }) + "\n",
        encoding="utf-8",
    )
    annotations_path = tmp_path / "annotations.json"
    annotations_path.write_text(
        json.dumps({
            "schema_version": 1,
            "provider": "Gold Signals",
            "channel": "canal2",
            "annotations": [{
                "message_id": 2268,
                "message_revision_id": "msgrev_verified",
                "media_sha256": digest,
                "transcription_method": "visual_transcription",
                "transcribed_text": "Monday Summary\nPips +550",
            }],
        }),
        encoding="utf-8",
    )

    with pytest.raises(
        ProviderMediaEvidenceError,
        match="payload hash mismatch",
    ):
        load_hash_bound_media_summaries(annotations_path, media_path)


def test_scorecard_uses_latest_revision_and_links_formal_signals():
    catalog = {
        "signals": [
            {
                "provider_signal_id": "canal2_summary",
                "channel": "canal2",
                "record_type": "daily_summary",
                "first_observed_utc": "2026-08-03T19:40:09+00:00",
                "revisions": [
                    {
                        "telegram_ts_utc": "2026-08-03T19:40:07+00:00",
                        "text": "Monday Summary\n14 Signals Sent\n12 Wins\n"
                        "2 Loss\nPips gained +750",
                    },
                    {
                        "telegram_ts_utc": "2026-08-03T20:06:13+00:00",
                        "text": "Monday Summary\n15 Trades Sent\n13 Wins\n"
                        "2 Loss\nPips gained +800",
                    },
                ],
            },
            {
                "provider_signal_id": "canal2_trade_1",
                "channel": "canal2",
                "record_type": "formal_signal",
                "first_observed_utc": "2026-08-03T10:00:00+00:00",
                "entry_contract": {
                    "trigger_telegram_utc": "2026-08-03T09:59:59+00:00",
                },
            },
            {
                "provider_signal_id": "canal2_trade_2",
                "channel": "canal2",
                "record_type": "formal_signal",
                "first_observed_utc": "2026-08-04T10:00:00+00:00",
            },
        ]
    }

    scorecard = build_scorecard(catalog)

    row = scorecard["summaries"][0]
    assert row["claim"]["signals_sent"] == 15
    assert row["claim"]["pips_gained"] == 800
    assert row["revision_count"] == 2
    assert row["observed_formal_signals"] == 1
    assert row["observed_signal_ids"] == ["canal2_trade_1"]
    assert row["signal_count_delta"] == -14
    assert row["claim"]["calibration_ready"] is False
    assert "provider_signal_count_mismatch" in row["claim"]["blockers"]


def test_inconsistent_claim_remains_blocked():
    claim = parse_provider_summary(
        "Weekly Summary\n10/08/26 - 14/08/26\n"
        "40 Signals Sent\n38 Wins\n3 Losses\nPips gained +4000",
        observed_at_utc="2026-08-14T19:00:00+00:00",
    )

    assert claim["arithmetic_consistent"] is False
    assert claim["calibration_ready"] is False
    assert "summary_arithmetic_inconsistent" in claim["blockers"]


def test_claimed_win_rate_must_match_wins_over_signals():
    claim = parse_provider_summary(
        "Weekly Summary\n10/08/26 - 14/08/26\n"
        "2 Signals Sent\n2 Wins\n0 Loss\nPips gained +200\n"
        "Win rate 10%",
        observed_at_utc="2026-08-14T19:00:00+00:00",
    )

    assert claim["calibration_ready"] is False
    assert "summary_win_rate_inconsistent" in claim["blockers"]


def test_integer_win_rate_allows_normal_rounding():
    claim = parse_provider_summary(
        "Weekly Summary\n17/08/26 - 21/08/26\n"
        "37 Signals Sent\n34 Wins\n3 Loss\n"
        "Potential Pips gained 4810\nWin Rate 92%",
        observed_at_utc="2026-08-21T16:57:46+00:00",
    )

    assert claim["win_rate_percent"] == 92
    assert "summary_win_rate_inconsistent" not in claim["blockers"]


def test_scorecard_exposes_weekly_accounting_discrepancies():
    def summary(signal_id, observed, text):
        return {
            "provider_signal_id": signal_id,
            "channel": "canal2",
            "record_type": "daily_summary",
            "first_observed_utc": observed,
            "revisions": [{
                "telegram_ts_utc": observed,
                "text": text,
            }],
        }

    catalog = {"signals": [
        summary(
            "mon",
            "2026-08-24T20:46:37+00:00",
            "Monday Summary\n9 Signals Sent\n8 Wins\n1 Loss\nPips +1100",
        ),
        summary(
            "tue",
            "2026-08-25T18:35:50+00:00",
            "Tuesday Summary\n10 Signals Sent\n8 Wins\n1 Loss\n1 B/E\n"
            "Pips +900",
        ),
        summary(
            "wed",
            "2026-08-26T21:14:40+00:00",
            "Wednesday Summary\n9 Signals Sent\n8 Wins\n0 Loss\n1 B/E\n"
            "Pips +960",
        ),
        summary(
            "thu",
            "2026-08-27T17:55:42+00:00",
            "Thursday Summary\n8 Signals Sent\n7 Wins\n1 Loss\nPips +1240",
        ),
        summary(
            "fri",
            "2026-08-28T18:07:40+00:00",
            "Friday Summary\n10 Signals Sent\n9 Wins\n1 Loss\nPips +1450",
        ),
        summary(
            "week",
            "2026-08-28T18:08:29+00:00",
            "Weekly Summary\n24/08/26 - 28/08/26\n"
            "46 Signals Sent\n42 Wins\n4 Loss\nPips +5200",
        ),
    ]}

    consistency = build_scorecard(catalog)["period_consistency"][0]

    assert consistency["complete_daily_coverage"] is True
    assert consistency["daily_totals"] == {
        "signals_sent": 46,
        "wins": 40,
        "losses": 4,
        "breakeven": 2,
        "pips_gained": 5650,
    }
    assert consistency["weekly_totals"]["pips_gained"] == 5200
    assert consistency["pips_daily_minus_weekly"] == 450
    assert consistency["outcome_accounting"] == "breakeven_counted_as_win"
    assert consistency["blockers"] == ["weekly_pips_differ_from_daily"]


def test_weekly_consistency_marks_omitted_daily_metric_without_crashing():
    def summary(signal_id, text):
        return {
            "provider_signal_id": signal_id,
            "channel": "canal2",
            "record_type": "daily_summary",
            "first_observed_utc": "2026-08-17T18:21:47+00:00",
            "revisions": [{
                "telegram_ts_utc": "2026-08-17T18:21:47+00:00",
                "text": text,
            }],
        }

    card = build_scorecard({"signals": [
        summary(
            "daily",
            "Monday Summary\n7 Signals Sent\n5 Wins\nPips +900",
        ),
        summary(
            "weekly",
            "Weekly Summary\n17/08/26 - 17/08/26\n"
            "7 Signals Sent\n5 Wins\n2 Loss\n0 B/E\nPips +900",
        ),
    ]})

    consistency = card["period_consistency"][0]
    assert consistency["complete_daily_coverage"] is True
    assert consistency["daily_totals"] == {}
    assert consistency["blockers"] == ["daily_metrics_incomplete"]


def test_duplicate_formal_signal_identity_blocks_scorecard_calibration():
    summary = {
        "provider_signal_id": "canal2_summary",
        "channel": "canal2",
        "record_type": "daily_summary",
        "first_observed_utc": "2026-08-03T19:40:09+00:00",
        "revisions": [{
            "telegram_ts_utc": "2026-08-03T19:40:07+00:00",
            "text": "Monday Summary\n1 Signal Sent\n1 Win\n"
            "0 Loss\nPips gained +100\nWin rate 100%",
        }],
    }
    duplicate = {
        "provider_signal_id": "canal2_trade_1",
        "channel": "canal2",
        "record_type": "formal_signal",
        "first_observed_utc": "2026-08-03T10:00:00+00:00",
    }

    row = build_scorecard({
        "signals": [summary, duplicate, dict(duplicate)],
    })["summaries"][0]

    assert row["observed_formal_signals"] == 1
    assert row["claim"]["calibration_ready"] is False
    assert "provider_signal_identity_duplicate" in row["claim"]["blockers"]
