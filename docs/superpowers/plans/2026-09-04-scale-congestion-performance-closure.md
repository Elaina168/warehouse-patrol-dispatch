# 规模与拥堵性能研究收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展并统一现有规模/拥堵离线基准，补齐在线真实重规划与任务分配工作量，随后以写时复制减少束搜索候选状态复制，并用优化前后各65次运行证明业务结果没有退化。

**Architecture:** 继续使用现有 `benchmark:algorithm`、隔离子进程和原子 JSON/CSV 报告，不新增业务 API 或前端入口。直接调度从 `PlanningDiagnostics` 取一次规划工作量，在线流程通过既有 `ReplanObservation` 聚合所有真实重规划；优化仅改变 `AssignmentCandidate` 的复制方式，不改变任务排序、评分、束宽或路径规划。

**Tech Stack:** Python 3.11、FastAPI/Pydantic 内部模型、pytest、multiprocessing spawn/Pipe、PowerShell/npm 脚本入口。

**Spec:** `docs/superpowers/specs/2026-09-04-scale-congestion-performance-closure-design.md`

## Global Constraints

- 从包含本计划的最新 `main` 创建并使用分支 `codex/scale-congestion-performance-closure`；不得直接在 `main` 实现。
- 所有源码、测试和文档保持 UTF-8；Python 代码注释使用中文。
- 不改变任何现有 FastAPI 路由、业务请求模型、业务响应模型或前端代码。
- 不修改束宽、任务排序、候选评分、路径规划顺序、滚动窗口阈值或执行安全门。
- 不新增 CBS/MAPF、分布式边缘外壳或并行基准运行。
- 所有基准仍逐案例、逐重复隔离运行；保留 `timeout`、`error` 和不稳定结果。
- 墙钟值只作同机辅助证据，不能成为 pytest 固定阈值；确定性复制计数和业务结果才是优化验收门。
- 不删除既有 `output/` 结果；新结果写入新的时间戳目录且保持 Git 忽略。
- 每个任务按 TDD 先看到相关新测试失败，再写最小实现并复测。

---

### Task 1: 记录任务分配的确定性工作量

**Files:**
- Modify: `backend/app/planning_diagnostics.py`
- Modify: `backend/app/dispatch.py:548-662`
- Modify: `backend/app/dispatch.py:2504-2602`
- Modify: `backend/app/replan_window.py:44-66`
- Modify: `backend/app/sessions.py:1988-2040`
- Modify: `backend/tests/test_algorithm.py`
- Modify: `backend/tests/test_sessions.py`
- Modify: `backend/tests/test_adaptive_replan_calibration.py:166-190`

**Interfaces:**
- Consumes: existing optional `PlanningDiagnostics` passed through `run_dispatch(..., planning_diagnostics=...)`.
- Produces: `PlanningDiagnostics.record_assignment_expansion(copied_robot_state_count: int) -> None`, `PlanningDiagnostics.record_assignment_beam_width(width: int) -> None`, and three new fields propagated into `ReplanObservation`.

- [ ] **Step 1: Add failing unit tests for assignment-work counters**

Add tests which run `scale-r4-t15` with `PlanningDiagnostics` and assert the pre-optimization implementation records full-copy behavior:

```python
def test_assignment_diagnostics_records_candidate_expansion_and_full_clone_work() -> None:
    diagnostics = PlanningDiagnostics()
    result = run_dispatch(
        build_benchmark_scenario("scale-r4-t15"),
        benchmark_options(),
        planning_diagnostics=diagnostics,
    )

    assert result.metrics.failureCount == 0
    assert diagnostics.assignment_candidate_expansion_count > 0
    assert (
        diagnostics.assignment_robot_state_copy_count
        == diagnostics.assignment_candidate_expansion_count * 4
    )
    assert 1 <= diagnostics.assignment_beam_peak_width <= 48
```

Extend `test_replan_observer_maps_all_planning_diagnostics_fields` so a fake diagnostic with values `101`, `404`, and `12` must appear unchanged on the emitted observation as:

