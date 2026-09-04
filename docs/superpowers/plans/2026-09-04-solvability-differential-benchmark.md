# 小规模可解性参照与差分基准 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成一个离线小规模精确可解性参照，并把它与现有固定分配路径规划器做隔离、可复现的差分比较，产出下一阶段局部联合修复所需的真实反例。

**Architecture:** 新的 `solvability_*` 模块与业务 API、在线会话和前端完全隔离。联合状态 A* 只处理静态、单位速度、每机器人一个唯一目标的小输入；差分适配器将同一输入转换成固定 `Assignment` 并调用现有 `build_paths`，然后通过现有进程隔离设施运行并生成独立 JSON/CSV 报告。

**Tech Stack:** Python 3.11、标准库 `dataclasses` / `heapq` / `itertools` / `random` / `csv` / `json`、现有 Pydantic 模型、pytest、现有 `backend.benchmarks.process_isolation`、npm 根脚本。

**Spec:** `docs/superpowers/specs/2026-09-04-solvability-differential-benchmark-design.md`

## Global Constraints

- 只新增离线算法研究能力；不得修改业务 API、请求模型、响应模型、在线会话行为或前端。
- 第一版只支持静态固定障碍、单位速度、每机器人一个必须访问一次的固定作业目标；完成目标后允许继续移动到最终停车位置。不处理任务分配、连续任务、动态事件、服务、充电、故障或库存。
- 精确参照输入限制为宽高各 `1..5`、总格数不超过 `25`、机器人 `1..4`。
- `unsolved` 只能在开放集耗尽后返回；达到 `max_expanded_states` 必须返回 `limit`。
- 生产比较必须固定 `Assignment`，并使用现有精确调用 `build_paths(scenario, scenario.robots, assignments, True, [], [])`。
- 不替换或修改现有优先级时空 A*；不宣称完整 CBS/MAPF、任意输入零冲突或全规划时域保证。
- 只使用现有依赖；不得增加求解器或其他第三方包。
- 所有新文本文件保持 UTF-8，代码注释使用中文。
- 输出中的 `timeout`、`error`、`oracleLimit` 和反例均必须保留；不得筛掉不利结果。
- 计划实施时从干净基线开始；每个任务完成后只提交该任务列出的文件，不合并、不推送，除非用户另行授权。

---

## 文件职责总览

- Create `backend/benchmarks/solvability_cases.py`：受限案例数据类型、输入不变量、固定目录和确定性生成器。
- Create `backend/benchmarks/solvability_oracle.py`：联合状态 A* 和精确参照结果。
- Create `backend/benchmarks/solvability_results.py`：逐次结果、分类汇总和 `schemaVersion=1` 报告模型。
- Create `backend/benchmarks/solvability_runner.py`：业务场景适配、固定分配比较、隔离单次运行和批量编排。
- Create `backend/benchmarks/solvability_reporting.py`：partial JSON、最终 JSON/CSV 和回滚保护发布。
- Create `backend/benchmarks/solvability_differential.py`：命令行参数、输出目录、运行编排和退出码。
- Create `backend/tests/test_solvability_oracle.py`：案例与精确参照单元测试。
- Create `backend/tests/test_solvability_differential.py`：适配、分类、隔离、报告和 CLI 测试。
- Modify `package.json`：新增 `benchmark:solvability` 根命令。
- Modify `docs/algorithm.md`、`docs/experiments.md`、`docs/testing-guide.md`、`AGENTS.md`：记录能力、使用方法、验证结果和严格边界。

### Task 1: 受限案例模型、目录与固定种子生成器

**Files:**
- Create: `backend/benchmarks/solvability_cases.py`
- Create: `backend/tests/test_solvability_oracle.py`

**Interfaces:**
- Produces: `SolvabilityAgent(agent_id: str, start: Cell, goal: Cell)`
- Produces: `SolvabilityCase(case_id: str, source: Literal["catalog", "generated"], width: int, height: int, obstacles: tuple[Cell, ...], agents: tuple[SolvabilityAgent, ...], expected_oracle_outcome: Literal["solved", "unsolved"] | None)`
- Produces: `solvability_catalog() -> tuple[SolvabilityCase, ...]`
- Produces: `generate_solvability_cases(seed: int, sample_count: int) -> tuple[SolvabilityCase, ...]`
- Produces: `solvability_cases(seed: int, sample_count: int) -> tuple[SolvabilityCase, ...]`

- [ ] **Step 1: 写案例目录和输入不变量的失败测试**

在 `backend/tests/test_solvability_oracle.py` 写入：

```python
import random

import pytest

from backend.benchmarks.solvability_cases import (
    SolvabilityAgent,
    SolvabilityCase,
    generate_solvability_cases,
    solvability_catalog,
    solvability_cases,
)


def test_solvability_catalog_has_exact_ids_and_expected_outcomes() -> None:
    cases = solvability_catalog()

    assert [case.case_id for case in cases] == [
        "catalog-solo-straight",
        "catalog-independent-r2",
        "catalog-side-bypass-swap-r2",
        "catalog-no-bypass-swap-r2",
    ]
    assert [case.expected_oracle_outcome for case in cases] == [
        "solved",
        "solved",
        "solved",
        "unsolved",
    ]


@pytest.mark.parametrize(
    "updates",
    [
        {"width": 0},
        {"width": 6},
        {"height": 6},
        {"obstacles": ((9, 9),)},
        {
            "agents": (
                SolvabilityAgent("R1", (0, 0), (1, 0)),
                SolvabilityAgent("R2", (0, 0), (1, 1)),
            )
        },
        {
            "agents": (
                SolvabilityAgent("R1", (0, 0), (1, 0)),
                SolvabilityAgent("R2", (0, 1), (1, 0)),
            )
        },
    ],
)
def test_solvability_case_rejects_invalid_bounded_inputs(updates) -> None:
    values = {
        "case_id": "invalid",
        "source": "generated",
        "width": 2,
        "height": 2,
        "obstacles": (),
        "agents": (SolvabilityAgent("R1", (0, 0), (1, 1)),),
        "expected_oracle_outcome": None,
    }
    values.update(updates)

    with pytest.raises(ValueError):
        SolvabilityCase(**values)


def test_generated_solvability_cases_are_exact_count_unique_and_deterministic() -> None:
    first = generate_solvability_cases(seed=20260904, sample_count=12)
    second = generate_solvability_cases(seed=20260904, sample_count=12)

    assert first == second
    assert len(first) == 12
    assert len({case.case_id for case in first}) == 12
    assert len({case.case_key for case in first}) == 12
    assert all(case.source == "generated" for case in first)
    assert all(agent.start != agent.goal for case in first for agent in case.agents)


def test_generated_solvability_cases_do_not_change_global_random_state() -> None:
    random.seed(73)
    before = random.getstate()

    generate_solvability_cases(seed=20260904, sample_count=4)

    assert random.getstate() == before


def test_solvability_cases_prepends_catalog_and_accepts_zero_samples() -> None:
    assert solvability_cases(20260904, 0) == solvability_catalog()
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_oracle.py -q
```

Expected: collection FAIL，错误包含 `No module named 'backend.benchmarks.solvability_cases'`。

- [ ] **Step 3: 实现不可变数据类型和精确校验**

在 `backend/benchmarks/solvability_cases.py` 定义常量和数据类型：

