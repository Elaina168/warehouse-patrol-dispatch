# Final Three Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复最终复审确认的三个问题，并用聚焦回归、完整测试和真实开发服务 smoke 证明在线调度平台保持正常。

**Architecture:** 前端错误处理通过显式会话上下文区分 create 与 reset；benchmark 在 partial 删除成功时建立明确提交点；进程清理只增加身份不匹配诊断，不改变 ownership 或终止策略。三个任务分别完成红—绿闭环，最后统一执行全平台验证。

**Tech Stack:** React 19、TypeScript 5、Vitest 3、Python 3.11+、pytest、PowerShell 7.6、FastAPI、Vite 6。

## Global Constraints

- 不修改调度算法、API 路由、场景结构、端口配置或进程归属判定规则。
- 不重构会话请求协调器。
- 不改变 benchmark 报告字段或文件格式。
- 不新增端口扫描、进程名匹配、命令行匹配或仅凭 PID 的终止行为。
- 所有源码和测试保持 UTF-8；新增代码注释必须使用中文。
- 不修改或清理未跟踪的 `output/`。
- 不合并或推送分支。

---

### Task 1: Preserve a valid session after reset HTTP 4xx

**Files:**
- Modify: `frontend/src/main.tsx:152-167,366-369,575-599`
- Test: `frontend/src/main.test.ts:75-107`

**Interfaces:**
- Consumes: `classifySessionRequestFailure(error, "mutation") -> SessionFailureDecision`
- Produces: `applySessionRequestFailure(error, context, setters)`, where `context` is `{ hasUsableSession: boolean }`

- [ ] **Step 1: Split the existing regression into create and reset expectations**

Replace the combined test with two tests. The create case passes `{ hasUsableSession: false }` and expects `dispatchStatus: "error"`. The reset case passes `{ hasUsableSession: true }` and expects `dispatchStatus: "ready"` while preserving both visible error strings:

```ts
type RequestFailureTestState = {
  apiStatus: "checking" | "online" | "offline" | "error";
  dispatchStatus: "loading" | "ready" | "error";
  dispatchError: string | null;
  operationError: string | null;
};

function createRequestFailureState(): RequestFailureTestState {
  return {
    apiStatus: "checking",
    dispatchStatus: "loading",
    dispatchError: null,
    operationError: null
  };
}

function requestFailureSetters(state: RequestFailureTestState) {
  return {
    setApiStatus: (status: RequestFailureTestState["apiStatus"]) => {
      state.apiStatus = status;
    },
    setDispatchStatus: (status: RequestFailureTestState["dispatchStatus"]) => {
      state.dispatchStatus = status;
    },
    setDispatchError: (message: string) => {
      state.dispatchError = message;
    },
    setOperationError: (message: string) => {
      state.operationError = message;
    }
  };
}

it("keeps create HTTP 4xx online without marking a missing session ready", () => {
  const state = createRequestFailureState();
  applySessionRequestFailure(
    new ApiRequestError(422, "session failed: invalid request"),
    { hasUsableSession: false },
    requestFailureSetters(state)
  );
  expect(state.apiStatus).toBe("online");
  expect(state.dispatchStatus).toBe("error");
});

it("keeps a retained session ready after reset HTTP 4xx", () => {
  const state = createRequestFailureState();
  applySessionRequestFailure(
    new ApiRequestError(409, "session reset failed: invalid snapshot"),
    { hasUsableSession: true },
    requestFailureSetters(state)
  );
  expect(state).toEqual({
    apiStatus: "online",
    dispatchStatus: "ready",
    dispatchError: "session reset failed: invalid snapshot",
    operationError: "session reset failed: invalid snapshot"
  });
});
```

Keep helper setup local to the `session request coordination` test block; do not move test-only state builders into production code.

