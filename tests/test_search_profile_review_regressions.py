"""Independent S1 review reproductions; no implementation edits or real data."""

from dataclasses import asdict
import json

import research.gold_iterative.__main__ as gold_cli
from research.dubai_iterative.contracts import StrategyGenome
from research.dubai_iterative.engine import ExecutionAssumptions
from research.dubai_iterative.market_contract import MarketProfile
from research.dubai_iterative.protection_contract import ProtectionProfile


def test_gold_tiny_profile_does_not_require_real_money_or_market_files(tmp_path, capsys):
    """Use fixture-only data or reject this combination before evaluation."""
    profile = tmp_path / "profile.json"
    execution = ExecutionAssumptions(
        protection=ProtectionProfile(.01, 2, 0, 0, 0, 0, 0),
        market=MarketProfile(0, 0, 0, .01, 1., .01),
    )
    profile.write_text(json.dumps(asdict(execution)), encoding="utf-8")
    rules = tmp_path / "rules.json"
    genome = StrategyGenome(schema_version=2, entry_mode="no_entry", leg_count=1,
        volume_weights=(.01,), target_mode="none", stop_mode="none", be_mode="none",
        provider_management_mode="ignore", entry_expiry_min=1, time_exit_min=1)
    rules.write_text(json.dumps({"schema_version": 1, "seeds": [genome.to_dict()], "mutations": {}}),
                     encoding="utf-8")
    absent = tmp_path / "real-money-must-not-be-read.json"
    argv = ["search", "--fixture", "tiny", "--execution-profile", str(profile),
        "--own-rules-config", str(rules), "--money-contract", str(absent),
        "--market-tick-cache", str(tmp_path / "no-market"),
        "--conversion-tick-cache", str(tmp_path / "no-fx"),
        "--max-generations", "1", "--max-evaluations", "1", "--population-size", "1",
        "--oracle-finalists", "1", "--bootstrap-samples", "20", "--workers", "1",
        "--output-root", str(tmp_path / "output")]
    code = gold_cli.main(argv)
    captured = capsys.readouterr().out
    assert absent.name not in captured, captured
    if code != 0:
        assert code == 2 and "fixture" in captured.lower() and "profile" in captured.lower(), captured
        assert not (tmp_path / "output").exists(), "unsupported fixture must fail before search"
    assert not absent.exists()
