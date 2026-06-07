import ledger_report
from types import SimpleNamespace


def _closed_row(sig_id, close_day, mt5, journal, **overrides):
    row = {
        "sig_id": sig_id,
        "channel": "canal2",
        "direction": "SELL",
        "signal_dt_utc": f"{close_day}T09:00:00+00:00",
        "close_dt_utc": f"{close_day}T10:00:00+00:00",
        "status": "closed",
        "pnl_real_mt5": mt5,
        "pnl_journal": journal,
        "pnl_discrepancy": None if journal is None else round(mt5 - journal, 2),
        "reconciled_ok": journal == mt5,
        "pnl_mt5_complete": True,
        "journal_has_signal_closed": journal is not None,
        "analysis_excluded": False,
        "flags": [],
    }
    row.update(overrides)
    return row


def test_metric_rows_exclude_operational_incidents_by_default():
    clean = {"sig_id": "canal2_1", "analysis_excluded": False}
    incident = {
        "sig_id": "canal2_2",
        "analysis_excluded": True,
        "analysis_exclusions": [{"code": "mt5_client_autotrading_disabled"}],
    }

    metric_rows, excluded = ledger_report.metric_universe([clean, incident])

    assert metric_rows == [clean]
    assert excluded == [incident]


def test_metric_rows_can_include_operational_incidents_explicitly():
    clean = {"sig_id": "canal2_1", "analysis_excluded": False}
    incident = {"sig_id": "canal2_2", "analysis_excluded": True}

    metric_rows, excluded = ledger_report.metric_universe(
        [clean, incident],
        include_excluded=True,
    )

    assert metric_rows == [clean, incident]
    assert excluded == [incident]


def test_weekly_summary_uses_mt5_pnl_and_surfaces_journal_gaps():
    rows = [
        _closed_row("canal2_13280", "2026-06-01", 8.02, 8.02),
        _closed_row("canal2_13281", "2026-06-02", -48.86, -48.86),
        _closed_row("canal2_13288", "2026-06-03", 1.54, 0.0,
                    reconciled_ok=False,
                    flags=["PNL_DISCREPANCY_+1.54"]),
        _closed_row("canal2_13293", "2026-06-03", -35.48, None,
                    journal_has_signal_closed=False,
                    reconciled_ok=None,
                    flags=["HUERFANO_journal_sin_signal_closed"]),
        _closed_row("canal2_13300", "2026-06-03", 48.48, 48.48),
        _closed_row("canal2_13310", "2026-06-04", 14.96, 14.96),
        _closed_row("canal2_13426", "2026-06-05", -77.71, -77.71),
        _closed_row("canal2_disabled", "2026-06-01", 0.0, None,
                    analysis_excluded=True,
                    flags=[
                        "MT5_AUTOTRADING_DISABLED_excluir_de_metricas_strategy"
                    ]),
        _closed_row("canal2_old", "2026-05-31", 999.0, 999.0,
                    flags=["OUT_OF_RANGE_SHOULD_NOT_COUNT"]),
    ]

    summary = ledger_report.summarize_closed_pnl(
        rows,
        since="2026-06-01",
        until="2026-06-06",
    )

    assert summary["total_mt5"] == -89.05
    assert summary["total_journal_nonnull"] == -55.11
    assert summary["journal_gap"] == -33.94
    assert summary["first_close_day"] == "2026-06-01"
    assert summary["last_close_day"] == "2026-06-05"
    assert summary["by_close_day"]["2026-06-05"]["mt5"] == -77.71
    assert summary["by_close_day"]["2026-06-03"]["mt5"] == 14.54
    assert [r["sig_id"] for r in summary["excluded_rows"]] == [
        "canal2_disabled"
    ]
    assert summary["flag_counts"] == {
        "MT5_AUTOTRADING_DISABLED": 1,
        "PNL_DISCREPANCY": 1,
        "HUERFANO_journal_sin_signal_closed": 1,
    }
    assert [r["sig_id"] for r in summary["discrepancies"]] == [
        "canal2_13288",
        "canal2_13293",
    ]


def test_load_ledger_from_git_ref_reads_remote_ledger_blob(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout='{"sig_id":"canal2_1","status":"closed"}\n',
            stderr="",
        )

    monkeypatch.setattr(ledger_report, "subprocess",
                        SimpleNamespace(run=fake_run), raising=False)

    rows = ledger_report.load_ledger_from_git_ref("origin/main")

    assert rows == [{"sig_id": "canal2_1", "status": "closed"}]
    args, kwargs = calls[0]
    assert args == ["git", "show", "origin/main:data/ledger.jsonl"]
    assert kwargs["cwd"] == ledger_report.Path(__file__).parent.parent


def test_main_uses_git_ref_instead_of_local_ledger(monkeypatch, capsys):
    calls = []
    row = _closed_row("canal2_1", "2026-06-05", 1.25, 1.25)

    def fake_load_git(ref):
        calls.append(ref)
        return [row]

    def fail_local_load(_path):
        raise AssertionError("main() should not read local ledger")

    monkeypatch.setattr(ledger_report, "load_ledger_from_git_ref",
                        fake_load_git)
    monkeypatch.setattr(ledger_report, "load_ledger", fail_local_load)
    monkeypatch.setattr(
        ledger_report.sys,
        "argv",
        ["ledger_report.py", "--git-ref", "origin/main",
         "--since", "2026-06-01", "--until", "2026-06-06"],
    )

    ledger_report.main()

    out = capsys.readouterr().out
    assert calls == ["origin/main"]
    assert "Ledger source: git:origin/main:data/ledger.jsonl" in out