```python
from collections import deque
from dataclasses import dataclass
import random
from typing import Literal

from backend.app.schemas import Cell

SolvabilityCaseSource = Literal["catalog", "generated"]
ExpectedOracleOutcome = Literal["solved", "unsolved"]

MAX_SOLVABILITY_AXIS = 5
MAX_SOLVABILITY_CELLS = 25
MAX_SOLVABILITY_ROBOTS = 4
MAX_GENERATION_ATTEMPTS_PER_CASE = 1_000


@dataclass(frozen=True, slots=True)
class SolvabilityAgent:
    agent_id: str
    start: Cell
    goal: Cell


@dataclass(frozen=True, slots=True)
class SolvabilityCase:
    case_id: str
    source: SolvabilityCaseSource
    width: int
    height: int
    obstacles: tuple[Cell, ...]
    agents: tuple[SolvabilityAgent, ...]
    expected_oracle_outcome: ExpectedOracleOutcome | None = None

    def __post_init__(self) -> None:
        if not self.case_id.strip():
            raise ValueError("case_id must not be blank")
        if self.source not in {"catalog", "generated"}:
            raise ValueError(f"unsupported source: {self.source}")
        if not 1 <= self.width <= MAX_SOLVABILITY_AXIS:
            raise ValueError(f"width must be within 1..{MAX_SOLVABILITY_AXIS}")
        if not 1 <= self.height <= MAX_SOLVABILITY_AXIS:
            raise ValueError(f"height must be within 1..{MAX_SOLVABILITY_AXIS}")
        if self.width * self.height > MAX_SOLVABILITY_CELLS:
            raise ValueError(f"cell count must be <= {MAX_SOLVABILITY_CELLS}")
        if not 1 <= len(self.agents) <= MAX_SOLVABILITY_ROBOTS:
            raise ValueError(f"agent count must be within 1..{MAX_SOLVABILITY_ROBOTS}")
        if len(set(self.obstacles)) != len(self.obstacles):
            raise ValueError("obstacles must be unique")

        def require_inside(cell: Cell, label: str) -> None:
            x, y = cell
            if not (0 <= x < self.width and 0 <= y < self.height):
                raise ValueError(f"{label} must be inside the map: {cell}")

        for obstacle in self.obstacles:
            require_inside(obstacle, "obstacle")
        ids = [agent.agent_id for agent in self.agents]
        starts = [agent.start for agent in self.agents]
        goals = [agent.goal for agent in self.agents]
        if any(not agent_id.strip() for agent_id in ids) or len(set(ids)) != len(ids):
            raise ValueError("agent ids must be non-blank and unique")
        if len(set(starts)) != len(starts):
            raise ValueError("agent starts must be unique")
        if len(set(goals)) != len(goals):
            raise ValueError("agent goals must be unique")
        blocked = set(self.obstacles)
        for agent in self.agents:
            require_inside(agent.start, f"{agent.agent_id} start")
            require_inside(agent.goal, f"{agent.agent_id} goal")
            if agent.start in blocked or agent.goal in blocked:
                raise ValueError("obstacles must not overlap starts or goals")

    @property
    def case_key(self) -> tuple[object, ...]:
        return (
            self.width,
            self.height,
            tuple(sorted(self.obstacles)),
            tuple((agent.start, agent.goal) for agent in self.agents),
        )

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "source": self.source,
            "width": self.width,
            "height": self.height,
            "obstacles": [list(cell) for cell in self.obstacles],
            "agents": [
                {
                    "agentId": agent.agent_id,
                    "start": list(agent.start),
                    "goal": list(agent.goal),
                }
                for agent in self.agents
            ],
            "expectedOracleOutcome": self.expected_oracle_outcome,
        }
```

- [ ] **Step 4: 实现四个目录案例**

使用以下精确几何；目标允许与另一机器人的起点重合：

```python
def solvability_catalog() -> tuple[SolvabilityCase, ...]:
    return (
        SolvabilityCase(
            "catalog-solo-straight",
            "catalog",
            3,
            1,
            (),
            (SolvabilityAgent("R1", (0, 0), (2, 0)),),
            "solved",
        ),
        SolvabilityCase(
            "catalog-independent-r2",
            "catalog",
            3,
            2,
            (),
            (
                SolvabilityAgent("R1", (0, 0), (2, 0)),
                SolvabilityAgent("R2", (0, 1), (2, 1)),
            ),
            "solved",
        ),
        SolvabilityCase(
            "catalog-side-bypass-swap-r2",
            "catalog",
            3,
            2,
            (),
            (
                SolvabilityAgent("R1", (0, 0), (2, 0)),
                SolvabilityAgent("R2", (2, 0), (0, 0)),
            ),
            "solved",
        ),
        SolvabilityCase(
            "catalog-no-bypass-swap-r2",
            "catalog",
            3,
            1,
            (),
            (
                SolvabilityAgent("R1", (0, 0), (2, 0)),
                SolvabilityAgent("R2", (2, 0), (0, 0)),
            ),
            "unsolved",
        ),
    )
```

- [ ] **Step 5: 实现确定性生成和静态可达过滤**

实现私有 `_statically_reachable`，只在单机器人网格上做 BFS：

```python
def _statically_reachable(
    width: int,
    height: int,
    blocked: set[Cell],
    start: Cell,
    goal: Cell,
) -> bool:
    queue = deque([start])
    visited = {start}
    while queue:
        x, y = queue.popleft()
        if (x, y) == goal:
            return True
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            following = (x + dx, y + dy)
            if (
                0 <= following[0] < width
                and 0 <= following[1] < height
                and following not in blocked
                and following not in visited
            ):
                visited.add(following)
                queue.append(following)
    return False
```

生成器必须使用 `random.Random(seed)`，按以下配置循环：

```python
_GENERATION_PROFILES = ((3, 3, 2), (4, 3, 2), (4, 4, 3))


def generate_solvability_cases(
    seed: int,
    sample_count: int,
) -> tuple[SolvabilityCase, ...]:
    if not 0 <= sample_count <= 512:
        raise ValueError("sample_count must be within 0..512")
    rng = random.Random(seed)
    generated: list[SolvabilityCase] = []
    seen: set[tuple[object, ...]] = set()
    total_attempts = sample_count * MAX_GENERATION_ATTEMPTS_PER_CASE
    attempts = 0
    while len(generated) < sample_count and attempts < total_attempts:
        index = len(generated)
        width, height, robot_count = _GENERATION_PROFILES[index % len(_GENERATION_PROFILES)]
        cells = [(x, y) for y in range(height) for x in range(width)]
        obstacle_count = rng.randint(0, min(3, len(cells) - robot_count))
        obstacles = tuple(sorted(rng.sample(cells, obstacle_count)))
        blocked = set(obstacles)
        traversable = [cell for cell in cells if cell not in blocked]
        starts = rng.sample(traversable, robot_count)
        goals = rng.sample(traversable, robot_count)
        attempts += 1
        if any(start == goal for start, goal in zip(starts, goals, strict=True)):
            continue
        if not all(
            _statically_reachable(width, height, blocked, start, goal)
            for start, goal in zip(starts, goals, strict=True)
        ):
            continue
        case = SolvabilityCase(
            case_id=f"generated-s{seed}-i{index + 1:04d}",
            source="generated",
            width=width,
            height=height,
            obstacles=obstacles,
            agents=tuple(
                SolvabilityAgent(f"R{agent_index + 1}", start, goal)
                for agent_index, (start, goal) in enumerate(
                    zip(starts, goals, strict=True)
                )
            ),
        )
        if case.case_key in seen:
            continue
        seen.add(case.case_key)
        generated.append(case)
    if len(generated) != sample_count:
        raise RuntimeError(
            f"unable to generate {sample_count} unique cases after {attempts} attempts"
        )
    return tuple(generated)


def solvability_cases(seed: int, sample_count: int) -> tuple[SolvabilityCase, ...]:
    return solvability_catalog() + generate_solvability_cases(seed, sample_count)
```

- [ ] **Step 6: 运行案例测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_oracle.py -q
```

Expected: 当前已有案例测试 PASS。

- [ ] **Step 7: 提交案例边界**

```powershell
git add backend/benchmarks/solvability_cases.py backend/tests/test_solvability_oracle.py
git commit -m "test: define bounded solvability cases"
```

### Task 2: 联合状态 A* 精确参照

**Files:**
- Create: `backend/benchmarks/solvability_oracle.py`
- Modify: `backend/tests/test_solvability_oracle.py`

**Interfaces:**
- Consumes: `SolvabilityCase`
- Produces: `OracleOutcome = Literal["solved", "unsolved", "limit"]`
- Produces: `OracleResult(outcome, makespan, expanded_state_count, paths)`
- Produces: `solve_exact(case: SolvabilityCase, max_expanded_states: int) -> OracleResult`

- [ ] **Step 1: 写精确结果、最短 makespan、安全路径和上限语义的失败测试**

追加：

```python
from backend.app.dispatch import detect_conflicts
from backend.benchmarks.solvability_oracle import solve_exact


def _catalog_case(case_id: str) -> SolvabilityCase:
    return next(case for case in solvability_catalog() if case.case_id == case_id)


