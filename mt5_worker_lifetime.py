"""Independent parent-death guard; never terminates a process by a stale PID."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import time


class ProcessReference:
    """Pin an OS process object before checking identity or terminating it."""

    def __init__(self, pid: int, identity: str | None = None, *, can_kill=False):
        self.handle = None
        if pid <= 0:
            raise ValueError("invalid process PID")
        try:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes

                self._ctypes = ctypes
                self._api = api = ctypes.WinDLL("kernel32", use_last_error=True)
                api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                api.OpenProcess.restype = wintypes.HANDLE
                api.CloseHandle.argtypes = [wintypes.HANDLE]
                api.CloseHandle.restype = wintypes.BOOL
                api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
                api.GetProcessTimes.restype = wintypes.BOOL
                api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                api.WaitForSingleObject.restype = wintypes.DWORD
                api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
                api.TerminateProcess.restype = wintypes.BOOL
                # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION; only the
                # worker reference receives PROCESS_TERMINATE.
                self.handle = api.OpenProcess(0x100000 | 0x1000 | int(can_kill), False, pid)
                if not self.handle:
                    raise ctypes.WinError(ctypes.get_last_error())
                stamps = [wintypes.FILETIME() for _ in range(4)]
                if not api.GetProcessTimes(self.handle, *(ctypes.byref(stamp) for stamp in stamps)):
                    raise ctypes.WinError(ctypes.get_last_error())
                self.identity = str((stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime)
            elif sys.platform == "linux" and hasattr(os, "pidfd_open"):
                import psutil
                import select

                self.handle = os.pidfd_open(pid)
                self._poll = select.poll()
                self._poll.register(self.handle, select.POLLIN)
                self.identity = repr(psutil.Process(pid).create_time())
            else:
                raise OSError("safe process references unavailable on this platform")
            if identity is not None and self.identity != identity:
                raise ValueError("process creation identity mismatch")
            if not self.is_alive():
                raise ProcessLookupError("process already exited")
        except BaseException:
            self.close()
            raise

    def is_alive(self) -> bool:
        if os.name == "nt":
            result = self._api.WaitForSingleObject(self.handle, 0)
            if result == 0xFFFFFFFF:
                raise self._ctypes.WinError(self._ctypes.get_last_error())
            return result == 0x102  # WAIT_TIMEOUT
        return not self._poll.poll(0)

    def kill(self) -> None:
        if not self.is_alive():
            return
        if os.name == "nt":
            if not self._api.TerminateProcess(self.handle, 77) and self.is_alive():
                raise self._ctypes.WinError(self._ctypes.get_last_error())
        else:
            import signal

            try:
                signal.pidfd_send_signal(self.handle, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle is not None:
            if os.name == "nt":
                if handle:
                    self._api.CloseHandle(handle)
            else:
                os.close(handle)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def current_identity() -> str:
    with ProcessReference(os.getpid()) as process:
        return process.identity


def python_subprocess_spec():
    executable = sys.executable
    base = getattr(sys, "_base_executable", None)
    if os.name == "nt" and base and os.path.normcase(base) != os.path.normcase(executable):
        # Match CPython multiprocessing's venv launch: bypass the redirector
        # process while preserving the virtual environment's imports/prefix.
        env = os.environ.copy()
        env["__PYVENV_LAUNCHER__"] = executable
        return base, env
    return executable, None


def guard_main(parent_pid: int, parent_identity: str, worker_pid: int, worker_identity: str) -> int:
    # The guard is spawned by the worker itself, never an arbitrary registry.
    if worker_pid != os.getppid() or worker_pid == parent_pid:
        raise ValueError("guard must be a direct child of its worker")
    with ExitStack() as stack:
        worker = stack.enter_context(ProcessReference(worker_pid, worker_identity, can_kill=True))
        parent = stack.enter_context(ProcessReference(parent_pid, parent_identity))
        sys.stdout.buffer.write(b"READY\n")
        sys.stdout.buffer.flush()
        while worker.is_alive():
            if not parent.is_alive():
                worker.kill()
                # Keep the reference until exit; the separate ownership lock
                # also prevents any replacement from initializing prematurely.
                while worker.is_alive():
                    time.sleep(.05)
                return 0
            time.sleep(.1)
    return 0


def start_parent_guard(parent_pid: int, parent_identity: str):
    if parent_pid != os.getppid():
        raise ValueError("worker parent mismatch")
    # Reject a dead/reused parent before starting the helper. The helper pins
    # both objects again and acknowledges readiness before native imports.
    with ProcessReference(parent_pid, parent_identity):
        executable, env = python_subprocess_spec()
        process = subprocess.Popen(
            [executable, "-u", str(Path(__file__).resolve()), str(parent_pid),
             parent_identity, str(os.getpid()), current_identity()],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env,
            # The watcher's Ctrl-Break for the bot must not also disable its
            # last-resort parent-death guard during a blocked native call.
            creationflags=(getattr(subprocess, "CREATE_NO_WINDOW", 0)
                           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
            start_new_session=os.name != "nt",
        )
    try:
        if process.stdout.readline(16) != b"READY\n" or process.poll() is not None:
            raise OSError("MT5 parent guard did not become ready")
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        process.stdout.close()
    return process


if __name__ == "__main__":
    try:
        raise SystemExit(guard_main(int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]))
    except (OSError, ValueError, IndexError):
        raise SystemExit(2)
