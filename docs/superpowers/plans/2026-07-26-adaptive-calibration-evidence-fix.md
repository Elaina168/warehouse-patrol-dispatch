# Adaptive Calibration Evidence Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove battery/deadline confounders from the adaptive pressure calibration case and report authoritative cumulative online travel distance without changing production dispatch behavior or policy thresholds.

**Architecture:** Normalize only the deep-copied `adaptive-pressure-r8-t45` calibration input while leaving `density-r8-t43` unchanged. Give every non-null base task the common calibration deadline T=120 so the legal scheduling horizon matches the fixed observation endpoint. Read cumulative distance and active conflicts from the terminal `MetricSnapshot`, fail completed runs that have no metric history, then regenerate and document smoke and 60-run evidence using the existing schema-v1 reporter.

**Tech Stack:** Python 3.13, FastAPI/Pydantic v2 models, pytest, online session APIs, Windows `multiprocessing` spawn/Pipe isolation, JSON/CSV reports, Node/npm command entry.

## Global Constraints

- Work only in `D:\codex\summer\.worktrees\adaptive-calibration-evidence-fix` on branch `codex/adaptive-calibration-evidence-fix`.
- Read and write all text as UTF-8. Use `Get-Content -Encoding UTF8` for Chinese files; do not use `sed` or `awk` on Chinese content.
- Keep production `SLOW_REPLAN_ENTER_THRESHOLD_MS = 60`, `SLOW_REPLAN_EXIT_THRESHOLD_MS = 40`, `REPLAN_TIME_SAMPLE_WINDOW = 5`, `MIN_REPLAN_TIME_SAMPLES = 3`, and task-pressure multiplier `2` unchanged.
- Do not change HTTP routes, Pydantic API fields, frontend types, OpenAPI models, beam score, candidate order, A*, task locks, preemption, charging, recovery, or execution-safety rules.
- Keep the report schema at version 1. Do not add or rename case IDs, variant IDs, JSON fields, or CSV fields.
- Keep `correctnessStable` unchanged: full coverage, zero active conflicts, zero deadline misses, and zero failures.
- Keep all three calibration case `tick_target` values at 120. In `adaptive-pressure-r8-t45`, every non-null base-task deadline must equal 120; dynamic and runtime task deadlines remain unchanged.
- Calibration sessions must keep `enforce_execution_safety=True`.
- Do not add a frontend panel or experiment API.
- Do not add real-wall-clock assertions to pytest.
- Do not change the 30-second per-run timeout or hide unstable/timeout/error outcomes.
- Never commit `.superpowers/`, `output/`, `.venv`, `frontend/node_modules`, generated JSON/CSV, build output, or Python caches.
- Use `apply_patch` for source, test, and documentation edits.
- Every production-code task follows RED → GREEN and ends in a focused commit.

---

## File and Responsibility Map

### Existing files to modify

- `backend/benchmarks/adaptive_scenarios.py`
  - Owns the two calibration-only normalization constants and applies them only to the pressure case deep copy.
- `backend/benchmarks/adaptive_runner.py`
  - Selects the terminal metric snapshot and maps cumulative online distance into `AdaptiveCalibrationRun`.
- `backend/tests/test_adaptive_replan_calibration.py`
  - Protects exact scenario transformation, source immutability, full pressure completion by T=120, authoritative distance selection, missing-history failure, and real online distance.
- `AGENTS.md`
  - Records the repaired evidence boundary and next decision gate.
- `docs/algorithm.md`
  - Distinguishes cumulative online distance from the current dispatch plan distance.
- `docs/experiments.md`
  - Preserves the original unstable evidence and appends the repaired run.
- `docs/testing-guide.md`
  - Adds reproducible pressure smoke and positive-distance artifact checks.

### Existing files that must remain unchanged

- `backend/app/replan_window.py`
- `backend/app/dispatch.py`
- `backend/app/sessions.py`
- `backend/benchmarks/adaptive_results.py`
- `backend/benchmarks/adaptive_reporting.py`
- `backend/benchmarks/adaptive_variants.py`
- `backend/benchmarks/process_isolation.py`
- `backend/benchmarks/scenarios.py`
- `backend/app/seeded_scenarios.py`
- `package.json`
- all frontend source and API types

---

### Task 1: Normalize Only the Adaptive Pressure Calibration Input

**Files:**
- Modify: `backend/benchmarks/adaptive_scenarios.py`
- Test: `backend/tests/test_adaptive_replan_calibration.py`

