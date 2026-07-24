# 时空 A* 目标预留剪枝与性能诊断 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为当前优先级时空 A* 增加可选的确定性工作量诊断，并在目标格于整个搜索时域均被顶点预留时零状态展开返回，从而消除 density 31/43 案例中的病态失败搜索。

**Architecture:** 新建不依赖 API 模型的 `planning_diagnostics.py`，通过显式可选参数从 `run_dispatch` 传递到每个路径候选和 `astar_timed`。先只记录候选与状态扩展数据并运行优化前基线，再增加目标时间槽预检；离线直接规划基准把汇总诊断写入 schema v2 JSON/CSV，在线会话和所有 API 响应保持不变。

**Tech Stack:** Python 3.13、dataclasses、FastAPI/Pydantic 现有调度模型、pytest、现有 multiprocessing 离线基准、PowerShell 7、UTF-8/UTF-8 with BOM。

## Global Constraints

- 所有源码、测试和 Markdown 使用 UTF-8；代码注释使用中文。
- 不改变 `DispatchResult`、`SessionResult`、Pydantic 模型、OpenAPI、前端 TypeScript 类型或任何 API 契约。
- 不修改 beam width、任务排序、机器人规划顺序、候选评分、自适应窗口、充电、任务锁、抢占或恢复语义。
- 不增加墙钟超时、线程中断或任意 A* 扩展节点上限。
- 不实现 CBS、ECBS 或其他完整 MAPF 求解器。
- 诊断对象必须通过显式可选参数传递；禁止模块级可变全局、`ContextVar` 和中文日志解析。
- pytest 不断言真实毫秒上限；性能比例只在同机、无并发重型任务的优化前后基准中人工验收。
- 生成的 `output/` 目录不得暂存或提交。
- 日常完整检查仍使用 `npm run check`；重型 45 次基准不加入该命令。

---

## File Structure

- Create `backend/app/planning_diagnostics.py`
  - 只定义时空 A*、路径候选和一次调度的内部可变诊断 dataclass。
  - 提供候选创建、调用完成和确定性汇总属性。
- Modify `backend/app/dispatch.py`
  - 记录 `astar_timed` 调用结果和扩展状态。
  - 显式传递候选诊断。
  - 在最终任务中增加目标时间槽完全预留剪枝。
- Modify `backend/benchmarks/results.py`
  - 将扁平诊断字段加入 `BenchmarkRun` 和案例汇总。
  - 将 `schemaVersion` 升为 `2`。
- Modify `backend/benchmarks/runner.py`
  - 仅在直接规划案例中创建并映射 `PlanningDiagnostics`。
- Modify `backend/benchmarks/reporting.py`
  - 将新字段加入两份 CSV 的精确表头。
- Modify `backend/tests/test_algorithm.py`
  - 覆盖时空 A* 调用级诊断和目标预留剪枝边界。
- Modify `backend/tests/test_algorithm_benchmark.py`
  - 覆盖候选级诊断、density 结果、schema v2、失败记录和 CSV。
- Modify `docs/algorithm.md`
  - 记录确定性目标预留剪枝及其边界。
- Modify `docs/experiments.md`
  - 记录 schema v2 字段与性能解释。
- Modify `docs/testing-guide.md`
  - 记录优化前后 density 对照和完整基准复核命令。
- Modify `AGENTS.md`
  - 记录性能根因、最终证据和下一阶段选择边界。

---

### Task 1: 单次时空 A* 诊断模型

**Files:**
- Create: `backend/app/planning_diagnostics.py`
- Modify: `backend/app/dispatch.py:3-7`
- Modify: `backend/app/dispatch.py:199-250`
- Test: `backend/tests/test_algorithm.py:1-66`

**Interfaces:**
- Produces:
  - `TimedAStarOutcome = Literal["success", "invalidEndpoint", "goalFullyReserved", "exhausted"]`
  - `TimedAStarCallDiagnostics.finish(outcome: TimedAStarOutcome, expanded_state_count: int) -> None`
  - `PathCandidateDiagnostics.start_timed_astar_call() -> TimedAStarCallDiagnostics`
  - `astar_timed(scenario: Scenario, start: Cell, goal: Cell, start_time: int, reservations: Reservations, extra_blocked: list[Cell] | None = None, move_ticks: int = 1, candidate_diagnostics: PathCandidateDiagnostics | None = None) -> list[Cell]`
- Consumes: existing `Scenario`、`Cell`、`Reservations` and `astar_timed` control flow.

- [ ] **Step 1: Write failing success/invalid/exhausted diagnostics tests**

Add the import:

```python
from backend.app.planning_diagnostics import PathCandidateDiagnostics
```

Add these tests after `test_timed_path_expands_each_move_by_robot_duration`:

```python
def _timed_diagnostics_scenario() -> Scenario:
    return Scenario.model_validate(
        {
            "id": "timed-diagnostics",
            "name": "timed-diagnostics",
            "description": "时空 A* 诊断测试",
            "width": 3,
            "height": 1,
            "obstacles": [],
            "zones": {"warehouse": [], "inspection": [], "delivery": []},
            "robots": [],
            "tasks": [],
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )


def test_timed_astar_diagnostics_records_success_without_changing_path() -> None:
    scenario = _timed_diagnostics_scenario()
    diagnostics = PathCandidateDiagnostics(robot_order=["R1"])

    path = dispatch_module.astar_timed(
        scenario,
        (0, 0),
        (2, 0),
        0,
        dispatch_module.Reservations(),
        candidate_diagnostics=diagnostics,
    )

    assert path == [(0, 0), (1, 0), (2, 0)]
    assert len(diagnostics.timed_astar_calls) == 1
    call = diagnostics.timed_astar_calls[0]
    assert call.outcome == "success"
    assert call.expanded_state_count == 2


def test_timed_astar_diagnostics_records_invalid_endpoint() -> None:
    scenario = _timed_diagnostics_scenario()
    diagnostics = PathCandidateDiagnostics(robot_order=["R1"])

    assert dispatch_module.astar_timed(
        scenario,
        (-1, 0),
        (2, 0),
        0,
        dispatch_module.Reservations(),
        candidate_diagnostics=diagnostics,
    ) == []

    assert diagnostics.timed_astar_calls[0].outcome == "invalidEndpoint"
    assert diagnostics.timed_astar_calls[0].expanded_state_count == 0


def test_timed_astar_diagnostics_records_exhausted_search() -> None:
    scenario = _timed_diagnostics_scenario()
    reservations = dispatch_module.Reservations()
    max_time = scenario.width * scenario.height * 4
    for time_index in range(2, max_time + 1):
        reservations.vertices.add(f"2,0@{time_index}")
    diagnostics = PathCandidateDiagnostics(robot_order=["R1"])

    assert dispatch_module.astar_timed(
        scenario,
        (0, 0),
        (2, 0),
        0,
        reservations,
        candidate_diagnostics=diagnostics,
    ) == []

    assert diagnostics.timed_astar_calls[0].outcome == "exhausted"
    assert diagnostics.timed_astar_calls[0].expanded_state_count > 0
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  backend/tests/test_algorithm.py::test_timed_astar_diagnostics_records_success_without_changing_path `
  backend/tests/test_algorithm.py::test_timed_astar_diagnostics_records_invalid_endpoint `
  backend/tests/test_algorithm.py::test_timed_astar_diagnostics_records_exhausted_search -q
```

