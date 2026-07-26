# Adaptive Replan Window Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline, process-isolated calibration workflow that compares the current adaptive replan window with fixed `4T`, `24T`, and `48T` baselines, records every real online replan, and reports evidence-derived candidate ranges without changing production thresholds.

**Architecture:** Keep the HTTP/API contract unchanged. Add an immutable internal `AdaptiveReplanPolicy` and optional session observer, extract the already-reviewed Windows spawn lifecycle into a shared benchmark helper, then implement a separate adaptive-calibration catalog, runner, result model, reporter, and CLI. Generated reports remain under ignored `output/`; production continues to use the current `60/40ms` and `2×` defaults.

**Tech Stack:** Python 3.13, FastAPI/Pydantic v2 models, pytest, `multiprocessing` with Windows `spawn`, JSON/CSV reporting, Node/npm script entry.

## Global Constraints

- Work only in `D:\codex\summer\.worktrees\adaptive-replan-calibration` on branch `codex/adaptive-replan-calibration`.
- Read and write all text as UTF-8. Use `Get-Content -Encoding UTF8` for Chinese files; do not use `sed` or `awk` on Chinese content.
- Keep `SLOW_REPLAN_ENTER_THRESHOLD_MS = 60`, `SLOW_REPLAN_EXIT_THRESHOLD_MS = 40`, `REPLAN_TIME_SAMPLE_WINDOW = 5`, `MIN_REPLAN_TIME_SAMPLES = 3`, and the production task-pressure multiplier `2` unchanged.
- Do not change any HTTP route, Pydantic API field, frontend type, OpenAPI model, beam score, candidate order, A* behavior, task lock, preemption, charging, recovery, or execution-safety rule.
- Production `create_session(request)` must continue to use default policy and no observer.
- Calibration sessions must keep `enforce_execution_safety=True`.
- Do not add a frontend panel or experiment API.
- Do not add real-wall-clock assertions to pytest.
- Per-run timeout/error is evidence and does not abort the batch; `BenchmarkInfrastructureError` still aborts immediately.
- Never commit `.superpowers/`, `output/`, `.venv`, `frontend/node_modules`, generated CSV/JSON, or Python caches.
- Use `apply_patch` for source and documentation edits.
- Every production-code task follows RED → GREEN and ends in a focused commit.

---

## File and Responsibility Map

### Existing files to modify

- `backend/app/replan_window.py`
  - Owns `AdaptiveReplanPolicy`, default policy, latency median/state calculation, window decision, and `ReplanObservation`.
- `backend/app/sessions.py`
  - Injects internal policy/observer, captures a trace only around a real `run_dispatch`, and preserves default API behavior.
- `backend/benchmarks/runner.py`
  - Translates shared process results back into the existing `BenchmarkRun`; retains its public domain functions.
- `backend/tests/test_replan_window.py`
  - Covers policy validation and default/custom decision behavior.
- `backend/tests/test_sessions.py`
  - Covers observer timing, session policy injection, reset lifecycle, and result invariance.
- `backend/tests/test_algorithm_benchmark.py`
  - Keeps existing boundary-runner behavior tests after process extraction.
- `package.json`
  - Adds only `benchmark:adaptive-replan`.
- `AGENTS.md`
  - Records the implemented calibration boundary and unchanged production thresholds.
- `docs/algorithm.md`
  - Documents observer semantics and why calibration does not prove a universal wall-clock threshold.
- `docs/experiments.md`
  - Documents the new offline command, schema, files, and candidate-envelope interpretation.
- `docs/testing-guide.md`
  - Documents smoke/full commands and manual evidence checks.

### New files to create

- `backend/benchmarks/process_isolation.py`
  - Generic Windows spawn, Pipe, timeout, bounded terminate/kill, and cleanup.
- `backend/benchmarks/adaptive_scenarios.py`
  - Three deterministic calibration cases and runtime task construction.
- `backend/benchmarks/adaptive_variants.py`
  - Four fixed/adaptive comparison variants.
- `backend/benchmarks/adaptive_results.py`
  - Run/observation/summary/report dataclasses and percentile/candidate-envelope calculations.
- `backend/benchmarks/adaptive_runner.py`
  - Online calibration execution, isolation translation, and batch order.
- `backend/benchmarks/adaptive_reporting.py`
  - Atomic partial JSON and final JSON/three-CSV output.
- `backend/benchmarks/adaptive_replan_calibration.py`
  - CLI parsing, validation, timestamp directory, partial updates, and final output.
- `backend/tests/test_benchmark_process_isolation.py`
  - Direct tests for the extracted generic process lifecycle.
- `backend/tests/test_adaptive_replan_calibration.py`
  - Catalog, runner, statistics, reports, and CLI tests.

---

### Task 1: Immutable Adaptive Policy With Exact Default Compatibility

**Files:**
- Modify: `backend/app/replan_window.py`
- Modify: `backend/tests/test_replan_window.py`

**Interfaces:**
- Consumes: existing constants `SLOW_REPLAN_ENTER_THRESHOLD_MS`, `SLOW_REPLAN_EXIT_THRESHOLD_MS`, `REPLAN_TIME_SAMPLE_WINDOW`, and `MIN_REPLAN_TIME_SAMPLES`.
- Produces:
  - `AdaptiveReplanPolicy`
  - `DEFAULT_ADAPTIVE_REPLAN_POLICY`
  - `recent_replan_latency_median(samples_ms: Sequence[float]) -> float | None`
  - `update_latency_slow_state(samples_ms: list[float], current_slow: bool, *, policy: AdaptiveReplanPolicy = DEFAULT_ADAPTIVE_REPLAN_POLICY) -> bool`
  - `decide_replan_window(*, configured_window: int, adaptive: bool, released_task_count: int, future_task_count: int, active_robot_count: int, latency_slow: bool, policy: AdaptiveReplanPolicy = DEFAULT_ADAPTIVE_REPLAN_POLICY) -> ReplanWindowDecision`

- [ ] **Step 1: Add failing imports and default-policy tests**

Append exact tests to `backend/tests/test_replan_window.py`:

```python
import math

from backend.app.replan_window import (
    DEFAULT_ADAPTIVE_REPLAN_POLICY,
    AdaptiveReplanPolicy,
    ReplanWindowDecision,
    recent_replan_latency_median,
)


def test_default_adaptive_policy_preserves_current_constants() -> None:
    assert DEFAULT_ADAPTIVE_REPLAN_POLICY == AdaptiveReplanPolicy(
        slow_enter_threshold_ms=SLOW_REPLAN_ENTER_THRESHOLD_MS,
        slow_exit_threshold_ms=SLOW_REPLAN_EXIT_THRESHOLD_MS,
        task_pressure_multiplier=2,
    )


def test_recent_replan_latency_median_uses_only_latest_five_samples() -> None:
    assert recent_replan_latency_median([1, 2, 30, 40, 50, 60]) == 40
    assert recent_replan_latency_median([]) is None
```

- [ ] **Step 2: Add failing validation and custom-policy tests**

```python
@pytest.mark.parametrize(
    "values",
    [
        {"slow_enter_threshold_ms": math.nan, "slow_exit_threshold_ms": 10, "task_pressure_multiplier": 2},
        {"slow_enter_threshold_ms": 20, "slow_exit_threshold_ms": math.inf, "task_pressure_multiplier": 2},
        {"slow_enter_threshold_ms": 20, "slow_exit_threshold_ms": -1, "task_pressure_multiplier": 2},
        {"slow_enter_threshold_ms": 20, "slow_exit_threshold_ms": 20, "task_pressure_multiplier": 2},
        {"slow_enter_threshold_ms": 20, "slow_exit_threshold_ms": 21, "task_pressure_multiplier": 2},
        {"slow_enter_threshold_ms": 20, "slow_exit_threshold_ms": 10, "task_pressure_multiplier": 0},
    ],
)
def test_adaptive_policy_rejects_invalid_values(values: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        AdaptiveReplanPolicy(**values)


def test_custom_policy_controls_latency_hysteresis() -> None:
    policy = AdaptiveReplanPolicy(
        slow_enter_threshold_ms=30,
        slow_exit_threshold_ms=20,
        task_pressure_multiplier=3,
    )

    assert update_latency_slow_state([30, 30, 30], False, policy=policy) is True
    assert update_latency_slow_state([20, 20, 20], True, policy=policy) is False


def test_custom_policy_controls_task_pressure_threshold() -> None:
    policy = AdaptiveReplanPolicy(
        slow_enter_threshold_ms=60,
        slow_exit_threshold_ms=40,
        task_pressure_multiplier=3,
    )

    balanced = decide_replan_window(
        configured_window=24,
        adaptive=True,
        released_task_count=8,
        future_task_count=0,
        active_robot_count=4,
        latency_slow=False,
        policy=policy,
    )
    pressured = decide_replan_window(
        configured_window=24,
        adaptive=True,
        released_task_count=12,
        future_task_count=0,
        active_robot_count=4,
        latency_slow=False,
        policy=policy,
    )

    assert balanced.window == 24
    assert pressured.window == 12


def test_explicit_default_policy_matches_implicit_default() -> None:
    decision_arguments = {
        "configured_window": 24,
        "adaptive": True,
        "released_task_count": 8,
        "future_task_count": 3,
        "active_robot_count": 4,
        "latency_slow": False,
    }
    assert decide_replan_window(**decision_arguments) == (
        decide_replan_window(
            **decision_arguments,
            policy=DEFAULT_ADAPTIVE_REPLAN_POLICY,
        )
    )
    assert update_latency_slow_state([60, 60, 60], False) == (
        update_latency_slow_state(
            [60, 60, 60],
            False,
            policy=DEFAULT_ADAPTIVE_REPLAN_POLICY,
        )
    )


def test_fixed_window_ignores_custom_policy_and_pressure() -> None:
    decision = decide_replan_window(
        configured_window=24,
        adaptive=False,
        released_task_count=999,
        future_task_count=999,
        active_robot_count=1,
        latency_slow=True,
        policy=AdaptiveReplanPolicy(30, 20, 0.5),
    )
    assert decision == ReplanWindowDecision(
        window=24,
        reason="固定窗口",
    )
```

