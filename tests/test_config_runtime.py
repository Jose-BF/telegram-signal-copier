import config


def test_normalize_entry_mode_keeps_supported_modes():
    assert config.normalize_entry_mode("scale_out") == "scale_out"
    assert config.normalize_entry_mode("market_only") == "market_only"


def test_normalize_entry_mode_deprecates_dca_runtime_modes():
    assert config.normalize_entry_mode("intra_dca") == "scale_out"
    assert config.normalize_entry_mode("extremes") == "scale_out"


def test_normalize_entry_mode_falls_back_to_default_for_unknown_or_blank():
    assert config.normalize_entry_mode("surprise") == "scale_out"
    assert config.normalize_entry_mode("") == "scale_out"
    assert config.normalize_entry_mode(None, default="market_only") == "market_only"


def test_default_telemetry_intervals_keep_non_causal_logs_sparse():
    """Defaults reduce repetitive telemetry without touching causal events.

    Runtime heartbeat stays separate in BOT_RUNTIME_HEARTBEAT_SEC because the
    watcher uses it to detect freezes. These values only affect journal noise.
    """
    assert config.BOT_JOURNAL_HEARTBEAT_SEC == 900.0
    assert config.BOT_CONNECTION_BEAT_SEC == 7200.0
    assert config.LIVE_AUDITOR_SNAPSHOT_EVERY_S == 300.0
    assert config.POSITION_MONITOR_PL_SNAPSHOT_INTERVAL_S == 120.0
