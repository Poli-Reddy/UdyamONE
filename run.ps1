# Windows quick start; optional -Port 8001 when 8000 is already in use.
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) {
  python -m venv .venv
}
& .\.venv\Scripts\python.exe -m ensurepip --upgrade
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
$AppOptions = @("app:app", "--reload", "--host", "127.0.0.1", "--port", "$Port")
if (Test-Path ".env") { $AppOptions += @("--env-file", ".env") }
& .\.venv\Scripts\python.exe -m uvicorn @AppOptions
