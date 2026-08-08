from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_PWSH = REPOSITORY_ROOT / ".tools" / "powershell" / "pwsh.exe"
MANIFEST_HELPER = REPOSITORY_ROOT / "scripts" / "dev-process-manifest.ps1"
STOP_SCRIPT = REPOSITORY_ROOT / "scripts" / "stop-dev.ps1"
START_SCRIPT = REPOSITORY_ROOT / "scripts" / "start-dev.ps1"
TEST_ALL_SCRIPT = REPOSITORY_ROOT / "scripts" / "test-all.ps1"


def run_powershell(script: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            str(PROJECT_PWSH),
            "-NoLogo",
            "-NoProfile",
            "-Command",
            script,
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def run_powershell_marked_result(script: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            str(PROJECT_PWSH),
            "-NoLogo",
            "-NoProfile",
            "-Command",
            script,
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("RESULT:"):
            return json.loads(line.removeprefix("RESULT:"))
    raise AssertionError(f"PowerShell did not emit a marked result:\n{completed.stdout}")


def write_fake_npm_shim(path: Path) -> None:
    path.write_text(
        "\r\n".join(
            [
                "@echo off",
                "chcp 65001 >nul",
                'echo %*>> "%TEST_ALL_NPM_LOG%"',
                'if "%TEST_ALL_FAIL_STAGE%"=="%2" exit /b 7',
                "exit /b 0",
                "",
            ]
        ),
        encoding="utf-8",
    )


def write_non_native_failure_shim(path: Path) -> None:
    path.write_text(
        "\r\n".join(
            [
                '& $env:ComSpec /c exit 9',
                'throw "injected non-native npm failure"',
                "",
            ]
        ),
        encoding="utf-8",
    )


def run_test_all(
    shim_path: Path,
    log_path: Path,
    failed_stage: str = "",
    native_error_action_preference: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["TEST_ALL_NPM_LOG"] = str(log_path)
    environment["TEST_ALL_FAIL_STAGE"] = failed_stage
    command = [
        str(PROJECT_PWSH),
        "-NoLogo",
        "-NoProfile",
    ]
    if native_error_action_preference:
        command.extend(
            [
                "-Command",
                (
                    "$PSNativeCommandUseErrorActionPreference = $true; "
                    f"& '{TEST_ALL_SCRIPT}' -NpmPath '{shim_path}'; "
                    "exit $LASTEXITCODE"
                ),
            ]
        )
    else:
        command.extend(
            [
                "-File",
                str(TEST_ALL_SCRIPT),
                "-NpmPath",
                str(shim_path),
            ]
        )
    return subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


def test_all_runs_complete_checks_in_order(tmp_path: Path) -> None:
    shim_path = tmp_path / "fake-npm.cmd"
    log_path = tmp_path / "npm.log"
    write_fake_npm_shim(shim_path)

    completed = run_test_all(shim_path, log_path)

    assert completed.returncode == 0
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "run frontend:build",
        "run frontend:test",
        "run backend:test",
    ]


@pytest.mark.parametrize(
    ("native_error_action_preference", "failed_stage", "expected_log_lines"),
    [
        (False, "frontend:build", ["run frontend:build"]),
        (False, "frontend:test", ["run frontend:build", "run frontend:test"]),
        (False, "backend:test", ["run frontend:build", "run frontend:test", "run backend:test"]),
        (True, "frontend:build", ["run frontend:build"]),
        (True, "frontend:test", ["run frontend:build", "run frontend:test"]),
        (True, "backend:test", ["run frontend:build", "run frontend:test", "run backend:test"]),
    ],
)
def test_all_stops_at_failed_check_and_propagates_exit_code(
    tmp_path: Path,
    native_error_action_preference: bool,
    failed_stage: str,
    expected_log_lines: list[str],
) -> None:
    shim_path = tmp_path / "fake-npm.cmd"
    log_path = tmp_path / "npm.log"
    write_fake_npm_shim(shim_path)

    completed = run_test_all(
        shim_path,
        log_path,
        failed_stage,
        native_error_action_preference=native_error_action_preference,
    )

    assert completed.returncode == 7
    assert log_path.read_text(encoding="utf-8").splitlines() == expected_log_lines


def test_all_reraises_non_native_npm_failure_despite_stale_exit_code(tmp_path: Path) -> None:
    shim_path = tmp_path / "fake-npm.ps1"
    log_path = tmp_path / "npm.log"
    write_non_native_failure_shim(shim_path)

    completed = run_test_all(shim_path, log_path)

    assert completed.returncode == 1
    assert "injected non-native npm failure" in completed.stderr
    assert not log_path.exists()


def test_stop_scope_has_no_port_based_kill_path() -> None:
    stop_source = STOP_SCRIPT.read_text(encoding="utf-8")

    assert "Get-NetTCPConnection" not in stop_source
    assert "netstat -ano" not in stop_source
    assert "$legacyPorts" not in stop_source
    assert "dev-process-manifest.ps1" in stop_source


def test_manifest_identity_requires_matching_pid_and_start_time() -> None:
    result = run_powershell(
        f"""
. '{MANIFEST_HELPER}'
$process = Get-Process -Id $PID
$startedAtUtc = $process.StartTime.ToUniversalTime().ToString('O')
[ordered]@{{
  matching = Test-DevProcessIdentity -ProcessId $PID -StartedAtUtc $startedAtUtc
  alteredStartTime = Test-DevProcessIdentity -ProcessId $PID -StartedAtUtc '2000-01-01T00:00:00.0000000Z'
  missingProcess = Test-DevProcessIdentity -ProcessId 2147483647 -StartedAtUtc $startedAtUtc
}} | ConvertTo-Json -Compress
"""
    )

    assert result == {
        "matching": True,
        "alteredStartTime": False,
        "missingProcess": False,
    }


def test_manifest_stop_only_removes_after_injected_success() -> None:
    result = run_powershell(
        f"""
. '{MANIFEST_HELPER}'
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dev-processes-" + [guid]::NewGuid().ToString() + ".json")
$process = Get-Process -Id $PID
$startedAtUtc = $process.StartTime.ToUniversalTime().ToString('O')
$matching = [pscustomobject]@{{ role = 'test'; pid = $PID; startedAtUtc = $startedAtUtc }}
$mismatched = [pscustomobject]@{{ role = 'test'; pid = $PID; startedAtUtc = '2000-01-01T00:00:00.0000000Z' }}

try {{
  $called = [System.Collections.Generic.List[int]]::new()
  $state = [pscustomobject]@{{
    allProcessObjects = $true
    failedProcess = $null
    successfulProcess = $null
  }}
  Write-DevProcessManifest -Manifest ([pscustomobject]@{{ version = 1; processes = @($mismatched) }}) -ManifestPath $manifestPath
  $mismatchDiagnostics = @(
    Stop-RecordedProcessTree -ManifestPath $manifestPath -StopCallback {{
      param($ownedProcess)
      $state.allProcessObjects = $state.allProcessObjects -and ($ownedProcess -is [System.Diagnostics.Process])
      if ($ownedProcess -is [System.Diagnostics.Process]) {{
        $called.Add($ownedProcess.Id)
      }}
    }} 3>&1 |
      ForEach-Object {{ $_.Message }}
  )
  $mismatchedCalls = @($called).Count
  $mismatchedEntries = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count

  Write-DevProcessManifest -Manifest ([pscustomobject]@{{ version = 1; processes = @($matching) }}) -ManifestPath $manifestPath
  $failureDiagnostics = @(
    Stop-RecordedProcessTree -ManifestPath $manifestPath -StopCallback {{
      param($ownedProcess)
      $state.allProcessObjects = $state.allProcessObjects -and ($ownedProcess -is [System.Diagnostics.Process])
      if ($ownedProcess -is [System.Diagnostics.Process]) {{
        $state.failedProcess = $ownedProcess
        throw "injected failure for $($ownedProcess.Id)"
      }}
      throw "injected failure for $ownedProcess"
    }} 3>&1 |
      ForEach-Object {{ $_.Message }}
  )
  $failedEntries = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count

  Write-DevProcessManifest -Manifest ([pscustomobject]@{{ version = 1; processes = @($matching) }}) -ManifestPath $manifestPath
  Stop-RecordedProcessTree -ManifestPath $manifestPath -StopCallback {{
    param($ownedProcess)
    $state.allProcessObjects = $state.allProcessObjects -and ($ownedProcess -is [System.Diagnostics.Process])
    if ($ownedProcess -is [System.Diagnostics.Process]) {{
      $state.successfulProcess = $ownedProcess
      $called.Add($ownedProcess.Id)
    }}
  }}
  $successfulCalls = @($called).Count
  $successfulEntries = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count

  $failedProcessClosed = if ($null -eq $state.failedProcess) {{
    $false
  }} else {{
    try {{
      $handle = $state.failedProcess.SafeHandle
      $null -eq $handle -or $handle.IsClosed
    }} catch {{ $true }}
  }}
  $successfulProcessClosed = if ($null -eq $state.successfulProcess) {{
    $false
  }} else {{
    try {{
      $handle = $state.successfulProcess.SafeHandle
      $null -eq $handle -or $handle.IsClosed
    }} catch {{ $true }}
  }}

  [ordered]@{{
    allProcessObjects = $state.allProcessObjects
    mismatchedCalls = $mismatchedCalls
    mismatchDiagnostics = @($mismatchDiagnostics)
    mismatchedEntries = $mismatchedEntries
    failedEntries = $failedEntries
    failedProcessClosed = $failedProcessClosed
    failureDiagnostics = @($failureDiagnostics)
    currentPid = $PID
    successfulCalls = $successfulCalls
    successfulEntries = $successfulEntries
    successfulProcessClosed = $successfulProcessClosed
  }} | ConvertTo-Json -Compress
}} finally {{
  foreach ($path in @($manifestPath, "$manifestPath.tmp")) {{
    if (Test-Path -LiteralPath $path) {{
      Remove-Item -LiteralPath $path -Force
    }}
  }}
}}
"""
    )

    assert result == {
        "allProcessObjects": True,
        "mismatchedCalls": 0,
        "mismatchDiagnostics": [
            f"Skipped recorded development process role=test pid={result['currentPid']} because its start time does not match the manifest entry; the entry was retained."
        ],
        "mismatchedEntries": 1,
        "failedEntries": 1,
        "failedProcessClosed": True,
        "failureDiagnostics": [
            f"Failed to stop recorded development process role=test pid={result['currentPid']}: injected failure for {result['currentPid']}"
        ],
        "currentPid": result["currentPid"],
        "successfulCalls": 1,
        "successfulEntries": 0,
        "successfulProcessClosed": True,
    }


def test_manifest_stop_discards_absent_pid_without_invoking_stop_callback() -> None:
    result = run_powershell(
        f"""
. '{MANIFEST_HELPER}'
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dev-processes-" + [guid]::NewGuid().ToString() + ".json")
$absent = [pscustomobject]@{{ role = 'stale'; pid = 2147483647; startedAtUtc = '2000-01-01T00:00:00.0000000Z' }}

try {{
  $called = [System.Collections.Generic.List[int]]::new()
  Write-DevProcessManifest -Manifest ([pscustomobject]@{{ version = 1; processes = @($absent) }}) -ManifestPath $manifestPath
  Stop-RecordedProcessTree -ManifestPath $manifestPath -StopCallback {{ param($ownedProcess) $called.Add($ownedProcess.Id) }}
  [ordered]@{{
    stopCalls = @($called).Count
    remainingEntries = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count
  }} | ConvertTo-Json -Compress
}} finally {{
  foreach ($path in @($manifestPath, "$manifestPath.tmp")) {{
    if (Test-Path -LiteralPath $path) {{
      Remove-Item -LiteralPath $path -Force
    }}
  }}
}}
"""
    )

    assert result == {"stopCalls": 0, "remainingEntries": 0}


def test_manifest_descendant_capture_holds_exact_process_handles(tmp_path: Path) -> None:
    leaf_script = tmp_path / "leaf.ps1"
    child_script = tmp_path / "child.ps1"
    root_script = tmp_path / "root.ps1"
    child_pid_path = tmp_path / "child.pid"
    grandchild_pid_path = tmp_path / "grandchild.pid"
    leaf_script.write_text("Start-Sleep -Seconds 60\n", encoding="utf-8")
    child_script.write_text(
        """
param([string]$LeafPath, [string]$GrandchildPidPath)
$grandchild = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') `
  -ArgumentList @('-NoLogo', '-NoProfile', '-File', $LeafPath) `
  -WindowStyle Hidden `
  -PassThru
[System.IO.File]::WriteAllText($GrandchildPidPath, [string]$grandchild.Id, [System.Text.Encoding]::UTF8)
$grandchild.WaitForExit()
""".strip()
        + "\n",
        encoding="utf-8",
    )
    root_script.write_text(
        """
param(
  [string]$ChildPath,
  [string]$LeafPath,
  [string]$ChildPidPath,
  [string]$GrandchildPidPath
)
$child = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') `
  -ArgumentList @(
    '-NoLogo', '-NoProfile', '-File', $ChildPath,
    '-LeafPath', $LeafPath,
    '-GrandchildPidPath', $GrandchildPidPath
  ) `
  -WindowStyle Hidden `
  -PassThru
[System.IO.File]::WriteAllText($ChildPidPath, [string]$child.Id, [System.Text.Encoding]::UTF8)
$child.WaitForExit()
""".strip()
        + "\n",
        encoding="utf-8",
    )
    root_process = subprocess.Popen(
        [
            str(PROJECT_PWSH),
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(root_script),
            "-ChildPath",
            str(child_script),
            "-LeafPath",
            str(leaf_script),
            "-ChildPidPath",
            str(child_pid_path),
            "-GrandchildPidPath",
            str(grandchild_pid_path),
        ],
        cwd=REPOSITORY_ROOT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 10
        while (
            (not child_pid_path.exists() or not grandchild_pid_path.exists())
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert child_pid_path.exists(), "child process did not start"
        assert grandchild_pid_path.exists(), "grandchild process did not start"
        child_pid = int(child_pid_path.read_text(encoding="utf-8-sig"))
        grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8-sig"))

        result = run_powershell(
            f"""
. '{MANIFEST_HELPER}'
$root = Get-Process -Id {root_process.pid} -ErrorAction Stop
$null = $root.SafeHandle
$descendants = @(Get-DevProcessDescendants -RootProcess $root)
try {{
  [ordered]@{{
    processIds = @($descendants | ForEach-Object {{ $_.Id }} | Sort-Object)
    openHandleCount = @(
      $descendants | Where-Object {{
        $null -ne $_.SafeHandle -and -not $_.SafeHandle.IsClosed
      }}
    ).Count
  }} | ConvertTo-Json -Compress
}} finally {{
  foreach ($process in $descendants) {{ $process.Close() }}
  $root.Close()
}}
"""
        )

        process_ids = result["processIds"]
        assert isinstance(process_ids, list)
        assert child_pid in process_ids
        assert grandchild_pid in process_ids
        assert result["openHandleCount"] == len(process_ids)

        manifest_path = tmp_path / "dev-processes.json"
        stop_result = run_powershell(
            f"""
. '{MANIFEST_HELPER}'
Add-DevProcessManifestEntry `
  -Role 'test-tree' `
  -ProcessId {root_process.pid} `
  -ManifestPath '{manifest_path}'
Stop-RecordedProcessTree -ManifestPath '{manifest_path}'
[ordered]@{{
  aliveProcessIds = @(
    @({root_process.pid}, {child_pid}, {grandchild_pid}) | Where-Object {{
      $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue)
    }}
  )
  remainingEntries = @(
    (Read-DevProcessManifest -ManifestPath '{manifest_path}').processes
  ).Count
}} | ConvertTo-Json -Compress
"""
        )
        assert stop_result == {"aliveProcessIds": [], "remainingEntries": 0}
    finally:
        subprocess.run(
            ["taskkill", "/PID", str(root_process.pid), "/T", "/F"],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        try:
            root_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            root_process.kill()
            root_process.wait(timeout=5)


def test_manifest_descendant_rows_reject_stale_parent_pid_reuse() -> None:
    result = run_powershell(
        f"""
. '{MANIFEST_HELPER}'
$rootCreated = [DateTimeOffset]::Parse('2026-08-01T00:00:00Z').UtcDateTime
$rows = @(
  [pscustomobject]@{{ ProcessId = 100; ParentProcessId = 1; CreationDate = $rootCreated }},
  [pscustomobject]@{{ ProcessId = 200; ParentProcessId = 100; CreationDate = $rootCreated.AddSeconds(-2) }},
  [pscustomobject]@{{ ProcessId = 201; ParentProcessId = 200; CreationDate = $rootCreated.AddSeconds(-1) }},
  [pscustomobject]@{{ ProcessId = 300; ParentProcessId = 100; CreationDate = $rootCreated.AddSeconds(1) }},
  [pscustomobject]@{{ ProcessId = 301; ParentProcessId = 300; CreationDate = $rootCreated.AddSeconds(2) }}
)
$descendants = @(Get-DevProcessDescendantRows `
  -ProcessRows $rows `
  -RootProcessId 100 `
  -RootCreationDate $rootCreated)
[ordered]@{{
  processIds = @($descendants | ForEach-Object {{ $_.row.ProcessId }})
  depths = @($descendants | ForEach-Object {{ $_.depth }})
}} | ConvertTo-Json -Compress
"""
    )

    assert result == {"processIds": [300, 301], "depths": [1, 2]}


def test_manifest_captures_descendant_when_root_exits_before_descendant_capture(
    tmp_path: Path,
) -> None:
    root_script = tmp_path / "exiting-root.ps1"
    child_pid_path = tmp_path / "orphan-child.pid"
    root_script.write_text(
        """
param([string]$ChildPidPath)
$child = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') `
  -ArgumentList @('-NoLogo', '-NoProfile', '-Command', 'Start-Sleep -Seconds 60') `
  -WindowStyle Hidden `
  -PassThru
[System.IO.File]::WriteAllText($ChildPidPath, [string]$child.Id, [System.Text.Encoding]::UTF8)
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = run_powershell(
        f"""
. '{MANIFEST_HELPER}'
$root = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') `
  -ArgumentList @('-NoLogo', '-NoProfile', '-File', '{root_script}', '-ChildPidPath', '{child_pid_path}') `
  -WindowStyle Hidden `
  -PassThru
$null = $root.SafeHandle
$descendants = @()
$childPid = 0
try {{
  $deadline = (Get-Date).AddSeconds(5)
  while (-not (Test-Path -LiteralPath '{child_pid_path}') -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 20
  }}
  $childPid = [int](Get-Content -LiteralPath '{child_pid_path}' -Encoding UTF8)
  $null = $root.WaitForExit(5000)
  $descendants = @(Get-DevProcessDescendants -RootProcess $root)
  [ordered]@{{
    childPid = $childPid
    descendantProcessIds = @($descendants | ForEach-Object {{ $_.Id }})
  }} | ConvertTo-Json -Compress
}} finally {{
  foreach ($process in $descendants) {{ $process.Close() }}
  $root.Close()
  if ($childPid -gt 0) {{
    $child = Get-Process -Id $childPid -ErrorAction SilentlyContinue
    if ($null -ne $child) {{
      $child.Kill($true)
      $null = $child.WaitForExit(5000)
      $child.Close()
    }}
  }}
}}
"""
    )

    assert result["childPid"] in result["descendantProcessIds"]


def test_manifest_stops_synced_descendant_after_root_exit(tmp_path: Path) -> None:
    root_script = tmp_path / "released-root.ps1"
    child_pid_path = tmp_path / "synced-child.pid"
    release_root_path = tmp_path / "release-root"
    manifest_path = tmp_path / "dev-processes.json"
    root_script.write_text(
        """
param([string]$ChildPidPath, [string]$ReleaseRootPath)
$child = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') `
  -ArgumentList @('-NoLogo', '-NoProfile', '-Command', 'Start-Sleep -Seconds 60') `
  -WindowStyle Hidden `
  -PassThru
[System.IO.File]::WriteAllText($ChildPidPath, [string]$child.Id, [System.Text.Encoding]::UTF8)
while (-not (Test-Path -LiteralPath $ReleaseRootPath)) {
  Start-Sleep -Milliseconds 20
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    result = run_powershell(
        f"""
. '{MANIFEST_HELPER}'
$root = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') `
  -ArgumentList @(
    '-NoLogo', '-NoProfile', '-File', '{root_script}',
    '-ChildPidPath', '{child_pid_path}',
    '-ReleaseRootPath', '{release_root_path}'
  ) `
  -WindowStyle Hidden `
  -PassThru
$childPid = 0
try {{
  $deadline = (Get-Date).AddSeconds(5)
  while (-not (Test-Path -LiteralPath '{child_pid_path}') -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 20
  }}
  $childPid = [int](Get-Content -LiteralPath '{child_pid_path}' -Encoding UTF8)
  Sync-DevProcessManifestTree `
    -Role 'test-tree' `
    -RootProcess $root `
    -ManifestPath '{manifest_path}'
  Set-Content -LiteralPath '{release_root_path}' -Value 'release' -Encoding UTF8
  $null = $root.WaitForExit(5000)
  Stop-RecordedProcessTree -ManifestPath '{manifest_path}'
  [ordered]@{{
    childAlive = $null -ne (Get-Process -Id $childPid -ErrorAction SilentlyContinue)
    remainingEntries = @((Read-DevProcessManifest -ManifestPath '{manifest_path}').processes).Count
  }} | ConvertTo-Json -Compress
}} finally {{
  if (-not $root.HasExited) {{
    $root.Kill($true)
    $null = $root.WaitForExit(5000)
  }}
  $root.Close()
  if ($childPid -gt 0) {{
    $child = Get-Process -Id $childPid -ErrorAction SilentlyContinue
    if ($null -ne $child) {{
      $child.Kill($true)
      $null = $child.WaitForExit(5000)
      $child.Close()
    }}
  }}
}}
exit 0
"""
    )

    assert result == {"childAlive": False, "remainingEntries": 0}


def test_manifest_sync_bounds_cim_query_and_releases_lock_after_timeout(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "dev-processes.json"

    result = run_powershell(
        f"""
. '{MANIFEST_HELPER}'
$state = [pscustomobject]@{{ operationTimeoutSec = $null; lockReacquired = $false }}
$seedEntry = [pscustomobject]@{{
  role = 'seed'
  pid = 2147483647
  startedAtUtc = '2000-01-01T00:00:00.0000000Z'
}}
Write-DevProcessManifest `
  -Manifest ([pscustomobject]@{{ version = 1; processes = @($seedEntry) }}) `
  -ManifestPath '{manifest_path}'
function Get-CimInstance {{
  param(
    [Parameter(Position = 0)]
    [string]$ClassName,
    [int]$OperationTimeoutSec
  )
  $state.operationTimeoutSec = $OperationTimeoutSec
  throw [TimeoutException]::new("injected CIM timeout")
}}

$root = Get-Process -Id $PID
try {{
  try {{
    Sync-DevProcessManifestTree `
      -Role 'test-tree' `
      -RootProcess $root `
      -ManifestPath '{manifest_path}'
  }} catch {{
    $errorMessage = $_.Exception.Message
  }}
  Invoke-WithDevProcessManifestLock `
    -ManifestPath '{manifest_path}' `
    -LockTimeoutMilliseconds 100 `
    -Action {{ $state.lockReacquired = $true }}
  [ordered]@{{
    operationTimeoutSec = $state.operationTimeoutSec
    lockReacquired = $state.lockReacquired
    errorMessage = $errorMessage
    remainingRoles = @(
      (Read-DevProcessManifest -ManifestPath '{manifest_path}').processes |
        ForEach-Object {{ $_.role }}
    )
  }} | ConvertTo-Json -Compress
}} finally {{
  $root.Close()
}}
"""
    )

    assert result == {
        "operationTimeoutSec": 5,
        "lockReacquired": True,
        "errorMessage": "injected CIM timeout",
        "remainingRoles": ["seed"],
    }


def test_manifest_add_waits_for_concurrent_stop_transaction(tmp_path: Path) -> None:
    manifest_path = tmp_path / "dev-processes.json"
    stop_entered_path = tmp_path / "stop-entered"
    release_stop_path = tmp_path / "release-stop"
    old_process = subprocess.Popen(
        [str(PROJECT_PWSH), "-NoLogo", "-NoProfile", "-Command", "Start-Sleep -Seconds 60"],
        cwd=REPOSITORY_ROOT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    new_process = subprocess.Popen(
        [str(PROJECT_PWSH), "-NoLogo", "-NoProfile", "-Command", "Start-Sleep -Seconds 60"],
        cwd=REPOSITORY_ROOT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    stopper: subprocess.Popen[str] | None = None
    adder: subprocess.Popen[str] | None = None
    try:
        run_powershell(
            f"""
. '{MANIFEST_HELPER}'
Add-DevProcessManifestEntry -Role 'backend' -ProcessId {old_process.pid} -ManifestPath '{manifest_path}'
@{{ seeded = $true }} | ConvertTo-Json -Compress
"""
        )
        stopper = subprocess.Popen(
            [
                str(PROJECT_PWSH),
                "-NoLogo",
                "-NoProfile",
                "-Command",
                f"""
. '{MANIFEST_HELPER}'
Stop-RecordedProcessTree -ManifestPath '{manifest_path}' -StopCallback {{
  param($ownedProcess)
  Set-Content -LiteralPath '{stop_entered_path}' -Value 'entered' -Encoding UTF8
  while (-not (Test-Path -LiteralPath '{release_stop_path}')) {{
    Start-Sleep -Milliseconds 20
  }}
}}
""",
            ],
            cwd=REPOSITORY_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + 5
        while not stop_entered_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert stop_entered_path.exists(), "concurrent stop did not enter its transaction"

        adder = subprocess.Popen(
            [
                str(PROJECT_PWSH),
                "-NoLogo",
                "-NoProfile",
                "-Command",
                f"""
. '{MANIFEST_HELPER}'
Add-DevProcessManifestEntry -Role 'frontend' -ProcessId {new_process.pid} -ManifestPath '{manifest_path}'
""",
            ],
            cwd=REPOSITORY_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        add_deadline = time.monotonic() + 2
        while adder.poll() is None and time.monotonic() < add_deadline:
            time.sleep(0.02)
        assert adder.poll() is None, "manifest add escaped the active stop transaction"

        release_stop_path.write_text("release", encoding="utf-8")
        stopper_stdout, stopper_stderr = stopper.communicate(timeout=10)
        adder_stdout, adder_stderr = adder.communicate(timeout=10)
        assert stopper.returncode == 0, f"{stopper_stdout}\n{stopper_stderr}"
        assert adder.returncode == 0, f"{adder_stdout}\n{adder_stderr}"

        result = run_powershell(
            f"""
. '{MANIFEST_HELPER}'
$entries = @((Read-DevProcessManifest -ManifestPath '{manifest_path}').processes)
[ordered]@{{
  count = $entries.Count
  role = if ($entries.Count -eq 1) {{ $entries[0].role }} else {{ $null }}
  pid = if ($entries.Count -eq 1) {{ $entries[0].pid }} else {{ $null }}
}} | ConvertTo-Json -Compress
"""
        )
        assert result == {"count": 1, "role": "frontend", "pid": new_process.pid}
    finally:
        release_stop_path.write_text("release", encoding="utf-8")
        for process in (stopper, adder):
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        for process in (old_process, new_process):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_start_finally_stops_exact_owned_processes_when_manifest_loses_entries() -> None:
    result = run_powershell_marked_result(
        f"""
. '{MANIFEST_HELPER}'
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dev-processes-" + [guid]::NewGuid().ToString() + ".json")
$childProcessIds = [System.Collections.Generic.List[int]]::new()
$children = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

try {{
  $hooks = @{{
    ManifestPath = $manifestPath
    StopCallback = {{ param($ownedProcess) throw "manifest cleanup must not own removed PID=$($ownedProcess.Id)" }}
    TestLocalPortOccupied = {{ param($port) $false }}
    TestHttpReady = {{
      param($url)
      Write-DevProcessManifest `
        -Manifest ([pscustomobject]@{{ version = 1; processes = @() }}) `
        -ManifestPath $manifestPath
      return $true
    }}
    StartManagedProcess = {{
      param($role, $filePath, $arguments, $workingDirectory)
      $child = Start-Process `
        -FilePath (Join-Path $PSHOME 'pwsh.exe') `
        -ArgumentList @('-NoLogo', '-NoProfile', '-Command', 'Start-Sleep -Seconds 60') `
        -WorkingDirectory $workingDirectory `
        -WindowStyle Hidden `
        -PassThru
      $children.Add($child)
      $childProcessIds.Add($child.Id)
      return $child
    }}
    ExitAfterStartup = $true
  }}

  & '{START_SCRIPT}' -NoBrowser -TestHooks $hooks
  $aliveCount = @(
    $childProcessIds | Where-Object {{
      $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue)
    }}
  ).Count
  $closedHandleCount = @(
    $children | Where-Object {{
      try {{
        $handle = $_.SafeHandle
        $null -eq $handle -or $handle.IsClosed
      }} catch {{
        $true
      }}
    }}
  ).Count
  Write-Output ("RESULT:" + ([ordered]@{{
    childCount = $childProcessIds.Count
    aliveCount = $aliveCount
    closedHandleCount = $closedHandleCount
    manifestEntryCount = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count
  }} | ConvertTo-Json -Compress))
}} finally {{
  foreach ($processId in $childProcessIds) {{
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -ne $process) {{
      $process.Kill($true)
      $process.WaitForExit(5000)
      $process.Close()
    }}
  }}
  foreach ($path in @($manifestPath, "$manifestPath.tmp")) {{
    if (Test-Path -LiteralPath $path) {{
      Remove-Item -LiteralPath $path -Force
    }}
  }}
}}
"""
    )

    assert result == {
        "childCount": 2,
        "aliveCount": 0,
        "closedHandleCount": 2,
        "manifestEntryCount": 0,
    }


def test_start_lifecycle_records_started_processes_and_cleans_them_up() -> None:
    result = run_powershell_marked_result(
        f"""
. '{MANIFEST_HELPER}'
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dev-processes-" + [guid]::NewGuid().ToString() + ".json")
$startedRoles = [System.Collections.Generic.List[string]]::new()
$stoppedProcessIds = [System.Collections.Generic.List[int]]::new()
$recordedEntries = [System.Collections.Generic.List[object]]::new()
$currentProcess = Get-Process -Id $PID
$startedAtUtc = $currentProcess.StartTime.ToUniversalTime().ToString('O')

try {{
  $hooks = @{{
    ManifestPath = $manifestPath
    StopCallback = {{
      param($ownedProcess)
      $stoppedProcessIds.Add($ownedProcess.Id)
      foreach ($entry in @((Read-DevProcessManifest -ManifestPath $manifestPath).processes)) {{
        $recordedEntries.Add([pscustomobject]@{{
          role = $entry.role
          pid = $entry.pid
          startedAtUtc = $entry.startedAtUtc
        }})
      }}
    }}
    TestLocalPortOccupied = {{ param($port) $false }}
    TestHttpReady = {{ param($url) $true }}
    StartManagedProcess = {{
      param($role, $filePath, $arguments, $workingDirectory)
      $startedRoles.Add($role)
      [pscustomobject]@{{ Id = $PID }}
    }}
    ExitAfterStartup = $true
  }}

  & '{START_SCRIPT}' -NoBrowser -TestHooks $hooks
  $remainingEntries = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count
  Write-Output ("RESULT:" + ([ordered]@{{
    roles = @($startedRoles)
    stoppedProcessIds = @($stoppedProcessIds)
    recordedEntries = @($recordedEntries)
    currentPid = $PID
    startedAtUtc = $startedAtUtc
    remainingEntries = $remainingEntries
  }} | ConvertTo-Json -Depth 4 -Compress))
}} finally {{
  foreach ($path in @($manifestPath, "$manifestPath.tmp")) {{
    if (Test-Path -LiteralPath $path) {{
      Remove-Item -LiteralPath $path -Force
    }}
  }}
}}
"""
    )

    assert result["roles"] == ["backend", "frontend"]
    assert result["stoppedProcessIds"] == [result["currentPid"], result["currentPid"]]
    assert result["remainingEntries"] == 0
    assert result["recordedEntries"] == [
        {"role": "backend", "pid": result["currentPid"], "startedAtUtc": result["startedAtUtc"]},
        {"role": "frontend", "pid": result["currentPid"], "startedAtUtc": result["startedAtUtc"]},
        {"role": "backend", "pid": result["currentPid"], "startedAtUtc": result["startedAtUtc"]},
        {"role": "frontend", "pid": result["currentPid"], "startedAtUtc": result["startedAtUtc"]},
    ]


def test_start_output_read_failure_cleans_process_owned_before_output_initialization() -> None:
    result = run_powershell_marked_result(
        f"""
. '{MANIFEST_HELPER}'
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dev-processes-" + [guid]::NewGuid().ToString() + ".json")
$fakeProcess = [pscustomobject]@{{ Id = $PID }}
$state = [pscustomobject]@{{
  outputHookCalled = $false
  ownershipVisibleDuringOutputInitialization = $false
  stopReceivedExactProcess = $false
}}

try {{
  $hooks = @{{
    ManifestPath = $manifestPath
    StopCallback = {{ param($ownedProcess) }}
    StopOwnedProcess = {{
      param($ownedProcess)
      $state.stopReceivedExactProcess = [object]::ReferenceEquals($ownedProcess, $fakeProcess)
    }}
    TestLocalPortOccupied = {{ param($port) $false }}
    TestHttpReady = {{ param($url) $true }}
    StartManagedProcess = {{
      param($role, $filePath, $arguments, $workingDirectory)
      return $fakeProcess
    }}
    BeginManagedProcessOutputRead = {{
      param($ownedProcess)
      $state.outputHookCalled = $true
      $state.ownershipVisibleDuringOutputInitialization = @(
        (Read-DevProcessManifest -ManifestPath $manifestPath).processes |
          Where-Object {{ $_.pid -eq $ownedProcess.Id }}
      ).Count -eq 1
      throw 'stream initialization failed'
    }}
    ExitAfterStartup = $true
  }}

  try {{
    & '{START_SCRIPT}' -NoBrowser -TestHooks $hooks
  }} catch {{
    $errorMessage = $_.Exception.Message
  }}

  Write-Output ("RESULT:" + ([ordered]@{{
    errorMessage = $errorMessage
    outputHookCalled = $state.outputHookCalled
    ownershipVisibleDuringOutputInitialization = $state.ownershipVisibleDuringOutputInitialization
    stopReceivedExactProcess = $state.stopReceivedExactProcess
    manifestEntryCount = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count
  }} | ConvertTo-Json -Compress))
}} finally {{
  foreach ($path in @($manifestPath, "$manifestPath.tmp")) {{
    if (Test-Path -LiteralPath $path) {{
      Remove-Item -LiteralPath $path -Force
    }}
  }}
}}
"""
    )

    assert result == {
        "errorMessage": "stream initialization failed",
        "outputHookCalled": True,
        "ownershipVisibleDuringOutputInitialization": True,
        "stopReceivedExactProcess": True,
        "manifestEntryCount": 0,
    }


def test_start_rolls_back_exact_child_when_manifest_registration_fails() -> None:
    result = run_powershell_marked_result(
        f"""
. '{MANIFEST_HELPER}'
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dev-processes-" + [guid]::NewGuid().ToString() + ".json")
$rollbackProcessIds = [System.Collections.Generic.List[int]]::new()
$state = [pscustomobject]@{{ child = $null; childPid = $null; rollbackReceivedExactProcess = $false }}

try {{
  $hooks = @{{
    ManifestPath = $manifestPath
    StopCallback = {{ param($ownedProcess) throw "manifest cleanup must not own unrecorded PID=$($ownedProcess.Id)" }}
    TestLocalPortOccupied = {{ param($port) $false }}
    StartManagedProcess = {{
      param($role, $filePath, $arguments, $workingDirectory)
      $state.child = Start-Process `
        -FilePath (Join-Path $PSHOME 'pwsh.exe') `
        -ArgumentList @('-NoLogo', '-NoProfile', '-Command', 'Start-Sleep -Seconds 60') `
        -WindowStyle Hidden `
        -PassThru
      $state.childPid = $state.child.Id
      New-Item -ItemType Directory -Path "$manifestPath.tmp" | Out-Null
      return $state.child
    }}
    RollbackStartedProcess = {{
      param($process)
      $state.rollbackReceivedExactProcess = [object]::ReferenceEquals($process, $state.child)
      $rollbackProcessIds.Add($process.Id)
      if (-not $process.HasExited) {{
        $process.Kill($true)
      }}
    }}
  }}

  try {{
    & '{START_SCRIPT}' -NoBrowser -TestHooks $hooks
  }} catch {{
    $errorMessage = $_.Exception.Message
    $errorType = $_.Exception.GetType().FullName
  }}

  $childAliveAfterFinally = $null -ne (Get-Process -Id $state.childPid -ErrorAction SilentlyContinue)
  try {{
    $safeHandle = $state.child.SafeHandle
    $handleClosed = $null -eq $safeHandle -or $safeHandle.IsClosed
  }} catch {{
    $handleClosed = $true
  }}
  $manifestEntryCount = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count
  Write-Output ("RESULT:" + ([ordered]@{{
    childPid = $state.childPid
    childAliveAfterFinally = $childAliveAfterFinally
    handleClosed = $handleClosed
    manifestEntryCount = $manifestEntryCount
    rollbackProcessIds = @($rollbackProcessIds)
    rollbackReceivedExactProcess = $state.rollbackReceivedExactProcess
    errorType = $errorType
    manifestErrorPreserved = $errorMessage.Contains("$manifestPath.tmp")
  }} | ConvertTo-Json -Compress))
}} finally {{
  if ($null -ne $state.childPid) {{
    $survivor = Get-Process -Id $state.childPid -ErrorAction SilentlyContinue
    if ($null -ne $survivor) {{
      $survivor.Kill($true)
      $null = $survivor.WaitForExit(5000)
      $survivor.Close()
    }}
  }}
  foreach ($path in @($manifestPath, "$manifestPath.tmp")) {{
    if (Test-Path -LiteralPath $path) {{
      Remove-Item -LiteralPath $path -Recurse -Force
    }}
  }}
}}
"""
    )

    assert result == {
        "childPid": result["childPid"],
        "childAliveAfterFinally": False,
        "handleClosed": True,
        "manifestEntryCount": 0,
        "rollbackProcessIds": [result["childPid"]],
        "rollbackReceivedExactProcess": True,
        "errorType": "System.NotSupportedException",
        "manifestErrorPreserved": True,
    }


def test_start_preserves_manifest_and_rollback_failures_together() -> None:
    result = run_powershell_marked_result(
        f"""
. '{MANIFEST_HELPER}'
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dev-processes-" + [guid]::NewGuid().ToString() + ".json")
$state = [pscustomobject]@{{ process = Get-Process -Id $PID }}

try {{
  $hooks = @{{
    ManifestPath = $manifestPath
    StopCallback = {{ param($ownedProcess) throw "manifest cleanup must not run for PID=$($ownedProcess.Id)" }}
    TestLocalPortOccupied = {{ param($port) $false }}
    StartManagedProcess = {{
      param($role, $filePath, $arguments, $workingDirectory)
      New-Item -ItemType Directory -Path "$manifestPath.tmp" | Out-Null
      return $state.process
    }}
    RollbackStartedProcess = {{ param($startedProcess) throw 'injected rollback failure' }}
  }}

  try {{
    & '{START_SCRIPT}' -NoBrowser -TestHooks $hooks
  }} catch {{
    $exception = $_.Exception
  }}

  $innerMessages = @($exception.InnerExceptions | ForEach-Object {{ $_.Message }} | Where-Object {{ $null -ne $_ }})
  Write-Output ("RESULT:" + ([ordered]@{{
    exceptionType = $exception.GetType().FullName
    innerExceptionCount = @($exception.InnerExceptions).Count
    manifestFailurePresent = @($innerMessages | Where-Object {{ $_.Contains("$manifestPath.tmp") }}).Count -eq 1
    rollbackFailurePresent = @($innerMessages | Where-Object {{ $_ -eq 'injected rollback failure' }}).Count -eq 1
    manifestEntryCount = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count
  }} | ConvertTo-Json -Compress))
}} finally {{
  $state.process.Close()
  foreach ($path in @($manifestPath, "$manifestPath.tmp")) {{
    if (Test-Path -LiteralPath $path) {{
      Remove-Item -LiteralPath $path -Recurse -Force
    }}
  }}
}}
"""
    )

    assert result == {
        "exceptionType": "System.AggregateException",
        "innerExceptionCount": 2,
        "manifestFailurePresent": True,
        "rollbackFailurePresent": True,
        "manifestEntryCount": 0,
    }


def test_start_reuses_compatible_backend_without_recording_or_stopping_it() -> None:
    result = run_powershell_marked_result(
        f"""
. '{MANIFEST_HELPER}'
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dev-processes-" + [guid]::NewGuid().ToString() + ".json")
$startedRoles = [System.Collections.Generic.List[string]]::new()
$stoppedRoles = [System.Collections.Generic.List[string]]::new()

try {{
  $hooks = @{{
    ManifestPath = $manifestPath
    StopCallback = {{
      param($ownedProcess)
      foreach ($entry in @((Read-DevProcessManifest -ManifestPath $manifestPath).processes | Where-Object {{ $_.pid -eq $ownedProcess.Id }})) {{
        $stoppedRoles.Add($entry.role)
      }}
    }}
    TestLocalPortOccupied = {{ param($port) $port -eq 8011 }}
    TestBackendCompatible = {{ param($port) $true }}
    TestHttpReady = {{ param($url) $true }}
    StartManagedProcess = {{
      param($role, $filePath, $arguments, $workingDirectory)
      $startedRoles.Add($role)
      [pscustomobject]@{{ Id = $PID }}
    }}
    ExitAfterStartup = $true
  }}

  & '{START_SCRIPT}' -NoBrowser -TestHooks $hooks
  Write-Output ("RESULT:" + ([ordered]@{{
    startedRoles = @($startedRoles)
    stoppedRoles = @($stoppedRoles)
  }} | ConvertTo-Json -Compress))
}} finally {{
  foreach ($path in @($manifestPath, "$manifestPath.tmp")) {{
    if (Test-Path -LiteralPath $path) {{
      Remove-Item -LiteralPath $path -Force
    }}
  }}
}}
"""
    )

    assert result == {"startedRoles": ["frontend"], "stoppedRoles": ["frontend"]}


def test_start_rejects_occupied_frontend_without_stopping_processes() -> None:
    result = run_powershell_marked_result(
        f"""
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dev-processes-" + [guid]::NewGuid().ToString() + ".json")
$startedRoles = [System.Collections.Generic.List[string]]::new()
$stopCalls = [System.Collections.Generic.List[int]]::new()

try {{
  $hooks = @{{
    ManifestPath = $manifestPath
    StopCallback = {{ param($ownedProcess) $stopCalls.Add($ownedProcess.Id) }}
    TestLocalPortOccupied = {{ param($port) $true }}
    TestBackendCompatible = {{ param($port) $true }}
    StartManagedProcess = {{ param($role, $filePath, $arguments, $workingDirectory) $startedRoles.Add($role) }}
  }}
  try {{
    & '{START_SCRIPT}' -NoBrowser -TestHooks $hooks
  }} catch {{
    $errorMessage = $_.Exception.Message
  }}
  Write-Output ("RESULT:" + ([ordered]@{{
    errorMessage = $errorMessage
    startedRoles = @($startedRoles)
    stopCalls = @($stopCalls)
  }} | ConvertTo-Json -Compress))
}} finally {{
  foreach ($path in @($manifestPath, "$manifestPath.tmp")) {{
    if (Test-Path -LiteralPath $path) {{
      Remove-Item -LiteralPath $path -Force
    }}
  }}
}}
"""
    )

    assert result == {
        "errorMessage": "Frontend port 5174 is occupied. Close the process using this port before starting the frontend.",
        "startedRoles": [],
        "stopCalls": [],
    }


def test_start_rejects_incompatible_backend_without_stopping_processes() -> None:
    result = run_powershell_marked_result(
        f"""
$manifestPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dev-processes-" + [guid]::NewGuid().ToString() + ".json")
$startedRoles = [System.Collections.Generic.List[string]]::new()
$stopCalls = [System.Collections.Generic.List[int]]::new()

try {{
  $hooks = @{{
    ManifestPath = $manifestPath
    StopCallback = {{ param($ownedProcess) $stopCalls.Add($ownedProcess.Id) }}
    TestLocalPortOccupied = {{ param($port) $port -eq 8011 }}
    TestBackendCompatible = {{ param($port) $false }}
    StartManagedProcess = {{ param($role, $filePath, $arguments, $workingDirectory) $startedRoles.Add($role) }}
  }}
  try {{
    & '{START_SCRIPT}' -NoBrowser -TestHooks $hooks
  }} catch {{
    $errorMessage = $_.Exception.Message
  }}
  Write-Output ("RESULT:" + ([ordered]@{{
    errorMessage = $errorMessage
    startedRoles = @($startedRoles)
    stopCalls = @($stopCalls)
  }} | ConvertTo-Json -Compress))
}} finally {{
  foreach ($path in @($manifestPath, "$manifestPath.tmp")) {{
    if (Test-Path -LiteralPath $path) {{
      Remove-Item -LiteralPath $path -Force
    }}
  }}
}}
"""
    )

    assert result == {
        "errorMessage": "Backend port 8011 is occupied by an incompatible service. Close the process using this port before starting the project.",
        "startedRoles": [],
        "stopCalls": [],
    }