Expected: collection fails because `backend.app.planning_diagnostics` does not exist.

- [ ] **Step 3: Create the diagnostics dataclasses**

Create `backend/app/planning_diagnostics.py`:

```python
from dataclasses import dataclass, field
from typing import Literal


TimedAStarOutcome = Literal[
    "success",
    "invalidEndpoint",
    "goalFullyReserved",
    "exhausted",
]


@dataclass(slots=True)
class TimedAStarCallDiagnostics:
    outcome: TimedAStarOutcome | None = None
    expanded_state_count: int = 0

    def finish(
        self,
        outcome: TimedAStarOutcome,
        expanded_state_count: int,
    ) -> None:
        self.outcome = outcome
        self.expanded_state_count = expanded_state_count


@dataclass(slots=True)
class PathCandidateDiagnostics:
    robot_order: list[str]
    timed_astar_calls: list[TimedAStarCallDiagnostics] = field(default_factory=list)
    failure_count: int | None = None
    conflict_count: int | None = None
    deadline_miss_count: int | None = None

    def start_timed_astar_call(self) -> TimedAStarCallDiagnostics:
        diagnostics = TimedAStarCallDiagnostics()
        self.timed_astar_calls.append(diagnostics)
        return diagnostics

    @property
    def timed_astar_call_count(self) -> int:
        return len(self.timed_astar_calls)

    @property
    def timed_astar_expanded_state_count(self) -> int:
        return sum(item.expanded_state_count for item in self.timed_astar_calls)

    @property
    def max_timed_astar_expanded_state_count(self) -> int:
        return max(
            (item.expanded_state_count for item in self.timed_astar_calls),
            default=0,
        )

    @property
    def timed_astar_exhausted_search_count(self) -> int:
        return sum(item.outcome == "exhausted" for item in self.timed_astar_calls)

    @property
    def timed_astar_goal_fully_reserved_reject_count(self) -> int:
        return sum(
            item.outcome == "goalFullyReserved"
            for item in self.timed_astar_calls
        )
```

- [ ] **Step 4: Instrument `astar_timed` without adding pruning**

Import the candidate type in `backend/app/dispatch.py`:

```python
from backend.app.planning_diagnostics import PathCandidateDiagnostics
```

Append the optional parameter:

```python
def astar_timed(
    scenario: Scenario,
    start: Cell,
    goal: Cell,
    start_time: int,
    reservations: Reservations,
    extra_blocked: list[Cell] | None = None,
    move_ticks: int = 1,
    candidate_diagnostics: PathCandidateDiagnostics | None = None,
) -> list[Cell]:
```

At function entry create one call record:

```python
    call_diagnostics = (
        candidate_diagnostics.start_timed_astar_call()
        if candidate_diagnostics is not None
        else None
    )
    expanded_state_count = 0
```

Replace the invalid endpoint return with:

```python
    if not is_walkable(start, scenario, blocked) or not is_walkable(
        goal,
        scenario,
        blocked,
    ):
        if call_diagnostics is not None:
            call_diagnostics.finish("invalidEndpoint", 0)
        return []
```

Immediately before the existing successful return:

```python
        if same_cell(current_cell, goal):
            if call_diagnostics is not None:
                call_diagnostics.finish(
                    "success",
                    expanded_state_count,
                )
            return reconstruct_timed(came_from, current_key)
```

Immediately after `closed.add(current_key)`:

```python
        expanded_state_count += 1
```

Replace the final empty return with:

```python
    if call_diagnostics is not None:
        call_diagnostics.finish("exhausted", expanded_state_count)
    return []
```

Do not add the target-reservation precheck in this task. The exhausted test must remain an actual expanded search so Task 4 has RED evidence.

- [ ] **Step 5: Run focused and nearby algorithm tests**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  backend/tests/test_algorithm.py::test_timed_path_expands_each_move_by_robot_duration `
  backend/tests/test_algorithm.py::test_timed_astar_diagnostics_records_success_without_changing_path `
  backend/tests/test_algorithm.py::test_timed_astar_diagnostics_records_invalid_endpoint `
  backend/tests/test_algorithm.py::test_timed_astar_diagnostics_records_exhausted_search -q
```

