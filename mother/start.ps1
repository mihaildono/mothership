# start.ps1 - Start the Mothership mother server on Windows.
#
# NOTE: The mother is designed to run on macOS or Linux.
# Running it on Windows is possible for local dev but Nebula setup
# (nebula/setup-mother.sh) still requires WSL or a Linux/macOS machine.
#
# Usage (PowerShell):
#   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#   .\start.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile   = "$ScriptDir\.env"

# ── Load .env ─────────────────────────────────────────────────────────────────
if (-not (Test-Path $EnvFile)) {
    Write-Host ""
    Write-Host "  ERROR: $EnvFile not found."
    Write-Host "  Run nebula/setup-mother.sh (on macOS/Linux or WSL) first."
    Write-Host ""
    exit 1
}

# Parse .env into current process environment
foreach ($line in Get-Content $EnvFile) {
    $line = $line.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { continue }
    if ($line -match "^([^=]+)=(.*)$") {
        [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), "Process")
    }
}

if (-not $env:MOTHER_API_KEY) {
    Write-Host "ERROR: MOTHER_API_KEY not set in .env"
    exit 1
}

# ── uv ────────────────────────────────────────────────────────────────────────
$uvExe = (Get-Command uv -ErrorAction SilentlyContinue)?.Source
if (-not $uvExe) {
    $localUv = "$env:LOCALAPPDATA\Programs\uv\uv.exe"
    if (Test-Path $localUv) {
        $uvExe = $localUv
    } else {
        Write-Host "==> Installing uv..."
        irm https://astral.sh/uv/install.ps1 | iex
        $uvExe = "$env:LOCALAPPDATA\Programs\uv\uv.exe"
    }
}

# ── Virtual environment ───────────────────────────────────────────────────────
$venv   = "$ScriptDir\.venv"
$python = "$venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "==> Creating virtual environment (Python 3.12)..."
    & $uvExe venv $venv --python 3.12 --seed
    & $python -m pip install --upgrade pip -q
    & $python -m pip install -r "$ScriptDir\requirements.txt" -q
}

# ── Launch ────────────────────────────────────────────────────────────────────
Write-Host "==> Starting mother on port 8765..."
& $python "$ScriptDir\main.py"
