from backend.app.experiments import SEEDED_PRESSURE_CASES
from dataclasses import replace
import csv
import hashlib
import json
import multiprocessing
from pathlib import Path
from statistics import median
import time

import pytest

import backend.competition.evidence as evidence_module
from backend.app.experiments import SEEDED_PRESSURE_PLANNING_TIME_BUDGET_MS
from backend.benchmarks.process_isolation import IsolatedExecution
from backend.competition.evidence import (
    EVIDENCE_CASES,
    EvidenceRun,
    evidence_is_accepted,
    execute_evidence_case,
    EvidenceReport,
    CHART_SPECS,
    chart_data,
    main,
    planned_runs,
    run_evidence_cases,
    write_evidence_bundle,
)


def test_evidence_catalog_plans_five_repetitions_for_the_fixed_seven_cases() -> None:
    assert [case.case_id for case in EVIDENCE_CASES] == [
        "integrated-demo-without-conflict-avoidance",
        "integrated-demo-with-conflict-avoidance",
        "main-demo-online",
        "seed-17",
        "seed-29",
        "seed-31",
        "safety-gate-boundary",
    ]
    assert [case.seeded_case for case in EVIDENCE_CASES[3:6]] == list(
        SEEDED_PRESSURE_CASES
    )

    planned = planned_runs()

    assert len(planned) == 35
    assert [(case.case_id, run_index) for case, run_index in planned[:6]] == [
        ("integrated-demo-without-conflict-avoidance", 1),
        ("integrated-demo-without-conflict-avoidance", 2),
        ("integrated-demo-without-conflict-avoidance", 3),
        ("integrated-demo-without-conflict-avoidance", 4),
        ("integrated-demo-without-conflict-avoidance", 5),
        ("integrated-demo-with-conflict-avoidance", 1),
    ]


def _completed_run(case_id: str, run_index: int) -> EvidenceRun:
    return EvidenceRun.completed(
        case_id=case_id,
        category="controlled",
        run_index=run_index,
        accepted=True,
        wall_clock_ms=1.0,
    )


def _slow_evidence_worker(case_id: str, run_index: int) -> EvidenceRun:
    time.sleep(5)
    return _completed_run(case_id, run_index)


def test_evidence_batch_preserves_timeout_error_and_rejected_completed_run(
    monkeypatch,
) -> None:
    outcomes = iter(
        [
            IsolatedExecution("timeout", None, "TimeoutError", None, 30_000.0),
            IsolatedExecution("error", None, "ValueError", "boom\nnext", 2.0),
            IsolatedExecution(
                "completed",
                replace(
                    _completed_run(EVIDENCE_CASES[0].case_id, 3),
                    accepted=False,
                    acceptance_error="conflict count mismatch",
                ),
                None,
                None,
                3.0,
            ),
        ]
    )

    def fake_isolated(worker, worker_args, timeout_seconds):
        assert worker is evidence_module.execute_evidence_case
        assert timeout_seconds == 30
        return next(outcomes)

    monkeypatch.setattr(evidence_module, "run_isolated_process", fake_isolated)

    runs = run_evidence_cases(EVIDENCE_CASES[:1], repetitions=3, timeout_seconds=30)

    assert [run.outcome for run in runs] == ["timeout", "error", "completed"]
    assert runs[0].wall_clock_ms == 30_000.0
    assert runs[1].error_type == "ValueError"
    assert runs[1].error_message == "boomnext"
    assert runs[2].accepted is False
    assert evidence_is_accepted(runs) is False