def test_exact_oracle_solves_catalog_cases_with_minimum_makespan() -> None:
    solo = solve_exact(_catalog_case("catalog-solo-straight"), 100_000)
    independent = solve_exact(_catalog_case("catalog-independent-r2"), 100_000)
    bypass = solve_exact(_catalog_case("catalog-side-bypass-swap-r2"), 100_000)

    assert (solo.outcome, solo.makespan) == ("solved", 2)
    assert (independent.outcome, independent.makespan) == ("solved", 2)
    assert (bypass.outcome, bypass.makespan) == ("solved", 4)
    for result in (solo, independent, bypass):
        assert result.paths is not None
        assert detect_conflicts(result.paths) == []


def test_exact_oracle_reports_no_bypass_swap_as_unsolved() -> None:
    result = solve_exact(_catalog_case("catalog-no-bypass-swap-r2"), 100_000)

    assert result.outcome == "unsolved"
    assert result.makespan is None
    assert result.paths is None


def test_exact_oracle_reports_limit_instead_of_unsolved() -> None:
    result = solve_exact(_catalog_case("catalog-side-bypass-swap-r2"), 1)

    assert result.outcome == "limit"
    assert result.expanded_state_count == 1
    assert result.paths is None


def test_exact_oracle_handles_initial_goal_and_is_deterministic() -> None:
    case = SolvabilityCase(
        "already-there",
        "generated",
        1,
        1,
        (),
        (SolvabilityAgent("R1", (0, 0), (0, 0)),),
    )
    first = solve_exact(case, 100)
    second = solve_exact(case, 100)

    assert first == second
    assert first.makespan == 0
    assert first.expanded_state_count == 0
    assert first.paths == {"R1": [(0, 0)]}


def test_exact_oracle_rejects_non_positive_expansion_limit() -> None:
    with pytest.raises(ValueError, match="max_expanded_states"):
        solve_exact(_catalog_case("catalog-solo-straight"), 0)
```

- [ ] **Step 2: 运行测试并确认缺少求解器**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_oracle.py -q
```

Expected: collection FAIL，错误包含 `No module named 'backend.benchmarks.solvability_oracle'`。

- [ ] **Step 3: 实现结果类型和合法联合后继**

在 `backend/benchmarks/solvability_oracle.py` 写入以下结构：

```python
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import product
from typing import Literal

from backend.app.schemas import Cell
from backend.benchmarks.solvability_cases import SolvabilityCase

OracleOutcome = Literal["solved", "unsolved", "limit"]
JointState = tuple[Cell, ...]
SearchState = tuple[JointState, int]
_MOVE_DELTAS = ((0, 0), (0, -1), (1, 0), (0, 1), (-1, 0))


@dataclass(frozen=True, slots=True)
class OracleResult:
    outcome: OracleOutcome
    makespan: int | None
    expanded_state_count: int
    paths: dict[str, list[Cell]] | None


def _has_edge_swap(previous: JointState, following: JointState) -> bool:
    return any(
        previous[first] == following[second]
        and previous[second] == following[first]
        for first in range(len(previous))
        for second in range(first + 1, len(previous))
    )


def _joint_successors(case: SolvabilityCase, state: JointState):
    blocked = set(case.obstacles)
    choices: list[tuple[Cell, ...]] = []
    for x, y in state:
        moves = []
        for dx, dy in _MOVE_DELTAS:
            target = (x + dx, y + dy)
            if (
                0 <= target[0] < case.width
                and 0 <= target[1] < case.height
                and target not in blocked
            ):
                moves.append(target)
        choices.append(tuple(moves))
    for following in product(*choices):
        if len(set(following)) != len(following):
            continue
        if _has_edge_swap(state, following):
            continue
        yield following
```

- [ ] **Step 4: 实现 A*、路径重建和严格返回条件**

实现时目标元组只构造一次，`best_g` 只接受更短路径：

```python
def _completed_mask(positions: JointState, goals: JointState, previous: int = 0) -> int:
    completed = previous
    for index, (cell, goal) in enumerate(zip(positions, goals, strict=True)):
        if cell == goal:
            completed |= 1 << index
    return completed


def _heuristic(
    positions: JointState,
    goals: JointState,
    completed_mask: int,
) -> int:
    return max(
        (
            0
            if completed_mask & (1 << index)
            else abs(cell[0] - goal[0]) + abs(cell[1] - goal[1])
        )
        for index, (cell, goal) in enumerate(
            zip(positions, goals, strict=True)
        )
    )


def _reconstruct_paths(
    case: SolvabilityCase,
    came_from: dict[SearchState, SearchState],
    goal_state: SearchState,
) -> dict[str, list[Cell]]:
    states = [goal_state]
    while states[-1] in came_from:
        states.append(came_from[states[-1]])
    states.reverse()
    return {
        agent.agent_id: [state[0][index] for state in states]
        for index, agent in enumerate(case.agents)
    }


def solve_exact(
    case: SolvabilityCase,
    max_expanded_states: int,
) -> OracleResult:
    if max_expanded_states <= 0:
        raise ValueError("max_expanded_states must be positive")
    start_positions = tuple(agent.start for agent in case.agents)
    goals = tuple(agent.goal for agent in case.agents)
    all_completed_mask = (1 << len(case.agents)) - 1
    start_completed_mask = _completed_mask(start_positions, goals)
    start: SearchState = (start_positions, start_completed_mask)
    if start_completed_mask == all_completed_mask:
        return OracleResult(
            "solved",
            0,
            0,
            {agent.agent_id: [agent.start] for agent in case.agents},
        )

    open_heap: list[tuple[int, int, JointState, int]] = []
    heappush(
        open_heap,
        (_heuristic(start_positions, goals, start_completed_mask), 0, *start),
    )
    best_g = {start: 0}
    came_from: dict[SearchState, SearchState] = {}
    expanded_state_count = 0

    while open_heap:
        _priority, distance, positions, completed_mask = heappop(open_heap)
        state: SearchState = (positions, completed_mask)
        if best_g.get(state) != distance:
            continue
        if completed_mask == all_completed_mask:
            return OracleResult(
                "solved",
                distance,
                expanded_state_count,
                _reconstruct_paths(case, came_from, state),
            )
        if expanded_state_count == max_expanded_states:
            return OracleResult("limit", None, expanded_state_count, None)
        expanded_state_count += 1
        for following_positions in _joint_successors(case, positions):
            following_completed_mask = _completed_mask(
                following_positions,
                goals,
                completed_mask,
            )
            following: SearchState = (
                following_positions,
                following_completed_mask,
            )
            following_distance = distance + 1
            if following_distance >= best_g.get(following, following_distance + 1):
                continue
            best_g[following] = following_distance
            came_from[following] = state
            heappush(
                open_heap,
                (
                    following_distance
                    + _heuristic(
                        following_positions,
                        goals,
                        following_completed_mask,
                    ),
                    following_distance,
                    following_positions,
                    following_completed_mask,
                ),
            )

    return OracleResult("unsolved", None, expanded_state_count, None)
```

- [ ] **Step 5: 对所有 solved 输出补充终点和逐 tick 不变量断言**

在测试中对三个 `solved` 结果逐个断言：

```python
for case_id in (
    "catalog-solo-straight",
    "catalog-independent-r2",
    "catalog-side-bypass-swap-r2",
):
    case = _catalog_case(case_id)
    result = solve_exact(case, 100_000)
    assert result.paths is not None
    for agent in case.agents:
        assert result.paths[agent.agent_id][0] == agent.start
        assert agent.goal in result.paths[agent.agent_id]
    assert len({len(path) for path in result.paths.values()}) == 1
```

- [ ] **Step 6: 运行精确参照测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_oracle.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交精确参照**

```powershell
git add backend/benchmarks/solvability_oracle.py backend/tests/test_solvability_oracle.py
git commit -m "feat: add bounded exact solvability oracle"
```

### Task 3: 差分结果模型和固定分配生产适配

**Files:**
- Create: `backend/benchmarks/solvability_results.py`
- Create: `backend/benchmarks/solvability_runner.py`
- Create: `backend/tests/test_solvability_differential.py`

**Interfaces:**
- Produces: `PlannerOutcome = Literal["solved", "failed", "conflicted"]`
- Produces: `ComparisonClass` 五个固定值。
- Produces: `classify_comparison(oracle_outcome: OracleOutcome, planner_outcome: PlannerOutcome) -> ComparisonClass`
- Produces: `build_fixed_assignment_input(case: SolvabilityCase) -> tuple[Scenario, list[Assignment]]`
- Produces: `execute_solvability_case(case: SolvabilityCase, run_index: int, max_expanded_states: int) -> SolvabilityRun`
- Produces: `SolvabilityRun`、`SolvabilityCaseSummary`、`SolvabilityReport`。