- [ ] **Step 2: Run the reset test and verify RED**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- src/main.test.ts
```

Expected: the new call signature or reset-ready expectation fails because the production helper still unconditionally writes `"error"`.

- [ ] **Step 3: Implement the explicit retained-session decision**

Change the production signature and status selection:

```ts
export function applySessionRequestFailure(
  error: unknown,
  context: { hasUsableSession: boolean },
  setters: {
    setApiStatus: (status: "checking" | "online" | "offline" | "error") => void;
    setDispatchStatus: (status: "loading" | "ready" | "error") => void;
    setDispatchError: (message: string) => void;
    setOperationError: (message: string) => void;
  }
): void {
  const decision = classifySessionRequestFailure(error, "mutation");
  const message = error instanceof Error ? error.message : "unknown error";
  const dispatchStatus = decision.dispatchStatus === "ready" && !context.hasUsableSession
    ? "error"
    : decision.dispatchStatus;
  setters.setApiStatus(decision.apiStatus);
  setters.setDispatchStatus(dispatchStatus);
  setters.setDispatchError(message);
  setters.setOperationError(message);
}
```

Pass `{ hasUsableSession: false }` from the create-session catch and `{ hasUsableSession: true }` from the reset catch. Do not infer this state from error text or HTTP status alone.

- [ ] **Step 4: Run the frontend regression and verify GREEN**

Run the command from Step 2. Expected: all `main.test.ts` tests pass, including both new 4xx cases.

- [ ] **Step 5: Inspect the Task 1 diff**

Run:

```powershell
git diff -- frontend/src/main.tsx frontend/src/main.test.ts
```

Confirm only the helper signature, its two call sites, and the two regression cases changed.

### Task 2: Make benchmark publication success and failure atomic

**Files:**
- Modify: `backend/benchmarks/reporting.py:264-303`
- Test: `backend/tests/test_algorithm_benchmark.py:782-1033`

**Interfaces:**
- Consumes: `_rollback_final_bundle(...)`, `_cleanup_transaction_files(paths, raise_errors=False)`, `_raise_write_error(...)`
- Produces: unchanged public signature `write_final_report(output_dir: Path, report: BenchmarkReport) -> None`

- [ ] **Step 1: Add partial-deletion rollback coverage**

Add a parametrized test for an existing bundle and no existing bundle. Write a partial report, install a `Path.unlink` failure only for `results.partial.json`, call `write_final_report`, and assert:

```python
@pytest.mark.parametrize("has_existing_bundle", [True, False])
def test_algorithm_final_report_rolls_back_when_partial_delete_fails(
    tmp_path,
    monkeypatch,
    has_existing_bundle,
) -> None:
    report = BenchmarkReport.create({}, [_benchmark_run("scale-r4-t15", 1)])
    write_partial_report(tmp_path, report)
    partial_path = tmp_path / "results.partial.json"
    partial_content = partial_path.read_bytes()
    original_files = {name: f"old {name}".encode() for name in FINAL_REPORT_FILE_NAMES}
    if has_existing_bundle:
        for name, content in original_files.items():
            (tmp_path / name).write_bytes(content)
    real_unlink = Path.unlink
    delete_error = PermissionError("locked partial")

    def fail_partial_delete(path, *args, **kwargs):
        if Path(path) == partial_path:
            raise delete_error
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_partial_delete)
    with pytest.raises(OSError) as exc_info:
        write_final_report(tmp_path, report)

    assert exc_info.value.__cause__ is delete_error
    assert partial_path.read_bytes() == partial_content
