import csv
import json
from collections.abc import Callable
from pathlib import Path
from shutil import copyfile
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
    target_path: Path | None = None,
) -> None:
    displayed_path = target_path or path
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
        _raise_write_error(displayed_path, exc)


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


def _stage_csv(
    temporary_path: Path,
    target_path: Path,
    field_names: tuple[str, ...],
    records_factory: Callable[[], list[dict[str, object]]],
) -> None:
    try:
        records = records_factory()
    except Exception as exc:
        _raise_write_error(target_path, exc)
    _write_csv(
        temporary_path,
        field_names,
        records,
        target_path=target_path,
    )


def _cleanup_transaction_files(
    paths: list[Path],
    *,
    raise_errors: bool = False,
) -> None:
    errors: list[str] = []
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{path.resolve()}: {exc}")
    if raise_errors and errors:
        raise OSError("；".join(errors))


def _rollback_final_bundle(
    target_paths: tuple[Path, ...],
    backup_paths: dict[Path, Path],
    existing_targets: set[Path],
) -> None:
    errors: list[str] = []
    for target_path in target_paths:
        try:
            if target_path in existing_targets:
                backup_path = backup_paths[target_path]
                if not backup_path.exists():
                    raise OSError(f"缺少回滚备份: {backup_path.resolve()}")
                backup_path.replace(target_path)
            else:
                target_path.unlink(missing_ok=True)
        except Exception as exc:
            errors.append(f"{target_path.resolve()}: {exc}")
    if errors:
        raise OSError("；".join(errors))


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
    target_paths = (
        output_path / "runs.csv",
        output_path / "replan-observations.csv",
        output_path / "variant-summaries.csv",
        output_path / "results.json",
    )
    transaction_id = uuid4().hex
    temporary_paths = {
        target_path: target_path.with_name(
            f".{target_path.name}.{transaction_id}.tmp"
        )
        for target_path in target_paths
    }
    backup_paths = {
        target_path: target_path.with_name(
            f".{target_path.name}.{transaction_id}.backup"
        )
        for target_path in target_paths
    }
    transaction_paths = [
        *temporary_paths.values(),
        *backup_paths.values(),
    ]
    partial_path = output_path / "results.partial.json"

    try:
        _write_json(
            temporary_paths[output_path / "results.json"],
            report,
            target_path=output_path / "results.json",
        )
        _stage_csv(
            temporary_paths[output_path / "runs.csv"],
            output_path / "runs.csv",
            RUN_FIELD_NAMES,
            lambda: _run_records(report),
        )
        _stage_csv(
            temporary_paths[output_path / "replan-observations.csv"],
            output_path / "replan-observations.csv",
            REPLAN_OBSERVATION_FIELD_NAMES,
            lambda: _observation_records(report),
        )
        _stage_csv(
            temporary_paths[output_path / "variant-summaries.csv"],
            output_path / "variant-summaries.csv",
            VARIANT_SUMMARY_FIELD_NAMES,
            lambda: _variant_summary_records(report),
        )

        existing_targets: set[Path] = set()
        for target_path in target_paths:
            if not target_path.exists():
                continue
            existing_targets.add(target_path)
            try:
                copyfile(target_path, backup_paths[target_path])
            except Exception as exc:
                _raise_write_error(target_path, exc)

        for target_path in target_paths:
            try:
                temporary_paths[target_path].replace(target_path)
            except Exception as exc:
                try:
                    _rollback_final_bundle(
                        target_paths,
                        backup_paths,
                        existing_targets,
                    )
                except Exception as rollback_exc:
                    exc = OSError(f"{exc}；回滚失败: {rollback_exc}")
                _raise_write_error(target_path, exc)

        try:
            partial_path.unlink(missing_ok=True)
        except Exception as exc:
            _raise_write_error(partial_path, exc)
        try:
            _cleanup_transaction_files(
                transaction_paths,
                raise_errors=True,
            )
        except Exception as exc:
            _raise_write_error(output_path / "results.json", exc)
    finally:
        _cleanup_transaction_files(transaction_paths)
