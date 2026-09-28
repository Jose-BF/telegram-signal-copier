"""A disposable descendant stands in for a separately managed terminal."""

import json
from pathlib import Path
import subprocess
import sys

import psutil

from tests.mt5_read_fakes import FakeMT5, FakeTradeMT5


class EnvironmentBackend(FakeTradeMT5):
    def account_info(self):
        return {**super().account_info()._asdict(), "python_prefix": sys.prefix}


class DescendantBackend(FakeMT5):
    def __init__(self, *, marker):
        super().__init__()
        self.marker = marker

    def initialize(self, *args, **kwargs):
        self.descendant = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        process = psutil.Process(self.descendant.pid)
        Path(self.marker).write_text(json.dumps({
            "pid": process.pid, "created": process.create_time(),
        }), encoding="ascii")
        return super().initialize(*args, **kwargs)