```python
assert observation.assignment_candidate_expansion_count == 101
assert observation.assignment_robot_state_copy_count == 404
assert observation.assignment_beam_peak_width == 12
```

Update the sole `ReplanObservation(...)` test factory in `test_adaptive_replan_calibration.py` with explicit values for all three new fields.

- [ ] **Step 2: Run the focused tests and confirm they fail for missing fields/signatures**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm.py backend/tests/test_sessions.py backend/tests/test_adaptive_replan_calibration.py -q
```

Expected: new assertions fail because the three fields and collection methods do not exist. Existing unrelated failures are blockers and must be investigated before continuing.

- [ ] **Step 3: Add counters to `PlanningDiagnostics`**

Append these fields and methods to `PlanningDiagnostics`:

```python
assignment_candidate_expansion_count: int = 0
assignment_robot_state_copy_count: int = 0
assignment_beam_peak_width: int = 0

def record_assignment_expansion(self, copied_robot_state_count: int) -> None:
    self.assignment_candidate_expansion_count += 1
    self.assignment_robot_state_copy_count += copied_robot_state_count

def record_assignment_beam_width(self, width: int) -> None:
    self.assignment_beam_peak_width = max(
        self.assignment_beam_peak_width,
        width,
    )
```

Reject negative counts or widths with `ValueError`; add direct tests for both invalid inputs so diagnostics cannot silently emit impossible work values.

- [ ] **Step 4: Instrument the existing full-copy assignment loop without optimizing it yet**

Add an optional final parameter to preserve all existing positional callers:

```python
def assign_tasks_beam_search(
    ...,
    task_limit_per_robot: int | None = None,
    planning_diagnostics: PlanningDiagnostics | None = None,
) -> list[Assignment]:
```

After creating the initial candidate, record width `1`. Immediately before the current `clone_assignment_candidate(candidate)` call, record exactly one expansion and `len(candidate.robots)` copied robot states. After truncating a non-empty expanded layer, record the retained width. Pass the existing `run_dispatch` diagnostic object into this new final parameter.

Do not change `clone_assignment_candidate` in this task; the optimization must remain separable from the baseline instrumentation commit.

- [ ] **Step 5: Propagate the counters through online observer records**

Append three non-default integer fields to `ReplanObservation`:

```python
assignment_candidate_expansion_count: int
assignment_robot_state_copy_count: int
assignment_beam_peak_width: int
```

In `_notify_replan_observer`, map the three values from the exact `PlanningDiagnostics` instance used for that replan. Update every explicit constructor in tests; do not use defaults that could conceal a missing production mapping.

- [ ] **Step 6: Run focused and full diagnostics tests**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm.py backend/tests/test_sessions.py backend/tests/test_adaptive_replan_calibration.py backend/tests/test_algorithm_benchmark.py -q
```

Expected: all selected tests pass, including the assertion that the still-unoptimized implementation copies four robot states per `scale-r4-t15` expansion.

- [ ] **Step 7: Commit the instrumentation baseline**

```powershell
git add backend/app/planning_diagnostics.py backend/app/dispatch.py backend/app/replan_window.py backend/app/sessions.py backend/tests/test_algorithm.py backend/tests/test_sessions.py backend/tests/test_adaptive_replan_calibration.py
git commit -m "feat: measure assignment planning work"
```

---

### Task 2: Upgrade the algorithm benchmark catalog and schema to version 3

