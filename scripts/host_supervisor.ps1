<#
.SYNOPSIS
Host Auto-Recovery supervisor for the Agent Shadow / PAPER_LITE soak.

.DESCRIPTION
Triggered by a Windows Scheduled Task at user logon (see
install_host_supervisor_task.ps1). Brings up, in strict order, exactly the
already-proven soak runtime:

    Postgres -> MT5/DEMO availability -> Static Agent healthy
    -> MT5 reader HEALTHY + exact InstrumentSpec pin -> PAPER_LITE -> dashboard

Each stage is health-checked, with a bounded number of polls, before the
next one starts. This script:

- never migrates, resets, or seeds crumblr_soak, the journal, the safety
  latch, the assignment, or credentials -- it only starts already-existing
  processes against already-existing state;
- never passes --initialize-paper-safety or --fixed-now to PAPER_LITE;
- refuses to start PAPER_LITE unless the safety state is coherently RUNNING
  on both the Postgres and file-latch backing stores (checked by the
  separate, read-only check_soak_safety_coherent.py);
- refuses to start a second PAPER_LITE writer if one is already running;
- never restarts a stage that is already running -- checked by process
  list, not assumed;
- carries no secret values itself -- every credential is read at run time
  from Windows Credential Manager (DPAPI-encrypted, scoped to this Windows
  user account; see `src\crumblr\local_admin\credential_store.py` and
  `scripts\local_host_admin.py`), never hard-coded, never passed as a
  literal command-line argument (which would be visible to any local
  process listing), and never falls back to a plaintext environment
  variable -- Credential Manager is the one and only source, deliberately,
  so a stage either reads a real secret or blocks and says so;
- reads exactly six secrets, no more: CRUMBLR_MT5_LOGIN, CRUMBLR_MT5_PASSWORD,
  CRUMBLR_MT5_SERVER, CRUMBLR_DATABASE_URL, CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL,
  and LOCAL_AGENT_SERVICE_TOKEN -- the last one shared: the same stored value
  is injected as CRUMBLR_TRADER_SERVICE_TOKEN into the Static Agent process
  and as CRUMBLR_PAPER_LITE_AGENT_TOKEN into PAPER_LITE, so there is exactly
  one bearer token to keep configured, never two copies that could drift
  apart;
- logs its own progress to var\host_supervisor.log -- stage names, health
  results, timestamps, PIDs -- never a credential value;
- gives up (does not loop forever) if a stage never becomes healthy within
  its own bound, so a persistently broken dependency cannot spin this
  script, or the Scheduled Task's own limited retry count, forever;
- issues PAPER_LITE's required --confirm-paper-incident-clear at most ONCE
  per Windows boot session, not once per run of this script (owner
  instruction, 2026-09-09), and fails closed by construction (Dev 1
  BLOCK, 2026-09-09): var\host_supervisor_boot_clear.json is eligible for
  the current boot ONLY if it holds a valid, strictly-older boot's claim
  (scripts\check_boot_clear_eligible.py -- unit-tested,
  tests\unit\test_boot_clear_gate.py). Missing, unreadable, malformed,
  schema-invalid, unparsable, future, or current-boot markers all block.
  The claim (scripts\claim_boot_clear.py, atomic temp-file-then-replace)
  happens before PAPER_LITE is even started, and PAPER_LITE is not
  started at all if that claim fails. First installation needs
  scripts\bootstrap_boot_clear_marker.ps1 run manually once -- never
  automatically -- to establish a starting marker; see that script's own
  docstring. Every other stage (Postgres/MT5/Static Agent/reader) may
  still retry with its own bounded backoff -- this restriction is
  specific to re-authorizing PAPER_LITE's incident CLEAR.

This script does not supervise already-running processes after the chain
is up -- if PAPER_LITE (or anything else) later crashes, it stays crashed
until the next real boot/logon runs this script again. That is deliberate:
an aggressive respawn-on-crash loop is exactly the kind of thing that could
quietly turn a real failure (e.g. the Agent being unreachable) into
something that looks like nothing happened. A crash must stay visible.
#>

$ErrorActionPreference = "Stop"

$RepoRoot = "C:\Users\Levi's MacBook\Desktop\Projecten\Crumblr"
$StaticAgentRoot = "C:\Users\Levi's MacBook\Desktop\Projecten\crumblr-static-agent-host"
$LogFile = Join-Path $RepoRoot "var\host_supervisor.log"

