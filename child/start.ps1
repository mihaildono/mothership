# start.ps1 - Start the Mothership child agent on Windows.
# Installs missing dependencies automatically on first run.
#
# Usage (PowerShell):
#   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#   .\start.ps1
#
# Or via manage.py (recommended):
#   python manage.py child start

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── Ollama ────────────────────────────────────────────────────────────────────
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "==> Ollama not found — installing..."
    $installer = "$env:TEMP\OllamaSetup.exe"
    Invoke-WebRequest "https://ollama.ai/download/OllamaSetup.exe" -OutFile $installer
    Start-Process $installer -ArgumentList "/S" -Wait
    # Refresh PATH
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH", "User")
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

# ── config.toml ───────────────────────────────────────────────────────────────
if (-not (Test-Path "$ScriptDir\config.toml")) {
    Write-Host "==> No config.toml found — launching configurator..."
    & $python "$ScriptDir\configure.py"
}

# ── Launch ────────────────────────────────────────────────────────────────────
Write-Host "==> Starting child agent..."
& $python "$ScriptDir\main.py"