def test_all_seven_evidence_cases_execute_real_acceptance_contracts() -> None:
    runs = [execute_evidence_case(case.case_id, 1) for case in EVIDENCE_CASES]

    assert len(runs) == 7
    assert all(run.outcome == "completed" and run.accepted for run in runs)

    without, with_avoidance, main, seed_17, seed_29, seed_31, safety = runs
    assert without.metrics["conflictCount"] > 0
    assert with_avoidance.metrics["conflictCount"] == 0
    assert without.metrics["assignedTaskCount"] == with_avoidance.metrics["assignedTaskCount"] == 6

    assert main.metrics["completedTaskCount"] == 7
    assert [item["time"] for item in main.timeline] == [12, 20, 28, 36, 36, 700]
    assert [item["action"] for item in main.timeline[3:5]] == [
        "removeBlockedCell",
        "restoreRobot",
    ]

    for run, seeded_case in zip((seed_17, seed_29, seed_31), SEEDED_PRESSURE_CASES):
        label, seed, robot_count, base_task_count = seeded_case
        assert run.case_id == label
        assert run.seed == seed
        assert run.robot_count == robot_count
        assert run.task_count == base_task_count + run.dynamic_task_count
        assert run.metrics["assignedTaskCount"] == run.task_count
        assert run.metrics["replanTimeMs"] < SEEDED_PRESSURE_PLANNING_TIME_BUDGET_MS

    assert safety.metrics["collisionFree"] is True
    assert safety.metrics["safetyInterventionCount"] >= 3
    assert safety.metrics["safetyStallCount"] == 3
    assert [item["time"] for item in safety.trajectory] == [0, 1, 2, 3, 4]


def _representative_runs() -> list[EvidenceRun]:
    return [execute_evidence_case(case.case_id, 1) for case in EVIDENCE_CASES]


