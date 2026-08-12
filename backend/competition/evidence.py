"""生成 3S 提交所需的可重算正式证据包。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
import traceback
import zlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Literal
from uuid import uuid4

from backend.app.dispatch import run_dispatch
from backend.app.experiments import (
    SEEDED_PRESSURE_CASES,
    SEEDED_PRESSURE_PLANNING_TIME_BUDGET_MS,
)
from backend.app.schemas import DispatchOptions, Scenario
from backend.app.seeded_scenarios import seeded_pressure_scenario
from backend.benchmarks.process_isolation import (
    BenchmarkInfrastructureError,
    run_isolated_process,
    sanitize_error_text,
)
from backend.competition.manifests import PROJECT_ROOT
from backend.competition.runners import run_main_demo, run_safety_demo


EvidenceKind = Literal[
    "integrated-direct",
    "main-demo-online",
    "seeded-pressure",
    "safety-gate",
]


@dataclass(frozen=True, slots=True)
class EvidenceCase:
    case_id: str
    kind: EvidenceKind
    avoid_conflicts: bool | None = None
    seeded_case: tuple[str, int, int, int] | None = None


EvidenceOutcome = Literal["completed", "timeout", "error"]


@dataclass(frozen=True, slots=True)
class EvidenceRun:
    case_id: str
    category: str
    run_index: int
    outcome: EvidenceOutcome
    accepted: bool
    acceptance_error: str | None
    error_type: str | None
    error_message: str | None
    wall_clock_ms: float
    seed: int | None = None
    robot_count: int | None = None
    task_count: int | None = None
    dynamic_task_count: int | None = None
    metrics: dict[str, int | float | bool | None] | None = None
    timeline: tuple[dict[str, object], ...] = ()
    trajectory: tuple[dict[str, object], ...] = ()

    def with_data(self, **changes: object) -> "EvidenceRun":
        return replace(self, **changes)

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "category": self.category,
            "runIndex": self.run_index,
            "outcome": self.outcome,
            "accepted": self.accepted,
            "acceptanceError": self.acceptance_error,
            "errorType": self.error_type,
            "errorMessage": self.error_message,
            "wallClockMs": self.wall_clock_ms,
            "seed": self.seed,
            "robotCount": self.robot_count,
            "taskCount": self.task_count,
            "dynamicTaskCount": self.dynamic_task_count,
            "metrics": self.metrics,
            "timeline": list(self.timeline),
            "trajectory": list(self.trajectory),
        }

    @classmethod
    def completed(
        cls,
        *,
        case_id: str,
        category: str,
        run_index: int,
        accepted: bool,
        wall_clock_ms: float,
        acceptance_error: str | None = None,
    ) -> "EvidenceRun":
        return cls(
            case_id=case_id,
            category=category,
            run_index=run_index,
            outcome="completed",
            accepted=accepted,
            acceptance_error=acceptance_error,
            error_type=None,
            error_message=None,
            wall_clock_ms=wall_clock_ms,
        )

    @classmethod
    def failed(
        cls,
        case: EvidenceCase,
        run_index: int,
        outcome: Literal["timeout", "error"],
        error_type: str,
        error_message: str | None,
        wall_clock_ms: float,
    ) -> "EvidenceRun":
        return cls(
            case_id=case.case_id,
            category=case.kind,
            run_index=run_index,
            outcome=outcome,
            accepted=False,
            acceptance_error=None,
            error_type=error_type,
            error_message=error_message,
            wall_clock_ms=wall_clock_ms,
        )


@dataclass(frozen=True, slots=True)
class EvidenceCaseSummary:
    case_id: str
    run_count: int
    completed_run_count: int
    timeout_count: int
    error_count: int
    accepted_run_count: int
    accepted_run_rate_percent: float
    median_wall_clock_ms: float | None
    p95_wall_clock_ms: float | None
    median_replan_time_ms: float | None
    p95_replan_time_ms: float | None

    def to_record(self) -> dict[str, object]:
        return {
            "caseId": self.case_id,
            "runCount": self.run_count,
            "completedRunCount": self.completed_run_count,
            "timeoutCount": self.timeout_count,
            "errorCount": self.error_count,
            "acceptedRunCount": self.accepted_run_count,
            "acceptedRunRatePercent": self.accepted_run_rate_percent,
            "medianWallClockMs": self.median_wall_clock_ms,
            "p95WallClockMs": self.p95_wall_clock_ms,
            "medianReplanTimeMs": self.median_replan_time_ms,
            "p95ReplanTimeMs": self.p95_replan_time_ms,
        }


@dataclass(frozen=True, slots=True)
class EvidenceReport:
    schema_version: int
    generated_at: str
    config: dict[str, object]
    runs: list[EvidenceRun]
    case_summaries: list[EvidenceCaseSummary]

    @classmethod
    def create(
        cls,
        config: dict[str, object],
        runs: list[EvidenceRun],
    ) -> "EvidenceReport":
        return cls(
            schema_version=1,
            generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            config=dict(config),
            runs=list(runs),
            case_summaries=summarize_evidence_runs(runs),
        )

    def to_record(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "generatedAt": self.generated_at,
            "config": self.config,
            "runs": [run.to_record() for run in self.runs],
            "caseSummaries": [summary.to_record() for summary in self.case_summaries],
        }


@dataclass(frozen=True, slots=True)
class ChartSpec:
    key: str
    file_stem: str
    title: str


CHART_SPECS = (
    ChartSpec(
        "conflict-avoidance",
        "conflict-avoidance",
        "Conflict avoidance comparison",
    ),
    ChartSpec(
        "dynamic-event-timeline",
        "dynamic-event-timeline",
        "Dynamic event timeline",
    ),
    ChartSpec(
        "scale-performance",
        "scale-performance",
        "4/6/8 robot performance",
    ),
    ChartSpec(
        "safety-gate-trajectory",
        "safety-gate-trajectory",
        "Safety gate execution trajectory",
    ),
)


EVIDENCE_CASES = (
    EvidenceCase(
        "integrated-demo-without-conflict-avoidance",
        "integrated-direct",
        avoid_conflicts=False,
    ),
    EvidenceCase(
        "integrated-demo-with-conflict-avoidance",
        "integrated-direct",
        avoid_conflicts=True,
    ),
    EvidenceCase("main-demo-online", "main-demo-online"),
    *(
        EvidenceCase(label, "seeded-pressure", seeded_case=seeded_case)
        for seeded_case in SEEDED_PRESSURE_CASES
        for label in (seeded_case[0],)
    ),
    EvidenceCase("safety-gate-boundary", "safety-gate"),
)


def planned_runs(repetitions: int = 5) -> list[tuple[EvidenceCase, int]]:
    """按稳定案例顺序列出待执行记录。"""
    if repetitions < 1:
        raise ValueError("repetitions 必须大于等于 1")
    return [
        (case, run_index)
        for case in EVIDENCE_CASES
        for run_index in range(1, repetitions + 1)
    ]


def execute_evidence_case(case_id: str, run_index: int) -> EvidenceRun:
    """在子进程内执行一个真实案例并计算案例验收。"""
    case = next((item for item in EVIDENCE_CASES if item.case_id == case_id), None)
    if case is None:
        raise KeyError(f"未知证据案例：{case_id}")
    started_at = perf_counter()
    if case.kind == "integrated-direct":
        return _execute_integrated_direct(case, run_index, started_at)
    if case.kind == "main-demo-online":
        return _execute_main_demo(case, run_index, started_at)
    if case.kind == "seeded-pressure":
        return _execute_seeded_pressure(case, run_index, started_at)
    return _execute_safety_gate(case, run_index, started_at)


def _execute_integrated_direct(
    case: EvidenceCase,
    run_index: int,
    started_at: float,
) -> EvidenceRun:
    scenario = _load_integrated_demo()
    result = run_dispatch(
        scenario,
        DispatchOptions(
            avoidConflicts=bool(case.avoid_conflicts),
            includeDynamic=False,
            assignmentReplanWindow=24,
            adaptiveReplanWindow=False,
        ),
    )
    metrics = result.metrics.model_dump(mode="json")
    accepted = (
        metrics["assignedTaskCount"] == len(result.tasks)
        and metrics["failureCount"] == 0
        and (
            metrics["conflictCount"] == 0
            if case.avoid_conflicts
            else metrics["conflictCount"] > 0
        )
    )
    return EvidenceRun.completed(
        case_id=case.case_id,
        category=case.kind,
        run_index=run_index,
        accepted=accepted,
        acceptance_error=None if accepted else "避碰对照验收不满足",
        wall_clock_ms=_elapsed_ms(started_at),
    ).with_data(
        robot_count=len(scenario.robots),
        task_count=len(result.tasks),
        dynamic_task_count=0,
        metrics=metrics,
    )


def _execute_main_demo(
    case: EvidenceCase,
    run_index: int,
    started_at: float,
) -> EvidenceRun:
    evidence = run_main_demo()
    final = evidence.final
    metrics = {
        **final.result.metrics.model_dump(mode="json"),
        "completedTaskCount": final.completedTaskCount,
        "runtimeTaskCount": final.runtimeTaskCount,
        "activeConflictCount": final.metricsHistory[-1].activeConflictCount,
    }
    timeline = tuple(
        {
            "time": step.time,
            "action": step.action,
            "completedTaskCount": step.result.completedTaskCount,
            "activeConflictCount": step.result.metricsHistory[-1].activeConflictCount,
            "failureCount": step.result.result.metrics.failureCount,
            "blockedCellCount": len(step.result.result.extraBlocked),
            "unavailableRobotCount": len(step.result.result.unavailableRobotIds),
        }
        for step in evidence.step_evidence
    )
    return EvidenceRun.completed(
        case_id=case.case_id,
        category=case.kind,
        run_index=run_index,
        accepted=True,
        wall_clock_ms=_elapsed_ms(started_at),
    ).with_data(
        robot_count=len(final.robotStates),
        task_count=len(final.taskStates),
        dynamic_task_count=1,
        metrics=metrics,
        timeline=timeline,
    )


def _execute_seeded_pressure(
    case: EvidenceCase,
    run_index: int,
    started_at: float,
) -> EvidenceRun:
    if case.seeded_case is None:
        raise RuntimeError(f"固定种子案例缺少参数：{case.case_id}")
    label, seed, robot_count, base_task_count = case.seeded_case
    scenario = seeded_pressure_scenario(label, seed, robot_count, base_task_count)
    result = run_dispatch(
        scenario,
        DispatchOptions(
            avoidConflicts=True,
            includeDynamic=True,
            assignmentReplanWindow=24,
            adaptiveReplanWindow=False,
        ),
    )
    metrics = result.metrics.model_dump(mode="json")
    task_count = len(result.tasks)
    accepted = (
        metrics["assignedTaskCount"] == task_count
        and metrics["conflictCount"] == 0
        and metrics["deadlineMissCount"] == 0
        and metrics["failureCount"] == 0
        and metrics["replanTimeMs"] < SEEDED_PRESSURE_PLANNING_TIME_BUDGET_MS
    )
    return EvidenceRun.completed(
        case_id=case.case_id,
        category=case.kind,
        run_index=run_index,
        accepted=accepted,
        acceptance_error=None if accepted else "固定种子案例验收不满足",
        wall_clock_ms=_elapsed_ms(started_at),
    ).with_data(
        seed=seed,
        robot_count=robot_count,
        task_count=task_count,
        dynamic_task_count=len(scenario.dynamic.tasks),
        metrics=metrics,
    )


def _execute_safety_gate(
    case: EvidenceCase,
    run_index: int,
    started_at: float,
) -> EvidenceRun:
    evidence = run_safety_demo()
    ordered = [evidence.checkpoints[time] for time in sorted(evidence.checkpoints)]
    interventions = sum(item.safetyIntervention is not None for item in ordered)
    stall_count = max(
        (
            item.safetyStall.consecutiveCount
            for item in ordered
            if item.safetyStall is not None
        ),
        default=0,
    )
    trajectory = tuple(
        {
            "time": item.currentTime,
            "positions": {
                state.robotId: list(state.position) for state in item.robotStates
            },
            "safetyIntervention": item.safetyIntervention is not None,
            "safetyStallCount": (
                item.safetyStall.consecutiveCount if item.safetyStall is not None else 0
            ),
        }
        for item in ordered
    )
    metrics = {
        "collisionFree": evidence.collision_free,
        "safetyInterventionCount": interventions,
        "safetyStallCount": stall_count,
        "activeConflictCount": ordered[-1].metricsHistory[-1].activeConflictCount,
    }
    accepted = (
        evidence.collision_free
        and interventions >= 3
        and stall_count == 3
        and metrics["activeConflictCount"] == 0
    )
    return EvidenceRun.completed(
        case_id=case.case_id,
        category=case.kind,
        run_index=run_index,
        accepted=accepted,
        acceptance_error=None if accepted else "安全门案例验收不满足",
        wall_clock_ms=_elapsed_ms(started_at),
    ).with_data(
        robot_count=len(ordered[-1].robotStates),
        task_count=len(ordered[-1].taskStates),
        dynamic_task_count=0,
        metrics=metrics,
        trajectory=trajectory,
    )


def _load_integrated_demo() -> Scenario:
    import json

    scenarios = json.loads(
        (PROJECT_ROOT / "frontend" / "src" / "domain" / "scenarios.json").read_text(
            encoding="utf-8"
        )
    )
    payload = next(item for item in scenarios if item["id"] == "integrated-demo")
    return Scenario.model_validate(payload)


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 2)


def run_evidence_cases(
    cases: tuple[EvidenceCase, ...],
    repetitions: int,
    timeout_seconds: float,
    *,
    on_result: Callable[[list[EvidenceRun]], None] | None = None,
) -> list[EvidenceRun]:
    """逐项隔离执行并保留所有可诊断结果。"""
    runs: list[EvidenceRun] = []
    for case, run_index in [
        (case, run_index)
        for case in cases
        for run_index in range(1, repetitions + 1)
    ]:
        started_at = perf_counter()
        try:
            execution = run_isolated_process(
                execute_evidence_case,
                (case.case_id, run_index),
                timeout_seconds,
            )
            if execution.outcome == "timeout":
                run = EvidenceRun.failed(
                    case,
                    run_index,
                    "timeout",
                    "TimeoutError",
                    None,
                    execution.wall_clock_ms,
                )
            elif execution.outcome == "error":
                run = EvidenceRun.failed(
                    case,
                    run_index,
                    "error",
                    execution.error_type or "ChildProcessError",
                    (
                        sanitize_error_text(execution.error_message)
                        if execution.error_message is not None
                        else None
                    ),
                    execution.wall_clock_ms,
                )
            elif isinstance(execution.value, EvidenceRun):
                run = replace(
                    execution.value,
                    wall_clock_ms=execution.wall_clock_ms,
                )
            else:
                run = EvidenceRun.failed(
                    case,
                    run_index,
                    "error",
                    "ChildProcessError",
                    "子进程返回了无效的证据结果载荷",
                    execution.wall_clock_ms,
                )
        except BenchmarkInfrastructureError:
            raise
        except Exception as exc:
            traceback.print_exc()
            run = EvidenceRun.failed(
                case,
                run_index,
                "error",
                type(exc).__name__,
                sanitize_error_text(str(exc)),
                round((perf_counter() - started_at) * 1000, 2),
            )
        runs.append(run)
        if on_result is not None:
            on_result(list(runs))
    return runs


def evidence_is_accepted(runs: list[EvidenceRun]) -> bool:
    return bool(runs) and all(
        run.outcome == "completed" and run.accepted for run in runs
    )


def summarize_evidence_runs(runs: list[EvidenceRun]) -> list[EvidenceCaseSummary]:
    summaries: list[EvidenceCaseSummary] = []
    for case_id in dict.fromkeys(run.case_id for run in runs):
        case_runs = [run for run in runs if run.case_id == case_id]
        completed = [run for run in case_runs if run.outcome == "completed"]
        wall_values = [run.wall_clock_ms for run in completed]
        replan_values = [
            float(run.metrics["replanTimeMs"])
            for run in completed
            if run.metrics is not None
            and isinstance(run.metrics.get("replanTimeMs"), (int, float))
        ]
        accepted_count = sum(run.accepted for run in case_runs)
        summaries.append(
            EvidenceCaseSummary(
                case_id=case_id,
                run_count=len(case_runs),
                completed_run_count=len(completed),
                timeout_count=sum(run.outcome == "timeout" for run in case_runs),
                error_count=sum(run.outcome == "error" for run in case_runs),
                accepted_run_count=accepted_count,
                accepted_run_rate_percent=_percent(accepted_count, len(case_runs)),
                median_wall_clock_ms=median(wall_values) if wall_values else None,
                p95_wall_clock_ms=_nearest_rank_p95(wall_values),
                median_replan_time_ms=median(replan_values) if replan_values else None,
                p95_replan_time_ms=_nearest_rank_p95(replan_values),
            )
        )
    return summaries


def chart_data(runs: list[EvidenceRun]) -> dict[str, list[dict[str, object]]]:
    def case_runs(case_id: str) -> list[EvidenceRun]:
        return [
            run
            for run in runs
            if run.case_id == case_id and run.outcome == "completed"
        ]

    without = case_runs("integrated-demo-without-conflict-avoidance")
    with_avoidance = case_runs("integrated-demo-with-conflict-avoidance")
    main_runs = case_runs("main-demo-online")
    seeded_runs = [
        run
        for case in EVIDENCE_CASES[3:6]
        for run in case_runs(case.case_id)
    ]
    safety_runs = case_runs("safety-gate-boundary")
    return {
        "conflict-avoidance": [
            {
                "label": "without",
                "values": [run.metrics["conflictCount"] for run in without if run.metrics],
            },
            {
                "label": "with",
                "values": [
                    run.metrics["conflictCount"] for run in with_avoidance if run.metrics
                ],
            },
        ],
        "dynamic-event-timeline": [
            item for run in main_runs for item in run.timeline
        ],
        "scale-performance": [
            {
                "robotCount": run.robot_count,
                "replanTimeMs": run.metrics["replanTimeMs"],
            }
            for run in seeded_runs
            if run.metrics
        ],
        "safety-gate-trajectory": [
            item for run in safety_runs for item in run.trajectory
        ],
    }


_RUN_FIELDS = (
    "caseId",
    "category",
    "runIndex",
    "outcome",
    "accepted",
    "acceptanceError",
    "errorType",
    "errorMessage",
    "wallClockMs",
    "seed",
    "robotCount",
    "taskCount",
    "dynamicTaskCount",
    "metrics",
    "timeline",
    "trajectory",
)
_SUMMARY_FIELDS = (
    "caseId",
    "runCount",
    "completedRunCount",
    "timeoutCount",
    "errorCount",
    "acceptedRunCount",
    "acceptedRunRatePercent",
    "medianWallClockMs",
    "p95WallClockMs",
    "medianReplanTimeMs",
    "p95ReplanTimeMs",
)


def write_evidence_bundle(output_dir: Path, report: EvidenceReport) -> None:
    """一次事务发布 JSON、CSV、图表和带哈希的证据清单。"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    chart_payloads = chart_data(report.runs)
    content: dict[str, bytes] = {
        "results.json": _json_bytes(report.to_record()),
        "runs.csv": _csv_bytes(
            _RUN_FIELDS,
            [_csv_run_record(run) for run in report.runs],
        ),
        "case-summaries.csv": _csv_bytes(
            _SUMMARY_FIELDS,
            [summary.to_record() for summary in report.case_summaries],
        ),
    }
    for spec in CHART_SPECS:
        values = _chart_numeric_values(spec.key, chart_payloads[spec.key])
        content[f"{spec.file_stem}.svg"] = _svg_chart(spec.title, values).encode("utf-8")
        content[f"{spec.file_stem}.png"] = _png_chart(values)

    manifest = _build_manifest(report, content)
    content["evidence-manifest.json"] = _json_bytes(manifest)
    _atomic_publish(output_path, content)