- [ ] **Step 3: Run the policy tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_replan_window.py -q
```

Expected: collection fails because `AdaptiveReplanPolicy`, `DEFAULT_ADAPTIVE_REPLAN_POLICY`, and `recent_replan_latency_median` do not exist.

- [ ] **Step 4: Implement the immutable policy and median helper**

In `backend/app/replan_window.py`, add:

```python
import math
from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class AdaptiveReplanPolicy:
    slow_enter_threshold_ms: float
    slow_exit_threshold_ms: float
    task_pressure_multiplier: float

    def __post_init__(self) -> None:
        values = (
            self.slow_enter_threshold_ms,
            self.slow_exit_threshold_ms,
            self.task_pressure_multiplier,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("自适应窗口策略值必须为有限数值")
        if self.slow_exit_threshold_ms < 0:
            raise ValueError("慢状态退出阈值不能为负数")
        if self.slow_enter_threshold_ms <= self.slow_exit_threshold_ms:
            raise ValueError("慢状态进入阈值必须大于退出阈值")
        if self.task_pressure_multiplier <= 0:
            raise ValueError("任务压力倍数必须为正数")


DEFAULT_ADAPTIVE_REPLAN_POLICY = AdaptiveReplanPolicy(
    slow_enter_threshold_ms=SLOW_REPLAN_ENTER_THRESHOLD_MS,
    slow_exit_threshold_ms=SLOW_REPLAN_EXIT_THRESHOLD_MS,
    task_pressure_multiplier=2,
)


def recent_replan_latency_median(
    samples_ms: Sequence[float],
) -> float | None:
    recent = list(samples_ms[-REPLAN_TIME_SAMPLE_WINDOW:])
    return median(recent) if recent else None
```

- [ ] **Step 5: Thread the policy through the two existing pure functions**

Replace the two pure functions with:

```python
def update_latency_slow_state(
    samples_ms: list[float],
    current_slow: bool,
    *,
    policy: AdaptiveReplanPolicy = DEFAULT_ADAPTIVE_REPLAN_POLICY,
) -> bool:
    recent = samples_ms[-REPLAN_TIME_SAMPLE_WINDOW:]
    if len(recent) < MIN_REPLAN_TIME_SAMPLES:
        return current_slow
    value = recent_replan_latency_median(recent)
    assert value is not None
    if (
        not current_slow
        and value >= policy.slow_enter_threshold_ms
    ):
        return True
    if current_slow and value <= policy.slow_exit_threshold_ms:
        return False
    return current_slow


def decide_replan_window(
    *,
    configured_window: int,
    adaptive: bool,
    released_task_count: int,
    future_task_count: int,
    active_robot_count: int,
    latency_slow: bool,
    policy: AdaptiveReplanPolicy = DEFAULT_ADAPTIVE_REPLAN_POLICY,
) -> ReplanWindowDecision:
    if not adaptive:
        return ReplanWindowDecision(
            window=configured_window,
            reason="固定窗口",
        )

    baseline = min(
        MAX_ADAPTIVE_REPLAN_WINDOW,
        max(MIN_ADAPTIVE_REPLAN_WINDOW, configured_window),
    )
    contracted = max(
        MIN_ADAPTIVE_REPLAN_WINDOW,
        baseline // 2,
    )
    expanded = min(
        MAX_ADAPTIVE_REPLAN_WINDOW,
        baseline * 2,
    )
    if latency_slow:
        return ReplanWindowDecision(
            window=contracted,
            reason="近期规划耗时中位数较高，收缩窗口",
        )

    robot_count = max(1, active_robot_count)
    if (
        released_task_count
        >= robot_count * policy.task_pressure_multiplier
    ):
        return ReplanWindowDecision(
            window=contracted,
            reason="当前任务压力较高，收缩窗口",
        )
    if released_task_count == 0 and future_task_count > 0:
        return ReplanWindowDecision(
            window=expanded,
            reason="当前负载较低且存在远期任务，扩大窗口",
        )
    return ReplanWindowDecision(
        window=baseline,
        reason="当前负载适中，保持基准窗口",
    )
```

- [ ] **Step 6: Run focused GREEN and existing adaptive regressions**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_replan_window.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py -q -k "adaptive or replan_sample or latency"
```

Expected: all selected tests pass; existing `60/40ms` and fixed-window assertions remain unchanged.

- [ ] **Step 7: Commit Task 1**

```powershell
git add -- backend/app/replan_window.py backend/tests/test_replan_window.py
git diff --cached --check
git commit -m "refactor: make adaptive replan policy explicit"
```

---

### Task 2: Record Only Real Online Replans

**Files:**
- Modify: `backend/app/replan_window.py`
- Modify: `backend/app/sessions.py`
- Modify: `backend/tests/test_sessions.py`

**Interfaces:**
- Consumes:
  - `AdaptiveReplanPolicy`
  - `DEFAULT_ADAPTIVE_REPLAN_POLICY`
  - `recent_replan_latency_median`
  - `PlanningDiagnostics`
- Produces:
  - `ReplanObservation`
  - `DispatchSession.adaptive_replan_policy`
  - `DispatchSession.replan_observer`
  - internal `create_session(request: CreateSessionRequest, *, enforce_execution_safety: bool = True, adaptive_replan_policy: AdaptiveReplanPolicy = DEFAULT_ADAPTIVE_REPLAN_POLICY, replan_observer: Callable[[ReplanObservation], None] | None = None) -> SessionResult`

- [ ] **Step 1: Define failing observer lifecycle tests**

Add these imports in `backend/tests/test_sessions.py`:

```python
from backend.app.replan_window import (
    DEFAULT_ADAPTIVE_REPLAN_POLICY,
    AdaptiveReplanPolicy,
    ReplanObservation,
)
from backend.app.schemas import (
    AddTaskRequest,
    CreateSessionRequest,
)
```

Add:

```python
def test_replan_observer_records_initial_and_runtime_replan_only() -> None:
    scenario = Scenario.model_validate(scenario_payload())
    observations: list[ReplanObservation] = []
    created = sessions_module.create_session(
        CreateSessionRequest(
            scenario=scenario,
            options=DispatchOptions(
                avoidConflicts=True,
                includeDynamic=False,
                assignmentReplanWindow=24,
                adaptiveReplanWindow=True,
            ),
        ),
        replan_observer=observations.append,
    )
    session_id = created.sessionId
    try:
        assert [item.time for item in observations] == [0]

        sessions_module.get_session(session_id)
        sessions_module.tick_session(
            session_id,
            sessions_module.SessionTickRequest(currentTime=1),
        )
        assert [item.time for item in observations] == [0]

        sessions_module.add_task(
            session_id,
            AddTaskRequest(
                task=Task(
                    id="OBSERVER-TASK",
                    type="emergency",
                    title="OBSERVER-TASK",
                    priority=5,
                    releaseTime=1,
                    deadline=20,
                    target=scenario.zones.inspection[0],
                )
            ),
        )
        assert [item.time for item in observations] == [0, 1]
    finally:
        sessions_module.delete_session(session_id)
```

- [ ] **Step 2: Add failing field and custom-policy tests**

```python
def test_replan_observation_contains_decision_inputs_and_planning_work() -> None:
    scenario = Scenario.model_validate(scenario_payload())
    observations: list[ReplanObservation] = []
    created = sessions_module.create_session(
        CreateSessionRequest(
            scenario=scenario,
            options=DispatchOptions(
                avoidConflicts=True,
                includeDynamic=False,
                assignmentReplanWindow=24,
                adaptiveReplanWindow=True,
            ),
        ),
        replan_observer=observations.append,
    )
    try:
        observation = observations[0]
        assert observation.time == 0
        assert observation.configured_window == 24
        assert observation.effective_window in {12, 24, 48}
        assert observation.released_task_count >= 0
        assert observation.future_task_count >= 0
        assert observation.active_robot_count == len(scenario.robots)
        assert observation.task_pressure_ratio == (
            observation.released_task_count / max(1, observation.active_robot_count)
        )
        assert observation.latency_samples_before_ms == ()
        assert observation.latency_median_before_ms is None
        assert observation.latency_slow_before is False
        assert observation.replan_time_ms >= 0
        assert observation.path_candidate_count >= 1
        assert observation.timed_astar_call_count >= 0
        assert observation.timed_astar_expanded_state_count >= 0
    finally:
        sessions_module.delete_session(created.sessionId)


def test_session_uses_injected_policy_without_changing_http_contract() -> None:
    scenario = Scenario.model_validate(scenario_payload())
    policy = AdaptiveReplanPolicy(
        slow_enter_threshold_ms=30,
        slow_exit_threshold_ms=20,
        task_pressure_multiplier=1,
    )
    created = sessions_module.create_session(
        CreateSessionRequest(
            scenario=scenario,
            options=DispatchOptions(
                avoidConflicts=True,
                includeDynamic=False,
                assignmentReplanWindow=24,
                adaptiveReplanWindow=True,
            ),
        ),
        adaptive_replan_policy=policy,
    )
    try:
        with sessions_module._locked_session(created.sessionId, touch_access=False) as session:
            assert session.adaptive_replan_policy == policy
    finally:
        sessions_module.delete_session(created.sessionId)
```

- [ ] **Step 3: Add failing result-invariance and reset tests**

```python
def test_replan_observer_does_not_change_dispatch_payload_except_timing() -> None:
    request = CreateSessionRequest(
        scenario=Scenario.model_validate(scenario_payload()),
        options=DispatchOptions(avoidConflicts=True, includeDynamic=False),
    )
    observations: list[ReplanObservation] = []
    without_observer = sessions_module.create_session(request)
    with_observer = sessions_module.create_session(
        request,
        replan_observer=observations.append,
    )
    try:
        first = without_observer.result.model_dump(mode="json")
        second = with_observer.result.model_dump(mode="json")
        first["metrics"].pop("replanTimeMs")
        second["metrics"].pop("replanTimeMs")
        assert first == second
        assert observations
    finally:
        sessions_module.delete_session(without_observer.sessionId)
        sessions_module.delete_session(with_observer.sessionId)


def test_session_without_observer_does_not_create_planning_diagnostics(
    monkeypatch,
) -> None:
    def fail_if_constructed():
        raise AssertionError(
            "生产会话不应创建离线校准规划诊断"
        )

    monkeypatch.setattr(
        sessions_module,
        "PlanningDiagnostics",
        fail_if_constructed,
    )
    created = sessions_module.create_session(
        CreateSessionRequest(
            scenario=Scenario.model_validate(scenario_payload()),
            options=DispatchOptions(
                avoidConflicts=True,
                includeDynamic=False,
            ),
        )
    )
    sessions_module.delete_session(created.sessionId)


def test_reset_preserves_internal_policy_and_observer_but_clears_latency_state() -> None:
    observations: list[ReplanObservation] = []
    observer = observations.append
    policy = AdaptiveReplanPolicy(30, 20, 3)
    created = sessions_module.create_session(
        CreateSessionRequest(
            scenario=Scenario.model_validate(scenario_payload()),
            options=DispatchOptions(adaptiveReplanWindow=True),
        ),
        adaptive_replan_policy=policy,
        replan_observer=observer,
    )
    try:
        before = len(observations)
        sessions_module.reset_session(created.sessionId)
        with sessions_module._locked_session(created.sessionId, touch_access=False) as session:
            assert session.adaptive_replan_policy == policy
            assert session.replan_observer is observer
            assert session.recent_replan_times_ms
            assert len(observations) == before + 1
    finally:
        sessions_module.delete_session(created.sessionId)
```

The post-reset `recent_replan_times_ms` assertion is non-empty because reset clears the old samples and immediately builds the new `T=0` plan, which records the new plan latency.

- [ ] **Step 4: Run observer tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py -q -k "replan_observer or injected_policy or reset_preserves_internal"
```

Expected: collection or calls fail because `ReplanObservation`, the observer arguments, and session policy fields do not exist.

- [ ] **Step 5: Add `ReplanObservation`**

In `backend/app/replan_window.py` add the exact dataclass from the approved design:

```python
@dataclass(frozen=True, slots=True)
class ReplanObservation:
    time: int
    configured_window: int
    effective_window: int
    reason: str
    released_task_count: int
    future_task_count: int
    active_robot_count: int
    task_pressure_ratio: float
    latency_samples_before_ms: tuple[float, ...]
    latency_median_before_ms: float | None
    latency_slow_before: bool
    replan_time_ms: float
    latency_slow_after: bool
    path_candidate_count: int
    selected_path_candidate_index: int | None
    failed_path_candidate_count: int
    timed_astar_call_count: int
    timed_astar_expanded_state_count: int
    max_timed_astar_expanded_state_count: int
    timed_astar_exhausted_search_count: int
    timed_astar_goal_fully_reserved_reject_count: int
```

- [ ] **Step 6: Add internal session policy and observer fields**

In `backend/app/sessions.py`:

```python
from collections.abc import Callable, Iterator
```

Import from `replan_window`:

```python
AdaptiveReplanPolicy
DEFAULT_ADAPTIVE_REPLAN_POLICY
ReplanObservation
recent_replan_latency_median
```

Import:

```python
from backend.app.planning_diagnostics import PlanningDiagnostics
```

Add to `DispatchSession`:

```python
adaptive_replan_policy: AdaptiveReplanPolicy = DEFAULT_ADAPTIVE_REPLAN_POLICY
replan_observer: Callable[[ReplanObservation], None] | None = field(
    default=None,
    repr=False,
    compare=False,
)
```

Change `create_session`:

```python
def create_session(
    request: CreateSessionRequest,
    *,
    enforce_execution_safety: bool = True,
    adaptive_replan_policy: AdaptiveReplanPolicy = DEFAULT_ADAPTIVE_REPLAN_POLICY,
    replan_observer: Callable[[ReplanObservation], None] | None = None,
) -> SessionResult:
```

Pass these exact constructor arguments into `DispatchSession(...)`:

```python
adaptive_replan_policy=adaptive_replan_policy,
replan_observer=replan_observer,
```

- [ ] **Step 7: Replace the private decision helper with an evaluation**

Add in `backend/app/sessions.py`:

```python
@dataclass(frozen=True, slots=True)
class _SessionReplanEvaluation:
    decision: ReplanWindowDecision
    released_task_count: int
    future_task_count: int
    active_robot_count: int
```

Add:

```python
def _session_replan_window_evaluation(
    session: DispatchSession,
) -> _SessionReplanEvaluation:
    tasks = [
        task
        for task in _all_tasks(session)
        if task.id not in session.completed_task_ids
    ]
    released_task_count = sum(
        1
        for task in tasks
        if _session_task_release_time(session, task) <= session.current_time
    )
    active_dynamic_failed = (
        session.scenario.dynamic.failedRobots
        if _is_scenario_dynamic_active(session)
        else []
    )
    unavailable_robot_ids = _merge_text(
        active_dynamic_failed,
        session.runtime_failed_robot_ids,
    )
    future_task_count = len(tasks) - released_task_count
    active_robot_count = (
        len(session.scenario.robots) - len(unavailable_robot_ids)
    )
    decision = decide_replan_window(
        configured_window=session.options.assignmentReplanWindow,
        adaptive=session.options.adaptiveReplanWindow,
        released_task_count=released_task_count,
        future_task_count=future_task_count,
        active_robot_count=active_robot_count,
        latency_slow=session.adaptive_latency_slow,
        policy=session.adaptive_replan_policy,
    )
    return _SessionReplanEvaluation(
        decision=decision,
        released_task_count=released_task_count,
        future_task_count=future_task_count,
        active_robot_count=active_robot_count,
    )
```

Use this evaluation in `_build_result`; do not keep two independent decision calculations.

Preserve the existing private compatibility wrapper because current session regressions call it directly:

```python
def _session_replan_window_decision(
    session: DispatchSession,
) -> ReplanWindowDecision:
    return _session_replan_window_evaluation(session).decision
```

`_build_result` uses the full evaluation. Existing callers that only require the decision continue through the wrapper, so the decision logic still has one implementation.

- [ ] **Step 8: Capture the observation around the real dispatch call**

In the `result is None` branch of `_build_result`:

```python
evaluation = _session_replan_window_evaluation(session)
samples_before = tuple(session.recent_replan_times_ms)
slow_before = session.adaptive_latency_slow
planning_diagnostics = (
    PlanningDiagnostics()
    if session.replan_observer is not None
    else None
)
```

Pass:

```python
replan_window_decision=evaluation.decision,
planning_diagnostics=planning_diagnostics,
```

After `_record_replan_window_decision` and `_record_replan_latency`, call a new helper only when diagnostics and observer are non-null:

```python
_notify_replan_observer(
    session,
    evaluation,
    samples_before,
    slow_before,
    result.metrics.replanTimeMs,
    planning_diagnostics,
)
```

Update `_record_replan_latency` so the injected internal policy controls its slow-state transition:

```python
session.adaptive_latency_slow = update_latency_slow_state(
    session.recent_replan_times_ms,
    session.adaptive_latency_slow,
    policy=session.adaptive_replan_policy,
)
```

Add the exact helper; do not catch observer exceptions:

```python
def _notify_replan_observer(
    session: DispatchSession,
    evaluation: _SessionReplanEvaluation,
    samples_before: tuple[float, ...],
    slow_before: bool,
    replan_time_ms: float,
    diagnostics: PlanningDiagnostics,
) -> None:
    observer = session.replan_observer
    if observer is None:
        return
    observer(
        ReplanObservation(
            time=session.current_time,
            configured_window=(
                session.options.assignmentReplanWindow
            ),
            effective_window=evaluation.decision.window,
            reason=evaluation.decision.reason,
            released_task_count=evaluation.released_task_count,
            future_task_count=evaluation.future_task_count,
            active_robot_count=evaluation.active_robot_count,
            task_pressure_ratio=(
                evaluation.released_task_count
                / max(1, evaluation.active_robot_count)
            ),
            latency_samples_before_ms=samples_before,
            latency_median_before_ms=recent_replan_latency_median(
                samples_before
            ),
            latency_slow_before=slow_before,
            replan_time_ms=replan_time_ms,
            latency_slow_after=session.adaptive_latency_slow,
            path_candidate_count=diagnostics.path_candidate_count,
            selected_path_candidate_index=(
                diagnostics.selected_path_candidate_index
            ),
            failed_path_candidate_count=(
                diagnostics.failed_path_candidate_count
            ),
            timed_astar_call_count=diagnostics.timed_astar_call_count,
            timed_astar_expanded_state_count=(
                diagnostics.timed_astar_expanded_state_count
            ),
            max_timed_astar_expanded_state_count=(
                diagnostics.max_timed_astar_expanded_state_count
            ),
            timed_astar_exhausted_search_count=(
                diagnostics.timed_astar_exhausted_search_count
            ),
            timed_astar_goal_fully_reserved_reject_count=(
                diagnostics.timed_astar_goal_fully_reserved_reject_count
            ),
        )
    )
```

- [ ] **Step 9: Run GREEN and API-contract regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py -q -k "replan_observer or injected_policy or reset_preserves_internal or adaptive or latency"
.\.venv\Scripts\python.exe -m pytest backend\tests\test_api_contract.py -q
```

Expected: all selected tests pass; no frontend or OpenAPI field is added.

- [ ] **Step 10: Commit Task 2**

```powershell
git add -- backend/app/replan_window.py backend/app/sessions.py backend/tests/test_sessions.py
git diff --cached --check
git commit -m "feat: observe real online replans"
```

---

### Task 3: Extract the Reviewed Windows Spawn Lifecycle

**Files:**
- Create: `backend/benchmarks/process_isolation.py`
- Create: `backend/tests/test_benchmark_process_isolation.py`
- Modify: `backend/benchmarks/runner.py`
- Modify: `backend/tests/test_algorithm_benchmark.py`

**Interfaces:**
- Produces:
  - `BenchmarkInfrastructureError`
  - `IsolatedExecution`
  - `sanitize_error_text(text: str) -> str`
  - `run_isolated_process(worker_callable, worker_args, timeout_seconds) -> IsolatedExecution`
- Preserves:
  - `runner.run_isolated_case(case: BenchmarkCase, run_index: int, timeout_seconds: float, worker_callable: Callable[[str, int], BenchmarkRun] = execute_benchmark_case) -> BenchmarkRun`
  - `runner.BenchmarkInfrastructureError`
  - all existing boundary timeout/error semantics

- [ ] **Step 1: Add generic helper tests for completed/error/timeout**

Create `backend/tests/test_benchmark_process_isolation.py` with module-level pickleable workers:

```python
import multiprocessing
import time

import pytest

from backend.benchmarks import process_isolation as isolation_module
from backend.benchmarks.process_isolation import (
    BenchmarkInfrastructureError,
    run_isolated_process,
)


def _return_value(value: str) -> str:
    return value


def _raise_error() -> str:
    raise RuntimeError("worker failed\r\nwith detail")


def _sleep() -> None:
    time.sleep(2)


class _UnpicklableArgument:
    def __reduce__(self):
        raise RuntimeError("worker argument serialization failed")


def _active_child_pids() -> set[int]:
    return {
        process.pid
        for process in multiprocessing.active_children()
        if process.pid is not None
    }


def test_shared_isolation_returns_completed_value() -> None:
    result = run_isolated_process(_return_value, ("ok",), 5)
    assert result.outcome == "completed"
    assert result.value == "ok"
    assert result.error_type is None
    assert result.wall_clock_ms >= 0


def test_shared_isolation_sanitizes_worker_error() -> None:
    result = run_isolated_process(_raise_error, (), 5)
    assert result.outcome == "error"
    assert result.error_type == "RuntimeError"
    assert result.error_message == "worker failedwith detail"


def test_shared_isolation_times_out_without_worker_residue() -> None:
    children_before = _active_child_pids()
    result = run_isolated_process(_sleep, (), 0.05)
    assert result.outcome == "timeout"
    assert result.error_type == "TimeoutError"
    assert _active_child_pids() <= children_before


def test_shared_isolation_validates_worker_and_argument_tuple_before_spawn() -> None:
    children_before = _active_child_pids()
    result = run_isolated_process(
        _return_value,
        (_UnpicklableArgument(),),
        5,
    )
    assert result.outcome == "error"
    assert result.error_type == "RuntimeError"
    assert result.error_message == "worker argument serialization failed"
    assert _active_child_pids() <= children_before
```

- [ ] **Step 2: Move the existing fake-process reliability cases to the shared module**

Copy the exact fake `Connection`, `Process`, and `Context` structures currently used by:

- `test_isolated_algorithm_benchmark_cleans_resources_after_start_error`
- `test_isolated_algorithm_benchmark_uses_kill_fallback_after_terminate_error`
- `test_isolated_algorithm_benchmark_propagates_unstoppable_child_cleanup_failure`

into the new test file. Change monkeypatch targets from:

```python
runner_module.multiprocessing
runner_module._guarded_worker_entry
```

to:

```python
isolation_module.multiprocessing
isolation_module._guarded_worker_entry
```

The assertions remain exact:

```python
# start() failure case
result = run_isolated_process(_return_value, ("ok",), 5)
assert result.outcome == "error"
assert result.error_type == "RuntimeError"
assert result.error_message == "process start failed"
assert context.process.closed is True
assert context.parent_connection.closed is True
assert context.child_connection.closed is True

# terminate failure / kill fallback case
result = run_isolated_process(_return_value, ("ok",), 0.01)
assert result.outcome == "timeout"
assert context.process.kill_called is True
assert all(timeout > 0 for timeout in context.process.join_timeouts)

# unstoppable process case
with pytest.raises(
    BenchmarkInfrastructureError,
    match="无法确认基准子进程已停止",
):
    run_isolated_process(_return_value, ("ok",), 0.01)
```

- [ ] **Step 3: Run the new test file and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_benchmark_process_isolation.py -q
```

Expected: collection fails because `backend.benchmarks.process_isolation` does not exist.

- [ ] **Step 4: Create the shared result and error types**

In `backend/benchmarks/process_isolation.py`:

```python
from dataclasses import dataclass
from typing import Literal


IsolatedOutcome = Literal["completed", "timeout", "error"]
_PROCESS_STOP_TIMEOUT_SECONDS = 1.0


class BenchmarkInfrastructureError(RuntimeError):
    """父进程无法可靠管理基准子进程时抛出。"""


@dataclass(frozen=True, slots=True)
class IsolatedExecution:
    outcome: IsolatedOutcome
    value: object | None
    error_type: str | None
    error_message: str | None
    wall_clock_ms: float


def sanitize_error_text(text: str) -> str:
    return text.replace("\r", "").replace("\n", "")[:500]
```

- [ ] **Step 5: Move the lifecycle implementation without semantic edits**

Move from `backend/benchmarks/runner.py` into the new module:

- `_write_traceback`
- `_cleanup_isolated_resources`
- `_guarded_worker_entry`
- Pipe creation
- `spawn` process creation
- worker and argument tuple pickle precheck through `pickle.dumps((worker_callable, worker_args))`
- parent `poll` before `recv`
- bounded join/terminate/kill cleanup

Use this exact public signature:

```python
def run_isolated_process(
    worker_callable: Callable[..., object],
    worker_args: tuple[object, ...],
    timeout_seconds: float,
) -> IsolatedExecution:
```

The guarded child call is:

```python
worker_callable(*worker_args)
```

Return:

```python
IsolatedExecution(
    outcome="completed",
    value=payload_value,
    error_type=None,
    error_message=None,
    wall_clock_ms=wall_clock_ms,
)
```

or the corresponding timeout/error record. Cleanup failures still raise `BenchmarkInfrastructureError`.

- [ ] **Step 6: Adapt the existing boundary wrapper**

In `backend/benchmarks/runner.py`, import:

```python
from backend.benchmarks.process_isolation import (
    BenchmarkInfrastructureError,
    run_isolated_process,
    sanitize_error_text,
)
```

Keep `BenchmarkInfrastructureError` imported at module scope so existing `runner_module.BenchmarkInfrastructureError` callers continue to work.

Replace `run_isolated_case` internals with:

```python
execution = run_isolated_process(
    worker_callable,
    (case.case_id, run_index),
    timeout_seconds,
)
if execution.outcome == "timeout":
    return BenchmarkRun.timeout(case, run_index, execution.wall_clock_ms)
if execution.outcome == "error":
    return BenchmarkRun.error(
        case,
        run_index,
        execution.error_type or "ChildProcessError",
        execution.error_message or "子进程未返回错误信息",
        execution.wall_clock_ms,
    )
if not isinstance(execution.value, BenchmarkRun):
    return BenchmarkRun.error(
        case,
        run_index,
        "ChildProcessError",
        "子进程返回了无效的基准结果载荷",
        execution.wall_clock_ms,
    )
return replace(execution.value, wall_clock_ms=execution.wall_clock_ms)
```

Keep `run_benchmark_cases` control flow unchanged. In its ordinary parent-exception branch, replace the migrated private traceback helper with:

```python
traceback.print_exc()
run = BenchmarkRun.error(
    case,
    run_index,
    type(exc).__name__,
    sanitize_error_text(str(exc)),
    round((perf_counter() - started_at) * 1000, 2),
)
```

The shared module keeps its own private `_write_traceback` for child/cleanup paths; `runner.py` must not reference that private name after extraction.

- [ ] **Step 7: Remove only migrated private-process tests from the old test file**

In `backend/tests/test_algorithm_benchmark.py`:

- Keep domain wrapper tests for timeout, worker error, sanitized error, unpicklable worker, batch continuation, and infrastructure propagation.
- Remove fake-context assertions that inspect `runner_module._guarded_worker_entry` or `runner_module.multiprocessing`; those now live in `test_benchmark_process_isolation.py`.
- Do not remove boundary report, scenario, planning diagnostics, online safety, or CLI tests.

- [ ] **Step 8: Run shared and legacy GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_benchmark_process_isolation.py backend\tests\test_algorithm_benchmark.py -q
```

Expected: all tests pass, including large-error payload, kill fallback, and no-residue checks.

- [ ] **Step 9: Commit Task 3**

```powershell
git add -- backend/benchmarks/process_isolation.py backend/benchmarks/runner.py backend/tests/test_benchmark_process_isolation.py backend/tests/test_algorithm_benchmark.py
git diff --cached --check
git commit -m "refactor: share benchmark process isolation"
```

---

### Task 4: Deterministic Calibration Cases and Variants

**Files:**
- Create: `backend/benchmarks/adaptive_scenarios.py`
- Create: `backend/benchmarks/adaptive_variants.py`
- Create: `backend/tests/test_adaptive_replan_calibration.py`

**Interfaces:**
- Produces:
  - `AdaptiveCalibrationCase`
  - `adaptive_calibration_cases(case_ids=None)`
  - `build_adaptive_calibration_scenario(case_id)`
  - `build_calibration_runtime_task(case_id, scenario, current_time)`
  - `AdaptiveCalibrationVariant`
  - `adaptive_calibration_variants(variant_ids=None)`
  - `AdaptiveCalibrationVariant.options()`

- [ ] **Step 1: Add failing exact-catalog tests**

Create `backend/tests/test_adaptive_replan_calibration.py`:

```python
import pytest

from backend.app.dispatch import task_waypoints
from backend.app.schemas import Scenario
from backend.app.validation import validate_scenario
from backend.benchmarks.adaptive_scenarios import (
    RUNTIME_TASK_TICKS,
    adaptive_calibration_cases,
    build_adaptive_calibration_scenario,
    build_calibration_runtime_task,
)
from backend.benchmarks.adaptive_variants import adaptive_calibration_variants
from backend.benchmarks.scenarios import (
    benchmark_cases,
    build_benchmark_scenario,
)


def test_adaptive_calibration_case_catalog_is_exact() -> None:
    cases = adaptive_calibration_cases()
    assert [
        (case.case_id, case.source_case_id, case.robot_count, case.task_count, case.tick_target)
        for case in cases
    ] == [
        ("adaptive-low-load-r4-t17", "scale-r4-t15", 4, 17, 120),
        ("adaptive-pressure-r8-t45", "density-r8-t43", 8, 45, 120),
        ("adaptive-transition-r4-t6", "bottleneck-r4-t4", 4, 6, 120),
    ]


def test_adaptive_calibration_variant_catalog_is_exact() -> None:
    variants = adaptive_calibration_variants()
    assert [
        (
            item.variant_id,
            item.assignment_replan_window,
            item.adaptive_replan_window,
        )
        for item in variants
    ] == [
        ("fixed-4", 4, False),
        ("fixed-24", 24, False),
        ("fixed-48", 48, False),
        ("adaptive-current-24", 24, True),
    ]
```

- [ ] **Step 2: Add failing deterministic scenario tests**

```python
@pytest.mark.parametrize(
    "case_id",
    [
        "adaptive-low-load-r4-t17",
        "adaptive-pressure-r8-t45",
        "adaptive-transition-r4-t6",
    ],
)
def test_adaptive_calibration_scenarios_are_valid_and_deterministic(case_id: str) -> None:
    first = build_adaptive_calibration_scenario(case_id)
    second = build_adaptive_calibration_scenario(case_id)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert Scenario.model_validate(
        first.model_dump(mode="json")
    ).model_dump(mode="json") == first.model_dump(mode="json")
    assert validate_scenario(
        first,
        adaptive_calibration_variants()[0].options(),
    ) == []
    tasks = first.tasks + first.dynamic.tasks
    case = next(
        item
        for item in adaptive_calibration_cases()
        if item.case_id == case_id
    )
    assert len(tasks) == case.task_count - len(RUNTIME_TASK_TICKS)
    assert len({task.id for task in tasks}) == len(tasks)
    assert first.zones.inspection or first.zones.delivery
    for task in tasks:
        if task.deadline is not None:
            assert task.releaseTime is not None
            assert task.releaseTime <= task.deadline
        for x, y in task_waypoints(task):
            assert 0 <= x < first.width
            assert 0 <= y < first.height
            assert (x, y) not in first.obstacles


def test_low_load_case_has_no_released_task_at_t0() -> None:
    scenario = build_adaptive_calibration_scenario("adaptive-low-load-r4-t17")
    assert [task.releaseTime for task in scenario.tasks] == [
        48 + index % 3 for index in range(12)
    ]
    assert scenario.dynamic.triggerTime == 72


def test_pressure_case_preserves_seeded_release_schedule() -> None:
    scenario = build_adaptive_calibration_scenario("adaptive-pressure-r8-t45")
    assert len(scenario.tasks) == 40
    assert len(scenario.dynamic.tasks) == 3
    assert [task.releaseTime for task in scenario.tasks[:7]] == [0, 1, 2, 3, 4, 5, 0]


def test_transition_case_has_two_current_and_two_future_tasks() -> None:
    scenario = build_adaptive_calibration_scenario("adaptive-transition-r4-t6")
    assert {task.id: task.releaseTime for task in scenario.tasks} == {
        "D1": 0,
        "D2": 0,
        "D3": 48,
        "D4": 48,
    }
```

- [ ] **Step 3: Add failing runtime-task and filter tests**

```python
@pytest.mark.parametrize("current_time", [20, 40])
def test_calibration_runtime_task_uses_exact_id_and_valid_target(current_time: int) -> None:
    case_id = "adaptive-transition-r4-t6"
    scenario = build_adaptive_calibration_scenario(case_id)
    task = build_calibration_runtime_task(case_id, scenario, current_time)
    assert task.id == f"{case_id}-runtime-{current_time}"
    assert task.type == "emergency"
    assert task.releaseTime == current_time
    assert task.deadline == current_time + 40
    assert task.priority == 5
    assert task.target == scenario.zones.delivery[0]


def test_calibration_catalog_filters_in_catalog_order() -> None:
    assert [
        case.case_id
        for case in adaptive_calibration_cases(
            ("adaptive-transition-r4-t6", "adaptive-low-load-r4-t17")
        )
    ] == [
        "adaptive-low-load-r4-t17",
        "adaptive-transition-r4-t6",
    ]
    assert [
        variant.variant_id
        for variant in adaptive_calibration_variants(("fixed-48", "fixed-4"))
    ] == ["fixed-4", "fixed-48"]


@pytest.mark.parametrize(
    ("function", "values"),
    [
        (adaptive_calibration_cases, ()),
        (adaptive_calibration_cases, ("unknown",)),
        (adaptive_calibration_variants, ()),
        (adaptive_calibration_variants, ("unknown",)),
    ],
)
def test_calibration_catalog_rejects_empty_or_unknown_filters(function, values) -> None:
    with pytest.raises(ValueError):
        function(values)


def test_original_algorithm_boundary_catalog_is_unchanged() -> None:
    assert [case.case_id for case in benchmark_cases()] == [
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


@pytest.mark.parametrize(
    ("calibration_case_id", "source_case_id"),
    [
        ("adaptive-low-load-r4-t17", "scale-r4-t15"),
        ("adaptive-pressure-r8-t45", "density-r8-t43"),
        ("adaptive-transition-r4-t6", "bottleneck-r4-t4"),
    ],
)
def test_calibration_transform_does_not_mutate_source_scenario(
    calibration_case_id: str,
    source_case_id: str,
) -> None:
    before = build_benchmark_scenario(source_case_id).model_dump(
        mode="json"
    )
    build_adaptive_calibration_scenario(calibration_case_id)
    after = build_benchmark_scenario(source_case_id).model_dump(
        mode="json"
    )
    assert after == before
```

- [ ] **Step 4: Run catalog tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_adaptive_replan_calibration.py -q
```

Expected: collection fails because the two calibration modules do not exist.

- [ ] **Step 5: Implement the case catalog and builders**

In `backend/benchmarks/adaptive_scenarios.py`:

```python
from dataclasses import dataclass

from backend.app.schemas import Scenario, Task
from backend.benchmarks.scenarios import build_benchmark_scenario


RUNTIME_TASK_TICKS = (20, 40)


@dataclass(frozen=True, slots=True)
class AdaptiveCalibrationCase:
    case_id: str
    source_case_id: str
    robot_count: int
    task_count: int
    tick_target: int


_CASES = (
    AdaptiveCalibrationCase(
        "adaptive-low-load-r4-t17",
        "scale-r4-t15",
        4,
        17,
        120,
    ),
    AdaptiveCalibrationCase(
        "adaptive-pressure-r8-t45",
        "density-r8-t43",
        8,
        45,
        120,
    ),
    AdaptiveCalibrationCase(
        "adaptive-transition-r4-t6",
        "bottleneck-r4-t4",
        4,
        6,
        120,
    ),
)


def adaptive_calibration_cases(
    case_ids: tuple[str, ...] | None = None,
) -> tuple[AdaptiveCalibrationCase, ...]:
    if case_ids is None:
        return _CASES
    if not case_ids:
        raise ValueError("校准案例必须至少包含一项")
    known = {case.case_id for case in _CASES}
    unknown = sorted(set(case_ids) - known)
    if unknown:
        raise ValueError(f"未知校准案例: {', '.join(unknown)}")
    return tuple(case for case in _CASES if case.case_id in case_ids)
```

Implement the approved transformations without changing the source catalog:

```python
def build_adaptive_calibration_scenario(case_id: str) -> Scenario:
    case = next(
        (item for item in _CASES if item.case_id == case_id),
        None,
    )
    if case is None:
        raise KeyError(f"未知校准案例: {case_id}")
    scenario = build_benchmark_scenario(
        case.source_case_id
    ).model_copy(deep=True)
    scenario.id = case.case_id
    scenario.name = case.case_id
    scenario.description = f"自适应重规划窗口校准场景：{case.case_id}"

    if case.case_id == "adaptive-low-load-r4-t17":
        for index, task in enumerate(scenario.tasks):
            task.releaseTime = 48 + index % 3
        scenario.dynamic.triggerTime = 72
    elif case.case_id == "adaptive-transition-r4-t6":
        release_times = {
            "D1": 0,
            "D2": 0,
            "D3": 48,
            "D4": 48,
        }
        for task in scenario.tasks:
            task.releaseTime = release_times[task.id]

    return Scenario.model_validate(
        scenario.model_dump(mode="json")
    )
```

Use this exact runtime-task builder:

```python
def build_calibration_runtime_task(
    case_id: str,
    scenario: Scenario,
    current_time: int,
) -> Task:
    if case_id not in {case.case_id for case in _CASES}:
        raise KeyError(f"未知校准案例: {case_id}")
    if current_time not in RUNTIME_TASK_TICKS:
        raise ValueError(
            f"校准运行时任务只允许在 {RUNTIME_TASK_TICKS} 插入"
        )
    if scenario.zones.inspection:
        target = scenario.zones.inspection[0]
    elif scenario.zones.delivery:
        target = scenario.zones.delivery[0]
    else:
        raise ValueError(f"校准案例缺少运行时任务目标: {case_id}")
    task_id = f"{case_id}-runtime-{current_time}"
    return Task(
        id=task_id,
        type="emergency",
        title=task_id,
        priority=5,
        releaseTime=current_time,
        deadline=current_time + 40,
        target=target,
    )
```

- [ ] **Step 6: Implement the four variants**

In `backend/benchmarks/adaptive_variants.py`:

```python
from dataclasses import dataclass

from backend.app.schemas import DispatchOptions


@dataclass(frozen=True, slots=True)
class AdaptiveCalibrationVariant:
    variant_id: str
    assignment_replan_window: int
    adaptive_replan_window: bool

    def options(self) -> DispatchOptions:
        return DispatchOptions(
            avoidConflicts=True,
            includeDynamic=True,
            assignmentReplanWindow=self.assignment_replan_window,
            adaptiveReplanWindow=self.adaptive_replan_window,
        )


_VARIANTS = (
    AdaptiveCalibrationVariant("fixed-4", 4, False),
    AdaptiveCalibrationVariant("fixed-24", 24, False),
    AdaptiveCalibrationVariant("fixed-48", 48, False),
    AdaptiveCalibrationVariant("adaptive-current-24", 24, True),
)


def adaptive_calibration_variants(
    variant_ids: tuple[str, ...] | None = None,
) -> tuple[AdaptiveCalibrationVariant, ...]:
    if variant_ids is None:
        return _VARIANTS
    if not variant_ids:
        raise ValueError("校准变体必须至少包含一项")
    known = {variant.variant_id for variant in _VARIANTS}
    unknown = sorted(set(variant_ids) - known)
    if unknown:
        raise ValueError(f"未知校准变体: {', '.join(unknown)}")
    return tuple(
        variant
        for variant in _VARIANTS
        if variant.variant_id in variant_ids
    )
```

- [ ] **Step 7: Run GREEN and boundary-catalog regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_adaptive_replan_calibration.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests\test_algorithm_benchmark.py -q -k "case or scenario or catalog"
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit Task 4**

```powershell
git add -- backend/benchmarks/adaptive_scenarios.py backend/benchmarks/adaptive_variants.py backend/tests/test_adaptive_replan_calibration.py
git diff --cached --check
git commit -m "feat: add adaptive calibration catalog"
```

---

### Task 5: Online Calibration Run and Batch Execution

**Files:**
- Create: `backend/benchmarks/adaptive_results.py`
- Create: `backend/benchmarks/adaptive_runner.py`
- Modify: `backend/tests/test_adaptive_replan_calibration.py`

**Interfaces:**
- Consumes:
  - `run_isolated_process`
  - `ReplanObservation`
  - calibration cases/variants/runtime task builder
  - normal session functions
- Produces:
  - `AdaptiveReplanRecord`
  - `AdaptiveCalibrationRun`
  - `execute_adaptive_calibration_case(case_id: str, variant_id: str, run_index: int) -> AdaptiveCalibrationRun`
  - `run_isolated_adaptive_calibration(case: AdaptiveCalibrationCase, variant: AdaptiveCalibrationVariant, run_index: int, timeout_seconds: float) -> AdaptiveCalibrationRun`
  - `run_adaptive_calibration_cases(cases: tuple[AdaptiveCalibrationCase, ...], variants: tuple[AdaptiveCalibrationVariant, ...], repetitions: int, timeout_seconds: float, on_result: Callable[[list[AdaptiveCalibrationRun]], None] | None = None) -> list[AdaptiveCalibrationRun]`

- [ ] **Step 1: Add failing run/observation model tests**

Append:

```python
from dataclasses import replace

from backend.app.replan_window import ReplanObservation
from backend.benchmarks.adaptive_results import (
    AdaptiveCalibrationRun,
    AdaptiveReplanRecord,
)


def _observation(time: int, window: int, reason: str = "固定窗口") -> ReplanObservation:
    return ReplanObservation(
        time=time,
        configured_window=window,
        effective_window=window,
        reason=reason,
        released_task_count=2,
        future_task_count=3,
        active_robot_count=4,
        task_pressure_ratio=0.5,
        latency_samples_before_ms=(),
        latency_median_before_ms=None,
        latency_slow_before=False,
        replan_time_ms=10,
        latency_slow_after=False,
        path_candidate_count=1,
        selected_path_candidate_index=0,
        failed_path_candidate_count=0,
        timed_astar_call_count=4,
        timed_astar_expanded_state_count=20,
        max_timed_astar_expanded_state_count=8,
        timed_astar_exhausted_search_count=0,
        timed_astar_goal_fully_reserved_reject_count=0,
    )


def _completed_run(
    case_id: str,
    variant_id: str,
    run_index: int,
    *,
    replan_times: list[float] | None = None,
    pressure_ratios: list[float] | None = None,
    correctness_stable: bool = True,
) -> AdaptiveCalibrationRun:
    case = next(
        item for item in adaptive_calibration_cases()
        if item.case_id == case_id
    )
    variant = next(
        item for item in adaptive_calibration_variants()
        if item.variant_id == variant_id
    )
    times = replan_times or [10]
    ratios = pressure_ratios or [0.5] * len(times)
    assert len(times) == len(ratios)
    observations = tuple(
        AdaptiveReplanRecord.from_observation(
            case_id,
            variant_id,
            run_index,
            index,
            replace(
                _observation(
                    time=index * 10,
                    window=variant.assignment_replan_window,
                ),
                replan_time_ms=replan_time,
                task_pressure_ratio=pressure_ratio,
            ),
        )
        for index, (replan_time, pressure_ratio) in enumerate(
            zip(times, ratios, strict=True),
            start=1,
        )
    )
    return AdaptiveCalibrationRun(
        case_id=case_id,
        variant_id=variant_id,
        run_index=run_index,
        robot_count=case.robot_count,
        task_count=case.task_count,
        tick_target=case.tick_target,
        outcome="completed",
        error_type=None,
        error_message=None,
        correctness_stable=correctness_stable,
        released_task_count=case.task_count,
        covered_task_count=case.task_count,
        completed_task_count=case.task_count,
        coverage_rate_percent=100,
        actual_completion_rate_percent=100,
        predicted_conflict_count=0,
        active_conflict_count=0,
        safety_intervention_count=0,
        safety_stall_reached=False,
        max_consecutive_safety_intervention_count=0,
        deadline_miss_count=0,
        failure_count=0,
        total_distance=10,
        makespan=10,
        wall_clock_ms=10,
        replan_count=len(observations),
        window_change_count=0,
        replan_observations=observations,
    )


def test_adaptive_replan_record_adds_run_identity() -> None:
    record = AdaptiveReplanRecord.from_observation(
        "adaptive-low-load-r4-t17",
        "fixed-24",
        2,
        3,
        _observation(20, 24),
    )
    payload = record.to_record()
    assert payload["caseId"] == "adaptive-low-load-r4-t17"
    assert payload["variantId"] == "fixed-24"
    assert payload["runIndex"] == 2
    assert payload["observationIndex"] == 3
    assert payload["effectiveWindow"] == 24
```

- [ ] **Step 2: Add failing real-run tests**

```python
from backend.benchmarks.adaptive_runner import (
    execute_adaptive_calibration_case,
    run_adaptive_calibration_cases,
    run_isolated_adaptive_calibration,
)
from backend.benchmarks.process_isolation import (
    BenchmarkInfrastructureError,
    IsolatedExecution,
)


def test_adaptive_calibration_executes_real_online_flow() -> None:
    run = execute_adaptive_calibration_case(
        "adaptive-transition-r4-t6",
        "fixed-24",
        1,
    )
    assert run.outcome == "completed"
    assert run.tick_target == 120
    assert run.task_count == 6
    assert run.released_task_count == 6
    assert run.covered_task_count == 6
    assert run.active_conflict_count == 0
    assert run.replan_count == len(run.replan_observations)
    assert run.replan_count >= 3
    assert {
        record.time for record in run.replan_observations
    }.issuperset({0, 20, 40})
    assert all(
        record.reason == "固定窗口"
        and record.effective_window == 24
        for record in run.replan_observations
    )


def test_adaptive_calibration_current_variant_uses_adaptive_reasons() -> None:
    run = execute_adaptive_calibration_case(
        "adaptive-low-load-r4-t17",
        "adaptive-current-24",
        1,
    )
    assert run.outcome == "completed"
    assert run.replan_observations[0].effective_window == 48
    assert run.replan_observations[0].reason == "当前负载较低且存在远期任务，扩大窗口"
```

- [ ] **Step 3: Add failing batch order and isolation translation tests**

```python
def test_adaptive_batch_uses_case_variant_run_order_and_copies_snapshots(monkeypatch) -> None:
    cases = adaptive_calibration_cases()[:2]
    variants = adaptive_calibration_variants()[:2]
    calls = []
    snapshots = []

    def fake_isolated(case, variant, run_index, timeout_seconds):
        calls.append((case.case_id, variant.variant_id, run_index, timeout_seconds))
        return _completed_run(
            case.case_id,
            variant.variant_id,
            run_index,
        )

    monkeypatch.setattr(
        "backend.benchmarks.adaptive_runner.run_isolated_adaptive_calibration",
        fake_isolated,
    )
    runs = run_adaptive_calibration_cases(
        cases,
        variants,
        repetitions=2,
        timeout_seconds=3.5,
        on_result=snapshots.append,
    )
    assert calls == [
        (cases[0].case_id, variants[0].variant_id, 1, 3.5),
        (cases[0].case_id, variants[0].variant_id, 2, 3.5),
        (cases[0].case_id, variants[1].variant_id, 1, 3.5),
        (cases[0].case_id, variants[1].variant_id, 2, 3.5),
        (cases[1].case_id, variants[0].variant_id, 1, 3.5),
        (cases[1].case_id, variants[0].variant_id, 2, 3.5),
        (cases[1].case_id, variants[1].variant_id, 1, 3.5),
        (cases[1].case_id, variants[1].variant_id, 2, 3.5),
    ]
    assert len(runs) == 8
    assert [len(snapshot) for snapshot in snapshots] == list(range(1, 9))
    assert len({id(snapshot) for snapshot in snapshots}) == 8


@pytest.mark.parametrize(
    ("execution", "expected_outcome", "expected_error_type"),
    [
        (
            IsolatedExecution(
                outcome="timeout",
                value=None,
                error_type="TimeoutError",
                error_message=None,
                wall_clock_ms=50,
            ),
            "timeout",
            "TimeoutError",
        ),
        (
            IsolatedExecution(
                outcome="error",
                value=None,
                error_type="RuntimeError",
                error_message="worker failed",
                wall_clock_ms=20,
            ),
            "error",
            "RuntimeError",
        ),
        (
            IsolatedExecution(
                outcome="completed",
                value="invalid",
                error_type=None,
                error_message=None,
                wall_clock_ms=10,
            ),
            "error",
            "ChildProcessError",
        ),
    ],
)
def test_isolated_adaptive_run_translates_process_outcomes(
    monkeypatch,
    execution,
    expected_outcome,
    expected_error_type,
) -> None:
    case = adaptive_calibration_cases()[0]
    variant = adaptive_calibration_variants()[0]
    monkeypatch.setattr(
        "backend.benchmarks.adaptive_runner.run_isolated_process",
        lambda worker, worker_args, timeout_seconds: execution,
    )
    run = run_isolated_adaptive_calibration(case, variant, 1, 5)
    assert run.outcome == expected_outcome
    assert run.error_type == expected_error_type
    assert run.correctness_stable is False
    assert run.released_task_count is None
    assert run.replan_observations == ()


def test_adaptive_batch_continues_after_per_run_error(monkeypatch) -> None:
    case = adaptive_calibration_cases()[0]
    variant = adaptive_calibration_variants()[0]
    calls = 0

    def fake_isolated(case, variant, run_index, timeout_seconds):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AdaptiveCalibrationRun.error(
                case,
                variant,
                run_index,
                "RuntimeError",
                "first failed",
                1,
            )
        return _completed_run(
            case.case_id,
            variant.variant_id,
            run_index,
        )

    monkeypatch.setattr(
        "backend.benchmarks.adaptive_runner.run_isolated_adaptive_calibration",
        fake_isolated,
    )
    runs = run_adaptive_calibration_cases(
        (case,),
        (variant,),
        repetitions=2,
        timeout_seconds=5,
    )
    assert [run.outcome for run in runs] == ["error", "completed"]


def test_adaptive_batch_propagates_infrastructure_failure(monkeypatch) -> None:
    case = adaptive_calibration_cases()[0]
    variant = adaptive_calibration_variants()[0]

    def fail(*args, **kwargs):
        raise BenchmarkInfrastructureError("worker cleanup failed")

    monkeypatch.setattr(
        "backend.benchmarks.adaptive_runner.run_isolated_adaptive_calibration",
        fail,
    )
    with pytest.raises(
        BenchmarkInfrastructureError,
        match="worker cleanup failed",
    ):
        run_adaptive_calibration_cases(
            (case,),
            (variant,),
            repetitions=1,
            timeout_seconds=5,
        )
```

- [ ] **Step 4: Run runner tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_adaptive_replan_calibration.py -q -k "record or executes_real or current_variant or batch or isolated"
```

Expected: collection fails because the result and runner modules do not exist.

- [ ] **Step 5: Implement `AdaptiveReplanRecord`**

In `backend/benchmarks/adaptive_results.py`, add:

```python
from dataclasses import dataclass
from typing import Literal

from backend.app.replan_window import ReplanObservation


Number = int | float
AdaptiveCalibrationOutcome = Literal["completed", "timeout", "error"]


@dataclass(frozen=True, slots=True)
class AdaptiveReplanRecord:
    case_id: str
    variant_id: str
    run_index: int
    observation_index: int
    time: int
    configured_window: int
    effective_window: int
    reason: str
    released_task_count: int
    future_task_count: int
    active_robot_count: int
    task_pressure_ratio: float
    latency_samples_before_ms: tuple[float, ...]
    latency_median_before_ms: float | None
    latency_slow_before: bool
    replan_time_ms: float
    latency_slow_after: bool
    path_candidate_count: int
    selected_path_candidate_index: int | None
    failed_path_candidate_count: int
    timed_astar_call_count: int
    timed_astar_expanded_state_count: int
    max_timed_astar_expanded_state_count: int
    timed_astar_exhausted_search_count: int
    timed_astar_goal_fully_reserved_reject_count: int

    @classmethod
    def from_observation(
        cls,
        case_id: str,
        variant_id: str,
        run_index: int,
        observation_index: int,
        observation: ReplanObservation,
    ) -> "AdaptiveReplanRecord":
        return cls(
            case_id=case_id,
            variant_id=variant_id,
            run_index=run_index,
            observation_index=observation_index,
            time=observation.time,
            configured_window=observation.configured_window,
            effective_window=observation.effective_window,
            reason=observation.reason,
            released_task_count=observation.released_task_count,
            future_task_count=observation.future_task_count,
            active_robot_count=observation.active_robot_count,
            task_pressure_ratio=observation.task_pressure_ratio,
            latency_samples_before_ms=observation.latency_samples_before_ms,
            latency_median_before_ms=observation.latency_median_before_ms,
            latency_slow_before=observation.latency_slow_before,
            replan_time_ms=observation.replan_time_ms,
            latency_slow_after=observation.latency_slow_after,
            path_candidate_count=observation.path_candidate_count,
            selected_path_candidate_index=(
                observation.selected_path_candidate_index
            ),
            failed_path_candidate_count=observation.failed_path_candidate_count,
            timed_astar_call_count=observation.timed_astar_call_count,
            timed_astar_expanded_state_count=(
                observation.timed_astar_expanded_state_count
            ),
            max_timed_astar_expanded_state_count=(
                observation.max_timed_astar_expanded_state_count
            ),
            timed_astar_exhausted_search_count=(
                observation.timed_astar_exhausted_search_count
            ),
            timed_astar_goal_fully_reserved_reject_count=(
                observation.timed_astar_goal_fully_reserved_reject_count
            ),
        )

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "variantId": self.variant_id,
            "runIndex": self.run_index,
            "observationIndex": self.observation_index,
            "time": self.time,
            "configuredWindow": self.configured_window,
            "effectiveWindow": self.effective_window,
            "reason": self.reason,
            "releasedTaskCount": self.released_task_count,
            "futureTaskCount": self.future_task_count,
            "activeRobotCount": self.active_robot_count,
            "taskPressureRatio": self.task_pressure_ratio,
            "latencySamplesBeforeMs": list(self.latency_samples_before_ms),
            "latencyMedianBeforeMs": self.latency_median_before_ms,
            "latencySlowBefore": self.latency_slow_before,
            "replanTimeMs": self.replan_time_ms,
            "latencySlowAfter": self.latency_slow_after,
            "pathCandidateCount": self.path_candidate_count,
            "selectedPathCandidateIndex": self.selected_path_candidate_index,
            "failedPathCandidateCount": self.failed_path_candidate_count,
            "timedAStarCallCount": self.timed_astar_call_count,
            "timedAStarExpandedStateCount": (
                self.timed_astar_expanded_state_count
            ),
            "maxTimedAStarExpandedStateCount": (
                self.max_timed_astar_expanded_state_count
            ),
            "timedAStarExhaustedSearchCount": (
                self.timed_astar_exhausted_search_count
            ),
            "timedAStarGoalFullyReservedRejectCount": (
                self.timed_astar_goal_fully_reserved_reject_count
            ),
        }
```

- [ ] **Step 6: Implement `AdaptiveCalibrationRun` and failure factories**

Add these imports, then define:

```python
from backend.benchmarks.adaptive_scenarios import AdaptiveCalibrationCase
from backend.benchmarks.adaptive_variants import AdaptiveCalibrationVariant


@dataclass(frozen=True, slots=True)
class AdaptiveCalibrationRun:
    case_id: str
    variant_id: str
    run_index: int
    robot_count: int
    task_count: int
    tick_target: int
    outcome: AdaptiveCalibrationOutcome
    error_type: str | None
    error_message: str | None
    correctness_stable: bool
    released_task_count: int | None
    covered_task_count: int | None
    completed_task_count: int | None
    coverage_rate_percent: Number | None
    actual_completion_rate_percent: Number | None
    predicted_conflict_count: int | None
    active_conflict_count: int | None
    safety_intervention_count: int | None
    safety_stall_reached: bool | None
    max_consecutive_safety_intervention_count: int | None
    deadline_miss_count: int | None
    failure_count: int | None
    total_distance: Number | None
    makespan: Number | None
    wall_clock_ms: Number
    replan_count: int
    window_change_count: int
    replan_observations: tuple[AdaptiveReplanRecord, ...]

    @classmethod
    def timeout(
        cls,
        case: AdaptiveCalibrationCase,
        variant: AdaptiveCalibrationVariant,
        run_index: int,
        wall_clock_ms: Number,
    ) -> "AdaptiveCalibrationRun":
        return cls._failed(
            case,
            variant,
            run_index,
            "timeout",
            "TimeoutError",
            None,
            wall_clock_ms,
        )

    @classmethod
    def error(
        cls,
        case: AdaptiveCalibrationCase,
        variant: AdaptiveCalibrationVariant,
        run_index: int,
        error_type: str,
        error_message: str,
        wall_clock_ms: Number,
    ) -> "AdaptiveCalibrationRun":
        return cls._failed(
            case,
            variant,
            run_index,
            "error",
            error_type,
            error_message,
            wall_clock_ms,
        )

    @classmethod
    def _failed(
        cls,
        case: AdaptiveCalibrationCase,
        variant: AdaptiveCalibrationVariant,
        run_index: int,
        outcome: Literal["timeout", "error"],
        error_type: str,
        error_message: str | None,
        wall_clock_ms: Number,
    ) -> "AdaptiveCalibrationRun":
        return cls(
            case_id=case.case_id,
            variant_id=variant.variant_id,
            run_index=run_index,
            robot_count=case.robot_count,
            task_count=case.task_count,
            tick_target=case.tick_target,
            outcome=outcome,
            error_type=error_type,
            error_message=error_message,
            correctness_stable=False,
            released_task_count=None,
            covered_task_count=None,
            completed_task_count=None,
            coverage_rate_percent=None,
            actual_completion_rate_percent=None,
            predicted_conflict_count=None,
            active_conflict_count=None,
            safety_intervention_count=None,
            safety_stall_reached=None,
            max_consecutive_safety_intervention_count=None,
            deadline_miss_count=None,
            failure_count=None,
            total_distance=None,
            makespan=None,
            wall_clock_ms=wall_clock_ms,
            replan_count=0,
            window_change_count=0,
            replan_observations=(),
        )

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "variantId": self.variant_id,
            "runIndex": self.run_index,
            "robotCount": self.robot_count,
            "taskCount": self.task_count,
            "tickTarget": self.tick_target,
            "outcome": self.outcome,
            "errorType": self.error_type,
            "errorMessage": self.error_message,
            "correctnessStable": self.correctness_stable,
            "releasedTaskCount": self.released_task_count,
            "coveredTaskCount": self.covered_task_count,
            "completedTaskCount": self.completed_task_count,
            "coverageRatePercent": self.coverage_rate_percent,
            "actualCompletionRatePercent": (
                self.actual_completion_rate_percent
            ),
            "predictedConflictCount": self.predicted_conflict_count,
            "activeConflictCount": self.active_conflict_count,
            "safetyInterventionCount": self.safety_intervention_count,
            "safetyStallReached": self.safety_stall_reached,
            "maxConsecutiveSafetyInterventionCount": (
                self.max_consecutive_safety_intervention_count
            ),
            "deadlineMissCount": self.deadline_miss_count,
            "failureCount": self.failure_count,
            "totalDistance": self.total_distance,
            "makespan": self.makespan,
            "wallClockMs": self.wall_clock_ms,
            "replanCount": self.replan_count,
            "windowChangeCount": self.window_change_count,
            "replanObservations": [
                item.to_record() for item in self.replan_observations
            ],
        }
```

Keep `_completed_run(...)` only in `backend/tests/test_adaptive_replan_calibration.py`; production result models expose no completed-test factory.

- [ ] **Step 7: Implement the real online worker**

In `backend/benchmarks/adaptive_runner.py`:

```python
from time import perf_counter

from backend.app.replan_window import (
    DEFAULT_ADAPTIVE_REPLAN_POLICY,
    ReplanObservation,
)
from backend.app.schemas import (
    AddTaskRequest,
    CreateSessionRequest,
    SessionTickRequest,
)
from backend.app.sessions import (
    add_task,
    create_session,
    delete_session,
    tick_session,
)
from backend.benchmarks.adaptive_results import (
    AdaptiveCalibrationRun,
    AdaptiveReplanRecord,
)
from backend.benchmarks.adaptive_scenarios import (
    RUNTIME_TASK_TICKS,
    AdaptiveCalibrationCase,
    adaptive_calibration_cases,
    build_adaptive_calibration_scenario,
    build_calibration_runtime_task,
)
from backend.benchmarks.adaptive_variants import (
    AdaptiveCalibrationVariant,
    adaptive_calibration_variants,
)
from backend.benchmarks.results import percent


def execute_adaptive_calibration_case(
    case_id: str,
    variant_id: str,
    run_index: int,
) -> AdaptiveCalibrationRun:
    case = adaptive_calibration_cases((case_id,))[0]
    variant = adaptive_calibration_variants((variant_id,))[0]
    scenario = build_adaptive_calibration_scenario(case.case_id)
    observations: list[ReplanObservation] = []
    session_id: str | None = None
    safety_intervention_count = 0
    safety_stall_reached = False
    max_consecutive_safety_intervention_count = 0
    inserted_ticks: set[int] = set()
    started_at = perf_counter()
    try:
        session = create_session(
            CreateSessionRequest(
                scenario=scenario,
                options=variant.options(),
            ),
            adaptive_replan_policy=DEFAULT_ADAPTIVE_REPLAN_POLICY,
            replan_observer=observations.append,
        )
        session_id = session.sessionId
        while session.currentTime < case.tick_target:
            session = tick_session(
                session_id,
                SessionTickRequest(
                    currentTime=session.currentTime + 1
                ),
            )
            if session.safetyIntervention is not None:
                safety_intervention_count += 1
            if session.safetyStall is not None:
                safety_stall_reached = True
                max_consecutive_safety_intervention_count = max(
                    max_consecutive_safety_intervention_count,
                    session.safetyStall.consecutiveCount,
                )
            if (
                session.currentTime in RUNTIME_TASK_TICKS
                and session.currentTime not in inserted_ticks
            ):
                inserted_ticks.add(session.currentTime)
                session = add_task(
                    session_id,
                    AddTaskRequest(
                        task=build_calibration_runtime_task(
                            case.case_id,
                            scenario,
                            session.currentTime,
                        )
                    ),
                )
                if session.safetyStall is not None:
                    safety_stall_reached = True
                    max_consecutive_safety_intervention_count = max(
                        max_consecutive_safety_intervention_count,
                        session.safetyStall.consecutiveCount,
                    )
    finally:
        if session_id is not None:
            delete_session(session_id)
    wall_clock_ms = (perf_counter() - started_at) * 1000

    records = tuple(
        AdaptiveReplanRecord.from_observation(
            case.case_id,
            variant.variant_id,
            run_index,
            observation_index,
            observation,
        )
        for observation_index, observation in enumerate(
            observations,
            start=1,
        )
    )
    window_change_count = sum(
        previous.effective_window != current.effective_window
        for previous, current in zip(records, records[1:])
    )
    metrics = session.result.metrics
    released_task_count = sum(
        state.releaseTime <= session.currentTime
        for state in session.taskStates
    )
    covered_task_count = sum(
        state.status != "unassigned" for state in session.taskStates
    )
    active_conflict_count = (
        session.metricsHistory[-1].activeConflictCount
        if session.metricsHistory
        else 0
    )
    correctness_stable = (
        covered_task_count == case.task_count
        and active_conflict_count == 0
        and metrics.deadlineMissCount == 0
        and metrics.failureCount == 0
    )
    return AdaptiveCalibrationRun(
        case_id=case.case_id,
        variant_id=variant.variant_id,
        run_index=run_index,
        robot_count=case.robot_count,
        task_count=case.task_count,
        tick_target=case.tick_target,
        outcome="completed",
        error_type=None,
        error_message=None,
        correctness_stable=correctness_stable,
        released_task_count=released_task_count,
        covered_task_count=covered_task_count,
        completed_task_count=session.completedTaskCount,
        coverage_rate_percent=percent(
            covered_task_count,
            case.task_count,
        ),
        actual_completion_rate_percent=percent(
            session.completedTaskCount,
            released_task_count,
        ),
        predicted_conflict_count=metrics.conflictCount,
        active_conflict_count=active_conflict_count,
        safety_intervention_count=safety_intervention_count,
        safety_stall_reached=safety_stall_reached,
        max_consecutive_safety_intervention_count=(
            max_consecutive_safety_intervention_count
        ),
        deadline_miss_count=metrics.deadlineMissCount,
        failure_count=metrics.failureCount,
        total_distance=metrics.totalDistance,
        makespan=metrics.makespan,
        wall_clock_ms=wall_clock_ms,
        replan_count=len(records),
        window_change_count=window_change_count,
        replan_observations=records,
    )
```

Do not read `_sessions` directly and do not set `enforce_execution_safety=False`.

- [ ] **Step 8: Implement isolated and batch wrappers**

```python
from collections.abc import Callable
from dataclasses import replace
from time import perf_counter
import traceback

from backend.benchmarks.process_isolation import (
    BenchmarkInfrastructureError,
    run_isolated_process,
    sanitize_error_text,
)


def run_isolated_adaptive_calibration(
    case: AdaptiveCalibrationCase,
    variant: AdaptiveCalibrationVariant,
    run_index: int,
    timeout_seconds: float,
) -> AdaptiveCalibrationRun:
    execution = run_isolated_process(
        execute_adaptive_calibration_case,
        (case.case_id, variant.variant_id, run_index),
        timeout_seconds,
    )
    if execution.outcome == "timeout":
        return AdaptiveCalibrationRun.timeout(
            case,
            variant,
            run_index,
            execution.wall_clock_ms,
        )
    if execution.outcome == "error":
        return AdaptiveCalibrationRun.error(
            case,
            variant,
            run_index,
            execution.error_type or "ChildProcessError",
            execution.error_message or "子进程未返回错误信息",
            execution.wall_clock_ms,
        )
    if not isinstance(execution.value, AdaptiveCalibrationRun):
        return AdaptiveCalibrationRun.error(
            case,
            variant,
            run_index,
            "ChildProcessError",
            "子进程返回了无效的自适应窗口校准结果载荷",
            execution.wall_clock_ms,
        )
    return replace(
        execution.value,
        wall_clock_ms=execution.wall_clock_ms,
    )


def run_adaptive_calibration_cases(
    cases: tuple[AdaptiveCalibrationCase, ...],
    variants: tuple[AdaptiveCalibrationVariant, ...],
    repetitions: int,
    timeout_seconds: float,
    on_result: Callable[[list[AdaptiveCalibrationRun]], None] | None = None,
) -> list[AdaptiveCalibrationRun]:
    runs: list[AdaptiveCalibrationRun] = []
    for case in cases:
        for variant in variants:
            for run_index in range(1, repetitions + 1):
                started_at = perf_counter()
                try:
                    run = run_isolated_adaptive_calibration(
                        case,
                        variant,
                        run_index,
                        timeout_seconds,
                    )
                except BenchmarkInfrastructureError:
                    raise
                except Exception as exc:
                    traceback.print_exc()
                    run = AdaptiveCalibrationRun.error(
                        case,
                        variant,
                        run_index,
                        type(exc).__name__,
                        sanitize_error_text(str(exc)),
                        round(
                            (perf_counter() - started_at) * 1000,
                            2,
                        ),
                    )
                runs.append(run)
                if on_result is not None:
                    on_result(list(runs))
    return runs
```

- [ ] **Step 9: Run GREEN, session safety, and no-residue checks**

Run:

```powershell
$benchmarkWorkerPidsBefore = @(
  Get-CimInstance Win32_Process |
    Where-Object {
      $_.Name -eq 'python.exe' -and
      $_.CommandLine -like '*multiprocessing.spawn*'
    } |
    Select-Object -ExpandProperty ProcessId
)
.\.venv\Scripts\python.exe -m pytest backend\tests\test_adaptive_replan_calibration.py -q -k "record or executes_real or current_variant or batch or isolated"
.\.venv\Scripts\python.exe -m pytest backend\tests\test_sessions.py -q -k "safety_intervention or continue_to_hold or reverse_edge"
```

After tests:

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
  throw "检测到测试遗留的基准 spawn worker"
}
```

Expected: no calibration worker remains.

- [ ] **Step 10: Commit Task 5**

```powershell
git add -- backend/benchmarks/adaptive_results.py backend/benchmarks/adaptive_runner.py backend/tests/test_adaptive_replan_calibration.py
git diff --cached --check
git commit -m "feat: run adaptive calibration flows"
```

---

### Task 6: Statistics, Candidate Envelope, Reports, and CLI

**Files:**
- Modify: `backend/benchmarks/adaptive_results.py`
- Create: `backend/benchmarks/adaptive_reporting.py`
- Create: `backend/benchmarks/adaptive_replan_calibration.py`
- Modify: `backend/tests/test_adaptive_replan_calibration.py`
- Modify: `package.json`

**Interfaces:**
- Produces:
  - `nearest_rank(values, percentile)`
  - `AdaptiveVariantSummary`
  - `ObservationDistribution`
  - `CandidateEnvelope`
  - `AdaptiveCalibrationReport.create(config: dict[str, object], runs: list[AdaptiveCalibrationRun]) -> AdaptiveCalibrationReport`
  - `write_partial_report(output_dir: Path, report: AdaptiveCalibrationReport) -> None`
  - `write_final_report(output_dir: Path, report: AdaptiveCalibrationReport) -> None`
  - CLI `main(argv=None) -> int`

- [ ] **Step 1: Add failing percentile and equal-run-weight tests**

Append:

```python
from backend.benchmarks.adaptive_results import (
    AdaptiveCalibrationReport,
    nearest_rank,
)


