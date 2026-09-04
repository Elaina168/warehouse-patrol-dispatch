from dataclasses import replace
import csv
import json
from pathlib import Path
import time

import pytest

from backend.benchmarks import solvability_runner as runner_module
from backend.benchmarks.solvability_cases import solvability_catalog
from backend.benchmarks.process_isolation import BenchmarkInfrastructureError
from backend.benchmarks.solvability_runner import (
    build_fixed_assignment_input,
    classify_comparison,
    execute_solvability_case,
    run_isolated_solvability_case,
    run_solvability_cases,
)
from backend.benchmarks.solvability_reporting import (
    write_final_report,
    write_partial_report,
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


def _sleeping_worker(case, run_index, max_expanded_states):
    time.sleep(0.2)


def _failing_worker(case, run_index, max_expanded_states):
    raise RuntimeError("worker failed\nwith second line")


def test_isolated_solvability_case_preserves_timeout() -> None:
    run = run_isolated_solvability_case(
        _case("catalog-solo-straight"),
        1,
        100_000,
        0.05,
        worker_callable=_sleeping_worker,
    )

    assert run.outcome == "timeout"
    assert run.error_type == "TimeoutError"
    assert run.comparison_class is None


def test_isolated_solvability_case_sanitizes_worker_error() -> None:
    run = run_isolated_solvability_case(
        _case("catalog-solo-straight"),
        1,
        100_000,
        5,
        worker_callable=_failing_worker,
    )

    assert run.outcome == "error"
    assert run.error_type == "RuntimeError"
    assert "\n" not in run.error_message


def test_solvability_batch_reports_cumulative_copies(monkeypatch) -> None:
    observed = []

    def fake_isolated(case, run_index, max_expanded_states, timeout_seconds):
        return execute_solvability_case(case, run_index, max_expanded_states)

    monkeypatch.setattr(runner_module, "run_isolated_solvability_case", fake_isolated)
    cases = (_case("catalog-solo-straight"), _case("catalog-independent-r2"))

    runs = run_solvability_cases(
        cases,
        repetitions=2,
        max_expanded_states=100_000,
        timeout_seconds=5,
        on_result=lambda current: observed.append(current),
    )

    assert len(runs) == 4
    assert [len(item) for item in observed] == [1, 2, 3, 4]
    assert observed[-1] is not runs


def test_solvability_batch_propagates_infrastructure_error(monkeypatch) -> None:
    def failing_isolated(case, run_index, max_expanded_states, timeout_seconds):
        raise BenchmarkInfrastructureError("worker cleanup failed")

    monkeypatch.setattr(
        runner_module,
        "run_isolated_solvability_case",
        failing_isolated,
    )

    with pytest.raises(BenchmarkInfrastructureError, match="worker cleanup failed"):
        run_solvability_cases(
            (_case("catalog-solo-straight"),),
            repetitions=1,
            max_expanded_states=100_000,
            timeout_seconds=5,
        )


def test_solvability_report_writes_utf8_json_and_bom_csv(tmp_path) -> None:
    case = _case("catalog-solo-straight")
    run = execute_solvability_case(case, 1, 100_000)
    report = SolvabilityReport.create({"seed": 20260904}, [case], [run])

    write_partial_report(tmp_path, report)
    assert (tmp_path / "results.partial.json").exists()

    write_final_report(tmp_path, report)

    assert not (tmp_path / "results.partial.json").exists()
    assert (tmp_path / "results.json").read_bytes().startswith(b"{")
    assert (tmp_path / "runs.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    assert (tmp_path / "case-summaries.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    payload = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == 1
    with (tmp_path / "runs.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["caseId"] == case.case_id
    assert json.loads(rows[0]["oraclePaths"])["R1"][-1] == [2, 0]


def test_solvability_report_rolls_back_all_files_when_runs_publish_fails(
    tmp_path,
    monkeypatch,
) -> None:
    case = _case("catalog-solo-straight")
    run = execute_solvability_case(case, 1, 100_000)
    report = SolvabilityReport.create({"seed": 20260904}, [case], [run])
    write_partial_report(tmp_path, report)
    partial_content = (tmp_path / "results.partial.json").read_bytes()
    original_files = {
        "results.json": b"old results",
        "runs.csv": b"old runs",
        "case-summaries.csv": b"old summaries",
    }
    for name, content in original_files.items():
        (tmp_path / name).write_bytes(content)

    real_replace = Path.replace

    def fail_runs_publish(source, target):
        source_path = Path(source)
        target_path = Path(target)
        if source_path.suffix == ".tmp" and target_path.name == "runs.csv":
            raise OSError("runs publish failed")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_runs_publish)

    with pytest.raises(OSError, match="runs publish failed"):
        write_final_report(tmp_path, report)

    assert {
        name: (tmp_path / name).read_bytes()
        for name in ("results.json", "runs.csv", "case-summaries.csv")
    } == original_files
    assert (tmp_path / "results.partial.json").read_bytes() == partial_content
    assert not list(tmp_path.glob("*.tmp"))
