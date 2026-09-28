"""Lost native reply recovered through real typed read/trade workers."""

from functools import partial
import json
from pathlib import Path
import asyncio
import subprocess
import sys

import psutil

import pytest

from durable_entry_execution import DurableEntryExecutor, EntryDispatchState
from durable_execution import DurableExecutionService
from mt5_client import MT5ReadClient
from mt5_protocol import BrokerRequest, IntentState, LookupState
from mt5_worker import WorkerConfig
from tests.mt5_read_fakes import FakeTradeMT5, Tick
from tests.test_mt5_trade_client import trade_request


BASE = 1700000000000


class LostReplyHistoryMT5(FakeTradeMT5):
    def __init__(self, *, ledger_path, marker, history_mode="found", hang_after_send=0):
        super().__init__(marker=marker, trade_mode="none")
        self.ledger_path, self.history_mode = ledger_path, history_mode
        self.hang_after_send = hang_after_send

    def symbol_info_tick(self, symbol):
        self._call("symbol_info_tick", symbol)
        return Tick(BASE + (1000 if self._ledger() else 0), 2500., 2500.2)

    def _ledger(self):
        from pathlib import Path
        path = Path(self.ledger_path)
        return json.loads(path.read_text(encoding="ascii")) if path.exists() else None

    def order_send(self, request):
        from pathlib import Path
        self._call("order_send", request)
        with open(self.marker, "a", encoding="ascii") as stream:
            stream.write("send\n")
        Path(self.ledger_path).write_text(json.dumps(request), encoding="ascii")
        Path(self.ledger_path).with_name("effect-ready.txt").write_text("ready", encoding="ascii")
        if self.hang_after_send:
            from tests.mt5_read_fakes import hold_native_gil
            hold_native_gil(self.hang_after_send)
        self.error = (-10004, "lost reply")
        return None

    def _entry_rows(self):
        native = self._ledger()
        if native is None or self.history_mode == "empty":
            return (), ()
        side = native["type"]
        common = {"symbol": native["symbol"], "magic": native["magic"],
                  "comment": native["comment"], "type": side}
        order = {**common, "ticket": 701, "position_id": 701, "state": 4,
                 "volume_initial": native["volume"], "volume_current": 0.,
                 "time_setup_msc": BASE + 200, "time_done_msc": BASE + 400}
        deal = {**common, "ticket": 702, "order": 701, "position_id": 701,
                "entry": 0, "time_msc": BASE + 300, "volume": native["volume"],
                "price": native["price"] + (.05 if side == 0 else -.05)}
        return (deal,), (order,)

    def history_deals_get(self, *args, **kwargs):
        self._call("history_deals_get", *args, **kwargs)
        from pathlib import Path
        with Path(self.ledger_path).with_name("history-reads.txt").open("a", encoding="ascii") as stream:
            stream.write("deals\n")
        if self.history_mode == "unknown":
            self.error = (-10004, "unavailable")
            return None
        return self._entry_rows()[0]

    def history_orders_get(self, *args, **kwargs):
        self._call("history_orders_get", *args, **kwargs)
        from pathlib import Path
        with Path(self.ledger_path).with_name("history-reads.txt").open("a", encoding="ascii") as stream:
            stream.write("orders\n")
        if self.history_mode == "unknown":
            self.error = (-10004, "unavailable")
            return None
        return self._entry_rows()[1]


def client(tmp_path, *, history_mode="found", exclusive=False):
    return MT5ReadClient(
        WorkerConfig(7, "demo", ("XAUUSD",),
                     owner_lock_path=str(tmp_path / "owner.lock") if exclusive else None),
        backend_factory=partial(
            LostReplyHistoryMT5, ledger_path=str(tmp_path / "broker.json"),
            marker=str(tmp_path / "sends.txt"), history_mode=history_mode),
        store_path=tmp_path / "intents.sqlite3")


def entry_request(direction):
    template = trade_request()
    if direction == "BUY":
        return template
    return BrokerRequest.create(
        template.intent_key,
        {**dict(template.payload), "direction": "SELL", "sl": 2501., "tp": 2498.},
        request_id=template.request_id, attempt_id=template.attempt_id,
        action_id=template.action_id,
    )


async def open_via_adapter(adapter):
    return await adapter.open_market(
        channel="canal1", signal_root="canal1_3086", generation=1,
        leg="entry-1", revision=0, direction="BUY", volume=.01,
        sl=2499., tp=2502., loss_budget=None, protection_policy="required",
        magic=111, comment="c1_3086_1", action_id="action-1",
    )


@pytest.mark.asyncio
async def test_uncertain_redelivery_recovers_without_resending(tmp_path):
    first = client(tmp_path)
    assert (await first.start()).state is LookupState.FOUND
    try:
        initial = await open_via_adapter(DurableEntryExecutor(
            DurableExecutionService(first), symbol="XAUUSD"))
        assert initial.state is EntryDispatchState.RECONCILE
        assert not (tmp_path / "history-reads.txt").exists()
    finally:
        await first.close()

    restarted = client(tmp_path)
    assert (await restarted.start()).state is LookupState.FOUND
    try:
        recovered = await open_via_adapter(DurableEntryExecutor(
            DurableExecutionService(restarted), symbol="XAUUSD"))
        assert recovered.state is EntryDispatchState.CONFIRMED
        assert (recovered.ticket, recovered.fill_price) == (701, 2500.25)
    finally:
        await restarted.close()
    assert (tmp_path / "sends.txt").read_text(encoding="ascii").splitlines() == ["send"]
    assert (tmp_path / "history-reads.txt").read_text(encoding="ascii").splitlines() == ["deals", "orders"]