**Files:**
- Modify: `backend/benchmarks/scenarios.py`
- Modify: `backend/benchmarks/results.py`
- Modify: `backend/benchmarks/reporting.py`
- Modify: `backend/benchmarks/algorithm_boundary.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Consumes: `seeded_pressure_scenario(label: str, seed: int, robot_count: int, task_count: int) -> Scenario`.
- Produces: five exact `BenchmarkFamily` values, thirteen ordered `BenchmarkCase` records, `runtime_task_count` case metadata, and flat version-3 run/summary records.

- [ ] **Step 1: Write failing catalog tests for the exact thirteen-case matrix**

Replace the old nine-case-only expectation with an exact ordered assertion:

```python
assert [case.case_id for case in benchmark_cases()] == [
    "scale-r4-t15",
    "scale-r8-t27",
    "scale-r12-t39",
    "density-r8-t31",
    "density-r8-t43",
    "density-r8-t55",
    "seeded-s17-r4-t15",
    "seeded-s29-r6-t23",
    "seeded-s31-r8-t27",
    "bottleneck-r4-t4",
    "bottleneck-r6-t6",
    "bottleneck-r8-t8",
    "online-pressure-s17-r4-t17",
]
```

Assert the three `seeded` cases filter exactly by `benchmark_cases(("seeded",))`, the one online pressure case filters by `("online-pressure",)`, and unknown family text is still rejected.

For every case, assert:

```python
len(scenario.tasks) + len(scenario.dynamic.tasks) + case.runtime_task_count == case.task_count
```

- [ ] **Step 2: Write failing serialization and summary tests for schema version 3**

Extend `_benchmark_run` and all failed-run factories with:

```python
runtime_task_count=0,
runtime_mutation_count=0,
replan_observation_count=0,
assignment_candidate_expansion_count=20,
assignment_robot_state_copy_count=80,
assignment_beam_peak_width=12,
```

Assert `BenchmarkReport.create(...).schema_version == 3`, the six camel-case run keys exist, and a completed three-run summary produces the exact median/P95/max values for the new work fields. Assert timeout/error runs set unevaluated work fields to `None` while preserving case `runtimeTaskCount`.

- [ ] **Step 3: Run catalog/result/reporting tests and confirm failure**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: failures for unknown families/cases, absent dataclass fields and schema version 2.

- [ ] **Step 4: Extend the case model and deterministic builders**

Change the exact literals to:

```python
BenchmarkFamily = Literal[
    "scale",
    "density",
    "seeded",
    "bottleneck",
    "online-pressure",
]
```

Add `runtime_task_count: int` to `BenchmarkCase` and set it explicitly on all thirteen records. Preserve the original nine records byte-for-byte except for the new zero metadata.

Build the three seeded cases with base task counts `12`, `20`, and `24`. Build the online pressure scenario with seed `17`, four robots and twelve base tasks; its catalog task count is `12 + 3 + 2 = 17`.

- [ ] **Step 5: Extend version-3 run and summary models**

Add these `BenchmarkRun` fields and exact record keys:

```python
runtime_task_count: int                    # runtimeTaskCount
runtime_mutation_count: int | None         # runtimeMutationCount
replan_observation_count: int | None       # replanObservationCount
assignment_candidate_expansion_count: int | None
assignment_robot_state_copy_count: int | None
assignment_beam_peak_width: int | None
```

Add the six summary fields defined in the design and compute them only from completed, diagnostics-evaluated runs. `BenchmarkRun._failed` must preserve `case.runtime_task_count`, set runtime mutation/observation data to `None`, and set all new planning fields to `None`.

Set `BenchmarkReport.create` to `schema_version=3`. Add the new run and summary columns to `reporting.py` without renaming or reordering existing version-2 columns; append new columns at the end of their respective groups.

- [ ] **Step 6: Update the CLI family allowlist**

Set:

```python
DEFAULT_FAMILIES = (
    "scale",
    "density",
    "seeded",
    "bottleneck",
    "online-pressure",
)
```

Keep repetition, finite timeout and output-directory validation unchanged. Update parser tests to assert exact trimming and order for all five family names.

- [ ] **Step 7: Run the benchmark model tests**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: catalog, schema, statistics and reporting tests pass. Online execution may still fail until Task 3; isolate this task's assertions to construction and serialization.

- [ ] **Step 8: Commit the catalog/schema upgrade**

```powershell
git add backend/benchmarks/scenarios.py backend/benchmarks/results.py backend/benchmarks/reporting.py backend/benchmarks/algorithm_boundary.py backend/tests/test_algorithm_benchmark.py
git commit -m "feat: expand algorithm performance matrix"
```

---

### Task 3: Execute online benchmark flows with real replan diagnostics

**Files:**
- Create: `backend/benchmarks/online_flow.py`
- Modify: `backend/benchmarks/runner.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Consumes: `BenchmarkCase`, `Scenario`, `DispatchOptions`, session runtime mutation functions and `ReplanObservation`.
- Produces: `OnlineFlowExecution` and `execute_online_flow(case, scenario, options) -> OnlineFlowExecution`; `runner.py` maps it into flat `BenchmarkRun` fields.

