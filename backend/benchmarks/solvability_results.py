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
    def timeout(
        cls,
        case: SolvabilityCase,
        run_index: int,
        wall_clock_ms: float,
    ) -> "SolvabilityRun":
        return cls.failed_run(
            case,
            run_index,
            "timeout",
            "TimeoutError",
            None,
            wall_clock_ms,
        )

    @classmethod
    def error(
        cls,
        case: SolvabilityCase,
        run_index: int,
        error_type: str,
        error_message: str,
        wall_clock_ms: float,
    ) -> "SolvabilityRun":
        return cls.failed_run(
            case,
            run_index,
            "error",
            error_type,
            error_message,
            wall_clock_ms,
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
