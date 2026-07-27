from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "mql5"
    / "Services"
    / "BrokerMoneySnapshotService.mq5"
)


def test_broker_snapshot_service_exports_exact_native_swap_evidence():
    source = SOURCE.read_text(encoding="utf-8")

    for required in (
        "#property service",
        "FileMove(",
        "TimeTradeServer()",
        "TimeGMT()",
        "SymbolInfoTick(InpSymbol",
        "ACCOUNT_SERVER",
        "SYMBOL_SWAP_MODE",
        "SYMBOL_SWAP_LONG",
        "SYMBOL_SWAP_SHORT",
        "SYMBOL_SWAP_ROLLOVER3DAYS",
        "SYMBOL_SWAP_SUNDAY",
        "SYMBOL_SWAP_MONDAY",
        "SYMBOL_SWAP_TUESDAY",
        "SYMBOL_SWAP_WEDNESDAY",
        "SYMBOL_SWAP_THURSDAY",
        "SYMBOL_SWAP_FRIDAY",
        "SYMBOL_SWAP_SATURDAY",
        "Sleep(",
    ):
        assert required in source
    assert "FILE_COMMON" not in source
    assert "MathAbs" not in source
    assert "TimeCurrent()" not in source


def test_broker_snapshot_service_has_no_trading_or_network_path():
    source = SOURCE.read_text(encoding="utf-8")

    for forbidden in (
        "OrderSend(",
        "OrderSendAsync(",
        "CTrade",
        "PositionClose",
        "PositionModify",
        "WebRequest(",
        "#import",
    ):
        assert forbidden not in source
