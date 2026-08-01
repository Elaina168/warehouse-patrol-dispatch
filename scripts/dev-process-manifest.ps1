$ErrorActionPreference = "Stop"

$script:DevProcessManifestPath = Join-Path (Split-Path -Parent $PSScriptRoot) ".runtime\dev-processes.json"

function Get-DevProcessManifestMutexName {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath
  )

  $normalizedPath = [System.IO.Path]::GetFullPath($ManifestPath).ToUpperInvariant()
  $sha256 = [System.Security.Cryptography.SHA256]::Create()
  try {
    $pathHash = [Convert]::ToHexString(
      $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($normalizedPath))
    )
  } finally {
    $sha256.Dispose()
  }
  return "Local\WarehousePatrol.DevProcessManifest.$pathHash"
}

function Invoke-WithDevProcessManifestLock {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,

    [Parameter(Mandatory = $true)]
    [scriptblock]$Action,

    [int]$LockTimeoutMilliseconds = 10000
  )

  $mutex = [System.Threading.Mutex]::new(
    $false,
    (Get-DevProcessManifestMutexName -ManifestPath $ManifestPath)
  )
  $lockAcquired = $false
  try {
    try {
      $lockAcquired = $mutex.WaitOne($LockTimeoutMilliseconds)
    } catch [System.Threading.AbandonedMutexException] {
      $lockAcquired = $true
    }
    if (-not $lockAcquired) {
      throw "Timed out waiting for development-process manifest lock: $ManifestPath"
    }
    & $Action
  } finally {
    if ($lockAcquired) {
      $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
  }
}

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

function Get-MatchingDevProcess {
  param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,

    [Parameter(Mandatory = $true)]
    [string]$StartedAtUtc
  )

  $process = $null
  try {
    $recordedStartTime = [DateTimeOffset]::Parse(
      $StartedAtUtc,
      [System.Globalization.CultureInfo]::InvariantCulture,
      [System.Globalization.DateTimeStyles]::RoundtripKind
    )
    $process = Get-Process -Id $ProcessId -ErrorAction Stop
    $null = $process.SafeHandle
    $actualStartTime = [DateTimeOffset]::Parse(
      $process.StartTime.ToUniversalTime().ToString("O"),
      [System.Globalization.CultureInfo]::InvariantCulture,
      [System.Globalization.DateTimeStyles]::RoundtripKind
    )
    if ($actualStartTime -ne $recordedStartTime) {
      $process.Close()
      return $null
    }
    return $process
  } catch {
    if ($null -ne $process) {
      $process.Close()
    }
    return $null
  }
}

function Get-DevProcessCreationTicks {
  param(
    [Parameter(Mandatory = $true)]
    [datetime]$CreationDate
  )

  $utcCreationDate = $CreationDate.ToUniversalTime()
  return $utcCreationDate.Ticks - ($utcCreationDate.Ticks % 10)
}

function Get-DevProcessDescendantRows {
  param(
    [Parameter(Mandatory = $true)]
    [object[]]$ProcessRows,

    [Parameter(Mandatory = $true)]
    [int]$RootProcessId,

    [Parameter(Mandatory = $true)]
    [datetime]$RootCreationDate
  )

  $depthByProcessId = @{
    $RootProcessId = 0
  }
  $creationTicksByProcessId = @{
    $RootProcessId = Get-DevProcessCreationTicks -CreationDate $RootCreationDate
  }
  $descendantRows = [System.Collections.Generic.List[object]]::new()
  $unresolvedRows = [System.Collections.Generic.List[object]]::new()
  foreach ($row in $ProcessRows) {
    if ([int]$row.ProcessId -ne $RootProcessId) {
      $unresolvedRows.Add($row)
    }
  }

  do {
    $changedAny = $false
    foreach ($row in @($unresolvedRows)) {
      $parentProcessId = [int]$row.ParentProcessId
      if (-not $depthByProcessId.ContainsKey($parentProcessId)) {
        continue
      }
      $processId = [int]$row.ProcessId
      $creationTicks = Get-DevProcessCreationTicks -CreationDate ([datetime]$row.CreationDate)
      if ($creationTicks -lt [long]$creationTicksByProcessId[$parentProcessId]) {
        $null = $unresolvedRows.Remove($row)
        $changedAny = $true
        continue
      }
      $depth = [int]$depthByProcessId[$parentProcessId] + 1
      $depthByProcessId[$processId] = $depth
      $creationTicksByProcessId[$processId] = $creationTicks
      $descendantRows.Add([pscustomobject]@{
          row = $row
          depth = $depth
        })
      $null = $unresolvedRows.Remove($row)
      $changedAny = $true
    }
  } while ($changedAny)

  return @($descendantRows)
}