def test_adaptive_percentiles_use_nearest_rank() -> None:
    values = [10, 20, 30, 40]
    assert nearest_rank(values, 0.50) == 20
    assert nearest_rank(values, 0.75) == 30
    assert nearest_rank(values, 0.95) == 40
    assert nearest_rank([], 0.50) is None


def test_variant_replan_summary_weights_each_run_once() -> None:
    first = _completed_run(
        case_id="adaptive-low-load-r4-t17",
        variant_id="fixed-24",
        run_index=1,
        replan_times=[10, 20, 30],
    )
    second = _completed_run(
        case_id="adaptive-low-load-r4-t17",
        variant_id="fixed-24",
        run_index=2,
        replan_times=[100],
    )
    report = AdaptiveCalibrationReport.create({}, [first, second])
    summary = report.variant_summaries[0]
    assert summary.median_run_replan_time_ms == 60
    assert summary.p95_run_replan_time_ms == 100
```

Reuse the complete `_completed_run(...)` test helper added in Task 5 Step 1. Do not add a completed-test factory to production code.

- [ ] **Step 2: Add failing candidate-envelope tests**

```python
def test_candidate_envelope_uses_only_stable_fixed_24_observations() -> None:
    runs = []
    for index, case in enumerate(adaptive_calibration_cases(), start=1):
        runs.append(
            _completed_run(
                case_id=case.case_id,
                variant_id="fixed-24",
                run_index=1,
                replan_times=[10 * index, 20 * index, 30 * index],
                pressure_ratios=[0.5 * index, 1.0 * index, 1.5 * index],
                correctness_stable=True,
            )
        )
    runs.append(
        _completed_run(
            case_id=adaptive_calibration_cases()[0].case_id,
            variant_id="adaptive-current-24",
            run_index=1,
            replan_times=[999, 999, 999],
            pressure_ratios=[99, 99, 99],
            correctness_stable=True,
        )
    )
    runs.append(
        _completed_run(
            case_id=adaptive_calibration_cases()[0].case_id,
            variant_id="fixed-24",
            run_index=2,
            replan_times=[888, 888, 888],
            pressure_ratios=[88, 88, 88],
            correctness_stable=False,
        )
    )
    report = AdaptiveCalibrationReport.create({}, runs)
    assert report.candidate_envelope_available is True
    assert report.observation_distribution.latency_ms.sample_count == 9
    assert report.observation_distribution.latency_ms.p95 == 90
    assert report.candidate_envelope is not None
    assert report.candidate_envelope.slow_exit_threshold_ms.minimum == (
        report.observation_distribution.latency_ms.p50
    )
    assert report.candidate_envelope.slow_enter_threshold_ms.maximum == (
        report.observation_distribution.latency_ms.p95
    )