**Interfaces:**
- Consumes:
  - `AdaptiveCalibrationCase.tick_target`
  - `build_benchmark_scenario("density-r8-t43")`
  - existing `Scenario.model_copy(deep=True)`
- Produces:
  - `PRESSURE_CALIBRATION_BATTERY_BUDGET = 150`
  - `PRESSURE_CALIBRATION_DEADLINE_FLOOR = 120`
  - normalized `build_adaptive_calibration_scenario("adaptive-pressure-r8-t45")`

- [ ] **Step 1: Import the two new constants in the calibration test**

Extend the existing import from `backend.benchmarks.adaptive_scenarios`:

```python
from backend.benchmarks.adaptive_scenarios import (
    PRESSURE_CALIBRATION_BATTERY_BUDGET,
    PRESSURE_CALIBRATION_DEADLINE_FLOOR,
    RUNTIME_TASK_TICKS,
    adaptive_calibration_cases,
    build_adaptive_calibration_scenario,
    build_calibration_runtime_task,
)
```

- [ ] **Step 2: Replace the narrow pressure release test with an exact transformation test**

Replace `test_pressure_case_preserves_seeded_release_schedule` with:

```python
def test_pressure_case_normalizes_resources_without_changing_workload() -> None:
    source = build_benchmark_scenario("density-r8-t43")
    source_before = source.model_dump(mode="json")
    scenario = build_adaptive_calibration_scenario(
        "adaptive-pressure-r8-t45"
    )

    assert PRESSURE_CALIBRATION_BATTERY_BUDGET == 150
    assert PRESSURE_CALIBRATION_DEADLINE_FLOOR == 120
    assert [
        robot.model_dump(mode="json")
        for robot in scenario.robots
    ] == [
        robot.model_copy(
            update={
                "battery": PRESSURE_CALIBRATION_BATTERY_BUDGET,
                "batteryCapacity": (
                    PRESSURE_CALIBRATION_BATTERY_BUDGET
                ),
            }
        ).model_dump(mode="json")
        for robot in source.robots
    ]
    assert [
        task.model_dump(mode="json")
        for task in scenario.tasks
    ] == [
        task.model_copy(
            update={
                "deadline": max(
                    task.deadline,
                    PRESSURE_CALIBRATION_DEADLINE_FLOOR,
                )
                if task.deadline is not None
                else None
            }
        ).model_dump(mode="json")
        for task in source.tasks
    ]
    assert scenario.dynamic.model_dump(mode="json") == (
        source.dynamic.model_dump(mode="json")
    )
    assert scenario.width == source.width
    assert scenario.height == source.height
    assert scenario.obstacles == source.obstacles
    assert scenario.zones == source.zones
    assert all(
        task.deadline is not None
        and task.deadline
        >= PRESSURE_CALIBRATION_DEADLINE_FLOOR
        for task in scenario.tasks
    )
    assert [task.releaseTime for task in scenario.tasks[:7]] == [
        0,
        1,
        2,
        3,
        4,
        5,
        0,
    ]
    assert len(scenario.tasks) == 40
    assert len(scenario.dynamic.tasks) == 3
    assert build_benchmark_scenario(
        "density-r8-t43"
    ).model_dump(mode="json") == source_before
```

This test deliberately compares every robot and base-task field, not only battery/deadline samples, and explicitly protects map dimensions, obstacles, zones, dynamic content, and the deadline floor. Deep-copy behavior remains protected by the existing `test_calibration_transform_deep_copies_persistent_source`, which monkeypatches the builder to return the same source object and is rerun in the complete module.

- [ ] **Step 3: Strengthen the cross-case isolation test**

Add:

```python
def test_pressure_normalization_does_not_change_other_calibration_cases() -> None:
    low_load_before = build_adaptive_calibration_scenario(
        "adaptive-low-load-r4-t17"
    ).model_dump(mode="json")
    transition_before = build_adaptive_calibration_scenario(
        "adaptive-transition-r4-t6"
    ).model_dump(mode="json")

    build_adaptive_calibration_scenario("adaptive-pressure-r8-t45")

    assert build_adaptive_calibration_scenario(
        "adaptive-low-load-r4-t17"
    ).model_dump(mode="json") == low_load_before
    assert build_adaptive_calibration_scenario(
        "adaptive-transition-r4-t6"
    ).model_dump(mode="json") == transition_before
```

- [ ] **Step 4: Run the two tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py `
  -q `
  -k "pressure_case_normalizes_resources or pressure_normalization_does_not_change"
