$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"
& $env:PROJECT_NPM --prefix frontend run dev
