# Tooling Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make development process control non-destructive, make the documented full-check script trustworthy, harden benchmark cancellation and final-report publication, remove flaky wall-clock gates, and pin audited dependencies.

**Architecture:** Replace port-based process killing with a small ignored PID manifest and testable PowerShell helpers. Keep benchmark workers and report schemas unchanged while moving cleanup into a guaranteed `finally` path and adding staged bundle rollback. Use existing npm/pip tooling rather than introducing a new package manager.

**Tech Stack:** PowerShell 7.6.2, Python 3.11+, pytest, npm 10+, Vite.

## Global Constraints

- Execute the four plans in this order: backend consistency, resource boundaries, frontend reliability, then this tooling plan.
- Never kill a process solely because it owns port 5174, 8011, 8000, or 8001.
- A PID manifest entry is valid only when PID and recorded process start time match.
- Unknown occupied ports cause a clear startup error, not automatic termination.
- `test-all.ps1` must run build, frontend tests, and backend tests and propagate the first failure.
- `KeyboardInterrupt` and `SystemExit` must still clean benchmark workers and handles.
- The final three-file algorithm report is published as one rollback-protected bundle.
- Daily pytest must not fail on a real wall-clock upper bound.
- PostCSS must resolve to at least 8.5.18.
- Use UTF-8 and Chinese code comments.
- Follow RED → GREEN for every production behavior.

---

### Task 1: Add a managed development-process manifest

**Files:**
- Create: `scripts/dev-process-manifest.ps1`
- Modify: `scripts/start-dev.ps1`
- Modify: `scripts/stop-dev.ps1`
- Modify: `.gitignore`
- Create: `backend/tests/test_dev_scripts.py`

**Interfaces:**
- Produces: `.runtime/dev-processes.json`
- Produces PowerShell functions:
  - `Read-DevProcessManifest`
  - `Write-DevProcessManifest`
  - `Add-DevProcessManifestEntry`
  - `Test-DevProcessIdentity`
  - `Stop-RecordedProcessTree`

- [ ] **Step 1: Add static destructive-scope regressions**

Read `scripts/stop-dev.ps1` and assert it no longer contains:

```text
Get-NetTCPConnection
netstat -ano
$legacyPorts
```

Assert it imports `dev-process-manifest.ps1`.

- [ ] **Step 2: Add manifest identity tests**

From pytest, invoke project PowerShell 7 and dot-source the helper. Use the current PowerShell process ID/start time to assert:

- exact PID and UTC start time match returns true;
- same PID with altered start time returns false;
- nonexistent PID returns false.

No test may call the real `taskkill`.

- [ ] **Step 3: Add injected stop-callback tests**

`Stop-RecordedProcessTree` accepts an optional scriptblock used to perform the actual stop. Pass a test callback that records IDs and assert:

- matching entry invokes the callback once;
- mismatched/reused entry never invokes it;
- failed callback leaves the manifest entry intact;
- successful callback removes the entry.

- [ ] **Step 4: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py -k "manifest or stop_scope"
```

Expected: helper is absent and stop script still scans ports.

- [ ] **Step 5: Implement atomic UTF-8 manifest storage**

Use the exact schema:

```json
{
  "version": 1,
  "processes": [
    {
      "role": "backend",
      "pid": 1234,
      "startedAtUtc": "2026-07-28T10:00:00.0000000Z"
    }
  ]
}
```

Write to `.runtime/dev-processes.json.tmp`, then `Move-Item`/`Replace` into place. Reject unknown manifest version instead of guessing.

Add `.runtime/` to `.gitignore`.

- [ ] **Step 6: Validate process identity**

Use `Get-Process -Id` and compare `StartTime.ToUniversalTime().ToString("O")` parsed as `DateTimeOffset`. PID alone is insufficient.

The default stop callback may call:

```powershell
cmd /c "taskkill /PID $ProcessId /T /F 2>&1"
```

only after identity succeeds.

- [ ] **Step 7: Record processes started by `start-dev.ps1`**

After `process.Start()` succeeds, add an entry with the role, PID and exact start time. On normal Ctrl+C cleanup, remove successfully stopped entries.

A compatible reused backend is not recorded because this script did not create it.

- [ ] **Step 8: Remove unconditional port killing**

Replace line 28 behavior with manifest cleanup only. Before starting:

- a compatible backend on 8011 may be reused;
- an incompatible backend port produces an error;
- an occupied frontend port produces an error before starting frontend;
- legacy ports 8000/8001 are ignored.

- [ ] **Step 9: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py
& '.\.tools\powershell\pwsh.exe' -NoLogo -NoProfile -File .\scripts\start-dev.ps1 -ValidateOnly
```