- [ ] **Step 1: 写固定分配和五类比较结果的失败测试**

在 `backend/tests/test_solvability_differential.py` 写入：

```python
from backend.benchmarks import solvability_runner as runner_module
from backend.benchmarks.solvability_cases import solvability_catalog
from backend.benchmarks.solvability_runner import (
    build_fixed_assignment_input,
    classify_comparison,
    execute_solvability_case,
)


def _case(case_id: str):
    return next(case for case in solvability_catalog() if case.case_id == case_id)


def test_fixed_assignment_adapter_preserves_agent_start_goal_and_order() -> None:
    case = _case("catalog-side-bypass-swap-r2")

    scenario, assignments = build_fixed_assignment_input(case)

    assert [robot.id for robot in scenario.robots] == ["R1", "R2"]
    assert [robot.start for robot in scenario.robots] == [(0, 0), (2, 0)]
    assert all(robot.moveTicks == 1 for robot in scenario.robots)
    assert all(robot.capabilities == ["inspection"] for robot in scenario.robots)
    assert [assignment.robotId for assignment in assignments] == ["R1", "R2"]
    assert [assignment.tasks[0].targets for assignment in assignments] == [
        [(2, 0)],
        [(0, 0)],
    ]
    assert all(assignment.tasks[0].serviceTime == 0 for assignment in assignments)


def test_comparison_classification_has_exact_truth_table() -> None:
    assert classify_comparison("solved", "solved") == "agreementSolved"
    assert classify_comparison("solved", "failed") == "oracleSolvedPlannerMiss"
    assert classify_comparison("solved", "conflicted") == "oracleSolvedPlannerMiss"
    assert (
        classify_comparison("unsolved", "failed")
        == "oracleUnsolvedPlannerNoValidPlan"
    )
    assert (
        classify_comparison("unsolved", "conflicted")
        == "oracleUnsolvedPlannerNoValidPlan"
    )
    assert (
        classify_comparison("unsolved", "solved")
        == "oracleUnsolvedPlannerSolved"
    )
    assert classify_comparison("limit", "solved") == "oracleLimit"


def test_execute_solvability_case_agrees_on_simple_case() -> None:
    run = execute_solvability_case(
        _case("catalog-solo-straight"),
        run_index=1,
        max_expanded_states=100_000,
    )

    assert run.outcome == "completed"
    assert run.oracle_outcome == "solved"
    assert run.planner_outcome == "solved"
    assert run.comparison_class == "agreementSolved"
    assert run.planner_failure_count == 0
    assert run.planner_conflict_count == 0


def test_execute_solvability_case_marks_valid_oracle_and_conflicted_planner_as_miss(
    monkeypatch,
) -> None:
    def conflicting_paths(scenario, robots, assignments, *args, **kwargs):
        return {
            "R1": [(0, 0), (1, 0), (2, 0)],
            "R2": [(2, 0), (1, 0), (0, 0)],
        }, []

    monkeypatch.setattr(runner_module, "build_paths", conflicting_paths)

    run = execute_solvability_case(
        _case("catalog-side-bypass-swap-r2"),
        run_index=1,
        max_expanded_states=100_000,
    )

    assert run.planner_outcome == "conflicted"
    assert run.planner_conflict_count > 0
    assert run.comparison_class == "oracleSolvedPlannerMiss"


def test_no_bypass_unsolved_case_is_not_reported_as_planner_miss() -> None:
    run = execute_solvability_case(
        _case("catalog-no-bypass-swap-r2"),
        run_index=1,
        max_expanded_states=100_000,
    )

    assert run.oracle_outcome == "unsolved"
    assert run.planner_outcome in {"failed", "conflicted"}
    assert run.comparison_class == "oracleUnsolvedPlannerNoValidPlan"
```

- [ ] **Step 2: 运行并确认缺少结果与运行器模块**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_differential.py -q
```

Expected: collection FAIL，指向 `solvability_runner` 或 `solvability_results` 不存在。

- [ ] **Step 3: 定义逐次结果和序列化边界**

在 `backend/benchmarks/solvability_results.py` 定义：

```python
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from statistics import median
from typing import Literal

from backend.app.schemas import Cell
from backend.benchmarks.solvability_cases import (
    SolvabilityCase,
    SolvabilityCaseSource,
)
from backend.benchmarks.solvability_oracle import OracleOutcome

SolvabilityRunOutcome = Literal["completed", "timeout", "error"]
PlannerOutcome = Literal["solved", "failed", "conflicted"]
ComparisonClass = Literal[
    "agreementSolved",
    "oracleSolvedPlannerMiss",
    "oracleUnsolvedPlannerNoValidPlan",
    "oracleUnsolvedPlannerSolved",
    "oracleLimit",
]


@dataclass(frozen=True, slots=True)
class SolvabilityRun:
    case_id: str
    source: SolvabilityCaseSource
    run_index: int
    width: int
    height: int
    robot_count: int
    obstacle_count: int
    outcome: SolvabilityRunOutcome
    error_type: str | None
    error_message: str | None
    oracle_outcome: OracleOutcome | None
    oracle_makespan: int | None
    oracle_expanded_state_count: int | None
    oracle_paths: dict[str, list[Cell]] | None
    planner_outcome: PlannerOutcome | None
    planner_makespan: int | None
    planner_reached_all_goals: bool | None
    planner_failure_count: int | None
    planner_failures: list[str] | None
    planner_conflict_count: int | None
    planner_conflicts: list[dict[str, object]] | None
    planner_paths: dict[str, list[Cell]] | None
    comparison_class: ComparisonClass | None
    wall_clock_ms: float | None

    @classmethod
    def timeout(cls, case: SolvabilityCase, run_index: int, wall_clock_ms: float):
        return cls.failed_run(case, run_index, "timeout", "TimeoutError", None, wall_clock_ms)

    @classmethod
    def error(
        cls,
        case: SolvabilityCase,
        run_index: int,
        error_type: str,
        error_message: str,
        wall_clock_ms: float,
    ):
        return cls.failed_run(
            case, run_index, "error", error_type, error_message, wall_clock_ms
        )

    @classmethod
    def failed_run(
        cls,
        case: SolvabilityCase,
        run_index: int,
        outcome: Literal["timeout", "error"],
        error_type: str,
        error_message: str | None,
        wall_clock_ms: float,
    ) -> "SolvabilityRun":
        return cls(
            case_id=case.case_id,
            source=case.source,
            run_index=run_index,
            width=case.width,
            height=case.height,
            robot_count=len(case.agents),
            obstacle_count=len(case.obstacles),
            outcome=outcome,
            error_type=error_type,
            error_message=error_message,
            oracle_outcome=None,
            oracle_makespan=None,
            oracle_expanded_state_count=None,
            oracle_paths=None,
            planner_outcome=None,
            planner_makespan=None,
            planner_reached_all_goals=None,
            planner_failure_count=None,
            planner_failures=None,
            planner_conflict_count=None,
            planner_conflicts=None,
            planner_paths=None,
            comparison_class=None,
            wall_clock_ms=wall_clock_ms,
        )

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "source": self.source,
            "runIndex": self.run_index,
            "width": self.width,
            "height": self.height,
            "robotCount": self.robot_count,
            "obstacleCount": self.obstacle_count,
            "outcome": self.outcome,
            "errorType": self.error_type,
            "errorMessage": self.error_message,
            "oracleOutcome": self.oracle_outcome,
            "oracleMakespan": self.oracle_makespan,
            "oracleExpandedStateCount": self.oracle_expanded_state_count,
            "oraclePaths": self.oracle_paths,
            "plannerOutcome": self.planner_outcome,
            "plannerMakespan": self.planner_makespan,
            "plannerReachedAllGoals": self.planner_reached_all_goals,
            "plannerFailureCount": self.planner_failure_count,
            "plannerFailures": self.planner_failures,
            "plannerConflictCount": self.planner_conflict_count,
            "plannerConflicts": self.planner_conflicts,
            "plannerPaths": self.planner_paths,
            "comparisonClass": self.comparison_class,
            "wallClockMs": self.wall_clock_ms,
        }
