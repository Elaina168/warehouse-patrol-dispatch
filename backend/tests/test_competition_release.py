from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from backend.competition.release import (
    RELEASE_DIRECTORIES,
    ReleaseInputs,
    ReleaseGateError,
    assemble_validated_release,
    atomic_publish_directory,
    create_release_layout,
    recording_gate,
    scan_sensitive_content,
    verify_release_hashes,
    write_release_hashes,
    write_release_gate_diagnostics,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _utf8_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def test_sensitive_scan_allows_formal_application_pii_but_rejects_secrets_metadata_paths_and_unauthorized_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release"
    application = root / "01-application/application-summary.docx"
    application.parent.mkdir(parents=True)
    with zipfile.ZipFile(application, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "受控申请人甲 13000000000 受控学校",
        )
    allowed = {application.relative_to(root).as_posix()}

    assert scan_sensitive_content(
        root,
        authorized_paths=allowed,
        metadata_filename="submission-metadata.local.json",
    ) == []

    source = root / "04-source/WarehousePatrol-source.zip"
    source.parent.mkdir(parents=True)
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("config.txt", "api_key = 'sk-controlled-secret-token-1234567890'")
        archive.writestr("submission-metadata.local.json", "{}")
        archive.writestr("notes.txt", r"C:\Users\controlled-user\secret.txt")
    unauthorized = root / "05-evidence/raw/unapproved-material.bin"
    unauthorized.parent.mkdir(parents=True)
    unauthorized.write_bytes(b"unapproved")

    issues = scan_sensitive_content(
        root,
        authorized_paths={*allowed, source.relative_to(root).as_posix()},
        metadata_filename="submission-metadata.local.json",
    )

    assert {issue["category"] for issue in issues} == {
        "secret",
        "localMetadata",
        "personalAbsolutePath",
        "unauthorizedMaterial",
    }
    assert "受控申请人甲" not in json.dumps(issues, ensure_ascii=False)
    assert "sk-controlled-secret" not in json.dumps(issues, ensure_ascii=False)


def test_atomic_publish_failure_restores_the_existing_formal_directory(
    tmp_path: Path,
) -> None:
    staging = tmp_path / ".release.tmp"
    destination = tmp_path / "warehouse-patrol-3s"
    staging.mkdir()
    destination.mkdir()
    (staging / "new.txt").write_text("new", encoding="utf-8")
    (destination / "old.txt").write_text("old", encoding="utf-8")
    real_replace = os.replace
    calls = 0

    def fail_second_replace(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("controlled publish failure")
        real_replace(source, target)

    with pytest.raises(OSError, match="controlled publish failure"):
        atomic_publish_directory(staging, destination, replace=fail_second_replace)

    assert (destination / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (destination / "new.txt").exists()


def test_release_layout_and_hashes_cover_every_formal_file_except_the_hash_list(
    tmp_path: Path,
) -> None:
    root = tmp_path / "warehouse-patrol-3s"
    create_release_layout(root)
    assert {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_dir()
    } == set(RELEASE_DIRECTORIES)
    (root / "01-application/application.pdf").write_bytes(b"%PDF-controlled")
    (root / "03-software/windows-x64/WarehousePatrol.exe").write_bytes(b"MZ-controlled")
    (root / "release-manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "gates": {
                    name: True
                    for name in (
                        "externalProcedures",
                        "software",
                        "evidence",
                        "documents",
                        "recording",
                        "hashes",
                    )
                },
                "formalSubmissionReady": True,
            }
        ),
        encoding="utf-8",
    )

    entries = write_release_hashes(root)

    assert set(entries) == {
        "01-application/application.pdf",
        "03-software/windows-x64/WarehousePatrol.exe",
        "release-manifest.json",
    }
    assert verify_release_hashes(root) == []
    sums = (root / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert "SHA256SUMS.txt" not in sums

    independent = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.competition.release",
            "verify-hashes",
            "--release-dir",
            str(root),
        ],
        cwd=REPOSITORY_ROOT,
        env=_utf8_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert independent.returncode == 0

    (root / "01-application/application.pdf").write_bytes(b"%PDF-changed")
    assert verify_release_hashes(root)
    changed = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.competition.release",
            "verify-hashes",
            "--release-dir",
            str(root),
        ],
        cwd=REPOSITORY_ROOT,
        env=_utf8_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert changed.returncode == 1