def test_evidence_bundle_csv_recomputes_json_summaries_and_manifest_hashes(
    tmp_path: Path,
) -> None:
    runs = _representative_runs()
    report = EvidenceReport.create(
        config={"repetitions": 1, "timeoutSeconds": 30.0},
        runs=runs,
    )

    write_evidence_bundle(tmp_path, report)

    result = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    with (tmp_path / "runs.csv").open(encoding="utf-8-sig", newline="") as handle:
        csv_runs = list(csv.DictReader(handle))
    with (tmp_path / "case-summaries.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        csv_summaries = list(csv.DictReader(handle))
    recomputed = {
        case_id: {
            "runCount": sum(row["caseId"] == case_id for row in csv_runs),
            "completedRunCount": sum(
                row["caseId"] == case_id and row["outcome"] == "completed"
                for row in csv_runs
            ),
            "acceptedRunCount": sum(
                row["caseId"] == case_id and row["accepted"] == "True"
                for row in csv_runs
            ),
            "medianWallClockMs": median(
                float(row["wallClockMs"])
                for row in csv_runs
                if row["caseId"] == case_id and row["outcome"] == "completed"
            ),
        }
        for case_id in {row["caseId"] for row in csv_runs}
    }
    assert recomputed == {
        row["caseId"]: {
            "runCount": int(row["runCount"]),
            "completedRunCount": int(row["completedRunCount"]),
            "acceptedRunCount": int(row["acceptedRunCount"]),
            "medianWallClockMs": float(row["medianWallClockMs"]),
        }
        for row in csv_summaries
    }
    assert result["caseSummaries"] == [summary.to_record() for summary in report.case_summaries]

    manifest = json.loads(
        (tmp_path / "evidence-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["parameters"] == {
        "repetitions": 1,
        "timeoutSeconds": 30.0,
    }
    assert manifest["git"]["commit"]
    assert isinstance(manifest["git"]["dirty"], bool)
    assert manifest["environment"]["operatingSystem"]
    assert manifest["environment"]["cpu"]
    assert manifest["environment"]["pythonVersion"]
    for relative_path, expected_hash in manifest["files"].items():
        assert hashlib.sha256((tmp_path / relative_path).read_bytes()).hexdigest() == expected_hash


def test_evidence_charts_use_only_values_in_raw_run_records(tmp_path: Path) -> None:
    runs = _representative_runs()
    source = chart_data(runs)

    assert source["conflict-avoidance"] == [
        {
            "label": "without",
            "values": [runs[0].metrics["conflictCount"]],
        },
        {"label": "with", "values": [runs[1].metrics["conflictCount"]]},
    ]
    assert source["dynamic-event-timeline"] == list(runs[2].timeline)
    assert source["scale-performance"] == [
        {
            "robotCount": run.robot_count,
            "replanTimeMs": run.metrics["replanTimeMs"],
        }
        for run in runs[3:6]
    ]
    assert source["safety-gate-trajectory"] == list(runs[6].trajectory)

    write_evidence_bundle(tmp_path, EvidenceReport.create({}, runs))
    for spec in CHART_SPECS:
        svg = tmp_path / f"{spec.file_stem}.svg"
        png = tmp_path / f"{spec.file_stem}.png"
        assert svg.read_text(encoding="utf-8").startswith("<svg")
        assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert len(png.read_bytes()) > 100


def test_evidence_bundle_publish_failure_restores_existing_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runs = _representative_runs()
    report = EvidenceReport.create({}, runs)
    write_evidence_bundle(tmp_path, report)
    old_files = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    original_replace = Path.replace
    failed = False

    def fail_mid_publish(path: Path, target: Path):
        nonlocal failed
        if not failed and path.name.startswith(".dynamic-event-timeline.svg"):
            failed = True
            raise OSError("controlled publish failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_mid_publish)

    with pytest.raises(OSError, match="controlled publish failure"):
        write_evidence_bundle(tmp_path, report)

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == old_files


def test_evidence_bundle_backup_failure_restores_existing_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    report = EvidenceReport.create({}, _representative_runs())
    write_evidence_bundle(tmp_path, report)
    old_files = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    original_replace = Path.replace
    backup_count = 0

    def fail_second_backup(path: Path, target: Path):
        nonlocal backup_count
        if target.name.endswith(".backup"):
            backup_count += 1
            if backup_count == 2:
                raise OSError("controlled backup failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_second_backup)

    with pytest.raises(OSError, match="controlled backup failure"):
        write_evidence_bundle(tmp_path, report)

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == old_files


def test_evidence_isolation_timeout_leaves_no_child_process_residue(
    monkeypatch,
) -> None:
    before = {process.pid for process in multiprocessing.active_children()}
    monkeypatch.setattr(evidence_module, "execute_evidence_case", _slow_evidence_worker)

    runs = run_evidence_cases(EVIDENCE_CASES[:1], repetitions=1, timeout_seconds=0.05)

    assert runs[0].outcome == "timeout"
    assert {process.pid for process in multiprocessing.active_children()} == before


def test_evidence_cli_defaults_to_35_runs_and_publishes_diagnostics_on_failure(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = []

    def fake_run(cases, repetitions, timeout_seconds, on_result=None):
        calls.append((cases, repetitions, timeout_seconds, on_result))
        runs = [
            _completed_run(case.case_id, run_index)
            for case, run_index in planned_runs(repetitions)
        ]
        runs[-1] = EvidenceRun.failed(
            EVIDENCE_CASES[-1],
            5,
            "timeout",
            "TimeoutError",
            None,
            30_000.0,
        )
        return runs

    monkeypatch.setattr(evidence_module, "run_evidence_cases", fake_run)
    monkeypatch.setattr(evidence_module, "write_evidence_bundle", lambda path, report: (path / "published").write_text(json.dumps(report.to_record()), encoding="utf-8"))

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 1
    assert len(calls) == 1
    assert calls[0][0] == EVIDENCE_CASES
    assert calls[0][1:3] == (5, 30.0)
    result_path = Path(capsys.readouterr().out.strip())
    assert result_path.parent == tmp_path.resolve()
    payload = json.loads((result_path / "published").read_text(encoding="utf-8"))
    assert len(payload["runs"]) == 35
    assert payload["runs"][-1]["outcome"] == "timeout"
    assert payload["config"] == {
        "caseIds": [case.case_id for case in EVIDENCE_CASES],
        "repetitions": 5,
        "timeoutSeconds": 30.0,
        "outputDir": str(result_path),
        "seededPressurePlanningTimeBudgetMs": SEEDED_PRESSURE_PLANNING_TIME_BUDGET_MS,
    }


def test_evidence_cli_rejects_invalid_config_before_creating_output(
    tmp_path: Path,
) -> None:
    assert main(["--repetitions", "0", "--output-dir", str(tmp_path)]) == 1
    assert list(tmp_path.iterdir()) == []
