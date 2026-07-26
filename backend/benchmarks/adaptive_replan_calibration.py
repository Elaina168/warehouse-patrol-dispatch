import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.app.replan_window import DEFAULT_ADAPTIVE_REPLAN_POLICY
from backend.benchmarks.adaptive_reporting import (
    write_final_report,
    write_partial_report,
)
from backend.benchmarks.adaptive_results import (
    AdaptiveCalibrationReport,
    AdaptiveCalibrationRun,
)
from backend.benchmarks.adaptive_runner import run_adaptive_calibration_cases
from backend.benchmarks.adaptive_scenarios import (
    RUNTIME_TASK_TICKS,
    AdaptiveCalibrationCase,
    adaptive_calibration_cases,
)
from backend.benchmarks.adaptive_variants import (
    AdaptiveCalibrationVariant,
    adaptive_calibration_variants,
)


DEFAULT_CASE_IDS = tuple(case.case_id for case in adaptive_calibration_cases())
DEFAULT_VARIANT_IDS = tuple(
    variant.variant_id for variant in adaptive_calibration_variants()
)


def _parse_identifier_list(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行自适应重规划窗口离线校准"
    )
    parser.add_argument(
        "--cases",
        type=_parse_identifier_list,
        default=DEFAULT_CASE_IDS,
    )
    parser.add_argument(
        "--variants",
        type=_parse_identifier_list,
        default=DEFAULT_VARIANT_IDS,
    )
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument(
        "--output-dir",
        default="output/adaptive-replan-calibration",
    )
    return parser.parse_args(argv)


def _validate_config(
    args: argparse.Namespace,
) -> tuple[
    tuple[AdaptiveCalibrationCase, ...],
    tuple[AdaptiveCalibrationVariant, ...],
]:
    cases = adaptive_calibration_cases(args.cases)
    variants = adaptive_calibration_variants(args.variants)
    if args.repetitions <= 0:
        raise ValueError("--repetitions 必须为正整数")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds 必须为有限正数")
    return cases, variants


def _create_result_directory(output_dir: str) -> Path:
    base_path = Path(output_dir).resolve()
    try:
        base_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(
            f"创建自适应窗口校准输出目录失败: {base_path}: {exc}"
        ) from exc

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = 1
    while True:
        directory_name = (
            timestamp if suffix == 1 else f"{timestamp}-{suffix}"
        )
        result_path = base_path / directory_name
        try:
            result_path.mkdir()
            return result_path.resolve()
        except FileExistsError:
            suffix += 1
        except OSError as exc:
            raise OSError(
                "创建自适应窗口校准结果目录失败: "
                f"{result_path.resolve()}: {exc}"
            ) from exc


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        cases, variants = _validate_config(args)
    except (TypeError, ValueError) as exc:
        print(f"自适应窗口校准配置无效: {exc}", file=sys.stderr)
        return 1

    try:
        result_path = _create_result_directory(args.output_dir)
        policy = DEFAULT_ADAPTIVE_REPLAN_POLICY
        config = {
            "caseIds": [case.case_id for case in cases],
            "variantIds": [
                variant.variant_id for variant in variants
            ],
            "repetitions": args.repetitions,
            "timeoutSeconds": args.timeout_seconds,
            "tickTarget": cases[0].tick_target,
            "runtimeTaskTicks": list(RUNTIME_TASK_TICKS),
            "outputDir": str(result_path),
            "defaultPolicy": {
                "slowEnterThresholdMs": policy.slow_enter_threshold_ms,
                "slowExitThresholdMs": policy.slow_exit_threshold_ms,
                "taskPressureMultiplier": policy.task_pressure_multiplier,
            },
        }

        def on_result(runs: list[AdaptiveCalibrationRun]) -> None:
            write_partial_report(
                result_path,
                AdaptiveCalibrationReport.create(config, runs),
            )

        runs = run_adaptive_calibration_cases(
            cases,
            variants,
            repetitions=args.repetitions,
            timeout_seconds=args.timeout_seconds,
            on_result=on_result,
        )
        write_final_report(
            result_path,
            AdaptiveCalibrationReport.create(config, runs),
        )
    except Exception as exc:
        print(f"自适应窗口校准失败: {exc}", file=sys.stderr)
        return 1

    print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
