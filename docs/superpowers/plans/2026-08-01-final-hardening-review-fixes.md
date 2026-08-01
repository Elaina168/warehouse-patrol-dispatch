# Final Hardening Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four validated final-review defects without changing the 10000-tick boundary, public API shape, permanent-failure semantics, or process-ownership scope.

**Architecture:** Put the session-time constant in the existing shared backend limits module and mirror it at the frontend import boundary. Make frontend JSON validation recursively strict, bind recorded-process cleanup to one verified `System.Diagnostics.Process` object, and bound timed A* expansion to the effective arrival limit while conservatively preserving generic unreachable results when budget causality cannot be proven.

**Tech Stack:** Python 3.13, FastAPI/Pydantic, pytest, React/TypeScript, Vitest, PowerShell 7.6, `System.Diagnostics.Process`.

## Global Constraints

- Work only in `D:\codex\summer\.worktrees\full-system-hardening` on `codex/full-system-hardening`.
- Preserve all existing uncommitted changes and the untracked `output/` directory.
- Read and write UTF-8; PowerShell source comments remain Chinese.
- Keep `MAX_SESSION_CURRENT_TIME` and `MAX_PLANNED_PATH_TICKS` equal to `10_000`.
- Do not cap `Task.deadline`.
- Do not silently strip or truncate imported JSON.
- Do not reintroduce PID-only termination after identity validation.
- Keep static and permanent reservation exhaustion classified as generic unreachable.
- For commits touching files with pre-existing hunks, inspect `git diff --cached` and stage only intended hunks; leave overlapping user-owned hunks uncommitted if they cannot be separated safely.

---

### Task 1: Align session-time input contracts

**Files:**
- Modify: `backend/app/limits.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/sessions.py`
- Modify: `backend/tests/test_schema_constraints.py`
- Modify: `frontend/src/domain/scenarioImport.ts`
- Modify: `frontend/src/domain/scenarioImport.test.ts`

**Interfaces:**
- Produces: `backend.app.limits.MAX_SESSION_CURRENT_TIME: int`.
- Produces: backend `SessionTimeInt` validation for `Task.releaseTime` and `DynamicEvent.triggerTime`.
- Produces: frontend `MAX_SESSION_CURRENT_TIME = 10_000` used only by imported release/trigger times.

- [ ] **Step 1: Write backend failing boundary tests**

Add to `backend/tests/test_schema_constraints.py`:

```python
def test_session_time_fields_accept_10000_and_reject_10001() -> None:
    task_payload = _inspection_task(0)
    scenario = scenario_payload()

    assert schemas.Task.model_validate(
        {**task_payload, "releaseTime": 10_000}
    ).releaseTime == 10_000
    with pytest.raises(ValidationError):
        schemas.Task.model_validate({**task_payload, "releaseTime": 10_001})

    scenario["dynamic"]["triggerTime"] = 10_000
    assert schemas.Scenario.model_validate(scenario).dynamic.triggerTime == 10_000
    scenario["dynamic"]["triggerTime"] = 10_001
    with pytest.raises(ValidationError):
        schemas.Scenario.model_validate(scenario)
```

Add an API assertion that `POST /api/sessions` and `POST /api/sessions/{session_id}/tasks` return 422 for a task with `releaseTime=10_001`, proving both initial and runtime entry points use the same model.

- [ ] **Step 2: Run the backend test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_schema_constraints.py -k "session_time"
```

Expected: FAIL because `releaseTime=10001` and `triggerTime=10001` are currently accepted.

- [ ] **Step 3: Write frontend failing boundary tests**

Add to `frontend/src/domain/scenarioImport.test.ts`:

```typescript
it("rejects release and dynamic trigger times above the session limit", () => {
  const lateTask = structuredClone(buildScenario());
  lateTask.tasks[0].releaseTime = 10_001;
  expect(() => parseScenario(lateTask)).toThrow("JSON 必须是 Scenario 对象");

  const lateDynamic = structuredClone(buildScenario());
  lateDynamic.dynamic.triggerTime = 10_001;
  expect(() => parseScenario(lateDynamic)).toThrow("JSON 必须是 Scenario 对象");
});
```

- [ ] **Step 4: Run the frontend test and verify RED**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport
```

