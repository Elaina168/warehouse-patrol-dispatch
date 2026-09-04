import datetime as datetime_module
from dataclasses import replace
import csv
import json
from pathlib import Path
import time

import pytest

from backend.app.dispatch import (
    build_paths_for_order,
    detect_conflicts,
    repair_local_joint_conflicts,
)
from backend.benchmarks import solvability_runner as runner_module
from backend.benchmarks import solvability_differential as cli_module
from backend.benchmarks.solvability_cases import (
    generate_solvability_cases,
    solvability_catalog,
)
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
from backend.benchmarks.solvability_differential import main, parse_args


def _case(case_id: str):
    return next(case for case in solvability_catalog() if case.case_id == case_id)


def _seeded_i0007_case():
    return next(
        case
        for case in generate_solvability_cases(seed=20260904, sample_count=7)
        if case.case_id == "generated-s20260904-i0007"
    )


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


def test_seeded_i0007_is_repaired_without_execution_conflicts() -> None:
    case = _seeded_i0007_case()

    assert case.width == 3
    assert case.height == 3
    assert case.obstacles == ((0, 1), (1, 1))
    assert [(agent.agent_id, agent.start, agent.goal) for agent in case.agents] == [
        ("R1", (0, 2), (1, 0)),
        ("R2", (1, 2), (2, 1)),
    ]

    run = execute_solvability_case(
        case,
        run_index=1,
        max_expanded_states=100_000,
    )

    assert run.oracle_outcome == "solved"
    assert run.oracle_makespan == 5
    assert run.planner_outcome == "solved"
    assert run.planner_failure_count == 0
    assert run.planner_conflict_count == 0
    assert run.comparison_class == "agreementSolved"


def test_local_joint_repair_respects_the_finite_tick_window() -> None:
    case = _seeded_i0007_case()
    scenario, assignments = build_fixed_assignment_input(case)
    candidate = build_paths_for_order(
        scenario,
        scenario.robots,
        assignments,
        True,
        [],
        [],
        scenario.robots,
    )

    assert candidate.failures == []
    assert candidate.paths["R2"] == [(1, 2), (2, 2), (2, 1)]
    assert repair_local_joint_conflicts(
        scenario,
        scenario.robots,
        assignments,
        candidate.paths,
        candidate.failures,
        max_planned_path_ticks=4,
    ) is None


def test_local_joint_repair_rejects_late_conflicts_with_failures() -> None:
    case = _case("catalog-side-bypass-swap-r2")
    scenario, assignments = build_fixed_assignment_input(case)
    paths = {
        "R1": [(0, 0)] * 13 + [(1, 0), (2, 0)],
        "R2": [(2, 0)] * 13 + [(1, 0), (0, 0)],
    }

    assert detect_conflicts(paths)[0].time == 13
    assert repair_local_joint_conflicts(
        scenario,
        scenario.robots,
        assignments,
        paths,
        ["R1 存在不可达任务"],
    ) is None


@pytest.mark.parametrize(
    ("case_id", "oracle_makespan"),
    [
        ("generated-s20260904-i0009", 3),
        ("generated-s20260904-i0020", 7),
        ("generated-s20260904-i0034", 4),
    ],
)
def test_remaining_seeded_misses_are_repaired_without_execution_conflicts(
    case_id: str,
    oracle_makespan: int,
) -> None:
    case = next(
        case
        for case in generate_solvability_cases(seed=20260904, sample_count=34)
        if case.case_id == case_id
    )

    run = execute_solvability_case(
        case,
        run_index=1,
        max_expanded_states=100_000,
    )

    assert run.oracle_outcome == "solved"
    assert run.oracle_makespan == oracle_makespan
    assert run.planner_outcome == "solved"
    assert run.planner_failure_count == 0
    assert run.planner_conflict_count == 0
    assert run.comparison_class == "agreementSolved"


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