```

Expected: collection fails because the two constants do not exist.

- [ ] **Step 5: Add the calibration-only constants**

Immediately after `RUNTIME_TASK_TICKS` in `backend/benchmarks/adaptive_scenarios.py`, add:

```python
PRESSURE_CALIBRATION_BATTERY_BUDGET = 150
PRESSURE_CALIBRATION_DEADLINE_FLOOR = 120
```

- [ ] **Step 6: Apply the pressure-only deep-copy transformation**

In `build_adaptive_calibration_scenario`, add a pressure branch between the existing low-load and transition branches:

```python
    elif case.case_id == "adaptive-pressure-r8-t45":
        for robot in scenario.robots:
            robot.battery = PRESSURE_CALIBRATION_BATTERY_BUDGET
            robot.batteryCapacity = (
                PRESSURE_CALIBRATION_BATTERY_BUDGET
            )
        for task in scenario.tasks:
            if task.deadline is not None:
                task.deadline = max(
                    task.deadline,
                    PRESSURE_CALIBRATION_DEADLINE_FLOOR,
                )
```

Do not modify `scenario.dynamic.tasks`, `scenario.dynamic.blockedCells`, runtime-task construction, or the source benchmark scenario.

- [ ] **Step 7: Run focused GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py `
  -q `
  -k "case_catalog or scenarios_are_valid or low_load_case or pressure_case or pressure_normalization or transition_case or runtime_task or transform"
```

Expected: all selected tests pass.

- [ ] **Step 8: Run the complete calibration test module**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit Task 1**

```powershell
git add -- `
  backend/benchmarks/adaptive_scenarios.py `
  backend/tests/test_adaptive_replan_calibration.py
git diff --cached --check
git commit -m "fix: normalize adaptive pressure calibration"
```

---

### Task 2: Report Terminal Cumulative Online Distance

**Files:**
- Modify: `backend/benchmarks/adaptive_runner.py`
- Test: `backend/tests/test_adaptive_replan_calibration.py`

**Interfaces:**
- Consumes:
  - `SessionResult.metricsHistory: list[MetricSnapshot]`
  - `MetricSnapshot.travelledDistance`
  - `MetricSnapshot.activeConflictCount`
- Produces:
  - `AdaptiveCalibrationRun.total_distance` from the terminal snapshot
  - exact missing-history error `自适应窗口校准完成但缺少指标历史`

- [ ] **Step 1: Add a test that distinguishes plan distance from cumulative distance**

Add after `test_adaptive_calibration_executes_real_online_flow`:

```python
def test_adaptive_run_uses_terminal_metric_snapshot_distance(
    monkeypatch,
) -> None:
    real_tick_session = adaptive_runner_module.tick_session

    def tick_with_distance_sentinel(session_id, request):
        result = real_tick_session(session_id, request)
        if request.currentTime != 120:
            return result
        terminal_snapshot = result.metricsHistory[-1].model_copy(
            update={"travelledDistance": 777}
        )
        plan_metrics = result.result.metrics.model_copy(
            update={"totalDistance": 0}
        )
        return result.model_copy(
            update={
                "metricsHistory": [
                    *result.metricsHistory[:-1],
                    terminal_snapshot,
                ],
                "result": result.result.model_copy(
                    update={"metrics": plan_metrics}
                ),
            }
        )

    monkeypatch.setattr(
        adaptive_runner_module,
        "tick_session",
        tick_with_distance_sentinel,
    )

    run = execute_adaptive_calibration_case(
        "adaptive-transition-r4-t6",
        "fixed-24",
        1,
    )

    assert run.total_distance == 777
```

The test must fail if the implementation reads `result.metrics.totalDistance`, takes the maximum plan distance, or writes a constant positive value.

- [ ] **Step 2: Add the missing-history failure and cleanup test**

Add:

```python
def test_adaptive_run_rejects_missing_metric_history_and_cleans_session(
    monkeypatch,
) -> None:
    session_ids_before = {
        item.sessionId for item in list_sessions()
    }
    real_tick_session = adaptive_runner_module.tick_session

    def tick_without_terminal_history(session_id, request):
        result = real_tick_session(session_id, request)
        if request.currentTime == 120:
            return result.model_copy(update={"metricsHistory": []})
        return result

    monkeypatch.setattr(
        adaptive_runner_module,
        "tick_session",
        tick_without_terminal_history,
    )

    with pytest.raises(
        RuntimeError,
        match="^自适应窗口校准完成但缺少指标历史$",
    ):
        execute_adaptive_calibration_case(
            "adaptive-transition-r4-t6",
            "fixed-24",
            1,
        )

    assert {
        item.sessionId for item in list_sessions()
    } == session_ids_before
```

