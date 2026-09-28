"""Frozen pre-refactor results and resumable scalar replay controls."""

from dataclasses import asdict, replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest

from research.dubai_iterative import engine
from research.dubai_iterative.client import ClientProfile
from research.dubai_iterative.dataset import ProviderEvent
from tests.test_iterative_market import market
from tests.test_iterative_protection import BASE, path, policy, profile


def replay_cases():
    cases = {}
    for direction in ("BUY", "SELL"):
        for kind in ("plain", "protection", "market", "client"):
            for scenario in ("target", "stop", "provider", "pending", "batch", "duplicates"):
                quotes = [100.] * 5 + [96.] + [100.] * 6 + [101.] * 12
                if scenario == "stop":
                    quotes = [100.] * 3 + [69.] * 16
                elif scenario == "pending":
                    quotes = [100.]
                offsets = list(range(len(quotes)))
                if scenario == "duplicates":
                    offsets[6] = offsets[5]
                if direction == "SELL":
                    quotes = [200. - quote for quote in quotes]
                tape = path(quotes, direction=direction, offsets=offsets)
                strategy = policy()
                if scenario == "batch":
                    strategy = policy(leg_count=3, volume_weights=(.04,) * 3,
                                      target_steps=(20.,) * 3,
                                      entry_ladder_mode="adverse", entry_ladder_step=1.5)
                if scenario == "provider":
                    tape = replace(tape, provider_events=(
                        ProviderEvent(BASE + timedelta(seconds=8), "CLOSE_ALL", {}),))
                    strategy = strategy.with_change(provider_management_mode="explicit_close_only")
                execution = engine.ExecutionAssumptions(
                    entry_fill_latency_ms=1000 if scenario == "pending" else 0,
                    protection=profile() if kind != "plain" else None,
                    market=market() if kind in {"market", "client"} else None,
                    client=ClientProfile() if kind == "client" else None,
                )
                cases[f"{direction}-{kind}-{scenario}"] = (tape, strategy, execution)
    return cases


def result_digest(result):
    return hashlib.sha256(json.dumps(asdict(result), sort_keys=True,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


CASES = replay_cases()
REFERENCE = Path(__file__).parent / "fixtures" / "iterative_resume_reference.json"


@pytest.mark.parametrize("name", CASES)
def test_scalar_result_matches_frozen_pre_refactor_reference(name):
    tape, strategy, execution = CASES[name]
    result = engine.simulate(tape, strategy, execution=execution)
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    assert result_digest(result) == reference["results"][name], asdict(result)


def drain(steps):
    boundaries = []
    while True:
        try:
            boundaries.append(next(steps))
        except StopIteration as finished:
            return boundaries, finished.value


@pytest.mark.parametrize("name", CASES)
def test_suspended_replay_preserves_entire_result_and_tick_order(name):
    tape, strategy, execution = CASES[name]
    steps = engine._simulation_steps(tape, strategy, execution=execution)
    boundaries, result = drain(steps)
    assert result_digest(result) == json.loads(REFERENCE.read_text(encoding="utf-8"))["results"][name]
    assert [step.tick_index for step in boundaries] == list(range(len(boundaries)))
    assert [step.time_ns for step in boundaries] == list(tape.times_ns[:len(boundaries)])


def test_two_paused_replays_do_not_share_state_or_ticket_identity():
    selected = [CASES["BUY-client-batch"], CASES["SELL-client-stop"]]
    streams = [engine._simulation_steps(tape, strategy, execution=execution)
               for tape, strategy, execution in selected]
    outputs = {}
    while len(outputs) < len(streams):
        for index, steps in enumerate(streams):
            if index in outputs:
                continue
            try:
                next(steps)
            except StopIteration as finished:
                outputs[index] = finished.value
    for index, (tape, strategy, execution) in enumerate(selected):
        assert outputs[index] == engine.simulate(tape, strategy, execution=execution)


def test_blocked_contract_returns_without_consuming_a_tick():
    tape, strategy, execution = CASES["BUY-client-target"]
    tape = replace(tape, direction="INVALID")
    boundaries, result = drain(engine._simulation_steps(tape, strategy, execution=execution))
    assert not boundaries
    assert "invalid_path_direction" in result.blockers


def test_abandoned_replay_does_not_synthesize_an_exit_or_finish(monkeypatch):
    tape, strategy, execution = CASES["BUY-client-target"]
    steps = engine._simulation_steps(tape, strategy, execution=execution)
    next(steps)

    def forbidden(*args, **kwargs):
        pytest.fail("closing a suspended generator must not execute another tick or finalize it")

    monkeypatch.setattr(engine, "_tick_is_usable", forbidden)
    monkeypatch.setattr(engine, "SimulationResult", forbidden)
    steps.close()
