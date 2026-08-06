# Gold Signals Zone Strategy Farm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline, deterministic strategy farm that reconstructs every robust Gold Signals zone causally, compares entry and DCA policies with verified ticks and account-currency money, and proves the current live baseline before presenting exploratory improvements.

**Architecture:** A catalog adapter produces an immutable causal zone specification. A pure simulator consumes that specification, a verified Bid/Ask tick window and an entry policy; exits reuse the independent replay oracle and money conversion already certified by the project. A farm CLI keeps blocked plans in the denominator, validates observed live zone entries, aggregates risk-aware metrics and never imports live execution modules.

**Tech Stack:** Python 3.11+, dataclasses, pandas/numpy, pytest, existing `simulation_oracle.py`, `broker_money.py` and verified parquet tick contracts.

---

### Task 1: Immutable Zone Policy Catalog

**Files:**
- Create: `zone_entry_policies.py`
- Test: `tests/test_zone_entry_policies.py`

- [ ] **Step 1: Write the failing policy-catalog tests**

```python
from zone_entry_policies import default_zone_entry_policies, zone_policy_by_id


def test_catalog_has_unique_bounded_policies():
    policies = default_zone_entry_policies()
    assert len({policy.policy_id for policy in policies}) == len(policies)
    assert all(policy.total_planned_volume <= 0.05 for policy in policies)


def test_one_plus_four_equal_spans_the_whole_zone():
    policy = zone_policy_by_id("one_plus_four_equal")
    assert policy.depth_fractions == (0.0, 0.25, 0.5, 0.75, 1.0)
    assert policy.order_modes == ("market", "limit", "limit", "limit", "limit")
    assert policy.volumes == (0.01,) * 5
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_zone_entry_policies.py -q`

Expected: FAIL because `zone_entry_policies` does not exist.

- [ ] **Step 3: Implement the immutable policy catalog**

```python
@dataclass(frozen=True)
class ZoneEntryPolicy:
    policy_id: str
    depth_fractions: tuple[float, ...]
    volumes: tuple[float, ...]
    order_modes: tuple[str, ...]
    expiry_mode: str
    activation_latency_ms: int = 0
    market_leg_spacing_ms: int = 125

    @property
    def total_planned_volume(self) -> float:
        return round(sum(self.volumes), 8)


def default_zone_entry_policies() -> tuple[ZoneEntryPolicy, ...]:
    return (
        ZoneEntryPolicy("all_first_touch_live", (0, 0, 0, 0, 0), (0.01,) * 5,
                        ("market",) * 5, "session_end"),
        ZoneEntryPolicy("all_first_touch_causal_expiry", (0, 0, 0, 0, 0),
                        (0.01,) * 5, ("market",) * 5, "provider_progress"),
        ZoneEntryPolicy("one_first_touch", (0,), (0.01,), ("market",),
                        "provider_progress"),
        ZoneEntryPolicy("one_plus_four_equal", (0, .25, .5, .75, 1),
                        (0.01,) * 5,
                        ("market", "limit", "limit", "limit", "limit"),
                        "provider_progress"),
        ZoneEntryPolicy("five_equal_limits", (0, .25, .5, .75, 1),
                        (0.01,) * 5, ("limit",) * 5, "provider_progress"),
        ZoneEntryPolicy("best_half_ladder", (.5, .625, .75, .875, 1),
                        (0.01,) * 5, ("limit",) * 5, "provider_progress"),
        ZoneEntryPolicy("mid_and_best", (.5, 1), (.025, .025),
                        ("limit", "limit"), "provider_progress"),
    )
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m pytest tests/test_zone_entry_policies.py -q`

Expected: all policy tests PASS.

- [ ] **Step 5: Commit**

Run: `git add zone_entry_policies.py tests/test_zone_entry_policies.py && git commit -m "sim: define zone entry policy catalog"`

### Task 2: Causal Zone Specification

**Files:**
- Create: `provider_zone_spec.py`
- Test: `tests/test_provider_zone_spec.py`

- [ ] **Step 1: Write failing tests for causal readiness and no future leakage**