- [ ] **Step 3: Strengthen the existing real-online-flow assertion**

In `test_adaptive_calibration_executes_real_online_flow`, add:

```python
    assert run.total_distance is not None
    assert run.total_distance > 0
```

- [ ] **Step 4: Run the three tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py `
  -q `
  -k "executes_real_online_flow or terminal_metric_snapshot_distance or missing_metric_history"
```

Expected:

- the sentinel test fails because the run still reports plan distance 0;
- the missing-history test fails because the current fallback accepts an empty list;
- the real-flow positive-distance assertion fails.

- [ ] **Step 5: Require and select the terminal metric snapshot**

In `execute_adaptive_calibration_case`, immediately before assigning `metrics = session.result.metrics`, add:

```python
    if not session.metricsHistory:
        raise RuntimeError(
            "自适应窗口校准完成但缺少指标历史"
        )
    final_snapshot = session.metricsHistory[-1]
```

Replace:

```python
    active_conflict_count = (
        session.metricsHistory[-1].activeConflictCount
        if session.metricsHistory
        else 0
    )
```

with:

```python
    active_conflict_count = final_snapshot.activeConflictCount
```

Replace:

```python
        total_distance=metrics.totalDistance,
```

with:

```python
        total_distance=final_snapshot.travelledDistance,
```

Do not change deadline, failure, makespan, observer, wall-clock, or correctness calculations.

- [ ] **Step 6: Run focused GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py `
  -q `
  -k "executes_real_online_flow or terminal_metric_snapshot_distance or missing_metric_history or worker_cleans_session or worker_enforces_safety"
```

Expected: all selected tests pass.

- [ ] **Step 7: Run calibration and process-isolation regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py `
  backend\tests\test_benchmark_process_isolation.py `
  backend\tests\test_algorithm_benchmark.py `
  -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 2**

```powershell
git add -- `
  backend/benchmarks/adaptive_runner.py `
  backend/tests/test_adaptive_replan_calibration.py
git diff --cached --check
git commit -m "fix: report cumulative adaptive distance"
```

---

## Approved Amendment After the Initial Pressure Smoke

The first four-variant smoke after Tasks 1 and 2 completed only 44/45 tasks in every variant. The sole unfinished task was `D33`: its source deadline remained T=122, its legal completion time was T=121, and at T=120 R8 was still one cell short of the dropoff. All failure, deadline, conflict, and safety counters were zero, so this was an observation-horizon mismatch rather than a planner failure or battery shortage.

User-approved ruling:

- keep every calibration case `tick_target = 120`;
- do not special-case `D33`;
- do not raise battery beyond 150;
- replace the pressure-case deadline floor with one common calibration deadline T=120 for every non-null base task;
- preserve dynamic tasks and the T=20/T=40 runtime tasks exactly;
- add a real online fixed-24 regression proving 45/45 completion by T=120 before rerunning evidence.

This amendment supersedes only Task 1's `max(originalDeadline, 120)` rule and the `PRESSURE_CALIBRATION_DEADLINE_FLOOR` name. All other completed Task 1/2 behavior and review conclusions remain binding.

---

### Task 3: Align Pressure Deadlines with the Observation Horizon

**Files:**
- Modify: `backend/benchmarks/adaptive_scenarios.py`
- Test: `backend/tests/test_adaptive_replan_calibration.py`

**Interfaces:**
- Consumes:
  - approved pressure battery normalization from Task 1
  - cumulative-distance mapping from Task 2
  - `execute_adaptive_calibration_case(case_id, variant_id, run_index)`
- Produces:
  - `PRESSURE_CALIBRATION_DEADLINE = 120`
  - every non-null pressure base-task deadline equal to 120
  - real fixed-24 pressure completion evidence at the existing T=120 target

- [ ] **Step 1: Change the exact transformation expectation before production code**

In `test_pressure_case_normalizes_resources_without_changing_workload`, replace the expected base-task deadline update:

```python
                "deadline": max(
                    task.deadline,
                    PRESSURE_CALIBRATION_DEADLINE_FLOOR,
                )
                if task.deadline is not None
                else None
```

with:

```python
                "deadline": (
                    PRESSURE_CALIBRATION_DEADLINE_FLOOR
                    if task.deadline is not None
                    else None
                )
