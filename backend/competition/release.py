"""3S 正式发布组装、扫描与哈希。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from backend.competition.document_support import (
    DocumentGenerationError,
    load_verified_evidence,
    resolve_latest_evidence,
)
from backend.competition.documents import _verify_existing_documents
from backend.competition.submission import SubmissionGateError, load_verified_submission_metadata


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELEASE_DIRECTORIES = (
    "01-application",
    "02-technical-report",
    "03-software",
    "03-software/windows-x64",
    "04-source",
    "05-evidence",
    "05-evidence/raw",
    "05-evidence/charts",
    "06-video",
    "07-licenses",
)


class ReleaseGateError(RuntimeError):
    """发布门未通过。"""


@dataclass(frozen=True, slots=True)
class ReleaseInputs:
    documents_directory: Path
    evidence_directory: Path
    software_directory: Path
    source_archive: Path
    video_path: Path
    recording_manifest: Path
    metadata_filename: str


def recording_gate(video_path: Path | None, recording_manifest: Path | None) -> dict[str, object]:
    if video_path is None or recording_manifest is None or not video_path.is_file() or not recording_manifest.is_file():
        return {"passed": False, "reasonCode": "task6RecordingMissing"}
    try:
        manifest = json.loads(recording_manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"passed": False, "reasonCode": "recordingManifestInvalid"}
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schemaVersion", "approved", "videoSha256"}
        or manifest.get("schemaVersion") != 1
        or manifest.get("approved") is not True
        or manifest.get("videoSha256") != _sha256(video_path)
    ):
        return {"passed": False, "reasonCode": "recordingManifestInvalid"}
    return {"passed": True, "reasonCode": "verified"}


def write_release_gate_diagnostics(path: Path, gates: dict[str, dict[str, object]]) -> None:
    required = {
        "externalProcedures",
        "software",
        "evidence",
        "documents",
        "recording",
        "hashes",
    }
    if set(gates) != required or any(
        not isinstance(value, dict)
        or set(value) != {"passed", "reasonCode"}
        or not isinstance(value.get("passed"), bool)
        or not isinstance(value.get("reasonCode"), str)
        for value in gates.values()
    ):
        raise ReleaseGateError("发布诊断必须精确记录六个门。")
    payload = {
        "schemaVersion": 1,
        "gates": gates,
        "formalSubmissionReady": all(value["passed"] is True for value in gates.values()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def assemble_validated_release(inputs: ReleaseInputs, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    create_release_layout(staging)
    authorized: set[str] = set()

    def copy(source: Path, relative: str) -> None:
        if not source.is_file() or source.is_symlink():
            raise ReleaseGateError(f"已验证输入缺少必需文件角色：{relative}")
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        authorized.add(relative)

    try:
        for suffix in ("docx", "pdf"):
            copy(inputs.documents_directory / f"application-summary.{suffix}", f"01-application/application-summary.{suffix}")
            copy(inputs.documents_directory / f"technical-report.{suffix}", f"02-technical-report/technical-report.{suffix}")
            copy(inputs.documents_directory / f"run-guide.{suffix}", f"03-software/run-guide.{suffix}")
            copy(inputs.documents_directory / f"third-party-dependencies.{suffix}", f"07-licenses/third-party-dependencies.{suffix}")

        if not inputs.software_directory.is_dir():
            raise ReleaseGateError("已验证软件目录不存在。")
        for source in sorted(inputs.software_directory.rglob("*")):
            if source.is_symlink():
                raise ReleaseGateError("软件目录不得包含符号链接。")
            if source.is_file():
                relative_source = source.relative_to(inputs.software_directory).as_posix()
                copy(source, f"03-software/windows-x64/{relative_source}")
        if "03-software/windows-x64/WarehousePatrol.exe" not in authorized:
            raise ReleaseGateError("软件门缺少 WarehousePatrol.exe。")
        if not zipfile.is_zipfile(inputs.source_archive):
            raise ReleaseGateError("源码包不是有效 ZIP。")
        copy(inputs.source_archive, "04-source/WarehousePatrol-source.zip")

        if not inputs.evidence_directory.is_dir():
            raise ReleaseGateError("已验证证据目录不存在。")
        for source in sorted(inputs.evidence_directory.iterdir()):
            if not source.is_file() or source.is_symlink():
                continue
            group = "charts" if source.suffix.lower() in {".png", ".svg"} else "raw"
            copy(source, f"05-evidence/{group}/{source.name}")
        if "05-evidence/raw/results.json" not in authorized:
            raise ReleaseGateError("证据门缺少 results.json。")

        copy(inputs.video_path, f"06-video/{inputs.video_path.name}")
        copy(inputs.recording_manifest, "06-video/recording-manifest.json")

        issues = scan_sensitive_content(
            staging,
            authorized_paths=authorized,
            metadata_filename=inputs.metadata_filename,
        )
        if issues:
            categories = sorted({issue["category"] for issue in issues})
            raise ReleaseGateError("敏感信息扫描未通过：" + "、".join(categories))

        gates = {
            "externalProcedures": True,
            "software": True,
            "evidence": True,
            "documents": True,
            "recording": True,
            "hashes": True,
        }
        (staging / "release-manifest.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "gates": gates,
                    "formalSubmissionReady": True,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        write_release_hashes(staging)
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        verified = subprocess.run(
            [
                sys.executable,
                "-m",
                "backend.competition.release",
                "verify-hashes",
                "--release-dir",
                str(staging),
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if verified.returncode != 0:
            raise ReleaseGateError("发布哈希独立进程重验失败。")
        atomic_publish_directory(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def create_release_layout(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=False)
    for relative in RELEASE_DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)


_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|token)\b\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
)
_PERSONAL_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+[^\\/\s]+"),
    re.compile(r"(?i)(?:^|\s)/(?:Users|home)/[^/\s]+"),
)


def _scan_text(text: str, location: str, metadata_filename: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if any(pattern.search(text) for pattern in _SECRET_PATTERNS):
        issues.append({"category": "secret", "location": location})
    if any(pattern.search(text) for pattern in _PERSONAL_PATH_PATTERNS):
        issues.append({"category": "personalAbsolutePath", "location": location})
    if metadata_filename and metadata_filename.casefold() in text.casefold():
        issues.append({"category": "localMetadata", "location": location})
    return issues


def scan_sensitive_content(
    root: Path,
    *,
    authorized_paths: set[str],
    metadata_filename: str,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() or item.is_symlink()):
        relative = path.relative_to(root).as_posix()
        parts = {part.casefold() for part in Path(relative).parts}
        if path.is_symlink():
            issues.append({"category": "unauthorizedMaterial", "location": relative})
            continue
        if relative not in authorized_paths:
            issues.append({"category": "unauthorizedMaterial", "location": relative})
        if path.name.casefold() in {".env", metadata_filename.casefold()}:
            category = "environmentFile" if path.name.casefold() == ".env" else "localMetadata"
            issues.append({"category": category, "location": relative})
        if parts.intersection({"__pycache__", ".cache", ".pytest_cache", "node_modules"}):
            issues.append({"category": "cache", "location": relative})
        if zipfile.is_zipfile(path):
            try:
                with zipfile.ZipFile(path) as archive:
                    for info in archive.infolist():
                        entry = info.filename.replace("\\", "/")
                        entry_parts = {part.casefold() for part in Path(entry).parts}
                        location = f"{relative}!{entry}"
                        if Path(entry).name.casefold() in {".env", metadata_filename.casefold()}:
                            category = "environmentFile" if Path(entry).name.casefold() == ".env" else "localMetadata"
                            issues.append({"category": category, "location": location})
                        if entry_parts.intersection({"__pycache__", ".cache", ".pytest_cache", "node_modules"}):
                            issues.append({"category": "cache", "location": location})
                        if info.is_dir() or info.file_size > 16 * 1024 * 1024:
                            continue
                        text = archive.read(info).decode("utf-8", errors="ignore")
                        issues.extend(_scan_text(text, location, metadata_filename))
            except (OSError, zipfile.BadZipFile):
                issues.append({"category": "unreadableArchive", "location": relative})
        elif path.stat().st_size <= 16 * 1024 * 1024:
            text = path.read_bytes().decode("utf-8", errors="ignore")
            issues.extend(_scan_text(text, relative, metadata_filename))
    unique = {(item["category"], item["location"]): item for item in issues}
    return [unique[key] for key in sorted(unique)]


def atomic_publish_directory(
    staging: Path,
    destination: Path,
    *,
    replace: Callable[[Path, Path], None] = os.replace,
) -> None:
    if not staging.is_dir():
        raise ReleaseGateError("临时发布目录不存在。")
    backup = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.backup")
    moved_old = False
    try:
        if destination.exists():
            replace(destination, backup)
            moved_old = True
        replace(staging, destination)
    except OSError:
        if destination.exists() and moved_old:
            shutil.rmtree(destination, ignore_errors=True)
        if moved_old and backup.exists():
            replace(backup, destination)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def write_release_hashes(root: Path) -> dict[str, str]:
    manifest_path = root / "release-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseGateError("release-manifest.json 无效。") from exc
    gate_names = {
        "externalProcedures",
        "software",
        "evidence",
        "documents",
        "recording",
        "hashes",
    }
    gates = manifest.get("gates")
    if not isinstance(gates, dict) or set(gates) != gate_names:
        raise ReleaseGateError("release-manifest.json 必须精确记录六个门。")
    all_passed = all(gates.get(name) is True for name in gate_names)
    if not all_passed or manifest.get("formalSubmissionReady") is not True:
        raise ReleaseGateError("六个门未全部通过，不得生成正式发布哈希。")
    entries = {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(item for item in root.rglob("*") if item.is_file())
        if path.name != "SHA256SUMS.txt"
    }
    temporary = root / f".SHA256SUMS.{uuid.uuid4().hex}.tmp"
    temporary.write_text(
        "".join(f"{digest}  {relative}\n" for relative, digest in sorted(entries.items())),
        encoding="utf-8",
    )
    os.replace(temporary, root / "SHA256SUMS.txt")
    return entries


def verify_release_hashes(root: Path) -> list[str]:
    sums_path = root / "SHA256SUMS.txt"
    if not sums_path.is_file():
        return ["缺少 SHA256SUMS.txt"]
    expected: dict[str, str] = {}
    issues: list[str] = []
    try:
        lines = sums_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ["SHA256SUMS.txt 不是有效 UTF-8"]
    for index, line in enumerate(lines, start=1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^\r\n]+)", line)
        if match is None:
            issues.append(f"第 {index} 行格式无效")
            continue
        digest, relative = match.groups()
        if relative == "SHA256SUMS.txt" or relative in expected:
            issues.append(f"第 {index} 行目标无效或重复")
            continue
        expected[relative] = digest
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS.txt"
    }
    if set(expected) != actual_paths:
        issues.append("哈希覆盖集合与正式文件集合不一致")
    for relative, digest in expected.items():
        path = root / Path(relative)
        if not path.is_file() or _sha256(path) != digest:
            issues.append(f"哈希不匹配：{relative}")
    return issues


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_release(
    *,
    metadata_path: Path,
    approvals_path: Path | None,
    documents_directory: Path,
    evidence_directory: Path,
    software_directory: Path,
    source_archive: Path,
    video_path: Path | None,
    recording_manifest: Path | None,
    destination: Path,
    diagnostics_path: Path,
) -> None:
    gate_results: dict[str, dict[str, object]] = {}
    try:
        load_verified_submission_metadata(metadata_path, REPOSITORY_ROOT)
        gate_results["externalProcedures"] = {"passed": True, "reasonCode": "verified"}
    except SubmissionGateError:
        gate_results["externalProcedures"] = {"passed": False, "reasonCode": "metadataGateFailed"}

    software_passed = (
        (software_directory / "WarehousePatrol.exe").is_file()
        and source_archive.is_file()
        and zipfile.is_zipfile(source_archive)
    )
    gate_results["software"] = {
        "passed": software_passed,
        "reasonCode": "verified" if software_passed else "softwareInputMissing",
    }

    resolved_evidence: Path | None = None
    try:
        resolved_evidence = resolve_latest_evidence(evidence_directory)
        load_verified_evidence(resolved_evidence)
        gate_results["evidence"] = {"passed": True, "reasonCode": "verified"}
    except DocumentGenerationError:
        gate_results["evidence"] = {"passed": False, "reasonCode": "evidenceGateFailed"}

    documents_passed = _verify_existing_documents(documents_directory, approvals_path)
    gate_results["documents"] = {
        "passed": documents_passed,
        "reasonCode": "verified" if documents_passed else "visualApprovalMissingOrStale",
    }
    gate_results["recording"] = recording_gate(video_path, recording_manifest)
    gate_results["hashes"] = {"passed": False, "reasonCode": "notGenerated"}
    write_release_gate_diagnostics(diagnostics_path, gate_results)
    if not all(gate_results[name]["passed"] is True for name in gate_results if name != "hashes"):
        raise ReleaseGateError("正式发布六门未全部通过。")
    if resolved_evidence is None or video_path is None or recording_manifest is None:
        raise ReleaseGateError("正式发布输入状态不一致。")

    assemble_validated_release(
        ReleaseInputs(
            documents_directory=documents_directory,
            evidence_directory=resolved_evidence,
            software_directory=software_directory,
            source_archive=source_archive,
            video_path=video_path,
            recording_manifest=recording_manifest,
            metadata_filename=metadata_path.name,
        ),
        destination,
    )
    gate_results["hashes"] = {"passed": True, "reasonCode": "verified"}
    write_release_gate_diagnostics(diagnostics_path, gate_results)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="3S 正式发布工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    verify = subparsers.add_parser("verify-hashes")
    verify.add_argument("--release-dir", required=True, type=Path)
    build = subparsers.add_parser("build")
    build.add_argument("--metadata", required=True, type=Path)
    build.add_argument("--approvals", type=Path)
    build.add_argument("--documents-dir", type=Path, default=REPOSITORY_ROOT / "output/3s-submission-documents")
    build.add_argument("--evidence-dir", type=Path, default=REPOSITORY_ROOT / "output/3s-submission-evidence")
    build.add_argument("--software-dir", type=Path, default=REPOSITORY_ROOT / "output/3s-competition-build/WarehousePatrol")
    build.add_argument("--source-archive", type=Path, default=REPOSITORY_ROOT / "output/3s-competition-build/WarehousePatrol-source.zip")
    build.add_argument("--video", type=Path)
    build.add_argument("--recording-manifest", type=Path)
    build.add_argument("--destination", type=Path, default=REPOSITORY_ROOT / "submission/warehouse-patrol-3s")
    build.add_argument("--diagnostics", type=Path, default=REPOSITORY_ROOT / "output/3s-release-gates.json")
    args = parser.parse_args(argv)
    if args.command == "verify-hashes":
        issues = verify_release_hashes(args.release_dir)
        if issues:
            print("发布哈希独立重验失败。", file=sys.stderr)
            return 1
        print("发布哈希独立重验通过。")
        return 0
    if args.command == "build":
        try:
            build_release(
                metadata_path=args.metadata,
                approvals_path=args.approvals,
                documents_directory=args.documents_dir,
                evidence_directory=args.evidence_dir,
                software_directory=args.software_dir,
                source_archive=args.source_archive,
                video_path=args.video,
                recording_manifest=args.recording_manifest,
                destination=args.destination,
                diagnostics_path=args.diagnostics,
            )
        except (ReleaseGateError, OSError):
            print("正式发布六门未全部通过；正式目录未替换。", file=sys.stderr)
            return 1
        print("3S 正式发布目录已原子替换并完成独立哈希重验。")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
