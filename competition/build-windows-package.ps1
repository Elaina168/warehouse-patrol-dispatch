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
$workDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("warehouse-patrol-pyinstaller-" + [guid]::NewGuid().ToString("N"))
$executablePath = Join-Path $outputDirectory "WarehousePatrol\WarehousePatrol.exe"
$packageReadmeSource = Join-Path $repositoryRoot "competition\3s\package-README.md"
$packageReadmeTarget = Join-Path $outputDirectory "README.md"

if (-not (Test-Path -LiteralPath $packageReadmeSource -PathType Leaf)) {
  throw "缺少评委运行说明：$packageReadmeSource"
}

# 发布目录是专用输出目录，构建前清理旧包，保证最终目录只保留正式交付项。
if (Test-Path -LiteralPath $outputDirectory) {
  Get-ChildItem -LiteralPath $outputDirectory -Force | ForEach-Object {
    Remove-Item -LiteralPath $_.FullName -Recurse -Force
  }
} else {
  New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

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

Copy-Item -LiteralPath $packageReadmeSource -Destination $packageReadmeTarget -Force

if (-not $SkipSourcePackage) {
  & "$PSScriptRoot\create-source-package.ps1" `
    -RepositoryRoot $repositoryRoot `
    -OutputPath (Join-Path $outputDirectory "WarehousePatrol-source.zip")
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

$expectedOutputNames = @("WarehousePatrol", "README.md")
if (-not $SkipSourcePackage) {
  $expectedOutputNames += "WarehousePatrol-source.zip"
}
$actualOutputNames = @(Get-ChildItem -LiteralPath $outputDirectory -Force | ForEach-Object { $_.Name })
$unexpectedOutputNames = @($actualOutputNames | Where-Object { $_ -notin $expectedOutputNames })
$missingOutputNames = @($expectedOutputNames | Where-Object { $_ -notin $actualOutputNames })
if ($unexpectedOutputNames.Count -gt 0 -or $missingOutputNames.Count -gt 0) {
  throw "发布目录结构不符合预期。缺少：$($missingOutputNames -join ', ')；多出：$($unexpectedOutputNames -join ', ')"
}

if (Test-Path -LiteralPath $workDirectory) {
  Remove-Item -LiteralPath $workDirectory -Recurse -Force
}

Write-Output $executablePath
