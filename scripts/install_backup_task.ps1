<#
.SYNOPSIS
Register the hourly Stage-C backup Scheduled Task (reversible install step).

.DESCRIPTION
Creates exactly one Windows Scheduled Task, "Crumblr Backup", that runs
scripts\backup_crumblr_soak.py hourly, forever, as the current user. This
is the only host-level state this feature adds -- the task's Action is
`uv.exe run python scripts\backup_crumblr_soak.py`, nothing else: no
inline PowerShell, no other script, no argument beyond that.

LogonType Interactive, matching host_supervisor.ps1 -- not S4U. S4U was
tried first (this task itself needs nothing graphical, only `docker exec`
against an already-running engine and DPAPI-protected Credential Manager
access, which S4U would provide without the user staying interactively
logged in) but registering an S4U-logon task is itself a privileged
operation: it failed live with "Access denied" from a non-elevated
session, and elevating just to register this task would mean granting
more privilege than the task itself ever needs to run. Interactive
registers with no elevation at all -- the literal "least privilege" of
the two options -- at the cost of only firing while this user stays
logged in, which this host already assumes everywhere else
(host_supervisor.ps1's own "At log on" trigger exists for exactly the
same reason). RunLevel Limited (never "Highest"/admin) either way: this
task never needs elevation to run, only Docker CLI and Credential Manager
access, both already available to a standard interactive session.

No secret is written into the task definition -- the Action is just "run
this one script", identical to running it by hand. backup_crumblr_soak.py
itself reads CRUMBLR_DATABASE_URL from Windows Credential Manager at run
time; nothing here, in the task's arguments, or in its XML ever carries a
database URL, password, or token.

-MultipleInstances IgnoreNew: if an hourly firing finds the previous run
still going, Task Scheduler skips starting a second one rather than
running two pg_dump/pg_restore cycles against the same scratch database
concurrently. -ExecutionTimeLimit bounds a single run so a hung backup
cannot silently occupy every future hourly slot forever. No restart-on-
failure setting is configured deliberately -- a failed run should surface
as a failure (the CLI's own exit code) and try again at the next natural
hourly trigger, not be retried aggressively into looking like it worked.

Idempotent: re-running this replaces the existing task definition rather
than erroring or duplicating it.
#>

$ErrorActionPreference = "Stop"

$TaskName = "Crumblr Backup"
$RepoRoot = "C:\Users\Levi's MacBook\Desktop\Projecten\Crumblr"
$ScriptPath = Join-Path $RepoRoot "scripts\backup_crumblr_soak.py"
$UvPath = "C:\Users\Levi's MacBook\.local\bin\uv.exe"

if (-not (Test-Path $ScriptPath)) {
    throw "backup_crumblr_soak.py not found at $ScriptPath -- nothing to register"
}
if (-not (Test-Path $UvPath)) {
    throw "uv.exe not found at $UvPath -- update `$UvPath in this script"
}

$action = New-ScheduledTaskAction -Execute $UvPath `
    -Argument "run python scripts\backup_crumblr_soak.py" `
    -WorkingDirectory $RepoRoot

# One-time trigger with an hourly repetition, indefinitely -- the standard
# PowerShell idiom for "run hourly forever" (there is no -Hourly trigger
# type). Starts on the next full hour rather than immediately, so the
# first automatic run does not race this installation.
$firstRun = (Get-Date).Date.AddHours((Get-Date).Hour + 1)
$trigger = New-ScheduledTaskTrigger -Once -At $firstRun `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
# [TimeSpan]::MaxValue renders as an XML duration (P99999999DT23H59M59S)
# Task Scheduler itself rejects as out of range -- confirmed live (a
# HRESULT 0x80041318 CIM exception that Register-ScheduledTask does NOT
# treat as terminating, so $ErrorActionPreference = "Stop" alone did not
# stop this script the first time; -ErrorAction Stop is now passed to the
# cmdlet explicitly below, in a try/catch, so a real failure is never
# printed as a false success again. 10 years is "effectively forever" for
# an hourly task and stays well within Task Scheduler's accepted range.

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -MultipleInstances IgnoreNew

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force -ErrorAction Stop `
        -Description "Hourly Stage-C backup: dump crumblr_soak, restore-verify into crumblr_backup_verify, retain the newest 72 verified dumps. Runs scripts\backup_crumblr_soak.py only. Installed/removed via scripts\install_backup_task.ps1 / uninstall_backup_task.ps1." `
        | Out-Null
} catch {
    Write-Host "REGISTRATION FAILED: $($_.Exception.Message)"
    exit 1
}

$confirmed = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $confirmed) {
    Write-Host "REGISTRATION FAILED: Register-ScheduledTask reported success but the task is not actually present"
    exit 1
}

Write-Host "Registered Scheduled Task '$TaskName' -> $UvPath run python scripts\backup_crumblr_soak.py"
Write-Host "Working directory: $RepoRoot"
Write-Host "Trigger: hourly, starting $firstRun, user $env:USERDOMAIN\$env:USERNAME (Interactive, Limited)"
Write-Host "Overlap policy: IgnoreNew (skips a firing if the previous run is still going)"
Write-Host "Execution time limit: 20 minutes"
Write-Host "To remove: scripts\uninstall_backup_task.ps1"
