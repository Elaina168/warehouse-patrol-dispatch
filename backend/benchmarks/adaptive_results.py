from dataclasses import dataclass
from typing import Literal

from backend.app.replan_window import ReplanObservation
from backend.benchmarks.adaptive_scenarios import AdaptiveCalibrationCase
from backend.benchmarks.adaptive_variants import AdaptiveCalibrationVariant


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
