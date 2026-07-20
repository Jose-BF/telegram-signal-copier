"""Small, dependency-free progress output for production pipelines."""

from __future__ import annotations

import sys
import time
from typing import Callable, TextIO


def render_progress(
    current: int,
    total: int,
    label: str,
    *,
    width: int = 20,
    elapsed_s: float | None = None,
) -> str:
    total = max(0, int(total))
    current = max(0, int(current))
    if total:
        current = min(current, total)
        filled = int(width * current / total)
    else:
        current = 0
        filled = 0
    bar = "#" * filled + "-" * max(0, width - filled)
    rendered = f"[{current}/{total}] [{bar}] {label}"
    if elapsed_s is not None:
        rendered += f" | {max(0.0, elapsed_s):.1f}s"
    return rendered


class ProgressReporter:
    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        min_interval_s: float = 0.25,
        width: int = 20,
        clock: Callable[[], float] = time.monotonic,
        interactive: bool | None = None,
    ) -> None:
        self.stream = stream or sys.stdout
        self.min_interval_s = max(0.0, float(min_interval_s))
        self.width = max(1, int(width))
        self.clock = clock
        self.interactive = (
            bool(getattr(self.stream, "isatty", lambda: False)())
            if interactive is None
            else bool(interactive)
        )
        self.started_at: float | None = None
        self.last_emitted_at: float | None = None
        self.last_length = 0

    def update(
        self,
        current: int,
        total: int,
        label: str,
        *,
        force: bool = False,
    ) -> bool:
        now = self.clock()
        if self.started_at is None:
            self.started_at = now
        completed = total > 0 and current >= total
        if (
            not force
            and not completed
            and self.last_emitted_at is not None
            and now - self.last_emitted_at < self.min_interval_s
        ):
            return False

        elapsed = now - self.started_at if completed else None
        line = render_progress(
            current,
            total,
            label,
            width=self.width,
            elapsed_s=elapsed,
        )
        if self.interactive:
            padding = " " * max(0, self.last_length - len(line))
            self.stream.write("\r" + line + padding)
            if completed:
                self.stream.write("\n")
        else:
            self.stream.write(line + "\n")
        self.stream.flush()
        self.last_length = len(line)
        self.last_emitted_at = now
        return True

    def complete(self, current: int, total: int, label: str) -> bool:
        return self.update(current, total, label, force=True)
