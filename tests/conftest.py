"""
conftest.py — Fixtures compartidas para los tests.

Carga env vars dummy ANTES de cualquier import del proyecto, porque
config.py llama a _require() en import time y abortaria si falta una.

Provee samples de mensajes REALES capturados del journal de produccion
(data/trade_events.jsonl) para que los tests reflejen lo que ocurre en
vivo, no casos teoricos.
"""

import os
import sys
from pathlib import Path

# ─── Setup env ANTES de cualquier import del proyecto ───────────────────────
# config.py importa python-dotenv y llama _require() en module-load time.
# Sin estas vars el import explota antes de que pytest pueda hacer nada.

os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "test_hash")
os.environ.setdefault("TELEGRAM_PHONE", "test_telegram_phone")
os.environ.setdefault("CANAL_1_ID", "-1001000000001")
os.environ.setdefault("CANAL_2_ID", "-1001000000002")
os.environ.setdefault("GOOGLE_API_KEY", "test_key")
os.environ.setdefault("MT5_LOGIN", "1")
os.environ.setdefault("MT5_PASSWORD", "test_pwd")
os.environ.setdefault("MT5_SERVER", "TestServer-Demo")

# Asegurar que la raiz del proyecto este en sys.path para que `import parser`,
# `import classifier`, etc. funcionen desde cualquier cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ─── Samples de mensajes REALES del journal ─────────────────────────────────
# Capturados de data/trade_events.jsonl. Sirven para que los tests reflejen
# lo que el bot ve en produccion, no casos sinteticos.

import pytest


# ── Canal 2: mensajes de entrada (sticker BUY/SELL NOW + edits con rango) ──

CANAL2_ENTRY_NEW = "XAU USD BUY NOW"
CANAL2_ENTRY_NEW_SELL = "XAU USD SELL NOW"

# Edit posterior con rango incluido (canal 2 edita el mismo mensaje)
CANAL2_ENTRY_WITH_RANGE = "XAU USD SELL NOW\n\n4585-4590"
CANAL2_ENTRY_WITH_RANGE_BUY = "XAU USD BUY NOW\n\n4638-4633"

# Mensaje completo con rango + TPs + SL (caso de estado final)
CANAL2_FULL_SIGNAL = """XAU USD BUY NOW

4795-4799

TP1: 4801
TP2: 4803
TP3: 4805
TP4: 4807
TP5: 4810
SL: 4791"""


# ── Canal 1: mensajes de texto que llegan tras el sticker ──

CANAL1_TEXT_SELL = """SELL GOLD NOW  4785-90
🎯 TP1: 4780.00
🎯 TP2: 4775.00
🎯 TP3: 4770.00
🎯 TP4: 4765.00
❌ SL: 4798.00"""

CANAL1_TEXT_BUY_SINGLE = """BUY GOLD NOW @4810
🎯 TP1: 4815.00
🎯 TP2: 4820.00
🎯 TP3: 4825.00
🎯 TP4: 4830.00
❌ SL: 4800.00"""

CANAL1_TEXT_RELAXED = "Buy Gold @4700 TP1 4695 SL 4710"


# ── Mensajes de gestion (mgmt_msg del journal) ──
# Cada tupla: (texto_real, accion_esperada_regex, confianza_regex)
# La accion esperada es la PRIMERA accion no-INFORMATIONAL si hay varias.
# Si el regex devuelve solo INFORMATIONAL, se indica.

MGMT_MOVE_SL_TO_PRICE = "I will adjust my stop loss to 4717"

MGMT_MOVE_SL_TO_BE_RISK_FREE = "If you have lower entries keep them risk free."

MGMT_MOVE_SL_TO_BE_TO_BE = "Move your SL to BE"

MGMT_OUT_AT_BE = "I am out of this trade at BE"

MGMT_CLOSE_FIRST = "Close your first entries now"

MGMT_CLOSE_FIRST_AND_MOVE = "Close your first entries and move SL"

MGMT_SINGLE_ENTRY_CLOSE = (
    "Close your first entries now. If you have one entry close it now."
)

MGMT_CLOSE_ALL = "Close all positions"
MGMT_CLOSE_THE_REST = "Close the rest"
MGMT_CLOSE_TRADE = "Close this trade"

MGMT_CLOSE_AT_TP = "Lets close TP1 here"

MGMT_PURE_LEVELS = "TP1 4705.50"
MGMT_PURE_LEVELS_MULTI = "TP1=4688.41 | TP2=4690.41 | TP3=4692.41"
MGMT_PURE_LEVELS_TPSP = "TP1 4705.50 SP 4716.50"  # SP es alias de SL

MGMT_INFO_TP1_HIT = "TP1 HIT"
MGMT_INFO_TP_NUMS = ["TP1 hit", "TP2 hit", "TP3 hit", "TP4 hit", "TP5 hit"]
MGMT_INFO_SL_HIT = "SL hit"
MGMT_INFO_SL_HIT_VARIANTS = [
    "SL hit",
    "SL was hit",
    "SL already hit",
    "SL just hit",
    "SL has been hit",
    "stop loss hit",
    "stop loss reached",
    "stop loss triggered",
    "❌",
]
MGMT_INFO_RUNNING = "Running in profit"
MGMT_INFO_PIPS = "+50 pips secured"


# ── HIGH RISK / RE-ENTER patterns (strategies.py) ──

REENTER_PATTERNS_REAL = [
    "Re-enter Gold here",
    "re entry",
    "Reentry on this level",
    "Enter again at",
    "Another entry at 4700",
    "second entry",
]

HIGH_RISK_PATTERNS_REAL = [
    "High risk trade 🚨",
    "High-risk entry",
    "risky entry",
    "HIGH RISK",
]


# ── Rangos y TPs reales del journal ──

REAL_RANGES_CANAL2 = [
    (4707.5, 4712.0),
    (4700.0, 4704.5),
    (4708.5, 4712.5),
    (4690.0, 4695.0),
    (4685.0, 4690.0),
    (4610.5, 4616.5),
    (4621.5, 4626.5),  # canal2_12161 del 2026-05-06
]

REAL_TPS_CANAL2 = [
    [4705.5],
    [4698.0],
    [4694.0],
    [4572.0],
    [4562.0],
    [4618.5, 4616.5, 4615.0, 4613.0, 4608.0],  # canal2_12161 5 TPs completos
]


# ── Fixtures pytest ──

@pytest.fixture
def fresh_state():
    """StateManager limpio. Importa state.py y resetea el dict global.

    Si tests modifican state.state, este fixture asegura que el siguiente
    test arranca limpio.
    """
    from state import state
    state._signals.clear()
    yield state
    state._signals.clear()


@pytest.fixture
def fresh_strategies_history():
    """Limpia el deque global _recent_sl_hits de strategies.py."""
    import strategies
    strategies._recent_sl_hits.clear()
    yield
    strategies._recent_sl_hits.clear()