Expected: 4 passed.

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm.py -q
```

Expected: all algorithm tests pass.

- [ ] **Step 6: Commit Task 1**

Run:

```powershell
git add backend/app/planning_diagnostics.py backend/app/dispatch.py backend/tests/test_algorithm.py
git diff --cached --check
git commit -m "feat: add timed A-star diagnostics"
```

Expected: one commit containing only the three Task 1 files.

---

### Task 2: 路径候选与调度级诊断

**Files:**
- Modify: `backend/app/planning_diagnostics.py`
- Modify: `backend/app/dispatch.py:589-805`
- Modify: `backend/app/dispatch.py:894-1045`
- Modify: `backend/app/dispatch.py:1800-1889`
- Test: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Consumes:
  - `PathCandidateDiagnostics`
  - Task 1 的 `astar_timed` 最终参数 `candidate_diagnostics: PathCandidateDiagnostics | None`
- Produces:
  - `PlanningDiagnostics.start_path_candidate(robot_order: list[str]) -> PathCandidateDiagnostics`
  - `PlanningDiagnostics.selected_path_candidate_index: int | None`
  - deterministic aggregate properties from the approved design
  - `run_dispatch(scenario: Scenario, options: DispatchOptions, locked_task_robot_ids: dict[str, str] | None = None, preferred_task_robot_ids: dict[str, str] | None = None, include_dynamic_events: bool | None = None, apply_dynamic_constraints_at_start: bool | None = None, task_limit_per_robot: int | None = None, replan_window_decision: ReplanWindowDecision | None = None, active_charging_visits: dict[str, ChargingVisit] | None = None, planning_diagnostics: PlanningDiagnostics | None = None) -> DispatchResult`

- [ ] **Step 1: Write failing aggregate tests**

Add imports to `backend/tests/test_algorithm_benchmark.py`:

```python
from backend.app.dispatch import run_dispatch
from backend.app.planning_diagnostics import PlanningDiagnostics
```

Add:

```python
def test_planning_diagnostics_collect_path_candidates_for_density_case() -> None:
    scenario = build_benchmark_scenario("density-r8-t31")
    diagnostics = PlanningDiagnostics()

    result = run_dispatch(
        scenario,
        benchmark_options(),
        planning_diagnostics=diagnostics,
    )

    assert result.metrics.assignedTaskCount == 31
    assert result.metrics.conflictCount == 0
    assert result.metrics.failureCount == 0
    assert result.metrics.deadlineMissCount == 0
    assert diagnostics.path_candidate_count == 2
    assert diagnostics.selected_path_candidate_index == 1
    assert diagnostics.failed_path_candidate_count == 1
    assert diagnostics.timed_astar_call_count > 0
    assert diagnostics.timed_astar_expanded_state_count > 0
    assert diagnostics.max_timed_astar_expanded_state_count > 0
    assert diagnostics.timed_astar_exhausted_search_count == 1
    assert diagnostics.timed_astar_goal_fully_reserved_reject_count == 0
    assert diagnostics.path_candidates[0].failure_count == 1
    assert diagnostics.path_candidates[1].failure_count == 0


def test_optional_planning_diagnostics_do_not_change_dispatch_result() -> None:
    scenario = build_benchmark_scenario("scale-r4-t15")
    without_diagnostics = run_dispatch(scenario, benchmark_options())
    diagnostics = PlanningDiagnostics()
    with_diagnostics = run_dispatch(
        scenario,
        benchmark_options(),
        planning_diagnostics=diagnostics,
    )

    assert (
        with_diagnostics.model_dump(mode="json")
        == without_diagnostics.model_dump(mode="json")
    )
    assert diagnostics.path_candidate_count >= 1
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  backend/tests/test_algorithm_benchmark.py::test_planning_diagnostics_collect_path_candidates_for_density_case `
  backend/tests/test_algorithm_benchmark.py::test_optional_planning_diagnostics_do_not_change_dispatch_result -q
```

Expected: FAIL because `PlanningDiagnostics` and the `run_dispatch` parameter do not exist.

- [ ] **Step 3: Add `PlanningDiagnostics` and candidate completion**

Append to `PathCandidateDiagnostics`:

```python
    def finish(
        self,
        failure_count: int,
        conflict_count: int,
        deadline_miss_count: int,
    ) -> None:
        self.failure_count = failure_count
        self.conflict_count = conflict_count
        self.deadline_miss_count = deadline_miss_count
```

Append to `backend/app/planning_diagnostics.py`:

```python
@dataclass(slots=True)
class PlanningDiagnostics:
    path_candidates: list[PathCandidateDiagnostics] = field(default_factory=list)
    selected_path_candidate_index: int | None = None

    def start_path_candidate(
        self,
        robot_order: list[str],
    ) -> PathCandidateDiagnostics:
        candidate = PathCandidateDiagnostics(robot_order=list(robot_order))
        self.path_candidates.append(candidate)
        return candidate

    @property
    def path_candidate_count(self) -> int:
        return len(self.path_candidates)

    @property
    def failed_path_candidate_count(self) -> int:
        return sum(
            (item.failure_count or 0) > 0
            for item in self.path_candidates
        )

    @property
    def timed_astar_call_count(self) -> int:
        return sum(item.timed_astar_call_count for item in self.path_candidates)

    @property
    def timed_astar_expanded_state_count(self) -> int:
        return sum(
            item.timed_astar_expanded_state_count
            for item in self.path_candidates
        )

    @property
    def max_timed_astar_expanded_state_count(self) -> int:
        return max(
            (
                item.max_timed_astar_expanded_state_count
                for item in self.path_candidates
            ),
            default=0,
        )

    @property
    def timed_astar_exhausted_search_count(self) -> int:
        return sum(
            item.timed_astar_exhausted_search_count
            for item in self.path_candidates
        )

    @property
    def timed_astar_goal_fully_reserved_reject_count(self) -> int:
        return sum(
            item.timed_astar_goal_fully_reserved_reject_count
            for item in self.path_candidates
        )
```

- [ ] **Step 4: Thread candidate diagnostics through every timed-path helper**

Import `PlanningDiagnostics` beside `PathCandidateDiagnostics`:

```python
from backend.app.planning_diagnostics import (
    PathCandidateDiagnostics,
    PlanningDiagnostics,
)
```

Append `candidate_diagnostics: PathCandidateDiagnostics | None = None` to:

```python
plan_robot_path
append_parking_step
plan_idle_robot_parking_path
build_paths_for_order
```

Pass `candidate_diagnostics=candidate_diagnostics` to every `astar_timed` call in:

```text
plan_robot_path
append_parking_step
plan_idle_robot_parking_path
```

Pass the candidate object from `build_paths_for_order` to all three helpers:

```python
            path = plan_idle_robot_parking_path(
                scenario,
                robot.start,
                robot.moveTicks,
                reservations,
                extra_blocked,
                delayed_blocked,
                horizon_padding,
                candidate_diagnostics,
            )
```

```python
            path, failed = plan_robot_path(
                scenario,
                robot,
                robot.start,
                assigned,
                robot.moveTicks,
                avoid_conflicts,
                reservations,
                extra_blocked,
                delayed_blocked,
                delayed_block_time,
                charging_visits,
                active_charging_visits.get(robot.id),
                candidate_diagnostics,
            )
```

