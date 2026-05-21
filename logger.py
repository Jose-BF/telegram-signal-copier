import csv
import os
from datetime import datetime

import journal  # reutilizamos su _test_context para detectar test mode
from state import Signal

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

SIGNALS_CSV = os.path.join(LOG_DIR, "signals.csv")
ACTIONS_CSV = os.path.join(LOG_DIR, "actions.csv")
# Ficheros separados para señales del canal de pruebas
SIGNALS_TEST_CSV = os.path.join(LOG_DIR, "signals_TEST.csv")
ACTIONS_TEST_CSV = os.path.join(LOG_DIR, "actions_TEST.csv")


def _append(path: str, row: list):
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


def _is_test_signal(signal: Signal) -> bool:
    """True si esta señal viene del canal de pruebas (contextvar o registry)."""
    sig_id = f"{signal.channel}_{signal.message_id}"
    return journal.is_test_signal(sig_id) or journal.is_test_mode()


def log_signal(signal: Signal, parsed: dict):
    is_test = _is_test_signal(signal)
    target = SIGNALS_TEST_CSV if is_test else SIGNALS_CSV
    _append(target, [
        datetime.utcnow().isoformat(),
        signal.channel,
        signal.message_id,
        signal.direction,
        parsed.get("range", ""),
        parsed.get("sl", ""),
        ",".join(str(t) for t in parsed.get("tps", [])),
        signal.market_ticket,
    ])
    suffix = " [TEST]" if is_test else ""
    print(f"[LOG]{suffix} Señal registrada: {signal.channel} {signal.direction} msg={signal.message_id}")


def log_action(signal: Signal, action: str, price=None):
    is_test = _is_test_signal(signal)
    target = ACTIONS_TEST_CSV if is_test else ACTIONS_CSV
    _append(target, [
        datetime.utcnow().isoformat(),
        signal.channel,
        signal.message_id,
        action,
        price if price is not None else "",
    ])
    suffix = " [TEST]" if is_test else ""
    print(f"[LOG]{suffix} Acción: {action} | señal={signal.message_id} | precio={price}")
