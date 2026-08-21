"""3S 材料流水线共享的证据、哈希与原子发布工具。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path


class DocumentGenerationError(RuntimeError):
    """表示材料生成、转换、渲染或验证未通过。"""


def sha256_file(path: Path) -> str:
    """计算文件的 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_verified_evidence(path: Path) -> dict[str, object]:
    """读取已由正式证据清单绑定且全部验收的运行结果。"""

    results_path = path / "results.json"
    manifest_path = path / "evidence-manifest.json"
    if not results_path.is_file() or not manifest_path.is_file():
        raise DocumentGenerationError(
            "证据目录缺少正式 results.json 或 evidence-manifest.json。"
        )
    try:
        results = json.loads(results_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DocumentGenerationError("正式证据不是有效 UTF-8 JSON。") from exc
    files = manifest.get("files")
    if not isinstance(files, dict) or files.get("results.json") != sha256_file(
        results_path
    ):
        raise DocumentGenerationError("正式证据 results.json 的清单哈希不匹配。")
    runs = results.get("runs")
    if not isinstance(runs, list) or not runs:
        raise DocumentGenerationError("正式证据没有可用运行记录。")
    if any(
        not isinstance(run, dict)
        or run.get("outcome") != "completed"
        or run.get("accepted") is not True
        for run in runs
    ):
        raise DocumentGenerationError("正式证据包含未完成或未验收运行。")
    return results


def resolve_latest_evidence(base: Path) -> Path:
    """解析显式证据目录或其最新的时间戳子目录。"""

    if (base / "results.json").is_file():
        return base
    candidates = (
        sorted(
            (
                item
                for item in base.iterdir()
                if item.is_dir() and (item / "results.json").is_file()
            ),
            key=lambda item: item.name,
            reverse=True,
        )
        if base.is_dir()
        else []
    )
    if not candidates:
        raise DocumentGenerationError("找不到默认正式证据目录。")
    return candidates[0]


def publish_directory_atomically(staging: Path, destination: Path) -> None:
    """以可回滚替换发布完整目录。"""

    if not staging.is_dir():
        raise DocumentGenerationError("材料临时目录不存在。")
    backup = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.backup")
    moved_old = False
    try:
        if destination.exists():
            os.replace(destination, backup)
            moved_old = True
        os.replace(staging, destination)
    except OSError:
        if destination.exists() and moved_old:
            shutil.rmtree(destination, ignore_errors=True)
        if moved_old and backup.exists():
            os.replace(backup, destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)
