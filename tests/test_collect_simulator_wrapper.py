"""Exercise the real Windows wrapper's bounded child-process block."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "runtime_data/calculator_delivery_20260910/forward_operations/collect_simulator_window.ps1"
POWERSHELL = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


@pytest.mark.skipif(not POWERSHELL.is_file(), reason="Windows PowerShell process contract")
@pytest.mark.parametrize("native_exit_code", [0, 7])
def test_redirected_child_preserves_real_exit_code(tmp_path, native_exit_code):
    script = tmp_path / "probe.ps1"
    script.write_text(r'''
param([string]$WrapperPath,[string]$LogPrefix,[string]$PythonPath,[int]$NativeExitCode)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$tokens = $null
$errors = $null
$tree = [System.Management.Automation.Language.Parser]::ParseFile($WrapperPath,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Wrapper parse failed' }
$blocks = @($tree.FindAll({param($node) $node -is [System.Management.Automation.Language.TryStatementAst]},$true))
$source = $null
foreach ($block in $blocks) {
    $selected = @()
    $collect = $false
    foreach ($statement in $block.Body.Statements) {
        if ($statement.Extent.Text.StartsWith('$process = Start-Process')) { $collect = $true }
        if ($collect -and $statement.Extent.Text.StartsWith('$after = @(')) { break }
        if ($collect) { $selected += $statement.Extent.Text }
    }
    if ($selected.Count) {
        if ($null -ne $source) { throw 'Ambiguous wrapper process block' }
        $source = $selected -join "`n"
    }
}
if ($null -eq $source) { throw 'Wrapper process block missing' }
$Python = $PythonPath
$quoted = @('"-c"',('"raise SystemExit(' + $NativeExitCode + ')"'))
$MaxRuntimeSeconds = 5
$process = $null
$exitCode = 2
$timedOut = $false
try {
    Invoke-Expression $source
    if ($null -eq $exitCode) { throw 'Child exit code lost' }
    @{exit_code=$exitCode;timed_out=$timedOut;retained_handle=($null -ne $processHandle)} | ConvertTo-Json -Compress
} finally {
    if ($null -ne $process) { $process.Dispose() }
}
''', encoding="ascii")
    completed = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-WrapperPath", str(WRAPPER), "-LogPrefix", str(tmp_path / "child"), "-PythonPath", sys.executable,
         "-NativeExitCode", str(native_exit_code)],
        capture_output=True, text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "exit_code": native_exit_code, "timed_out": False, "retained_handle": True,
    }