```python
            path = append_parking_step(
                scenario,
                path,
                robot.moveTicks,
                reservations,
                extra_blocked,
                delayed_blocked,
                delayed_block_time,
                horizon_padding,
                candidate_diagnostics,
            )
```

Keep every new parameter at the end so existing positional callers remain valid.

- [ ] **Step 5: Collect and select diagnostics in `build_paths`**

Append:

```python
    planning_diagnostics: PlanningDiagnostics | None = None,
```

to `build_paths`.

Replace the order loop setup with:

```python
    for order in path_planning_orders(
        scenario,
        robots,
        tasks_by_robot,
        locked_task_robot_ids,
        extra_blocked,
    ):
        candidate_index = (
            planning_diagnostics.path_candidate_count
            if planning_diagnostics is not None
            else 0
        )
        candidate_diagnostics = (
            planning_diagnostics.start_path_candidate(
                [robot.id for robot in order]
            )
            if planning_diagnostics is not None
            else None
        )
```

Pass `candidate_diagnostics` as the final `build_paths_for_order` argument.

After calculating `score`, add:

```python
        if candidate_diagnostics is not None:
            candidate_diagnostics.finish(
                failure_count=int(score[0]),
                conflict_count=int(score[1]),
                deadline_miss_count=int(score[2]),
            )
```

Inside the existing best-score branch add:

```python
            if planning_diagnostics is not None:
                planning_diagnostics.selected_path_candidate_index = (
                    candidate_index
                )
```

- [ ] **Step 6: Expose the internal optional parameter from `run_dispatch`**

Append this parameter:

```python
    planning_diagnostics: PlanningDiagnostics | None = None,
```

to `run_dispatch`.

Pass it as the final argument in the existing `build_paths` call:

```python
        active_charging_visits,
        planning_diagnostics,
```

No API, experiment or session caller should pass this parameter.

- [ ] **Step 7: Run focused and dispatch regressions**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  backend/tests/test_algorithm_benchmark.py::test_planning_diagnostics_collect_path_candidates_for_density_case `
  backend/tests/test_algorithm_benchmark.py::test_optional_planning_diagnostics_do_not_change_dispatch_result -q
```

Expected: 2 passed.

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  backend/tests/test_algorithm.py `
  backend/tests/test_sessions.py `
  backend/tests/test_experiments.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 2**

Run:

```powershell
git add backend/app/planning_diagnostics.py backend/app/dispatch.py backend/tests/test_algorithm_benchmark.py
git diff --cached --check
git commit -m "feat: collect path planning diagnostics"
```

Expected: one Task 2 commit; no benchmark output staged.

---

### Task 3: 基准 schema v2 与优化前诊断基线

**Files:**
- Modify: `backend/benchmarks/results.py`
- Modify: `backend/benchmarks/runner.py`
- Modify: `backend/benchmarks/reporting.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Consumes: all aggregate properties on `PlanningDiagnostics`.
- Produces:
  - nine new `BenchmarkRun` fields from the approved spec.
  - three new `BenchmarkCaseSummary` fields.
  - `BenchmarkReport.schema_version == 2`.
  - exact JSON/CSV camelCase fields.

- [ ] **Step 1: Extend the benchmark helper and write failing schema tests**

Add these defaults to `_benchmark_run`:

```python
        "planning_diagnostics_evaluated": True,
        "path_candidate_count": 1,
        "selected_path_candidate_index": 0,
        "failed_path_candidate_count": 0,
        "timed_astar_call_count": 4,
        "timed_astar_expanded_state_count": 20,
        "max_timed_astar_expanded_state_count": 8,
        "timed_astar_exhausted_search_count": 0,
        "timed_astar_goal_fully_reserved_reject_count": 0,
```

Update the timeout expected record with:

```python
        "planningDiagnosticsEvaluated": False,
        "pathCandidateCount": None,
        "selectedPathCandidateIndex": None,
        "failedPathCandidateCount": None,
        "timedAStarCallCount": None,
        "timedAStarExpandedStateCount": None,
        "maxTimedAStarExpandedStateCount": None,
        "timedAStarExhaustedSearchCount": None,
        "timedAStarGoalFullyReservedRejectCount": None,
```

Change both existing schema assertions from `1` to `2`.

Add:

```python
def test_algorithm_benchmark_summary_aggregates_planning_work() -> None:
    summary = summarize_runs(
        [
            _benchmark_run(
                "density-r8-t31",
                1,
                timed_astar_expanded_state_count=10,
                timed_astar_goal_fully_reserved_reject_count=0,
            ),
            _benchmark_run(
                "density-r8-t31",
                2,
                timed_astar_expanded_state_count=20,
                timed_astar_goal_fully_reserved_reject_count=1,
            ),
            _benchmark_run(
                "density-r8-t31",
                3,
                timed_astar_expanded_state_count=30,
                timed_astar_goal_fully_reserved_reject_count=1,
            ),
            _benchmark_run(
                "density-r8-t31",
                4,
                outcome="timeout",
                correctness_stable=False,
                error_type="TimeoutError",
                planning_diagnostics_evaluated=False,
                path_candidate_count=None,
                selected_path_candidate_index=None,
                failed_path_candidate_count=None,
                timed_astar_call_count=None,
                timed_astar_expanded_state_count=None,
                max_timed_astar_expanded_state_count=None,
                timed_astar_exhausted_search_count=None,
                timed_astar_goal_fully_reserved_reject_count=None,
            ),
        ]
    )[0]

    assert summary.median_timed_astar_expanded_state_count == 20
    assert summary.p95_timed_astar_expanded_state_count == 30
    assert summary.max_timed_astar_goal_fully_reserved_reject_count == 1


def test_algorithm_benchmark_summary_uses_null_planning_work_without_diagnostics() -> None:
    summary = summarize_runs(
        [
            _benchmark_run(
                "bottleneck-r4-t4",
                1,
                mode="online",
                planning_diagnostics_evaluated=False,
                path_candidate_count=None,
                selected_path_candidate_index=None,
                failed_path_candidate_count=None,
                timed_astar_call_count=None,
                timed_astar_expanded_state_count=None,
                max_timed_astar_expanded_state_count=None,
                timed_astar_exhausted_search_count=None,
                timed_astar_goal_fully_reserved_reject_count=None,
            )
        ]
    )[0]

    assert summary.median_timed_astar_expanded_state_count is None
    assert summary.p95_timed_astar_expanded_state_count is None
    assert summary.max_timed_astar_goal_fully_reserved_reject_count is None