def test_candidate_envelope_is_unavailable_when_one_case_has_fewer_than_three_samples() -> None:
    cases = adaptive_calibration_cases()
    runs = [
        _completed_run(
            case_id=case.case_id,
            variant_id="fixed-24",
            run_index=1,
            replan_times=(
                [10, 20, 30]
                if case.case_id != cases[-1].case_id
                else [10, 20]
            ),
            pressure_ratios=(
                [1, 2, 3]
                if case.case_id != cases[-1].case_id
                else [1, 2]
            ),
            correctness_stable=True,
        )
        for case in cases
    ]
    report = AdaptiveCalibrationReport.create({}, runs)
    assert report.candidate_envelope_available is False
    assert report.candidate_envelope is None
    assert report.observation_distribution.latency_ms.sample_count == 8


def test_candidate_distribution_is_explicitly_empty_without_stable_fixed_24() -> None:
    run = _completed_run(
        case_id="adaptive-low-load-r4-t17",
        variant_id="fixed-24",
        run_index=1,
        replan_times=[10, 20, 30],
        correctness_stable=False,
    )
    report = AdaptiveCalibrationReport.create({}, [run])
    latency = report.observation_distribution.latency_ms
    pressure = report.observation_distribution.task_pressure_ratio
    assert (latency.p50, latency.p75, latency.p95) == (
        None,
        None,
        None,
    )
    assert (pressure.p50, pressure.p75, pressure.p95) == (
        None,
        None,
        None,
    )
    assert latency.sample_count == 0
    assert pressure.sample_count == 0
    assert report.candidate_envelope_available is False
    assert report.candidate_envelope is None
