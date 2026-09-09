<#
.SYNOPSIS
Register the Host Auto-Recovery Scheduled Task (reversible install step).

.DESCRIPTION
Creates exactly one Windows Scheduled Task, "Crumblr Host Supervisor", that
runs scripts\host_supervisor.ps1 as the current user at logon. This is the
only host-level state this feature adds -- everything else host_supervisor.ps1
does is start already-existing, already-proven processes against
already-existing state (crumblr_soak, the journal, the safety latch).

Trigger is "At log on" for this specific user, not "At startup": Docker
Desktop's own auto-launch (already registered by Docker's installer under
this user's Run key) needs a real interactive desktop session to bring up
its WSL2-backed engine, which a headless/S4U "At startup" task cannot
reliably provide. Task Scheduler's own restart-on-failure setting is bounded
(3 attempts, 5 minutes apart) so a persistently broken dependency cannot
retry forever -- matching host_supervisor.ps1's own bounded per-stage
retries.

No secret is written into the task definition -- the action is just "run
this script", identical to running it by hand. host_supervisor.ps1 itself
reads every credential from this Windows user's own environment-variable
store at run time.

Idempotent: re-running this replaces the existing task definition rather
than erroring or duplicating it.
#>

$ErrorActionPreference = "Stop"

$TaskName = "Crumblr Host Supervisor"
$ScriptPath = Join-Path $PSScriptRoot "host_supervisor.ps1"

if (-not (Test-Path $ScriptPath)) {
    throw "host_supervisor.ps1 not found at $ScriptPath -- nothing to register"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force `
    -Description "Starts the Crumblr Agent Shadow / PAPER_LITE soak dependency chain (Postgres -> MT5 -> Static Agent -> reader -> PAPER_LITE -> dashboard) at logon. Installed/removed via scripts\install_host_supervisor_task.ps1 / uninstall_host_supervisor_task.ps1." `
    | Out-Null

Write-Host "Registered Scheduled Task '$TaskName' -> $ScriptPath"
Write-Host "Trigger: At log on, user $env:USERDOMAIN\$env:USERNAME"
Write-Host "Bounded restart: 3 attempts, 5 minutes apart, 1 hour execution limit"
Write-Host "To remove: scripts\uninstall_host_supervisor_task.ps1"
