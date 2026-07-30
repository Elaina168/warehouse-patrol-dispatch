$ErrorActionPreference = "Stop"

$script:DevProcessManifestPath = Join-Path (Split-Path -Parent $PSScriptRoot) ".runtime\dev-processes.json"

function Read-DevProcessManifest {
  param(
    [string]$ManifestPath = $script:DevProcessManifestPath
  )

  if (-not (Test-Path -LiteralPath $ManifestPath)) {
    return [pscustomobject]@{
      version = 1
      processes = @()
    }
  }

  $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json -DateKind String
  if ($manifest.version -ne 1) {
    throw "Unsupported development-process manifest version: $($manifest.version)"
  }

  if ($null -eq $manifest.processes) {
    $manifest.processes = @()
  }

  return $manifest
}

function Write-DevProcessManifest {
  param(
    [Parameter(Mandatory = $true)]
    [pscustomobject]$Manifest,

    [string]$ManifestPath = $script:DevProcessManifestPath
  )

  if ($Manifest.version -ne 1) {
    throw "Unsupported development-process manifest version: $($Manifest.version)"
  }

  $manifestDirectory = Split-Path -Parent $ManifestPath
  New-Item -ItemType Directory -Path $manifestDirectory -Force | Out-Null
  $temporaryPath = "$ManifestPath.tmp"
  $Manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryPath -Encoding UTF8
  Move-Item -LiteralPath $temporaryPath -Destination $ManifestPath -Force
}

function Test-DevProcessIdentity {
  param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,

    [Parameter(Mandatory = $true)]
    [string]$StartedAtUtc
  )

  try {
    $recordedStartTime = [DateTimeOffset]::Parse(
      $StartedAtUtc,
      [System.Globalization.CultureInfo]::InvariantCulture,
      [System.Globalization.DateTimeStyles]::RoundtripKind
    )
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    $actualStartTime = [DateTimeOffset]::Parse(
      $process.StartTime.ToUniversalTime().ToString("O"),
      [System.Globalization.CultureInfo]::InvariantCulture,
      [System.Globalization.DateTimeStyles]::RoundtripKind
    )
    return $actualStartTime -eq $recordedStartTime
  } catch {
    return $false
  }
}

function Add-DevProcessManifestEntry {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Role,

    [Parameter(Mandatory = $true)]
    [int]$ProcessId,

    [string]$ManifestPath = $script:DevProcessManifestPath
  )

  $process = Get-Process -Id $ProcessId -ErrorAction Stop
  $manifest = Read-DevProcessManifest -ManifestPath $ManifestPath
  $processes = [System.Collections.Generic.List[object]]::new()
  foreach ($entry in @($manifest.processes)) {
    $processes.Add($entry)
  }
  $processes.Add([pscustomobject]@{
      role = $Role
      pid = $ProcessId
      startedAtUtc = $process.StartTime.ToUniversalTime().ToString("O")
    })
  $manifest.processes = @($processes)
  Write-DevProcessManifest -Manifest $manifest -ManifestPath $ManifestPath
}

function Stop-RecordedProcessTree {
  param(
    [string]$ManifestPath = $script:DevProcessManifestPath,

    [scriptblock]$StopCallback = {
      param([int]$ProcessId)
      $taskkillOutput = cmd /c "taskkill /PID $ProcessId /T /F 2>&1"
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to stop process tree PID=${ProcessId}: $taskkillOutput"
      }
    }
  )

  $manifest = Read-DevProcessManifest -ManifestPath $ManifestPath
  $remainingEntries = [System.Collections.Generic.List[object]]::new()

  foreach ($entry in @($manifest.processes)) {
    if ($null -eq (Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue)) {
      continue
    }

    if (-not (Test-DevProcessIdentity -ProcessId ([int]$entry.pid) -StartedAtUtc ([string]$entry.startedAtUtc))) {
      $remainingEntries.Add($entry)
      continue
    }

    try {
      & $StopCallback ([int]$entry.pid)
    } catch {
      $remainingEntries.Add($entry)
    }
  }

  $manifest.processes = @($remainingEntries)
  Write-DevProcessManifest -Manifest $manifest -ManifestPath $ManifestPath
}