Expected: tests and validation pass without stopping any process.

- [ ] **Step 10: Commit**

```powershell
git add .gitignore scripts/dev-process-manifest.ps1 scripts/start-dev.ps1 scripts/stop-dev.ps1 backend/tests/test_dev_scripts.py
git commit -m "fix: stop only recorded dev processes"
```

---

### Task 2: Make `test-all.ps1` a trustworthy full check

**Files:**
- Modify: `scripts/test-all.ps1`
- Modify: `backend/tests/test_dev_scripts.py`
- Modify: `docs/environment.md`
- Modify: `AGENTS.md`

**Interfaces:**
- `scripts/test-all.ps1 -NpmPath C:\temp\fake-npm.cmd` demonstrates the dependency-injection parameter used by tests
- Runs exact npm scripts: `frontend:build`, `frontend:test`, `backend:test`

- [ ] **Step 1: Add an invocation-order test**

Create a temporary `.cmd` shim that appends its arguments to a UTF-8 log and exits zero. Run:

```powershell
$shimPath = Join-Path $testTemp "fake-npm.cmd"
.\.tools\powershell\pwsh.exe -NoLogo -NoProfile -File .\scripts\test-all.ps1 -NpmPath $shimPath
```

Assert log lines are exactly:

```text
run frontend:build
run frontend:test
run backend:test
```

- [ ] **Step 2: Add first/second/third failure propagation tests**

Configure the shim to return exit code seven on each stage in turn. Assert:

- script exit code is seven;
- no later stage appears in the log.

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py -k "test_all"
```

Expected: current script omits frontend tests and can continue after a native failure.

- [ ] **Step 4: Implement explicit native exit handling**

Use:

```powershell
param([string]$NpmPath)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\env.ps1"

$npm = if ($NpmPath) { $NpmPath } else { $env:PROJECT_NPM }
foreach ($scriptName in @("frontend:build", "frontend:test", "backend:test")) {
  & $npm run $scriptName
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}
exit 0
```

Do not rely on `$PSNativeCommandUseErrorActionPreference`.

- [ ] **Step 5: Align documentation**

State that `test-all.ps1` and `npm run check` run the same three project checks. Do not call a command equivalent unless the scripts truly match.

- [ ] **Step 6: Run GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py -k "test_all"
```

- [ ] **Step 7: Commit**

```powershell
git add scripts/test-all.ps1 backend/tests/test_dev_scripts.py docs/environment.md AGENTS.md
git commit -m "fix: propagate complete check failures"
```

---

### Task 3: Clean benchmark resources on `BaseException`

**Files:**
- Modify: `backend/benchmarks/process_isolation.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`
- Modify: `backend/tests/test_adaptive_replan_calibration.py`

**Interfaces:**
- Consumes: `run_isolated(...) -> IsolatedExecution`
- Preserves: `_cleanup_isolated_resources(...)`

- [ ] **Step 1: Add `KeyboardInterrupt` and `SystemExit` RED tests**

Inject a parent connection whose `poll()` raises each exception. Assert:

```python
with pytest.raises(KeyboardInterrupt):
    run_isolated(...)
assert process.terminate_called
assert process.join_called
assert parent_connection.closed
assert child_connection.closed
```

Repeat for `SystemExit`.

- [ ] **Step 2: Add cleanup-failure precedence test**

When cancellation occurs and cleanup cannot confirm the process stopped, assert `BenchmarkInfrastructureError` is raised with the original cancellation as `__context__` or `__cause__`.

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm_benchmark.py backend/tests/test_adaptive_replan_calibration.py -k "keyboard_interrupt or system_exit or cancellation_cleanup"
```

Expected: cancellation bypasses cleanup.

- [ ] **Step 4: Move cleanup into `finally`**

Track:

```python
pending_exception: BaseException | None = None
```

Convert ordinary `Exception` into the existing structured error result. Capture non-`Exception` `BaseException`, run cleanup in `finally`, then re-raise the exact original object after cleanup.

Do not turn `KeyboardInterrupt` into an `IsolatedExecution(outcome="error")`.

- [ ] **Step 5: Run GREEN and process-isolation suites**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm_benchmark.py backend/tests/test_adaptive_replan_calibration.py -k "process or isolation or interrupt or system_exit"
```

