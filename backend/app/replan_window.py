from dataclasses import dataclass


MIN_ADAPTIVE_REPLAN_WINDOW = 4
MAX_ADAPTIVE_REPLAN_WINDOW = 120
SLOW_REPLAN_THRESHOLD_MS = 50


@dataclass(frozen=True)
class ReplanWindowDecision:
    window: int
    reason: str


def decide_replan_window(
    *,
    configured_window: int,
    adaptive: bool,
    released_task_count: int,
    future_task_count: int,
    active_robot_count: int,
    recent_replan_time_ms: float | None,
) -> ReplanWindowDecision:
    if not adaptive:
        return ReplanWindowDecision(window=configured_window, reason="固定窗口")

    baseline = min(
        MAX_ADAPTIVE_REPLAN_WINDOW,
        max(MIN_ADAPTIVE_REPLAN_WINDOW, configured_window),
    )
    contracted = max(MIN_ADAPTIVE_REPLAN_WINDOW, baseline // 2)
    expanded = min(MAX_ADAPTIVE_REPLAN_WINDOW, baseline * 2)

    if recent_replan_time_ms is not None and recent_replan_time_ms >= SLOW_REPLAN_THRESHOLD_MS:
        return ReplanWindowDecision(
            window=contracted,
            reason="近期规划耗时较高，收缩窗口",
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
