import json
from tools.audit_mt5_native_access import BASELINE, inventory, native_references


def test_checked_in_temporary_inventory_matches_source():
    actual = inventory()
    assert actual == json.loads(BASELINE.read_text(encoding="utf-8"))
    assert "executor.py" not in actual["files"]
    assert actual["files"]["mt5_worker.py"]["scope"] == "isolated_live_owner"
    assert not any(
        row["scope"] == "legacy_live_pending_migration"
        for row in actual["files"].values()
    )


def test_aliases_imported_functions_callbacks_and_dynamic_access_remain_visible():
    source = '''
import MetaTrader5 as m
import executor as ex
from executor import mt5 as x
from MetaTrader5 import positions_get as positions
import importlib
n = importlib.import_module("MetaTrader5")
alias = x
tick = ex.mt5.symbol_info_tick
await loop.run_in_executor(None, tick, "XAUUSD")
positions()
alias.orders_get()
n.account_info()
getattr(m, operation)()
'''
    targets = {(row["kind"], row["target"]) for row in native_references(source)}
    assert {("access", name) for name in ("positions_get", "account_info", "<dynamic>")} <= targets
    assert ("access", "symbol_info_tick") not in targets
    assert ("access", "orders_get") not in targets


def test_added_native_access_changes_inventory(tmp_path):
    path = tmp_path / "consumer.py"
    path.write_text("import MetaTrader5 as mt5\n", encoding="utf-8")
    before = inventory(tmp_path)
    path.write_text("import MetaTrader5 as mt5\nmt5.order_send({})\n", encoding="utf-8")
    assert inventory(tmp_path) != before