- [ ] **Step 6: Check real worker residue**

Run one scale smoke and compare relevant Python worker counts before/after. Require zero residual spawned benchmark workers.

- [ ] **Step 7: Commit**

```powershell
git add backend/benchmarks/process_isolation.py backend/tests/test_algorithm_benchmark.py backend/tests/test_adaptive_replan_calibration.py
git commit -m "fix: clean benchmark workers on cancellation"
```

---

### Task 4: Publish algorithm reports as a rollback-protected bundle

**Files:**
- Modify: `backend/benchmarks/reporting.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`
- Reference: `backend/benchmarks/adaptive_reporting.py`

**Interfaces:**
- Changes: `write_final_report(output_dir, report) -> None`
- Produces internal helpers for staging, backup, publish, and rollback

- [ ] **Step 1: Add second-file failure regression**

Create an old complete bundle with known contents. Inject a failure while publishing `runs.csv`. Assert all three old final files remain byte-for-byte unchanged and `results.partial.json` remains.

- [ ] **Step 2: Add third-file and no-old-bundle regressions**

When `case-summaries.csv` publish fails:

- restore all old files if they existed;
- expose no final files if no old bundle existed;
- remove temporary staging/backup artifacts;
- retain partial JSON.

- [ ] **Step 3: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm_benchmark.py -k "final_report_rollback"
```

Expected: `results.json` or `runs.csv` remains partially replaced.

- [ ] **Step 4: Stage all files before publishing**

Write three uniquely named staging files in the target directory. Complete JSON serialization and CSV writes before touching any final path.

- [ ] **Step 5: Add backup and rollback**

For each existing final file, move it to a unique backup. Replace all three staging files in deterministic order. On any failure:

1. remove newly published files;
2. restore all backups;
3. remove remaining staging files;
4. raise the existing report-write error type.

Delete backups and `results.partial.json` only after all final replacements succeed.

- [ ] **Step 6: Run GREEN and report suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm_benchmark.py -k "report"
```

- [ ] **Step 7: Commit**

```powershell
git add backend/benchmarks/reporting.py backend/tests/test_algorithm_benchmark.py
git commit -m "fix: publish benchmark reports transactionally"
```

---

### Task 5: Remove real wall-clock pass/fail gates from daily pytest

**Files:**
- Modify: `backend/tests/test_algorithm.py`
- Modify: `backend/tests/test_experiments.py`
- Modify: `docs/testing-guide.md`

**Interfaces:**
- Preserves production metric: `replanTimeMs`
- Preserves evidence field: `planningTimeBudgetMs`

- [ ] **Step 1: Identify exact hard thresholds**

Confirm the four reviewed assertions:

- `< 1000` and `< 2000` in `test_algorithm.py`;
- `< 2000` maximum checks in `test_experiments.py`.

Do not remove non-time correctness assertions from the same tests.

- [ ] **Step 2: Replace thresholds with deterministic assertions**

Retain assertions for:

- all expected tasks assigned;
- zero conflicts and failures;
- exact fixed-seed case labels/counts;
- `planningTimeBudgetMs == 2000` as API metadata;
- `replanTimeMs >= 0` only as type/domain validity.

Where planning diagnostics are already available, assert deterministic expanded-state or candidate counts instead of time.

- [ ] **Step 3: Run the focused tests under synthetic delay**

Monkeypatch the timing source or insert a controlled delay outside planning correctness so the old threshold would fail. Verify the revised tests still pass because outputs remain correct.

- [ ] **Step 4: Update testing guidance**

List the removed test names and state that real timing limits belong to offline repeated benchmarks only.

