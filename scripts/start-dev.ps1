param(
  [switch]$NoBrowser,
  [switch]$ValidateOnly,
  [hashtable]$TestHooks
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"
. "$PSScriptRoot\dev-process-manifest.ps1"

$frontendPort = [int]$env:PROJECT_FRONTEND_PORT
$backendPort = [int]$env:PROJECT_BACKEND_PORT
$frontendUrl = "http://127.0.0.1:$frontendPort"
$backendHealthUrl = "http://127.0.0.1:$backendPort/health"
$backendPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"

if (-not (Test-Path $backendPython)) {
  throw "Backend virtual environment was not found. Expected: $backendPython"
}

if (-not (Test-Path (Join-Path $PSScriptRoot "..\frontend\node_modules"))) {
  throw "Frontend dependencies were not found. Run: npm --prefix frontend install"
}

if ($ValidateOnly) {
  Write-Host "Dev startup validation passed."
  exit 0
}

function Invoke-RecordedProcessCleanup {
  if ($TestHooks -and $TestHooks.ContainsKey("ManifestPath")) {
    if ($TestHooks.ContainsKey("StopCallback")) {
      Stop-RecordedProcessTree -ManifestPath $TestHooks.ManifestPath -StopCallback $TestHooks.StopCallback
      return
    }
    Stop-RecordedProcessTree -ManifestPath $TestHooks.ManifestPath
    return
  }

  Stop-RecordedProcessTree
}

function Add-StartedProcessToManifest {
  param(
    [string]$Role,
    [object]$Process
  )

  if ($Process -is [System.Diagnostics.Process]) {
    if ($TestHooks -and $TestHooks.ContainsKey("ManifestPath")) {
      Sync-DevProcessManifestTree -Role $Role -RootProcess $Process -ManifestPath $TestHooks.ManifestPath
      return
    }
    Sync-DevProcessManifestTree -Role $Role -RootProcess $Process
    return
  }
  if ($TestHooks -and $TestHooks.ContainsKey("ManifestPath")) {
    Add-DevProcessManifestEntry -Role $Role -ProcessId $Process.Id -ManifestPath $TestHooks.ManifestPath
    return
  }

  Add-DevProcessManifestEntry -Role $Role -ProcessId $Process.Id
}

function Add-ManagedProcessOwnership {
  param(
    [string]$Role,
    [object]$Process
  )

  $startedProcesses.Add($Process)
  try {
    Add-StartedProcessToManifest -Role $Role -Process $Process
  } catch {
    $manifestError = $_.Exception
    $null = $startedProcesses.Remove($Process)
    try {
      if ($TestHooks -and $TestHooks.ContainsKey("RollbackStartedProcess")) {
        Stop-NewlyStartedProcessTree -Process $Process -StopCallback $TestHooks.RollbackStartedProcess
      } else {
        Stop-NewlyStartedProcessTree -Process $Process
      }
    } catch {
      throw [AggregateException]::new(
        "Failed to register and roll back newly started process role=$Role pid=$($Process.Id).",
        [Exception[]]@($manifestError, $_.Exception)
      )
    }
    throw $manifestError
  }
  $startedProcessOwnerships.Add([pscustomobject]@{
      role = $Role
      process = $Process
    })
}

function Sync-StartedProcessOwnership {
  foreach ($ownership in $startedProcessOwnerships) {
    if ($ownership.process -isnot [System.Diagnostics.Process]) {
      continue
    }
    if ($TestHooks -and $TestHooks.ContainsKey("ManifestPath")) {
      Sync-DevProcessManifestTree `
        -Role $ownership.role `
        -RootProcess $ownership.process `
        -ManifestPath $TestHooks.ManifestPath
      continue
    }
    Sync-DevProcessManifestTree `
      -Role $ownership.role `
      -RootProcess $ownership.process
  }
}

Invoke-RecordedProcessCleanup

$startedProcesses = New-Object System.Collections.Generic.List[object]
$startedProcessOwnerships = New-Object System.Collections.Generic.List[object]

function Test-HttpReady {
  param([string]$Url)

  if ($TestHooks -and $TestHooks.ContainsKey("TestHttpReady")) {
    return & $TestHooks.TestHttpReady $Url
  }

  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
    return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
  } catch {
    return $false
  }
}

function Test-BackendCompatible {
  param([int]$Port)

  if ($TestHooks -and $TestHooks.ContainsKey("TestBackendCompatible")) {
    return & $TestHooks.TestBackendCompatible $Port
  }

  try {
    $openapi = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/openapi.json" -TimeoutSec 2
    return $openapi.Content.Contains("/api/sessions")
  } catch {
    return $false
  }
}

function Test-LocalPortOccupied {
  param([int]$Port)

  if ($TestHooks -and $TestHooks.ContainsKey("TestLocalPortOccupied")) {
    return & $TestHooks.TestLocalPortOccupied $Port
  }

  $client = [System.Net.Sockets.TcpClient]::new()
  try {
    $connection = $client.ConnectAsync("127.0.0.1", $Port)
    if (-not $connection.Wait(1000)) {
      return $false
    }
    return $client.Connected
  } catch {
    return $false
  } finally {
    $client.Dispose()
  }
}

function Start-ManagedProcess {
  param(
    [string]$Name,
    [string]$FilePath,
    [string]$Arguments,
    [string]$WorkingDirectory
  )

  if ($TestHooks -and $TestHooks.ContainsKey("StartManagedProcess")) {
    $process = & $TestHooks.StartManagedProcess $Name $FilePath $Arguments $WorkingDirectory
    if ($null -eq $process) {
      throw "Failed to start $Name."
    }
    Add-ManagedProcessOwnership -Role $Name -Process $process
    Write-Host "$Name started. PID=$($process.Id)"
    return
  }

  $psi = [System.Diagnostics.ProcessStartInfo]::new()
  $psi.FileName = $FilePath
  $psi.Arguments = $Arguments
  $psi.WorkingDirectory = $WorkingDirectory
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true

  $process = [System.Diagnostics.Process]::new()
  $process.StartInfo = $psi
  $null = Register-ObjectEvent -InputObject $process -EventName OutputDataReceived -MessageData $Name -Action {
    if ($EventArgs.Data) {
      Write-Host "[$($Event.MessageData)] $($EventArgs.Data)"
    }
  }
  $null = Register-ObjectEvent -InputObject $process -EventName ErrorDataReceived -MessageData $Name -Action {
    if ($EventArgs.Data) {
      Write-Host "[$($Event.MessageData)] $($EventArgs.Data)"
    }
  }

  if (-not $process.Start()) {
    throw "Failed to start $Name."
  }

  $process.BeginOutputReadLine()
  $process.BeginErrorReadLine()
  Add-ManagedProcessOwnership -Role $Name -Process $process
  Write-Host "$Name started. PID=$($process.Id)"
}

function Stop-StartedProcesses {
  if ($startedProcesses.Count -eq 0) {
    return
  }

  $cleanupErrors = [System.Collections.Generic.List[Exception]]::new()
  try {
    Invoke-RecordedProcessCleanup
  } catch {
    $cleanupErrors.Add($_.Exception)
  }

  foreach ($process in $startedProcesses) {
    if ($null -eq $process) {
      continue
    }
    try {
      if ($TestHooks -and $TestHooks.ContainsKey("StopOwnedProcess")) {
        & $TestHooks.StopOwnedProcess $process
      } elseif ($process -is [System.Diagnostics.Process]) {
        Stop-NewlyStartedProcessTree -Process $process
      }
    } catch {
      $cleanupErrors.Add($_.Exception)
    }
  }

  try {
    Invoke-RecordedProcessCleanup
  } catch {
    $cleanupErrors.Add($_.Exception)
  }

  if ($cleanupErrors.Count -eq 1) {
    throw $cleanupErrors[0]
  }
  if ($cleanupErrors.Count -gt 1) {
    throw [AggregateException]::new(
      "Failed to clean up one or more started development processes.",
      [Exception[]]@($cleanupErrors)
    )
  }
}

function Wait-ForService {
  param(
    [string]$Name,
    [string]$Url,
    [int]$TimeoutSeconds = 45
  )

  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    Sync-StartedProcessOwnership
    foreach ($process in $startedProcesses) {
      if ($process.HasExited) {
        throw "$Name startup failed because PID=$($process.Id) exited."
      }
    }

    if (Test-HttpReady -Url $Url) {
      Write-Host "$Name is ready: $Url"
      return
    }

    Start-Sleep -Milliseconds 500
  }

  throw "$Name did not become ready within $TimeoutSeconds seconds: $Url"
}

try {
  Write-Host "Starting backend and frontend..."

  if (Test-LocalPortOccupied -Port $backendPort) {
    if (-not (Test-BackendCompatible -Port $backendPort)) {
      throw "Backend port $backendPort is occupied by an incompatible service. Close the process using this port before starting the project."
    }
    Write-Host "Reusing compatible backend on port $backendPort."
  } else {
    Start-ManagedProcess `
      -Name "backend" `
      -FilePath $backendPython `
      -Arguments "-m uvicorn backend.app.main:app --app-dir `"$PWD`" --reload --host 127.0.0.1 --port $backendPort" `
      -WorkingDirectory $PWD
  }

  if (Test-LocalPortOccupied -Port $frontendPort) {
    throw "Frontend port $frontendPort is occupied. Close the process using this port before starting the frontend."
  }

  Start-ManagedProcess `
    -Name "frontend" `
    -FilePath $env:PROJECT_NPM `
    -Arguments "--prefix frontend run dev -- --host 127.0.0.1 --port $frontendPort" `
    -WorkingDirectory $PWD

  Wait-ForService -Name "backend" -Url $backendHealthUrl
  Wait-ForService -Name "frontend" -Url $frontendUrl
  Sync-StartedProcessOwnership

  if (-not $NoBrowser) {
    Start-Process $frontendUrl
  }

  Write-Host ""
  Write-Host "Project is running."
  Write-Host "Frontend: $frontendUrl"
  Write-Host "Backend:  $backendHealthUrl"
  Write-Host "Press Ctrl+C in this terminal to stop both services."

  if ($TestHooks -and $TestHooks.ContainsKey("ExitAfterStartup") -and $TestHooks.ExitAfterStartup) {
    return
  }

  while ($true) {
    Sync-StartedProcessOwnership
    foreach ($process in $startedProcesses) {
      if ($process.HasExited) {
        throw "Managed process PID=$($process.Id) exited."
      }
    }
    Start-Sleep -Seconds 1
  }
} finally {
  Stop-StartedProcesses
}