Expected: FAIL because both values are currently accepted.

- [ ] **Step 5: Implement the shared backend limit**

Add to `backend/app/limits.py`:

```python
MAX_SESSION_CURRENT_TIME = MAX_PLANNED_PATH_TICKS
```

Import it in `schemas.py`, define:

```python
SessionTimeInt = Annotated[int, Field(ge=0, le=MAX_SESSION_CURRENT_TIME)]
```

Use `SessionTimeInt | None` for `Task.releaseTime` and `SessionTimeInt` for `DynamicEvent.triggerTime`. Import the same constant in `sessions.py` and remove its local definition.

- [ ] **Step 6: Implement the frontend limit**

Add:

```typescript
export const MAX_SESSION_CURRENT_TIME = 10_000;
```

Use it as the maximum in `isTask` for `releaseTime` and in `isDynamicEvent` for `triggerTime`. Leave `deadline` at `Number.POSITIVE_INFINITY`.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run both commands from Steps 2 and 4. Expected: all selected tests pass.

- [ ] **Step 8: Commit only this task's hunks**

```powershell
git add backend/app/limits.py backend/app/schemas.py backend/app/sessions.py backend/tests/test_schema_constraints.py frontend/src/domain/scenarioImport.ts frontend/src/domain/scenarioImport.test.ts
git diff --cached --check
git commit -m "fix: align session time input limits"
```

---

### Task 2: Reject unknown fields during frontend scenario import

**Files:**
- Modify: `frontend/src/domain/scenarioImport.ts`
- Modify: `frontend/src/domain/scenarioImport.test.ts`

**Interfaces:**
- Produces: internal `hasOnlyKeys(value, allowedKeys): boolean`.
- Preserves: optional backend-defaulted fields remain optional and accepted.

- [ ] **Step 1: Write recursive unknown-field tests**

Add a table-driven test whose mutators add `unknownField: true` at Scenario, Zones, Shelf, Robot, DynamicEvent, and Task levels. Ensure the shelf case first inserts a valid shelf. The assertion for every case is:

```typescript
expect(() => parseScenario(scenario)).toThrow("JSON 必须是 Scenario 对象");
```

Also retain the existing test that deletes optional `shelves`, `chargeTime`, `zones.charging`, `batteryCapacity`, `moveTicks`, and `capabilities` and expects successful import.