- [ ] **Step 5: Run algorithm and experiment suites**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm.py backend/tests/test_experiments.py
```

- [ ] **Step 6: Commit**

```powershell
git add backend/tests/test_algorithm.py backend/tests/test_experiments.py docs/testing-guide.md
git commit -m "test: remove wall clock regression gates"
```

---

### Task 6: Patch and lock dependencies

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`
- Create: `backend/requirements.lock.txt`
- Modify: `docs/environment.md`
- Modify: `docs/testing-guide.md`
- Create or modify: `backend/tests/test_dependency_lock.py`

**Interfaces:**
- Frontend override: `postcss >= 8.5.18`
- Backend lock: exact `name==version` entries for the validated environment

- [ ] **Step 1: Add backend lock consistency RED test**

Parse direct pins from `backend/requirements.txt` and exact pins from `backend/requirements.lock.txt`. Assert every direct requirement appears with the same exact version in the lock.

The test must fail because the lock file does not exist.

- [ ] **Step 2: Run RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dependency_lock.py
```

- [ ] **Step 3: Pin patched PostCSS**

Add:

```json
"overrides": {
  "postcss": "8.5.18"
}
```

Run npm install using the configured official or project registry so `frontend/package-lock.json` resolves exactly 8.5.18 or a later explicitly reviewed compatible patch. Do not leave `8.5.15`.

- [ ] **Step 4: Generate the backend transitive lock**

From the current validated virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip freeze --all
```

Write exact UTF-8 pins to `backend/requirements.lock.txt`, excluding `pip` itself. Include runtime, Uvicorn standard extras, HTTP/test dependencies, and their transitive packages.

- [ ] **Step 5: Document installation and refresh**

Use:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock.txt
```

Document that `requirements.txt` is the direct-dependency source and the lock is regenerated only after an intentional dependency update plus full verification.

- [ ] **Step 6: Run GREEN and audits**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dependency_lock.py
.\.venv\Scripts\python.exe -m pip check
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend ls postcss --all
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend audit --registry=https://registry.npmjs.org
```

Expected:

- lock consistency passes;
- `pip check` passes;
- PostCSS resolves to at least 8.5.18;
- `GHSA-r28c-9q8g-f849` is absent;
- no high or critical npm advisories remain.

- [ ] **Step 7: Run frontend build/tests**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test
```

- [ ] **Step 8: Commit**

```powershell
git add frontend/package.json frontend/package-lock.json backend/requirements.lock.txt backend/tests/test_dependency_lock.py docs/environment.md docs/testing-guide.md
git commit -m "build: patch and lock project dependencies"
```

---

### Task 7: Synchronize current-maintenance documentation

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/algorithm.md`
- Modify: `docs/environment.md`
- Modify: `docs/experiments.md`
- Modify: `docs/testing-guide.md`

**Interfaces:**
- Documents only behavior verified by Tasks 1–6 and the other three implementation plans.

- [ ] **Step 1: Update execution invariants**

Document in `docs/algorithm.md`:

- timed-path movement is the final battery authority;
- online execution never writes zero-battery movement;
- robot starts are unique;
- future runtime block validation is transactional;
- T=0 completions use the same task/inventory transition as normal ticks;
- the path horizon is 10,000 ticks.

Do not describe the planner as complete MAPF.

- [ ] **Step 2: Update operational frontend behavior**

Document in `README.md` and `AGENTS.md`:

- runtime mutations are unavailable during tick synchronization and historical playback;
- 4xx business errors preserve online status;
- historical playback is path/event/metric-only and does not claim historical inventory/task snapshots.

- [ ] **Step 3: Update scripts and dependency instructions**

Document in `docs/environment.md`:

- `.runtime/dev-processes.json` ownership;
- stop only affects recorded matching processes;
- unknown occupied ports are reported;
- `test-all.ps1` runs the same three checks as `npm run check`;
- backend reproducible install uses `requirements.lock.txt`.

- [ ] **Step 4: Update experiment and test boundaries**

Document in `docs/experiments.md` and `docs/testing-guide.md`:

- experiment batch maximum 32 and unique windows;
- report bundle rollback guarantee;
- cancellation worker cleanup;
- real wall-clock is offline evidence only;
- dependency audit commands.

- [ ] **Step 5: Preserve the verification-count boundary**

Update implemented behavior in `AGENTS.md`, but leave its current verification snapshot unchanged in this task. Task 8 replaces the counts only after a fresh complete check; do not predict them.