def _build_manifest(
    report: EvidenceReport,
    files: dict[str, bytes],
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "generatedAt": report.generated_at,
        "git": _git_state(),
        "environment": {
            "operatingSystem": platform.platform(),
            "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER") or "unknown",
            "pythonVersion": platform.python_version(),
        },
        "parameters": report.config,
        "files": {
            name: hashlib.sha256(payload).hexdigest()
            for name, payload in sorted(files.items())
        },
    }


def _git_state() -> dict[str, object]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    return {"commit": commit, "dirty": bool(status.strip())}


def _atomic_publish(output_path: Path, content: dict[str, bytes]) -> None:
    transaction_id = uuid4().hex
    targets = tuple(output_path / name for name in content)
    staged = {
        target: target.with_name(f".{target.name}.{transaction_id}.tmp")
        for target in targets
    }
    backups = {
        target: target.with_name(f".{target.name}.{transaction_id}.backup")
        for target in targets
    }
    backed_up: set[Path] = set()
    published: set[Path] = set()
    try:
        for target, payload in zip(targets, content.values()):
            staged[target].write_bytes(payload)
        try:
            for target in targets:
                if target.exists():
                    target.replace(backups[target])
                    backed_up.add(target)
            for target in targets:
                staged[target].replace(target)
                published.add(target)
        except BaseException:
            for target in published:
                target.unlink(missing_ok=True)
            for target in targets:
                if target in backed_up:
                    backups[target].replace(target)
                    backed_up.remove(target)
            raise
    except Exception as exc:
        raise OSError(f"写入 3S 证据包失败: {exc}") from exc
    finally:
        for path in (*staged.values(), *backups.values()):
            path.unlink(missing_ok=True)


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _csv_bytes(
    fields: tuple[str, ...],
    records: list[dict[str, object]],
) -> bytes:
    import io

    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
    writer.writeheader()
    writer.writerows(records)
    return ("\ufeff" + handle.getvalue()).encode("utf-8")


