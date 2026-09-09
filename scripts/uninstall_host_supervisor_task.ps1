<#
.SYNOPSIS
Remove the Host Auto-Recovery Scheduled Task (reversal of install_host_supervisor_task.ps1).

.DESCRIPTION
Unregisters the "Crumblr Host Supervisor" Scheduled Task only. Does not stop
any currently running process (Postgres, MT5, the Static Agent, the reader,
PAPER_LITE, the dashboard), does not touch crumblr_soak, the journal, the
safety latch, the assignment, or any credential. This only prevents the
chain from being started automatically at the next logon.
#>

$ErrorActionPreference = "Stop"

$TaskName = "Crumblr Host Supervisor"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "No Scheduled Task named '$TaskName' is registered -- nothing to remove."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed Scheduled Task '$TaskName'."
Write-Host "Any already-running processes (Postgres/MT5/Static Agent/reader/PAPER_LITE/dashboard) were left untouched."