```

同一文件继续定义精确汇总字段：

```python
@dataclass(frozen=True, slots=True)
class SolvabilityCaseSummary:
    case_id: str
    run_count: int
    completed_run_count: int
    timeout_count: int
    error_count: int
    agreement_solved_count: int
    oracle_solved_planner_miss_count: int
    oracle_unsolved_planner_no_valid_plan_count: int
    oracle_unsolved_planner_solved_count: int
    oracle_limit_count: int
    classification_stable: bool
    comparison_class: ComparisonClass | None
    median_wall_clock_ms: float | None
    p95_wall_clock_ms: float | None

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "runCount": self.run_count,
            "completedRunCount": self.completed_run_count,
            "timeoutCount": self.timeout_count,
            "errorCount": self.error_count,
            "agreementSolvedCount": self.agreement_solved_count,
            "oracleSolvedPlannerMissCount": self.oracle_solved_planner_miss_count,
            "oracleUnsolvedPlannerNoValidPlanCount": (
                self.oracle_unsolved_planner_no_valid_plan_count
            ),
            "oracleUnsolvedPlannerSolvedCount": (
                self.oracle_unsolved_planner_solved_count
            ),
            "oracleLimitCount": self.oracle_limit_count,
            "classificationStable": self.classification_stable,
            "comparisonClass": self.comparison_class,
            "medianWallClockMs": self.median_wall_clock_ms,
            "p95WallClockMs": self.p95_wall_clock_ms,
        }


def nearest_rank_p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[ceil(0.95 * len(ordered)) - 1]


def summarize_runs(
    cases: list[SolvabilityCase],
    runs: list[SolvabilityRun],
) -> list[SolvabilityCaseSummary]:
    summaries = []
    for case in cases:
        case_runs = [run for run in runs if run.case_id == case.case_id]
        completed = [run for run in case_runs if run.outcome == "completed"]
        classes = [
            run.comparison_class
            for run in completed
            if run.comparison_class is not None
        ]
        wall = [
            run.wall_clock_ms
            for run in completed
            if run.wall_clock_ms is not None
        ]
        unique_classes = set(classes)
        classification_stable = (
            len(completed) == len(case_runs)
            and len(classes) == len(case_runs)
            and len(unique_classes) == 1
        )
        summaries.append(
            SolvabilityCaseSummary(
                case_id=case.case_id,
                run_count=len(case_runs),
                completed_run_count=len(completed),
                timeout_count=sum(run.outcome == "timeout" for run in case_runs),
                error_count=sum(run.outcome == "error" for run in case_runs),
                agreement_solved_count=classes.count("agreementSolved"),
                oracle_solved_planner_miss_count=classes.count(
                    "oracleSolvedPlannerMiss"
                ),
                oracle_unsolved_planner_no_valid_plan_count=classes.count(
                    "oracleUnsolvedPlannerNoValidPlan"
                ),
                oracle_unsolved_planner_solved_count=classes.count(
                    "oracleUnsolvedPlannerSolved"
                ),
                oracle_limit_count=classes.count("oracleLimit"),
                classification_stable=classification_stable,
                comparison_class=classes[0] if classification_stable else None,
                median_wall_clock_ms=median(wall) if wall else None,
                p95_wall_clock_ms=nearest_rank_p95(wall),
            )
        )
    return summaries


@dataclass(frozen=True, slots=True)
class SolvabilityReport:
    schema_version: int
    generated_at: str
    config: dict[str, object]
    cases: list[SolvabilityCase]
    runs: list[SolvabilityRun]
    case_summaries: list[SolvabilityCaseSummary]
    candidate_counterexample_case_ids: list[str]

    @classmethod
    def create(
        cls,
        config: dict[str, object],
        cases: list[SolvabilityCase],
        runs: list[SolvabilityRun],
    ) -> "SolvabilityReport":
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        miss_ids = {
            run.case_id
            for run in runs
            if run.comparison_class == "oracleSolvedPlannerMiss"
        }
        case_copy = list(cases)
        run_copy = list(runs)
        return cls(
            schema_version=1,
            generated_at=generated_at,
            config=dict(config),
            cases=case_copy,
            runs=run_copy,
            case_summaries=summarize_runs(case_copy, run_copy),
            candidate_counterexample_case_ids=[
                case.case_id for case in case_copy if case.case_id in miss_ids
            ],
        )

    def to_record(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "generatedAt": self.generated_at,
            "config": self.config,
            "cases": [case.to_record() for case in self.cases],
            "runs": [run.to_record() for run in self.runs],
            "caseSummaries": [
                summary.to_record() for summary in self.case_summaries
            ],
            "candidateCounterexampleCaseIds": (
                self.candidate_counterexample_case_ids
            ),
        }
```

`classification_stable` 只有在该案例没有 timeout/error、每次都有分类且全部分类相同时才为 `True`。失败工厂已把所有 oracle/planner 字段设为 `None`，同时保留 case 的 ID、来源、尺寸、机器人和障碍数量；不要用空字符串伪装缺失值。

- [ ] **Step 4: 实现固定分配场景适配器**

在 `backend/benchmarks/solvability_runner.py` 中使用现有精确类型：

```python
from dataclasses import replace
from time import perf_counter

from backend.app.dispatch import build_paths, detect_conflicts
from backend.app.schemas import Assignment, Scenario
from backend.benchmarks.solvability_cases import SolvabilityCase
from backend.benchmarks.solvability_oracle import OracleOutcome, solve_exact
from backend.benchmarks.solvability_results import (
    ComparisonClass,
    PlannerOutcome,
    SolvabilityRun,
)


def build_fixed_assignment_input(
    case: SolvabilityCase,
) -> tuple[Scenario, list[Assignment]]:
    robots = [
        {
            "id": agent.agent_id,
            "name": agent.agent_id,
            "start": agent.start,
            "battery": 10_000,
            "batteryCapacity": 10_000,
            "load": 1,
            "moveTicks": 1,
            "capabilities": ["inspection"],
        }
        for agent in case.agents
    ]
    tasks = [
        {
            "id": f"GOAL-{agent.agent_id}",
            "type": "inspection",
            "title": f"Goal {agent.agent_id}",
            "priority": 1,
            "releaseTime": 0,
            "serviceTime": 0,
            "targets": [agent.goal],
        }
        for agent in case.agents
    ]
    scenario = Scenario.model_validate(
        {
            "id": case.case_id,
            "name": case.case_id,
            "description": "离线小规模可解性差分案例",
            "width": case.width,
            "height": case.height,
            "obstacles": case.obstacles,
            "zones": {
                "warehouse": [agent.start for agent in case.agents],
                "inspection": [agent.goal for agent in case.agents],
                "delivery": [],
                "charging": [],
            },
            "robots": robots,
            "tasks": tasks,
            "dynamic": {
                "triggerTime": 0,
                "blockedCells": [],
                "failedRobots": [],
                "tasks": [],
            },
        }
    )
    assignments = [
        Assignment(robotId=robot.id, tasks=[task])
        for robot, task in zip(scenario.robots, scenario.tasks, strict=True)
    ]
    return scenario, assignments
```

- [ ] **Step 5: 实现比较真值表和一次完整差分**

```python
def classify_comparison(
    oracle_outcome: OracleOutcome,
    planner_outcome: PlannerOutcome,
) -> ComparisonClass:
    if oracle_outcome == "limit":
        return "oracleLimit"
    if oracle_outcome == "solved":
        return "agreementSolved" if planner_outcome == "solved" else "oracleSolvedPlannerMiss"
    return (
        "oracleUnsolvedPlannerSolved"
        if planner_outcome == "solved"
        else "oracleUnsolvedPlannerNoValidPlan"
    )
