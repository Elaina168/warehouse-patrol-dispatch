from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_PWSH = REPOSITORY_ROOT / ".tools" / "powershell" / "pwsh.exe"
MANIFEST_HELPER = REPOSITORY_ROOT / "scripts" / "dev-process-manifest.ps1"
STOP_SCRIPT = REPOSITORY_ROOT / "scripts" / "stop-dev.ps1"


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
