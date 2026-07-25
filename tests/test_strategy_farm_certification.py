import hashlib
import json
from copy import deepcopy
from datetime import timezone

import numpy as np
import pandas as pd

import strategy_farm
import strategy_policies


def _write_tick_day(cache_dir, day, *, symbol=None):
    cache_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([{
        "time_utc": pd.Timestamp(f"{day}T10:00:00+00:00"),
        "bid": 100.0 if symbol is None else 1.1,
        "ask": 100.2 if symbol is None else 1.2,
    }])
    parquet = cache_dir / f"{day}.parquet"
    frame.to_parquet(parquet, index=False)
    content = hashlib.sha256()
    content.update(b"time_bid_ask_sequence_sha256_v1\0")
    content.update(str(len(frame)).encode("ascii") + b"\0")
    for values in (
        frame["time_utc"].astype("int64").to_numpy(dtype="<i8", copy=False),
        frame["bid"].to_numpy(dtype="<f8", copy=False),
        frame["ask"].to_numpy(dtype="<f8", copy=False),
    ):
        content.update(np.ascontiguousarray(values).tobytes())
    content_digest = content.hexdigest()
    verified_symbol = symbol or "XAUUSD"
    contract = {
        "tick_time_contract": "mt5_server_epoch_utc_v3",
        "time_basis": "UTC",
        "source_time_basis": "mt5_server_epoch",
        "utc_offset_seconds": 10_800,
        "offset_detection_method": "fill_anchor",
        "offset_reference": {"signal_id": "canal2_380"},
        "semantic_time_valid": True,
        "anchor_validation": {
            "valid": True,
            "anchors_checked": 1 if symbol is None else 0,
            "anchors_matched": 1 if symbol is None else 0,
            "max_time_delta_ms": 0,
            "max_price_delta": 0.0,
            "errors": [],
        },
        "coverage": {
            "complete_from_utc": f"{day}T00:00:00+00:00",
            "complete_through_utc": (
                pd.Timestamp(day, tz=timezone.utc) + pd.Timedelta(days=1)
            ).isoformat(),
            "row_count": 1,
        },
        "source_verification": {
            "verified": True,
            "method": "full_day_vs_two_half_days_v1",
            "content_digest": "time_bid_ask_sequence_sha256_v1",
            "symbol": verified_symbol,
            "primary_row_count": len(frame),
            "verification_row_count": len(frame),
            "primary_content_sha256": content_digest,
            "verification_content_sha256": content_digest,
            "errors": [],
        },
        "symbol": verified_symbol,
        "parquet_sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(),
    }
    (cache_dir / f"{day}.parquet.meta.json").write_text(
        json.dumps(contract, sort_keys=True),
        encoding="utf-8",
    )


def _truncate_tick_coverage(cache_dir, day, complete_through):
    contract_path = cache_dir / f"{day}.parquet.meta.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["coverage"]["complete_through_utc"] = complete_through
    contract["coverage"]["captured_at_utc"] = complete_through
    contract_path.write_text(
        json.dumps(contract, sort_keys=True),
        encoding="utf-8",
    )


def _write_money_contract(path):
    contract = {
        "schema_version": 1,
        "account": {"currency": "EUR", "currency_digits": 2},
        "instrument": {
            "symbol": "XAUUSD",
            "trade_calc_mode": 4,
            "contract_size": 100.0,
            "tick_size": 0.01,
            "currency_profit": "USD",
        },
        "conversion": {
            "orientation": "account_base_profit_quote",
            "symbol": "EURUSD",
            "max_quote_age_ms": 5_000,
            "max_quote_interval_ms": 60_000,
        },
        "costs": {
            "commission_model": "observed_zero_intraday",
            "fee_model": "observed_zero_intraday",
            "swap_model": "intraday_only_zero",
        },
        "live_validation": {"valid": True},
    }
    path.write_text(json.dumps(contract, sort_keys=True), encoding="utf-8")


def _trade():
    return {
        "sig_id": "canal2_380",
        "channel": "canal2",
        "direction": "BUY",
        "open_dt_utc": "2026-07-06T10:00:00+00:00",
        "pnl_real_mt5": 1.0,
        "tickets": [{
            "ticket": 101,
            "open_dt_utc": "2026-07-06T10:00:00+00:00",
            "open_price": 100.0,
            "close_dt_utc": "2026-07-06T10:01:00+00:00",
            "close_price": 101.0,
            "close_reason": "tp",
            "is_closed": True,
            "volume": 0.01,
            "pnl_net": 1.0,
            "sl_history": [],
            "tp_history": [],
        }],
    }


def _candidate():
    return {
        "sig_id": "canal2_380",
        "channel": "canal2",
        "direction": "BUY",
        "strategy": "follow_actual",
        "entry_authority": "mt5_deals",
        "status": "unchanged",
        "actual_pnl": 1.0,
        "strategy_pnl": 1.0,
        "blockers": [],
        "tickets": [{
            "ticket": 101,
            "status": "unchanged_no_strategy_event",
            "leg_action": "follow_actual",
            "open_time_utc": "2026-07-06T10:00:00+00:00",
            "open_price": 100.0,
            "volume": 0.01,
            "close_reason": "tp",
            "close_time_utc": "2026-07-06T10:01:00+00:00",
            "close_price": 101.0,
            "strategy_pnl": 1.0,
            "actual_pnl": 1.0,
            "blockers": [],
        }],
    }


