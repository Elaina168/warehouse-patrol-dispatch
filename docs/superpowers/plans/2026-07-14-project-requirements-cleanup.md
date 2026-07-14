# Project Requirements Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Align the online dispatch UI, session contract, priority rules, experiment metrics, tests, and project documentation with the approved 2026-07-14 requirements cleanup design.

**Architecture:** Keep scenario dynamic events and all six experiment endpoints in the FastAPI backend, while simplifying the operational React dashboard and removing obsolete experiment-panel client code. Rename session and experiment fields at the Pydantic boundary, propagate exact names through session construction and tests, then update documentation from one verified contract.

**Tech Stack:** Python 3.13, FastAPI, Pydantic, pytest, React 19, TypeScript 5.8, Vite 6, Vitest, PowerShell 7.

## Global Constraints

- All source reads and writes use UTF-8; Chinese comments and text must not be copied from garbled output.
- Preserve the accepted `integrated-demo` baseline: `E1` remains a default queue task and the default scenario does not auto-block cells or auto-fail robots.
- Main UI task insertion and timed generation continue to use `POST /api/sessions/{session_id}/tasks`.
- Keep backend dynamic-event support and all six experiment endpoints.
- Do not add an experiment panel, a separate stream-task endpoint, a task-source field, a full-horizon safety gate, capability modeling, a new map, or 3D.
- Use one final commit after all tasks pass, per the user's explicit request; do not create per-task commits.
- Preserve the current uncommitted fixed-demo recovery, deadline `24`, and UTF-8 title fixes.

---

### Task 1: Rename session runtime task counts

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/sessions.py`
- Modify: `backend/tests/test_sessions.py`
- Modify: `backend/tests/test_api_contract.py`
- Modify: `frontend/src/domain/types.ts`
- Modify: `frontend/src/main.test.ts`

**Interfaces:**
- Produces: `SessionResult.runtimeTaskCount: int` and `SessionSummary.runtimeTaskCount: int`.
- Removes: `manualTaskCount` and `streamTaskCount` from session responses.
- Internal state: `DispatchSession.runtime_task_count: int` increments once for every successful `add_task` call and resets to zero.

- [x] **Step 1: Change representative session tests to the new response contract**

Update assertions so a newly created session returns:

```python
assert payload["runtimeTaskCount"] == 0
assert "manualTaskCount" not in payload
assert "streamTaskCount" not in payload
```

After one successful task insertion:

```python
assert payload["runtimeTaskCount"] == 1
```

For reset and list summary coverage, assert the same exact field and removed keys.

- [x] **Step 2: Run the representative tests and verify contract failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py -k "list_summaries or reset or stream_task" -q
```

Expected: FAIL because responses still contain `manualTaskCount` and `streamTaskCount` and do not contain `runtimeTaskCount`.

- [x] **Step 3: Implement the session model and state rename**

In `backend/app/schemas.py`, make both response models contain:

```python
runtimeTaskCount: int
runtimeEventCount: int
```

In `DispatchSession`, replace both counters with:

```python
runtime_task_count: int = 0
```

Increment `runtime_task_count` in the existing successful task-addition path, reset it to zero, and emit `runtimeTaskCount=session.runtime_task_count` from detail and summary builders.

- [x] **Step 4: Update all backend session assertions mechanically**

Replace assertions that treated `manualTaskCount` as all `/tasks` arrivals with `runtimeTaskCount`. Remove assertions for the permanently zero `streamTaskCount`. Keep the removed `/stream-task` endpoint regression asserting HTTP 404.

- [x] **Step 5: Update frontend session types and fixtures**

Use the exact shape:

```ts
runtimeTaskCount: number;
runtimeEventCount: number;
```

Remove `manualTaskCount` and `streamTaskCount` from `SessionResult`, `SessionSummary`, and frontend test fixtures.

- [x] **Step 6: Update API contract expectations**

