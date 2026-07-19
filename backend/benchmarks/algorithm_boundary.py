import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.benchmarks.reporting import write_final_report, write_partial_report
from backend.benchmarks.results import BenchmarkReport, BenchmarkRun
from backend.benchmarks.runner import run_benchmark_cases
from backend.benchmarks.scenarios import benchmark_cases, benchmark_options


DEFAULT_FAMILIES = ("scale", "density", "bottleneck")


def _parse_families(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行算法边界基准")
    parser.add_argument("--families", type=_parse_families, default=DEFAULT_FAMILIES)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument(
        "--output-dir",
        default="output/algorithm-boundary-benchmark",
    )
    return parser.parse_args(argv)


def _validate_config(args: argparse.Namespace) -> None:
    unknown_families = sorted(set(args.families) - set(DEFAULT_FAMILIES))
    if unknown_families:
        raise ValueError(f"未知基准场景族: {', '.join(unknown_families)}")
    if args.repetitions <= 0:
        raise ValueError("--repetitions 必须为正整数")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds 必须为正数")


def _create_result_directory(output_dir: str) -> Path:
    base_path = Path(output_dir).resolve()
    try:
        base_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"创建基准输出目录失败: {base_path}: {exc}") from exc

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
            raise OSError(f"创建基准结果目录失败: {result_path.resolve()}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    try:
        _validate_config(args)
        cases = benchmark_cases(args.families)
    except (TypeError, ValueError) as exc:
        print(f"基准配置无效: {exc}", file=sys.stderr)
        return 1

    try:
        result_path = _create_result_directory(args.output_dir)
        config = {
            "families": list(args.families),
            "repetitions": args.repetitions,
            "timeoutSeconds": args.timeout_seconds,
            "outputDir": str(result_path),
            "options": benchmark_options().model_dump(mode="json"),
        }

        def on_result(runs: list[BenchmarkRun]) -> None:
            write_partial_report(result_path, BenchmarkReport.create(config=config, runs=runs))

        runs = run_benchmark_cases(
            cases,
            repetitions=args.repetitions,
            timeout_seconds=args.timeout_seconds,
            on_result=on_result,
        )
        write_final_report(
            result_path,
            BenchmarkReport.create(config=config, runs=runs),
        )
    except Exception as exc:
        print(f"算法边界基准失败: {exc}", file=sys.stderr)
        return 1

    print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