```

Replace the floor assertion:

```python
    assert all(
        task.deadline is not None
        and task.deadline
        >= PRESSURE_CALIBRATION_DEADLINE_FLOOR
        for task in scenario.tasks
    )
```

with:

```python
    assert all(
        task.deadline == PRESSURE_CALIBRATION_DEADLINE_FLOOR
        for task in scenario.tasks
    )
```

- [ ] **Step 2: Add a real pressure-horizon completion regression**

Add after `test_adaptive_calibration_executes_real_online_flow`:

```python
def test_pressure_calibration_completes_by_tick_target() -> None:
    run = execute_adaptive_calibration_case(
        "adaptive-pressure-r8-t45",
        "fixed-24",
        1,
    )

    assert run.outcome == "completed"
    assert run.tick_target == 120
    assert run.released_task_count == 45
    assert run.covered_task_count == 45
    assert run.completed_task_count == 45
    assert run.coverage_rate_percent == 100
    assert run.actual_completion_rate_percent == 100
    assert run.correctness_stable is True
    assert run.predicted_conflict_count == 0
    assert run.active_conflict_count == 0
    assert run.safety_intervention_count == 0
    assert run.deadline_miss_count == 0
    assert run.failure_count == 0
    assert run.total_distance is not None
    assert run.total_distance > 0
```

This test must fail against Task 1's floor rule because the run stops with `completed_task_count == 44` at T=120.

- [ ] **Step 3: Run the two regressions and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py `
  -q `
  -k "pressure_case_normalizes_resources or pressure_calibration_completes_by_tick_target"
```

Expected: two failures. The transformation still preserves deadlines above 120, and the real pressure run still completes only 44/45 tasks.

- [ ] **Step 4: Apply the common pressure deadline**

In the pressure branch of `build_adaptive_calibration_scenario`, replace:

```python
        for task in scenario.tasks:
            if task.deadline is not None:
                task.deadline = max(
                    task.deadline,
                    PRESSURE_CALIBRATION_DEADLINE_FLOOR,
                )
```

with:

```python
        for task in scenario.tasks:
            if task.deadline is not None:
                task.deadline = (
                    PRESSURE_CALIBRATION_DEADLINE_FLOOR
                )
```

Do not change dynamic tasks, runtime tasks, releases, priorities, task IDs, targets, map content, or `tick_target`.

- [ ] **Step 5: Run focused GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py `
  -q `
  -k "pressure_case_normalizes_resources or pressure_calibration_completes_by_tick_target"
```

Expected: both selected tests pass, including real 45/45 completion at T=120.

- [ ] **Step 6: Rename the internal constant as a green refactor**

Rename:

```python
PRESSURE_CALIBRATION_DEADLINE_FLOOR = 120
```

to:

```python
PRESSURE_CALIBRATION_DEADLINE = 120
```

Update the exact import and all pressure-deadline references in `backend/tests/test_adaptive_replan_calibration.py`. Do not retain a compatibility alias; the identifier is internal and the old floor semantics are no longer valid.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py `
  -q `
  -k "pressure_case_normalizes_resources or pressure_calibration_completes_by_tick_target"
```

Expected: both selected tests remain green after the rename.

- [ ] **Step 7: Run the complete calibration module**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit Task 3**

```powershell
git add -- `
  backend/benchmarks/adaptive_scenarios.py `
  backend/tests/test_adaptive_replan_calibration.py
git diff --cached --check
git commit -m "fix: align adaptive pressure deadlines"
```

---

### Task 4: Verify and Publish Repaired Calibration Evidence

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/algorithm.md`
- Modify: `docs/experiments.md`
- Modify: `docs/testing-guide.md`

**Interfaces:**
- Consumes:
  - repaired schema-v1 CLI output
  - existing four final files:
    - `results.json`
    - `runs.csv`
    - `replan-observations.csv`
    - `variant-summaries.csv`
- Produces:
  - reproducible pressure smoke procedure
  - reviewed 60-run evidence
  - documented same-machine candidate envelope without applying it

- [ ] **Step 1: Run all focused suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  backend\tests\test_adaptive_replan_calibration.py `
  backend\tests\test_replan_window.py `
  backend\tests\test_sessions.py `
  backend\tests\test_benchmark_process_isolation.py `
  backend\tests\test_algorithm_benchmark.py `
  -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run the complete project check**

Run:

```powershell
chcp 65001
$OutputEncoding = [Console]::OutputEncoding = (
  [System.Text.UTF8Encoding]::new()
)
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Expected: frontend build, all frontend tests, and all backend tests pass.

- [ ] **Step 3: Capture worker baseline and run the four-variant pressure smoke**

Do not run another benchmark, test suite, browser stress flow, or other heavy command concurrently.

```powershell
$benchmarkWorkerPidsBefore = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -eq 'python.exe' -and
      $_.CommandLine -like '*multiprocessing.spawn*'
    } |
    Select-Object -ExpandProperty ProcessId
)
$pressureSmokePath = (
  & 'C:\nvm4w\nodejs\npm.cmd' run benchmark:adaptive-replan -- `
    --cases adaptive-pressure-r8-t45 `
    --repetitions 1 `
    --timeout-seconds 30 `
    --output-dir output/adaptive-calibration-evidence-fix-smoke |
    Select-Object -Last 1
).Trim()
if ($LASTEXITCODE -ne 0) {
  throw "压力校准 smoke 失败，退出码: $LASTEXITCODE"
}
if (-not (
  Test-Path -LiteralPath $pressureSmokePath -PathType Container
)) {
  throw "压力校准 smoke 结果目录不存在: $pressureSmokePath"
}
$pressureSmokePath
```

- [ ] **Step 4: Cross-check every smoke run**

```powershell
$smokeCheck = @'
import csv
import json
import sys
from pathlib import Path

result_path = Path(sys.argv[1])
payload = json.loads(
    (result_path / "results.json").read_text(encoding="utf-8")
)
runs = payload["runs"]
assert payload["schemaVersion"] == 1
assert len(runs) == 4
assert len(payload["variantSummaries"]) == 4
assert {run["variantId"] for run in runs} == {
    "fixed-4",
    "fixed-24",
    "fixed-48",
    "adaptive-current-24",
}
for run in runs:
    assert run["caseId"] == "adaptive-pressure-r8-t45"
    assert run["outcome"] == "completed"
    assert run["correctnessStable"] is True
    assert run["releasedTaskCount"] == 45
    assert run["coveredTaskCount"] == 45
    assert run["completedTaskCount"] == 45
    assert run["coverageRatePercent"] == 100
    assert run["actualCompletionRatePercent"] == 100
    assert run["predictedConflictCount"] == 0
    assert run["activeConflictCount"] == 0
    assert run["safetyInterventionCount"] == 0
    assert run["deadlineMissCount"] == 0
    assert run["failureCount"] == 0
    assert run["totalDistance"] > 0
assert not (result_path / "results.partial.json").exists()
assert not list(result_path.glob("*.tmp"))
for file_name in (
    "runs.csv",
    "replan-observations.csv",
    "variant-summaries.csv",
):
    assert (result_path / file_name).read_bytes()[:3] == bytes(
        (239, 187, 191)
    )
with (result_path / "runs.csv").open(
    encoding="utf-8-sig",
    newline="",
) as handle:
    rows = list(csv.DictReader(handle))
assert len(rows) == 4
assert all(float(row["totalDistance"]) > 0 for row in rows)
print(
    json.dumps(
        {
            "runs": len(runs),
            "stable": sum(
                run["correctnessStable"] for run in runs
            ),
            "completedTasks": [
                run["completedTaskCount"] for run in runs
            ],
            "totalDistances": [
                run["totalDistance"] for run in runs
            ],
        },
        ensure_ascii=False,
    )
)
'@
$smokeCheck |
  .\.venv\Scripts\python.exe - $pressureSmokePath
if ($LASTEXITCODE -ne 0) {
  throw "压力校准 smoke artifact 核对失败"
}
```

- [ ] **Step 5: Verify no smoke worker residue**

```powershell
$newBenchmarkWorkers = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq 'python.exe' -and
    $_.CommandLine -like '*multiprocessing.spawn*' -and
    $_.ProcessId -notin $benchmarkWorkerPidsBefore
  }
