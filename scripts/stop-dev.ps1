$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"
. "$PSScriptRoot\dev-process-manifest.ps1"

Stop-RecordedProcessTree
