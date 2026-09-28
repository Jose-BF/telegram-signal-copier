import pandas as pd

from tools.audit_isolated_mt5_extract import compare_sequence


def test_exact_sequence_requires_timestamps_and_both_quote_sides():
    old = pd.DataFrame({"source_time_msc": [1000, 1000, 1001], "bid": [1., 1.1, 1.2], "ask": [1.1, 1.2, 1.3]})
    new = old.rename(columns={"source_time_msc": "time_msc"}).copy()
    assert compare_sequence(old, new)["ordered_bid_ask_identical"]
    new.loc[1, "ask"] += .1
    assert not compare_sequence(old, new)["ordered_bid_ask_identical"]
    new = new.iloc[[1, 0, 2]].reset_index(drop=True)
    assert not compare_sequence(old, new)["ordered_bid_ask_identical"]
    assert not compare_sequence(old, new.iloc[:2])["source_times_identical"]


def test_source_clock_shift_is_not_silently_tolerated():
    old = pd.DataFrame({"source_time_msc": [1000], "bid": [1.], "ask": [1.1]})
    new = pd.DataFrame({"time_msc": [1000 + 3600000], "bid": [1.], "ask": [1.1]})
    assert not compare_sequence(old, new)["source_times_identical"]
