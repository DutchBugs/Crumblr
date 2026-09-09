<#
.SYNOPSIS
Manual, one-time bootstrap for the Host Auto-Recovery boot-CLEAR marker.

.DESCRIPTION
Writes var\host_supervisor_boot_clear.json recording the CURRENT boot time
as already consumed. Does NOT issue an incident-CLEAR, does NOT touch
PAPER_LITE, safety state, crumblr_soak, the journal, or the assignment --
it only writes the marker file, via the exact same atomic primitive
scripts\claim_boot_clear.py already uses (temp-file-then-replace).

Never invoked automatically by host_supervisor.ps1 or the Scheduled Task.
A human runs this exactly once, on first installation, so that:

    this bootstrap, on boot N        -> marker records boot N, "consumed"
    (a real, controlled reboot)      -> boot N+1 begins
    host_supervisor.ps1, on boot N+1 -> marker's boot N is strictly older
                                         than the current boot N+1 -> the
                                         one automatic incident-CLEAR this
                                         boot is now eligible

Without this bootstrap, the very first host_supervisor.ps1 run on a fresh
install finds no marker at all and correctly refuses to auto-CLEAR
(fail-closed, scripts\check_boot_clear_eligible.py) -- which is the right
default, but it means PAPER_LITE cannot start automatically until a
marker exists recording some earlier boot. This script exists to
establish that starting point deliberately, once, under an owner's own
hand, rather than needing a throwaway manual PAPER_LITE run to do it as a
side effect.

Run this once, then perform a real, controlled reboot, then let the
Scheduled Task (once registered) bring PAPER_LITE up automatically on the
next boot.
#>

$ErrorActionPreference = "Stop"

$RepoRoot = "C:\Users\Levi's MacBook\Desktop\Projecten\Crumblr"
$BootClearMarkerPath = Join-Path $RepoRoot "var\host_supervisor_boot_clear.json"

$currentBootTimeUtc = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString("o")

Push-Location $RepoRoot
uv run python scripts\claim_boot_clear.py $BootClearMarkerPath $currentBootTimeUtc
$claimed = ($LASTEXITCODE -eq 0)
Pop-Location

if (-not $claimed) {
    Write-Host "BOOTSTRAP FAILED: could not write $BootClearMarkerPath -- see output above."
    exit 1
}

Write-Host "Bootstrapped $BootClearMarkerPath with the current boot time (marked already-consumed)."
Write-Host "No incident-CLEAR was issued. No PAPER_LITE process was started. No safety state was touched."
Write-Host "The NEXT real, controlled reboot will be eligible for exactly one automatic incident-CLEAR."
