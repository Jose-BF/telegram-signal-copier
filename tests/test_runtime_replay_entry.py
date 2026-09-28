"""Direct model controls; no claim to exercise the real entry controller."""

from contextlib import contextmanager
from dataclasses import replace

import pytest

from research.dubai_iterative.protection import ProtectionBlocked
from research.dubai_iterative.runtime_control import (
    NativeEntryCommand, NativeEntryResponse, RuntimeBrokerIdentity,
)
from research.risk_metrics import money_path_metrics
from tests.runtime_replay_support import ReplayOwner
from tests.test_client_terminal_close import execution, strategy, tape
from tests.test_shared_policy_replay import spec


@contextmanager
def model(direction="BUY", quotes=None, *, entry_owner="external_runtime", ack_ms=0,
          market_events=100, protection_events=100, offsets=None, ladder=False):
    assumptions = execution()
    assumptions = replace(assumptions,
        market=replace(assumptions.market, entry_acknowledgement_delay_ms=ack_ms,
                       max_events=market_events),
        protection=replace(assumptions.protection, max_events=protection_events))
    rules = strategy().with_change(provider_management_mode="ignore")
    if ladder:
        rules = rules.with_change(leg_count=2, volume_weights=(.04, .03),
                                  entry_ladder_mode="adverse", entry_ladder_step=1.5)
    item = spec("canal2_entry_control", [], strategy=rules, execution=assumptions,
                tape=tape(quotes if quotes is not None else [100.] * 5,
                          direction=direction, close_at=100, offsets=offsets))
    owner = ReplayOwner(item, RuntimeBrokerIdentity("XAUUSD", 111, 1000, entry_owner=entry_owner))
    try:
        assert owner.advance()
        yield owner
    finally:
        owner.close()


def command(owner, **changes):
    snapshot = owner.snapshot()
    direction = owner.spec.path.direction
    return replace(NativeEntryCommand("send-1", "XAUUSD", 111, direction, .04, 123.,
        70. if direction == "BUY" else 130., 0., snapshot["quote_index"], snapshot["time_ns"]),
        **changes)


def send(owner, **changes):
    receipt = owner.broker.runtime_control.apply_entry(command(owner, **changes))
    owner.risk.extend(owner.broker.runtime_control.drain_risk())
    return receipt


def ack(owner, receipt):
    snapshot = owner.snapshot()
    return owner.broker.runtime_control.observe_entry_response(NativeEntryResponse(
        receipt.execution_id, snapshot["quote_index"], snapshot["time_ns"]))


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_external_owner_never_generates_initial_or_ladder_fills(direction):
    quotes = [100., 98., 96., 94.] if direction == "BUY" else [100., 102., 104., 106.]
    with model(direction, quotes, ladder=True) as owner:
        for _ in range(2):
            assert owner.snapshot()["positions"] == owner.snapshot()["entries"] == []
            assert owner.advance()
        result = owner.finish()
        assert result.entries == result.exits == result.client_events == result.market_events == ()
        assert result.unfilled and not result.blockers


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_fill_uses_current_executable_quote_not_request_price(direction):
    quotes = [100., 102., 102., 102.] if direction == "BUY" else [100., 98., 98., 98.]
    with model(direction, quotes) as owner:
        assert owner.advance()
        receipt = send(owner, requested_price=500.)
        expected = quotes[1]
        assert receipt.retcode == 10009 and receipt.price == expected and receipt.volume == .04
        assert receipt.native_result()["order"] == receipt.ticket
        snapshot = owner.snapshot()
        assert len(snapshot["positions"]) == len(snapshot["entries"]) == 1
        position, entry = snapshot["positions"][0], snapshot["entries"][0]
        assert position["price_open"] == expected and position["ticket"] == receipt.ticket
        assert position["sl"] == (70. if direction == "BUY" else 130.) and position["tp"] == 0.
        assert entry["tick_index"] == entry["price_tick_index"] == 1
        assert entry["requested_ns"] == snapshot["time_ns"] and entry["acknowledged_ns"] is None
        assert ack(owner, receipt) is True
        assert owner.snapshot()["entries"][0]["acknowledged_ns"] == snapshot["time_ns"]
        assert ack(owner, receipt) is False


@pytest.mark.parametrize("ack_ms", [0, 1000])
def test_advancing_quotes_never_delivers_external_ack(ack_ms):
    with model(ack_ms=ack_ms) as owner:
        receipt = send(owner)
        assert owner.advance() and owner.advance()
        assert owner.snapshot()["entries"][0]["acknowledged_ns"] is None
        assert ack(owner, receipt) is True
        assert owner.snapshot()["entries"][0]["acknowledged_ns"] == owner.spec.path.times_ns[2]


