"""Test classifier + parser on real messages extracted from apr27 JSONL."""
import sys, io, os
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import re as _re
from classifier import _regex_classify_all
from parser import parse_canal2

tests = [
    # (msg, expected_actions, expected_parsed_keys)
    ("TP1 4705.50  SP 4716.50",         ["INFORMATIONAL"],         ["tps","sl"]),
    ("TP1 4705.50  SL 4716.50",         ["INFORMATIONAL"],         ["tps","sl"]),
    ("TP1 4705.50",                     ["INFORMATIONAL"],         ["tps"]),
    ("TP1=4688.41 | TP2=4690.41",       ["INFORMATIONAL"],         ["tps"]),
    ("To protect your capital close your first entries now.",
                                         ["CLOSE_FIRST"],           []),
    ("To protect your capital close your first entries now.   If you have one entry close it now.",
                                         ["CLOSE_FIRST"],           []),
    ("close first entry",                ["CLOSE_FIRST"],           []),
    ("close the first position",        ["CLOSE_FIRST"],           []),
    ("close the early entries",         ["CLOSE_FIRST"],           []),
    ("close all positions",             ["CLOSE_ALL"],             []),
    ("close all",                        ["CLOSE_ALL"],             []),
    ("SL already hit",                   ["INFORMATIONAL"],         []),
    ("SL ALREADY HIT",                   ["INFORMATIONAL"],         []),
    ("SL was hit",                       ["INFORMATIONAL"],         []),
    ("Stop loss has been hit",           ["INFORMATIONAL"],         []),
    ("I will adjust my stop loss to 4717",
                                         ["MOVE_SL_TO_PRICE"],      []),
    ("Sl edited",                        ["INFORMATIONAL"],         []),
    ("TP1 HIT",                          ["INFORMATIONAL"],         []),
    ("TP1 4698  SL 4687",                ["INFORMATIONAL"],         ["tps","sl"]),
    ("Move SL to BE",                    ["MOVE_SL_TO_BE"],         []),
    ("Close TP3 here",                   ["CLOSE_AT_TP"],           []),
]

print(f"{'mensaje':<70} {'actions OK':<10} {'parser OK':<10}")
print("-" * 100)
ok_acts = ok_pars = 0
for t, exp_acts, exp_keys in tests:
    actions = _regex_classify_all(t)
    got_acts = [a["action"] for a in actions]
    parsed = parse_canal2(t)
    got_keys = sorted(parsed.keys())
    a_ok = got_acts == exp_acts
    # Para parser solo nos importa que las keys esperadas estén; "direction"
    # puede aparecer extra en mensajes con BUY/SELL embebidos.
    p_ok = all(k in got_keys for k in exp_keys)
    if a_ok: ok_acts += 1
    if p_ok: ok_pars += 1
    a_mark = "OK" if a_ok else f"FAIL got={got_acts}"
    p_mark = "OK" if p_ok else f"FAIL exp={exp_keys} got={got_keys}"
    print(f"{t[:68]!r:<70} {a_mark:<10} {p_mark:<10}")

print("-" * 100)
print(f"  Classifier: {ok_acts}/{len(tests)}   Parser: {ok_pars}/{len(tests)}")