- [ ] **Step 2: Run the frontend test and verify RED**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- scenarioImport
```

Expected: all six new cases fail because unknown keys are accepted and retained.

- [ ] **Step 3: Add exact allowed-key sets**

Define readonly sets matching the approved design exactly:

```typescript
const scenarioKeys = new Set(["id", "name", "description", "width", "height", "obstacles", "zones", "shelves", "robots", "tasks", "dynamic", "chargeTime"]);
const zoneKeys = new Set(["warehouse", "inspection", "delivery", "charging"]);
const shelfKeys = new Set(["id", "cell", "serviceCell", "initialOccupied"]);
const robotKeys = new Set(["id", "name", "start", "battery", "batteryCapacity", "load", "moveTicks", "capabilities"]);
const dynamicEventKeys = new Set(["triggerTime", "blockedCells", "failedRobots", "tasks"]);
const taskKeys = new Set(["id", "type", "title", "priority", "releaseTime", "deadline", "serviceTime", "targets", "pickup", "dropoff", "demand", "target"]);
```

Implement:

```typescript
function hasOnlyKeys(value: Record<string, unknown>, allowedKeys: ReadonlySet<string>): boolean {
  return Object.keys(value).every((key) => allowedKeys.has(key));
}
```

Call it immediately after each `isRecord` check for the six object levels.

- [ ] **Step 4: Run focused frontend tests and verify GREEN**

Run the Step 2 command. Expected: every scenario-import test passes, including defaulted optional fields.

- [ ] **Step 5: Commit this task**

```powershell
git add frontend/src/domain/scenarioImport.ts frontend/src/domain/scenarioImport.test.ts
git diff --cached --check
git commit -m "fix: reject unknown scenario import fields"
```

---

### Task 3: Bind recorded-process cleanup to the verified process object

**Files:**
- Modify: `scripts/dev-process-manifest.ps1`
- Modify: `backend/tests/test_dev_scripts.py`

**Interfaces:**
- Produces: `Get-MatchingDevProcess -ProcessId <int> -StartedAtUtc <string>` returning a matching `System.Diagnostics.Process` or `$null`.
- Changes: `Stop-RecordedProcessTree -StopCallback` receives a `System.Diagnostics.Process`, not an integer PID.
- Preserves: `Test-DevProcessIdentity` remains a boolean compatibility helper and closes its temporary process object.

- [ ] **Step 1: Change tests to require a process object and closed handle**

Update `test_manifest_stop_preserves_mismatched_and_failed_entries` so callbacks receive `$ownedProcess`, record `$ownedProcess.Id`, and save the object through a mutable state object. After success and injected failure, assert the captured objects' handles are closed. Add a source assertion:

```python
assert "taskkill /PID" not in MANIFEST_HELPER.read_text(encoding="utf-8")
```

Update every existing `StopCallback` test hook to use `$ownedProcess.Id` where it previously consumed `$processId`.

- [ ] **Step 2: Run dev-script tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_dev_scripts.py
```

Expected: FAIL because callbacks currently receive integers and the default callback still contains `taskkill /PID`.

- [ ] **Step 3: Implement matching-process acquisition**

Add `Get-MatchingDevProcess` that:

1. Parses `StartedAtUtc` as `DateTimeOffset`.
2. gets the process by exact `ProcessId`;
3. evaluates `$process.SafeHandle` before reading `StartTime`;
4. compares exact UTC start time;
5. returns the same process object on match;
6. closes it and returns `$null` on mismatch or error.

Refactor `Test-DevProcessIdentity` to call this helper and close a returned process in `finally`.

- [ ] **Step 4: Stop and close the same verified object**

Change the default callback to:

```powershell
param([System.Diagnostics.Process]$OwnedProcess)
if (-not $OwnedProcess.HasExited) {
  $OwnedProcess.Kill($true)
}
if (-not $OwnedProcess.WaitForExit(5000)) {
  throw "Timed out waiting for recorded process tree PID=$($OwnedProcess.Id) to stop."
}
```

In `Stop-RecordedProcessTree`, obtain `$ownedProcess` once. If it is null, recheck exact PID existence to distinguish an absent process (discard entry) from an identity mismatch (retain entry). Pass `$ownedProcess` to the callback and close it in `finally`, aggregating close errors with stop errors while retaining the manifest entry on any failure.

- [ ] **Step 5: Run dev-script tests and verify GREEN**

Run the Step 2 command. Expected: all dev-script tests pass with no surviving test child processes.

- [ ] **Step 6: Commit only intended process-cleanup hunks**

Because both files contain pre-existing uncommitted work, use interactive staging and inspect the index:

```powershell
git add -p scripts/dev-process-manifest.ps1 backend/tests/test_dev_scripts.py
git diff --cached --check
git diff --cached
git commit -m "fix: bind manifest cleanup to process identity"
```

Do not stage unrelated pre-existing launcher cleanup or manifest mutex hunks unless they are inseparable dependencies and are explicitly reported with the commit.

---

### Task 4: Bound timed A* to the effective remaining budget

**Files:**
- Modify: `backend/app/dispatch.py`
- Modify: `backend/tests/test_algorithm.py`

