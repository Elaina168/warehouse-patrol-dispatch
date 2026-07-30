from __future__ import annotations

import json
import os
import subprocess
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
  Write-DevProcessManifest -Manifest ([pscustomobject]@{{ version = 1; processes = @($mismatched) }}) -ManifestPath $manifestPath
  Stop-RecordedProcessTree -ManifestPath $manifestPath -StopCallback {{ param($processId) $called.Add($processId) }}
  $mismatchedCalls = @($called).Count
  $mismatchedEntries = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count

  Write-DevProcessManifest -Manifest ([pscustomobject]@{{ version = 1; processes = @($matching) }}) -ManifestPath $manifestPath
  Stop-RecordedProcessTree -ManifestPath $manifestPath -StopCallback {{ param($processId) throw "injected failure for $processId" }}
  $failedEntries = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count

  Write-DevProcessManifest -Manifest ([pscustomobject]@{{ version = 1; processes = @($matching) }}) -ManifestPath $manifestPath
  Stop-RecordedProcessTree -ManifestPath $manifestPath -StopCallback {{ param($processId) $called.Add($processId) }}
  $successfulCalls = @($called).Count
  $successfulEntries = @((Read-DevProcessManifest -ManifestPath $manifestPath).processes).Count

  [ordered]@{{
    mismatchedCalls = $mismatchedCalls
    mismatchedEntries = $mismatchedEntries
    failedEntries = $failedEntries
    successfulCalls = $successfulCalls
    successfulEntries = $successfulEntries
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
        "mismatchedCalls": 0,
        "mismatchedEntries": 1,
        "failedEntries": 1,
        "successfulCalls": 1,
        "successfulEntries": 0,
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
  Stop-RecordedProcessTree -ManifestPath $manifestPath -StopCallback {{ param($processId) $called.Add($processId) }}
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
      param($processId)
      $stoppedProcessIds.Add($processId)
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
      param($processId)
      foreach ($entry in @((Read-DevProcessManifest -ManifestPath $manifestPath).processes | Where-Object {{ $_.pid -eq $processId }})) {{
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
    StopCallback = {{ param($processId) $stopCalls.Add($processId) }}
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
    StopCallback = {{ param($processId) $stopCalls.Add($processId) }}
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