@pytest.mark.parametrize("changes,retcode", [
    ({"symbol": "OTHER"}, 10013), ({"magic": 222}, 10013),
    ({"direction": "SELL"}, 10013), ({"volume": .005}, 10014),
    ({"volume": 1.01}, 10014), ({"sl": 100.}, 10016), ({"tp": 99.}, 10016),
])
def test_rejected_entry_has_response_but_never_exposure(changes, retcode):
    with model() as owner:
        before = owner.snapshot()
        receipt = send(owner, **changes)
        assert receipt.retcode == retcode and receipt.ticket == 0 and receipt.price == 0.
        assert owner.snapshot() == before
        assert ack(owner, receipt) is True
        result = owner.finish()
        assert result.entries == result.exits == () and not result.blockers
        assert [row.kind for row in result.market_events] == [
            "entry_requested", "entry_rejected", "entry_acknowledged"]


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_late_ack_after_native_tp_cannot_resurrect_position(direction):
    quotes = [100., 101., 101., 101.] if direction == "BUY" else [100., 99., 99., 99.]
    with model(direction, quotes) as owner:
        receipt = send(owner, tp=100.5 if direction == "BUY" else 99.5)
        assert owner.advance()
        assert owner.snapshot()["positions"] == []
        assert owner.snapshot()["entries"][0]["acknowledged_ns"] is None
        assert ack(owner, receipt) is True
        assert owner.snapshot()["positions"] == []
        result = owner.finish()
        assert not result.blockers and len(result.entries) == len(result.exits) == 1
        assert result.exits[0].tick_index == 1 and result.exits[0].reason == "runtime_initial_tp"
        assert float(result.pnl_eur) == pytest.approx(2.)


def test_duplicate_request_id_models_two_real_sends_not_intent_deduplication():
    with model() as owner:
        first = send(owner, request_id="same")
        second = send(owner, request_id="same")
        assert first.request_id == second.request_id == "same"
        assert first.execution_id != second.execution_id and first.ticket != second.ticket
        positions = owner.snapshot()["positions"]
        assert len(positions) == 2 and sum(p["volume"] for p in positions) == .08
        assert ack(owner, first) and ack(owner, second)


@pytest.mark.parametrize("field", ["quote_index", "time_ns"])
def test_entry_rejects_mismatched_current_clock_without_mutation(field):
    with model() as owner:
        current = command(owner)
        before = owner.snapshot()
        wrong = replace(current, **{field: getattr(current, field) + 1})
        with pytest.raises(ProtectionBlocked, match="causal_clock"):
            owner.broker.runtime_control.apply_entry(wrong)
        assert owner.snapshot() == before
        assert owner.broker.runtime_control.drain_risk() == ()


def test_previous_ordinal_is_stale_even_when_timestamp_is_duplicate():
    with model(offsets=[0, 0, 1, 2, 3]) as owner:
        old = command(owner)
        assert owner.advance()
        assert owner.snapshot()["time_ns"] == old.time_ns
        with pytest.raises(ProtectionBlocked, match="causal_clock"):
            owner.broker.runtime_control.apply_entry(old)
        assert owner.snapshot()["positions"] == []


def test_unknown_or_wrong_clock_response_cannot_ack_entry():
    with model() as owner:
        receipt = send(owner)
        snapshot = owner.snapshot()
        response = NativeEntryResponse(receipt.execution_id, snapshot["quote_index"], snapshot["time_ns"])
        with pytest.raises(ValueError, match="unknown"):
            owner.broker.runtime_control.observe_entry_response(replace(response, execution_id=999))
        with pytest.raises(ProtectionBlocked, match="causal_clock"):
            owner.broker.runtime_control.observe_entry_response(replace(response, time_ns=response.time_ns + 1))
        assert owner.snapshot() == snapshot


def test_model_entry_owner_cannot_accept_external_entry():
    with model(entry_owner="model") as owner:
        before = owner.snapshot()
        with pytest.raises(ValueError, match="ownership"):
            owner.broker.runtime_control.apply_entry(command(owner))
        assert owner.snapshot() == before


def test_closed_external_owner_cannot_accept_entry():
    with model() as owner:
        owner.external_entry_open = False
        before = owner.snapshot()
        with pytest.raises(ProtectionBlocked, match="control_closed"):
            owner.broker.runtime_control.apply_entry(command(owner))
        assert owner.snapshot() == before


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_fill_risk_is_immediate_and_post_event_path_has_independent_drawdown(direction):
    quotes = [100., 105., 80., 80.] if direction == "BUY" else [100., 95., 120., 120.]
    with model(direction, quotes) as owner:
        receipt = owner.broker.runtime_control.apply_entry(command(owner,
            sl=90. if direction == "BUY" else 110.))
        samples = owner.broker.runtime_control.drain_risk()
        assert [row["phase"] for row in samples] == ["entry_fill", "settled"]
        assert all(row["tick_index"] == 0 and row["time_ns"] == owner.spec.path.times_ns[0]
                   and row["realized_minor"] == 0 and row["floating_minor"] == -80 for row in samples)
        assert samples[-1]["positions"] == (("runtime_1", .04, 100.),)
        assert owner.broker.runtime_control.drain_risk() == ()
        owner.risk.extend(samples)
        assert ack(owner, receipt)
        result = owner.finish()
        assert not result.blockers and float(result.pnl_eur) == pytest.approx(-80.8)
        settled = {row["tick_index"]: row for row in owner.risk if row["phase"] == "settled"}
        values = [row["realized_minor"] + row["floating_minor"] for row in settled.values()]
        assert list(settled) == [0, 1, 2]
        assert values == [-80, 1920, -8080]
        assert money_path_metrics(values, origin=0)["max_drawdown"] == 10000
        assert float(result.max_floating_drawdown_eur) == pytest.approx(100.)


