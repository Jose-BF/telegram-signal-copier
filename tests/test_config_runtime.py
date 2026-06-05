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