$AgentId = "760e93be-117c-48a3-b997-f258055ec29b"
$AssignmentId = "14255834-811d-4b19-8d57-8057d3e39424"
$CanonicalSymbol = "EUR/USD"
$Timeframe = "M5"
$CodeCommit = "c775333"
$AgentUrl = "http://127.0.0.1:8765"
$DashboardPort = 8050

function Write-SupervisorLog {
    param([string]$Stage, [string]$Message)
    $line = "$(Get-Date -Format o) [$Stage] $Message"
    Add-Content -Path $LogFile -Value $line
    Write-Host $line
}

# ---- Windows Credential Manager (read-only) -------------------------------
# The PowerShell-side counterpart of src\crumblr\local_admin\credential_store.py
# -- CredReadW only, deliberately: this script only ever reads a secret to
# hand it to one child process's environment, it never writes or deletes
# one (that stays exclusively Local Host Admin's job, on explicit owner
# action). Same "Crumblr:" TargetName prefix and UTF-16LE CredentialBlob
# convention as the Python writer -- proven to round-trip cross-language
# during this work order's isolation testing (2026-09-09).
$CredManagerSource = @'
using System;
using System.Runtime.InteropServices;

public static class CrumblrCredManager {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct CREDENTIAL {
        public int Flags;
        public int Type;
        public string TargetName;
        public string Comment;
        public long LastWritten;
        public int CredentialBlobSize;
        public IntPtr CredentialBlob;
        public int Persist;
        public int AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    [DllImport("Advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool CredRead(string target, int type, int reservedFlag, out IntPtr credentialPtr);

    [DllImport("Advapi32.dll", SetLastError = true)]
    public static extern void CredFree(IntPtr cred);

    public static string Read(string name) {
        IntPtr credPtr;
        bool ok = CredRead("Crumblr:" + name, 1, 0, out credPtr);
        if (!ok) {
            return null;
        }
        try {
            CREDENTIAL cred = (CREDENTIAL)Marshal.PtrToStructure(credPtr, typeof(CREDENTIAL));
            if (cred.CredentialBlobSize == 0) { return ""; }
            byte[] bytes = new byte[cred.CredentialBlobSize];
            Marshal.Copy(cred.CredentialBlob, bytes, 0, cred.CredentialBlobSize);
            return System.Text.Encoding.Unicode.GetString(bytes);
        } finally {
            CredFree(credPtr);
        }
    }
}
'@
Add-Type -TypeDefinition $CredManagerSource -Language CSharp

function Get-CrumblrSecret {
    # Credential Manager only -- no environment-variable fallback of any
    # kind, on any scope. A missing/misconfigured secret must block the
    # stage and say so, never silently succeed off a stale plaintext copy.
    param([string]$Name)
    return [CrumblrCredManager]::Read($Name)
}

$BootClearMarkerPath = Join-Path $RepoRoot "var\host_supervisor_boot_clear.json"

function Get-CurrentBootTimeUtc {
    # The one source of truth for "what is the current boot" -- passed as
    # an argument to check_boot_clear_eligible.py/claim_boot_clear.py
    # rather than rediscovered in Python, so PowerShell and Python never
    # have two independent (and possibly disagreeing) ideas of "now."
    return (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString("o")
}

function Test-BootClearEligible {
    # Fail-closed by construction (Dev 1 BLOCK, 2026-09-09): delegates the
    # actual validation to scripts\check_boot_clear_eligible.py, which is
    # unit-tested (tests\unit\test_boot_clear_gate.py) precisely because
    # this decision is security-relevant and must not live only in
    # PowerShell. See that script's own docstring for the exact rules --
    # in short, eligible only when the marker holds a valid, older boot's
    # claim; anything else (missing, invalid, current-boot) blocks.
    param([string]$CurrentBootTimeUtc)
    Push-Location $RepoRoot
    uv run python scripts\check_boot_clear_eligible.py $BootClearMarkerPath $CurrentBootTimeUtc
    $eligible = ($LASTEXITCODE -eq 0)
    Pop-Location
    return $eligible
}

function Set-BootClearMarker {
    # Atomically claims the marker for $CurrentBootTimeUtc via
    # scripts\claim_boot_clear.py (temp-file-then-replace). Returns $false
    # on any failure -- the caller must not start PAPER_LITE in that case.
    param([string]$CurrentBootTimeUtc)
    Push-Location $RepoRoot
    uv run python scripts\claim_boot_clear.py $BootClearMarkerPath $CurrentBootTimeUtc
    $claimed = ($LASTEXITCODE -eq 0)
    Pop-Location
    return $claimed
}

function Get-CrumblrSetting {
    # Non-secret local host settings (config\local_host_settings.json,
    # owned by Local Host Admin). Missing file or key returns $null --
    # callers fall back to the same literal default Local Host Admin itself
    # uses, so a supervisor run before Local Host Admin has ever been
    # opened still works.
    param([string]$Key)
    $path = Join-Path $RepoRoot "config\local_host_settings.json"
    if (-not (Test-Path $path)) { return $null }
    $settings = Get-Content $path -Raw | ConvertFrom-Json
    return $settings.$Key
}

function Test-ProcessRunning {
    param([string]$CommandLineMatch)
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like $CommandLineMatch }
    return @($procs).Count -gt 0
}

# ---- Stage 1: Postgres ---------------------------------------------------
function Start-PostgresStage {
    Write-SupervisorLog "postgres" "checking Docker engine"
    if (-not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue)) {
        Write-SupervisorLog "postgres" "starting Docker Desktop"
        Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    }

    $dockerReady = $false
    for ($i = 0; $i -lt 36; $i++) {
        docker ps > $null 2>&1
        if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
        Start-Sleep -Seconds 5
    }
    if (-not $dockerReady) {
        Write-SupervisorLog "postgres" "BLOCKED: Docker engine never became ready"
        return $false
    }

    $running = docker inspect -f "{{.State.Running}}" crumblr-pg 2>$null
    if ($running -ne "true") {
        Write-SupervisorLog "postgres" "starting existing crumblr-pg container (no create, no seed)"
        docker start crumblr-pg 2>&1 | Out-Null
    }

    $env:CRUMBLR_DATABASE_URL = Get-CrumblrSecret "CRUMBLR_DATABASE_URL"
    if (-not $env:CRUMBLR_DATABASE_URL) {
        Write-SupervisorLog "postgres" "BLOCKED: CRUMBLR_DATABASE_URL not set for this user"
        return $false
    }

    Push-Location $RepoRoot
    $reachable = $false
    for ($i = 0; $i -lt 20; $i++) {
        # Uses the project's own engine helper (crumblr.persistence.engine),
        # not a raw psycopg.connect() -- CRUMBLR_DATABASE_URL carries the
        # SQLAlchemy dialect form ("postgresql+psycopg://..."), which a bare
        # psycopg driver cannot parse on its own.
        uv run python -c "from crumblr.persistence.engine import create_db_engine; import sqlalchemy; e = create_db_engine(); c = e.connect(); c.execute(sqlalchemy.text('select 1')); c.close()" > $null 2>&1
        if ($LASTEXITCODE -eq 0) { $reachable = $true; break }
        Start-Sleep -Seconds 3
    }
    Pop-Location

    if (-not $reachable) {
        Write-SupervisorLog "postgres" "BLOCKED: crumblr_soak not reachable within bound"
        return $false
    }
    Write-SupervisorLog "postgres" "reachable"
    return $true
}

# ---- Stage 2: MT5/DEMO availability --------------------------------------
function Start-Mt5Stage {
    Write-SupervisorLog "mt5" "checking terminal"
    $mt5Exe = "C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe"

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        if (-not (Get-Process "terminal64" -ErrorAction SilentlyContinue)) {
            Write-SupervisorLog "mt5" "launching terminal (attempt $attempt)"
            Start-Process $mt5Exe
        }

        $env:CRUMBLR_MT5_LOGIN = Get-CrumblrSecret "CRUMBLR_MT5_LOGIN"
        $env:CRUMBLR_MT5_PASSWORD = Get-CrumblrSecret "CRUMBLR_MT5_PASSWORD"
        $env:CRUMBLR_MT5_SERVER = Get-CrumblrSecret "CRUMBLR_MT5_SERVER"
        $termPath = Get-CrumblrSetting "mt5_terminal_path"
        if ($termPath) { $env:CRUMBLR_MT5_TERMINAL_PATH = $termPath }

        if (-not $env:CRUMBLR_MT5_LOGIN -or -not $env:CRUMBLR_MT5_PASSWORD -or -not $env:CRUMBLR_MT5_SERVER) {
            Write-SupervisorLog "mt5" "BLOCKED: MT5 credentials not set for this user"
            return $false
        }

        Push-Location $RepoRoot
        uv run python scripts/mt5_probe.py --canonical-symbol $CanonicalSymbol --sanitized-json var\mt5_probe_check.json > var\mt5_probe_attempt.log 2>&1
        $probeExit = $LASTEXITCODE
        Pop-Location

        if ($probeExit -eq 0) {
            Write-SupervisorLog "mt5" "connected and authorized"
            return $true
        }
        Write-SupervisorLog "mt5" "probe attempt $attempt failed (exit $probeExit), see var\mt5_probe_attempt.log"
        Start-Sleep -Seconds 15
    }

