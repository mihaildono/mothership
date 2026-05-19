# setup.ps1 - Mother dependency setup for Windows.
# Installs uv, creates a venv with Python 3.12, and installs dependencies.
#
# Usage (PowerShell):
#   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#   .\setup.ps1

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "==> Mothership mother setup (Windows)"

# ── uv ────────────────────────────────────────────────────────────────────────
$uvExe = (Get-Command uv -ErrorAction SilentlyContinue)?.Source
if (-not $uvExe) {
    $localUv = "$env:LOCALAPPDATA\Programs\uv\uv.exe"
    if (Test-Path $localUv) {
        $uvExe = $localUv
        Write-Host "==> uv found at $uvExe"
    } else {
        Write-Host "==> Installing uv..."
        irm https://astral.sh/uv/install.ps1 | iex
        $uvExe = "$env:LOCALAPPDATA\Programs\uv\uv.exe"
    }
} else {
    Write-Host "==> uv already installed ($( & $uvExe --version )) -- skipping"
}

# ── Virtual environment ───────────────────────────────────────────────────────
$venv   = "$ScriptDir\.venv"
$python = "$venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "==> Creating virtual environment (Python 3.12)..."
    & $uvExe venv $venv --python 3.12 --seed
} else {
    Write-Host "==> Virtual environment already exists -- skipping"
}

# ── Python dependencies ───────────────────────────────────────────────────────
Write-Host "==> Installing Python dependencies..."
& $python -m pip install --upgrade pip -q
& $python -m pip install -r "$ScriptDir\requirements.txt"

Write-Host ""
Write-Host "==> Setup complete."
Write-Host "    NOTE: Nebula setup (key/cert generation) requires WSL or a macOS/Linux machine."
Write-Host "    Run: .\start.ps1  to launch the mother (after .env is in place)."
