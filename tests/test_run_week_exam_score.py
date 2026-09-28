import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("rwe", Path(__file__).resolve().parents[1] / "tools" / "run_week_exam.py")


def _score():
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.score


def _sim(net, dd=1.0):
    return {"channel": "canal1", "net_eur": net, "dd_eur": dd, "max_volume": "0.01", "exposed": True}


def test_real_basket_with_blocked_path_is_not_sim_only():
    score = _score()
    real = {"canal1_1": {"net_eur": 10.0, "dd_eur": 5.0, "max_volume": "0.01"}}
    results = [{"profile": "P", "status": "diagnostic_only", "baskets": {"canal1_1": _sim(9.0, 4.0), "canal1_2": _sim(-4.0),
                                                                        "canal1_3": _sim(2.0)}}]
    per, _, _ = score(real, results, ["P"], covered={"canal1_1", "canal1_2", "canal1_3"},
                      real_net_only={"canal1_2": -4.47})
    p = per["P"]
    assert p["sim_only"] == ["canal1_3"]
    assert p["matched_net_only_real_path_blocked"] == ["canal1_2"]
    assert p["real_net_eur"] == round(10.0 - 4.47, 2)
    assert p["sim_net_eur"] == round(9.0 - 4.0 + 2.0, 2)
    assert p["matched_real_dd_sum"] == 5.0 and p["matched_sim_dd_sum"] == 4.0


def test_blocked_real_basket_missing_in_sim_is_reported():
    score = _score()
    per, _, _ = score({}, [{"profile": "P", "status": "diagnostic_only", "baskets": {}}], ["P"],
                      covered={"canal1_2"}, real_net_only={"canal1_2": -4.47})
    assert per["P"]["real_path_blocked_not_simulated"] == ["canal1_2"]