@pytest.mark.asyncio
async def test_uncertain_redelivery_throttles_empty_history_reads(tmp_path):
    c = client(tmp_path, history_mode="empty")
    assert (await c.start()).state is LookupState.FOUND
    try:
        adapter = DurableEntryExecutor(DurableExecutionService(c), symbol="XAUUSD")
        assert (await open_via_adapter(adapter)).state is EntryDispatchState.RECONCILE
        assert (await open_via_adapter(adapter)).state is EntryDispatchState.RECONCILE
        assert (await open_via_adapter(adapter)).state is EntryDispatchState.RECONCILE
    finally:
        await c.close()
    assert (tmp_path / "sends.txt").read_text(encoding="ascii").splitlines() == ["send"]
    assert (tmp_path / "history-reads.txt").read_text(encoding="ascii").splitlines() == ["deals", "orders"]


@pytest.mark.asyncio
@pytest.mark.parametrize("history_mode", ["found", "empty"])
async def test_full_parent_death_recovers_committed_entry_without_resend(tmp_path, history_mode):
    parent = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("entry_history_orphan_parent.py")), str(tmp_path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    worker = None
    replacement = client(tmp_path, history_mode=history_mode, exclusive=True)
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while not (tmp_path / "effect-ready.txt").exists():
            assert parent.poll() is None, "disposable parent exited before native effect"
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.02)
        metadata = json.loads((tmp_path / "worker.json").read_text(encoding="ascii"))
        worker = psutil.Process(metadata["pid"])
        assert worker.create_time() == metadata["created"]
        parent.kill()
        await asyncio.to_thread(parent.wait, timeout=5)
        await asyncio.to_thread(worker.wait, timeout=5)
        assert (await replacement.start()).state is LookupState.FOUND
        request = entry_request("BUY")
        assert replacement.store.get(request.intent_id).state is IntentState.UNKNOWN
        recovered = await open_via_adapter(DurableEntryExecutor(
            DurableExecutionService(replacement), symbol="XAUUSD"))
        expected = EntryDispatchState.CONFIRMED if history_mode == "found" else EntryDispatchState.RECONCILE
        assert recovered.state is expected
        assert recovered.ticket == (701 if history_mode == "found" else None)
        projections = replacement.store.list_projections(projection_key="signal:canal1_3086")
        assert [item.projection["state"] for item in projections] == (
            ["UNKNOWN", "DONE"] if history_mode == "found" else ["UNKNOWN"]
        )
        assert (tmp_path / "sends.txt").read_text(encoding="ascii").splitlines() == ["send"]
    finally:
        if parent.poll() is None:
            parent.kill()
        await asyncio.to_thread(parent.wait, timeout=5)
        if worker is not None and worker.is_running():
            worker.kill()
            await asyncio.to_thread(worker.wait, timeout=5)
        await replacement.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
async def test_lost_reply_reinspected_after_worker_restart_without_a_second_send(tmp_path, direction):
    request = entry_request(direction)
    first = client(tmp_path)
    assert (await first.start()).state is LookupState.FOUND
    try:
        unknown = await first.execute(request, reservation_key="entry", policy_revision=0)
    finally:
        await first.close()
    assert unknown.state is IntentState.UNKNOWN
    assert unknown.outcome["order"] is None
    assert (tmp_path / "sends.txt").read_text(encoding="ascii").splitlines() == ["send"]

    restarted = client(tmp_path)
    assert (await restarted.start()).state is LookupState.FOUND
    try:
        result = await DurableExecutionService(restarted).reinspect_entry(request.intent_id)
        assert result.recovered is True
        assert result.record.state is IntentState.DONE
        assert result.record.attempt_id == request.attempt_id
        assert result.record.outcome["retcode"] is None
        assert result.record.outcome["order"] == 701
        assert result.record.outcome["deal"] == 702
        assert result.record.outcome["filled_volume"] == .01
        assert result.record.outcome["price"] == (2500.25 if direction == "BUY" else 2499.95)
        assert len(restarted.store.list_projections(
            projection_key=f"signal:{request.intent_key.signal_root}")) == 1
        redelivery = await restarted.execute(
            BrokerRequest.create(
                request.intent_key, dict(request.payload), request_id="request-redelivery",
                attempt_id="attempt-redelivery", action_id=request.action_id),
            reservation_key="entry", policy_revision=0,
        )
        assert redelivery.state is IntentState.DONE
        repeat = await DurableExecutionService(restarted).reinspect_entry(request.intent_id)
        assert repeat.recovered is False
        assert repeat.record == result.record
    finally:
        await restarted.close()
    assert (tmp_path / "sends.txt").read_text(encoding="ascii").splitlines() == ["send"]


@pytest.mark.asyncio
@pytest.mark.parametrize("history_mode", ["empty", "unknown"])
async def test_missing_or_unknown_history_keeps_open_risk_explicit(tmp_path, history_mode):
    request = entry_request("BUY")
    c = client(tmp_path, history_mode=history_mode)
    assert (await c.start()).state is LookupState.FOUND
    try:
        assert (await c.execute(request, reservation_key="entry", policy_revision=0)).state is IntentState.UNKNOWN
        result = await DurableExecutionService(c).reinspect_entry(request.intent_id)
        assert result.recovered is False
        assert result.record.state is IntentState.UNKNOWN
        assert result.reason is not None
        assert c.store.list_projections(projection_key="signal:canal1_3086") == []
    finally:
        await c.close()
    assert (tmp_path / "sends.txt").read_text(encoding="ascii").splitlines() == ["send"]