Keep `SessionResult` and `SessionSummary` in the frontend/backend field-alignment test and require exact equality after the rename.

- [x] **Step 7: Run session and contract tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py backend\tests\test_api_contract.py -q
```

Expected: all selected tests pass.

---

### Task 2: Enforce priority 0-5 and simplify the main dynamic-event UI

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/tests/test_schema_constraints.py`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/main.test.ts`
- Modify: `frontend/src/domain/types.ts` only if an import becomes unused

**Interfaces:**
- Produces: backend `Task.priority` validation limited to inclusive range `0..5`.
- Main dashboard always creates sessions with `includeDynamic: true`.
- Manual priority input range is `0..5`; emergency tasks remain at least `4`; random emergency priority is `4..5`.

- [x] **Step 1: Add backend priority boundary tests**

Add tests based on an existing valid scenario payload:

```python
scenario["tasks"][0]["priority"] = -1
assert client.post("/api/dispatch", json={"scenario": scenario}).status_code == 422

scenario["tasks"][0]["priority"] = 6
assert client.post("/api/dispatch", json={"scenario": scenario}).status_code == 422
```

Retain or add a `priority = 0` case that returns 200.

- [x] **Step 2: Run schema tests and verify the upper-bound test fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_schema_constraints.py -q
```

Expected: the priority `6` request is currently accepted, so the new test fails.

- [x] **Step 3: Add an exact priority type constraint**

Define and use:

```python
PriorityInt = Annotated[int, Field(ge=0, le=5)]
```

Change `Task.priority` to `PriorityInt`.

- [x] **Step 4: Update frontend priority behavior tests**

Require manual priority `0` to stay `0`, values above `5` to clamp to `5`, and random emergency tasks to fall only in `4..5`:

```ts
expect(task?.priority).toBe(0);
expect(emergency.priority).toBeGreaterThanOrEqual(4);
expect(emergency.priority).toBeLessThanOrEqual(5);
```

- [x] **Step 5: Run the focused frontend tests and verify failure**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts
```

Expected: manual priority `0` is currently clamped to `1`, and random emergency generation can produce `3`.

- [x] **Step 6: Implement frontend priority rules**

Change the numeric input minimum and task builder clamp to `0`. Keep manual emergency normalization as `Math.max(base.priority, 4)`. Change random emergency generation to exactly `4 + (seed % 2)`.

- [x] **Step 7: Remove the main dynamic-event control**

Remove `includeDynamic` component state, its toolbar checkbox, its status-strip label, `dynamicModeLabel`, and its dedicated tests. Use a fixed true value when building session options:

```ts
options: buildDispatchOptions(avoidConflicts, true, assignmentReplanWindow)
```

Remove `includeDynamic` from effect dependencies that existed only because it was editable. Preserve `DispatchOptions.includeDynamic` in shared API types because the backend and imported scenarios still use it.

- [x] **Step 8: Run focused backend and frontend tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_schema_constraints.py -q
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test -- main.test.ts
```

Expected: all selected tests pass.

---

### Task 3: Correct fixed-seed and online-pressure metrics

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/experiments.py`
- Modify: `backend/tests/test_experiments.py`
- Modify: `backend/tests/test_api_contract.py`

**Interfaces:**
- Seeded case/summary: `assignmentRatePercent` replaces `completionRatePercent`.
- Online case: adds `releasedTaskCount`, uses `runtimeTaskCount`, `coverageRatePercent`, and `actualCompletionRatePercent`; removes manual/stream counts and the misleading completion field.
- Online summary: adds `totalReleasedTaskCount`, `totalRuntimeTaskCount`, `coverageRatePercent`, and `actualCompletionRatePercent`; removes total manual/stream counts and the misleading completion field.

- [x] **Step 1: Change experiment tests to exact new fields**

For fixed seed:

```python
assert summary["assignmentRatePercent"] == 100
assert "completionRatePercent" not in summary
```

For online pressure:

```python
assert case["releasedTaskCount"] == 17
assert case["runtimeTaskCount"] == 2
assert case["coverageRatePercent"] == 100
assert case["actualCompletionRatePercent"] == round(case["completedTaskCount"] / case["releasedTaskCount"] * 100, 1)
assert "manualTaskCount" not in case
assert "streamTaskCount" not in case
```

Require corresponding summary totals and removed keys.

- [x] **Step 2: Run experiment tests and verify contract failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_experiments.py -q
```