```

`execute_solvability_case` 按以下精确实现执行；隔离父进程会在 Task 4 用实际隔离墙钟覆盖本地 `wall_clock_ms`：

```python
def execute_solvability_case(
    case: SolvabilityCase,
    run_index: int,
    max_expanded_states: int,
) -> SolvabilityRun:
    started_at = perf_counter()
    oracle = solve_exact(case, max_expanded_states)
    if (
        case.expected_oracle_outcome is not None
        and oracle.outcome != case.expected_oracle_outcome
    ):
        raise AssertionError(
            f"{case.case_id} oracle outcome changed: "
            f"{oracle.outcome} != {case.expected_oracle_outcome}"
        )

    scenario, assignments = build_fixed_assignment_input(case)
    paths, failures = build_paths(
        scenario,
        scenario.robots,
        assignments,
        True,
        [],
        [],
    )
    conflicts = detect_conflicts(paths)
    planner_reached_all_goals = all(
        agent.agent_id in paths
        and agent.goal in paths[agent.agent_id]
        for agent in case.agents
    )
    if failures or not planner_reached_all_goals:
        planner_outcome: PlannerOutcome = "failed"
    elif conflicts:
        planner_outcome = "conflicted"
    else:
        planner_outcome = "solved"
    planner_makespan = (
        max(
            paths[agent.agent_id].index(agent.goal)
            for agent in case.agents
        )
        if planner_reached_all_goals
        else None
    )
    return SolvabilityRun(
        case_id=case.case_id,
        source=case.source,
        run_index=run_index,
        width=case.width,
        height=case.height,
        robot_count=len(case.agents),
        obstacle_count=len(case.obstacles),
        outcome="completed",
        error_type=None,
        error_message=None,
        oracle_outcome=oracle.outcome,
        oracle_makespan=oracle.makespan,
        oracle_expanded_state_count=oracle.expanded_state_count,
        oracle_paths=oracle.paths,
        planner_outcome=planner_outcome,
        planner_makespan=planner_makespan,
        planner_reached_all_goals=planner_reached_all_goals,
        planner_failure_count=len(failures),
        planner_failures=list(failures),
        planner_conflict_count=len(conflicts),
        planner_conflicts=[
            conflict.model_dump(mode="json") for conflict in conflicts
        ],
        planner_paths=paths,
        comparison_class=classify_comparison(oracle.outcome, planner_outcome),
        wall_clock_ms=round((perf_counter() - started_at) * 1_000, 2),
    )
```

- [ ] **Step 6: 完成结果模型测试**

追加测试，构造双方一致、漏解和 `oracleLimit` 记录，断言：

```python
from dataclasses import replace

from backend.benchmarks.solvability_results import SolvabilityReport

solved_case = _case("catalog-solo-straight")
limited_case = replace(
    _case("catalog-independent-r2"),
    case_id="generated-limit-case",
    source="generated",
    expected_oracle_outcome=None,
)
agreement_run = execute_solvability_case(solved_case, 1, 100_000)
miss_run = replace(
    agreement_run,
    run_index=2,
    planner_outcome="failed",
    planner_reached_all_goals=False,
    planner_failure_count=1,
    planner_failures=["forced planner miss"],
    comparison_class="oracleSolvedPlannerMiss",
)
limit_run = replace(
    execute_solvability_case(limited_case, 1, 100_000),
    oracle_outcome="limit",
    oracle_makespan=None,
    oracle_paths=None,
    comparison_class="oracleLimit",
)
report = SolvabilityReport.create(
    config={"seed": 20260904},
    cases=[solved_case, limited_case],
    runs=[agreement_run, miss_run, limit_run],
)

assert report.schema_version == 1
assert report.candidate_counterexample_case_ids == [solved_case.case_id]
assert report.to_record()["schemaVersion"] == 1
assert report.to_record()["cases"][0]["caseId"] == solved_case.case_id
assert report.case_summaries[0].run_count == 2
assert report.case_summaries[0].oracle_solved_planner_miss_count == 1
```

- [ ] **Step 7: 运行差分适配测试和原算法聚焦回归**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_differential.py backend/tests/test_algorithm.py -q
```

Expected: PASS；现有 `build_paths` 回归无变化。

- [ ] **Step 8: 提交适配与结果语义**

```powershell
git add backend/benchmarks/solvability_results.py backend/benchmarks/solvability_runner.py backend/tests/test_solvability_differential.py
git commit -m "feat: compare exact and prioritized path planning"
```

### Task 4: 隔离单次运行与批量编排

**Files:**
- Modify: `backend/benchmarks/solvability_runner.py`
- Modify: `backend/tests/test_solvability_differential.py`

**Interfaces:**
- Consumes: `run_isolated_process` 和 `BenchmarkInfrastructureError`。
- Produces: `run_isolated_solvability_case(case, run_index, max_expanded_states, timeout_seconds, worker_callable=execute_solvability_case) -> SolvabilityRun`
- Produces: `run_solvability_cases(cases, repetitions, max_expanded_states, timeout_seconds, on_result=None) -> list[SolvabilityRun]`

- [ ] **Step 1: 写完成、超时、异常和累计回调测试**

在测试模块顶层定义可被 spawn pickle 的 worker：

```python
import time

from backend.benchmarks.process_isolation import BenchmarkInfrastructureError
from backend.benchmarks.solvability_runner import (
    run_isolated_solvability_case,
    run_solvability_cases,
)


def _sleeping_worker(case, run_index, max_expanded_states):
    time.sleep(0.2)


def _failing_worker(case, run_index, max_expanded_states):
    raise RuntimeError("worker failed\nwith second line")


def test_isolated_solvability_case_preserves_timeout() -> None:
    run = run_isolated_solvability_case(
        _case("catalog-solo-straight"),
        1,
        100_000,
        0.05,
        worker_callable=_sleeping_worker,
    )

    assert run.outcome == "timeout"
    assert run.error_type == "TimeoutError"
    assert run.comparison_class is None


def test_isolated_solvability_case_sanitizes_worker_error() -> None:
    run = run_isolated_solvability_case(
        _case("catalog-solo-straight"),
        1,
        100_000,
        5,
        worker_callable=_failing_worker,
    )

    assert run.outcome == "error"
    assert run.error_type == "RuntimeError"
    assert "\n" not in run.error_message


def test_solvability_batch_reports_cumulative_copies(monkeypatch) -> None:
    observed = []

    def fake_isolated(case, run_index, max_expanded_states, timeout_seconds):
        return execute_solvability_case(case, run_index, max_expanded_states)

    monkeypatch.setattr(runner_module, "run_isolated_solvability_case", fake_isolated)
    cases = (_case("catalog-solo-straight"), _case("catalog-independent-r2"))

    runs = run_solvability_cases(
        cases,
        repetitions=2,
        max_expanded_states=100_000,
        timeout_seconds=5,
        on_result=lambda current: observed.append(current),
    )

    assert len(runs) == 4
    assert [len(item) for item in observed] == [1, 2, 3, 4]
    assert observed[-1] is not runs
```

再用 monkeypatch 让 `run_isolated_solvability_case` 抛出 `BenchmarkInfrastructureError`，断言批量函数原样向上抛出，而不是生成普通 `error` 记录。

- [ ] **Step 2: 运行并确认缺少隔离接口**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_differential.py -q
```

Expected: FAIL，指出隔离接口不存在。

- [ ] **Step 3: 复用现有隔离器并映射结果**

在运行器中实现：

```python
from collections.abc import Callable
import traceback

from backend.benchmarks.process_isolation import (
    BenchmarkInfrastructureError,
    run_isolated_process,
    sanitize_error_text,
)


def run_isolated_solvability_case(
    case: SolvabilityCase,
    run_index: int,
    max_expanded_states: int,
    timeout_seconds: float,
    worker_callable: Callable = execute_solvability_case,
) -> SolvabilityRun:
    execution = run_isolated_process(
        worker_callable,
        (case, run_index, max_expanded_states),
        timeout_seconds,
    )
    if execution.outcome == "timeout":
        return SolvabilityRun.timeout(case, run_index, execution.wall_clock_ms)
    if execution.outcome == "error":
        return SolvabilityRun.error(
            case,
            run_index,
            execution.error_type or "ChildProcessError",
            execution.error_message or "子进程未返回错误信息",
            execution.wall_clock_ms,
        )
    if not isinstance(execution.value, SolvabilityRun):
        return SolvabilityRun.error(
            case,
            run_index,
            "ChildProcessError",
            "子进程返回了无效的可解性差分结果载荷",
            execution.wall_clock_ms,
        )
    return replace(execution.value, wall_clock_ms=execution.wall_clock_ms)
```

- [ ] **Step 4: 实现顺序批量编排**

严格按 case 顺序、再按 `run_index=1..repetitions` 执行。普通父进程异常转为 `SolvabilityRun.error` 并保留已完成记录；`BenchmarkInfrastructureError` 必须重新抛出。每次回调传 `list(runs)`，不能暴露内部可变列表。

- [ ] **Step 5: 运行隔离器和新运行器测试**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_benchmark_process_isolation.py backend/tests/test_solvability_differential.py -q
```

