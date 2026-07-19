# 算法边界基准与性能画像 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立不进入日常重型检查的离线算法边界基准，用三组确定性场景重复测量正确性、安全介入和耗时分布，并输出可人工复核的 JSON/CSV。

**Architecture:** `backend/benchmarks/scenarios.py` 只构造确定性 `Scenario`；`results.py` 定义内部结果结构和统计；`runner.py` 执行直接规划、普通在线安全会话和子进程隔离；`reporting.py` 负责 UTF-8 JSON/CSV；`algorithm_boundary.py` 只处理参数、批次编排和退出码。现有 API、前端和 `backend.app.schemas` 不增加字段。

**Tech Stack:** Python 3.11+、Pydantic 现有 `Scenario`/`DispatchOptions`、标准库 `argparse`/`csv`/`json`/`multiprocessing`/`statistics`/`time`、pytest、PowerShell/npm。

## Global Constraints

- 不修改前端，不新增或改变六个实验 API、会话 API、`POST /api/dispatch` 的契约。
- 不实现 CBS、ECBS 或其他完整 MAPF 求解器，不调整现有规划算法参数。
- 基准统一使用 `DispatchOptions(avoidConflicts=True, includeDynamic=True, assignmentReplanWindow=120, adaptiveReplanWindow=False)`。
- 案例目录固定为九例：`scale-r4-t15`、`scale-r8-t27`、`scale-r12-t39`、`density-r8-t31`、`density-r8-t43`、`density-r8-t55`、`bottleneck-r4-t4`、`bottleneck-r6-t6`、`bottleneck-r8-t8`。
- 公开参数只允许 `--families`、`--repetitions`、`--timeout-seconds`、`--output-dir`；默认值分别为三组、`5`、`30`、`output/algorithm-boundary-benchmark`。
- 重型基准不进入 `npm run check`；pytest 不断言真实机器上的墙钟性能上限。
- 源码与 Markdown 使用 UTF-8；生成 JSON 使用 UTF-8，CSV 使用 `utf-8-sig`。
- `output/` 中生成结果不得暂存或提交。
- 代码注释使用中文；精确复用现有字段名，不解析中文事件日志推导安全介入。

---

## File Structure

- Create `backend/benchmarks/__init__.py`: 声明离线基准包，不导出 API 路由。
- Create `backend/benchmarks/scenarios.py`: 案例元数据、统一选项和三组场景构造。
- Create `backend/benchmarks/results.py`: 逐次结果、汇总结果、百分比和 P95。
- Create `backend/benchmarks/runner.py`: 单次直接/在线运行、结构化异常、spawn 子进程和批次执行。
- Create `backend/benchmarks/reporting.py`: partial/final JSON 与两份 CSV。
- Create `backend/benchmarks/algorithm_boundary.py`: 命令行校验、输出目录和退出码。
- Create `backend/tests/test_algorithm_benchmark.py`: 全部快速确定性回归。
- Modify `package.json`: 增加 `benchmark:algorithm`。
- Modify `docs/experiments.md`: 说明离线基准与实验 API 的边界及字段映射。
- Modify `docs/testing-guide.md`: 增加运行、读取和人工复核步骤。
- Modify `AGENTS.md`: 记录已实现基准和仍未完成的算法升级。

### Task 1: 建立确定性场景目录

**Files:**
- Create: `backend/benchmarks/__init__.py`
- Create: `backend/benchmarks/scenarios.py`
- Create: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Consumes: `backend.app.schemas.Scenario`、`backend.app.schemas.DispatchOptions`、`backend.app.seeded_scenarios.seeded_pressure_scenario`。
- Produces: `BenchmarkCase`、`benchmark_options() -> DispatchOptions`、`benchmark_cases(families: tuple[str, ...] | None = None) -> tuple[BenchmarkCase, ...]`、`build_benchmark_scenario(case_id: str) -> Scenario`。

- [ ] **Step 1: 为九个案例目录写失败测试**