Expected: FAIL because the old response fields are still emitted.

- [x] **Step 3: Rename fixed-seed Pydantic fields and constructors**

Replace `completionRatePercent` with `assignmentRatePercent` in `SeededPressureExperimentCaseResult`, `SeededPressureExperimentSummary`, and `run_seeded_pressure_experiment`.

- [x] **Step 4: Implement online released and completion calculations**

After the final online session payload, calculate:

```python
released_task_count = sum(
    1 for state in session.taskStates if state.releaseTime <= session.currentTime
)
actual_completion_rate = _percent(session.completedTaskCount, released_task_count)
```

Construct the case with `runtimeTaskCount=session.runtimeTaskCount`, `coverageRatePercent=_percent(covered_task_count, task_count)`, and `actualCompletionRatePercent=actual_completion_rate`.

- [x] **Step 5: Update online summary aggregation**

Aggregate exact totals and calculate:

```python
coverageRatePercent=_percent(total_covered_task_count, total_task_count)
actualCompletionRatePercent=_percent(total_completed_task_count, total_released_task_count)
```

Keep stability based on full coverage, zero conflicts, zero deadline misses, and zero failures.

- [x] **Step 6: Remove frontend experiment contract alignment only**

Because experiment endpoints are backend-only after Task 4, remove experiment request/result names from frontend/backend TypeScript field-alignment lists in `test_api_contract.py`. Keep OpenAPI request-model and response-model route assertions for all six backend endpoints.

