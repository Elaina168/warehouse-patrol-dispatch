$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"
& $env:PROJECT_NPM --prefix frontend run build
& "$PSScriptRoot\..\.venv\Scripts\python.exe" -m pytest backend/tests