Expected: PASS；现有进程清理测试也保持通过。

- [ ] **Step 6: 提交批量编排**

```powershell
git add backend/benchmarks/solvability_runner.py backend/tests/test_solvability_differential.py
git commit -m "feat: isolate solvability differential runs"
```

### Task 5: 独立报告与回滚保护

**Files:**
- Create: `backend/benchmarks/solvability_reporting.py`
- Modify: `backend/tests/test_solvability_differential.py`

**Interfaces:**
- Produces: `write_partial_report(output_dir: Path, report: SolvabilityReport) -> None`
- Produces: `write_final_report(output_dir: Path, report: SolvabilityReport) -> None`
- Produces exact final names: `results.json`、`runs.csv`、`case-summaries.csv`。

- [ ] **Step 1: 写编码、字段和 partial/final 生命周期失败测试**

追加一个最小 report 夹具，测试：

```python
import csv
import json
from pathlib import Path

from backend.benchmarks.solvability_reporting import (
    write_final_report,
    write_partial_report,
)
from backend.benchmarks.solvability_results import SolvabilityReport


def test_solvability_report_writes_utf8_json_and_bom_csv(tmp_path) -> None:
    case = _case("catalog-solo-straight")
    run = execute_solvability_case(case, 1, 100_000)
    report = SolvabilityReport.create({"seed": 20260904}, [case], [run])

    write_partial_report(tmp_path, report)
    assert (tmp_path / "results.partial.json").exists()

    write_final_report(tmp_path, report)

    assert not (tmp_path / "results.partial.json").exists()
    assert (tmp_path / "results.json").read_bytes().startswith(b"{")
    assert (tmp_path / "runs.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    assert (tmp_path / "case-summaries.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    payload = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    with (tmp_path / "runs.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["caseId"] == case.case_id
    assert json.loads(rows[0]["oraclePaths"])["R1"][-1] == [2, 0]
```

- [ ] **Step 2: 写最终三文件回滚失败测试**

先分别写入 `b"old results"`、`b"old runs"`、`b"old summaries"` 和 partial，再 monkeypatch `Path.replace`，让发布 `runs.csv` 的临时文件时抛出 `OSError("runs publish failed")`。断言：

```python
assert {
    name: (tmp_path / name).read_bytes()
    for name in ("results.json", "runs.csv", "case-summaries.csv")
} == original_files
assert (tmp_path / "results.partial.json").read_bytes() == partial_content
assert not list(tmp_path.glob("*.tmp"))
```

该测试的 monkeypatch 判定应读取 `source_path.suffix == ".tmp"` 和 `target_path.name == "runs.csv"`，不得依赖临时文件 UUID。

- [ ] **Step 3: 运行并确认报告模块不存在**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_differential.py -q
```

Expected: collection FAIL，错误包含 `solvability_reporting` 不存在。

- [ ] **Step 4: 定义精确 CSV 字段和嵌套 JSON 编码**

`RUN_FIELD_NAMES` 和 `CASE_SUMMARY_FIELD_NAMES` 使用以下精确顺序：

```python
RUN_FIELD_NAMES = (
    "caseId",
    "source",
    "runIndex",
    "width",
    "height",
    "robotCount",
    "obstacleCount",
    "outcome",
    "errorType",
    "errorMessage",
    "oracleOutcome",
    "oracleMakespan",
    "oracleExpandedStateCount",
    "oraclePaths",
    "plannerOutcome",
    "plannerMakespan",
    "plannerReachedAllGoals",
    "plannerFailureCount",
    "plannerFailures",
    "plannerConflictCount",
    "plannerConflicts",
    "plannerPaths",
    "comparisonClass",
    "wallClockMs",
)

CASE_SUMMARY_FIELD_NAMES = (
    "caseId",
    "runCount",
    "completedRunCount",
    "timeoutCount",
    "errorCount",
    "agreementSolvedCount",
    "oracleSolvedPlannerMissCount",
    "oracleUnsolvedPlannerNoValidPlanCount",
    "oracleUnsolvedPlannerSolvedCount",
    "oracleLimitCount",
    "classificationStable",
    "comparisonClass",
    "medianWallClockMs",
    "p95WallClockMs",
)

_JSON_RUN_FIELDS = (
    "oraclePaths",
    "plannerFailures",
    "plannerConflicts",
    "plannerPaths",
)


