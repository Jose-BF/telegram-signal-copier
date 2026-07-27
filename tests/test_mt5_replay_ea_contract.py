from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "mql5" / "Experts" / "TelegramSignalReplayEA.mq5"


def test_replay_ea_is_tester_only_and_uses_virtual_profit():
    source = SOURCE.read_text(encoding="utf-8")

    for required in (
        "MQLInfoInteger(MQL_TESTER)",
        "FILE_COMMON",
        "OrderCalcProfit",
        "SymbolInfoTick",
        "time_msc",
        "observed_close",
        "all_tp2_keep_be",
        "all_tp2_no_be",
        "order_calc_profit_zero",
    ):
        assert required in source


def test_replay_ea_has_no_live_order_or_network_path():
    source = SOURCE.read_text(encoding="utf-8")

    for forbidden in (
        "OrderSend(",
        "OrderSendAsync(",
        "CTrade",
        "PositionClose",
        "PositionModify",
        "WebRequest(",
        "TesterStop(",
        "#import",
    ):
        assert forbidden not in source


def test_replay_ea_does_not_finalize_without_an_open_result_file():
    source = SOURCE.read_text(encoding="utf-8")
    finalize = source.split("void FinalizeResults()", 1)[1].split(
        "int OnInit()", 1
    )[0]

    assert "if(g_result_handle==INVALID_HANDLE)" in finalize


def test_replay_ea_retries_impossible_zero_without_moving_first_touch():
    source = SOURCE.read_text(encoding="utf-8")

    for required in (
        "close_pending",
        "pending_close_time_msc",
        "pending_close_price",
        "pending_close_reason",
        "pending_touch_bid",
        "pending_touch_ask",
        "profit_calc_zero_attempts",
        "profit_calc_zero_first_tick_msc",
        "PROFIT_CALC_ZERO_RETRY_WINDOW_MSC",
        "RetryPendingClose",
    ):
        assert required in source

    assert "MathAbs(pnl)<" in source
    assert "MathAbs(item.pending_close_price-item.entry_price)" in source
    assert 'BlockPendingClose(item,"order_calc_profit_zero",tick)' in source