- [ ] **Step 1: Write failing online-flow tests**

Add a bottleneck test asserting:

```python
execution = execute_online_flow(
    case,
    build_benchmark_scenario(case.case_id),
    benchmark_options(),
)
assert execution.session.currentTime == 120
assert execution.observations
assert execution.runtime_mutation_count == 0
```

Add an online-pressure test asserting:

```python
assert execution.session.currentTime == 20
assert execution.session.runtimeTaskCount == 2
assert execution.runtime_mutation_count == 6
assert {"RUNTIME-SEED-17", "G-SEED-17"} <= {
    task.id for task in execution.session.result.tasks
}
assert execution.observations
assert execution.session.result.metrics.failureCount == 0
assert execution.session.metricsHistory[-1].activeConflictCount == 0
```

Capture the local registry before/after or expose a test-only injected registry argument so the test proves the session is deleted in success and exception paths.

- [ ] **Step 2: Run the new tests and confirm import/function failures**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

- [ ] **Step 3: Implement `OnlineFlowExecution` and the shared tick helper**

Create:

```python
@dataclass(frozen=True, slots=True)
class OnlineFlowExecution:
    session: SessionResult
    safety_intervention_count: int
    observations: tuple[ReplanObservation, ...]
    runtime_mutation_count: int
```

Use `SessionRegistry(max_sessions=1)`, create with `enforce_execution_safety=True` and `replan_observer=observations.append`, advance one tick at a time, count non-null `safetyIntervention`, and always call `delete_session(..., registry=registry)` in `finally`.

- [ ] **Step 4: Implement the exact online-pressure event sequence**

At T=8 issue the five mutations in this exact order:

```python
session = add_task(
    session_id,
    AddTaskRequest(task=Task(
        id="RUNTIME-SEED-17",
        type="emergency",
        title="固定种子运行时复核",
        priority=5,
        releaseTime=8,
        deadline=28,
        target=(2, 9),
    )),
    registry=registry,
)
session = add_blocked_cell(
    session_id,
    AddBlockRequest(cell=(3, 9), currentTime=8),
    registry=registry,
)
session = fail_robot(
    session_id,
    FailRobotRequest(robotId="R4", currentTime=8),
    registry=registry,
)
session = restore_robot(
    session_id,
    RestoreRobotRequest(robotId="R4", currentTime=8),
    registry=registry,
)
session = remove_blocked_cell(
    session_id,
    RemoveBlockRequest(cell=(3, 9), currentTime=8),
    registry=registry,
)
```

After each of the five successful T=8 mutation calls, increment a local `runtime_mutation_count` by one. At T=18 add `G-SEED-17` exactly as specified in the design, increment the same counter only after that call succeeds, then advance to T=20. Return the local count (`6` on the complete path). Do not read `SessionResult.runtimeEventCount`: that field is the number of currently active runtime blocks and robot failures, not a cumulative operation count.

- [ ] **Step 5: Aggregate online observations in `runner.py`**

For online cases, replace the local session loop with `execute_online_flow`. Map all observer fields using these rules:

```python
replan_observation_count = len(observations)
path_candidate_count = sum(item.path_candidate_count for item in observations)
selected_path_candidate_index = None
failed_path_candidate_count = sum(item.failed_path_candidate_count for item in observations)
timed_astar_call_count = sum(item.timed_astar_call_count for item in observations)
timed_astar_expanded_state_count = sum(
    item.timed_astar_expanded_state_count for item in observations
)
max_timed_astar_expanded_state_count = max(
    (item.max_timed_astar_expanded_state_count for item in observations),
    default=0,
)
assignment_candidate_expansion_count = sum(
    item.assignment_candidate_expansion_count for item in observations
)
assignment_robot_state_copy_count = sum(
    item.assignment_robot_state_copy_count for item in observations
)
assignment_beam_peak_width = max(
    (item.assignment_beam_peak_width for item in observations),
    default=0,
)
```

Apply the same sum rule to exhausted and fully-reserved reject counts. Set `planningDiagnosticsEvaluated = bool(observations)`; if false, all work fields must be `None` rather than misleading zeros.

For direct runs, populate new fields from one `PlanningDiagnostics`, with `runtimeTaskCount=0`, `runtimeMutationCount=0` and `replanObservationCount=0`.

- [ ] **Step 6: Add runner-level correctness and cleanup regressions**

Assert all four online cases have:

```python
run.outcome == "completed"
run.correctness_stable is True
run.planning_diagnostics_evaluated is True
run.replan_observation_count > 0
run.active_conflict_count == 0
run.failure_count == 0
run.deadline_miss_count == 0
```

For `online-pressure-s17-r4-t17`, additionally assert `runtime_task_count == 2` and `runtime_mutation_count == 6`. Preserve existing collision-history verification.

- [ ] **Step 7: Run all benchmark tests**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py backend/tests/test_benchmark_process_isolation.py backend/tests/test_sessions.py -q
```

- [ ] **Step 8: Commit online performance evidence support**

```powershell
git add backend/benchmarks/online_flow.py backend/benchmarks/runner.py backend/tests/test_algorithm_benchmark.py
git commit -m "feat: profile online benchmark replans"
```

---

### Task 4: Produce and validate the pre-optimization 65-run baseline

**Files:**
- Generated only: `output/scale-congestion-performance/baseline/<UTC>/results.json`
- Generated only: `output/scale-congestion-performance/baseline/<UTC>/runs.csv`
- Generated only: `output/scale-congestion-performance/baseline/<UTC>/case-summaries.csv`

**Interfaces:**
- Consumes: version-3 thirteen-case `benchmark:algorithm` command before Task 5 optimization.
- Produces: immutable local baseline evidence path used by Task 6 comparison; no generated output is staged.

- [ ] **Step 1: Run focused tests before measuring**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm.py backend/tests/test_algorithm_benchmark.py backend/tests/test_sessions.py -q
```

- [ ] **Step 2: Run the complete baseline with no concurrent heavy command**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- --repetitions 5 --timeout-seconds 30 --output-dir output/scale-congestion-performance/baseline
```

Record the absolute directory printed by the command. Do not select a different result directory by modification time and do not delete prior output.

- [ ] **Step 3: Cross-check the three baseline files**

Read `results.json` as UTF-8 and both CSV files as UTF-8. Verify:

```text
schemaVersion = 3
runs = 65
caseSummaries = 13
completed = 65
timeout = 0
error = 0
correctnessStable = 65
```

Recompute per-case run counts and every new median/P95/max field from `runs.csv`; they must equal JSON and `case-summaries.csv`. Confirm successful publication left no `results.partial.json`, `.tmp` or `.backup` file.

- [ ] **Step 4: Record deterministic baseline optimization inputs**

For every run, retain these exact values for post-optimization comparison:

```text
caseId
runIndex
assignedTaskCount
predictedConflictCount
activeConflictCount
deadlineMissCount
failureCount
totalDistance
makespan
assignmentCandidateExpansionCount
assignmentRobotStateCopyCount
```

Confirm at least `scale-r12-t39` and `density-r8-t55` have `assignmentRobotStateCopyCount > assignmentCandidateExpansionCount`; otherwise the planned optimization target is not present and execution must stop for review instead of changing unrelated code.

- [ ] **Step 5: Confirm Git remains clean except intended source commits**

```powershell
git status --short
git check-ignore -v output/scale-congestion-performance/baseline
```

Expected: generated evidence is ignored and no output file is staged. This task creates no commit.

---

### Task 5: Replace full candidate cloning with selected-robot write-on-copy

**Files:**
- Modify: `backend/app/dispatch.py:548-662`
- Modify: `backend/tests/test_algorithm.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Consumes: `clone_assignment_candidate` and Task 1 assignment-work diagnostics.
- Produces: `clone_assignment_candidate(candidate: AssignmentCandidate, robot_index: int) -> AssignmentCandidate`, where only the selected `RobotAssignmentState` is newly allocated.