def _run_records(report: SolvabilityReport) -> list[dict[str, object]]:
    records = []
    for run in report.runs:
        record = run.to_record()
        for field_name in _JSON_RUN_FIELDS:
            record[field_name] = json.dumps(
                record[field_name],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        records.append(record)
    return records
```

写入时精确使用 `csv.DictWriter(handle, fieldnames=field_names, extrasaction="raise")`，防止字段漂移。汇总记录直接使用 `[summary.to_record() for summary in report.case_summaries]`。

- [ ] **Step 5: 实现 partial 原子替换和 final 三文件事务**

以现有 `backend/benchmarks/reporting.py` 的已测试顺序为准，保持以下不可省略的状态：

1. 每个目标使用同一个 `transaction_id` 生成隐藏 `.tmp` 和 `.backup` 路径；
2. 先完整写三份临时文件；任一序列化或写入失败时，错误必须包含最终目标的绝对路径；
3. 按 `results.json`、`runs.csv`、`case-summaries.csv` 顺序备份旧目标；
4. 按同顺序发布新目标；
5. 三份发布完成后删除 `results.partial.json`；
6. 在备份、发布或 partial 删除阶段收到 `Exception` 或 `BaseException` 时，使用已记录状态加文件存在状态协调回滚；
7. 如果 partial 已删除且三份新目标完整存在、三份临时文件均不存在，则视为提交已完成，不回滚新包；
8. 恢复旧文件失败时保留对应 `.backup`，并在新 `OSError` 中同时包含发布错误和回滚错误；
9. `finally` 清理未被有意保留的临时和备份文件。

不要直接导入 `reporting.py` 的下划线私有函数，也不要修改现有算法边界或自适应报告器；本任务以独立模块保持报告类型边界。

- [ ] **Step 6: 运行新旧报告回归**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_differential.py backend/tests/test_algorithm_benchmark.py backend/tests/test_adaptive_replan_calibration.py -q
```

Expected: PASS；三套报告互不覆盖。

- [ ] **Step 7: 提交报告层**

```powershell
git add backend/benchmarks/solvability_reporting.py backend/tests/test_solvability_differential.py
git commit -m "feat: publish solvability differential reports"
```

### Task 6: CLI 与根命令

**Files:**
- Create: `backend/benchmarks/solvability_differential.py`
- Modify: `backend/tests/test_solvability_differential.py`
- Modify: `package.json:12-16`

**Interfaces:**
- Produces: `parse_args(argv: list[str] | None = None) -> argparse.Namespace`
- Produces: `main(argv: list[str] | None = None) -> int`
- Produces root command: `npm run benchmark:solvability -- --sample-count 64 --seed 20260904 --repetitions 1 --max-expanded-states 100000 --timeout-seconds 5`

- [ ] **Step 1: 写默认值、非法参数和时间戳目录测试**

测试精确默认值：

```python
from backend.benchmarks import solvability_differential as cli_module
from backend.benchmarks.solvability_differential import main, parse_args


def test_solvability_cli_has_exact_defaults() -> None:
    args = parse_args([])

    assert args.sample_count == 64
    assert args.seed == 20260904
    assert args.repetitions == 1
    assert args.max_expanded_states == 100_000
    assert args.timeout_seconds == 5
    assert args.output_dir == "output/solvability-differential"
```

参数化验证 `sample-count=-1/513`、`repetitions=0`、`max-expanded-states=0`、`timeout-seconds=0/nan/inf` 均在创建输出目录前返回 `1`。仿照现有 CLI 测试替换模块内 `datetime`，预建同秒目录，断言新目录后缀为 `-2`。

- [ ] **Step 2: 写完整配置和正常发现反例不失败测试**

monkeypatch `run_solvability_cases` 返回一条 `oracleSolvedPlannerMiss`，用本测试传入的完整参数调用 `main`，断言返回值为 `0`、最终三文件存在、partial 不存在，且配置精确为：

```python
{
    "seed": 11,
    "sampleCount": 2,
    "catalogCaseCount": 4,
    "caseCount": 6,
    "repetitions": 1,
    "maxExpandedStates": 500,
    "timeoutSeconds": 3.0,
    "outputDir": str(result_path.resolve()),
    "oracle": {
        "objective": "minimumMakespan",
        "moves": ["wait", "up", "right", "down", "left"],
        "forbidVertexConflicts": True,
        "forbidReverseEdgeConflicts": True,
        "goalSemantics": "visitOnceThenMayReposition",
        "terminalOccupancy": "persistentAtFinalPositions",
    },
    "planner": {
        "entrypoint": "backend.app.dispatch.build_paths",
        "fixedAssignments": True,
        "avoidConflicts": True,
    },
}
```

- [ ] **Step 3: 实现参数、前置校验和目录创建**

在新 CLI 中定义：

```python
DEFAULT_SAMPLE_COUNT = 64
DEFAULT_SEED = 20260904
DEFAULT_REPETITIONS = 1
DEFAULT_MAX_EXPANDED_STATES = 100_000
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_OUTPUT_DIR = "output/solvability-differential"
```

使用 `math.isfinite` 验证 timeout。`sample_count` 范围必须与案例生成器同为 `0..512`。输出目录创建逻辑保持现有 UTC `YYYYMMDDTHHMMSSZ` 与递增后缀规则；目录创建失败的错误包含绝对路径。

- [ ] **Step 4: 实现 partial 回调、最终发布与退出码**

`main` 的固定顺序：

1. parse；
2. 校验全部参数；
3. 使用 `cases = list(solvability_cases(args.seed, args.sample_count))` 生成目录 + 固定种子案例；
4. 创建时间戳结果目录；
5. 构造上一步测试中的 config；
6. 每次回调用当前累计 runs 构造 `SolvabilityReport.create` 并写 partial；
7. 批量结束后写 final；
8. 标准输出只打印最终绝对目录。

配置错误以 `可解性差分配置无效: ` 加具体异常文本打印并返回 `1`；运行或报告异常以 `可解性差分基准失败: ` 加具体异常文本打印并返回 `1`。找到候选反例、参照 `limit` 或普通子进程 `timeout/error` 都保留报告且返回 `0`；只有基础设施或报告流程抛出异常才失败。

- [ ] **Step 5: 新增根脚本**

在 `package.json` 的 benchmark 脚本相邻位置加入精确键值：

```json
"benchmark:solvability": ".\\.venv\\Scripts\\python.exe -m backend.benchmarks.solvability_differential"
```

保持 JSON 逗号和原有脚本顺序有效。

- [ ] **Step 6: 运行 CLI 测试和一次最小真实冒烟**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_oracle.py backend/tests/test_solvability_differential.py -q
```

Expected: tests PASS。

- [ ] **Step 7: 独立重算冒烟报告**

在同一个 PowerShell 调用中捕获 CLI 最后一行返回的精确目录并立即只读重算，不通过通配符猜目录：

```powershell
$solvabilityResultPath = (& npm run benchmark:solvability -- --sample-count 0 --repetitions 1 --max-expanded-states 100000 --timeout-seconds 5 --output-dir output/solvability-differential-smoke | Select-Object -Last 1).Trim()
.\.venv\Scripts\python.exe -c "import csv,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); data=json.loads((p/'results.json').read_text(encoding='utf-8')); runs=list(csv.DictReader((p/'runs.csv').open(encoding='utf-8-sig',newline=''))); summaries=list(csv.DictReader((p/'case-summaries.csv').open(encoding='utf-8-sig',newline=''))); assert len(data['runs'])==len(runs)==4; assert len(data['caseSummaries'])==len(summaries)==4; assert not (p/'results.partial.json').exists(); print({'path':str(p),'runs':len(runs),'summaries':len(summaries),'candidates':data['candidateCounterexampleCaseIds']})" $solvabilityResultPath
```

Expected: 输出真实绝对路径、`runs: 4`、`summaries: 4` 和真实候选 ID 列表；结果目录无 `results.partial.json`。

- [ ] **Step 8: 提交 CLI**

```powershell
git add backend/benchmarks/solvability_differential.py backend/tests/test_solvability_differential.py package.json
git commit -m "feat: add solvability differential benchmark command"
```

### Task 7: 文档、边界声明与全量验证

**Files:**
- Modify: `docs/algorithm.md:271`
- Modify: `docs/experiments.md:253`
- Modify: `docs/testing-guide.md:462`
- Modify: `AGENTS.md:172-218`

**Interfaces:**
- Documents: `npm run benchmark:solvability`
- Documents: `agreementSolved`、`oracleSolvedPlannerMiss`、`oracleUnsolvedPlannerNoValidPlan`、`oracleUnsolvedPlannerSolved`、`oracleLimit`
- Documents: `schemaVersion=1` 和三个报告文件。

- [ ] **Step 1: 更新算法文档，不改变生产能力表述**

在 `docs/algorithm.md` 的“当前边界”前增加“离线小规模可解性参照”小节，必须包含：

- 联合状态 A* 的固定模型和 `1..5`、`25` 格、`1..4` 台硬上限；
- 最大曼哈顿距离启发式和最小 makespan 目标；
- `limit` 不等于无解；
- 生产比较固定分配并调用 `build_paths`；
- 该工具不进入在线会话，不实现 CBS，不改变生产调度保证。

保持当前 `docs/algorithm.md:273-289` 对一级执行安全、二级规划质量和三级未实现保证的原意不变。

- [ ] **Step 2: 更新实验文档和人工检查方法**

在 `docs/experiments.md` 的离线基准部分记录默认命令、参数范围、案例目录、固定种子生成、五类结果、三个文件和 UTF-8 编码。明确：

- `oracleSolvedPlannerMiss` 才是后续局部联合修复候选；
- `oracleUnsolvedPlannerSolved` 先排查语义或适配错误；
- 无旁路一维换位是无解对照，不是漏解；
- 反例是正常研究结果，不导致 CLI 非零退出。

- [ ] **Step 3: 更新测试指南**

在 `docs/testing-guide.md` 的离线基准章节追加：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_solvability_oracle.py backend/tests/test_solvability_differential.py
npm run benchmark:solvability -- --sample-count 64 --seed 20260904 --repetitions 1 --max-expanded-states 100000 --timeout-seconds 5
```

人工检查必须逐项说明如何核对 `results.json` 与两份 CSV 的记录数、分类计数、候选 ID，以及如何确认 `timeout/error/oracleLimit` 未被遗漏。

- [ ] **Step 4: 更新 AGENTS 当前计划状态**

仅在代码和聚焦测试已经通过后修改 `AGENTS.md`：

- 把“下一项具体实现尚未选择”改为已选择并完成离线小规模可解性参照；
- 在核心算法/实验工具状态中记录其严格输入限制、差分目的和输出；
- 把下一候选写成“先人工复核真实 `oracleSolvedPlannerMiss`，再另行确认局部联合修复设计”，不能自动宣布第二阶段已经批准；
- 保留持久化、认证、跨进程一致性、连续物理能耗和中央—边缘协同仍未实现的事实。

- [ ] **Step 5: 运行文档关键词和格式检查**

Run:

```powershell
rg -n --encoding UTF-8 "benchmark:solvability|oracleSolvedPlannerMiss|oracleLimit|完整 MAPF|limit" docs AGENTS.md package.json
git diff --check
```

Expected: 新命令和边界均能定位；`git diff --check` 无输出。

- [ ] **Step 6: 运行完整项目检查**

Run:

```powershell
npm run check
```

Expected: 前端正式构建、全部前端测试和全部后端测试均退出 `0`。记录本轮真实测试总数；不得沿用历史 `219/219` 与 `798 passed, 19 skipped` 代替本轮结果。

- [ ] **Step 7: 检查工作树只包含计划内文件**

Run:

```powershell
git status --short
git diff --stat
```

Expected: 只出现本计划列出的源文件、测试、`package.json` 和四份文档；`output/` 冒烟产物保持忽略且不进入提交。

- [ ] **Step 8: 提交文档和最终验证状态**

```powershell
git add docs/algorithm.md docs/experiments.md docs/testing-guide.md AGENTS.md
git commit -m "docs: document solvability differential benchmark"
```

完成后停在实现分支，不合并、不推送；向用户报告真实测试结果、冒烟输出目录、各分类计数和候选反例 ID。下一阶段只有在用户复核并批准至少一个真实 `oracleSolvedPlannerMiss` 后才进入设计。