```python
from datetime import datetime, timezone

from provider_zone_spec import build_zone_trade_spec


BASE = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)


def utc(clock):
    return datetime.fromisoformat(f"2026-08-04T{clock}+00:00")


def event(clock, **values):
    return {"observed_ts_utc": utc(clock).isoformat(), **values}


def zone_record(*, ranges, levels):
    return {
        "provider_signal_id": "canal2_9000",
        "channel": "canal2",
        "record_type": "zone_plan",
        "zone_plan_timeline": [event("10:00:00", direction="BUY")],
        "entry_zone_timeline": ranges,
        "level_timeline": levels,
        "management_events": [],
        "execution_batches": [],
    }


def test_zone_becomes_ready_only_when_sl_is_observed():
    record = zone_record(
        ranges=[event("10:00:00", range=[100, 105])],
        levels=[event("10:00:01", tps=[110], sl=None),
                event("10:00:02", tps=[110], sl=95)],
    )
    spec = build_zone_trade_spec(record)
    assert spec.ready_at_utc == utc("10:00:02")
    assert spec.ready_states[0].zone == (100.0, 105.0)
    assert spec.ready_states[0].tps == (110.0,)
    assert spec.ready_states[0].sl == 95.0


def test_later_range_revision_is_ordered_not_backfilled():
    record = zone_record(
        ranges=[event("10:00:00", range=[100, 105]),
                event("10:05:00", range=[98, 103])],
        levels=[event("10:00:00", tps=[110], sl=95)],
    )
    spec = build_zone_trade_spec(record)
    assert [state.zone for state in spec.ready_states] == [
        (100.0, 105.0), (98.0, 103.0)
    ]
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_provider_zone_spec.py -q`

Expected: FAIL because the causal zone adapter does not exist.

- [ ] **Step 3: Implement immutable causal state reconstruction**

```python
@dataclass(frozen=True)
class ZoneState:
    observed_utc: datetime
    direction: str
    zone: tuple[float, float]
    tps: tuple[float, ...]
    sl: float


@dataclass(frozen=True)
class ProviderZoneSpec:
    provider_signal_id: str
    channel: str
    ready_at_utc: datetime | None
    ready_states: tuple[ZoneState, ...]
    management_events: tuple[Mapping[str, object], ...]
    execution_batches: tuple[Mapping[str, object], ...]
    blockers: tuple[str, ...]
    source_sha256: str


def build_zone_trade_spec(record: Mapping[str, object]) -> ProviderZoneSpec:
    """Merge direction, range and TP/SL observations in observed-time order.

    Emit a ready state only after all fields exist and validate BUY/SELL
    geometry at every later revision. Invalid or incomplete records return one
    specification with named blockers rather than disappearing.
    """
```

- [ ] **Step 4: Add geometry, duplicate timestamp and immutability tests**

```python
def test_invalid_buy_geometry_is_named_and_not_dropped():
    record = zone_record(
        ranges=[event("10:00:00", range=[100, 105])],
        levels=[event("10:00:00", tps=[110], sl=102)],
    )
    spec = build_zone_trade_spec(record)
    assert spec.ready_at_utc is None
    assert spec.blockers == ("invalid_buy_zone_geometry",)


def test_equal_timestamps_preserve_source_order_and_spec_is_detached():
    record = zone_record(
        ranges=[event("10:00:00", range=[100, 105]),
                event("10:00:00", range=[99, 104])],
        levels=[event("10:00:00", tps=[110], sl=95)],
    )
    spec = build_zone_trade_spec(record)
    record["entry_zone_timeline"][1]["range"][0] = 1
    assert spec.ready_states[-1].zone == (99.0, 104.0)
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_provider_zone_spec.py -q`

Expected: all causal specification tests PASS.

- [ ] **Step 6: Commit**

Run: `git add provider_zone_spec.py tests/test_provider_zone_spec.py && git commit -m "sim: reconstruct causal provider zones"`

### Task 3: Tick-Exact Zone Fill Engine

**Files:**
- Create: `provider_zone_simulator.py`
- Test: `tests/test_provider_zone_simulator.py`

- [ ] **Step 1: Write failing BUY/SELL fill-side tests**

