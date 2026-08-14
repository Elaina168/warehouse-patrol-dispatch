param(
  [string]$NpmPath,
  [string]$PythonPath,
  [switch]$SkipSourcePackage
)

if ($PSVersionTable.PSVersion.Major -lt 7) {
  throw "PowerShell 7 or later is required. Install PowerShell 7 and run pwsh.exe instead of powershell.exe."
}

$ErrorActionPreference = "Stop"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$repositoryRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$npm = if ($NpmPath) {
  (Resolve-Path -LiteralPath $NpmPath).Path
} else {
  (Get-Command npm.cmd -CommandType Application -ErrorAction Stop).Source
}
$python = if ($PythonPath) {
  (Resolve-Path -LiteralPath $PythonPath).Path
} else {
  (Resolve-Path -LiteralPath (Join-Path $repositoryRoot ".venv\Scripts\python.exe")).Path
}
$outputDirectory = Join-Path $repositoryRoot "output\3s-competition-build"
$workDirectory = Join-Path $outputDirectory "pyinstaller-work"
$executablePath = Join-Path $outputDirectory "WarehousePatrol\WarehousePatrol.exe"

Set-Location $repositoryRoot

& $python "$PSScriptRoot\packaging.py" validate-build-environment
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

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

& $python "$PSScriptRoot\packaging.py" validate-amd64-pe $executablePath
if ($LASTEXITCODE -ne 0) {
  exit $LASTEXITCODE
}

if (-not $SkipSourcePackage) {
  & "$PSScriptRoot\create-source-package.ps1" `
    -RepositoryRoot $repositoryRoot `
    -OutputPath (Join-Path $outputDirectory "WarehousePatrol-source.zip")
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

Write-Output $executablePath
