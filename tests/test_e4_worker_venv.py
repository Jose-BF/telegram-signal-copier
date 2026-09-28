import json
import os
from pathlib import Path
import subprocess
import venv

import psutil
import pytest


@pytest.fixture(scope="module")
def virtual_python(tmp_path_factory):
    root = tmp_path_factory.mktemp("guard-venv")
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(root)
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python"), root


@pytest.mark.parametrize("mode", ["close", "kill"])
def test_real_virtual_environment_worker_and_guard_lifetime(tmp_path, virtual_python, mode):
    python, root = virtual_python
    try:
        result = subprocess.run(
            [str(python), str(Path(__file__).with_name("mt5_lifetime_venv_probe.py")), str(tmp_path), mode],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == (23 if mode == "kill" else 0), result.stderr
        metadata = json.loads((tmp_path / "processes.json").read_text(encoding="ascii"))
        assert Path(metadata["prefix"]).resolve() == root.resolve()
        for info in metadata["processes"]:
            try:
                process = psutil.Process(info["pid"])
                if process.create_time() == info["created"]:
                    process.wait(timeout=5)
            except psutil.NoSuchProcess:
                pass
        if mode == "kill":
            assert (tmp_path / "send.txt").read_text().splitlines() == ["send"]
    finally:
        if (tmp_path / "processes.json").exists():
            metadata = json.loads((tmp_path / "processes.json").read_text(encoding="ascii"))
            for info in metadata["processes"]:
                try:
                    process = psutil.Process(info["pid"])
                    if process.create_time() == info["created"]:
                        process.kill()
                        process.wait(timeout=5)
                except psutil.NoSuchProcess:
                    pass
