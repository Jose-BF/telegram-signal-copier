from provider_result_scorecard import build_scorecard, parse_provider_summary


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