def _policy():
    return strategy_policies.StrategyPolicy(
        policy_id="follow_actual",
        mode="follow_actual",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )


def _paths(tmp_path):
    market = tmp_path / "market"
    conversion = tmp_path / "conversion"
    money = tmp_path / "money.json"
    _write_tick_day(market, "2026-07-06")
    _write_tick_day(conversion, "2026-07-06", symbol="EURUSD")
    _write_money_contract(money)
    return market, conversion, money


def test_farm_independent_certification_accepts_exact_second_engine(tmp_path):
    market, conversion, money = _paths(tmp_path)

    summary, certificates = strategy_farm._build_independent_certification(
        trades=[_trade()],
        policies=[_policy()],
        rows_by_policy={"follow_actual": [_candidate()]},
        providers={},
        tick_cache_dir=market,
        money_contract_path=money,
        money_tick_cache_dir=conversion,
    )

    assert summary["complete"] is True
    assert summary["conclusions_allowed"] is True
    assert summary["rows_expected"] == 1
    assert summary["certified_rows"] == 1
    assert summary["certified_tickets"] == 1
    assert summary["blockers"] == []
    assert certificates[0]["status"] == "certified"


def test_farm_independent_certification_blocks_one_cent_difference(tmp_path):
    market, conversion, money = _paths(tmp_path)
    candidate = deepcopy(_candidate())
    candidate["tickets"][0]["strategy_pnl"] = 1.01

    summary, certificates = strategy_farm._build_independent_certification(
        trades=[_trade()],
        policies=[_policy()],
        rows_by_policy={"follow_actual": [candidate]},
        providers={},
        tick_cache_dir=market,
        money_contract_path=money,
        money_tick_cache_dir=conversion,
    )

    assert summary["complete"] is False
    assert summary["conclusions_allowed"] is False
    assert summary["mismatched_tickets"] == 1
    assert certificates[0]["status"] == "mismatch"


def test_farm_independent_certification_fails_closed_without_contract(tmp_path):
    market, conversion, _money = _paths(tmp_path)

    summary, certificates = strategy_farm._build_independent_certification(
        trades=[_trade()],
        policies=[_policy()],
        rows_by_policy={"follow_actual": [_candidate()]},
        providers={},
        tick_cache_dir=market,
        money_contract_path=tmp_path / "missing.json",
        money_tick_cache_dir=conversion,
    )

    assert certificates == []
    assert summary["complete"] is False
    assert summary["conclusions_allowed"] is False
    assert summary["blockers"] == [
        "independent_money_contract_missing"
    ]


def test_counterfactual_certification_requires_verified_policy_horizon(
    tmp_path,
):
    market, conversion, money = _paths(tmp_path)
    _truncate_tick_coverage(
        market,
        "2026-07-06",
        "2026-07-06T10:01:00+00:00",
    )
    _truncate_tick_coverage(
        conversion,
        "2026-07-06",
        "2026-07-06T10:01:00+00:00",
    )
    policy = strategy_policies.StrategyPolicy(
        policy_id="no_be",
        close_legs=0,
        be_legs=0,
        runner_legs=1,
        base_leg_count=1,
    )
    candidate = deepcopy(_candidate())
    candidate["strategy"] = "no_be"
    candidate["tickets"][0][
        "leg_action"
    ] = "unchanged_no_provider_trigger"

    summary, certificates = strategy_farm._build_independent_certification(
        trades=[_trade()],
        policies=[policy],
        rows_by_policy={"no_be": [candidate]},
        providers={},
        tick_cache_dir=market,
        money_contract_path=money,
        money_tick_cache_dir=conversion,
    )

    assert summary["complete"] is False
    assert summary["conclusions_allowed"] is False
    assert certificates[0]["status"] == "blocked"
    assert certificates[0]["blockers"] == [
        "oracle_blocked:canal2_380:no_be:"
        "incomplete_market_policy_horizon:2026-07-06",
        "oracle_blocked:canal2_380:no_be:"
        "incomplete_conversion_policy_horizon:2026-07-06",
    ]


def test_farm_execution_applies_and_retains_independent_certification(
    tmp_path,
    monkeypatch,
):
    summary = {
        "complete": False,
        "conclusions_allowed": False,
        "blockers": ["forced_test_mismatch"],
    }
    certificates = [{
        "sig_id": "test",
        "strategy": "follow_actual",
        "status": "mismatch",
        "proof_sha256": "a" * 64,
    }]
    captured = {}

    def fake_builder(**kwargs):
        captured.update(kwargs)
        return summary, certificates

    monkeypatch.setattr(
        strategy_farm,
        "_build_independent_certification",
        fake_builder,
    )
    execution = strategy_farm.build_farm_execution(
        [],
        [],
        tick_cache_dir=tmp_path / "ticks",
        policies=[_policy()],
        catalog={"signals": []},
    )

    assert captured["trades"] == []
    assert captured["policies"] == [_policy()]
    assert execution.report["independent_certification"] == summary
    assert execution.report["validation"][
        "independent_certification_complete"
    ] is False
    assert execution.report["selection"]["exploratory_ranking"] == []
    assert execution.selected_payloads[
        "independent_certificates"
    ] == certificates
