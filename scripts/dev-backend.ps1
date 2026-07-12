$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"
& "$PSScriptRoot\..\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --app-dir "$PWD" --reload --host 127.0.0.1 --port $env:PROJECT_BACKEND_PORT
