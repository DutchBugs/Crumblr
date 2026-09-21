<#
.SYNOPSIS
Register the Reader/Dashboard-only Scheduled Task (reversible install step).

.DESCRIPTION
Creates exactly one Windows Scheduled Task, "Crumblr Reader Dashboard
Supervisor", that runs `host_supervisor.ps1 -ReaderDashboardOnly` as the
current user at logon -- Postgres -> MT5 -> reader -> dashboard only, never
the Static Agent or PAPER_LITE stages.

Deliberately a separate task from "Crumblr Host Supervisor"
(install_host_supervisor_task.ps1), which is NOT installed by this script
and is NOT registered on this host as of 2026-09-17: that task's own
PAPER_LITE stage targets a stale post-incident config that is not what is
actually running today (a per-bar preflight watcher script, started and
owned outside this supervisor chain) -- running it at logon would silently
start a second, different decision-adjacent process. This task starts
nothing decision-related at all.

Same bounded-restart philosophy as the full supervisor: Task Scheduler's
own restart-on-failure (3 attempts, 5 minutes apart), never an unbounded
respawn loop. If the reader or dashboard later crashes mid-session, it
stays crashed until the next logon/reboot runs this task again -- a crash
must stay visible, not be silently retried forever.

No secret is written into the task definition -- host_supervisor.ps1 reads
every credential from Windows Credential Manager at run time.

Idempotent: re-running this replaces the existing task definition rather
than erroring or duplicating it.
#>

$ErrorActionPreference = "Stop"

$TaskName = "Crumblr Reader Dashboard Supervisor"
$ScriptPath = Join-Path $PSScriptRoot "host_supervisor.ps1"

if (-not (Test-Path $ScriptPath)) {
    throw "host_supervisor.ps1 not found at $ScriptPath -- nothing to register"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -ReaderDashboardOnly"

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
    -Description "Starts Postgres -> MT5 -> read-only live reader -> read-only dashboard at logon (host_supervisor.ps1 -ReaderDashboardOnly). Never starts the Static Agent or PAPER_LITE stages -- see install_host_supervisor_task.ps1 for the full chain, which is deliberately NOT installed alongside this one as of 2026-09-17." `
    | Out-Null

Write-Host "Registered Scheduled Task '$TaskName' -> $ScriptPath -ReaderDashboardOnly"
Write-Host "Trigger: At log on, user $env:USERDOMAIN\$env:USERNAME"
Write-Host "Bounded restart: 3 attempts, 5 minutes apart, 1 hour execution limit"
Write-Host "To remove: Unregister-ScheduledTask -TaskName 'Crumblr Reader Dashboard Supervisor' -Confirm:`$false"