**Interfaces:**
- Preserves: `_TimedPathBudgetExceeded` means the remaining absolute path budget is provably insufficient.
- Preserves: static unreachable and full-natural-horizon reservation exhaustion return `[]`.
- Produces: no heap state with `next_time > latest_arrival`.

- [ ] **Step 1: Write lower-bound and delayed-goal failing tests**

Add one test using `PathCandidateDiagnostics` where a valid 16×16 static path from `(0, 0)` to `(15, 15)` is requested with `path_end_time=5`; assert `_TimedPathBudgetExceeded` and zero expanded states.

Add a 3×1 case from `(0, 0)` to `(2, 0)` with the goal reserved at every time from 2 through 5 and available at 6; request `path_end_time=5`, then assert `_TimedPathBudgetExceeded` and zero expanded states.

Keep `test_timed_path_budget_keeps_permanent_reservation_exhaustion_generic` unchanged and passing.

- [ ] **Step 2: Run the new algorithm tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm.py -k "timed_path_budget"
```

Expected: the lower-bound case expands states before failing, and the delayed-goal case searches beyond the effective budget.

- [ ] **Step 3: Implement provable early budget failures**

After computing the static `earliest_arrival`:

```python
if earliest_arrival > latest_arrival:
    if call_diagnostics is not None:
        call_diagnostics.finish("exhausted", 0)
    raise _TimedPathBudgetExceeded
```

Only execute this branch when `budget_limited` is true; retain the existing natural-horizon generic return for non-budget-limited calls.

Check goal availability first through `latest_arrival`. If unavailable there but available by `natural_latest_arrival`, finish as `exhausted` with zero expansions and raise `_TimedPathBudgetExceeded`. If unavailable through the natural horizon, finish as `goalFullyReserved` and return `[]`.

- [ ] **Step 4: Cap heap expansion**

Replace the neighbor bound with:

```python
if next_time > latest_arrival:
    continue
```

Store `came_from` unconditionally for pushed states because no pushed state can exceed the reconstruction budget. If the capped heap exhausts without one of the provable conditions above, return `[]` conservatively.

- [ ] **Step 5: Run focused and adjacent algorithm tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider backend/tests/test_algorithm.py -k "timed_path_budget or path_tick_budget"
```

Expected: all selected tests pass; permanent reservation and static unreachable remain generic.

- [ ] **Step 6: Commit this task**

```powershell
git add backend/app/dispatch.py backend/tests/test_algorithm.py
git diff --cached --check
git commit -m "fix: bound timed astar to remaining budget"
```

---

### Task 5: Update evidence and run complete verification

**Files:**
- Modify: `AGENTS.md` only if its verification snapshot is retained on this branch.

**Interfaces:**
- Produces: fresh build/test/dependency/process-cleanup evidence.

- [ ] **Step 1: Run the complete project check**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Expected: frontend production build succeeds, all frontend tests pass, and all backend tests pass.

- [ ] **Step 2: Run dependency and diff gates**

```powershell
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Expected: `No broken requirements found.` and no diff errors. LF/CRLF conversion notices are not diff failures.

- [ ] **Step 3: Run real launcher cleanup smoke**

Start with the project script using `-NoBrowser`, verify `/health`, stop with `scripts/stop-dev.ps1`, then verify exact ports 5174 and 8011 have no listeners and `.runtime/dev-processes.json` contains `"processes": []`. Do not scan or kill unrelated ports.

- [ ] **Step 4: Update the verification snapshot from actual output**

If `AGENTS.md` retains test counts, replace both snapshot counts with the exact frontend and backend totals printed by Step 1. Do not estimate counts before the run.

- [ ] **Step 5: Review final scope**

```powershell
git status --short
git diff --stat main...HEAD
git diff --check
```

Confirm `output/` remains untouched and every pre-existing user change is either preserved unstaged or intentionally included and reported.

- [ ] **Step 6: Commit only the verified snapshot if changed**

```powershell
git add AGENTS.md
git diff --cached --check
git commit -m "docs: refresh hardening verification evidence"
```