- [x] **Step 7: Run experiment and API route contract tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_experiments.py backend\tests\test_api_contract.py -q
```

Expected: all selected tests pass and all six experiment OpenAPI routes remain verified.

---

### Task 4: Delete obsolete frontend experiment-panel code

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/main.test.ts`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/domain/sessionApi.ts`
- Modify: `frontend/src/domain/sessionApi.test.ts`
- Modify: `frontend/src/domain/types.ts`

**Interfaces:**
- Keeps: session API client, dispatch/session types, operational dashboard.
- Removes: all frontend experiment API request/result types and client functions because the six endpoints are backend-only evidence interfaces.

- [x] **Step 1: Delete experiment-only frontend tests first**

Remove the `experiment summaries` test block and experiment-only imports from `main.test.ts`. Remove the six experiment-client request tests and experiment type imports from `sessionApi.test.ts`. Do not remove session API tests.

- [x] **Step 2: Delete experiment-only implementation and types**

Remove from `main.tsx` the `ExperimentStatus`, summary/chart/highlight types and the contiguous experiment helper section from `buildExperimentSummaryRows` through `downloadExperimentCsv`, plus `experimentStatusLabel` if unused afterward.

Remove from `sessionApi.ts` the six `/api/experiments/*` client functions and imports. Remove all experiment request/result type declarations from `types.ts` while retaining `DispatchOptions` and normal session types.

- [x] **Step 3: Delete experiment-panel CSS**

Remove `.experiment-panel` through the final experiment report/chart/table style rules. Confirm no `experiment-` class remains in source.

- [x] **Step 4: Run dead-reference scans**

Run:

```powershell
rg -n --encoding UTF8 "Experiment|experiment-|buildExperiment|runSeededPressureExperiment|runOnlinePressureExperiment" frontend\src
```

Expected: no production or test references remain.

- [x] **Step 5: Run frontend build and tests**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run build
& 'C:\nvm4w\nodejs\npm.cmd' --prefix frontend run test
```

Expected: build succeeds and all remaining frontend tests pass.

---

### Task 5: Align project documentation and roadmap status

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/algorithm.md`
- Modify: `docs/baseline.md`
- Modify: `docs/demo.md`
- Modify: `docs/experiments.md`
- Modify: `docs/testing-guide.md`
- Keep: `docs/superpowers/specs/2026-07-14-project-requirements-cleanup-design.md`
- Keep: `docs/superpowers/plans/2026-07-14-project-requirements-cleanup.md`

**Interfaces:**
- Documentation is the human-facing contract for the exact names and meanings implemented in Tasks 1-4.

- [x] **Step 1: Remove stale UI and charging claims**

State that the main dashboard has no experiment panel and no dynamic-event toggle. State that `integrated-demo` displays charging infrastructure but does not have to trigger charging; low-battery scenarios and automated regressions verify the charging loop.

- [x] **Step 2: Document priority 0-5**

Add the approved table and rules: higher numeric priority wins, emergency is at least 4, equal priorities do not preempt, and `-1` is not used.

- [x] **Step 3: Document both pressure endpoints**

Add exact sections for:

```text
POST /api/experiments/seeded-pressure
POST /api/experiments/online-pressure
```

Explain standard/extended fixed seeds, assignment rate, online runtime flow, coverage rate, actual completion rate, and backend-only usage.

- [x] **Step 4: Document three-level conflict acceptance**

Clearly label current online tick safety and current planning-quality criteria as implemented requirements. Label full-horizon zero-conflict planning and an execution safety gate as not implemented future work.

- [x] **Step 5: Replace progress percentages with statuses**

Remove the overall 98% and module percentage suffixes. Use only: `稳定基线`, `接近完成`, `部分完成`, `未开始`, and `延后/可选`. Remove all claims that a frontend experiment panel currently exists.

- [x] **Step 6: Scan documentation for obsolete terms**

Run:

```powershell
rg -n --encoding UTF8 "整体完成度|98%|实验面板|includeDynamic|streamTaskCount|manualTaskCount|completionRatePercent|-1.*普通任务|默认.*充电" AGENTS.md README.md docs
```

Expected: any remaining matches are explicit historical/removal explanations in the approved design, not current product claims.

---

### Task 6: Full verification and one unified commit

**Files:**
- Verify every modified file in the working tree.

**Interfaces:**
- Produces one reviewed commit containing the current fixed-demo fixes, approved design, implementation plan, code cleanup, contract changes, tests, and documentation.

- [x] **Step 1: Run targeted backend tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_schema_constraints.py backend\tests\test_experiments.py backend\tests\test_api_contract.py backend\tests\test_e2e_demo.py -q
```

Expected: all selected tests pass.

- [x] **Step 2: Run the complete project check**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Expected: frontend production build, all frontend tests, and all backend tests pass.

- [x] **Step 3: Run source-integrity scans**

Run:

```powershell
git diff --check
rg -n --encoding UTF8 "\?\?\?+|锟|�" backend frontend docs AGENTS.md README.md
rg -n --encoding UTF8 "manualTaskCount|streamTaskCount|completionRatePercent" backend frontend README.md docs\algorithm.md docs\baseline.md docs\demo.md docs\experiments.md docs\testing-guide.md AGENTS.md
```

Expected: no whitespace errors, no damaged UTF-8 text, and no obsolete contract fields outside the design/plan's historical explanations.

- [x] **Step 4: Review final diff and status**

Run:

```powershell
git diff --stat
git status --short
git diff -- AGENTS.md README.md backend frontend docs
```

Expected: only approved scope is present; no generated `dist`, caches, or unrelated files are staged.

- [x] **Step 5: Stage and commit once**

Run:

```powershell
git add AGENTS.md README.md backend frontend docs
git commit -m "refactor: align online dispatch requirements"
```

Expected: one commit succeeds after all verification evidence is current.
