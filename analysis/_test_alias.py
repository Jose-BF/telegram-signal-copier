"""Test del fix de alias: confirma que un signal registrado bajo sticker_id
es accesible bajo el text_id tras llamar a state.alias()."""
import sys, os
sys.path.insert(0, os.getcwd())

from state import StateManager, Signal

s = StateManager()

# 1. Sticker llega -> bot crea Signal con sticker_id
sig = Signal(channel="canal1", message_id=19192, direction="BUY")
s.add(sig)

# 2. Verificar que se accede por sticker_id
got_by_sticker = s.get("canal1", 19192)
assert got_by_sticker is sig, f"FAIL: not accessible by sticker_id"
print(f"  state.get('canal1', 19192)  ->  signal #{got_by_sticker.message_id}  OK")

# 3. Antes del alias: text_id NO accede al signal
got_by_text_before = s.get("canal1", 19193)
assert got_by_text_before is None, f"FAIL: should be None"
print(f"  state.get('canal1', 19193)  ->  None (antes del alias)  OK")

# 4. Llamar alias: el text_id 19193 ahora apunta al mismo signal
s.alias(sig, 19193)

# 5. Después del alias: text_id devuelve el MISMO signal
got_by_text_after = s.get("canal1", 19193)
assert got_by_text_after is sig, f"FAIL: alias not working"
print(f"  state.alias(sig, 19193) + state.get('canal1', 19193)  ->  signal #{got_by_text_after.message_id}  OK")
assert got_by_text_after is got_by_sticker, "FAIL: not the same object"
print(f"  Mismo objeto bajo ambos IDs (mutaciones compartidas)  OK")

# 6. Cambios al signal son visibles bajo cualquier alias
sig.status = "closed"
assert s.get("canal1", 19192).status == "closed", "FAIL"
assert s.get("canal1", 19193).status == "closed", "FAIL"
print(f"  Mutación visible bajo ambos IDs  OK")

# 7. latest_open no duplica (mismo objeto en dos keys, debería contar 1)
sig2 = Signal(channel="canal1", message_id=20000, direction="SELL")
s.add(sig2)
# sig está cerrada, sig2 abierta -> latest_open canal1 debe ser sig2
latest = s.latest_open("canal1")
assert latest is sig2, "FAIL"
print(f"  latest_open ignora cerradas y devuelve la abierta correcta  OK")

print(f"\n  TODOS LOS TESTS PASAN  OK")
