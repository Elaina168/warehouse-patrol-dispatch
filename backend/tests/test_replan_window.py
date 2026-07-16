from backend.app.replan_window import decide_replan_window


def test_fixed_replan_window_preserves_configured_value() -> None:
    decision = decide_replan_window(
        configured_window=24,
        adaptive=False,
        released_task_count=20,
        future_task_count=3,
        active_robot_count=2,
        recent_replan_time_ms=120,
    )

    assert decision.window == 24
    assert decision.reason == "固定窗口"


def test_adaptive_replan_window_shrinks_for_slow_planning() -> None:
    decision = decide_replan_window(
        configured_window=24,
        adaptive=True,
        released_task_count=0,
        future_task_count=2,
        active_robot_count=4,
        recent_replan_time_ms=50,
    )

    assert decision.window == 12
    assert decision.reason == "近期规划耗时较高，收缩窗口"


def test_adaptive_replan_window_shrinks_for_task_pressure() -> None:
    decision = decide_replan_window(
        configured_window=24,
        adaptive=True,
        released_task_count=8,
        future_task_count=2,
        active_robot_count=4,
        recent_replan_time_ms=10,
    )

    assert decision.window == 12
    assert decision.reason == "当前任务压力较高，收缩窗口"


def test_adaptive_replan_window_expands_for_idle_future_work() -> None:
    decision = decide_replan_window(
        configured_window=24,
        adaptive=True,
        released_task_count=0,
        future_task_count=2,
        active_robot_count=4,
        recent_replan_time_ms=10,
    )

    assert decision.window == 48
    assert decision.reason == "当前负载较低且存在远期任务，扩大窗口"


def test_adaptive_replan_window_keeps_normalized_baseline_for_balanced_work() -> None:
    decision = decide_replan_window(
        configured_window=2,
        adaptive=True,
        released_task_count=2,
        future_task_count=0,
        active_robot_count=4,
        recent_replan_time_ms=None,
    )

    assert decision.window == 4
    assert decision.reason == "当前负载适中，保持基准窗口"


def test_adaptive_replan_window_respects_maximum() -> None:
    decision = decide_replan_window(
        configured_window=80,
        adaptive=True,
        released_task_count=0,
        future_task_count=1,
        active_robot_count=4,
        recent_replan_time_ms=0,
    )

    assert decision.window == 120