- [ ] **Step 1: Write failing parent/sibling isolation tests**

Construct a parent with at least two robot states. Branch index `0` and index `1`, mutate each selected child state, and assert:

```python
assert left.robots[0] is not parent.robots[0]
assert left.robots[1] is parent.robots[1]
assert right.robots[0] is parent.robots[0]
assert right.robots[1] is not parent.robots[1]
assert parent.robots[0].tasks == []
assert parent.robots[1].tasks == []
assert left.robots[0].tasks != right.robots[0].tasks
assert left.robots[1].tasks != right.robots[1].tasks
```

Add invalid index tests for `-1` and `len(candidate.robots)`; the helper must raise `IndexError` rather than silently selecting a Python negative index.

- [ ] **Step 2: Change the final diagnostics expectation before implementation**

Update the Task 1 scale test to require:

```python
assert (
    diagnostics.assignment_robot_state_copy_count
    == diagnostics.assignment_candidate_expansion_count
)
```

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm.py backend/tests/test_algorithm_benchmark.py -q
```

Expected: isolation/signature/copy-count tests fail against the full-clone implementation.

- [ ] **Step 3: Implement selected-robot write-on-copy**

Change the helper to this structure:

```python
def clone_assignment_candidate(
    candidate: AssignmentCandidate,
    robot_index: int,
) -> AssignmentCandidate:
    if robot_index < 0 or robot_index >= len(candidate.robots):
        raise IndexError("机器人候选索引越界")
    robots = list(candidate.robots)
    state = candidate.robots[robot_index]
    robots[robot_index] = RobotAssignmentState(
        robot=state.robot,
        cursor=state.cursor,
        battery=state.battery,
        time=state.time,
        distance=state.distance,
        penalty=state.penalty,
        tasks=[*state.tasks],
    )
    return AssignmentCandidate(robots=robots)
```

Call it as `clone_assignment_candidate(candidate, robot_index)`. Change only the diagnostic copy increment from `len(candidate.robots)` to `1`. Do not alter any other assignment decision.

- [ ] **Step 4: Run isolation, assignment, fixed-seed and solvability regressions**

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm.py backend/tests/test_experiments.py backend/tests/test_algorithm_benchmark.py backend/tests/test_solvability_differential.py -q
```

Expected: all pass; every benchmark run which produces assignment candidates has copy count equal to expansion count.

- [ ] **Step 5: Commit the isolated optimization**

```powershell
git add backend/app/dispatch.py backend/tests/test_algorithm.py backend/tests/test_algorithm_benchmark.py
git commit -m "perf: copy only changed assignment state"
```

---

### Task 6: Produce the optimized report and compare it with baseline

**Files:**
- Generated only: `output/scale-congestion-performance/optimized/<UTC>/results.json`
- Generated only: `output/scale-congestion-performance/optimized/<UTC>/runs.csv`
- Generated only: `output/scale-congestion-performance/optimized/<UTC>/case-summaries.csv`

**Interfaces:**
- Consumes: Task 4 baseline report and Task 5 optimized code.
- Produces: a paired 65-run comparison included verbatim in the final handoff; no generated file is committed.

