import ledger_report


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