```

- [ ] **Step 3: Add failing reporting tests**

```python
import csv
import json

from backend.benchmarks import adaptive_reporting as adaptive_reporting_module
from backend.benchmarks.adaptive_reporting import (
    REPLAN_OBSERVATION_FIELD_NAMES,
    RUN_FIELD_NAMES,
    VARIANT_SUMMARY_FIELD_NAMES,
    write_final_report,
    write_partial_report,
)


def test_adaptive_report_writes_utf8_json_and_three_csv_files(tmp_path) -> None:
    run = _completed_run(
        case_id="adaptive-low-load-r4-t17",
        variant_id="fixed-24",
        run_index=1,
        replan_times=[10, 20, 30],
        pressure_ratios=[0.5, 1, 1.5],
    )
    report = AdaptiveCalibrationReport.create({"repetitions": 1}, [run])
    write_final_report(tmp_path, report)

    payload = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    assert payload["runs"][0]["caseId"] == "adaptive-low-load-r4-t17"
    assert payload["runs"][0]["replanObservations"][0]["reason"] == "固定窗口"
    expected_headers = {
        "runs.csv": RUN_FIELD_NAMES,
        "replan-observations.csv": REPLAN_OBSERVATION_FIELD_NAMES,
        "variant-summaries.csv": VARIANT_SUMMARY_FIELD_NAMES,
    }
    for file_name, expected_header in expected_headers.items():
        with (tmp_path / file_name).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            assert tuple(reader.fieldnames or ()) == expected_header
            assert list(reader)


