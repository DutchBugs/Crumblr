# Convenience wrapper for Dashboard v0 (review 1.12 §8).
#
# Sets up the environment `run_dashboard.py` needs and starts it, so viewing
# the dashboard is one command instead of three. Defaults to `crumblr_soak`
# (real Phase A/B data) rather than the empty `crumblr` test database — pass
# -DatabaseUrl to point at something else.
#
# Usage:
#   .\scripts\dashboard.ps1
#   .\scripts\dashboard.ps1 -DatabaseUrl "postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr"

param(
    [string]$DatabaseUrl = "postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr_soak",
    [int]$Port = 8050
)

$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
$env:CRUMBLR_DATABASE_URL = $DatabaseUrl

Set-Location (Split-Path -Parent $PSScriptRoot)
uv run python scripts/run_dashboard.py --port $Port
