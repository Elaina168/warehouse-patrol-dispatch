param(
  [switch]$NoBrowser,
  [switch]$ValidateOnly
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

Stop-RecordedProcessTree

$startedProcesses = New-Object System.Collections.Generic.List[System.Diagnostics.Process]

function Test-HttpReady {
  param([string]$Url)

  try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
    return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
  } catch {
    return $false
  }
}

function Test-BackendCompatible {
  param([int]$Port)

  try {
    $openapi = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/openapi.json" -TimeoutSec 2
    return $openapi.Content.Contains("/api/sessions")
  } catch {
    return $false
  }
}

function Test-LocalPortOccupied {
  param([int]$Port)

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
  $startedProcesses.Add($process)
  Add-DevProcessManifestEntry -Role $Name -ProcessId $process.Id
  Write-Host "$Name started. PID=$($process.Id)"
}

function Stop-StartedProcesses {
  if ($startedProcesses.Count -gt 0) {
    Stop-RecordedProcessTree
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

  if (-not $NoBrowser) {
    Start-Process $frontendUrl
  }

  Write-Host ""
  Write-Host "Project is running."
  Write-Host "Frontend: $frontendUrl"
  Write-Host "Backend:  $backendHealthUrl"
  Write-Host "Press Ctrl+C in this terminal to stop both services."

  while ($true) {
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
