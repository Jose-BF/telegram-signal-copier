"""Explicit unrounded valuation hypothesis, not a native broker certificate."""

import asyncio
from contextlib import contextmanager
from dataclasses import asdict, replace

import numpy as np
import pytest

from research.dubai_iterative.engine import _ReplayBoundary
from research.dubai_iterative.runtime_control import RuntimeBrokerAccess, RuntimeBrokerIdentity
from tests.runtime_replay_support import ReplayOwner, RuntimeMailbox
from tests.test_client_terminal_close import execution, strategy, tape
from tests.test_shared_policy_replay import spec


PROFILE = "linear_contract_fx_unrounded_v1"


@contextmanager
def profit_model(*, data=None, enabled=True, advance=True):
    data = tape([100., 101., 102.]) if data is None else data
    item = spec("canal1_profit", [], tape=data,
                strategy=strategy().with_change(provider_management_mode="ignore"), execution=execution())
    identity = RuntimeBrokerIdentity("XAUUSD", 111, 1000, entry_owner="external_runtime",
                                    valuation_profile=PROFILE if enabled else None)
    owner = ReplayOwner(item, identity)
    try:
        if advance:
            assert owner.advance()
        yield owner
    finally:
        owner.close()


def fx_tape(orientation="account_base_profit_quote", **changes):
    data = replace(tape([100., 101., 102.]), conversion_orientation=orientation,
                   fx_bid=np.array([2., 4., 8.]), fx_ask=np.array([2.5, 5., 10.]))
    return replace(data, **changes)


def test_capability_is_opt_in_and_appended_without_changing_old_positional_fields():
    identity = RuntimeBrokerIdentity("XAUUSD", 111, 1000, "external_runtime",
                                    "synthetic_zero_commission_fee_v1")
    assert identity.valuation_profile is None
    callbacks = tuple(object() for _ in range(8))
    access = RuntimeBrokerAccess(*callbacks)
    assert access.observe_close_response is callbacks[-1]
    assert access.calc_profit is None
    with profit_model(enabled=False) as owner:
        assert owner.broker.runtime_control.calc_profit is None
    with pytest.raises(ValueError, match="valuation"):
        replace(identity, valuation_profile="native")


@pytest.mark.parametrize("action,close,expected", [
    (0, 101., 4.), (0, 99., -4.), (0, 100., 0.),
    (1, 101., -4.), (1, 99., 4.), (1, 100., 0.),
    (0, 100.001, .004), (1, 100.001, -.004),
])
def test_requested_direction_unrounded_profit_and_no_book_or_risk_mutation(action, close, expected):
    with profit_model() as owner:
        before = owner.snapshot(), list(owner.risk), owner.risk_count
        calc = owner.broker.runtime_control.calc_profit
        assert calc(action, "XAUUSD", .04, 100., close) == pytest.approx(expected)
        assert (owner.snapshot(), owner.risk, owner.risk_count) == before
        assert owner.broker.runtime_control.history is None
        assert asdict(owner.runtime_identity)["valuation_profile"] == PROFILE


@pytest.mark.parametrize("orientation,positive,negative", [
    ("account_base_profit_quote", [1.6, .8, .4], [-2., -1., -.5]),
    ("profit_base_account_quote", [8., 16., 32.], [-10., -20., -40.]),
])
@pytest.mark.parametrize("action", [0, 1])
def test_each_call_samples_current_fx_and_boundary_does_not_read_future(orientation, positive, negative, action):
    with profit_model(data=fx_tape(orientation)) as owner:
        calc = owner.broker.runtime_control.calc_profit
        gain = 101. if action == 0 else 99.
        loss = 99. if action == 0 else 101.
        for index in range(3):
            assert calc(action, "XAUUSD", .04, 100., gain) == pytest.approx(positive[index])
            assert calc(action, "XAUUSD", .04, 100., loss) == pytest.approx(negative[index])
            if index < 2:
                owner._resume()
                assert isinstance(owner.state, _ReplayBoundary)
                assert calc(action, "XAUUSD", .04, 100., gain) == pytest.approx(positive[index])
                assert owner.advance()


@pytest.mark.parametrize("changes", [
    {"fx_valid": np.array([False, True, True])},
    {"fx_bid": np.array([float("nan"), 4., 8.])},
    {"fx_ask": np.array([float("inf"), 5., 10.])},
    {"fx_bid": np.array([0., 4., 8.])},
    {"fx_ask": np.array([1., 5., 10.])},
    {"conversion_orientation": "unknown"},
])
def test_invalid_fx_is_unknown_even_for_zero_profit(changes):
    with profit_model(data=fx_tape(**changes)) as owner:
        for close in (99., 100., 101.):
            assert owner.broker.runtime_control.calc_profit(0, "XAUUSD", .04, 100., close) is None