```

Extend `test_direct_algorithm_benchmark_maps_existing_metrics`:

```python
    assert run.planning_diagnostics_evaluated is True
    assert run.path_candidate_count is not None
    assert run.selected_path_candidate_index is not None
    assert run.timed_astar_call_count is not None
    assert run.timed_astar_expanded_state_count is not None
```

Extend `test_online_algorithm_benchmark_uses_execution_safety`:

```python
    assert run.planning_diagnostics_evaluated is False
    assert run.path_candidate_count is None
    assert run.timed_astar_expanded_state_count is None
```

- [ ] **Step 2: Run schema tests and verify RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  backend/tests/test_algorithm_benchmark.py::test_algorithm_benchmark_statistics_use_completed_runs_only `
  backend/tests/test_algorithm_benchmark.py::test_algorithm_benchmark_failed_run_factories_preserve_case_metadata `
  backend/tests/test_algorithm_benchmark.py::test_algorithm_benchmark_report_serializes_runs_and_summaries `
  backend/tests/test_algorithm_benchmark.py::test_algorithm_benchmark_summary_aggregates_planning_work `
  backend/tests/test_algorithm_benchmark.py::test_algorithm_benchmark_summary_uses_null_planning_work_without_diagnostics `
  backend/tests/test_algorithm_benchmark.py::test_direct_algorithm_benchmark_maps_existing_metrics `
  backend/tests/test_algorithm_benchmark.py::test_online_algorithm_benchmark_uses_execution_safety -q
```

Expected: FAIL because the new dataclass fields and schema v2 do not exist.

- [ ] **Step 3: Add exact fields to `BenchmarkRun`**

Add after `wall_clock_ms`:

```python
    planning_diagnostics_evaluated: bool
    path_candidate_count: int | None
    selected_path_candidate_index: int | None
    failed_path_candidate_count: int | None
    timed_astar_call_count: int | None
    timed_astar_expanded_state_count: int | None
    max_timed_astar_expanded_state_count: int | None
    timed_astar_exhausted_search_count: int | None
    timed_astar_goal_fully_reserved_reject_count: int | None
```

Set the failed factory fields to:

```python
            planning_diagnostics_evaluated=False,
            path_candidate_count=None,
            selected_path_candidate_index=None,
            failed_path_candidate_count=None,
            timed_astar_call_count=None,
            timed_astar_expanded_state_count=None,
            max_timed_astar_expanded_state_count=None,
            timed_astar_exhausted_search_count=None,
            timed_astar_goal_fully_reserved_reject_count=None,
```

Add exact camelCase keys to `to_record`:

```python
            "planningDiagnosticsEvaluated": self.planning_diagnostics_evaluated,
            "pathCandidateCount": self.path_candidate_count,
            "selectedPathCandidateIndex": self.selected_path_candidate_index,
            "failedPathCandidateCount": self.failed_path_candidate_count,
            "timedAStarCallCount": self.timed_astar_call_count,
            "timedAStarExpandedStateCount": self.timed_astar_expanded_state_count,
            "maxTimedAStarExpandedStateCount": self.max_timed_astar_expanded_state_count,
            "timedAStarExhaustedSearchCount": self.timed_astar_exhausted_search_count,
            "timedAStarGoalFullyReservedRejectCount": (
                self.timed_astar_goal_fully_reserved_reject_count
            ),
```

- [ ] **Step 4: Extend case summaries and schema version**

Add to `BenchmarkCaseSummary`:

```python
    median_timed_astar_expanded_state_count: Number | None
    p95_timed_astar_expanded_state_count: Number | None
    max_timed_astar_goal_fully_reserved_reject_count: int | None
```

Add to `to_record`:

```python
            "medianTimedAStarExpandedStateCount": (
                self.median_timed_astar_expanded_state_count
            ),
            "p95TimedAStarExpandedStateCount": (
                self.p95_timed_astar_expanded_state_count
            ),
            "maxTimedAStarGoalFullyReservedRejectCount": (
                self.max_timed_astar_goal_fully_reserved_reject_count
            ),
```

Inside `summarize_runs`, calculate:

```python
        planning_runs = [
            run
            for run in completed
            if run.planning_diagnostics_evaluated
            and run.timed_astar_expanded_state_count is not None
        ]
        expanded_states = [
            run.timed_astar_expanded_state_count
            for run in planning_runs
            if run.timed_astar_expanded_state_count is not None
        ]
        fully_reserved_rejects = [
            run.timed_astar_goal_fully_reserved_reject_count
            for run in planning_runs
            if run.timed_astar_goal_fully_reserved_reject_count is not None
        ]
```

Pass:

```python
                median_timed_astar_expanded_state_count=(
                    median(expanded_states) if expanded_states else None
                ),
                p95_timed_astar_expanded_state_count=(
                    nearest_rank_p95(expanded_states)
                ),
                max_timed_astar_goal_fully_reserved_reject_count=(
                    max(fully_reserved_rejects)
                    if fully_reserved_rejects
                    else None
                ),
```

Change:

```python
        return cls(2, generated_at, config, list(runs), summarize_runs(runs))
```

- [ ] **Step 5: Map direct and online runs**

Import:

```python
from backend.app.planning_diagnostics import PlanningDiagnostics
```

In `_execute_direct` create diagnostics before timing:

```python
    planning_diagnostics = PlanningDiagnostics()
```

Call:

```python
    result = run_dispatch(
        scenario,
        benchmark_options(),
        planning_diagnostics=planning_diagnostics,
    )
```

Pass all direct values:

```python
        planning_diagnostics_evaluated=True,
        path_candidate_count=planning_diagnostics.path_candidate_count,
        selected_path_candidate_index=(
            planning_diagnostics.selected_path_candidate_index
        ),
        failed_path_candidate_count=(
            planning_diagnostics.failed_path_candidate_count
        ),
        timed_astar_call_count=planning_diagnostics.timed_astar_call_count,
        timed_astar_expanded_state_count=(
            planning_diagnostics.timed_astar_expanded_state_count
        ),
        max_timed_astar_expanded_state_count=(
            planning_diagnostics.max_timed_astar_expanded_state_count
        ),
        timed_astar_exhausted_search_count=(
            planning_diagnostics.timed_astar_exhausted_search_count
        ),
        timed_astar_goal_fully_reserved_reject_count=(
            planning_diagnostics.timed_astar_goal_fully_reserved_reject_count
        ),
```