```python
from datetime import datetime, timedelta, timezone

import pandas as pd

from provider_zone_simulator import simulate_zone_policy
from provider_zone_spec import ProviderZoneSpec, ZoneState
from zone_entry_policies import zone_policy_by_id


BASE = datetime(2026, 8, 4, 10, tzinfo=timezone.utc)


def t(seconds):
    return BASE + timedelta(seconds=seconds)


def ticks(rows):
    return pd.DataFrame(rows, columns=["time_utc", "bid", "ask"])


def state(at, *, zone, direction="BUY", tps=(110,), sl=95):
    return ZoneState(at, direction, tuple(zone), tuple(tps), sl)


def management(at, action):
    return {"observed_ts_utc": at.isoformat(), "classified_action": action}


def buy_spec(*, zone, tps=(110,), sl=95, later_states=(), management=()):
    states = (state(BASE, zone=zone, tps=tps, sl=sl), *later_states)
    return ProviderZoneSpec(
        provider_signal_id="canal2_9000",
        channel="canal2",
        ready_at_utc=BASE,
        ready_states=states,
        management_events=tuple(management),
        execution_batches=(),
        blockers=(),
        source_sha256="0" * 64,
    )


def sell_spec(*, zone, tps=(95,), sl=110):
    states = (state(BASE, zone=zone, direction="SELL", tps=tps, sl=sl),)
    return ProviderZoneSpec(
        provider_signal_id="canal2_9001",
        channel="canal2",
        ready_at_utc=BASE,
        ready_states=states,
        management_events=(),
        execution_batches=(),
        blockers=(),
        source_sha256="1" * 64,
    )


def test_buy_limits_fill_from_ask_at_declared_depths():
    result = simulate_zone_policy(
        buy_spec(zone=(100, 105)),
        ticks([(t(0), 105.1, 105.3), (t(1), 104.7, 104.9),
               (t(2), 102.4, 102.6), (t(3), 99.8, 100.0)]),
        zone_policy_by_id("five_equal_limits"),
        horizon_at=t(10),
    )
    assert [leg["depth_fraction"] for leg in result["filled_legs"]] == [
        0.0, 0.25, 0.5, 0.75, 1.0
    ]
    assert all(leg["touch_side"] == "ask" for leg in result["filled_legs"])


def test_sell_limits_fill_from_bid():
    result = simulate_zone_policy(
        sell_spec(zone=(100, 105)),
        ticks([(t(0), 99.8, 100.0), (t(1), 100.1, 100.3),
               (t(2), 105.0, 105.2)]),
        zone_policy_by_id("five_equal_limits"),
        horizon_at=t(10),
    )
    assert result["filled_legs"][-1]["planned_level"] == 105.0
    assert result["filled_legs"][-1]["touch_side"] == "bid"
```

- [ ] **Step 2: Run fill tests and verify RED**

Run: `python -m pytest tests/test_provider_zone_simulator.py -q`

Expected: FAIL because `simulate_zone_policy` does not exist.

- [ ] **Step 3: Implement activation, fills and revisions**

```python
def simulate_zone_policy(
    spec: ProviderZoneSpec,
    ticks: pd.DataFrame | PreparedTickWindow,
    policy: ZoneEntryPolicy,
    *,
    horizon_at: datetime,
    tick_size: float = 0.01,
    money_converter: BrokerMoneyConverter | None = None,
) -> dict:
    """Apply each state only after its observed timestamp.

    Market legs use the executable quote on first touch plus declared spacing.
    Limit legs fill at their declared level when Ask (BUY) or Bid (SELL)
    crosses it. Unfilled levels are repriced on a causal range revision and
    remain explicit in the result.
    """
```

Use `simulation_oracle.prepare_tick_window` for strict time/quote validation.
For a BUY zone, depth is `(upper - ask) / width`; for SELL it is
`(bid - lower) / width`. Clip only reported penetration to `[0, 1]`; crossing
logic must use the original quote.

- [ ] **Step 4: Write tests for market spacing, revisions and cutoffs**

```python
def test_provider_progress_cancels_only_unfilled_future_entries():
    spec = buy_spec(
        zone=(100, 105),
        management=[management(t(2), "PROGRESS_UPDATE")],
    )
    safe = simulate_zone_policy(
        spec,
        ticks([(t(0), 106, 106.2), (t(1), 105, 105.2),
               (t(3), 99.8, 100)]),
        zone_policy_by_id("one_plus_four_equal"),
        horizon_at=t(10),
    )
    assert safe["fill_cutoff_reason"] == "provider_progress"
    assert safe["filled_leg_count"] == 1


def test_unfilled_levels_reprice_after_causal_zone_revision():
    spec = buy_spec(
        zone=(100, 105),
        later_states=[state(t(2), zone=(98, 103))],
    )
    result = simulate_zone_policy(
        spec,
        ticks([(t(0), 106, 106.2), (t(3), 104, 104.2)]),
        zone_policy_by_id("five_equal_limits"),
        horizon_at=t(10),
    )
    assert result["unfilled_legs"][-1]["planned_level"] == 98.0
```