- [ ] **Step 1: Run the complete optimized benchmark under the same conditions**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- --repetitions 5 --timeout-seconds 30 --output-dir output/scale-congestion-performance/optimized
```

Record the printed absolute directory. Do not run baseline and optimized commands concurrently.

- [ ] **Step 2: Apply the same structural cross-check as Task 4**

Require version 3, 65 completed/stable runs, thirteen summaries, zero timeout/error, consistent CSV/JSON statistics, successful transaction cleanup, no residual worker and no residual session.

- [ ] **Step 3: Compare every corresponding run by `(caseId, runIndex)`**

The following values must be identical before and after:

```text
assignedTaskCount
predictedConflictCount
activeConflictCount
deadlineMissCount
failureCount
totalDistance
makespan
assignmentCandidateExpansionCount
```

For every run with `assignmentCandidateExpansionCount > 0`, require:

```text
optimized.assignmentRobotStateCopyCount
    == optimized.assignmentCandidateExpansionCount
optimized.assignmentRobotStateCopyCount
    < baseline.assignmentRobotStateCopyCount
```

If any business value changes, any copy count fails the inequality, or any outcome is non-completed, stop and report the exact case/run; do not average away the difference.

- [ ] **Step 4: Report wall-clock distributions without making them a gate**

For each case, list baseline/optimized median and P95 `wallClockMs` and `replanTimeMs`. State whether each moved up or down, but do not claim portability or failure solely from wall-clock noise.

---

### Task 7: Document the completed boundary and run final verification

**Files:**
- Modify: `docs/algorithm.md`
- Modify: `docs/experiments.md`
- Modify: `docs/testing-guide.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: both exact report directories and their verified comparison.
- Produces: current commands, schema-3 field semantics, thirteen-case matrix, copy-on-write boundary and fresh test snapshot.

- [ ] **Step 1: Update documentation with exact implemented behavior**

Document:

- the five exact family names and thirteen exact case IDs;
- default 65-run count;
- all new run/summary fields and direct-versus-online aggregation semantics;
- the exact online pressure event sequence;
- selected-robot write-on-copy and parent/sibling immutability requirement;
- both absolute local evidence directories and their exact structural counts;
- before/after copy-count comparison, using actual report values rather than estimates;
- wall-clock as same-machine evidence only;
- no API/frontend change and no MAPF/CBS or cross-machine performance claim.

Change the current-plan status in `AGENTS.md` only after both reports and all acceptance gates pass. Mark phase 3 closed while keeping phases 4—7 unchanged.

- [ ] **Step 2: Run documentation and whitespace checks**

```powershell
$placeholderPattern = ('T' + 'BD') + '|' + ('TO' + 'DO') + '|' + ('implement ' + 'later') + '|' + ('fill in ' + 'details')
rg -n --encoding UTF-8 $placeholderPattern docs/superpowers/specs/2026-09-04-scale-congestion-performance-closure-design.md docs/superpowers/plans/2026-09-04-scale-congestion-performance-closure.md docs/algorithm.md docs/experiments.md docs/testing-guide.md AGENTS.md
git diff --check
```

Expected: no placeholder hits in the two new planning documents and no whitespace errors. Existing unrelated placeholder text, if any, must be inspected rather than blindly deleted.

- [ ] **Step 3: Run the complete repository verification**

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Read the full output and record the actual frontend and backend test counts. A historical count is not sufficient.

- [ ] **Step 4: Commit documentation only after fresh verification**

```powershell
git add AGENTS.md docs/algorithm.md docs/experiments.md docs/testing-guide.md
git commit -m "docs: close scale congestion performance study"
```

- [ ] **Step 5: Final branch audit and handoff**

Run:

```powershell
git status --short --branch
git log --oneline main..HEAD
git diff --check main...HEAD
```

Final report must include:

- branch name and commit list;
- files changed;
- actual focused and full test counts;
- baseline and optimized report directories;
- 65-run structural and correctness counts for both;
- exact deterministic copy-count reduction;
- same-machine timing comparison without overclaiming;
- any timeout/error/unexpected result;
- confirmation that no business API or frontend changed.

Stop on `codex/scale-congestion-performance-closure`. Do not merge, push, delete the branch, delete either report, or begin phase 4 without a new explicit user request.