Pass these values in `_execute_online`:

```python
        planning_diagnostics_evaluated=False,
        path_candidate_count=None,
        selected_path_candidate_index=None,
        failed_path_candidate_count=None,
        timed_astar_call_count=None,
        timed_astar_expanded_state_count=None,
        max_timed_astar_expanded_state_count=None,
        timed_astar_exhausted_search_count=None,
        timed_astar_goal_fully_reserved_reject_count=None,
```

- [ ] **Step 6: Extend CSV field names**

Append to `RUN_FIELD_NAMES`:

```python
    "planningDiagnosticsEvaluated",
    "pathCandidateCount",
    "selectedPathCandidateIndex",
    "failedPathCandidateCount",
    "timedAStarCallCount",
    "timedAStarExpandedStateCount",
    "maxTimedAStarExpandedStateCount",
    "timedAStarExhaustedSearchCount",
    "timedAStarGoalFullyReservedRejectCount",
```

Append to `CASE_SUMMARY_FIELD_NAMES`:

```python
    "medianTimedAStarExpandedStateCount",
    "p95TimedAStarExpandedStateCount",
    "maxTimedAStarGoalFullyReservedRejectCount",
```

- [ ] **Step 7: Run all benchmark tests**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: all benchmark tests pass.

- [ ] **Step 8: Commit schema v2**

Run:

```powershell
git add backend/benchmarks/results.py backend/benchmarks/runner.py backend/benchmarks/reporting.py backend/tests/test_algorithm_benchmark.py
git diff --cached --check
git commit -m "feat: report planning work in algorithm benchmark"
```

Expected: one Task 3 commit.

- [ ] **Step 9: Run and preserve the optimization-before density baseline**

Run with no other heavy command in parallel:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- `
  --families density `
  --repetitions 5 `
  --timeout-seconds 30 `
  --output-dir output/timed-astar-density-before
```

Expected:

- exit code `0`;
- 15 completed and 15 stable runs;
- no timeout or error;
- schema version `2`;
- 31/43 runs have `timedAStarExhaustedSearchCount >= 1`;
- 31/43 runs have `timedAStarGoalFullyReservedRejectCount = 0`;
- record the absolute result directory in the task report;
- do not stage the output.

---

### Task 4: 确定性目标预留剪枝

**Files:**
- Modify: `backend/app/dispatch.py:199-250`
- Modify: `backend/tests/test_algorithm.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Consumes:
  - `TimedAStarCallDiagnostics.finish`
  - candidate and benchmark aggregate diagnostics
- Produces:
  - `has_unreserved_goal_arrival_time(goal: Cell, earliest_arrival: int, max_time: int, reservations: Reservations) -> bool`
  - `goalFullyReserved` early return with zero expanded states.

- [ ] **Step 1: Replace the old exhausted expectation and add boundary tests**

Rename the existing Task 1 exhausted test to:

```python
def test_timed_astar_rejects_goal_reserved_for_full_search_horizon() -> None:
```

Keep its setup and change assertions to:

```python
    assert diagnostics.timed_astar_calls[0].outcome == "goalFullyReserved"
    assert diagnostics.timed_astar_calls[0].expanded_state_count == 0
```

This existing test uses the default `move_ticks = 1`, so `latest_goal_arrival == max_time`; its reservation setup already covers the complete valid arrival interval.

Add:

```python
def test_timed_astar_waits_when_goal_becomes_available_later() -> None:
    scenario = _timed_diagnostics_scenario()
    reservations = dispatch_module.Reservations(
        vertices={"2,0@2", "2,0@3"},
    )
    diagnostics = PathCandidateDiagnostics(robot_order=["R1"])

    path = dispatch_module.astar_timed(
        scenario,
        (0, 0),
        (2, 0),
        0,
        reservations,
        candidate_diagnostics=diagnostics,
    )

    assert path[-1] == (2, 0)
    assert len(path) - 1 == 4
    assert diagnostics.timed_astar_calls[0].outcome == "success"


def test_timed_astar_start_equal_goal_keeps_immediate_success() -> None:
    scenario = _timed_diagnostics_scenario()
    reservations = dispatch_module.Reservations(
        vertices={
            f"0,0@{time_index}"
            for time_index in range(0, 13)
        },
    )
    diagnostics = PathCandidateDiagnostics(robot_order=["R1"])

    path = dispatch_module.astar_timed(
        scenario,
        (0, 0),
        (0, 0),
        0,
        reservations,
        candidate_diagnostics=diagnostics,
    )

    assert path == [(0, 0)]
    assert diagnostics.timed_astar_calls[0].outcome == "success"
    assert diagnostics.timed_astar_calls[0].expanded_state_count == 0


def test_timed_astar_full_goal_reservation_uses_move_ticks_arrival_bound() -> None:
    scenario = _timed_diagnostics_scenario()
    reservations = dispatch_module.Reservations()
    max_time = scenario.width * scenario.height * 4 * 3
    latest_goal_arrival = max_time + 3 - 1
    for time_index in range(6, latest_goal_arrival + 1):
        reservations.vertices.add(f"2,0@{time_index}")
    diagnostics = PathCandidateDiagnostics(robot_order=["R1"])

    assert dispatch_module.astar_timed(
        scenario,
        (0, 0),
        (2, 0),
        0,
        reservations,
        move_ticks=3,
        candidate_diagnostics=diagnostics,
    ) == []

    assert diagnostics.timed_astar_calls[0].outcome == "goalFullyReserved"
    assert diagnostics.timed_astar_calls[0].expanded_state_count == 0


def test_timed_astar_goal_reserved_through_search_horizon_keeps_late_slow_arrival() -> None:
    scenario = _timed_diagnostics_scenario()
    reservations = dispatch_module.Reservations()
    max_time = scenario.width * scenario.height * 4 * 3
    latest_goal_arrival = max_time + 3 - 1
    for time_index in range(6, max_time + 1):
        reservations.vertices.add(f"2,0@{time_index}")
    diagnostics = PathCandidateDiagnostics(robot_order=["R1"])

    path = dispatch_module.astar_timed(
        scenario,
        (0, 0),
        (2, 0),
        0,
        reservations,
        move_ticks=3,
        candidate_diagnostics=diagnostics,
    )

    assert path[-1] == (2, 0)
    assert max_time < len(path) - 1 <= latest_goal_arrival
    assert diagnostics.timed_astar_calls[0].outcome == "success"
```