function Get-DevProcessDescendants {
  param(
    [Parameter(Mandatory = $true)]
    [System.Diagnostics.Process]$RootProcess
  )

  $null = $RootProcess.SafeHandle
  $rootProcessId = $RootProcess.Id
  $rootStartTimeUtc = $RootProcess.StartTime.ToUniversalTime()
  $processRows = @(
    Get-CimInstance Win32_Process |
      Select-Object ProcessId, ParentProcessId, CreationDate
  )
  $rootRow = @(
    $processRows | Where-Object { [int]$_.ProcessId -eq $rootProcessId }
  ) | Select-Object -First 1
  if ($null -eq $rootRow) {
    return @()
  }

  $rootStartTicks = Get-DevProcessCreationTicks -CreationDate $rootStartTimeUtc
  $rootCimStartTimeUtc = ([datetime]$rootRow.CreationDate).ToUniversalTime()
  $rootCimStartTicks = Get-DevProcessCreationTicks -CreationDate $rootCimStartTimeUtc
  if ($rootStartTicks -ne $rootCimStartTicks) {
    throw "Root process identity changed before descendant capture PID=$rootProcessId."
  }

  $descendantRows = @(
    Get-DevProcessDescendantRows `
      -ProcessRows $processRows `
      -RootProcessId $rootProcessId `
      -RootCreationDate $rootCimStartTimeUtc
  )

  $capturedProcesses = [System.Collections.Generic.List[object]]::new()
  try {
    foreach ($candidate in @($descendantRows | Sort-Object depth -Descending)) {
      $process = Get-Process -Id ([int]$candidate.row.ProcessId) -ErrorAction SilentlyContinue
      if ($null -eq $process) {
        continue
      }
      try {
        $null = $process.SafeHandle
        $actualStartTimeUtc = $process.StartTime.ToUniversalTime()
        $actualStartTicks = Get-DevProcessCreationTicks -CreationDate $actualStartTimeUtc
        $cimStartTimeUtc = ([datetime]$candidate.row.CreationDate).ToUniversalTime()
        $cimStartTicks = Get-DevProcessCreationTicks -CreationDate $cimStartTimeUtc
        if ($actualStartTicks -ne $cimStartTicks) {
          $process.Close()
          continue
        }
        $capturedProcesses.Add($process)
      } catch {
        $process.Close()
        throw
      }
    }
    return @($capturedProcesses)
  } catch {
    foreach ($process in $capturedProcesses) {
      $process.Close()
    }
    throw
  }
}

function Test-DevProcessIdentity {
  param(
    [Parameter(Mandatory = $true)]
    [int]$ProcessId,

    [Parameter(Mandatory = $true)]
    [string]$StartedAtUtc
  )

  $process = Get-MatchingDevProcess -ProcessId $ProcessId -StartedAtUtc $StartedAtUtc
  if ($null -eq $process) {
    return $false
  }
  try {
    return $true
  } finally {
    $process.Close()
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

  Invoke-WithDevProcessManifestLock -ManifestPath $ManifestPath -Action {
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
}

function Stop-NewlyStartedProcessTree {
  param(
    [Parameter(Mandatory = $true)]
    [System.Diagnostics.Process]$Process,

    [scriptblock]$StopCallback = {
      param([System.Diagnostics.Process]$StartedProcess)
      if (-not $StartedProcess.HasExited) {
        $StartedProcess.Kill($true)
      }
    },

    [int]$WaitTimeoutMilliseconds = 5000
  )

  $stopError = $null
  try {
    & $StopCallback $Process
    if (-not $Process.WaitForExit($WaitTimeoutMilliseconds)) {
      throw "Timed out waiting for newly started process tree PID=$($Process.Id) to stop."
    }
  } catch {
    $stopError = $_.Exception
  } finally {
    try {
      $Process.Close()
    } catch {
      if ($null -eq $stopError) {
        $stopError = $_.Exception
      } else {
        $stopError = [AggregateException]::new(
          "Failed to stop and close newly started process PID=$($Process.Id).",
          [Exception[]]@($stopError, $_.Exception)
        )
      }
    }
  }

  if ($null -ne $stopError) {
    throw $stopError
  }
}

function Stop-RecordedProcessTree {
  param(
    [string]$ManifestPath = $script:DevProcessManifestPath,

    [scriptblock]$StopCallback = {
      param([System.Diagnostics.Process]$OwnedProcess)
      $rootProcessId = $OwnedProcess.Id
      $descendants = @(Get-DevProcessDescendants -RootProcess $OwnedProcess)
      $stopErrors = [System.Collections.Generic.List[Exception]]::new()
      try {
        try {
          if (-not $OwnedProcess.HasExited) {
            $OwnedProcess.Kill()
          }
        } catch {
          $stopErrors.Add($_.Exception)
        }

        foreach ($descendant in $descendants) {
          try {
            if (-not $descendant.HasExited) {
              $descendant.Kill()
            }
          } catch {
            $stopErrors.Add($_.Exception)
          }
        }

        try {
          if (-not $OwnedProcess.WaitForExit(5000)) {
            $stopErrors.Add([TimeoutException]::new(
                "Timed out waiting for recorded process root PID=$rootProcessId to stop."
              ))
          }
        } catch {
          $stopErrors.Add($_.Exception)
        }
        foreach ($descendant in $descendants) {
          try {
            if (-not $descendant.WaitForExit(5000)) {
              $stopErrors.Add([TimeoutException]::new(
                  "Timed out waiting for recorded process descendant PID=$($descendant.Id) to stop."
                ))
            }
          } catch {
            $stopErrors.Add($_.Exception)
          }
        }
      } finally {
        foreach ($descendant in $descendants) {
          try {
            $descendant.Close()
          } catch {
            $stopErrors.Add($_.Exception)
          }
        }
      }

      if ($stopErrors.Count -eq 1) {
        throw $stopErrors[0]
      }
      if ($stopErrors.Count -gt 1) {
        throw [AggregateException]::new(
          "Failed to stop recorded process tree PID=$rootProcessId.",
          [Exception[]]$stopErrors.ToArray()
        )
      }
    }
  )

  Invoke-WithDevProcessManifestLock -ManifestPath $ManifestPath -Action {
    $manifest = Read-DevProcessManifest -ManifestPath $ManifestPath
    $remainingEntries = [System.Collections.Generic.List[object]]::new()
    $stopFailures = [System.Collections.Generic.List[object]]::new()

    foreach ($entry in @($manifest.processes)) {
      $ownedProcess = Get-MatchingDevProcess `
        -ProcessId ([int]$entry.pid) `
        -StartedAtUtc ([string]$entry.startedAtUtc)
      if ($null -eq $ownedProcess) {
        if ($null -eq (Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue)) {
          continue
        }
        $remainingEntries.Add($entry)
        continue
      }

      $stopError = $null
      try {
        & $StopCallback $ownedProcess
      } catch {
        $stopError = $_.Exception
      }
      try {
        $ownedProcess.Close()
      } catch {
        if ($null -eq $stopError) {
          $stopError = $_.Exception
        } else {
          $stopError = [AggregateException]::new(
            "Failed to stop and close recorded development process PID=$($entry.pid).",
            [Exception[]]@($stopError, $_.Exception)
          )
        }
      }
      if ($null -ne $stopError) {
        $remainingEntries.Add($entry)
        $stopFailures.Add([pscustomobject]@{
            role = [string]$entry.role
            pid = [int]$entry.pid
            message = $stopError.Message
          })
      }
    }

    $manifest.processes = @($remainingEntries)
    Write-DevProcessManifest -Manifest $manifest -ManifestPath $ManifestPath
    foreach ($failure in $stopFailures) {
      Write-Warning "Failed to stop recorded development process role=$($failure.role) pid=$($failure.pid): $($failure.message)"
    }
  }
}