```

For the existing case, assert all old bytes are restored. For the empty case, assert no final exists. In both cases call `_assert_no_final_report_transaction_files(tmp_path)`.

- [ ] **Step 2: Add post-commit cleanup coverage**

Create an old complete bundle, allow partial deletion, but make `Path.unlink` persistently fail for one `.backup` path. Assert `write_final_report` does not raise, partial is absent, all three final files contain new report data, and a diagnostic backup file may remain:

```python
def test_algorithm_final_report_cleanup_failure_after_commit_keeps_new_bundle(
    tmp_path,
    monkeypatch,
) -> None:
    report = BenchmarkReport.create({}, [_benchmark_run("scale-r4-t15", 1)])
    write_partial_report(tmp_path, report)
    for name in FINAL_REPORT_FILE_NAMES:
        (tmp_path / name).write_bytes(f"old {name}".encode())
    real_unlink = Path.unlink

    def fail_one_backup_cleanup(path, *args, **kwargs):
        candidate = Path(path)
        if candidate.suffix == ".backup" and candidate.name.startswith(".results.json."):
            raise PermissionError("locked backup")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one_backup_cleanup)
    write_final_report(tmp_path, report)

    assert not (tmp_path / "results.partial.json").exists()
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))["runs"]
    assert list(tmp_path.glob(".results.json.*.backup"))
```

- [ ] **Step 3: Run the two new tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm_benchmark.py -k "partial_delete_fails or cleanup_failure_after_commit"
```

Expected: partial deletion leaves new final files published, and backup cleanup still raises a report write error.

- [ ] **Step 4: Roll back partial deletion failure before the commit point**

Replace the partial deletion exception path with the existing rollback pattern:

```python
        try:
            partial_path.unlink(missing_ok=True)
        except Exception as exc:
            rollback_error: Exception | None = None
            try:
                _rollback_final_bundle(
                    target_paths,
                    backup_paths,
                    backed_up_targets,
                    published_targets,
                    preserved_backup_paths,
                )
            except Exception as rollback_exc:
                rollback_error = rollback_exc
            _raise_write_error(
                partial_path,
                exc,
                rollback_error=rollback_error,
            )
```

- [ ] **Step 5: Make post-commit cleanup best-effort**

After partial deletion succeeds, call `_cleanup_transaction_files(transaction_paths)` without `raise_errors=True`. Keep the existing `finally` best-effort cleanup and preserved-backup exclusion unchanged. Do not alter staging, backup, publish, or rollback helpers.

- [ ] **Step 6: Run the focused benchmark tests and verify GREEN**

Run the command from Step 3, then run all final-report tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm_benchmark.py -k "final_report"
```

Expected: the two new tests and all existing rollback tests pass.

- [ ] **Step 7: Inspect the Task 2 diff**

Run:

```powershell
git diff -- backend/benchmarks/reporting.py backend/tests/test_algorithm_benchmark.py
```

Confirm the public report schema and CSV/JSON serialization code are unchanged.

### Task 3: Report retained process identity mismatches

**Files:**
- Modify: `scripts/dev-process-manifest.ps1:482-531`
- Test: `backend/tests/test_dev_scripts.py:222-329`

**Interfaces:**
- Consumes: `Get-MatchingDevProcess -ProcessId -StartedAtUtc`, `Get-Process -Id`, manifest fields `role`, `pid`, `startedAtUtc`
- Produces: unchanged `Stop-RecordedProcessTree` process behavior plus warning text containing the exact role and PID

- [ ] **Step 1: Capture and assert mismatch diagnostics**

Change the mismatched call in `test_manifest_stop_only_removes_after_injected_success` to capture warning stream 3 and add it to the JSON result:

```powershell
$mismatchDiagnostics = @(
  Stop-RecordedProcessTree -ManifestPath $manifestPath -StopCallback {
    param($ownedProcess)
    $state.allProcessObjects = $state.allProcessObjects -and ($ownedProcess -is [System.Diagnostics.Process])
    if ($ownedProcess -is [System.Diagnostics.Process]) {
      $called.Add($ownedProcess.Id)
    }
  } 3>&1 |
    ForEach-Object { $_.Message }
)
```

Assert the Python result contains:

```python
"mismatchDiagnostics": [
    f"Skipped recorded development process role=test pid={result['currentPid']} because its start time does not match the manifest entry; the entry was retained."
],
```

Keep the existing assertions `mismatchedCalls == 0` and `mismatchedEntries == 1`.

- [ ] **Step 2: Run the identity test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py::test_manifest_stop_only_removes_after_injected_success
```