def test_solvability_cli_has_exact_defaults() -> None:
    args = parse_args([])

    assert args.sample_count == 64
    assert args.seed == 20260904
    assert args.repetitions == 1
    assert args.max_expanded_states == 100_000
    assert args.timeout_seconds == 5
    assert args.output_dir == "output/solvability-differential"


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--sample-count", "-1"),
        ("--sample-count", "513"),
        ("--repetitions", "0"),
        ("--max-expanded-states", "0"),
        ("--timeout-seconds", "0"),
        ("--timeout-seconds", "nan"),
        ("--timeout-seconds", "inf"),
    ],
)
def test_solvability_cli_rejects_invalid_parameters_before_creating_output(
    tmp_path,
    option,
    value,
) -> None:
    assert main([option, value, "--output-dir", str(tmp_path)]) == 1
    assert list(tmp_path.iterdir()) == []


def test_solvability_cli_uses_unique_timestamp_directory(monkeypatch, tmp_path) -> None:
    class FixedDatetime:
        @classmethod
        def now(cls, timezone_value):
            assert timezone_value is cli_module.timezone.utc
            return datetime_module.datetime(2026, 9, 4, 1, 2, 3, tzinfo=timezone_value)

    existing_path = tmp_path / "20260904T010203Z"
    existing_path.mkdir()
    monkeypatch.setattr(cli_module, "datetime", FixedDatetime)
    monkeypatch.setattr(
        cli_module,
        "run_solvability_cases",
        lambda cases, repetitions, max_expanded_states, timeout_seconds, on_result: [],
    )

    assert main(["--sample-count", "0", "--output-dir", str(tmp_path)]) == 0
    result_path = tmp_path / "20260904T010203Z-2"
    assert result_path.exists()
    assert not (result_path / "results.partial.json").exists()


def test_solvability_cli_writes_exact_config_and_keeps_planner_miss_successful(
    tmp_path,
    monkeypatch,
) -> None:
    case = _case("catalog-solo-straight")
    run = replace(
        execute_solvability_case(case, 1, 100_000),
        comparison_class="oracleSolvedPlannerMiss",
    )
    observed = {}

    def fake_run_solvability_cases(
        cases,
        repetitions,
        max_expanded_states,
        timeout_seconds,
        on_result,
    ):
        observed["case_count"] = len(cases)
        observed["repetitions"] = repetitions
        observed["max_expanded_states"] = max_expanded_states
        observed["timeout_seconds"] = timeout_seconds
        on_result([run])
        return [run]

    monkeypatch.setattr(cli_module, "run_solvability_cases", fake_run_solvability_cases)
    output_base = tmp_path / "results"

    assert (
        main(
            [
                "--sample-count",
                "2",
                "--seed",
                "11",
                "--repetitions",
                "1",
                "--max-expanded-states",
                "500",
                "--timeout-seconds",
                "3",
                "--output-dir",
                str(output_base),
            ]
        )
        == 0
    )

    result_path = next(output_base.iterdir())
    payload = json.loads((result_path / "results.json").read_text(encoding="utf-8"))
    assert observed == {
        "case_count": 6,
        "repetitions": 1,
        "max_expanded_states": 500,
        "timeout_seconds": 3.0,
    }
    assert payload["config"] == {
        "seed": 11,
        "sampleCount": 2,
        "catalogCaseCount": 4,
        "caseCount": 6,
        "repetitions": 1,
        "maxExpandedStates": 500,
        "timeoutSeconds": 3.0,
        "outputDir": str(result_path.resolve()),
        "oracle": {
            "objective": "minimumMakespan",
            "moves": ["wait", "up", "right", "down", "left"],
            "forbidVertexConflicts": True,
            "forbidReverseEdgeConflicts": True,
            "goalSemantics": "visitOnceThenMayReposition",
            "terminalOccupancy": "persistentAtFinalPositions",
        },
        "planner": {
            "entrypoint": "backend.app.dispatch.build_paths",
            "fixedAssignments": True,
            "avoidConflicts": True,
        },
    }
    assert payload["candidateCounterexampleCaseIds"] == [case.case_id]
    assert not (result_path / "results.partial.json").exists()
