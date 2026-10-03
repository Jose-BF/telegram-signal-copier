from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

import gold_trail_live_candidate as gt


def _momentum(mi: int, mi_rev: int) -> gt.Momentum:
    return gt.Momentum(status="ok", mi=mi, mi_rev=mi_rev)


PUB = datetime(2026, 10, 5, 9, 17, 23, tzinfo=timezone.utc)


def test_fingerprints_differ_by_mode_and_ignore_volume() -> None:
    dev = gt.GoldTrailPolicy(mode="desarrollada")
    cand = gt.GoldTrailPolicy(mode="candidata")
    assert dev.fingerprint != cand.fingerprint
    assert gt.GoldTrailPolicy(mode="candidata", live_volume=0.01).fingerprint == cand.fingerprint
    assert gt.policy_for_fingerprint(dev.fingerprint).mode == "desarrollada"
    with pytest.raises(ValueError):
        gt.policy_for_fingerprint("nope")


def test_policy_contract_rejects_changes() -> None:
    with pytest.raises(ValueError):
        gt.GoldTrailPolicy(mode="otra")
    with pytest.raises(ValueError):
        gt.GoldTrailPolicy(provider_management_mode="explicit_close_only")
    with pytest.raises(ValueError):
        gt.GoldTrailPolicy(trail_distance=6.0, trail_activation=5.0)


def test_demo_account_gate() -> None:
    gt.assert_demo_eur_account({"trade_mode": 0, "trade_mode_name": "demo", "currency": "EUR"})
    with pytest.raises(gt.GoldTrailAccountError):
        gt.assert_demo_eur_account({"trade_mode": 2, "trade_mode_name": "real", "currency": "EUR"})


def test_desarrollada_follows_only_with_momentum() -> None:
    policy = gt.GoldTrailPolicy(mode="desarrollada")
    follow = gt.decide(policy, provider_direction="BUY", published_utc=PUB, momentum=_momentum(2, 1), day_losses=set())
    assert (follow.action, follow.side, follow.direction) == ("open", "follow", "BUY")
    against = gt.decide(policy, provider_direction="BUY", published_utc=PUB, momentum=_momentum(1, 3), day_losses=set())
    assert against.action == "skip"
    round_minute = gt.decide(policy, provider_direction="SELL", published_utc=PUB.replace(minute=15),
                             momentum=_momentum(3, 0), day_losses=set())
    assert (round_minute.action, round_minute.direction) == ("open", "SELL")


def test_candidata_reverses_against_momentum_and_skips_round_minutes() -> None:
    policy = gt.GoldTrailPolicy(mode="candidata")
    rev = gt.decide(policy, provider_direction="BUY", published_utc=PUB, momentum=_momentum(1, 2), day_losses=set())
    assert (rev.action, rev.side, rev.direction) == ("open", "reverse", "SELL")
    none = gt.decide(policy, provider_direction="BUY", published_utc=PUB, momentum=_momentum(1, 1), day_losses=set())
    assert none.action == "skip"
    both = gt.decide(policy, provider_direction="SELL", published_utc=PUB, momentum=_momentum(2, 2), day_losses=set())
    assert (both.side, both.direction) == ("follow", "SELL")
    for minute in (0, 5, 30, 55):
        d = gt.decide(policy, provider_direction="BUY", published_utc=PUB.replace(minute=minute),
                      momentum=_momentum(4, 0), day_losses=set())
        assert (d.action, d.reason) == ("skip", "round_minute")
    unavailable = gt.decide(policy, provider_direction="BUY", published_utc=PUB,
                            momentum=gt.Momentum(status="no_ticks"), day_losses=set())
    assert unavailable.reason == "momentum_no_ticks"


def test_day_rule_blocks_only_the_losing_side_that_day() -> None:
    policy = gt.GoldTrailPolicy(mode="candidata")
    losses = {(date(2026, 10, 5), "follow")}
    blocked = gt.decide(policy, provider_direction="BUY", published_utc=PUB, momentum=_momentum(3, 0), day_losses=losses)
    assert (blocked.action, blocked.reason) == ("skip", "day_rule_loss_today")
    other_side = gt.decide(policy, provider_direction="BUY", published_utc=PUB, momentum=_momentum(0, 3),
                           day_losses=losses)
    assert other_side.action == "open"
    next_day = gt.decide(policy, provider_direction="BUY", published_utc=PUB + timedelta(days=1),
                         momentum=_momentum(3, 0), day_losses=losses)
    assert next_day.action == "open"