Update the Task 2 density test:

```python
    assert diagnostics.timed_astar_exhausted_search_count == 0
    assert diagnostics.timed_astar_goal_fully_reserved_reject_count == 1
```

Add parameterized density assertions:

```python
@pytest.mark.parametrize(
    ("case_id", "task_count", "candidate_count", "selected_index", "reject_count"),
    [
        ("density-r8-t31", 31, 2, 1, 1),
        ("density-r8-t43", 43, 2, 1, 1),
        ("density-r8-t55", 55, 1, 0, 0),
    ],
)
def test_density_planning_pruning_preserves_results(
    case_id: str,
    task_count: int,
    candidate_count: int,
    selected_index: int,
    reject_count: int,
) -> None:
    diagnostics = PlanningDiagnostics()
    result = run_dispatch(
        build_benchmark_scenario(case_id),
        benchmark_options(),
        planning_diagnostics=diagnostics,
    )

    assert result.metrics.assignedTaskCount == task_count
    assert result.metrics.conflictCount == 0
    assert result.metrics.failureCount == 0
    assert result.metrics.deadlineMissCount == 0
    assert diagnostics.path_candidate_count == candidate_count
    assert diagnostics.selected_path_candidate_index == selected_index
    assert (
        diagnostics.timed_astar_goal_fully_reserved_reject_count
        == reject_count
    )
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  backend/tests/test_algorithm.py::test_timed_astar_rejects_goal_reserved_for_full_search_horizon `
  backend/tests/test_algorithm.py::test_timed_astar_waits_when_goal_becomes_available_later `
  backend/tests/test_algorithm.py::test_timed_astar_start_equal_goal_keeps_immediate_success `
  backend/tests/test_algorithm.py::test_timed_astar_full_goal_reservation_uses_move_ticks_arrival_bound `
  backend/tests/test_algorithm.py::test_timed_astar_goal_reserved_through_search_horizon_keeps_late_slow_arrival `
  backend/tests/test_algorithm_benchmark.py::test_planning_diagnostics_collect_path_candidates_for_density_case `
  backend/tests/test_algorithm_benchmark.py::test_density_planning_pruning_preserves_results -q
```

Expected before pruning: the full-reservation tests fail because the call is still recorded as `exhausted` with expanded states, while the late slow-arrival regression passes and preserves the old goal-before-horizon behavior. A precheck that stops at `max_time` would instead make the late slow-arrival regression fail.

- [ ] **Step 3: Add the deterministic availability helper**

Add immediately before `astar_timed`:

```python
def has_unreserved_goal_arrival_time(
    goal: Cell,
    earliest_arrival: int,
    max_time: int,
    reservations: Reservations,
) -> bool:
    return any(
        f"{cell_key(goal)}@{time_index}" not in reservations.vertices
        for time_index in range(earliest_arrival, max_time + 1)
    )
```

The helper parameter name remains the final source identifier `max_time`; the caller passes `latest_goal_arrival`, not the search-expansion boundary.

- [ ] **Step 4: Add the early rejection after endpoint validation**

Keep invalid endpoint validation first. Then calculate:

```python
    max_time = (
        start_time
        + scenario.width * scenario.height * 4 * move_ticks
    )
    if not same_cell(start, goal):
        earliest_arrival = (
            start_time + manhattan(start, goal) * move_ticks
        )
        latest_goal_arrival = max_time + move_ticks - 1
        if not has_unreserved_goal_arrival_time(
            goal,
            earliest_arrival,
            latest_goal_arrival,
            reservations,
        ):
            if call_diagnostics is not None:
                call_diagnostics.finish("goalFullyReserved", 0)
            return []
```

Remove the old duplicate `max_time` assignment below this insertion. Keep invalid endpoint handling before the precheck, skip the precheck for `start == goal`, and keep the existing heap search and goal-before-horizon order unchanged.

- [ ] **Step 5: Run focused and complete backend tests**

Run the exact RED command again.

Expected: all selected tests pass.

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests -q
```

Expected: all backend tests pass.

- [ ] **Step 6: Run the optimized density benchmark**

Run with no heavy command in parallel:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- `
  --families density `
  --repetitions 5 `
  --timeout-seconds 30 `
  --output-dir output/timed-astar-density-after
```

Expected:

- 15 completed and 15 stable runs;
- 0 timeout and 0 error;
- 31/43 summaries have nonzero `maxTimedAStarGoalFullyReservedRejectCount`;
- 31/43 no longer contain an exhausted search for the fully reserved goal;
- compare with Task 3 output on the same machine;
- each 31/43 `medianReplanTimeMs` is at most 50% of its Task 3 baseline.

If the correctness conditions or the 50% manual target fail, stop and report the exact before/after JSON fields. Do not loosen the target or add an arbitrary search limit.

- [ ] **Step 7: Commit Task 4**

Run:

```powershell
git add backend/app/dispatch.py backend/tests/test_algorithm.py backend/tests/test_algorithm_benchmark.py
git diff --cached --check
git commit -m "perf: prune fully reserved timed goals"
```

Expected: one Task 4 commit; both before/after output directories remain untracked.

---

### Task 5: 文档、全量验证与最终边界基准

**Files:**
- Modify: `docs/algorithm.md`
- Modify: `docs/experiments.md`
- Modify: `docs/testing-guide.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: final schema v2 field names and actual Task 3/4 benchmark results.
- Produces: exact operator guidance and current roadmap evidence.

- [ ] **Step 1: Update algorithm boundaries**

Add to `docs/algorithm.md` under the current boundary section:

```markdown
- 时空 A* 在搜索前检查目标格从曼哈顿最早到达时刻到旧搜索允许的真实最晚目标到达时刻（`max_time + move_ticks - 1`）的顶点预留；仅当该闭区间每个目标时间槽都已被预留时，才以 `goalFullyReserved` 零状态展开返回并尝试下一个机器人规划顺序。
- 该剪枝只证明当前预留表和当前搜索时域下不存在目标到达时间槽；它不证明任意边预留、动态障碍或 MAPF 输入全局无解。
```

Use the actual optimized density median/P95 values in the performance paragraph. Do not copy the design-time expectation if final results differ.

- [ ] **Step 2: Document schema v2**

Add to `docs/experiments.md`:

```markdown
算法边界报告 `schemaVersion = 2`。直接规划运行设置 `planningDiagnosticsEvaluated = true`，并记录：

