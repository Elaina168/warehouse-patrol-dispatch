param([string]$NpmPath)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"

$npm = if ($NpmPath) { $NpmPath } else { $env:PROJECT_NPM }
foreach ($scriptName in @("frontend:build", "frontend:test", "backend:test")) {
  try {
    & $npm run $scriptName
  } catch {
    if ($_.Exception -is [System.Management.Automation.NativeCommandExitException]) {
      exit $LASTEXITCODE
    }
    throw
  }
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}
exit 0
