"""Test del smart TP allocation contextual."""
import sys, os
sys.path.insert(0, os.getcwd())

from state import Signal, TradeContext

# ── Test 1: SMART CONTEXTUAL — 1 posición -> market apunta a TP2 ──
sig = Signal(channel="canal2", message_id=1, direction="SELL")
sig.tps = [4570.0, 4568.0, 4566.0, 4564.0, 4562.0]
sig.sl = 4585.0
sig.market_ticket = 12345  # ya hay 1 posición

# Con n_total_open=1, el market (idx=0) debe apuntar a TP2 (4568.0), no TP1 (4570.0)
tp = sig.tp_for_position(0, n_total_open=1)
assert tp == 4568.0, f"FAIL: esperado 4568.0 (TP2 por smart), got {tp}"
print(f"  TEST 1 OK  1 pos -> market a TP2={tp}")

# ── Test 2: 2 posiciones -> escalonado (market->TP1, DCA->TP2) ──
sig2 = Signal(channel="canal2", message_id=2, direction="SELL")
sig2.tps = [4570.0, 4568.0, 4566.0, 4564.0, 4562.0]
sig2.market_ticket = 12345
sig2.dca_tickets = [99999]

tp_market = sig2.tp_for_position(0, n_total_open=2)
tp_dca1 = sig2.tp_for_position(1, n_total_open=2)
assert tp_market == 4570.0, f"FAIL market: esperado TP1=4570, got {tp_market}"
assert tp_dca1 == 4568.0, f"FAIL DCA1: esperado TP2=4568, got {tp_dca1}"
print(f"  TEST 2 OK  2 pos -> market a TP1={tp_market}, DCA1 a TP2={tp_dca1}")

# ── Test 3: 5 posiciones -> escalonado completo ──
sig3 = Signal(channel="canal2", message_id=3, direction="SELL")
sig3.tps = [4570.0, 4568.0, 4566.0, 4564.0, 4562.0]

for i, expected in enumerate([4570.0, 4568.0, 4566.0, 4564.0, 4562.0]):
    tp_i = sig3.tp_for_position(i, n_total_open=5)
    assert tp_i == expected, f"FAIL pos{i}: esperado {expected}, got {tp_i}"
print(f"  TEST 3 OK  5 pos -> escalonado completo TP1..TP5")

# ── Test 4: BACKWARD COMPAT — sin n_total_open, comportamiento legacy ──
sig4 = Signal(channel="canal1", message_id=4, direction="BUY")
sig4.tps = [4500.0, 4502.0, 4504.0, 4506.0]
# Sin n_total_open: pos 0 -> TP1
tp = sig4.tp_for_position(0)
assert tp == 4500.0, f"FAIL legacy: esperado TP1=4500, got {tp}"
print(f"  TEST 4 OK  legacy mode (sin n_total_open) -> pos 0 a TP1={tp}")

# ── Test 5: target_tp_index sigue overridando smart ──
sig5 = Signal(channel="canal1", message_id=5, direction="SELL")
sig5.tps = [4570.0, 4568.0, 4566.0, 4564.0]
sig5.target_tp_index = 2  # todas a TP3
# Aún con n_total_open=1, debería ir a TP3 (no TP2)
tp = sig5.tp_for_position(0, n_total_open=1)
assert tp == 4566.0, f"FAIL target override: esperado TP3=4566, got {tp}"
print(f"  TEST 5 OK  target_tp_index=2 overridee smart -> TP3={tp}")

# ── Test 6: BE trigger sigue siendo TP1 (idx=0) ──
sig6 = Signal(channel="canal2", message_id=6, direction="SELL")
sig6.tps = [4570.0, 4568.0, 4566.0]
sig6.be_at_tp_index = 0
be = sig6.be_trigger_price()
assert be == 4570.0, f"FAIL BE trigger: esperado TP1=4570, got {be}"
print(f"  TEST 6 OK  BE trigger en TP1={be}")

print(f"\n  TODOS LOS TESTS PASAN")
