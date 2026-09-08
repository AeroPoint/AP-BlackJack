<#
.SYNOPSIS
    One-time setup for the blackjack development environment.

.DESCRIPTION
    Installs uv, pins Python 3.13, and syncs the locked dependency set.
    Optionally installs the Rust toolchain for the native core.

    Safe to re-run: every step checks before acting.

    See markdown/adr/ADR-0003-environment.md for why uv and why 3.13.

.PARAMETER WithRust
    Also install rustup and build the native accelerator.

.PARAMETER WithApp
    Also install Node dependencies for the web front end.

.EXAMPLE
    .\environment\bootstrap.ps1
    .\environment\bootstrap.ps1 -WithRust -WithApp
#>
[CmdletBinding()]
param(
    [switch]$WithRust,
    [switch]$WithApp
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Ok($message)   { Write-Host "    $message" -ForegroundColor Green }
function Write-Skip($message) { Write-Host "    $message" -ForegroundColor DarkGray }

Write-Host "Blackjack solver -- environment bootstrap" -ForegroundColor White
Write-Host "Repository: $RepoRoot"

# --- uv -----------------------------------------------------------------------
Write-Step "Checking for uv"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Skip "uv already installed: $(uv --version)"
} else {
    Write-Host "    Installing uv (MIT/Apache-2.0) from astral.sh..."
    # The official installer. Reviewed rather than piped blindly: it writes to
    # %USERPROFILE%\.local\bin and modifies PATH for the current user only.
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    Write-Ok "uv installed"
}

# --- Python + dependencies ----------------------------------------------------
Write-Step "Syncing the Python environment"
Push-Location $RepoRoot
try {
    # .python-version pins 3.13; uv fetches it if the machine does not have it.
    uv python install
    uv sync --all-extras
    Write-Ok "Environment ready. uv.lock is the source of truth."
} finally {
    Pop-Location
}

# --- Rust ---------------------------------------------------------------------
if ($WithRust) {
    Write-Step "Checking for the Rust toolchain"
    if (Get-Command cargo -ErrorAction SilentlyContinue) {
        Write-Skip "cargo already installed: $(cargo --version)"
    } else {
        Write-Host "    Installing rustup..."
        $installer = Join-Path $env:TEMP 'rustup-init.exe'
        Invoke-WebRequest -Uri 'https://win.rustup.rs/x86_64' -OutFile $installer
        & $installer -y --default-toolchain stable
        $env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
        Write-Ok "Rust installed"
    }

    Write-Step "Building the native core"
    Push-Location $RepoRoot
    try {
        uv run maturin develop --release --manifest-path crates/blackjack-core/Cargo.toml
        Write-Ok "blackjack_core built"
    } catch {
        Write-Warning "Native core build failed. The engine works without it, only slower."
        Write-Warning $_.Exception.Message
    } finally {
        Pop-Location
    }
} else {
    Write-Step "Rust toolchain"
    Write-Skip "Skipped. Re-run with -WithRust to build the native accelerator."
}

# --- Web ----------------------------------------------------------------------
if ($WithApp) {
    Write-Step "Installing web dependencies"
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm not found. Install Node.js 20+ from https://nodejs.org and re-run."
    }
    Push-Location (Join-Path $RepoRoot 'apps/web')
    try {
        npm install
        Write-Ok "Web dependencies installed"
    } finally {
        Pop-Location
    }
}

# --- Verify -------------------------------------------------------------------
Write-Step "Verifying"
Push-Location $RepoRoot
try {
    uv run bj solve --rules vegas6-h17
    Write-Ok "The solver runs."
} finally {
    Pop-Location
}

Write-Host "`nDone." -ForegroundColor Green
Write-Host @"

Next steps:
    uv run bj chart --rules vegas6-h17 --importance
    uv run bj explain T6 T
    uv run bj indices --system hi-lo
    uv run pytest -m "not slow"

Documentation: markdown/ReadMe.md
"@
