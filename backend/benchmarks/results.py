from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil
from statistics import median
from typing import Literal

from backend.benchmarks.scenarios import BenchmarkCase, BenchmarkFamily, BenchmarkMode

BenchmarkOutcome = Literal["completed", "timeout", "error"]
Number = int | float


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    case_id: str
    family: BenchmarkFamily
    mode: BenchmarkMode
    seed: int | None
    run_index: int
    robot_count: int
    task_count: int
    dynamic_task_count: int
    obstacle_count: int | None
    tick_target: int | None
    outcome: BenchmarkOutcome
    error_type: str | None
    error_message: str | None
    correctness_stable: bool
    released_task_count: int | None
    covered_task_count: int | None
    assigned_task_count: int | None
    completed_task_count: int | None
    assignment_rate_percent: Number | None
    coverage_rate_percent: Number | None
    actual_completion_rate_percent: Number | None
    predicted_conflict_count: int | None
    active_conflict_count: int | None
    execution_safety_evaluated: bool
    safety_intervention_count: int
    deadline_miss_count: int | None
    failure_count: int | None
    total_distance: Number | None
    makespan: Number | None
    replan_time_ms: Number | None
    max_snapshot_replan_time_ms: Number | None
    wall_clock_ms: Number | None
    planning_diagnostics_evaluated: bool
    path_candidate_count: int | None
    selected_path_candidate_index: int | None
    failed_path_candidate_count: int | None
    timed_astar_call_count: int | None
    timed_astar_expanded_state_count: int | None
    max_timed_astar_expanded_state_count: int | None
    timed_astar_exhausted_search_count: int | None
    timed_astar_goal_fully_reserved_reject_count: int | None
    runtime_task_count: int
    runtime_mutation_count: int | None
    replan_observation_count: int | None
    assignment_candidate_expansion_count: int | None
    assignment_robot_state_copy_count: int | None
    assignment_beam_peak_width: int | None

    @classmethod
    def timeout(cls, case: BenchmarkCase, run_index: int, wall_clock_ms: Number) -> "BenchmarkRun":
        return cls._failed(case, run_index, "timeout", "TimeoutError", None, wall_clock_ms)

    @classmethod
    def error(
        cls,
        case: BenchmarkCase,
        run_index: int,
        error_type: str,
        error_message: str,
        wall_clock_ms: Number,
    ) -> "BenchmarkRun":
        return cls._failed(case, run_index, "error", error_type, error_message, wall_clock_ms)

    @classmethod
    def _failed(
        cls,
        case: BenchmarkCase,
        run_index: int,
        outcome: Literal["timeout", "error"],
        error_type: str,
        error_message: str | None,
        wall_clock_ms: Number,
    ) -> "BenchmarkRun":
        return cls(
            case_id=case.case_id,
            family=case.family,
            mode=case.mode,
            seed=case.seed,
            run_index=run_index,
            robot_count=case.robot_count,
            task_count=case.task_count,
            dynamic_task_count=case.dynamic_task_count,
            obstacle_count=None,
            tick_target=case.tick_target,
            outcome=outcome,
            error_type=error_type,
            error_message=error_message,
            correctness_stable=False,
            released_task_count=None,
            covered_task_count=None,
            assigned_task_count=None,
            completed_task_count=None,
            assignment_rate_percent=None,
            coverage_rate_percent=None,
            actual_completion_rate_percent=None,
            predicted_conflict_count=None,
            active_conflict_count=None,
            execution_safety_evaluated=case.mode == "online",
            safety_intervention_count=0,
            deadline_miss_count=None,
            failure_count=None,
            total_distance=None,
            makespan=None,
            replan_time_ms=None,
            max_snapshot_replan_time_ms=None,
            wall_clock_ms=wall_clock_ms,
            planning_diagnostics_evaluated=False,
            path_candidate_count=None,
            selected_path_candidate_index=None,
            failed_path_candidate_count=None,
            timed_astar_call_count=None,
            timed_astar_expanded_state_count=None,
            max_timed_astar_expanded_state_count=None,
            timed_astar_exhausted_search_count=None,
            timed_astar_goal_fully_reserved_reject_count=None,
            runtime_task_count=case.runtime_task_count,
            runtime_mutation_count=None,
            replan_observation_count=None,
            assignment_candidate_expansion_count=None,
            assignment_robot_state_copy_count=None,
            assignment_beam_peak_width=None,
        )

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "family": self.family,
            "mode": self.mode,
            "seed": self.seed,
            "runIndex": self.run_index,
            "robotCount": self.robot_count,
            "taskCount": self.task_count,
            "dynamicTaskCount": self.dynamic_task_count,
            "obstacleCount": self.obstacle_count,
            "tickTarget": self.tick_target,
            "outcome": self.outcome,
            "errorType": self.error_type,
            "errorMessage": self.error_message,
            "correctnessStable": self.correctness_stable,
            "releasedTaskCount": self.released_task_count,
            "coveredTaskCount": self.covered_task_count,
            "assignedTaskCount": self.assigned_task_count,
            "completedTaskCount": self.completed_task_count,
            "assignmentRatePercent": self.assignment_rate_percent,
            "coverageRatePercent": self.coverage_rate_percent,
            "actualCompletionRatePercent": self.actual_completion_rate_percent,
            "predictedConflictCount": self.predicted_conflict_count,
            "activeConflictCount": self.active_conflict_count,
            "executionSafetyEvaluated": self.execution_safety_evaluated,
            "safetyInterventionCount": self.safety_intervention_count,
            "deadlineMissCount": self.deadline_miss_count,
            "failureCount": self.failure_count,
            "totalDistance": self.total_distance,
            "makespan": self.makespan,
            "replanTimeMs": self.replan_time_ms,
            "maxSnapshotReplanTimeMs": self.max_snapshot_replan_time_ms,
            "wallClockMs": self.wall_clock_ms,
            "planningDiagnosticsEvaluated": self.planning_diagnostics_evaluated,
            "pathCandidateCount": self.path_candidate_count,
            "selectedPathCandidateIndex": self.selected_path_candidate_index,
            "failedPathCandidateCount": self.failed_path_candidate_count,
            "timedAStarCallCount": self.timed_astar_call_count,
            "timedAStarExpandedStateCount": self.timed_astar_expanded_state_count,
            "maxTimedAStarExpandedStateCount": self.max_timed_astar_expanded_state_count,
            "timedAStarExhaustedSearchCount": self.timed_astar_exhausted_search_count,
            "timedAStarGoalFullyReservedRejectCount": (
                self.timed_astar_goal_fully_reserved_reject_count
            ),
            "runtimeTaskCount": self.runtime_task_count,
            "runtimeMutationCount": self.runtime_mutation_count,
            "replanObservationCount": self.replan_observation_count,
            "assignmentCandidateExpansionCount": (
                self.assignment_candidate_expansion_count
            ),
            "assignmentRobotStateCopyCount": self.assignment_robot_state_copy_count,
            "assignmentBeamPeakWidth": self.assignment_beam_peak_width,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkCaseSummary:
    case_id: str
    run_count: int
    completed_run_count: int
    timeout_count: int
    error_count: int
    stable_run_count: int
    stable_run_rate_percent: Number
    median_wall_clock_ms: Number | None
    p95_wall_clock_ms: Number | None
    median_replan_time_ms: Number | None
    p95_replan_time_ms: Number | None
    max_safety_intervention_count: int
    median_timed_astar_expanded_state_count: Number | None
    p95_timed_astar_expanded_state_count: Number | None
    max_timed_astar_goal_fully_reserved_reject_count: int | None
    median_replan_observation_count: Number | None
    median_assignment_candidate_expansion_count: Number | None
    p95_assignment_candidate_expansion_count: Number | None
    median_assignment_robot_state_copy_count: Number | None
    p95_assignment_robot_state_copy_count: Number | None
    max_assignment_beam_peak_width: int | None

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "runCount": self.run_count,
            "completedRunCount": self.completed_run_count,
            "timeoutCount": self.timeout_count,
            "errorCount": self.error_count,
            "stableRunCount": self.stable_run_count,
            "stableRunRatePercent": self.stable_run_rate_percent,
            "medianWallClockMs": self.median_wall_clock_ms,
            "p95WallClockMs": self.p95_wall_clock_ms,
            "medianReplanTimeMs": self.median_replan_time_ms,
            "p95ReplanTimeMs": self.p95_replan_time_ms,
            "maxSafetyInterventionCount": self.max_safety_intervention_count,
            "medianTimedAStarExpandedStateCount": (
                self.median_timed_astar_expanded_state_count
            ),
            "p95TimedAStarExpandedStateCount": (
                self.p95_timed_astar_expanded_state_count
            ),
            "maxTimedAStarGoalFullyReservedRejectCount": (
                self.max_timed_astar_goal_fully_reserved_reject_count
            ),
            "medianReplanObservationCount": self.median_replan_observation_count,
            "medianAssignmentCandidateExpansionCount": (
                self.median_assignment_candidate_expansion_count
            ),
            "p95AssignmentCandidateExpansionCount": (
                self.p95_assignment_candidate_expansion_count
            ),
            "medianAssignmentRobotStateCopyCount": (
                self.median_assignment_robot_state_copy_count
            ),
            "p95AssignmentRobotStateCopyCount": (
                self.p95_assignment_robot_state_copy_count
            ),
            "maxAssignmentBeamPeakWidth": self.max_assignment_beam_peak_width,
        }


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
        return cls(3, generated_at, config, list(runs), summarize_runs(runs))

    def to_record(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "generatedAt": self.generated_at,
            "config": self.config,
            "runs": [run.to_record() for run in self.runs],
            "caseSummaries": [summary.to_record() for summary in self.case_summaries],
        }


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
        planning_runs = [
            run
            for run in completed
            if run.planning_diagnostics_evaluated
            and run.timed_astar_expanded_state_count is not None
        ]
        expanded_states = [
            run.timed_astar_expanded_state_count
            for run in planning_runs
            if run.timed_astar_expanded_state_count is not None
        ]
        fully_reserved_rejects = [
            run.timed_astar_goal_fully_reserved_reject_count
            for run in planning_runs
            if run.timed_astar_goal_fully_reserved_reject_count is not None
        ]
        assignment_runs = [
            run
            for run in completed
            if run.planning_diagnostics_evaluated
            and run.replan_observation_count is not None
            and run.assignment_candidate_expansion_count is not None
            and run.assignment_robot_state_copy_count is not None
            and run.assignment_beam_peak_width is not None
        ]
        replan_observations = [
            run.replan_observation_count
            for run in assignment_runs
            if run.replan_observation_count is not None
        ]
        assignment_expansions = [
            run.assignment_candidate_expansion_count
            for run in assignment_runs
            if run.assignment_candidate_expansion_count is not None
        ]
        assignment_copies = [
            run.assignment_robot_state_copy_count
            for run in assignment_runs
            if run.assignment_robot_state_copy_count is not None
        ]
        assignment_beam_widths = [
            run.assignment_beam_peak_width
            for run in assignment_runs
            if run.assignment_beam_peak_width is not None
        ]
        stable_run_count = sum(run.correctness_stable for run in case_runs)
        summaries.append(
            BenchmarkCaseSummary(
                case_id=case_id,
                run_count=len(case_runs),
                completed_run_count=len(completed),
                timeout_count=sum(run.outcome == "timeout" for run in case_runs),
                error_count=sum(run.outcome == "error" for run in case_runs),
                stable_run_count=stable_run_count,
                stable_run_rate_percent=percent(stable_run_count, len(case_runs)),
                median_wall_clock_ms=median(wall) if wall else None,
                p95_wall_clock_ms=nearest_rank_p95(wall),
                median_replan_time_ms=median(replan) if replan else None,
                p95_replan_time_ms=nearest_rank_p95(replan),
                max_safety_intervention_count=max(
                    (run.safety_intervention_count for run in case_runs), default=0
                ),
                median_timed_astar_expanded_state_count=(
                    median(expanded_states) if expanded_states else None
                ),
                p95_timed_astar_expanded_state_count=nearest_rank_p95(expanded_states),
                max_timed_astar_goal_fully_reserved_reject_count=(
                    max(fully_reserved_rejects) if fully_reserved_rejects else None
                ),
                median_replan_observation_count=(
                    median(replan_observations) if replan_observations else None
                ),
                median_assignment_candidate_expansion_count=(
                    median(assignment_expansions) if assignment_expansions else None
                ),
                p95_assignment_candidate_expansion_count=nearest_rank_p95(
                    assignment_expansions
                ),
                median_assignment_robot_state_copy_count=(
                    median(assignment_copies) if assignment_copies else None
                ),
                p95_assignment_robot_state_copy_count=nearest_rank_p95(
                    assignment_copies
                ),
                max_assignment_beam_peak_width=(
                    max(assignment_beam_widths) if assignment_beam_widths else None
                ),
            )
        )
    return summaries