Verify in separate tests:

```python
assert live_baseline["fill_cutoff_reason"] == "session_end"
assert safe_baseline["fill_cutoff_reason"] == "provider_progress"
assert stale_touch_after_progress["filled_leg_count"] == 0
assert revision_result["unfilled_legs"][0]["planned_level"] == 98.0
```

Also test missing ticks, invalid tick quotes, zero-width zones, deterministic
repeat runs and no mutation of spec/ticks.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_provider_zone_simulator.py -q`

Expected: all fill-engine tests PASS.

- [ ] **Step 6: Commit**

Run: `git add provider_zone_simulator.py tests/test_provider_zone_simulator.py && git commit -m "sim: add tick exact zone fill engine"`

### Task 4: Provider Exits, Money And Basket Excursions

**Files:**
- Modify: `provider_zone_simulator.py`
- Modify: `tests/test_provider_zone_simulator.py`

- [ ] **Step 1: Write failing exit and money tests**

```python
def test_each_filled_leg_uses_provider_tp_and_sl_causally():
    result = simulate_zone_policy(
        buy_spec(zone=(100, 105), tps=(110, 115), sl=95),
        ticks([(t(0), 104.8, 105.0), (t(1), 109.9, 110.1),
               (t(2), 114.9, 115.1)]),
        zone_policy_by_id("mid_and_best"),
        horizon_at=t(10),
    )
    assert [leg["close_reason"] for leg in result["filled_legs"]] == ["tp", "tp"]
    assert result["strategy_pnl"] is None
    assert result["money_status"] == "unverified"
```

Add a fake verified converter and assert the basket P&L equals the rounded sum
of independently converted legs.

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/test_provider_zone_simulator.py -q`

Expected: FAIL because fills do not yet contain closes or money.

- [ ] **Step 3: Reuse the independent close and money contracts**

For each filled leg call:

```python
close = replay_first_close(
    direction=state.direction,
    opened_at=leg.open_time_utc,
    open_price=leg.open_price,
    ticks=prepared_ticks,
    sl_events=sl_events,
    tp_events=tp_events_for_leg,
    horizon_at=horizon_at,
    tick_size=tick_size,
)
money = money_converter.convert_leg(
    direction=state.direction,
    open_price=leg.open_price,
    close_price=close["close_price"],
    volume=leg.volume,
    open_time_utc=leg.open_time_utc,
    close_time_utc=close["close_time_utc"],
) if money_converter else None
```

Map TP indexes by directional distance and repeat the final TP when there are
more filled legs than provider targets, matching current live scale-out.

- [ ] **Step 4: Calculate basket diagnostics**

Add volume-weighted average entry, realized profit-currency P&L, maximum
favorable/adverse floating P&L, giveback, holding time and incremental P&L per
layer. Keep account-currency fields `None` whenever any required money contract
is blocked.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_provider_zone_simulator.py -q`

Expected: all fill, exit, money and excursion tests PASS.

- [ ] **Step 6: Commit**

Run: `git add provider_zone_simulator.py tests/test_provider_zone_simulator.py && git commit -m "sim: price zone exits and basket risk"`

### Task 5: Independent Fill Auditor

**Files:**
- Create: `zone_fill_auditor.py`
- Create: `tests/test_zone_fill_auditor.py`

- [ ] **Step 1: Write the failing independence and agreement tests**

```python
def test_auditor_does_not_import_candidate_simulator():
    source = Path("zone_fill_auditor.py").read_text(encoding="utf-8")
    assert "provider_zone_simulator" not in source


def test_auditor_agrees_on_depth_fill_counts():
    audit = audit_zone_depths(spec, ticks, fractions=(0, .2, .4, .6, .8, 1))
    assert audit["touched_depths"] == [0.0, 0.2, 0.4, 0.6]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_zone_fill_auditor.py -q`

Expected: FAIL because the independent auditor does not exist.

- [ ] **Step 3: Implement a source-order brute-force audit**

The auditor iterates normalized ticks and active causal states directly. It
uses no simulator helper, reports first touch per requested depth and emits the
same named blockers for invalid ticks or incomplete specifications.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_zone_fill_auditor.py -q`

Expected: all auditor tests PASS.

- [ ] **Step 5: Commit**

Run: `git add zone_fill_auditor.py tests/test_zone_fill_auditor.py && git commit -m "sim: independently audit zone penetration"`