if ($newBenchmarkWorkers) {
  $newBenchmarkWorkers |
    Select-Object ProcessId,CommandLine |
    Format-Table -AutoSize
  throw "检测到压力校准 smoke 遗留的 spawn worker"
}
```

If any smoke assertion fails, stop. Report the exact run and do not proceed to the 60-run or documentation update.

- [ ] **Step 6: Run the default 60-run calibration**

Capture a fresh worker baseline again, then run:

```powershell
$benchmarkWorkerPidsBefore = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -eq 'python.exe' -and
      $_.CommandLine -like '*multiprocessing.spawn*'
    } |
    Select-Object -ExpandProperty ProcessId
)
$calibrationResultPath = (
  & 'C:\nvm4w\nodejs\npm.cmd' run benchmark:adaptive-replan -- `
    --repetitions 5 `
    --timeout-seconds 30 `
    --output-dir output/adaptive-replan-calibration |
    Select-Object -Last 1
).Trim()
if ($LASTEXITCODE -ne 0) {
  throw "默认自适应窗口校准失败，退出码: $LASTEXITCODE"
}
if (-not (
  Test-Path -LiteralPath $calibrationResultPath -PathType Container
)) {
  throw "校准结果目录不存在: $calibrationResultPath"
}
$calibrationResultPath
```

- [ ] **Step 7: Cross-check the complete artifacts**

```powershell
$artifactCheck = @'
import csv
import json
import sys
from collections import Counter
from pathlib import Path

result_path = Path(sys.argv[1])
payload = json.loads(
    (result_path / "results.json").read_text(encoding="utf-8")
)
known_cases = {
    "adaptive-low-load-r4-t17",
    "adaptive-pressure-r8-t45",
    "adaptive-transition-r4-t6",
}
known_variants = {
    "fixed-4",
    "fixed-24",
    "fixed-48",
    "adaptive-current-24",
}

def csv_rows(file_name):
    with (result_path / file_name).open(
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        return list(csv.DictReader(handle))

runs = payload["runs"]
run_rows = csv_rows("runs.csv")
observation_rows = csv_rows("replan-observations.csv")
summary_rows = csv_rows("variant-summaries.csv")
assert payload["schemaVersion"] == 1
assert len(runs) == 60
assert len(run_rows) == 60
assert len(summary_rows) == 12
assert len(observation_rows) == sum(
    run["replanCount"] for run in runs
)
assert all(run["caseId"] in known_cases for run in runs)
assert all(run["variantId"] in known_variants for run in runs)
assert all(run["outcome"] == "completed" for run in runs)
assert all(run["correctnessStable"] is True for run in runs)
assert all(run["totalDistance"] > 0 for run in runs)
assert not (result_path / "results.partial.json").exists()
assert not list(result_path.glob("*.tmp"))

pressure_runs = [
    run
    for run in runs
    if run["caseId"] == "adaptive-pressure-r8-t45"
]
assert len(pressure_runs) == 20
for run in pressure_runs:
    assert run["releasedTaskCount"] == 45
    assert run["coveredTaskCount"] == 45
    assert run["completedTaskCount"] == 45
    assert run["coverageRatePercent"] == 100
    assert run["actualCompletionRatePercent"] == 100
    assert run["predictedConflictCount"] == 0
    assert run["activeConflictCount"] == 0
    assert run["safetyInterventionCount"] == 0
    assert run["deadlineMissCount"] == 0
    assert run["failureCount"] == 0

assert payload["candidateEnvelopeAvailable"] is True
assert payload["candidateEnvelope"] is not None
for file_name in (
    "runs.csv",
    "replan-observations.csv",
    "variant-summaries.csv",
):
    assert (result_path / file_name).read_bytes()[:3] == bytes(
        (239, 187, 191)
    )

outcomes = Counter(run["outcome"] for run in runs)
print(
    json.dumps(
        {
            "runs": len(runs),
            "stable": sum(
                run["correctnessStable"] for run in runs
            ),
            "observations": len(observation_rows),
            "summaries": len(summary_rows),
            "outcomes": dict(outcomes),
            "pressureDistanceRange": [
                min(run["totalDistance"] for run in pressure_runs),
                max(run["totalDistance"] for run in pressure_runs),
            ],
            "candidateEnvelope": payload["candidateEnvelope"],
        },
        ensure_ascii=False,
    )
)
'@
$artifactCheck |
  .\.venv\Scripts\python.exe - $calibrationResultPath
if ($LASTEXITCODE -ne 0) {
  throw "默认校准 artifact 交叉核对失败"
}

$newBenchmarkWorkers = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq 'python.exe' -and
    $_.CommandLine -like '*multiprocessing.spawn*' -and
    $_.ProcessId -notin $benchmarkWorkerPidsBefore
  }
if ($newBenchmarkWorkers) {
  $newBenchmarkWorkers |
    Select-Object ProcessId,CommandLine |
    Format-Table -AutoSize
  throw "检测到默认校准遗留的 spawn worker"
}
```

- [ ] **Step 8: Update the four documents from actual evidence**

Use `apply_patch`. Make these exact semantic updates:

- `AGENTS.md`
  - preserve the 2026-07-26 pre-fix 40 stable/20 unstable result as historical diagnosis;
  - record the calibration-only battery/deadline normalization;
  - record that cumulative distance now comes from the terminal metric snapshot;
  - record the new result directory and the exact object printed by Step 7;
  - state that the candidate envelope is same-machine evidence and production remains `60/40ms`, latest-5/minimum-3, `2×`.
- `docs/algorithm.md`
  - distinguish `DispatchResult.metrics.totalDistance` for the current plan from `MetricSnapshot.travelledDistance` for cumulative online execution;
  - document the missing-history error rather than a zero fallback.
- `docs/experiments.md`
  - retain the pre-fix table and label it as pre-fix evidence;
  - append the new result path, 60-run counts, observation count, summary count, pressure distance range, and exact candidate envelope from Step 7;
  - state that no threshold was applied.
- `docs/testing-guide.md`
  - add the Step 3 pressure smoke command;
  - make artifact checks assert positive `totalDistance`, 45/45 pressure completion, 60 stable runs, and non-null candidate envelope;
  - retain worker, BOM, partial/tmp, and Git hygiene checks.

Do not transcribe wall-clock values from an older result directory.

- [ ] **Step 9: Run documentation, encoding, and Git scope checks**

```powershell
git diff --check
git status --short
git diff --name-only d3b5c61
```

The final command compares the base commit with committed Task 1/2/3 work and the still-uncommitted Task 4 documentation. Expected tracked scope after Task 4 is limited to:

```text
AGENTS.md
backend/benchmarks/adaptive_runner.py
backend/benchmarks/adaptive_scenarios.py
backend/tests/test_adaptive_replan_calibration.py
docs/algorithm.md
docs/experiments.md
docs/superpowers/plans/2026-07-26-adaptive-calibration-evidence-fix.md
docs/superpowers/specs/2026-07-26-adaptive-calibration-evidence-fix-design.md
docs/testing-guide.md
```

Generated `output/`, `.superpowers/`, `.venv`, `frontend/node_modules`, build output, and caches must remain ignored or untracked.

- [ ] **Step 10: Commit Task 4**

```powershell
git add -- `
  AGENTS.md `
  docs/algorithm.md `
  docs/experiments.md `
  docs/testing-guide.md
git diff --cached --check
git commit -m "docs: record repaired adaptive calibration evidence"
```

- [ ] **Step 11: Request final branch review**

Review range:

```text
d3b5c61..HEAD
```

Treat as blocking:

- pressure normalization leaks into `density-r8-t43` or another case;
- any production policy, API, dispatch, A*, charging, recovery, or safety change;
- pressure smoke is not 45/45 completed with zero deadline/failure/conflict;
- `totalDistance` still reads current plan metrics or silently falls back to 0;
- empty metric history becomes a completed result;
- report schema or field names change;
- candidate envelope includes unstable/non-`fixed-24` evidence;
- generated reports, dependency junctions, or caches are committed;
- process residue or unbounded waiting appears.

After any final-review fix, rerun Task 4 Steps 1–9 before claiming completion.

---

## Final Acceptance Checklist

- [ ] Pressure normalization applies only to the deep-copied `adaptive-pressure-r8-t45`.
- [ ] Every pressure robot has `battery=150` and `batteryCapacity=150`.
- [ ] Every non-null pressure base-task deadline is exactly T=120.
- [ ] Source `density-r8-t43`, other calibration cases, dynamic blocks, releases, runtime tasks, and case/variant IDs remain unchanged.
- [ ] Terminal `MetricSnapshot.travelledDistance` is the only source of calibration `totalDistance`.
- [ ] Empty metric history produces an error and session cleanup.
- [ ] Report schema remains v1.
- [ ] Pressure smoke has four completed/stable runs, 45/45 completion, zero deadline/failure/conflict, and positive distance.
- [ ] Full `npm run check` passes.
- [ ] Default calibration has 60 completed/stable runs and no timeout/error.
- [ ] JSON/CSV values and counts agree; CSV BOM, partial/tmp, and worker checks pass.
- [ ] Candidate envelope is non-null same-machine evidence only.
- [ ] Production `60/40ms`, latest-5/minimum-3 samples, and `2×` pressure policy remain unchanged.
- [ ] Generated output remains untracked.