```python
from backend.benchmarks.scenarios import benchmark_cases, benchmark_options, build_benchmark_scenario


def test_algorithm_benchmark_catalog_has_exact_cases_and_options() -> None:
    cases = benchmark_cases()
    assert [case.case_id for case in cases] == [
        "scale-r4-t15",
        "scale-r8-t27",
        "scale-r12-t39",
        "density-r8-t31",
        "density-r8-t43",
        "density-r8-t55",
        "bottleneck-r4-t4",
        "bottleneck-r6-t6",
        "bottleneck-r8-t8",
    ]
    assert [(case.family, case.mode) for case in cases] == [
        *(('scale', 'direct') for _ in range(3)),
        *(('density', 'direct') for _ in range(3)),
        *(('bottleneck', 'online') for _ in range(3)),
    ]
    assert benchmark_options().model_dump(mode="json") == {
        "avoidConflicts": True,
        "includeDynamic": True,
        "assignmentReplanWindow": 120,
        "adaptiveReplanWindow": False,
    }


def test_algorithm_benchmark_scenarios_match_catalog_and_are_deterministic() -> None:
    for case in benchmark_cases():
        first = build_benchmark_scenario(case.case_id)
        second = build_benchmark_scenario(case.case_id)
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
        assert len(first.robots) == case.robot_count
        assert len(first.tasks) + len(first.dynamic.tasks) == case.task_count
        assert len(first.dynamic.tasks) == case.dynamic_task_count
        assert len({robot.id for robot in first.robots}) == len(first.robots)
        assert len({robot.start for robot in first.robots}) == len(first.robots)
        all_tasks = [*first.tasks, *first.dynamic.tasks]
        assert len({task.id for task in all_tasks}) == len(all_tasks)
```

- [ ] **Step 2: 运行测试并确认模块尚不存在**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'backend.benchmarks'`。

- [ ] **Step 3: 实现案例元数据、选项和场景构造**

`backend/benchmarks/scenarios.py` 的公开结构固定为：

```python
from dataclasses import dataclass
from typing import Literal

from backend.app.schemas import DispatchOptions, Scenario
from backend.app.seeded_scenarios import seeded_pressure_scenario

BenchmarkFamily = Literal["scale", "density", "bottleneck"]
BenchmarkMode = Literal["direct", "online"]


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    family: BenchmarkFamily
    mode: BenchmarkMode
    seed: int | None
    robot_count: int
    task_count: int
    dynamic_task_count: int
    tick_target: int | None


_CASES = (
    BenchmarkCase("scale-r4-t15", "scale", "direct", None, 4, 15, 3, None),
    BenchmarkCase("scale-r8-t27", "scale", "direct", None, 8, 27, 3, None),
    BenchmarkCase("scale-r12-t39", "scale", "direct", None, 12, 39, 3, None),
    BenchmarkCase("density-r8-t31", "density", "direct", 43, 8, 31, 3, None),
    BenchmarkCase("density-r8-t43", "density", "direct", 43, 8, 43, 3, None),
    BenchmarkCase("density-r8-t55", "density", "direct", 43, 8, 55, 3, None),
    BenchmarkCase("bottleneck-r4-t4", "bottleneck", "online", None, 4, 4, 0, 120),
    BenchmarkCase("bottleneck-r6-t6", "bottleneck", "online", None, 6, 6, 0, 120),
    BenchmarkCase("bottleneck-r8-t8", "bottleneck", "online", None, 8, 8, 0, 120),
)


def benchmark_options() -> DispatchOptions:
    return DispatchOptions(
        avoidConflicts=True,
        includeDynamic=True,
        assignmentReplanWindow=120,
        adaptiveReplanWindow=False,
    )


def benchmark_cases(families: tuple[str, ...] | None = None) -> tuple[BenchmarkCase, ...]:
    if families is None:
        return _CASES
    unknown = sorted(set(families) - {"scale", "density", "bottleneck"})
    if unknown:
        raise ValueError(f"未知基准场景族: {', '.join(unknown)}")
    return tuple(case for case in _CASES if case.family in families)
```

`build_benchmark_scenario()` 必须按 `case_id` 查找 `_CASES`，未知 ID 抛出 `KeyError`。三个构造函数使用以下完整结构：

```python
def _scale_scenario(case: BenchmarkCase) -> Scenario:
    robots = [
        {
            "id": f"R{row + 1}",
            "name": f"R{row + 1}",
            "start": [0, row],
            "battery": 100,
            "load": 2,
        }
        for row in range(case.robot_count)
    ]
    tasks = []
    inspection_cells = []
    for row in range(case.robot_count):
        for column_index, x in enumerate((4, 7, 10), start=1):
            task_id = f"I{row * 3 + column_index}"
            target = [x, row]
            inspection_cells.append(target)
            tasks.append(
                {
                    "id": task_id,
                    "type": "inspection",
                    "title": task_id,
                    "priority": 1 + (row + column_index) % 5,
                    "releaseTime": column_index - 1,
                    "deadline": 120 + row,
                    "targets": [target],
                }
            )
    dynamic_tasks = [
        {
            "id": f"E{row + 1}",
            "type": "emergency",
            "title": f"E{row + 1}",
            "priority": 5,
            "target": [12, row],
        }
        for row in range(3)
    ]
    inspection_cells.extend(task["target"] for task in dynamic_tasks)
    return Scenario.model_validate(
        {
            "id": case.case_id,
            "name": case.case_id,
            "description": "算法机器人规模基准",
            "width": 14,
            "height": case.robot_count,
            "obstacles": [],
            "zones": {
                "warehouse": [robot["start"] for robot in robots],
                "inspection": inspection_cells,
                "delivery": [],
            },
            "robots": robots,
            "tasks": tasks,
            "dynamic": {
                "triggerTime": 8,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": dynamic_tasks,
            },
        }
    )


