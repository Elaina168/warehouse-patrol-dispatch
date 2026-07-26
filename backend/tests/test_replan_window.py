import pytest

from backend.app.replan_window import (
    MIN_REPLAN_TIME_SAMPLES,
    REPLAN_TIME_SAMPLE_WINDOW,
    SLOW_REPLAN_ENTER_THRESHOLD_MS,
    SLOW_REPLAN_EXIT_THRESHOLD_MS,
    decide_replan_window,
    update_latency_slow_state,
)


@pytest.mark.parametrize("samples", [[], [100], [100, 100]])
def test_latency_state_requires_three_samples(samples: list[float]) -> None:
    assert update_latency_slow_state(samples, False) is False


def test_latency_state_uses_only_latest_five_sample_median() -> None:
    samples = [0, 0, 50, 60, 100, 100]

    assert update_latency_slow_state(samples, False) is True


def test_latency_state_enters_at_60_and_exits_at_40() -> None:
    assert update_latency_slow_state([60, 60, 60], False) is True
    assert update_latency_slow_state([40, 40, 40], True) is False


def test_latency_state_is_sticky_between_40_and_60() -> None:
    samples = [50, 50, 50]

    assert update_latency_slow_state(samples, False) is False
    assert update_latency_slow_state(samples, True) is True


def test_fixed_replan_window_preserves_configured_value() -> None:
    decision = decide_replan_window(
        configured_window=24,
        adaptive=False,
        released_task_count=20,
        future_task_count=3,
        active_robot_count=2,
        latency_slow=True,
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
        latency_slow=True,
    )

    assert decision.window == 12
    assert decision.reason == "近期规划耗时中位数较高，收缩窗口"


def test_adaptive_replan_window_shrinks_for_task_pressure() -> None:
    decision = decide_replan_window(
        configured_window=24,
        adaptive=True,
        released_task_count=8,
        future_task_count=2,
        active_robot_count=4,
        latency_slow=False,
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
        latency_slow=False,
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
        latency_slow=False,
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
        latency_slow=False,
    )

    assert decision.window == 120
