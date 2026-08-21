"""通过隔离 Word 进程只读转换旧版 DOC。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any


class LegacyDocConversionError(RuntimeError):
    """旧版 DOC 转换门禁失败。"""


class LegacyDocConverter:
    def __init__(
        self,
        *,
        command_builder: Callable[[Path, Path, Path], list[str]] | None = None,
        process_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        terminate_pid: Callable[[int], None] | None = None,
        timeout_seconds: float = 30,
    ) -> None:
        self.command_builder = command_builder or _default_word_command
        self.process_factory = process_factory
        self.terminate_pid = terminate_pid or _terminate_pid
        self.timeout_seconds = timeout_seconds

    def convert(self, source: Path, destination: Path) -> dict[str, object]:
        source = source.resolve()
        destination = destination.resolve()
        if source.suffix.lower() != ".doc" or not source.is_file():
            raise LegacyDocConversionError("显式来源必须是存在的旧版 .doc 文件。")
        if destination.suffix.lower() != ".docx":
            raise LegacyDocConversionError("转换目标必须使用新的 .docx 路径。")
        if source == destination:
            raise LegacyDocConversionError("转换不得覆盖原件。")
        if destination.exists() or destination.with_suffix(".conversion.json").exists():
            raise LegacyDocConversionError("转换目标或记录已存在，拒绝覆盖。")
        if not (self.timeout_seconds > 0):
            raise LegacyDocConversionError("转换超时必须是正数。")

        destination.parent.mkdir(parents=True, exist_ok=True)
        source_hash = _sha256(source)
        receipt_path = destination.with_suffix(".conversion.json")
        with tempfile.TemporaryDirectory(
            prefix=".legacy-doc-conversion-",
            dir=destination.parent,
        ) as temporary_directory:
            temporary_root = Path(temporary_directory)
            temporary_docx = temporary_root / "converted.docx"
            word_pid_file = temporary_root / "word.pid"
            temporary_receipt = temporary_root / "conversion.json"
            command = self.command_builder(source, temporary_docx, word_pid_file)
            process: subprocess.Popen[str] | None = None
            word_pid: int | None = None
            try:
                process = self.process_factory(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                    ),
                )
                try:
                    process.communicate(timeout=self.timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    word_pid = _read_pid(word_pid_file)
                    _terminate_process(process)
                    if word_pid is not None and word_pid != process.pid:
                        self.terminate_pid(word_pid)
                    raise LegacyDocConversionError("旧版 DOC 转换超时，已清理隔离进程。") from exc
                word_pid = _read_pid(word_pid_file)
                if process.returncode != 0:
                    raise LegacyDocConversionError("旧版 DOC 转换失败，隔离 Word 未生成结果。")
                if not temporary_docx.is_file() or not zipfile.is_zipfile(temporary_docx):
                    raise LegacyDocConversionError("旧版 DOC 转换未生成有效 DOCX。")
                receipt: dict[str, object] = {
                    "schemaVersion": 1,
                    "sourceSha256": source_hash,
                    "outputSha256": _sha256(temporary_docx),
                }
                temporary_receipt.write_text(
                    json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary_docx, destination)
                try:
                    os.replace(temporary_receipt, receipt_path)
                except OSError:
                    destination.unlink(missing_ok=True)
                    raise
                return receipt
            except LegacyDocConversionError:
                raise
            except (OSError, subprocess.SubprocessError) as exc:
                raise LegacyDocConversionError("旧版 DOC 转换进程无法安全完成。") from exc
            finally:
                if process is not None and process.poll() is None:
                    _terminate_process(process)
                if word_pid is None:
                    word_pid = _read_pid(word_pid_file)
                if (
                    word_pid is not None
                    and (process is None or word_pid != process.pid)
                    and _pid_exists(word_pid)
                ):
                    self.terminate_pid(word_pid)


def _default_word_command(
    source: Path,
    destination: Path,
    pid_file: Path,
) -> list[str]:
    executable = os.environ.get("WAREHOUSE_PATROL_PWSH") or shutil.which("pwsh")
    if executable is None:
        raise LegacyDocConversionError("找不到 PowerShell 7，无法启动隔离 Word 转换。")
    worker = Path(__file__).resolve().parents[2] / "competition" / "convert-legacy-doc-worker.ps1"
    if not worker.is_file():
        raise LegacyDocConversionError("旧版 DOC 隔离转换脚本不存在。")
    return [
        executable,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(worker),
        "-SourcePath",
        str(source),
        "-DestinationPath",
        str(destination),
        "-PidFile",
        str(pid_file),
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_pid(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_pid(pid: int) -> None:
    if not _pid_exists(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        if not _pid_exists(pid):
            return
        time.sleep(0.05)
    if hasattr(signal, "SIGKILL"):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读转换显式旧版 DOC 来源")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        LegacyDocConverter(timeout_seconds=args.timeout_seconds).convert(
            args.source,
            args.destination,
        )
    except LegacyDocConversionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("旧版 DOC 已转换并记录原件 SHA-256。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
