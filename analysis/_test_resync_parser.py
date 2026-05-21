"""Test del parser de comments para resync."""
import sys, os
sys.path.insert(0, os.getcwd())

from executor import _parse_signal_id_from_comment

cases = [
    # (comment, expected_result)
    ("c1_19236",                ("canal1", 19236)),
    ("c2_12015",                ("canal2", 12015)),
    ("c2_12015_rescue",         ("canal2", 12015)),
    ("DCA_c1_19236_4593.5",     ("canal1", 19236)),
    ("DCA_c2_12015_4570.0",     ("canal2", 12015)),
    ("DCA_4593.5",              None),  # formato viejo, no se puede parsear
    ("",                        None),
    ("random_other",            None),
    ("c3_99",                   None),  # canal inexistente
    ("c1_abc",                  None),  # message_id no numérico
]

print("Test del parser de comments para resync:\n")
ok = fail = 0
for comment, expected in cases:
    got = _parse_signal_id_from_comment(comment)
    if got == expected:
        print(f"  OK   {comment!r:35} -> {got}")
        ok += 1
    else:
        print(f"  FAIL {comment!r:35} -> got {got}, expected {expected}")
        fail += 1

print(f"\n  {ok}/{ok+fail} tests pasan")
sys.exit(0 if fail == 0 else 1)