def test_adaptive_partial_is_replaced_and_removed_after_final(tmp_path) -> None:
    first = _completed_run("adaptive-low-load-r4-t17", "fixed-24", 1)
    second = _completed_run("adaptive-low-load-r4-t17", "fixed-24", 2)
    write_partial_report(tmp_path, AdaptiveCalibrationReport.create({}, [first]))
    write_partial_report(tmp_path, AdaptiveCalibrationReport.create({}, [first, second]))
    partial_path = tmp_path / "results.partial.json"
    payload = json.loads(partial_path.read_text(encoding="utf-8"))
    assert [item["runIndex"] for item in payload["runs"]] == [1, 2]
    assert not list(tmp_path.glob("*.tmp"))
    write_final_report(
        tmp_path,
        AdaptiveCalibrationReport.create({}, [first, second]),
    )
    assert partial_path.exists() is False


def test_adaptive_final_write_failure_keeps_partial(
    tmp_path,
    monkeypatch,
) -> None:
    run = _completed_run(
        "adaptive-low-load-r4-t17",
        "fixed-24",
        1,
    )
    report = AdaptiveCalibrationReport.create({}, [run])
    write_partial_report(tmp_path, report)

    def fail_csv(*args, **kwargs):
        raise OSError("csv failed")

    monkeypatch.setattr(
        adaptive_reporting_module,
        "_write_csv",
        fail_csv,
    )
    with pytest.raises(OSError, match="csv failed"):
        write_final_report(tmp_path, report)
    assert (tmp_path / "results.partial.json").exists()
```

- [ ] **Step 4: Add failing CLI validation and exact-config tests**

```python
from datetime import datetime, timezone

from backend.benchmarks import adaptive_replan_calibration as cli_module
from backend.benchmarks.adaptive_replan_calibration import main, parse_args


@pytest.mark.parametrize(
    "args",
    [
        ["--cases", ","],
        ["--cases", "unknown"],
        ["--variants", ","],
        ["--variants", "unknown"],
        ["--repetitions", "0"],
        ["--timeout-seconds", "0"],
        ["--timeout-seconds", "nan"],
        ["--timeout-seconds", "inf"],
    ],
)
def test_adaptive_cli_rejects_invalid_config_before_output(tmp_path, args) -> None:
    assert main([*args, "--output-dir", str(tmp_path)]) != 0
    assert list(tmp_path.iterdir()) == []


def test_adaptive_cli_parses_trimmed_case_and_variant_lists() -> None:
    args = parse_args(
        [
            "--cases",
            " adaptive-low-load-r4-t17, adaptive-transition-r4-t6 ",
            "--variants",
            " fixed-4, adaptive-current-24 ",
        ]
    )
    assert args.cases == (
        "adaptive-low-load-r4-t17",
        "adaptive-transition-r4-t6",
    )
    assert args.variants == ("fixed-4", "adaptive-current-24")


def test_adaptive_cli_same_second_directories_do_not_overwrite(
    tmp_path,
    monkeypatch,
) -> None:
    class FixedDateTime:
        @classmethod
        def now(cls, tz):
            assert tz is timezone.utc
            return datetime(2026, 7, 26, tzinfo=timezone.utc)

    monkeypatch.setattr(cli_module, "datetime", FixedDateTime)
    first = cli_module._create_result_directory(str(tmp_path))
    second = cli_module._create_result_directory(str(tmp_path))
    assert first.name == "20260726T000000Z"
    assert second.name == "20260726T000000Z-2"