- [ ] **Step 6: Run documentation checks**

```powershell
rg -n "零电量|10000|32|历史回放|dev-processes|requirements.lock" README.md AGENTS.md docs
git diff --check
```

Read all changed Chinese files with explicit UTF-8 and verify no replacement characters or placeholder text.

- [ ] **Step 7: Commit**

```powershell
git add README.md AGENTS.md docs/algorithm.md docs/environment.md docs/experiments.md docs/testing-guide.md
git commit -m "docs: record full system hardening"
```

---

### Task 8: Full-branch verification and review

**Files:**
- Review: all files changed by all four implementation plans
- Modify only if a new failing regression proves a review defect

**Interfaces:**
- Final integration gate for the approved design.

- [ ] **Step 1: Run targeted safety reproductions**

Run all new tests for:

- battery detour and energy hold;
- future block transaction;
- duplicate starts;
- T=0 completion/inventory;
- path and experiment limits;
- tick mutation gate;
- HTTP classification;
- historical playback;
- stale create deletion;
- import alignment;
- process manifest;
- test-all propagation;
- benchmark cancellation and report rollback.

- [ ] **Step 2: Run complete project check**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Expected: frontend build, all frontend tests, and all backend tests pass.

- [ ] **Step 3: Run dependency and warning gates**

```powershell
$reviewTemp = Join-Path $env:TEMP "warehouse-patrol-full-hardening-$PID"
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -W error --basetemp $reviewTemp backend/tests
.\.venv\Scripts\python.exe -m pip check
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend audit --registry=https://registry.npmjs.org
```

- [ ] **Step 4: Run development-script smoke**

Confirm no unrelated process is using project ports before starting. Start with `npm run dev:no-browser`, verify health/UI, then stop with `npm run dev:stop`. Assert:

- only manifest-recorded process trees stop;
- `.runtime/dev-processes.json` is removed or contains only confirmed survivors;
- ports are released;
- no unrelated process is terminated.

- [ ] **Step 5: Run browser operational smoke**

Verify all six behaviors explicitly:

1. playback tick temporarily disables block, fail, recover and task controls;
2. a deliberate 409 displays backend detail while the status remains “后端在线”;
3. a rejected tick pauses playback;
4. jumping to an earlier event displays the path-only history notice and hides current task, inventory, block and failure overlays;
5. returning to the latest T restores live state and controls;
6. rapid replan-window changes leave one active server session.

Confirm no browser console errors.

- [ ] **Step 6: Run algorithm benchmark smoke**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- --families scale --repetitions 1 --timeout-seconds 30
```

Inspect the newest report bundle and assert three runs/three summaries, no partial file after success, and no worker residue. Do not use wall-clock values as pass/fail.

- [ ] **Step 7: Review the complete branch**

Review `de02ffb5d305a05c2a6a762c48c7ef2a70520de9..HEAD`. Treat as blocking:

- any executed zero-battery movement;
- any rejected request that mutates runtime state;
- any T=0 overlap or stale inventory reservation;
- any path above 10,001 nodes;
- any stale frontend mutation request;
- any historical current-state overlay;
- any unrecorded process kill;
- any swallowed test failure;
- any residual benchmark worker or mixed report bundle;
- any high/critical npm advisory.

- [ ] **Step 8: Run final repository checks**

```powershell
git diff --check de02ffb5d305a05c2a6a762c48c7ef2a70520de9..HEAD
git status --short
```

Expected: tracked work is committed; only pre-existing `.superpowers/` and `output/` plus ignored `.runtime/` artifacts remain.

- [ ] **Step 9: Record the fresh verification counts**

Read the exact frontend and backend totals from Step 2 output, replace the `AGENTS.md` current verification snapshot with those totals, and commit:

```powershell
git add AGENTS.md
git commit -m "docs: record full hardening verification"
```

- [ ] **Step 10: Commit review corrections**

For any blocking finding, add a failing regression first, apply the smallest fix, rerun the relevant gate, then:

```powershell
git add .gitignore backend frontend scripts docs AGENTS.md
git commit -m "fix: close full hardening review findings"
```

- [ ] **Step 11: Stop before merge or push**

Report final evidence and wait for explicit user authorization. Do not merge or push automatically.
