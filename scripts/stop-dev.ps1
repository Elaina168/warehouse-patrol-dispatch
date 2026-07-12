$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"

$activePorts = @(
  [int]$env:PROJECT_FRONTEND_PORT,
  [int]$env:PROJECT_BACKEND_PORT
) | Select-Object -Unique
$legacyPorts = @(8000, 8001) | Where-Object { $activePorts -notcontains $_ }
$ports = @($activePorts + $legacyPorts) | Select-Object -Unique

function Get-PortProcessIds {
  param([int]$Port)

  $ids = New-Object System.Collections.Generic.HashSet[int]

  $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
  foreach ($connection in $connections) {
    if ($connection.OwningProcess -gt 0) {
      $null = $ids.Add([int]$connection.OwningProcess)
    }
  }

  $netstatRows = netstat -ano | Select-String ":$Port"
  foreach ($row in $netstatRows) {
    $columns = ($row.Line -replace "^\s+", "") -split "\s+"
    if ($columns.Length -ge 5 -and $columns[1].StartsWith("127.0.0.1:$Port")) {
      $processId = 0
      if ([int]::TryParse($columns[-1], [ref]$processId) -and $processId -gt 0) {
        $null = $ids.Add($processId)
      }
    }
  }

  return $ids
}

function Stop-ProcessTree {
  param([int]$ProcessId)

  $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  $processName = if ($process) { $process.ProcessName } else { "unknown" }
  Write-Host "Stopping process tree. PID=$ProcessId ($processName)"
  $taskkillOutput = cmd /c "taskkill /PID $ProcessId /T /F 2>&1"
  if ($LASTEXITCODE -ne 0) {
    Write-Host $taskkillOutput
    if ($process) {
      Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
  }
}

foreach ($port in $ports) {
  $processIds = Get-PortProcessIds -Port $port
  foreach ($processId in $processIds) {
    Stop-ProcessTree -ProcessId $processId
  }
}

Start-Sleep -Milliseconds 500

$remainingActive = @()
$remainingLegacy = @()
foreach ($port in $activePorts) {
  $processIds = Get-PortProcessIds -Port $port
  foreach ($processId in $processIds) {
    $remainingActive += "port $port PID=$processId"
  }
}

foreach ($port in $legacyPorts) {
  $processIds = Get-PortProcessIds -Port $port
  foreach ($processId in $processIds) {
    $remainingLegacy += "port $port PID=$processId"
  }
}

if ($remainingActive.Length -gt 0) {
  Write-Host "Warning: active dev ports still appear occupied: $($remainingActive -join ', ')"
  Write-Host "If the backend on $env:PROJECT_BACKEND_PORT is compatible, start-dev.ps1 will reuse it instead of changing ports."
}

if ($remainingLegacy.Length -gt 0) {
  Write-Host "Warning: legacy ports still appear occupied: $($remainingLegacy -join ', ')"
}

if ($remainingActive.Length -eq 0) {
  Write-Host "Active dev ports cleared: $($activePorts -join ', ')"
} else {
  Write-Host "Dev port cleanup attempted: $($activePorts -join ', ')"
}
