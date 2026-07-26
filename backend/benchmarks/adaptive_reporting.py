import csv
import json
from pathlib import Path
from uuid import uuid4

from backend.benchmarks.adaptive_results import AdaptiveCalibrationReport


RUN_FIELD_NAMES = (
    "caseId",
    "variantId",
    "runIndex",
    "robotCount",
    "taskCount",
    "tickTarget",
    "outcome",
    "errorType",
    "errorMessage",
    "correctnessStable",
    "releasedTaskCount",
    "coveredTaskCount",
    "completedTaskCount",
    "coverageRatePercent",
    "actualCompletionRatePercent",
    "predictedConflictCount",
    "activeConflictCount",
    "safetyInterventionCount",
    "safetyStallReached",
    "maxConsecutiveSafetyInterventionCount",
    "deadlineMissCount",
    "failureCount",
    "totalDistance",
    "makespan",
    "wallClockMs",
    "replanCount",
    "windowChangeCount",
)

REPLAN_OBSERVATION_FIELD_NAMES = (
    "caseId",
    "variantId",
    "runIndex",
    "observationIndex",
    "time",
    "configuredWindow",
    "effectiveWindow",
    "reason",
    "releasedTaskCount",
    "futureTaskCount",
    "activeRobotCount",
    "taskPressureRatio",
    "latencySamplesBeforeMs",
    "latencyMedianBeforeMs",
    "latencySlowBefore",
    "replanTimeMs",
    "latencySlowAfter",
    "pathCandidateCount",
    "selectedPathCandidateIndex",
    "failedPathCandidateCount",
    "timedAStarCallCount",
    "timedAStarExpandedStateCount",
    "maxTimedAStarExpandedStateCount",
    "timedAStarExhaustedSearchCount",
    "timedAStarGoalFullyReservedRejectCount",
)

VARIANT_SUMMARY_FIELD_NAMES = (
    "caseId",
    "variantId",
    "runCount",
    "completedRunCount",
    "timeoutCount",
    "errorCount",
    "stableRunCount",
    "stableRunRatePercent",
    "medianWallClockMs",
    "p95WallClockMs",
    "medianRunReplanTimeMs",
    "p95RunReplanTimeMs",
    "medianReplanCount",
    "medianWindowChangeCount",
    "medianCoverageRatePercent",
    "medianActualCompletionRatePercent",
    "maxSafetyInterventionCount",
    "maxConsecutiveSafetyInterventionCount",
    "windowReasonCounts",
)


def _raise_write_error(target_path: Path, exc: Exception) -> None:
    raise OSError(
        "写入自适应窗口校准报告失败: "
        f"{target_path.resolve()}: {exc}"
    ) from exc


def _write_json(
    path: Path,
    report: AdaptiveCalibrationReport,
    target_path: Path | None = None,
) -> None:
    displayed_path = target_path or path
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(
                report.to_record(),
                handle,
                ensure_ascii=False,
                indent=2,
            )
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
            writer = csv.DictWriter(
                handle,
                fieldnames=field_names,
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(records)
    except Exception as exc:
        _raise_write_error(path, exc)


def _run_records(report: AdaptiveCalibrationReport) -> list[dict[str, object]]:
    records = []
    for run in report.runs:
        record = run.to_record()
        record.pop("replanObservations")
        records.append(record)
    return records


def _observation_records(
    report: AdaptiveCalibrationReport,
) -> list[dict[str, object]]:
    records = []
    for run in report.runs:
        for observation in run.replan_observations:
            record = observation.to_record()
            record["latencySamplesBeforeMs"] = json.dumps(
                record["latencySamplesBeforeMs"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            records.append(record)
    return records


def _variant_summary_records(
    report: AdaptiveCalibrationReport,
) -> list[dict[str, object]]:
    records = []
    for summary in report.variant_summaries:
        record = summary.to_record()
        record["windowReasonCounts"] = json.dumps(
            record["windowReasonCounts"],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        records.append(record)
    return records


def write_partial_report(
    output_dir: Path,
    report: AdaptiveCalibrationReport,
) -> None:
    partial_path = Path(output_dir) / "results.partial.json"
    temporary_path = partial_path.with_name(
        f".{partial_path.name}.{uuid4().hex}.tmp"
    )
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


def write_final_report(
    output_dir: Path,
    report: AdaptiveCalibrationReport,
) -> None:
    output_path = Path(output_dir)
    _write_json(output_path / "results.json", report)
    _write_csv(
        output_path / "runs.csv",
        RUN_FIELD_NAMES,
        _run_records(report),
    )
    _write_csv(
        output_path / "replan-observations.csv",
        REPLAN_OBSERVATION_FIELD_NAMES,
        _observation_records(report),
    )
    _write_csv(
        output_path / "variant-summaries.csv",
        VARIANT_SUMMARY_FIELD_NAMES,
        _variant_summary_records(report),
    )
    partial_path = output_path / "results.partial.json"
    try:
        partial_path.unlink(missing_ok=True)
    except Exception as exc:
        _raise_write_error(partial_path, exc)
