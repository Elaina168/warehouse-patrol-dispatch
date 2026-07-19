import csv
import json
from pathlib import Path
from uuid import uuid4

from backend.benchmarks.results import BenchmarkReport


RUN_FIELD_NAMES = (
    "caseId",
    "family",
    "mode",
    "seed",
    "runIndex",
    "robotCount",
    "taskCount",
    "dynamicTaskCount",
    "obstacleCount",
    "tickTarget",
    "outcome",
    "errorType",
    "errorMessage",
    "correctnessStable",
    "releasedTaskCount",
    "coveredTaskCount",
    "assignedTaskCount",
    "completedTaskCount",
    "assignmentRatePercent",
    "coverageRatePercent",
    "actualCompletionRatePercent",
    "predictedConflictCount",
    "activeConflictCount",
    "executionSafetyEvaluated",
    "safetyInterventionCount",
    "deadlineMissCount",
    "failureCount",
    "totalDistance",
    "makespan",
    "replanTimeMs",
    "maxSnapshotReplanTimeMs",
    "wallClockMs",
)

CASE_SUMMARY_FIELD_NAMES = (
    "caseId",
    "runCount",
    "completedRunCount",
    "timeoutCount",
    "errorCount",
    "stableRunCount",
    "stableRunRatePercent",
    "medianWallClockMs",
    "p95WallClockMs",
    "medianReplanTimeMs",
    "p95ReplanTimeMs",
    "maxSafetyInterventionCount",
)


def _raise_write_error(target_path: Path, exc: Exception) -> None:
    raise OSError(f"写入基准报告失败: {target_path.resolve()}: {exc}") from exc


def _write_json(path: Path, report: BenchmarkReport, target_path: Path | None = None) -> None:
    displayed_path = target_path or path
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(report.to_record(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except Exception as exc:
        _raise_write_error(displayed_path, exc)


def _write_csv(
    path: Path,
    field_names: tuple[str, ...],
    records: list[dict[str, object]],
) -> None:
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=field_names, extrasaction="raise")
            writer.writeheader()
            writer.writerows(records)
    except Exception as exc:
        _raise_write_error(path, exc)


def write_partial_report(output_dir: Path, report: BenchmarkReport) -> None:
    partial_path = Path(output_dir) / "results.partial.json"
    temporary_path = partial_path.with_name(f".{partial_path.name}.{uuid4().hex}.tmp")
    try:
        _write_json(temporary_path, report, target_path=partial_path)
        try:
            temporary_path.replace(partial_path)
        except Exception as exc:
            _raise_write_error(partial_path, exc)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def write_final_report(output_dir: Path, report: BenchmarkReport) -> None:
    output_path = Path(output_dir)
    _write_json(output_path / "results.json", report)
    _write_csv(
        output_path / "runs.csv",
        RUN_FIELD_NAMES,
        [run.to_record() for run in report.runs],
    )
    _write_csv(
        output_path / "case-summaries.csv",
        CASE_SUMMARY_FIELD_NAMES,
        [summary.to_record() for summary in report.case_summaries],
    )
    partial_path = output_path / "results.partial.json"
    try:
        partial_path.unlink(missing_ok=True)
    except Exception as exc:
        _raise_write_error(partial_path, exc)
