import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median


MIN_ADAPTIVE_REPLAN_WINDOW = 4
MAX_ADAPTIVE_REPLAN_WINDOW = 120
REPLAN_TIME_SAMPLE_WINDOW = 5
MIN_REPLAN_TIME_SAMPLES = 3
SLOW_REPLAN_ENTER_THRESHOLD_MS = 60
SLOW_REPLAN_EXIT_THRESHOLD_MS = 40


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


@dataclass(frozen=True)
class ReplanWindowDecision:
    window: int
    reason: str


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
