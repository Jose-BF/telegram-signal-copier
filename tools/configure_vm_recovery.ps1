$ErrorActionPreference = 'Stop'
$taskName = 'Signal Copier Bot Watcher'
$repo = 'C:\Users\bot\telegram-signal-copier'
$backup = 'C:\Users\bot\codex-control\watcher-before-recovery-' + (Get-Date -Format 'yyyyMMddTHHmmss') + '.xml'
Export-ScheduledTask -TaskName $taskName | Set-Content -LiteralPath $backup -Encoding Unicode
$action = New-ScheduledTaskAction -Execute 'C:\Program Files\Python311\python.exe' -Argument ('-u "' + $repo + '\tools\start_logged_bot.py"') -WorkingDirectory $repo
$startup = New-ScheduledTaskTrigger -AtStartup
$logon = New-ScheduledTaskTrigger -AtLogOn -User 'bot'
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
# S4U runs in the bot account even before an interactive logon. No password is stored here.
$principal = New-ScheduledTaskPrincipal -UserId 'bot' -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Set-ScheduledTask -TaskName $taskName -Action $action -Trigger @($startup, $logon, $periodic) -Principal $principal -Settings $settings | Select-Object TaskName, State
Write-Output ('Previous task definition: ' + $backup)
