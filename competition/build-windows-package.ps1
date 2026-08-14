param([string]$NpmPath)

if ($PSVersionTable.PSVersion.Major -lt 7) {
  throw "PowerShell 7 or later is required. Run .\.tools\powershell\pwsh.exe instead of powershell.exe."
}

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\..\scripts\env.ps1"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$npm = if ($NpmPath) { $NpmPath } else { $env:PROJECT_NPM }
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$outputDirectory = Join-Path $repositoryRoot "output\3s-competition-build"
$workDirectory = Join-Path $outputDirectory "pyinstaller-work"
$executablePath = Join-Path $outputDirectory "WarehousePatrol\WarehousePatrol.exe"

& $npm run frontend:build
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --distpath $outputDirectory `
  --workpath $workDirectory `
  "$repositoryRoot\competition\warehouse_patrol.spec"
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}
if (-not (Test-Path -LiteralPath $executablePath)) {
  throw "PyInstaller 未生成预期的可执行文件：$executablePath"
}

& "$PSScriptRoot\create-source-package.ps1" `
  -RepositoryRoot $repositoryRoot `
  -OutputPath (Join-Path $outputDirectory "WarehousePatrol-source.zip")
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

Write-Output $executablePath
