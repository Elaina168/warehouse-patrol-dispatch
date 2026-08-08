import csv
import json
from collections.abc import Callable
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
    "planningDiagnosticsEvaluated",
    "pathCandidateCount",
    "selectedPathCandidateIndex",
    "failedPathCandidateCount",
    "timedAStarCallCount",
    "timedAStarExpandedStateCount",
    "maxTimedAStarExpandedStateCount",
    "timedAStarExhaustedSearchCount",
    "timedAStarGoalFullyReservedRejectCount",
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
    "medianTimedAStarExpandedStateCount",
    "p95TimedAStarExpandedStateCount",
    "maxTimedAStarGoalFullyReservedRejectCount",
)


def _raise_write_error(
    target_path: Path,
    exc: Exception,
    *,
    rollback_error: Exception | None = None,
) -> None:
    message = f"写入基准报告失败: {target_path.resolve()}: {exc}"
    if rollback_error is not None:
        message = f"{message}；回滚失败: {rollback_error}"
    raise OSError(message) from exc


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
    target_path: Path | None = None,
) -> None:
    displayed_path = target_path or path
    try:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=field_names, extrasaction="raise")
            writer.writeheader()
            writer.writerows(records)
    except Exception as exc:
        _raise_write_error(displayed_path, exc)


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
    backed_up_targets: set[Path],
    published_targets: set[Path],
    preserved_backup_paths: set[Path],
) -> None:
    errors: list[str] = []
    for target_path in target_paths:
        if target_path not in published_targets:
            continue
        try:
            target_path.unlink(missing_ok=True)
        except Exception as exc:
            errors.append(f"{target_path.resolve()}: {exc}")
    for target_path in target_paths:
        if target_path not in backed_up_targets:
            continue
        backup_path = backup_paths[target_path]
        preserved_backup_paths.add(backup_path)
        try:
            backup_path.replace(target_path)
        except Exception as exc:
            errors.append(
                f"{backup_path.resolve()} -> {target_path.resolve()}: {exc}"
            )
        else:
            preserved_backup_paths.discard(backup_path)
    if errors:
        raise OSError("；".join(errors))


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
    target_paths = (
        output_path / "results.json",
        output_path / "runs.csv",
        output_path / "case-summaries.csv",
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
    preserved_backup_paths: set[Path] = set()

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
            lambda: [run.to_record() for run in report.runs],
        )
        _stage_csv(
            temporary_paths[output_path / "case-summaries.csv"],
            output_path / "case-summaries.csv",
            CASE_SUMMARY_FIELD_NAMES,
            lambda: [summary.to_record() for summary in report.case_summaries],
        )

        backed_up_targets: set[Path] = set()
        published_targets: set[Path] = set()
        for target_path in target_paths:
            if not target_path.exists():
                continue
            try:
                target_path.replace(backup_paths[target_path])
                backed_up_targets.add(target_path)
            except Exception as exc:
                rollback_error: Exception | None = None
                try:
                    _rollback_final_bundle(
                        target_paths,
                        backup_paths,
                        backed_up_targets,
                        published_targets,
                        preserved_backup_paths,
                    )
                except Exception as rollback_exc:
                    rollback_error = rollback_exc
                _raise_write_error(
                    target_path,
                    exc,
                    rollback_error=rollback_error,
                )

        for target_path in target_paths:
            try:
                temporary_paths[target_path].replace(target_path)
                published_targets.add(target_path)
            except Exception as exc:
                rollback_error = None
                try:
                    _rollback_final_bundle(
                        target_paths,
                        backup_paths,
                        backed_up_targets,
                        published_targets,
                        preserved_backup_paths,
                    )
                except Exception as rollback_exc:
                    rollback_error = rollback_exc
                _raise_write_error(
                    target_path,
                    exc,
                    rollback_error=rollback_error,
                )

        try:
            partial_path.unlink(missing_ok=True)
        except Exception as exc:
            rollback_error: Exception | None = None
            try:
                _rollback_final_bundle(
                    target_paths,
                    backup_paths,
                    backed_up_targets,
                    published_targets,
                    preserved_backup_paths,
                )
            except Exception as rollback_exc:
                rollback_error = rollback_exc
            _raise_write_error(
                partial_path,
                exc,
                rollback_error=rollback_error,
            )
        _cleanup_transaction_files(transaction_paths)
    finally:
        _cleanup_transaction_files(
            [
                path
                for path in transaction_paths
                if path not in preserved_backup_paths
            ]
        )