def test_hash_writer_refuses_a_manifest_that_claims_readiness_without_six_true_gates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "release"
    create_release_layout(root)
    (root / "release-manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "gates": {
                    "externalProcedures": True,
                    "software": True,
                    "evidence": True,
                    "documents": True,
                    "recording": False,
                    "hashes": True,
                },
                "formalSubmissionReady": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseGateError, match="六个门"):
        write_release_hashes(root)


def test_missing_task6_recording_writes_an_explicit_six_gate_failure_state(
    tmp_path: Path,
) -> None:
    gate = recording_gate(None, None)
    gates = {
        "externalProcedures": {"passed": True, "reasonCode": "verified"},
        "software": {"passed": True, "reasonCode": "verified"},
        "evidence": {"passed": True, "reasonCode": "verified"},
        "documents": {"passed": False, "reasonCode": "visualApprovalMissing"},
        "recording": gate,
        "hashes": {"passed": False, "reasonCode": "notGenerated"},
    }
    diagnostics = tmp_path / "release-gates.json"

    write_release_gate_diagnostics(diagnostics, gates)

    payload = json.loads(diagnostics.read_text(encoding="utf-8"))
    assert payload["gates"]["recording"] == {
        "passed": False,
        "reasonCode": "task6RecordingMissing",
    }
    assert payload["formalSubmissionReady"] is False
    assert set(payload["gates"]) == {
        "externalProcedures",
        "software",
        "evidence",
        "documents",
        "recording",
        "hashes",
    }


def test_validated_publisher_builds_exact_structure_scans_then_hashes_and_publishes(
    tmp_path: Path,
) -> None:
    inputs_root = tmp_path / "inputs"
    documents = inputs_root / "documents"
    evidence = inputs_root / "evidence"
    software = inputs_root / "software"
    documents.mkdir(parents=True)
    evidence.mkdir(parents=True)
    software.mkdir(parents=True)
    for document_id in (
        "application-summary",
        "technical-report",
        "run-guide",
        "third-party-dependencies",
    ):
        (documents / f"{document_id}.docx").write_bytes(b"controlled-docx")
        (documents / f"{document_id}.pdf").write_bytes(b"%PDF-controlled")
    (documents / "technical-report.md").write_text("private source", encoding="utf-8")
    (documents / "markdown-manifest.json").write_text("{}", encoding="utf-8")
    (documents / "conversion-instructions.md").write_text("private", encoding="utf-8")
    (evidence / "results.json").write_text("{}", encoding="utf-8")
    (evidence / "runs.csv").write_text("caseId\n", encoding="utf-8")
    (evidence / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\ncontrolled")
    (software / "WarehousePatrol.exe").write_bytes(b"MZ-controlled")
    (software / "runtime.dll").write_bytes(b"controlled")
    source_zip = inputs_root / "WarehousePatrol-source.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr("README.md", "controlled source")
    video = inputs_root / "warehouse-patrol-demo.mp4"
    video.write_bytes(b"controlled-video")
    recording_manifest = inputs_root / "recording-manifest.json"
    recording_manifest.write_text("{}", encoding="utf-8")
    destination = tmp_path / "submission/warehouse-patrol-3s"
    inputs = ReleaseInputs(
        documents_directory=documents,
        evidence_directory=evidence,
        software_directory=software,
        source_archive=source_zip,
        video_path=video,
        recording_manifest=recording_manifest,
        metadata_filename="submission-metadata.local.json",
    )

    assemble_validated_release(inputs, destination)

    assert {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_dir()} == set(RELEASE_DIRECTORIES)
    manifest = json.loads((destination / "release-manifest.json").read_text(encoding="utf-8"))
    assert manifest["gates"] == {
        "externalProcedures": True,
        "software": True,
        "evidence": True,
        "documents": True,
        "recording": True,
        "hashes": True,
    }
    assert manifest["formalSubmissionReady"] is True
    assert verify_release_hashes(destination) == []
    assert (destination / "03-software/windows-x64/WarehousePatrol.exe").is_file()
    assert (destination / "04-source/WarehousePatrol-source.zip").is_file()
    listing = {path.relative_to(destination).as_posix() for path in destination.rglob("*")}
    assert not any(path.endswith(".md") for path in listing)
    assert "markdown-manifest.json" not in listing
    assert "conversion-instructions.md" not in listing
    assert not any("competition/3s/local" in path for path in listing)
