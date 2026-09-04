import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.benchmarks.solvability_cases import solvability_cases, solvability_catalog
from backend.benchmarks.solvability_reporting import (
    write_final_report,
    write_partial_report,
)
from backend.benchmarks.solvability_results import SolvabilityReport, SolvabilityRun
from backend.benchmarks.solvability_runner import run_solvability_cases


DEFAULT_SAMPLE_COUNT = 64
DEFAULT_SEED = 20260904
DEFAULT_REPETITIONS = 1
DEFAULT_MAX_EXPANDED_STATES = 100_000
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_OUTPUT_DIR = "output/solvability-differential"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行可解性差分基准")
    parser.add_argument("--sample-count", type=int, default=DEFAULT_SAMPLE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument(
        "--max-expanded-states",
        type=int,
        default=DEFAULT_MAX_EXPANDED_STATES,
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def _validate_config(args: argparse.Namespace) -> None:
    if not 0 <= args.sample_count <= 512:
        raise ValueError("--sample-count 必须在 0..512 范围内")
    if args.repetitions <= 0:
        raise ValueError("--repetitions 必须为正整数")
    if args.max_expanded_states <= 0:
        raise ValueError("--max-expanded-states 必须为正整数")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds 必须为有限正数")


def _create_result_directory(output_dir: str) -> Path:
    base_path = Path(output_dir).resolve()
    try:
        base_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"创建可解性差分输出目录失败: {base_path}: {exc}") from exc

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = 1
    while True:
        directory_name = timestamp if suffix == 1 else f"{timestamp}-{suffix}"
        result_path = base_path / directory_name
        try:
            result_path.mkdir()
            return result_path.resolve()
        except FileExistsError:
            suffix += 1
        except OSError as exc:
            raise OSError(
                f"创建可解性差分结果目录失败: {result_path.resolve()}: {exc}"
            ) from exc


def _report_config(args: argparse.Namespace, cases: list[object], result_path: Path):
    return {
        "seed": args.seed,
        "sampleCount": args.sample_count,
        "catalogCaseCount": len(solvability_catalog()),
        "caseCount": len(cases),
        "repetitions": args.repetitions,
        "maxExpandedStates": args.max_expanded_states,
        "timeoutSeconds": args.timeout_seconds,
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


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        _validate_config(args)
        cases = list(solvability_cases(args.seed, args.sample_count))
    except (TypeError, ValueError) as exc:
        print(f"可解性差分配置无效: {exc}", file=sys.stderr)
        return 1

    try:
        result_path = _create_result_directory(args.output_dir)
        config = _report_config(args, cases, result_path)

        def on_result(runs: list[SolvabilityRun]) -> None:
            write_partial_report(
                result_path,
                SolvabilityReport.create(
                    config=config,
                    cases=cases,
                    runs=runs,
                ),
            )

        runs = run_solvability_cases(
            tuple(cases),
            repetitions=args.repetitions,
            max_expanded_states=args.max_expanded_states,
            timeout_seconds=args.timeout_seconds,
            on_result=on_result,
        )
        write_final_report(
            result_path,
            SolvabilityReport.create(
                config=config,
                cases=cases,
                runs=runs,
            ),
        )
    except Exception as exc:
        print(f"可解性差分基准失败: {exc}", file=sys.stderr)
        return 1

    print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