def test_unknown_symbol_and_not_yet_observed_quote_are_unknown_not_zero():
    with profit_model(advance=False) as owner:
        calc = owner.broker.runtime_control.calc_profit
        assert calc(0, "XAUUSD", .04, 100., 101.) is None
        assert owner.advance()
        assert calc(0, "OTHER", .04, 100., 100.) is None


def test_current_unusable_quote_does_not_reuse_previous_valid_quote():
    data = tape([100., float("nan"), 102.])
    with profit_model(data=data) as owner:
        calc = owner.broker.runtime_control.calc_profit
        assert calc(0, "XAUUSD", .04, 100., 101.) == 4.
        assert owner.advance()
        assert calc(0, "XAUUSD", .04, 100., 101.) is None
        assert owner.advance()
        assert calc(0, "XAUUSD", .04, 100., 101.) == 4.


@pytest.mark.parametrize("slot,value", [
    (0, True), (0, 0.), (0, 2), (0, "BUY"), (1, ""), (1, None),
    *[(slot, value) for slot in (2, 3, 4)
      for value in (True, 0., -1., float("nan"), float("inf"), "1", None)],
])
def test_invalid_parameters_rejected_without_mutation(slot, value):
    with profit_model() as owner:
        params = [0, "XAUUSD", .04, 100., 101.]
        params[slot] = value
        before = owner.snapshot(), owner.risk_count
        with pytest.raises(ValueError):
            owner.broker.runtime_control.calc_profit(*params)
        assert (owner.snapshot(), owner.risk_count) == before


def test_overflow_result_is_unknown_not_infinity():
    with profit_model() as owner:
        assert owner.broker.runtime_control.calc_profit(0, "XAUUSD", 1e308, 1., 1e308) is None


@pytest.mark.asyncio
async def test_mailbox_owner_read_has_explicit_hypothesis_and_current_source_trace():
    with profit_model(data=fx_tape()) as owner:
        mailbox = RuntimeMailbox(owner)
        try:
            assert await asyncio.to_thread(mailbox.call, "order_calc_profit", 0, "XAUUSD", .04, 100., 101.) == 1.6
            assert owner.advance()
            assert await asyncio.to_thread(mailbox.call, "order_calc_profit", 0, "XAUUSD", .04, 100., 101.) == .8
            assert [row["quote_index"] for row in mailbox.records] == [0, 1]
            assert all(row["valuation_profile"] == PROFILE and row["native_valuation_verified"] is False
                       for row in mailbox.records)
            assert not owner.snapshot()["positions"]
        finally:
            await mailbox.close()


@pytest.mark.asyncio
async def test_mailbox_does_not_automatically_admit_profit_reads():
    with profit_model(enabled=False) as owner:
        mailbox = RuntimeMailbox(owner)
        try:
            with pytest.raises(ValueError, match="valuation"):
                await asyncio.to_thread(mailbox.call, "order_calc_profit", 0, "XAUUSD", .04, 100., 101.)
        finally:
            await mailbox.close()


@pytest.mark.asyncio
async def test_mailbox_invalid_quote_returns_unknown_without_snapshot_dependency():
    with profit_model(data=tape([100., float("nan"), 102.])) as owner:
        mailbox = RuntimeMailbox(owner)
        try:
            assert owner.advance()
            value = await asyncio.to_thread(mailbox.call, "order_calc_profit", 0, "XAUUSD", .04, 100., 101.)
            assert value is None
            assert mailbox.records[-1]["quote_index"] == 1
            assert mailbox.records[-1]["time_ns"] == int(owner.spec.path.times_ns[1])
        finally:
            await mailbox.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("action,close,expected", [(0, 99., -4.), (1, 99., 4.), (0, 100., 0.)])
async def test_real_framed_read_worker_preserves_scalar_and_unknown(tmp_path, action, close, expected):
    from mt5_protocol import LookupState
    from mt5_read_protocol import ReadOperation, ReadRequest
    from mt5_worker import WorkerConfig
    from tests.runtime_replay_transport import InProcessMT5Client

    data = fx_tape(fx_bid=np.ones(3), fx_ask=np.ones(3),
                   fx_valid=np.array([True, False, True]))
    with profit_model(data=data) as owner:
        mailbox = RuntimeMailbox(owner)
        client = InProcessMT5Client(WorkerConfig(7, "demo", ("XAUUSD",)),
                                    mailbox=mailbox, store_path=tmp_path / "profit.sqlite3")
        try:
            assert (await client.start()).state is LookupState.FOUND
            params = dict(action=action, symbol="XAUUSD", volume=.04, price_open=100., price_close=close)
            response = await client.read(ReadRequest(ReadOperation.PROFIT, params))
            assert response.state is LookupState.FOUND
            assert response.payload["data"] == expected
            assert owner.advance()
            response = await client.read(ReadRequest(ReadOperation.PROFIT, params))
            assert response.state is LookupState.UNKNOWN
            assert response.payload["data"] is None
        finally:
            mailbox.abort()
            try:
                await client.close()
            finally:
                await mailbox.close()
