from dataclasses import replace

from backend.benchmarks import solvability_runner as runner_module
from backend.benchmarks.solvability_cases import solvability_catalog
from backend.benchmarks.solvability_runner import (
    build_fixed_assignment_input,
    classify_comparison,
    execute_solvability_case,
)
from backend.benchmarks.solvability_results import SolvabilityReport


def _case(case_id: str):
    return next(case for case in solvability_catalog() if case.case_id == case_id)


def test_fixed_assignment_adapter_preserves_agent_start_goal_and_order() -> None:
    case = _case("catalog-side-bypass-swap-r2")

    scenario, assignments = build_fixed_assignment_input(case)

    assert [robot.id for robot in scenario.robots] == ["R1", "R2"]
    assert [robot.start for robot in scenario.robots] == [(0, 0), (2, 0)]
    assert all(robot.moveTicks == 1 for robot in scenario.robots)
    assert all(robot.capabilities == ["inspection"] for robot in scenario.robots)
    assert [assignment.robotId for assignment in assignments] == ["R1", "R2"]
    assert [assignment.tasks[0].targets for assignment in assignments] == [
        [(2, 0)],
        [(0, 0)],
    ]
    assert all(assignment.tasks[0].serviceTime == 0 for assignment in assignments)


def test_comparison_classification_has_exact_truth_table() -> None:
    assert classify_comparison("solved", "solved") == "agreementSolved"
    assert classify_comparison("solved", "failed") == "oracleSolvedPlannerMiss"
    assert classify_comparison("solved", "conflicted") == "oracleSolvedPlannerMiss"
    assert (
        classify_comparison("unsolved", "failed")
        == "oracleUnsolvedPlannerNoValidPlan"
    )
    assert (
        classify_comparison("unsolved", "conflicted")
        == "oracleUnsolvedPlannerNoValidPlan"
    )
    assert (
        classify_comparison("unsolved", "solved")
        == "oracleUnsolvedPlannerSolved"
    )
    assert classify_comparison("limit", "solved") == "oracleLimit"


def test_execute_solvability_case_agrees_on_simple_case() -> None:
    run = execute_solvability_case(
        _case("catalog-solo-straight"),
        run_index=1,
        max_expanded_states=100_000,
    )

    assert run.outcome == "completed"
    assert run.oracle_outcome == "solved"
    assert run.planner_outcome == "solved"
    assert run.comparison_class == "agreementSolved"
    assert run.planner_failure_count == 0
    assert run.planner_conflict_count == 0


def test_execute_solvability_case_marks_valid_oracle_and_conflicted_planner_as_miss(
    monkeypatch,
) -> None:
    def conflicting_paths(scenario, robots, assignments, *args, **kwargs):
        return {
            "R1": [(0, 0), (1, 0), (2, 0)],
            "R2": [(2, 0), (1, 0), (0, 0)],
        }, []

    monkeypatch.setattr(runner_module, "build_paths", conflicting_paths)

    run = execute_solvability_case(
        _case("catalog-side-bypass-swap-r2"),
        run_index=1,
        max_expanded_states=100_000,
    )

    assert run.planner_outcome == "conflicted"
    assert run.planner_conflict_count > 0
    assert run.comparison_class == "oracleSolvedPlannerMiss"


def test_no_bypass_unsolved_case_is_not_reported_as_planner_miss() -> None:
    run = execute_solvability_case(
        _case("catalog-no-bypass-swap-r2"),
        run_index=1,
        max_expanded_states=100_000,
    )

    assert run.oracle_outcome == "unsolved"
    assert run.planner_outcome in {"failed", "conflicted"}
    assert run.comparison_class == "oracleUnsolvedPlannerNoValidPlan"


def test_solvability_report_preserves_classification_and_limit_semantics() -> None:
    solved_case = _case("catalog-solo-straight")
    limited_case = replace(
        _case("catalog-independent-r2"),
        case_id="generated-limit-case",
        source="generated",
        expected_oracle_outcome=None,
    )
    agreement_run = execute_solvability_case(solved_case, 1, 100_000)
    miss_run = replace(
        agreement_run,
        run_index=2,
        planner_outcome="failed",
        planner_reached_all_goals=False,
        planner_failure_count=1,
        planner_failures=["forced planner miss"],
        comparison_class="oracleSolvedPlannerMiss",
    )
    limit_run = replace(
        execute_solvability_case(limited_case, 1, 100_000),
        oracle_outcome="limit",
        oracle_makespan=None,
        oracle_paths=None,
        comparison_class="oracleLimit",
    )
    report = SolvabilityReport.create(
        config={"seed": 20260904},
        cases=[solved_case, limited_case],
        runs=[agreement_run, miss_run, limit_run],
    )

    assert report.schema_version == 1
    assert report.candidate_counterexample_case_ids == [solved_case.case_id]
    assert report.to_record()["schemaVersion"] == 1
    assert report.to_record()["cases"][0]["caseId"] == solved_case.case_id
    assert report.case_summaries[0].run_count == 2
    assert report.case_summaries[0].oracle_solved_planner_miss_count == 1