def _bottleneck_scenario(case: BenchmarkCase) -> Scenario:
    row_order = (1, 7, 2, 6, 3, 5, 0, 8)
    robots = []
    tasks = []
    pickup_cells = []
    dropoff_cells = []
    for index, y in enumerate(row_order[: case.robot_count]):
        starts_left = index % 2 == 0
        start = [0 if starts_left else 12, y]
        pickup = [1 if starts_left else 11, y]
        dropoff = [11 if starts_left else 1, y]
        robot_id = f"R{index + 1}"
        task_id = f"D{index + 1}"
        robots.append({"id": robot_id, "name": robot_id, "start": start, "battery": 100, "load": 2})
        tasks.append(
            {
                "id": task_id,
                "type": "delivery",
                "title": task_id,
                "priority": 2 + index % 4,
                "releaseTime": index % 4,
                "deadline": 200,
                "pickup": pickup,
                "dropoff": dropoff,
                "demand": 1,
            }
        )
        pickup_cells.append(pickup)
        dropoff_cells.append(dropoff)
    return Scenario.model_validate(
        {
            "id": case.case_id,
            "name": case.case_id,
            "description": "算法瓶颈通道基准",
            "width": 13,
            "height": 9,
            "obstacles": [[6, y] for y in range(9) if y not in {3, 4, 5}],
            "zones": {
                "warehouse": pickup_cells,
                "inspection": [],
                "delivery": dropoff_cells,
            },
            "robots": robots,
            "tasks": tasks,
            "dynamic": {
                "triggerTime": 8,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )


def build_benchmark_scenario(case_id: str) -> Scenario:
    case = next((item for item in _CASES if item.case_id == case_id), None)
    if case is None:
        raise KeyError(f"未知基准案例: {case_id}")
    if case.family == "scale":
        return _scale_scenario(case)
    if case.family == "density":
        base_task_count = {
            "density-r8-t31": 28,
            "density-r8-t43": 40,
            "density-r8-t55": 52,
        }[case.case_id]
        return seeded_pressure_scenario(case.case_id, 43, 8, base_task_count)
    return _bottleneck_scenario(case)
```

- [ ] **Step 4: 补充静态可达和瓶颈旁路测试**

```python
from backend.app.dispatch import astar


def test_algorithm_benchmark_targets_are_reachable_and_bottleneck_has_bypass() -> None:
    for case in benchmark_cases():
        scenario = build_benchmark_scenario(case.case_id)
        for task in [*scenario.tasks, *scenario.dynamic.tasks]:
            if task.targets is not None:
                waypoints = task.targets
            elif task.target is not None:
                waypoints = [task.target]
            else:
                assert task.pickup is not None and task.dropoff is not None
                waypoints = [task.pickup, task.dropoff]
                assert astar(scenario, task.pickup, task.dropoff)
            for point in waypoints:
                assert any(astar(scenario, robot.start, point) for robot in scenario.robots)
    for case in benchmark_cases(("bottleneck",)):
        scenario = build_benchmark_scenario(case.case_id)
        open_rows = [y for y in range(scenario.height) if (6, y) not in set(scenario.obstacles)]
        assert open_rows == [3, 4, 5]
```

- [ ] **Step 5: 运行场景测试**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: PASS，当前文件内全部测试通过。

- [ ] **Step 6: 提交场景目录**

```powershell
git add backend/benchmarks/__init__.py backend/benchmarks/scenarios.py backend/tests/test_algorithm_benchmark.py
git commit -m "test: add algorithm benchmark scenarios"
```

### Task 2: 定义结果结构和统计规则

**Files:**
- Create: `backend/benchmarks/results.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Consumes: Task 1 的 `BenchmarkCase`。
- Produces: `BenchmarkRun`、`BenchmarkCaseSummary`、`BenchmarkReport`、`BenchmarkRun.timeout()`、`BenchmarkRun.error()`、`BenchmarkReport.create()`、`percent()`、`nearest_rank_p95()`、`summarize_runs()`。

- [ ] **Step 1: 写百分比、中位数、P95 和空样本失败测试**

```python
from backend.benchmarks.results import BenchmarkRun, nearest_rank_p95, percent, summarize_runs


def _benchmark_run(case_id: str, run_index: int, **updates) -> BenchmarkRun:
    values = {
        "case_id": case_id,
        "family": "scale",
        "mode": "direct",
        "seed": None,
        "run_index": run_index,
        "robot_count": 4,
        "task_count": 15,
        "dynamic_task_count": 3,
        "obstacle_count": 0,
        "tick_target": None,
        "outcome": "completed",
        "error_type": None,
        "error_message": None,
        "correctness_stable": True,
        "released_task_count": None,
        "covered_task_count": None,
        "assigned_task_count": 15,
        "completed_task_count": None,
        "assignment_rate_percent": 100,
        "coverage_rate_percent": None,
        "actual_completion_rate_percent": None,
        "predicted_conflict_count": 0,
        "active_conflict_count": None,
        "execution_safety_evaluated": False,
        "safety_intervention_count": 0,
        "deadline_miss_count": 0,
        "failure_count": 0,
        "total_distance": 10,
        "makespan": 10,
        "replan_time_ms": 4,
        "max_snapshot_replan_time_ms": None,
        "wall_clock_ms": 10,
    }
    values.update(updates)
    return BenchmarkRun(**values)


def test_algorithm_benchmark_statistics_use_completed_runs_only() -> None:
    runs = [
        _benchmark_run("scale-r4-t15", 1, wall_clock_ms=10, replan_time_ms=4),
        _benchmark_run("scale-r4-t15", 2, wall_clock_ms=20, replan_time_ms=8),
        _benchmark_run("scale-r4-t15", 3, wall_clock_ms=30, replan_time_ms=12),
        _benchmark_run(
            "scale-r4-t15",
            4,
            outcome="timeout",
            correctness_stable=False,
            error_type="TimeoutError",
            error_message="运行超时",
            replan_time_ms=None,
            wall_clock_ms=30,
        ),
    ]
    summary = summarize_runs(runs)[0]
    assert percent(1, 3) == 33.3
    assert nearest_rank_p95([10, 20, 30]) == 30
    assert summary.completed_run_count == 3
    assert summary.timeout_count == 1
    assert summary.median_wall_clock_ms == 20
    assert summary.p95_wall_clock_ms == 30
    assert summary.median_replan_time_ms == 8
    assert summary.p95_replan_time_ms == 12


def test_algorithm_benchmark_summary_uses_null_timings_without_completed_runs() -> None:
    summary = summarize_runs([
        _benchmark_run(
            "scale-r4-t15",
            1,
            outcome="timeout",
            correctness_stable=False,
            error_type="TimeoutError",
            error_message="运行超时",
            replan_time_ms=None,
            wall_clock_ms=30,
        )
    ])[0]
    assert summary.median_wall_clock_ms is None
    assert summary.p95_wall_clock_ms is None
    assert summary.median_replan_time_ms is None
    assert summary.p95_replan_time_ms is None
```

- [ ] **Step 2: 运行统计测试并确认失败**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'backend.benchmarks.results'`。

- [ ] **Step 3: 实现内部 dataclass 和统计函数**

`BenchmarkRun` 必须声明设计规格中的全部字段，使用 snake_case Python 属性，并由 `to_record()` 映射为 camelCase 输出字段。`outcome` 使用 `Literal["completed", "timeout", "error"]`；所有不适用数值使用 `None`。测试数据只由测试模块的 `_benchmark_run()` 构造，不向生产 dataclass 添加测试专用方法。

`BenchmarkRun.timeout(case, run_index, wall_clock_ms)` 使用案例元数据填充输入字段，设置 `outcome="timeout"`、`error_type="TimeoutError"`、`correctness_stable=False`，其余调度结果字段为 `None`，`execution_safety_evaluated` 根据 `case.mode == "online"`，`safety_intervention_count=0`。`BenchmarkRun.error(case, run_index, error_type, error_message, wall_clock_ms)` 使用相同规则，但设置 `outcome="error"`。

`BenchmarkReport` 的精确字段为：

```python
@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    schema_version: int
    generated_at: str
    config: dict[str, object]
    runs: list[BenchmarkRun]
    case_summaries: list[BenchmarkCaseSummary]

    @classmethod
    def create(cls, config: dict[str, object], runs: list[BenchmarkRun]) -> "BenchmarkReport":
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return cls(1, generated_at, config, list(runs), summarize_runs(runs))

    def to_record(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "generatedAt": self.generated_at,
            "config": self.config,
            "runs": [run.to_record() for run in self.runs],
            "caseSummaries": [summary.to_record() for summary in self.case_summaries],
        }
```

核心统计实现：

```python
from math import ceil
from statistics import median


def percent(numerator: int | float, denominator: int | float) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0


def nearest_rank_p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[ceil(0.95 * len(ordered)) - 1]


def summarize_runs(runs: list[BenchmarkRun]) -> list[BenchmarkCaseSummary]:
    summaries = []
    for case_id in dict.fromkeys(run.case_id for run in runs):
        case_runs = [run for run in runs if run.case_id == case_id]
        completed = [run for run in case_runs if run.outcome == "completed"]
        wall = [run.wall_clock_ms for run in completed if run.wall_clock_ms is not None]
        replan = [run.replan_time_ms for run in completed if run.replan_time_ms is not None]
        summaries.append(
            BenchmarkCaseSummary(
                case_id=case_id,
                run_count=len(case_runs),
                completed_run_count=len(completed),
                timeout_count=sum(run.outcome == "timeout" for run in case_runs),
                error_count=sum(run.outcome == "error" for run in case_runs),
                stable_run_count=sum(run.correctness_stable for run in case_runs),
                stable_run_rate_percent=percent(sum(run.correctness_stable for run in case_runs), len(case_runs)),
                median_wall_clock_ms=median(wall) if wall else None,
                p95_wall_clock_ms=nearest_rank_p95(wall),
                median_replan_time_ms=median(replan) if replan else None,
                p95_replan_time_ms=nearest_rank_p95(replan),
                max_safety_intervention_count=max((run.safety_intervention_count for run in case_runs), default=0),
            )
        )
    return summaries
```

- [ ] **Step 4: 运行统计测试**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交结果结构**

```powershell
git add backend/benchmarks/results.py backend/tests/test_algorithm_benchmark.py
git commit -m "feat: define algorithm benchmark results"
```

### Task 3: 实现直接规划和在线安全运行

**Files:**
- Create: `backend/benchmarks/runner.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Consumes: `build_benchmark_scenario()`、`benchmark_options()`、`BenchmarkRun`。
- Produces: `execute_benchmark_case(case_id: str, run_index: int) -> BenchmarkRun`。

- [ ] **Step 1: 写直接规划字段映射失败测试**

```python
from backend.benchmarks.runner import execute_benchmark_case


def test_direct_algorithm_benchmark_maps_existing_metrics() -> None:
    run = execute_benchmark_case("scale-r4-t15", 1)
    assert run.outcome == "completed"
    assert run.mode == "direct"
    assert run.execution_safety_evaluated is False
    assert run.safety_intervention_count == 0
    assert run.active_conflict_count is None
    assert run.assigned_task_count == run.task_count
    assert run.assignment_rate_percent == 100
    assert run.coverage_rate_percent is None
    assert run.actual_completion_rate_percent is None
    assert run.predicted_conflict_count is not None
    assert run.wall_clock_ms is not None and run.wall_clock_ms >= 0
```

- [ ] **Step 2: 写在线安全链路失败测试**

```python
from backend.app import sessions as sessions_module


def _assert_history_collision_free(history: dict[str, list[tuple[int, int]]]) -> None:
    robot_ids = sorted(history)
    horizon = max((len(history[robot_id]) for robot_id in robot_ids), default=0)
    for time_index in range(horizon):
        positions = {
            robot_id: history[robot_id][min(time_index, len(history[robot_id]) - 1)]
            for robot_id in robot_ids
        }
        assert len(set(positions.values())) == len(positions)
        if time_index == 0:
            continue
        previous = {
            robot_id: history[robot_id][min(time_index - 1, len(history[robot_id]) - 1)]
            for robot_id in robot_ids
        }
        for first_index, first_id in enumerate(robot_ids):
            for second_id in robot_ids[first_index + 1 :]:
                assert not (
                    previous[first_id] == positions[second_id]
                    and previous[second_id] == positions[first_id]
                )


def test_online_algorithm_benchmark_uses_execution_safety(monkeypatch) -> None:
    real_delete = sessions_module.delete_session
    captured_histories = []

    def capture_delete(session_id: str):
        session = sessions_module._sessions[session_id]
        captured_histories.append({key: list(value) for key, value in session.robot_path_history.items()})
        return real_delete(session_id)

    monkeypatch.setattr("backend.benchmarks.runner.delete_session", capture_delete)
    run = execute_benchmark_case("bottleneck-r4-t4", 1)
    assert run.outcome == "completed"
    assert run.mode == "online"
    assert run.execution_safety_evaluated is True
    assert run.active_conflict_count == 0
    assert captured_histories
    _assert_history_collision_free(captured_histories[0])
```

- [ ] **Step 3: 运行 runner 测试并确认失败**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'backend.benchmarks.runner'`。

- [ ] **Step 4: 实现直接运行映射**

在 `runner.py` 中使用 `time.perf_counter()` 测量单次执行。直接运行调用 `run_dispatch()`；`predicted_conflict_count` 精确读取 `result.metrics.conflictCount`；`correctness_stable` 使用“全任务分配且冲突、超期、失败为零”。直接运行的 `released_task_count`、`covered_task_count`、`completed_task_count`、覆盖率、实际完成率、`active_conflict_count`、`max_snapshot_replan_time_ms` 为 `None`。

- [ ] **Step 5: 实现普通在线安全运行映射**

```python
def _execute_online(case: BenchmarkCase, run_index: int) -> BenchmarkRun:
    scenario = build_benchmark_scenario(case.case_id)
    session_id: str | None = None
    safety_count = 0
    try:
        session = create_session(CreateSessionRequest(scenario=scenario, options=benchmark_options()))
        session_id = session.sessionId
        while session.currentTime < case.tick_target:
            session = tick_session(
                session_id,
                SessionTickRequest(currentTime=session.currentTime + 1),
            )
            if session.safetyIntervention is not None:
                safety_count += 1
    finally:
        if session_id is not None:
            delete_session(session_id)
```

完成映射时：

- `released_task_count` 为 `releaseTime <= currentTime` 的任务状态数量。
- `covered_task_count` 为 `status != "unassigned"` 的任务状态数量。
- `coverage_rate_percent = percent(covered_task_count, task_count)`。
- `actual_completion_rate_percent = percent(completedTaskCount, released_task_count)`。
- `active_conflict_count` 读取最后一个 `MetricSnapshot.activeConflictCount`，没有快照时为 `0`。
- `max_snapshot_replan_time_ms` 为全部快照 `replanTimeMs` 最大值，没有快照时为 `0`。
- `correctness_stable` 忽略预测冲突和安全介入次数，只要求全部任务被覆盖、最终活动冲突/超期/失败均为零。

- [ ] **Step 6: 运行 runner 测试和现有会话安全测试**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py backend/tests/test_sessions.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交运行器**

```powershell
git add backend/benchmarks/runner.py backend/tests/test_algorithm_benchmark.py
git commit -m "feat: run direct and online algorithm benchmarks"
```

### Task 4: 增加 spawn 隔离、超时和异常继续执行

**Files:**
- Modify: `backend/benchmarks/runner.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Consumes: `execute_benchmark_case()`。
- Produces: `run_isolated_case(case: BenchmarkCase, run_index: int, timeout_seconds: float, worker_callable: Callable[[str, int], BenchmarkRun] = execute_benchmark_case) -> BenchmarkRun`、`run_benchmark_cases(cases: tuple[BenchmarkCase, ...], repetitions: int, timeout_seconds: float, on_result: Callable[[list[BenchmarkRun]], None] | None = None) -> list[BenchmarkRun]`。

- [ ] **Step 1: 写真实 spawn 超时和异常记录失败测试**

在测试模块顶层定义可 pickling worker：

```python
import time


def _sleeping_benchmark_worker(case_id: str, run_index: int):
    time.sleep(2)


def _failing_benchmark_worker(case_id: str, run_index: int):
    raise RuntimeError("benchmark worker failed")


def test_isolated_algorithm_benchmark_records_timeout() -> None:
    case = benchmark_cases(("scale",))[0]
    run = run_isolated_case(case, 1, 0.05, worker_callable=_sleeping_benchmark_worker)
    assert run.outcome == "timeout"
    assert run.correctness_stable is False
    assert run.error_type == "TimeoutError"


def test_isolated_algorithm_benchmark_records_worker_error() -> None:
    case = benchmark_cases(("scale",))[0]
    run = run_isolated_case(case, 1, 5, worker_callable=_failing_benchmark_worker)
    assert run.outcome == "error"
    assert run.error_type == "RuntimeError"
    assert run.error_message == "benchmark worker failed"
```

- [ ] **Step 2: 运行隔离测试并确认函数缺失**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: FAIL，包含 `ImportError` 或 `run_isolated_case` 未定义。

- [ ] **Step 3: 实现 guarded worker 和父进程控制**

```python
def _guarded_worker_entry(queue, worker_callable, case_id: str, run_index: int) -> None:
    try:
        queue.put(("completed", worker_callable(case_id, run_index)))
    except Exception as exc:
        queue.put(("error", type(exc).__name__, str(exc), traceback.format_exc()))


def run_isolated_case(case, run_index, timeout_seconds, worker_callable=execute_benchmark_case):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_guarded_worker_entry,
        args=(queue, worker_callable, case.case_id, run_index),
    )
    started = time.perf_counter()
    process.start()
    process.join(timeout_seconds)
    wall_clock_ms = round((time.perf_counter() - started) * 1000, 2)
    if process.is_alive():
        process.terminate()
        process.join()
        return BenchmarkRun.timeout(case, run_index, wall_clock_ms)
    try:
        payload = queue.get(timeout=1)
    except queue_module.Empty:
        payload = None
    if payload is None:
        return BenchmarkRun.error(case, run_index, "ChildProcessError", f"子进程退出码: {process.exitcode}", wall_clock_ms)
    if payload[0] == "error":
        return BenchmarkRun.error(case, run_index, payload[1], payload[2], wall_clock_ms)
    run = payload[1]
    return replace(run, wall_clock_ms=wall_clock_ms)
```

错误文本通过单独的 `_sanitize_error_text()` 移除 `\r`/`\n` 并截断到 `500` 个字符；完整 traceback 写到父进程 stderr。`run_benchmark_cases()` 必须按案例目录顺序和 `runIndex=1..repetitions` 顺序执行，每次追加结果后调用可空 `on_result`，单例错误不抛出。

- [ ] **Step 4: 运行隔离和批次继续测试**

补充一个 `on_result` 测试，确认每完成一次都会收到累积副本，随后运行：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: PASS，超时测试在远小于 2 秒内结束，异常测试继续返回结构化结果。

- [ ] **Step 5: 提交隔离执行**

```powershell
git add backend/benchmarks/runner.py backend/tests/test_algorithm_benchmark.py
git commit -m "feat: isolate algorithm benchmark runs"
```

### Task 5: 实现 JSON/CSV 报告和命令行

**Files:**
- Create: `backend/benchmarks/reporting.py`
- Create: `backend/benchmarks/algorithm_boundary.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`
- Modify: `package.json`

**Interfaces:**
- Consumes: `run_benchmark_cases()`、`summarize_runs()`、`BenchmarkReport.to_record()`。
- Produces: `write_partial_report()`、`write_final_report()`、`parse_args()`、`main(argv: list[str] | None = None) -> int`。

- [ ] **Step 1: 写 JSON/CSV 输出失败测试**

```python
import csv
import json

from backend.benchmarks.reporting import write_final_report
from backend.benchmarks.results import BenchmarkReport


def test_algorithm_benchmark_writes_utf8_json_and_csv(tmp_path) -> None:
    run = execute_benchmark_case("scale-r4-t15", 1)
    report = BenchmarkReport.create(
        config={"families": ["scale"], "repetitions": 1, "timeoutSeconds": 30},
        runs=[run],
    )
    write_final_report(tmp_path, report)
    payload = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["runs"][0]["caseId"] == "scale-r4-t15"
    assert payload["caseSummaries"][0]["runCount"] == 1
    with (tmp_path / "runs.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["caseId"] == "scale-r4-t15"
    assert (tmp_path / "case-summaries.csv").exists()
```

- [ ] **Step 2: 写参数预校验失败测试**

```python
from backend.benchmarks.algorithm_boundary import main


def test_algorithm_benchmark_rejects_invalid_config_before_creating_output(tmp_path) -> None:
    assert main(["--families", "unknown", "--output-dir", str(tmp_path)]) != 0
    assert list(tmp_path.iterdir()) == []
    assert main(["--repetitions", "0", "--output-dir", str(tmp_path)]) != 0
    assert list(tmp_path.iterdir()) == []
    assert main(["--timeout-seconds", "0", "--output-dir", str(tmp_path)]) != 0
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 3: 运行报告/CLI 测试并确认模块缺失**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
```

Expected: FAIL，包含 `backend.benchmarks.reporting` 或 `algorithm_boundary` 未定义。

- [ ] **Step 4: 实现报告写入**

`reporting.py` 必须：

- `results.json` 使用 `encoding="utf-8"`、`ensure_ascii=False`、`indent=2`。
- `runs.csv` 和 `case-summaries.csv` 使用 `encoding="utf-8-sig"`、`newline=""`、固定字段顺序。
- `write_partial_report()` 先写同目录临时文件，再使用 `Path.replace()` 原子更新 `results.partial.json`。
- `write_final_report()` 写三个最终文件后调用 `partial_path.unlink(missing_ok=True)`。
- 写入异常必须包含目标绝对路径重新抛出。

- [ ] **Step 5: 实现 CLI 和时间戳目录**

`parse_args()` 使用 `argparse`，`--families` 按逗号切分并去除空白；`main()` 在任何目录创建前完成未知 family、非正重复次数和非正超时校验。时间戳使用 `datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")`。若同秒目录已存在，则追加 `-2`、`-3`，不得覆盖。

`on_result` 每次构造当前 `BenchmarkReport` 并调用 `write_partial_report()`；批次结束调用 `write_final_report()`，打印结果目录绝对路径并返回 `0`。父进程配置/写入失败打印到 stderr 并返回 `1`。

传入 `BenchmarkReport.create()` 的 `config` 精确包含 `families`、`repetitions`、`timeoutSeconds`、`outputDir` 和 `options`；`outputDir` 为时间戳结果目录的绝对路径，`options` 为 `benchmark_options().model_dump(mode="json")`。

- [ ] **Step 6: 在 package.json 增加精确 npm 脚本**

```json
"benchmark:algorithm": ".\\.venv\\Scripts\\python.exe -m backend.benchmarks.algorithm_boundary"
```

将它放在 `backend:test` 与 `check` 之间，不修改既有脚本。

- [ ] **Step 7: 运行 CLI 测试和一次最小真实命令**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- --families scale --repetitions 1 --timeout-seconds 30 --output-dir output/algorithm-boundary-benchmark-smoke
```

Expected: pytest PASS；命令退出码 `0`，控制台打印绝对结果目录，目录内存在三个最终文件且没有 `results.partial.json`。

- [ ] **Step 8: 提交 CLI 和报告**

```powershell
git add backend/benchmarks/reporting.py backend/benchmarks/algorithm_boundary.py backend/tests/test_algorithm_benchmark.py package.json
git commit -m "feat: add algorithm benchmark reports"
```

### Task 6: 更新文档、运行完整基准并收尾验证

**Files:**
- Modify: `docs/experiments.md`
- Modify: `docs/testing-guide.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: Task 5 的公开命令、字段和结果文件。
- Produces: 可执行的人工取证流程和项目路线图状态说明。

- [ ] **Step 1: 更新实验文档**

在 `docs/experiments.md` 的“后续实验方向”前增加“离线算法边界基准”，准确记录：

- 六个 `/api/experiments/*` 接口保持不变。
- 离线基准命令和九个案例 ID。
- `predictedConflictCount` 对应 `Metrics.conflictCount`，在线 `activeConflictCount` 与 `safetyInterventionCount` 表示实际执行安全证据。
- `timeout`/`error` 是边界结果，不自动判为代码缺陷。
- 报告结论必须人工复核，不把预测零冲突写成完整 MAPF 保证。

- [ ] **Step 2: 更新人工测试指南**

在 `docs/testing-guide.md` 增加标准命令、结果目录、三份最终文件、字段解释和人工复核清单：案例数量、完成/超时/错误数量、稳定运行率、中位数/P95、安全介入次数、未提交输出。

- [ ] **Step 3: 更新 AGENTS.md 路线图**

在算法与实验进度中记录离线基准已经实现，但不要把模块状态改成“完整 MAPF 已完成”。明确下一步是运行并人工复核结果，再按证据选择性能优化、阈值校准或独立 MAPF 评估。

- [ ] **Step 4: 运行新增测试和完整项目检查**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m pytest backend/tests/test_algorithm_benchmark.py -q
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Expected: 新增测试全部 PASS；前端构建、全部前端测试和全部后端测试 PASS。

- [ ] **Step 5: 运行默认完整基准**

Run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:algorithm -- --repetitions 5 --timeout-seconds 30 --output-dir output/algorithm-boundary-benchmark
```

Expected: 命令退出码 `0`；九个案例各有五次记录，总计 `45` 条 `runs`；每个案例有一条 `caseSummaries`。允许具体案例记录 `timeout`、`error` 或 `correctnessStable=false`，但文件结构必须完整。

- [ ] **Step 6: 人工核对输出与 Git 范围**

读取 `results.json`、`runs.csv`、`case-summaries.csv`，确认计数一致、CSV 可用 UTF-8 打开、在线案例包含安全字段。运行：

```powershell
git diff --check
git status --short
```

Expected: `git diff --check` 无输出；`output/` 结果保持未跟踪且不加入暂存区，源码差异只包含计划内文件。

- [ ] **Step 7: 提交文档和收尾状态**

```powershell
git add AGENTS.md docs/experiments.md docs/testing-guide.md
git commit -m "docs: document algorithm benchmark workflow"
```

- [ ] **Step 8: 最终检查提交历史和工作树**

Run:

```powershell
git log --oneline --decorate -8
git status --short --branch
```

Expected: 当前分支包含设计、计划和各任务提交；tracked 工作树无修改，只有未提交的 `output/` 基准证据。