def _csv_run_record(run: EvidenceRun) -> dict[str, object]:
    record = run.to_record()
    for field in ("metrics", "timeline", "trajectory"):
        record[field] = json.dumps(record[field], ensure_ascii=False, separators=(",", ":"))
    return record


def _chart_numeric_values(
    key: str,
    records: list[dict[str, object]],
) -> list[float]:
    if key == "conflict-avoidance":
        return [
            float(value)
            for record in records
            for value in record.get("values", [])
        ]
    if key == "dynamic-event-timeline":
        return [float(record["time"]) for record in records]
    if key == "scale-performance":
        return [float(record["replanTimeMs"]) for record in records]
    return [float(record["time"]) for record in records]


def _svg_chart(title: str, values: list[float]) -> str:
    width, height, margin = 800, 450, 50
    maximum = max(values, default=1.0) or 1.0
    points = []
    for index, value in enumerate(values):
        x = margin + index * ((width - 2 * margin) / max(len(values) - 1, 1))
        y = height - margin - (value / maximum) * (height - 2 * margin)
        points.append(f"{x:.2f},{y:.2f}")
    polyline = " ".join(points)
    circles = "".join(
        f'<circle cx="{point.split(",")[0]}" cy="{point.split(",")[1]}" r="4" fill="#1f6feb"/>'
        for point in points
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">'
        '<rect width="100%" height="100%" fill="white"/>'
        f'<text x="{margin}" y="30" font-family="Arial" font-size="20">{title}</text>'
        f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#333"/>'
        f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#333"/>'
        f'<polyline points="{polyline}" fill="none" stroke="#1f6feb" stroke-width="3"/>'
        f'{circles}</svg>'
    )