```

Add this monkeypatched CLI success test:

```python
def test_adaptive_cli_success_writes_exact_config(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    result_path = (tmp_path / "20260726T000000Z").resolve()
    result_path.mkdir()
    completed = _completed_run(
        "adaptive-low-load-r4-t17",
        "fixed-24",
        1,
    )

    def fake_run(
        cases,
        variants,
        repetitions,
        timeout_seconds,
        on_result=None,
    ):
        assert [case.case_id for case in cases] == [
            "adaptive-low-load-r4-t17"
        ]
        assert [variant.variant_id for variant in variants] == ["fixed-24"]
        assert repetitions == 1
        assert timeout_seconds == 30
        runs = [completed]
        if on_result is not None:
            on_result(list(runs))
        return runs

    monkeypatch.setattr(
        cli_module,
        "_create_result_directory",
        lambda output_dir: result_path,
    )
    monkeypatch.setattr(
        cli_module,
        "run_adaptive_calibration_cases",
        fake_run,
    )

    assert main(
        [
            "--cases",
            "adaptive-low-load-r4-t17",
            "--variants",
            "fixed-24",
            "--repetitions",
            "1",
            "--output-dir",
            str(tmp_path),
        ]
    ) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == str(result_path)
    assert captured.err == ""

    payload = json.loads(
        (result_path / "results.json").read_text(encoding="utf-8")
    )
    assert payload["config"] == {
        "caseIds": ["adaptive-low-load-r4-t17"],
        "variantIds": ["fixed-24"],
        "repetitions": 1,
        "timeoutSeconds": 30,
        "tickTarget": 120,
        "runtimeTaskTicks": [20, 40],
        "outputDir": str(result_path),
        "defaultPolicy": {
            "slowEnterThresholdMs": 60,
            "slowExitThresholdMs": 40,
            "taskPressureMultiplier": 2,
        },
    }
```

- [ ] **Step 5: Run statistics/report/CLI tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_adaptive_replan_calibration.py -q -k "percentile or summary or envelope or report or partial or cli"
```

Expected: missing summary/reporting/CLI functions cause collection or assertion failures.

- [ ] **Step 6: Implement percentile, summaries, and candidate envelope**

In `adaptive_results.py`:

```python
from math import ceil


def nearest_rank(
    values: list[float],
    percentile: float,
) -> float | None:
    if not values:
        return None
    if not 0 < percentile <= 1:
        raise ValueError("percentile 必须位于 (0, 1]")
    ordered = sorted(values)
    return ordered[ceil(percentile * len(ordered)) - 1]
```

Add these immutable result types and exact JSON mappings:

```python
from datetime import datetime, timezone
from statistics import median


@dataclass(frozen=True, slots=True)
class AdaptiveVariantSummary:
    case_id: str
    variant_id: str
    run_count: int
    completed_run_count: int
    timeout_count: int
    error_count: int
    stable_run_count: int
    stable_run_rate_percent: Number
    median_wall_clock_ms: Number | None
    p95_wall_clock_ms: Number | None
    median_run_replan_time_ms: Number | None
    p95_run_replan_time_ms: Number | None
    median_replan_count: Number | None
    median_window_change_count: Number | None
    median_coverage_rate_percent: Number | None
    median_actual_completion_rate_percent: Number | None
    max_safety_intervention_count: int
    max_consecutive_safety_intervention_count: int
    window_reason_counts: dict[str, int]

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "variantId": self.variant_id,
            "runCount": self.run_count,
            "completedRunCount": self.completed_run_count,
            "timeoutCount": self.timeout_count,
            "errorCount": self.error_count,
            "stableRunCount": self.stable_run_count,
            "stableRunRatePercent": self.stable_run_rate_percent,
            "medianWallClockMs": self.median_wall_clock_ms,
            "p95WallClockMs": self.p95_wall_clock_ms,
            "medianRunReplanTimeMs": self.median_run_replan_time_ms,
            "p95RunReplanTimeMs": self.p95_run_replan_time_ms,
            "medianReplanCount": self.median_replan_count,
            "medianWindowChangeCount": self.median_window_change_count,
            "medianCoverageRatePercent": (
                self.median_coverage_rate_percent
            ),
            "medianActualCompletionRatePercent": (
                self.median_actual_completion_rate_percent
            ),
            "maxSafetyInterventionCount": (
                self.max_safety_intervention_count
            ),
            "maxConsecutiveSafetyInterventionCount": (
                self.max_consecutive_safety_intervention_count
            ),
            "windowReasonCounts": dict(self.window_reason_counts),
        }


@dataclass(frozen=True, slots=True)
class DistributionSummary:
    p50: Number | None
    p75: Number | None
    p95: Number | None
    sample_count: int

    @classmethod
    def from_values(cls, values: list[float]) -> "DistributionSummary":
        def rounded(percentile: float) -> float | None:
            value = nearest_rank(values, percentile)
            return round(value, 2) if value is not None else None

        return cls(
            p50=rounded(0.50),
            p75=rounded(0.75),
            p95=rounded(0.95),
            sample_count=len(values),
        )

    def to_record(self) -> dict[str, object]:
        return {
            "p50": self.p50,
            "p75": self.p75,
            "p95": self.p95,
            "sampleCount": self.sample_count,
        }


@dataclass(frozen=True, slots=True)
class ObservationDistribution:
    latency_ms: DistributionSummary
    task_pressure_ratio: DistributionSummary

    def to_record(self) -> dict[str, object]:
        return {
            "latencyMs": self.latency_ms.to_record(),
            "taskPressureRatio": self.task_pressure_ratio.to_record(),
        }


@dataclass(frozen=True, slots=True)
class RangeSummary:
    minimum: Number
    maximum: Number

    def to_record(self) -> dict[str, object]:
        return {"min": self.minimum, "max": self.maximum}


@dataclass(frozen=True, slots=True)
class CandidateEnvelope:
    slow_exit_threshold_ms: RangeSummary
    slow_enter_threshold_ms: RangeSummary
    task_pressure_multiplier: RangeSummary

    def to_record(self) -> dict[str, object]:
        return {
            "slowExitThresholdMs": self.slow_exit_threshold_ms.to_record(),
            "slowEnterThresholdMs": self.slow_enter_threshold_ms.to_record(),
            "taskPressureMultiplier": (
                self.task_pressure_multiplier.to_record()
            ),
        }


@dataclass(frozen=True, slots=True)
class AdaptiveCalibrationReport:
    schema_version: int
    generated_at: str
    config: dict[str, object]
    runs: list[AdaptiveCalibrationRun]
    variant_summaries: list[AdaptiveVariantSummary]
    observation_distribution: ObservationDistribution
    candidate_envelope_available: bool
    candidate_envelope: CandidateEnvelope | None

    @classmethod
    def create(
        cls,
        config: dict[str, object],
        runs: list[AdaptiveCalibrationRun],
    ) -> "AdaptiveCalibrationReport":
        distribution, available, envelope = _build_observation_evidence(
            runs
        )
        return cls(
            schema_version=1,
            generated_at=(
                datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            ),
            config=dict(config),
            runs=list(runs),
            variant_summaries=summarize_adaptive_runs(runs),
            observation_distribution=distribution,
            candidate_envelope_available=available,
            candidate_envelope=envelope,
        )

    def to_record(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "generatedAt": self.generated_at,
            "config": dict(self.config),
            "runs": [run.to_record() for run in self.runs],
            "variantSummaries": [
                summary.to_record() for summary in self.variant_summaries
            ],
            "observationDistribution": (
                self.observation_distribution.to_record()
            ),
            "candidateEnvelopeAvailable": (
                self.candidate_envelope_available
            ),
            "candidateEnvelope": (
                self.candidate_envelope.to_record()
                if self.candidate_envelope is not None
                else None
            ),
        }
```

Implement the summary helper with these exact inclusion rules:

```python
def summarize_adaptive_runs(
    runs: list[AdaptiveCalibrationRun],
) -> list[AdaptiveVariantSummary]:
    summaries: list[AdaptiveVariantSummary] = []
    keys = dict.fromkeys((run.case_id, run.variant_id) for run in runs)
    for case_id, variant_id in keys:
        group = [
            run
            for run in runs
            if run.case_id == case_id and run.variant_id == variant_id
        ]
        completed = [run for run in group if run.outcome == "completed"]
        run_replan_medians = [
            median(
                record.replan_time_ms
                for record in run.replan_observations
            )
            for run in completed
            if run.replan_observations
        ]
        wall_clock = [float(run.wall_clock_ms) for run in completed]
        replan_counts = [run.replan_count for run in completed]
        window_changes = [run.window_change_count for run in completed]
        coverage = [
            float(run.coverage_rate_percent)
            for run in completed
            if run.coverage_rate_percent is not None
        ]
        completion = [
            float(run.actual_completion_rate_percent)
            for run in completed
            if run.actual_completion_rate_percent is not None
        ]
        reason_counts: dict[str, int] = {}
        for run in completed:
            for record in run.replan_observations:
                reason_counts[record.reason] = (
                    reason_counts.get(record.reason, 0) + 1
                )
        stable_count = sum(run.correctness_stable for run in group)
        summaries.append(
            AdaptiveVariantSummary(
                case_id=case_id,
                variant_id=variant_id,
                run_count=len(group),
                completed_run_count=len(completed),
                timeout_count=sum(
                    run.outcome == "timeout" for run in group
                ),
                error_count=sum(run.outcome == "error" for run in group),
                stable_run_count=stable_count,
                stable_run_rate_percent=percent(
                    stable_count,
                    len(group),
                ),
                median_wall_clock_ms=(
                    median(wall_clock) if wall_clock else None
                ),
                p95_wall_clock_ms=nearest_rank(wall_clock, 0.95),
                median_run_replan_time_ms=(
                    median(run_replan_medians)
                    if run_replan_medians
                    else None
                ),
                p95_run_replan_time_ms=nearest_rank(
                    run_replan_medians,
                    0.95,
                ),
                median_replan_count=(
                    median(replan_counts) if replan_counts else None
                ),
                median_window_change_count=(
                    median(window_changes) if window_changes else None
                ),
                median_coverage_rate_percent=(
                    median(coverage) if coverage else None
                ),
                median_actual_completion_rate_percent=(
                    median(completion) if completion else None
                ),
                max_safety_intervention_count=max(
                    (
                        run.safety_intervention_count or 0
                        for run in group
                    ),
                    default=0,
                ),
                max_consecutive_safety_intervention_count=max(
                    (
                        run.max_consecutive_safety_intervention_count or 0
                        for run in group
                    ),
                    default=0,
                ),
                window_reason_counts=reason_counts,
            )
        )
    return summaries
```

Extend the scenario import to include `adaptive_calibration_cases`, and import `percent` from `backend.benchmarks.results`. Build the observation evidence only from stable completed `fixed-24` runs:

```python
def _build_observation_evidence(
    runs: list[AdaptiveCalibrationRun],
) -> tuple[
    ObservationDistribution,
    bool,
    CandidateEnvelope | None,
]:
    eligible = [
        run
        for run in runs
        if run.variant_id == "fixed-24"
        and run.outcome == "completed"
        and run.correctness_stable
    ]
    observations = [
        record
        for run in eligible
        for record in run.replan_observations
    ]
    distribution = ObservationDistribution(
        latency_ms=DistributionSummary.from_values(
            [record.replan_time_ms for record in observations]
        ),
        task_pressure_ratio=DistributionSummary.from_values(
            [record.task_pressure_ratio for record in observations]
        ),
    )
    required_case_ids = tuple(
        case.case_id for case in adaptive_calibration_cases()
    )
    available = all(
        sum(
            len(run.replan_observations)
            for run in eligible
            if run.case_id == case_id
        )
        >= 3
        for case_id in required_case_ids
    )
    if not available:
        return distribution, False, None

    latency = distribution.latency_ms
    pressure = distribution.task_pressure_ratio
    assert latency.p50 is not None
    assert latency.p75 is not None
    assert latency.p95 is not None
    assert pressure.p50 is not None
    assert pressure.p75 is not None
    envelope = CandidateEnvelope(
        slow_exit_threshold_ms=RangeSummary(
            minimum=latency.p50,
            maximum=latency.p75,
        ),
        slow_enter_threshold_ms=RangeSummary(
            minimum=latency.p75,
            maximum=latency.p95,
        ),
        task_pressure_multiplier=RangeSummary(
            minimum=pressure.p50,
            maximum=pressure.p75,
        ),
    )
    return distribution, True, envelope
```

- [ ] **Step 7: Implement atomic JSON and three CSV writers**

In `adaptive_reporting.py`, define:

```python
import csv
import json
from pathlib import Path
from uuid import uuid4

from backend.benchmarks.adaptive_results import AdaptiveCalibrationReport


RUN_FIELD_NAMES = (
    "caseId",
    "variantId",
    "runIndex",
    "robotCount",
    "taskCount",
    "tickTarget",
    "outcome",
    "errorType",
    "errorMessage",
    "correctnessStable",
    "releasedTaskCount",
    "coveredTaskCount",
    "completedTaskCount",
    "coverageRatePercent",
    "actualCompletionRatePercent",
    "predictedConflictCount",
    "activeConflictCount",
    "safetyInterventionCount",
    "safetyStallReached",
    "maxConsecutiveSafetyInterventionCount",
    "deadlineMissCount",
    "failureCount",
    "totalDistance",
    "makespan",
    "wallClockMs",
    "replanCount",
    "windowChangeCount",
)

REPLAN_OBSERVATION_FIELD_NAMES = (
    "caseId",
    "variantId",
    "runIndex",
    "observationIndex",
    "time",
    "configuredWindow",
    "effectiveWindow",
    "reason",
    "releasedTaskCount",
    "futureTaskCount",
    "activeRobotCount",
    "taskPressureRatio",
    "latencySamplesBeforeMs",
    "latencyMedianBeforeMs",
    "latencySlowBefore",
    "replanTimeMs",
    "latencySlowAfter",
    "pathCandidateCount",
    "selectedPathCandidateIndex",
    "failedPathCandidateCount",
    "timedAStarCallCount",
    "timedAStarExpandedStateCount",
    "maxTimedAStarExpandedStateCount",
    "timedAStarExhaustedSearchCount",
    "timedAStarGoalFullyReservedRejectCount",
)

VARIANT_SUMMARY_FIELD_NAMES = (
    "caseId",
    "variantId",
    "runCount",
    "completedRunCount",
    "timeoutCount",
    "errorCount",
    "stableRunCount",
    "stableRunRatePercent",
    "medianWallClockMs",
    "p95WallClockMs",
    "medianRunReplanTimeMs",
    "p95RunReplanTimeMs",
    "medianReplanCount",
    "medianWindowChangeCount",
    "medianCoverageRatePercent",
    "medianActualCompletionRatePercent",
    "maxSafetyInterventionCount",
    "maxConsecutiveSafetyInterventionCount",
    "windowReasonCounts",
)


def _raise_write_error(target_path: Path, exc: Exception) -> None:
    raise OSError(
        "写入自适应窗口校准报告失败: "
        f"{target_path.resolve()}: {exc}"
    ) from exc


def _write_json(
    path: Path,
    report: AdaptiveCalibrationReport,
    target_path: Path | None = None,
) -> None:
    displayed_path = target_path or path
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(
                report.to_record(),
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
    except Exception as exc:
        _raise_write_error(displayed_path, exc)


def _write_csv(
    path: Path,
    field_names: tuple[str, ...],
    records: list[dict[str, object]],
) -> None:
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=field_names,
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(records)
    except Exception as exc:
        _raise_write_error(path, exc)


def _run_records(report: AdaptiveCalibrationReport) -> list[dict[str, object]]:
    records = []
    for run in report.runs:
        record = run.to_record()
        record.pop("replanObservations")
        records.append(record)
    return records


def _observation_records(
    report: AdaptiveCalibrationReport,
) -> list[dict[str, object]]:
    records = []
    for run in report.runs:
        for observation in run.replan_observations:
            record = observation.to_record()
            record["latencySamplesBeforeMs"] = json.dumps(
                record["latencySamplesBeforeMs"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            records.append(record)
    return records


def _variant_summary_records(
    report: AdaptiveCalibrationReport,
) -> list[dict[str, object]]:
    records = []
    for summary in report.variant_summaries:
        record = summary.to_record()
        record["windowReasonCounts"] = json.dumps(
            record["windowReasonCounts"],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        records.append(record)
    return records


def write_partial_report(
    output_dir: Path,
    report: AdaptiveCalibrationReport,
) -> None:
    partial_path = Path(output_dir) / "results.partial.json"
    temporary_path = partial_path.with_name(
        f".{partial_path.name}.{uuid4().hex}.tmp"
    )
    try:
        _write_json(temporary_path, report, target_path=partial_path)
        try:
            temporary_path.replace(partial_path)
        except Exception as exc:
            _raise_write_error(partial_path, exc)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def write_final_report(
    output_dir: Path,
    report: AdaptiveCalibrationReport,
) -> None:
    output_path = Path(output_dir)
    _write_json(output_path / "results.json", report)
    _write_csv(
        output_path / "runs.csv",
        RUN_FIELD_NAMES,
        _run_records(report),
    )
    _write_csv(
        output_path / "replan-observations.csv",
        REPLAN_OBSERVATION_FIELD_NAMES,
        _observation_records(report),
    )
    _write_csv(
        output_path / "variant-summaries.csv",
        VARIANT_SUMMARY_FIELD_NAMES,
        _variant_summary_records(report),
    )
    partial_path = output_path / "results.partial.json"
    try:
        partial_path.unlink(missing_ok=True)
    except Exception as exc:
        _raise_write_error(partial_path, exc)
```

This produces UTF-8 JSON without BOM and UTF-8-BOM CSV files. The exact failure prefix is `写入自适应窗口校准报告失败:` followed by the resolved target path and the original exception text.

- [ ] **Step 8: Implement CLI and npm script**

In `adaptive_replan_calibration.py`:

```python
import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.app.replan_window import DEFAULT_ADAPTIVE_REPLAN_POLICY
from backend.benchmarks.adaptive_reporting import (
    write_final_report,
    write_partial_report,
)
from backend.benchmarks.adaptive_results import (
    AdaptiveCalibrationReport,
    AdaptiveCalibrationRun,
)
from backend.benchmarks.adaptive_runner import run_adaptive_calibration_cases
from backend.benchmarks.adaptive_scenarios import (
    RUNTIME_TASK_TICKS,
    AdaptiveCalibrationCase,
    adaptive_calibration_cases,
)
from backend.benchmarks.adaptive_variants import (
    AdaptiveCalibrationVariant,
    adaptive_calibration_variants,
)


DEFAULT_CASE_IDS = tuple(case.case_id for case in adaptive_calibration_cases())
DEFAULT_VARIANT_IDS = tuple(
    variant.variant_id for variant in adaptive_calibration_variants()
)


def _parse_identifier_list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行自适应重规划窗口离线校准"
    )
    parser.add_argument(
        "--cases",
        type=_parse_identifier_list,
        default=DEFAULT_CASE_IDS,
    )
    parser.add_argument(
        "--variants",
        type=_parse_identifier_list,
        default=DEFAULT_VARIANT_IDS,
    )
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument(
        "--output-dir",
        default="output/adaptive-replan-calibration",
    )
    return parser.parse_args(argv)


def _validate_config(
    args: argparse.Namespace,
) -> tuple[
    tuple[AdaptiveCalibrationCase, ...],
    tuple[AdaptiveCalibrationVariant, ...],
]:
    cases = adaptive_calibration_cases(args.cases)
    variants = adaptive_calibration_variants(args.variants)
    if args.repetitions <= 0:
        raise ValueError("--repetitions 必须为正整数")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds 必须为有限正数")
    return cases, variants


def _create_result_directory(output_dir: str) -> Path:
    base_path = Path(output_dir).resolve()
    try:
        base_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"创建自适应窗口校准输出目录失败: {base_path}: {exc}"
        ) from exc

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = 1
    while True:
        directory_name = (
            timestamp if suffix == 1 else f"{timestamp}-{suffix}"
        )
        result_path = base_path / directory_name
        try:
            result_path.mkdir()
            return result_path.resolve()
        except FileExistsError:
            suffix += 1
        except OSError as exc:
            raise OSError(
                "创建自适应窗口校准结果目录失败: "
                f"{result_path.resolve()}: {exc}"
            ) from exc


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        cases, variants = _validate_config(args)
    except (TypeError, ValueError) as exc:
        print(f"自适应窗口校准配置无效: {exc}", file=sys.stderr)
        return 1

    try:
        result_path = _create_result_directory(args.output_dir)
        policy = DEFAULT_ADAPTIVE_REPLAN_POLICY
        config = {
            "caseIds": [case.case_id for case in cases],
            "variantIds": [
                variant.variant_id for variant in variants
            ],
            "repetitions": args.repetitions,
            "timeoutSeconds": args.timeout_seconds,
            "tickTarget": cases[0].tick_target,
            "runtimeTaskTicks": list(RUNTIME_TASK_TICKS),
            "outputDir": str(result_path),
            "defaultPolicy": {
                "slowEnterThresholdMs": policy.slow_enter_threshold_ms,
                "slowExitThresholdMs": policy.slow_exit_threshold_ms,
                "taskPressureMultiplier": policy.task_pressure_multiplier,
            },
        }

        def on_result(runs: list[AdaptiveCalibrationRun]) -> None:
            write_partial_report(
                result_path,
                AdaptiveCalibrationReport.create(config, runs),
            )

        runs = run_adaptive_calibration_cases(
            cases,
            variants,
            repetitions=args.repetitions,
            timeout_seconds=args.timeout_seconds,
            on_result=on_result,
        )
        write_final_report(
            result_path,
            AdaptiveCalibrationReport.create(config, runs),
        )
    except Exception as exc:
        print(f"自适应窗口校准失败: {exc}", file=sys.stderr)
        return 1

    print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Add to `package.json` immediately after `benchmark:algorithm`:

```json
"benchmark:adaptive-replan": ".\\.venv\\Scripts\\python.exe -m backend.benchmarks.adaptive_replan_calibration",
```

- [ ] **Step 9: Run GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_adaptive_replan_calibration.py -q
```

Expected: all calibration tests pass.

- [ ] **Step 10: Run the real one-repetition smoke**

Run:

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
  & 'C:\nvm4w\nodejs\npm.cmd' run benchmark:adaptive-replan -- --repetitions 1 --timeout-seconds 30 --output-dir output/adaptive-replan-calibration-smoke |
    Select-Object -Last 1
).Trim()
if ($LASTEXITCODE -ne 0) {
  throw "自适应窗口校准 smoke 失败，退出码: $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $calibrationResultPath -PathType Container)) {
  throw "校准结果目录不存在: $calibrationResultPath"
}
$calibrationResultPath
```

Read that exact `results.json` and assert with project Python:

```powershell
.\.venv\Scripts\python.exe -c "import json,sys; from pathlib import Path; p=Path(sys.argv[1]); d=json.loads((p/'results.json').read_text(encoding='utf-8')); assert len(d['runs'])==12; assert len(d['variantSummaries'])==12; assert all(r['outcome']=='completed' for r in d['runs']); print({'runs':len(d['runs']),'summaries':len(d['variantSummaries'])})" $calibrationResultPath
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
  throw "检测到 smoke 遗留的基准 spawn worker"
}
```

If any run is timeout/error, stop Task 6 and report the exact record. Do not raise timeout values or weaken correctness checks.

- [ ] **Step 11: Commit Task 6**

```powershell
git add -- backend/benchmarks/adaptive_results.py backend/benchmarks/adaptive_reporting.py backend/benchmarks/adaptive_replan_calibration.py backend/tests/test_adaptive_replan_calibration.py package.json
git diff --cached --check
git commit -m "feat: report adaptive replan calibration"
```

---

### Task 7: Documentation, Complete Verification, and Full Calibration Evidence

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/algorithm.md`
- Modify: `docs/experiments.md`
- Modify: `docs/testing-guide.md`