def test_initial_and_following_stop_buy_and_sell() -> None:
    p = gt.GoldTrailPolicy()
    assert p.initial_stop("BUY", 4200.0) == 4180.0
    assert p.initial_stop("SELL", 4200.0) == 4220.0
    # not active before +5
    assert p.following_stop("BUY", entry=4200.0, best_price=4204.99, executable_price=4204.5,
                            current_stop=4180.0, stops_level=0.2) is None
    # active at +5: best - 3
    assert p.following_stop("BUY", entry=4200.0, best_price=4205.0, executable_price=4205.0,
                            current_stop=4180.0, stops_level=0.2) == 4202.0
    # step of 0.25: an improvement of 0.20 is not sent, 0.25 is
    assert p.following_stop("BUY", entry=4200.0, best_price=4205.2, executable_price=4205.2,
                            current_stop=4202.0, stops_level=0.2) is None
    assert p.following_stop("BUY", entry=4200.0, best_price=4205.25, executable_price=4205.25,
                            current_stop=4202.0, stops_level=0.2) == 4202.25
    # never closer than the stops level to the current price
    assert p.following_stop("BUY", entry=4200.0, best_price=4210.0, executable_price=4207.1,
                            current_stop=4202.0, stops_level=0.2) == 4206.9
    # sell mirror
    assert p.following_stop("SELL", entry=4200.0, best_price=4195.0, executable_price=4195.0,
                            current_stop=4220.0, stops_level=0.2) == 4198.0
    assert p.following_stop("SELL", entry=4200.0, best_price=4194.9, executable_price=4194.9,
                            current_stop=4198.0, stops_level=0.2) is None


def test_time_exit_is_life_or_before_the_daily_break() -> None:
    p = gt.GoldTrailPolicy()
    fill = datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)
    close = datetime(2026, 10, 5, 20, 58, tzinfo=timezone.utc)
    assert p.time_exit_at(fill, close) == fill + timedelta(hours=6)
    late = datetime(2026, 10, 5, 17, 0, tzinfo=timezone.utc)
    assert p.time_exit_at(late, close) == close - timedelta(seconds=120)
    assert p.time_exit_at(fill, None) == fill + timedelta(hours=6)


def test_market_comment_is_short_and_marked() -> None:
    assert gt.market_comment(3850) == "c2_3850_gtr"
    assert len(gt.market_comment(99999999)) <= 31


def test_compute_momentum_pieces_buy() -> None:
    t0 = int(datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc).timestamp() * 1000)
    tick_t = np.arange(t0 - 20 * 60_000, t0, 10_000, dtype=np.int64)
    tick_mid = np.linspace(4190.0, 4200.0, len(tick_t))
    day_start = t0 - 12 * 3_600_000                      # broker midnight 9 h earlier (UTC+3)
    prev_start = day_start - 86_400_000
    bars_t = np.arange(prev_start, t0, 60_000, dtype=np.int64)
    close = np.full(len(bars_t), 4150.0)
    close[bars_t >= day_start] = 4195.0
    high, low = close + 1.0, close - 1.0
    high[(bars_t >= prev_start) & (bars_t < day_start)] = 4198.0   # previous day range 4149-4198
    m = gt.compute_momentum(sign=1, t0_ms=t0, tick_t_ms=tick_t, tick_mid=tick_mid, day_open_mid=4180.0,
                            bars_t_ms=bars_t, bars_h=high, bars_l=low, bars_c=close,
                            broker_day_start_ms=day_start, prev_broker_day_start_ms=prev_start)
    assert m.status == "ok"
    assert m.day_move == 20.0
    assert m.pre_move_15 is not None and m.pre_move_15 > 0
    assert m.pd_loc is not None and m.pd_loc > 0.6
    assert m.mi >= 3
    m_sell = gt.compute_momentum(sign=-1, t0_ms=t0, tick_t_ms=tick_t, tick_mid=tick_mid, day_open_mid=4180.0,
                                 bars_t_ms=bars_t, bars_h=high, bars_l=low, bars_c=close,
                                 broker_day_start_ms=day_start, prev_broker_day_start_ms=prev_start)
    assert m_sell.mi_rev == m.mi


def test_compute_momentum_needs_ticks() -> None:
    t0 = 1_790_000_000_000
    m = gt.compute_momentum(sign=1, t0_ms=t0, tick_t_ms=np.array([t0 - 1000]), tick_mid=np.array([4200.0]),
                            day_open_mid=None, bars_t_ms=np.zeros(0), bars_h=np.zeros(0), bars_l=np.zeros(0),
                            bars_c=np.zeros(0), broker_day_start_ms=t0, prev_broker_day_start_ms=None)
    assert m.status == "no_ticks" and m.mi == -1