def test_native_close_without_ack_remains_incomplete_at_cutoff():
    with model(quotes=[100., 101., 101.]) as owner:
        send(owner, tp=100.5)
        result = owner.finish()
        assert len(result.entries) == len(result.exits) == 1
        assert result.entries[0].acknowledged_ns is None
        assert "market_lifecycle_incomplete_at_data_end" in result.blockers
        assert result.pnl_eur is None


def test_unclosed_entry_source_is_not_a_complete_flat_result():
    with model(quotes=[100., 100.]) as owner:
        while owner.advance():
            pass
        result = owner.result
        assert result.entries == () and result.pnl_eur is None
        assert "runtime_entry_control_open_at_data_end" in result.blockers


@pytest.mark.parametrize("budget", ["market", "protection"])
def test_event_budget_blocks_entry_before_any_partial_fill(budget):
    with model(market_events=1 if budget == "market" else 100,
               protection_events=1) as owner:
        if budget == "protection":
            assert send(owner).retcode == 10009
        before = owner.snapshot()
        with pytest.raises(ProtectionBlocked, match=budget + "_event_budget_exhausted"):
            send(owner, request_id="second")
        assert owner.snapshot() == before
        assert owner.broker.runtime_control.drain_risk() == ()


@pytest.mark.parametrize("stage", ["completed", "closed", "boundary"])
@pytest.mark.parametrize("operation", ["entry", "protection", "ack"])
def test_runtime_callbacks_reject_mutation_outside_active_settlement(stage, operation):
    from research.dubai_iterative.engine import _ReplayBoundary
    from research.dubai_iterative.runtime_control import NativeProtectionEffect

    with model(quotes=[100., 100., 100.]) as owner:
        receipt = send(owner)
        if stage == "completed":
            while owner.advance():
                pass
            assert owner.result is not None and owner.state is None
        elif stage == "closed":
            owner.stream.close()
        else:
            owner._resume()
            assert isinstance(owner.state, _ReplayBoundary)
        # Read-only inspection remains valid even when mutation is forbidden.
        before = owner.snapshot()
        result_before, risk_count_before = owner.result, owner.risk_count
        with pytest.raises(ProtectionBlocked, match="runtime_control_not_active"):
            if operation == "entry":
                owner.broker.runtime_control.apply_entry(command(owner, request_id="after-stop"))
            elif operation == "protection":
                owner.broker.runtime_control.apply_protection(NativeProtectionEffect(
                    receipt.ticket, 90., 110., before["quote_index"], before["time_ns"]))
            else:
                ack(owner, receipt)
        assert owner.snapshot() == before
        assert owner.result == result_before and owner.risk_count == risk_count_before
        assert owner.broker.runtime_control.drain_risk() == ()


def test_entry_ack_after_native_exit_and_final_cutoff_cannot_rewrite_result():
    with model(quotes=[100., 101., 101.]) as owner:
        receipt = send(owner, tp=100.5)
        result = owner.finish()
        before = owner.snapshot()
        assert before["positions"] == []
        assert len(result.entries) == len(result.exits) == 1
        assert result.entries[0].acknowledged_ns is None
        assert "market_lifecycle_incomplete_at_data_end" in result.blockers
        with pytest.raises(ProtectionBlocked, match="runtime_control_not_active"):
            ack(owner, receipt)
        assert owner.snapshot() == before
        assert owner.result == result and result.entries[0].acknowledged_ns is None


@pytest.mark.parametrize("free_slots", [0, 1])
def test_external_fill_reserves_both_risk_samples_atomically_and_finishes_blocked(free_slots):
    with model() as owner:
        owner.risk_count = 10_000 - free_slots
        before, count_before = owner.snapshot(), owner.risk_count
        with pytest.raises(ProtectionBlocked, match="runtime_control_risk_budget_exhausted"):
            send(owner)
        assert owner.risk_count == count_before
        assert owner.snapshot() == before
        assert owner.broker.runtime_control.drain_risk() == ()
        result = owner.finish()
        assert "runtime_control_risk_budget_exhausted" in result.blockers
        assert result.entries == result.exits == ()
        assert result.market_events == result.protection_events == ()
        assert result.pnl_eur is None
        assert owner.risk_count <= 10_000
