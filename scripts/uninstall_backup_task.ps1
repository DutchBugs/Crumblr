<#
.SYNOPSIS
Remove the hourly Stage-C backup Scheduled Task (reversal of install_backup_task.ps1).

.DESCRIPTION
Unregisters the "Crumblr Backup" Scheduled Task only. Does not touch
crumblr_soak, crumblr_backup_verify, any existing dump/marker file under
C:\CrumblrBackups\crumblr_soak\, or Credential Manager. This only
prevents future hourly firings.
#>

$ErrorActionPreference = "Stop"

$TaskName = "Crumblr Backup"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "No Scheduled Task named '$TaskName' is registered -- nothing to remove."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Removed Scheduled Task '$TaskName'."
Write-Host "Existing backups under C:\CrumblrBackups\crumblr_soak\ were left untouched."