Expected: `mismatchDiagnostics` is empty because the current implementation silently retains the entry.

- [ ] **Step 3: Add warning collection without changing stop decisions**

Add an `identityMismatches` list beside `stopFailures`. When the PID still exists but `Get-MatchingDevProcess` returned null, retain the existing entry and add only its exact `role` and `pid`:

```powershell
$identityMismatches = [System.Collections.Generic.List[object]]::new()

$remainingEntries.Add($entry)
$identityMismatches.Add([pscustomobject]@{
    role = [string]$entry.role
    pid = [int]$entry.pid
  })
continue
```

After the manifest write, emit warnings before existing stop-failure warnings:

```powershell
foreach ($mismatch in $identityMismatches) {
  Write-Warning "Skipped recorded development process role=$($mismatch.role) pid=$($mismatch.pid) because its start time does not match the manifest entry; the entry was retained."
}
```

Do not call the stop callback for these entries and do not remove them from the manifest.

- [ ] **Step 4: Run development-script tests and verify GREEN**

Run the test from Step 2, then:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py
```

Expected: the focused identity test and the complete development-script suite pass.

- [ ] **Step 5: Inspect the Task 3 diff**

Run:

```powershell
git diff -- scripts/dev-process-manifest.ps1 backend/tests/test_dev_scripts.py
```

Confirm no PID, start-time, descendant, port, or kill decision changed.

### Task 4: Full platform verification and final bounded review

**Files:**
- Verify only; no planned production edits

**Interfaces:**
- Consumes: the three completed fixes and the existing root `npm run check` pipeline
- Produces: fresh build, test, dependency, smoke, residue, and diff evidence

- [ ] **Step 1: Run the complete project check**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Expected: frontend production build, all frontend tests, and all backend tests exit zero.

- [ ] **Step 2: Verify Python dependency consistency**

Run:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

Expected: `No broken requirements found.`

- [ ] **Step 3: Run a real hidden development-service smoke**

Start `scripts/start-dev.ps1 -NoBrowser` in a hidden PowerShell process, wait for readiness, then verify:

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8011/health -TimeoutSec 5).StatusCode
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5174 -TimeoutSec 5).StatusCode
```

Expected: both return `200`. Run `scripts/stop-dev.ps1` in `finally` even if either probe fails.

- [ ] **Step 4: Verify cleanup ownership and residue**

After stop, verify ports `8011` and `5174` have no listeners, project-owned worker processes are absent, and `.runtime/dev-processes.json` contains exactly:

```json
{
  "version": 1,
  "processes": []
}
```

Do not terminate processes based only on a port or fuzzy command-line match.

- [ ] **Step 5: Run final diff checks**

Run:

```powershell
git diff --check
git status --short
git diff -- frontend/src/main.tsx frontend/src/main.test.ts backend/benchmarks/reporting.py backend/tests/test_algorithm_benchmark.py scripts/dev-process-manifest.ps1 backend/tests/test_dev_scripts.py
```

Expected: no whitespace errors; final code changes are limited to the three approved fixes. Existing unrelated working-tree changes remain preserved.

- [ ] **Step 6: Perform one bounded review of the fix diff**

Check only these invariants:

1. create 4xx never marks a missing session ready;
2. reset 4xx preserves a valid session, while 5xx/network failures remain unavailable;
3. benchmark never raises pre-commit failure while leaving a new final bundle exposed;
4. post-commit cleanup cannot invalidate an already valid final bundle;
5. identity mismatch still never invokes the stop callback or removes the manifest entry;
6. platform start, health probes, stop, and cleanup all succeeded.

Do not start another unbounded repository-wide review after this gate.
