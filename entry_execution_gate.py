"""Single authority for turning one Telegram message into market exposure."""

from __future__ import annotations

from collections import deque
from threading import Lock


class EntryExecutionGate:
    """Bounded process-local claim registry keyed by channel and message ID."""

    def __init__(self, *, max_committed: int = 1000):
        if max_committed < 1:
            raise ValueError("max_committed must be positive")
        self._max_committed = int(max_committed)
        self._opening: set[tuple[str, int]] = set()
        self._committed: set[tuple[str, int]] = set()
        self._committed_order: deque[tuple[str, int]] = deque()
        self._lock = Lock()

    @staticmethod
    def _key(channel: str, message_id: int) -> tuple[str, int]:
        return str(channel), int(message_id)

    def claim(self, channel: str, message_id: int) -> bool:
        key = self._key(channel, message_id)
        with self._lock:
            if key in self._opening or key in self._committed:
                return False
            self._opening.add(key)
            return True

    def commit(self, channel: str, message_id: int) -> None:
        key = self._key(channel, message_id)
        with self._lock:
            self._opening.discard(key)
            if key in self._committed:
                return
            self._committed.add(key)
            self._committed_order.append(key)
            while len(self._committed_order) > self._max_committed:
                self._committed.discard(self._committed_order.popleft())

    def release(self, channel: str, message_id: int) -> None:
        key = self._key(channel, message_id)
        with self._lock:
            self._opening.discard(key)

    def in_progress(self, channel: str, message_id: int) -> bool:
        key = self._key(channel, message_id)
        with self._lock:
            return key in self._opening

    def committed(self, channel: str, message_id: int) -> bool:
        key = self._key(channel, message_id)
        with self._lock:
            return key in self._committed

    def reset(self) -> None:
        with self._lock:
            self._opening.clear()
            self._committed.clear()
            self._committed_order.clear()