    Write-SupervisorLog "mt5" "BLOCKED: could not confirm MT5/DEMO availability after 3 attempts"
    return $false
}

# ---- Stage 3: Static Agent healthy ---------------------------------------
function Start-StaticAgentStage {
    Write-SupervisorLog "static-agent" "checking service"

    $alreadyUp = $false
    try {
        $health = Invoke-RestMethod -Uri "$AgentUrl/health" -TimeoutSec 3
        if ($health.status -eq "READY") { $alreadyUp = $true }
    } catch {}

    if (-not $alreadyUp) {
        $env:CRUMBLR_AGENT_ID = $AgentId
        # LOCAL_AGENT_SERVICE_TOKEN is the one shared bearer -- the Static
        # Agent server and PAPER_LITE's client both authenticate with this
        # same stored value, injected here under the env-var name the
        # Static Agent's own CLI expects.
        $env:CRUMBLR_TRADER_SERVICE_TOKEN = Get-CrumblrSecret "LOCAL_AGENT_SERVICE_TOKEN"
        if (-not $env:CRUMBLR_TRADER_SERVICE_TOKEN) {
            Write-SupervisorLog "static-agent" "BLOCKED: LOCAL_AGENT_SERVICE_TOKEN not configured in Credential Manager"
            return $false
        }
        Write-SupervisorLog "static-agent" "starting"
        Push-Location $StaticAgentRoot
        Start-Process -FilePath "uv" -ArgumentList "run","crumblr-strategy-agent","serve","--neutral-only" `
            -WorkingDirectory $StaticAgentRoot -WindowStyle Hidden
        Pop-Location
    }

    for ($i = 0; $i -lt 24; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "$AgentUrl/health" -TimeoutSec 3
            if ($health.status -eq "READY" -and $health.neutral_context_strategy.strategy_artifact_hash) {
                Write-SupervisorLog "static-agent" "READY, artifact hash $($health.neutral_context_strategy.strategy_artifact_hash)"
                return $true
            }
        } catch {}
        Start-Sleep -Seconds 5
    }
    Write-SupervisorLog "static-agent" "BLOCKED: never reached READY within bound"
    return $false
}

# ---- Stage 4: MT5 reader HEALTHY + exact InstrumentSpec pin --------------
function Start-ReaderStage {
    Write-SupervisorLog "reader" "checking"
    $healthPath = Join-Path $RepoRoot "var\live_reader_health.json"

    if (-not (Test-ProcessRunning "*mt5_live_reader.py*")) {
        $env:CRUMBLR_MT5_LOGIN = Get-CrumblrSecret "CRUMBLR_MT5_LOGIN"
        $env:CRUMBLR_MT5_PASSWORD = Get-CrumblrSecret "CRUMBLR_MT5_PASSWORD"
        $env:CRUMBLR_MT5_SERVER = Get-CrumblrSecret "CRUMBLR_MT5_SERVER"
        $env:CRUMBLR_DATABASE_URL = Get-CrumblrSecret "CRUMBLR_DATABASE_URL"
        Write-SupervisorLog "reader" "starting"
        Start-Process -FilePath "uv" `
            -ArgumentList "run","python","scripts\mt5_live_reader.py","--canonical-symbol",$CanonicalSymbol,"--timeframe",$Timeframe,"--json","var\live_reader_health.json" `
            -WorkingDirectory $RepoRoot -WindowStyle Hidden
    } else {
        Write-SupervisorLog "reader" "already running"
    }

    # The expected spec version this deployment is pinned to -- read from
    # config, not hard-coded here, so a real config change is never
    # silently out of sync with this check.
    Push-Location $RepoRoot
    $expectedSpec = uv run python -c "from pathlib import Path; from crumblr.config import load_config; from crumblr.domain.enums import Environment; c = load_config(Environment.PAPER, config_dir=Path('config')); m = c.market_for('$CanonicalSymbol'); print(m.expected_spec_version if m else '')" 2>$null
    Pop-Location
    $expectedSpec = ($expectedSpec | Select-Object -Last 1).Trim()

    for ($i = 0; $i -lt 24; $i++) {
        if (Test-Path $healthPath) {
            $health = Get-Content $healthPath -Raw | ConvertFrom-Json
            if ($health.status -eq "HEALTHY" -and $health.spec_version -eq $expectedSpec) {
                Write-SupervisorLog "reader" "HEALTHY, spec_version matches pin"
                return $true
            }
        }
        Start-Sleep -Seconds 5
    }
    Write-SupervisorLog "reader" "BLOCKED: never reached HEALTHY + exact spec pin within bound"
    return $false
}

# ---- Stage 5: PAPER_LITE (exactly one writer, safety must already be
# coherently RUNNING -- never asserted here) -------------------------------
function Start-PaperLiteStage {
    Write-SupervisorLog "paper-lite" "checking"

    if (Test-ProcessRunning "*paper_lite.py*") {
        Write-SupervisorLog "paper-lite" "already running -- not starting a second writer"
        return $true
    }

    $env:CRUMBLR_DATABASE_URL = Get-CrumblrSecret "CRUMBLR_DATABASE_URL"
    Push-Location $RepoRoot
    # The URL is never a CLI argument (Dev 1 finding, 2026-09-09): it is
    # already in this process's own environment block above, and the child
    # process inherits it from there -- a CLI argument would be visible to
    # any other local process via Get-CimInstance Win32_Process | Select
    # CommandLine.
    uv run python scripts\check_soak_safety_coherent.py var\agent_paper_soak.safety.json
    $safetyOk = ($LASTEXITCODE -eq 0)
    Pop-Location

    if (-not $safetyOk) {
        Write-SupervisorLog "paper-lite" "BLOCKED: safety state is not coherently RUNNING on both stores -- staying stopped, not auto-resetting"
        return $false
    }

    $env:CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL = Get-CrumblrSecret "CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL"
    # Same shared value as the Static Agent stage's CRUMBLR_TRADER_SERVICE_TOKEN
    # -- one stored secret, injected under both env-var names, never two
    # independently editable copies.
    $env:CRUMBLR_PAPER_LITE_AGENT_TOKEN = Get-CrumblrSecret "LOCAL_AGENT_SERVICE_TOKEN"
    if (-not $env:CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL -or -not $env:CRUMBLR_PAPER_LITE_AGENT_TOKEN) {
        Write-SupervisorLog "paper-lite" "BLOCKED: CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL or LOCAL_AGENT_SERVICE_TOKEN not configured in Credential Manager"
        return $false
    }

    # --confirm-paper-incident-clear is required by paper_lite.py on every
    # invocation, but this supervisor may supply it automatically at most
    # ONCE per Windows boot session (owner instruction, 2026-09-09), and
    # fail-closed by construction (Dev 1 BLOCK, 2026-09-09): eligibility
    # is delegated to check_boot_clear_eligible.py, which blocks unless
    # the marker holds a valid, strictly-older boot's claim -- a missing,
    # invalid, or current-boot marker always blocks. A crashed/missing
    # PAPER_LITE later in the same boot is a real failure to investigate,
    # not something to paper over with another automatic CLEAR.
    $currentBootTimeUtc = Get-CurrentBootTimeUtc
    if (-not (Test-BootClearEligible -CurrentBootTimeUtc $currentBootTimeUtc)) {
        Write-SupervisorLog "paper-lite" "BLOCKED: this boot is not eligible for an automatic incident-CLEAR (see check_boot_clear_eligible.py output above -- missing/invalid/already-consumed marker). PAPER_LITE will not be started automatically; investigate, then use scripts\bootstrap_boot_clear_marker.ps1 or an owner-supplied manual start as appropriate."
        return $false
    }

    Write-SupervisorLog "paper-lite" "starting continuous, external Supervisor enabled, real wall-clock (no --fixed-now, no --initialize-paper-safety)"
    Write-SupervisorLog "paper-lite" "claiming this boot session's one automatic incident-CLEAR -- all recovery preconditions passed (Postgres reachable, MT5 authorized, Static Agent READY, reader HEALTHY + exact spec pin, safety coherently RUNNING, zero prior PAPER_LITE writers)"

    # The marker is claimed before the process is even started (not after
    # the poll loop below, and not merely written best-effort): if this
    # atomic claim fails for any reason, PAPER_LITE must not start at all.
    # paper_lite.py records the CLEAR assertion durably as soon as it
    # starts, before its main loop, so the boot's one allowance is spent
    # by the attempt itself -- a crash moments later must not be read as
    # "never happened" and silently permit a second automatic CLEAR.
    if (-not (Set-BootClearMarker -CurrentBootTimeUtc $currentBootTimeUtc)) {
        Write-SupervisorLog "paper-lite" "BLOCKED: could not durably claim the boot-clear marker -- PAPER_LITE will not be started"
        return $false
    }

    $args = @(
        "run","python","scripts\paper_lite.py",
        "--agent-id",$AgentId,
        "--assignment-id",$AssignmentId,
        "--agent-url",$AgentUrl,
        "--code-commit",$CodeCommit,
        "--symbol",$CanonicalSymbol,
        "--timeframe",$Timeframe,
        "--settings","config\agent_paper_soak.yaml",
        "--enable-external-supervisor",
        "--external-supervisor-min-confidence","0.5",
        "--confirm-paper-incident-clear",
        "--operator","host-supervisor",
        "--incident-clear-note","owner-preauthorized host boot recovery; all recovery invariants passed; safety state unchanged and not reset"
    )
    Start-Process -FilePath "uv" -ArgumentList $args -WorkingDirectory $RepoRoot -WindowStyle Hidden

    for ($i = 0; $i -lt 12; $i++) {
        if (Test-ProcessRunning "*paper_lite.py*") {
            Write-SupervisorLog "paper-lite" "process running"
            return $true
        }
        Start-Sleep -Seconds 3
    }
    Write-SupervisorLog "paper-lite" "BLOCKED: process did not stay running"
    return $false
}

# ---- Stage 6: dashboard ---------------------------------------------------
function Start-DashboardStage {
    Write-SupervisorLog "dashboard" "checking"
    if (Test-ProcessRunning "*run_dashboard.py*") {
        Write-SupervisorLog "dashboard" "already running"
        return $true
    }
    $env:CRUMBLR_DATABASE_URL = Get-CrumblrSecret "CRUMBLR_DATABASE_URL"
    $args = @(
        "run","python","scripts\run_dashboard.py",
        "--canonical-symbol",$CanonicalSymbol,
        "--timeframe",$Timeframe,
        "--reader-health","var\live_reader_health.json",
        "--agent-assignment-id",$AssignmentId,
        "--paper-lite-journal-path","var\agent_paper_soak.journal.jsonl",
        "--paper-lite-settings-path","config\agent_paper_soak.yaml",
        "--port",$DashboardPort
    )
    Start-Process -FilePath "uv" -ArgumentList $args -WorkingDirectory $RepoRoot -WindowStyle Hidden
    Write-SupervisorLog "dashboard" "started"
    return $true
}

# ---- Main -----------------------------------------------------------------
Write-SupervisorLog "supervisor" "run starting"

if (-not (Start-PostgresStage)) { Write-SupervisorLog "supervisor" "STOPPED after postgres"; exit 1 }
if (-not (Start-Mt5Stage)) { Write-SupervisorLog "supervisor" "STOPPED after mt5"; exit 1 }
if (-not (Start-StaticAgentStage)) { Write-SupervisorLog "supervisor" "STOPPED after static-agent"; exit 1 }
if (-not (Start-ReaderStage)) { Write-SupervisorLog "supervisor" "STOPPED after reader"; exit 1 }
if (-not (Start-PaperLiteStage)) { Write-SupervisorLog "supervisor" "STOPPED after paper-lite"; exit 1 }
Start-DashboardStage | Out-Null

Write-SupervisorLog "supervisor" "run complete, full chain healthy"
exit 0
