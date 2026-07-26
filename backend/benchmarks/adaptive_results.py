from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from statistics import median
from typing import Literal

from backend.app.replan_window import ReplanObservation
from backend.benchmarks.adaptive_scenarios import (
    AdaptiveCalibrationCase,
    adaptive_calibration_cases,
)
from backend.benchmarks.adaptive_variants import AdaptiveCalibrationVariant
from backend.benchmarks.results import percent


Number = int | float
AdaptiveCalibrationOutcome = Literal["completed", "timeout", "error"]


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