### Task 6: Offline Zone Strategy Farm And Baseline Proof

**Files:**
- Create: `zone_strategy_farm.py`
- Create: `tests/test_zone_strategy_farm.py`

- [ ] **Step 1: Write failing farm completeness tests**

```python
def test_farm_emits_one_row_per_plan_and_policy_even_when_blocked(tmp_path):
    report = build_zone_farm_report(catalog, tick_source, policies=policies)
    assert len(report["rows"]) == len(catalog["signals"]) * len(policies)
    assert report["summary"]["blocked_rows"] > 0


def test_observed_baseline_validation_uses_execution_batch():
    proof = validate_observed_baseline(simulated_row, execution_batch)
    assert proof["actual_fill_count"] == 5
    assert proof["simulated_fill_count"] == 5
    assert proof["time_tolerance_ms"] == 3000
    assert proof["price_tolerance"] == 1.0
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_zone_strategy_farm.py -q`

Expected: FAIL because the farm does not exist.

- [ ] **Step 3: Implement verified artifact loading and complete rows**

Use `simulation_oracle.IndependentTickCache` for XAUUSD, `BrokerMoneyConverter`
for EUR results and SHA-256 fingerprints for catalog, tick contracts, money
contract and policy catalog. Never download data or import MT5/live modules.

- [ ] **Step 4: Implement aggregate risk metrics**

For every policy report total plans, simulated/unfilled/blocked counts, fill
rate by depth, net P&L, expectancy, profit factor, maximum drawdown, worst
basket, worst day, return/drawdown and delta from `all_first_touch_live`.
Blocked plans stay in all denominators. Set `selection_status` to
`exploratory_only` until untouched forward days exist.

- [ ] **Step 5: Add the CLI**

Run contract:

```powershell
python zone_strategy_farm.py `
  --catalog C:\path\provider_signal_catalog.json `
  --tick-cache C:\path\ticks_cache `
  --money-contract C:\path\broker_money_contract.json `
  --money-tick-cache C:\path\money_ticks_cache `
  --since 2026-07-29 --until 2026-08-05 `
  --output C:\path\zone_strategy_farm.json
```

- [ ] **Step 6: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_zone_strategy_farm.py -q`

Expected: all farm and baseline-proof tests PASS.

- [ ] **Step 7: Commit**

Run: `git add zone_strategy_farm.py tests/test_zone_strategy_farm.py && git commit -m "sim: build offline zone strategy farm"`

### Task 7: Robust-Window Execution And Regression Gate

**Files:**
- Modify: `README.md`
- Test: existing and newly created test suites
- Output outside Git: `%TEMP%\codex-trading-design-20260806-015658\zone_strategy_farm.json`

- [ ] **Step 1: Run the focused simulator suite**

Run: `python -m pytest tests/test_zone_entry_policies.py tests/test_provider_zone_spec.py tests/test_provider_zone_simulator.py tests/test_zone_fill_auditor.py tests/test_zone_strategy_farm.py -q`

Expected: all tests PASS with no warnings.

- [ ] **Step 2: Run the existing replay and money regression suites**

Run: `python -m pytest tests/test_simulation_oracle.py tests/test_broker_money.py tests/test_provider_strategy_simulator.py tests/test_strategy_farm.py -q`

Expected: all tests PASS.

- [ ] **Step 3: Run the full repository suite**

Run: `python -m pytest -q`

Expected: all non-integration tests PASS; only the repository's documented skip remains.

- [ ] **Step 4: Execute the farm against the read-only robust snapshot**

Use the CLI from Task 6 with `since=2026-07-29` and `until=2026-08-05`.
Verify exactly 41 complete plans are represented, 2026-08-03 remains visibly
blocked by its invalid tick-clock contract, and all five observed August 5
zone baskets receive a baseline proof row.

- [ ] **Step 5: Independently compare depth counts**

Run the auditor for all tick-valid plans and require exact agreement with the
farm at depths `0, 20, 40, 60, 80, 100` percent. Any disagreement blocks the
comparison report.

- [ ] **Step 6: Document honest usage**

Add a short README section stating that the farm is offline, exploratory,
does not modify live behavior, retains blocked zones and cannot promote a
policy without forward/OOS evidence.

- [ ] **Step 7: Verify diff and commit**

Run: `git diff --check`

Run: `git add README.md && git commit -m "docs: explain offline zone strategy research"`