def _png_chart(values: list[float]) -> bytes:
    width, height = 320, 180
    pixels = [[255, 255, 255] * width for _ in range(height)]
    maximum = max(values, default=1.0) or 1.0
    points: list[tuple[int, int]] = []
    for index, value in enumerate(values):
        x = 20 + round(index * ((width - 40) / max(len(values) - 1, 1)))
        y = height - 20 - round((value / maximum) * (height - 40))
        points.append((x, y))
    for first, second in zip(points, points[1:]):
        _draw_line(pixels, first, second)
    for x, y in points:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                _set_pixel(pixels, x + dx, y + dy, (31, 111, 235))
    raw = b"".join(b"\x00" + bytes(row) for row in pixels)
    return b"\x89PNG\r\n\x1a\n" + b"".join(
        _png_chunk(kind, payload)
        for kind, payload in (
            (b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            (b"IDAT", zlib.compress(raw, level=9)),
            (b"IEND", b""),
        )
    )


def _draw_line(
    pixels: list[list[int]],
    first: tuple[int, int],
    second: tuple[int, int],
) -> None:
    x1, y1 = first
    x2, y2 = second
    steps = max(abs(x2 - x1), abs(y2 - y1), 1)
    for step in range(steps + 1):
        ratio = step / steps
        _set_pixel(
            pixels,
            round(x1 + (x2 - x1) * ratio),
            round(y1 + (y2 - y1) * ratio),
            (31, 111, 235),
        )


def _set_pixel(
    pixels: list[list[int]],
    x: int,
    y: int,
    color: tuple[int, int, int],
) -> None:
    if 0 <= y < len(pixels) and 0 <= x < len(pixels[0]) // 3:
        offset = x * 3
        pixels[y][offset : offset + 3] = color


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _percent(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def _nearest_rank_p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[ceil(0.95 * len(ordered)) - 1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 3S 正式提交证据包")
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "output" / "3s-submission-evidence",
    )
    return parser.parse_args(argv)


def _validate_cli_config(args: argparse.Namespace) -> None:
    if args.repetitions < 1:
        raise ValueError("repetitions 必须大于等于 1")
    if not (args.timeout_seconds > 0 and args.timeout_seconds < float("inf")):
        raise ValueError("timeout-seconds 必须是有限正数")


def _create_result_directory(base_dir: Path) -> Path:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = 1
    while True:
        candidate = base / (timestamp if suffix == 1 else f"{timestamp}-{suffix}")
        try:
            candidate.mkdir()
            return candidate.resolve()
        except FileExistsError:
            suffix += 1


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        _validate_cli_config(args)
    except (SystemExit, ValueError) as exc:
        if isinstance(exc, ValueError):
            print(f"证据配置无效: {exc}", file=sys.stderr)
            return 1
        return int(exc.code)

    try:
        result_path = _create_result_directory(args.output_dir)
        config = {
            "repetitions": args.repetitions,
            "timeoutSeconds": args.timeout_seconds,
        }
        runs = run_evidence_cases(
            EVIDENCE_CASES,
            repetitions=args.repetitions,
            timeout_seconds=args.timeout_seconds,
        )
        write_evidence_bundle(result_path, EvidenceReport.create(config, runs))
    except Exception as exc:
        print(f"3S 证据生成失败: {exc}", file=sys.stderr)
        return 1

    print(result_path)
    return 0 if evidence_is_accepted(runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