- `pathCandidateCount`
- `selectedPathCandidateIndex`
- `failedPathCandidateCount`
- `timedAStarCallCount`
- `timedAStarExpandedStateCount`
- `maxTimedAStarExpandedStateCount`
- `timedAStarExhaustedSearchCount`
- `timedAStarGoalFullyReservedRejectCount`

在线、超时和异常记录不伪造规划工作量；未评价时 `planningDiagnosticsEvaluated = false`，其余字段为 `null`。扩展状态数是确定性工作量指标，墙钟中位数和 P95 仍只用于同机人工比较。
```

- [ ] **Step 3: Add the exact manual verification workflow**

Add to `docs/testing-guide.md`:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- --families density --repetitions 5 --timeout-seconds 30 --output-dir output/timed-astar-density
```

Document these checks:

```markdown
1. density 三个案例共 15 条运行并全部 stable。
2. 31/43 案例出现 `goalFullyReserved` 拒绝且不再以 exhausted 搜索遍历完整时域。
3. 55 案例保持首个候选成功。
4. 只在相同机器和无并发重型任务下比较前后 `medianReplanTimeMs`、P95 和扩展状态数。
5. 日常 pytest 不断言真实墙钟阈值。
```

- [ ] **Step 4: Update the roadmap with actual evidence**

In `AGENTS.md`, replace the stale “next, run and manually review” wording with:

```markdown
- The density benchmark performance anomaly has been attributed to a timed A* candidate whose goal remained vertex-reserved for the complete search horizon. The planner now rejects that candidate before state expansion, records deterministic planning-work diagnostics in benchmark schema v2, and preserves the existing candidate order and result semantics.
```

Add the actual optimized density results and state the next choice without claiming complete MAPF capability.

- [ ] **Step 5: Run focused tests and full project verification**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest `
  backend/tests/test_algorithm.py `
  backend/tests/test_algorithm_benchmark.py -q
```

Expected: all selected tests pass.

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Expected:

- frontend production build passes;
- frontend 104/104 tests pass unless the repository has intentionally added new frontend tests;
- all backend tests pass;
- exit code `0`.

- [ ] **Step 6: Run the final complete boundary benchmark**

Run with no other heavy command in parallel:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- `
  --repetitions 5 `
  --timeout-seconds 30 `
  --output-dir output/algorithm-boundary-benchmark
```

Expected:

- 45 run records and 9 case summaries;
- 45 completed and 45 stable;
- 0 timeout and 0 error;
- direct cases retain full assignment, zero predicted conflicts, zero failures and zero deadline misses;
- online cases retain `executionSafetyEvaluated = true`, zero active conflicts and complete safety fields;
- `results.partial.json` is removed;
- `results.json` has `schemaVersion = 2`;
- both CSV files use UTF-8 with BOM.

- [ ] **Step 7: Cross-check JSON and CSV with PowerShell 7**

Use the exact result directory printed by Step 6:

```powershell
& 'D:\codex\summer\.tools\powershell\pwsh.exe' -NoLogo -NoProfile -Command {
  $ResultDir = (
    Get-ChildItem -LiteralPath 'output/algorithm-boundary-benchmark' -Directory |
      Sort-Object LastWriteTimeUtc -Descending |
      Select-Object -First 1
  ).FullName
  if (-not $ResultDir) { throw '未找到最终基准结果目录' }
  $json = Get-Content -LiteralPath (Join-Path $ResultDir 'results.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  $runs = Import-Csv -LiteralPath (Join-Path $ResultDir 'runs.csv') -Encoding utf8BOM
  $summaries = Import-Csv -LiteralPath (Join-Path $ResultDir 'case-summaries.csv') -Encoding utf8BOM
  if ($json.schemaVersion -ne 2) { throw 'schemaVersion 不为 2' }
  if ($json.runs.Count -ne 45 -or $runs.Count -ne 45) { throw '运行数量不一致' }
  if ($json.caseSummaries.Count -ne 9 -or $summaries.Count -ne 9) { throw '汇总数量不一致' }
  if (($json.runs | Where-Object outcome -ne 'completed').Count -ne 0) { throw '存在非 completed 运行' }
  if (($json.runs | Where-Object correctnessStable -ne $true).Count -ne 0) { throw '存在不稳定运行' }
  if (($json.runs | Where-Object { $_.mode -eq 'direct' -and $_.planningDiagnosticsEvaluated -ne $true }).Count -ne 0) { throw '直接规划缺少诊断' }
  if (($json.runs | Where-Object { $_.mode -eq 'online' -and $_.planningDiagnosticsEvaluated -ne $false }).Count -ne 0) { throw '在线规划诊断语义错误' }
  [pscustomobject]@{
    SchemaVersion = $json.schemaVersion
    JsonRuns = $json.runs.Count
    CsvRuns = $runs.Count
    JsonSummaries = $json.caseSummaries.Count
    CsvSummaries = $summaries.Count
  }
}
```

Expected: all five reported counts match and no exception is thrown. Confirm that the selected `$ResultDir` equals the absolute directory printed by Step 6 before accepting the result.

- [ ] **Step 8: Check encoding, scope and repository cleanliness**

Run:

```powershell
git diff --check
git status --short
git diff --name-only ab05bf5..HEAD
```

Expected:

- no whitespace errors;
- only planned source, test and documentation files are tracked changes;
- `output/` remains untracked;
- `.superpowers/` remains untracked if present.

- [ ] **Step 9: Commit documentation**

Run:

```powershell
git add AGENTS.md docs/algorithm.md docs/experiments.md docs/testing-guide.md
git diff --cached --check
git commit -m "docs: record timed A-star performance evidence"
```

Expected: one documentation-only commit.

- [ ] **Step 10: Final verification after the documentation commit**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run check
git status --short --branch
```

Expected:

- frontend build and all tests pass;
- branch contains only the approved commits after `ab05bf5`;
- no tracked changes remain;
- only pre-existing/generated ignored or untracked output directories remain.
