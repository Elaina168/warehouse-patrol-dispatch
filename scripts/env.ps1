if ($PSVersionTable.PSVersion.Major -lt 7) {
  throw "PowerShell 7 or later is required. Run .\.tools\powershell\pwsh.exe instead of powershell.exe."
}

$ErrorActionPreference = "Stop"

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8NoBom
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom
$PSDefaultParameterValues["Get-Content:Encoding"] = "UTF8"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$nodeDir = "C:\nvm4w\nodejs"
$nodeExe = "$nodeDir\node.exe"
$npmCmd = "$nodeDir\npm.cmd"
$projectPwsh = Join-Path $repoRoot ".tools\powershell\pwsh.exe"

if (-not (Test-Path $nodeExe)) {
  throw "Node.js was not found at $nodeDir. Run nvm use <version> or update scripts/env.ps1."
}

$env:Path = "$nodeDir;$env:Path"
$env:PROJECT_NODE = $nodeExe
$env:PROJECT_NPM = $npmCmd
$env:PROJECT_PWSH = $projectPwsh
$env:PROJECT_FRONTEND_PORT = "5174"
$env:PROJECT_BACKEND_PORT = "8011"
Set-Location $repoRoot

function Get-VersionText {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,

    [Parameter(Mandatory = $true)]
    [string[]]$Arguments
  )

  try {
    $output = & $FilePath @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
      return "unavailable"
    }
    return ($output | Select-Object -First 1)
  } catch {
    return "unavailable"
  }
}

Write-Host "Repository: $repoRoot"
Write-Host "PowerShell: $(if (Test-Path $projectPwsh) { Get-VersionText -FilePath $projectPwsh -Arguments @('--version') } else { 'not installed' })"
Write-Host "Node: $(Get-VersionText -FilePath $nodeExe -Arguments @('--version'))"
Write-Host "npm: $(Get-VersionText -FilePath $npmCmd -Arguments @('--version'))"
Write-Host "Python: $(Get-VersionText -FilePath 'python' -Arguments @('--version'))"
Write-Host "Frontend port: $env:PROJECT_FRONTEND_PORT"
Write-Host "Backend port: $env:PROJECT_BACKEND_PORT"
