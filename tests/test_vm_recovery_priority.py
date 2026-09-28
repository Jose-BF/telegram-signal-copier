"""Execute the real configurator with every ScheduledTask mutation replaced."""

import base64
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_repeated_recovery_configuration_retains_normal_priority():
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell required for the Windows recovery configurator")
    path = str(ROOT / "tools" / "configure_vm_recovery.ps1").replace("'", "''")
    script = r"""
$ErrorActionPreference = 'Stop'
$global:records = @()
function Export-ScheduledTask { param($TaskName) '<Task />' }
function Set-Content { param($LiteralPath, $Encoding, [Parameter(ValueFromPipeline)]$Value) }
function New-ScheduledTaskAction { param($Execute, $Argument, $WorkingDirectory) $PSBoundParameters }
function New-ScheduledTaskTrigger {
    param([switch]$AtStartup, [switch]$AtLogOn, $User, [switch]$Once, $At, $RepetitionInterval)
    $PSBoundParameters
}
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel) $PSBoundParameters }
function New-ScheduledTaskSettingsSet {
    param([switch]$StartWhenAvailable, $RestartCount, $RestartInterval, $ExecutionTimeLimit,
          $MultipleInstances, [switch]$AllowStartIfOnBatteries,
          [switch]$DontStopIfGoingOnBatteries, [int]$Priority = 7)
    @{ Priority = $Priority; MultipleInstances = $MultipleInstances;
       ExecutionTimeLimit = $ExecutionTimeLimit.TotalSeconds }
}
function Set-ScheduledTask {
    param($TaskName, $Action, $Trigger, $Principal, $Settings)
    $global:records += @{ TaskName = $TaskName; Settings = $Settings;
        TriggerCount = @($Trigger).Count; Principal = $Principal }
    [pscustomobject]@{TaskName = $TaskName; State = 'MockOnly'}
}
& '__SCRIPT__' | Out-Null
& '__SCRIPT__' | Out-Null
ConvertTo-Json -InputObject $global:records -Depth 8 -Compress
""".replace("__SCRIPT__", path)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30, check=True,
    )
    records = json.loads(result.stdout)
    assert len(records) == 2
    for record in records:
        assert record["Settings"]["Priority"] == 4
        assert record["Settings"]["MultipleInstances"] == "IgnoreNew"
        assert record["Settings"]["ExecutionTimeLimit"] == 0
        assert record["TriggerCount"] == 3
        assert record["Principal"]["LogonType"] == "S4U"
