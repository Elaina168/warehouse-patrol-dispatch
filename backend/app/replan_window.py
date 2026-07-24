from dataclasses import dataclass
from statistics import median


MIN_ADAPTIVE_REPLAN_WINDOW = 4
MAX_ADAPTIVE_REPLAN_WINDOW = 120
REPLAN_TIME_SAMPLE_WINDOW = 5
MIN_REPLAN_TIME_SAMPLES = 3
SLOW_REPLAN_ENTER_THRESHOLD_MS = 60
SLOW_REPLAN_EXIT_THRESHOLD_MS = 40


@dataclass(frozen=True)
class ReplanWindowDecision:
    window: int
    reason: str


def update_latency_slow_state(
    samples_ms: list[float],
    current_slow: bool,
) -> bool:
    recent = samples_ms[-REPLAN_TIME_SAMPLE_WINDOW:]
    if len(recent) < MIN_REPLAN_TIME_SAMPLES:
        return current_slow
    value = median(recent)
    if not current_slow and value >= SLOW_REPLAN_ENTER_THRESHOLD_MS:
        return True
    if current_slow and value <= SLOW_REPLAN_EXIT_THRESHOLD_MS:
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
) -> ReplanWindowDecision:
    if not adaptive:
        return ReplanWindowDecision(window=configured_window, reason="固定窗口")

    baseline = min(
        MAX_ADAPTIVE_REPLAN_WINDOW,
        max(MIN_ADAPTIVE_REPLAN_WINDOW, configured_window),
    )
    contracted = max(MIN_ADAPTIVE_REPLAN_WINDOW, baseline // 2)
    expanded = min(MAX_ADAPTIVE_REPLAN_WINDOW, baseline * 2)

    if latency_slow:
        return ReplanWindowDecision(
            window=contracted,
            reason="近期规划耗时中位数较高，收缩窗口",
        )

    robot_count = max(1, active_robot_count)
    if released_task_count >= robot_count * 2:
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
