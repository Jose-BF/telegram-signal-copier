"""Minimal executable entrypoint for the isolated MT5 read worker."""

from __future__ import annotations

import os
import sys


def main() -> int:
    from mt5_worker_lifetime import start_parent_guard

    guard = start_parent_guard(int(sys.argv[1]), sys.argv[2])

    def check_guard():
        if guard.poll() is not None:
            raise OSError("MT5 parent guard exited")

    # Preserve the inherited protocol pipe on a private descriptor, then move
    # stdout itself to stderr so Python and native writes cannot corrupt JSON.
    protocol_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    protocol_output = os.fdopen(protocol_fd, "wb", buffering=0)
    sys.stdout = sys.stderr
    sys.__stdout__ = sys.stderr
    from mt5_worker import worker_stream_main
    try:
        return worker_stream_main(sys.stdin.buffer, protocol_output, lifetime_check=check_guard)
    finally:
        protocol_output.close()


if __name__ == "__main__":
    raise SystemExit(main())
