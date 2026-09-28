import numpy as np
import pytest

from research.basket_grid import BasketRule, Paths, evaluate
from research.bracket_grid import SimpleRule, simple_outcomes
from research.signal_paths import NS, products, second_path

T0 = 1_700_000_000 * NS
SPREAD = 0.2


def paths_from(tapes, directions, fx=1.0, cutoff_s=3600):
    parts, zrows = [], []
    for pts in tapes:
        t = np.array([T0 + int(s * NS) for s, _ in pts], dtype=np.int64)
        b = np.array([p for _, p in pts], dtype=float)
        p = second_path(t, b, b + SPREAD, T0, T0 + cutoff_s * NS, latency_ms=0)
        s0 = p.ask0 - p.bid0
        parts.append((p.bid_hi, p.bid_lo, p.bid_last, p.ask_hi - s0, p.ask_lo - s0, p.ask_last - s0))
        zrows.append((p, products(p)))
    lens = [len(x[0]) for x in parts]
    offsets = np.concatenate([[0], np.cumsum(lens)]).astype(np.int64)
    cat = [np.concatenate([x[j] for x in parts]).astype(np.float32) for j in range(6)]
    n = len(tapes)
    P = Paths(np.array([f"s{i}" for i in range(n)]), np.array(["canal2"] * n),
              np.array([1 if d == 0 else -1 for d in directions], dtype=np.int8), np.array(["2026-05-01"] * n),
              np.full(n, SPREAD), np.full(n, fx), offsets, *cat)
    st = lambda k: np.stack([pr[k] for _, pr in zrows])
    z = {"ids": P.ids, "direction": np.array(directions, dtype=np.int8), "fx": np.full(n, fx),
         "n_seconds": np.array(lens), "fp_fav": st("fp_fav"), "fp_adv": st("fp_adv"),
         "exit_val": st("exit_val"), "end_val": st("end_val")}
    return P, z


TAPES = [
    [(0, 100.0), (10, 103.2), (20, 97.2)],            # up 3 then down 6
    [(0, 100.0), (5, 98.0), (15, 95.0), (30, 104.0)],  # down 5 then up
    [(0, 100.0), (60, 100.5), (400, 99.0)],            # quiet
]


@pytest.mark.parametrize("direction", [0, 1])
def test_single_leg_matches_simple_calculator(direction):
    P, z = paths_from(TAPES, [direction] * 3, fx=1.1)
    for tp, sl, inv in ((3.0, 3.0, False), (1.0, 5.0, False), (2.0, 2.0, True)):
        simple = simple_outcomes(z, [SimpleRule(inv, tp, sl, None)])[:, 0]
        basket = evaluate(P, [BasketRule(invert=inv, weights=(0.01,), tp_steps=(tp,), sl_move=sl)])["pnl"][:, 0]
        assert np.allclose(simple, basket, atol=1e-6), (tp, sl, inv, simple, basket)


def test_adverse_ladder_fills_and_per_leg_targets():
    # BUY at ask 100.2; ask falls to 98.2 (legs at 99.2 and 98.2), then bid rebounds to 104
    P, _ = paths_from([TAPES[1]], [0])
    rule = BasketRule(weights=(0.01, 0.02, 0.03), step=1.0, tp_steps=(0.5, 0.5, 0.5), expiry_min=30)
    out = evaluate(P, [rule])
    assert out["legs"][0, 0] == 3
    # every leg closes at its own +0.5 -> 0.5 * (1 + 2 + 3) = 3.0 EUR at fx 1
    assert np.isclose(out["pnl"][0, 0], 3.0)
    # lowest equity at the 95.0 bid: legs at 100.2, 99.2, 98.2 -> -(5.2*1 + 4.2*2 + 3.2*3)
    assert np.isclose(out["min_equity"][0, 0], -(5.2 + 8.4 + 9.6))


def test_basket_cap_closes_at_threshold():
    P, _ = paths_from([TAPES[1]], [0])
    out = evaluate(P, [BasketRule(weights=(0.01, 0.02, 0.03), step=1.0, tp_steps=(0.5,), basket_stop_eur=10.0)])
    assert np.isclose(out["pnl"][0, 0], -10.0)


def test_adverse_reversal_entry_waits_for_drop_and_rebound():
    # 555-style: 2 against, then 3 rebound from the low (ask low 95.2 -> condition at ask >= 98.2, seen
    # at 30 s); the market order fills on the next second's quote (ask 104.2), target +1 at bid 105.2
    tape = TAPES[1] + [(40, 106.0)]
    P, _ = paths_from([tape], [0])
    out = evaluate(P, [BasketRule(entry="adverse_reversal", e_x=2.0, e_y=3.0, weights=(0.01,), tp_steps=(1.0,),
                                  expiry_min=30)])
    assert out["legs"][0, 0] == 1 and np.isclose(out["pnl"][0, 0], 1.0)
    assert out["min_equity"][0, 0] < 0   # entered at 104.2, bid 104.0 right after
    # never rebounds enough -> not filled
    out = evaluate(P, [BasketRule(entry="adverse_reversal", e_x=2.0, e_y=20.0, weights=(0.01,), tp_steps=(1.0,))])
    assert out["legs"][0, 0] == 0 and out["pnl"][0, 0] == 0.0


def test_time_exit_modes():
    P, _ = paths_from([TAPES[2]], [0])
    # quiet tape: after 5 min the basket is at 100.5 - 100.2 = +0.3 -> loss_only keeps it, always closes it
    keep = evaluate(P, [BasketRule(weights=(0.01,), time_min=5, time_mode="loss_only")])
    close = evaluate(P, [BasketRule(weights=(0.01,), time_min=5, time_mode="always")])
    assert np.isclose(close["pnl"][0, 0], 0.3)
    # loss_only keeps watching once due (as the engine): it closes when the basket turns negative at 400 s
    assert not keep["censored"][0, 0] and np.isclose(keep["pnl"][0, 0], 99.0 - 100.2)
    # no time exit at all -> held to the cutoff and marked to market
    hold = evaluate(P, [BasketRule(weights=(0.01,))])
    assert hold["censored"][0, 0] and np.isclose(hold["pnl"][0, 0], 99.0 - 100.2)