**Interfaces:**
- Consumes: final CLI/report schema and actual full-run evidence.
- Produces: accurate user-facing boundaries and reproducible verification commands.

- [ ] **Step 1: Update `AGENTS.md` without overstating the result**

Add to the algorithm and experiment progress:

- Offline adaptive-window calibration exists.
- It compares fixed `4/24/48` with `adaptive-current-24`.
- It records real online replan decisions and deterministic planning work.
- Production remains `60/40ms` and `2×`.
- Candidate envelope is evidence for a later decision, not an automatic recommendation.
- No complete MAPF or cross-machine threshold claim.

- [ ] **Step 2: Update algorithm semantics**

In `docs/algorithm.md`, document:

- `AdaptiveReplanPolicy` is internal; HTTP callers cannot supply it.
- observer records only real `run_dispatch` calls.
- fixed/cached/passive behavior remains.
- calibration wall-clock values are same-machine evidence.
- production thresholds remain unchanged until a separate approved change.

- [ ] **Step 3: Document command, reports, and manual review**

In `docs/experiments.md` add:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run benchmark:adaptive-replan
```

Document the three case IDs, four variant IDs, 60-run default, four final files, partial file, schema version 1, and candidate availability rule.

In `docs/testing-guide.md`, add smoke and full commands, UTF-8 checks, JSON/CSV count cross-check, no-worker check, and the rule that timeout/error/unstable outcomes must be reported rather than hidden.

- [ ] **Step 4: Run all focused suites**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_replan_window.py backend\tests\test_sessions.py backend\tests\test_benchmark_process_isolation.py backend\tests\test_algorithm_benchmark.py backend\tests\test_adaptive_replan_calibration.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Run the complete project check**

Run:

```powershell
chcp 65001
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
& 'C:\nvm4w\nodejs\npm.cmd' run check
```

Expected: frontend build, all frontend tests, and all backend tests pass.

- [ ] **Step 6: Run the default 60-run calibration with no concurrent heavy command**

Run:

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
  & 'C:\nvm4w\nodejs\npm.cmd' run benchmark:adaptive-replan -- --repetitions 5 --timeout-seconds 30 --output-dir output/adaptive-replan-calibration |
    Select-Object -Last 1
).Trim()
if ($LASTEXITCODE -ne 0) {
  throw "默认自适应窗口校准失败，退出码: $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $calibrationResultPath -PathType Container)) {
  throw "校准结果目录不存在: $calibrationResultPath"
}
$calibrationResultPath
```

Do not run `npm run check`, another benchmark, browser stress, or another heavy agent command concurrently.

- [ ] **Step 7: Cross-check the final artifacts**

Use the captured `$calibrationResultPath`:

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
assert not (result_path / "results.partial.json").exists()

fixed_24_counts = Counter()
for run in runs:
    if (
        run["variantId"] == "fixed-24"
        and run["outcome"] == "completed"
        and run["correctnessStable"]
    ):
        fixed_24_counts[run["caseId"]] += len(
            run["replanObservations"]
        )
expected_envelope_available = all(
    fixed_24_counts[case_id] >= 3 for case_id in known_cases
)
assert (
    payload["candidateEnvelopeAvailable"]
    is expected_envelope_available
)
assert (payload["candidateEnvelope"] is not None) is (
    expected_envelope_available
)

for file_name in (
    "runs.csv",
    "replan-observations.csv",
    "variant-summaries.csv",
):
    assert (result_path / file_name).read_bytes()[:3] == bytes(
        (239, 187, 191)
    )

outcomes = Counter(run["outcome"] for run in runs)
unstable = [
    {
        "caseId": run["caseId"],
        "variantId": run["variantId"],
        "runIndex": run["runIndex"],
        "predictedConflictCount": run["predictedConflictCount"],
        "activeConflictCount": run["activeConflictCount"],
        "deadlineMissCount": run["deadlineMissCount"],
        "failureCount": run["failureCount"],
        "safetyInterventionCount": run["safetyInterventionCount"],
        "actualCompletionRatePercent": (
            run["actualCompletionRatePercent"]
        ),
    }
    for run in runs
    if run["outcome"] == "completed"
    and not run["correctnessStable"]
]
print(
    json.dumps(
        {
            "runs": len(runs),
            "observations": len(observation_rows),
            "summaries": len(summary_rows),
            "outcomes": dict(outcomes),
            "unstableCompletedRuns": unstable,
        },
        ensure_ascii=False,
    )
)
'@
$artifactCheck | .\.venv\Scripts\python.exe - $calibrationResultPath

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
  throw "检测到默认校准遗留的基准 spawn worker"
}
```

If any completed run is not stable, record its `caseId`, `variantId`, `runIndex`, conflicts, deadline misses, failures, safety interventions, and completion rate. Do not change policy values in this branch.

- [ ] **Step 8: Run encoding and Git hygiene checks**

Run:

```powershell
git diff --check
git status --short
git diff --name-only 14c52d2..HEAD
```

Expected tracked scope:

```text
AGENTS.md
backend/app/replan_window.py
backend/app/sessions.py
backend/benchmarks/adaptive_replan_calibration.py
backend/benchmarks/adaptive_reporting.py
backend/benchmarks/adaptive_results.py
backend/benchmarks/adaptive_runner.py
backend/benchmarks/adaptive_scenarios.py
backend/benchmarks/adaptive_variants.py
backend/benchmarks/process_isolation.py
backend/benchmarks/runner.py
backend/tests/test_adaptive_replan_calibration.py
backend/tests/test_algorithm_benchmark.py
backend/tests/test_benchmark_process_isolation.py
backend/tests/test_replan_window.py
backend/tests/test_sessions.py
docs/algorithm.md
docs/experiments.md
docs/superpowers/plans/2026-07-26-adaptive-replan-calibration.md
docs/testing-guide.md
package.json
```

Generated `output/`, `.superpowers/`, dependency junctions, and caches must remain untracked.

- [ ] **Step 9: Commit Task 7**

```powershell
git add -- AGENTS.md docs/algorithm.md docs/experiments.md docs/testing-guide.md
git diff --cached --check
git commit -m "docs: document adaptive replan calibration"
```

- [ ] **Step 10: Request final branch review**

Review range:

```text
14c52d2..HEAD
```

Treat as blocking:

- any production threshold or API change;
- observer records on cached/passive responses;
- observer changes dispatch results;
- unsafe calibration sessions;
- copied/divergent process cleanup;
- report/model field mismatch;
- candidate envelope built from non-stable/non-`fixed-24` data;
- process residue or unbounded wait;
- generated report committed.

After review fixes, rerun Task 7 Steps 4–8 before claiming completion.

---

## Final Acceptance Checklist

- [ ] Default `AdaptiveReplanPolicy` is exactly `60/40ms` and pressure multiplier `2`.
- [ ] HTTP and frontend contracts are unchanged.
- [ ] Observer is absent for production sessions and emits only on real replans.
- [ ] Existing boundary benchmark schema remains version 2 and its nine cases remain exact.
- [ ] Shared process isolation retains poll-before-join and bounded terminate/kill behavior.
- [ ] Three calibration cases and four variants are exact and deterministic.
- [ ] Smoke produces 12 completed run records.
- [ ] Full calibration produces 60 run records and 12 variant summaries.
- [ ] Observation CSV row count equals summed `replanCount`.
- [ ] Candidate envelope uses only stable completed `fixed-24` observations and requires three observations from each case.
- [ ] Candidate envelope does not modify production policy.
- [ ] Full `npm run check` passes.
- [ ] Generated output remains untracked.
