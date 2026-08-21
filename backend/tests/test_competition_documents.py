from __future__ import annotations

from pathlib import Path

import pytest

import backend.competition.documents as document_module
from backend.competition.documents import main
from backend.tests.test_competition_markdown_documents import (
    _write_controlled_evidence,
    _write_metadata,
)


pytest_plugins = ("backend.tests.competition_fixtures",)


def test_documents_command_without_converted_dir_generates_markdown_only(
    repository_tmp_path: Path,
) -> None:
    metadata = _write_metadata(repository_tmp_path / "metadata.json")
    evidence = _write_controlled_evidence(repository_tmp_path / "evidence")
    output = repository_tmp_path / "documents"

    result = main(
        [
            "--metadata",
            str(metadata),
            "--evidence-dir",
            str(evidence),
            "--output-dir",
            str(output),
        ]
    )

    assert result == 0
    assert {path.name for path in (output / "markdown").iterdir()} == {
        "application-summary.md",
        "technical-report.md",
        "run-guide.md",
        "third-party-dependencies.md",
    }
    assert not list(output.glob("*.docx"))
    assert not list(output.glob("*.pdf"))
    assert not (output / "visual-review-request.json").exists()


def test_documents_command_with_converted_dir_ingests_and_renders(
    repository_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _write_metadata(repository_tmp_path / "metadata.json")
    evidence = _write_controlled_evidence(repository_tmp_path / "evidence")
    converted = repository_tmp_path / "converted"
    converted.mkdir()
    output = repository_tmp_path / "documents"
    pdftoppm = repository_tmp_path / "pdftoppm.exe"
    pdfinfo = repository_tmp_path / "pdfinfo.exe"
    pdftoppm.write_bytes(b"fake")
    pdfinfo.write_bytes(b"fake")
    calls: list[dict[str, Path]] = []

    def ingest(**arguments: Path) -> Path:
        calls.append(arguments)
        (output / "rendered/application-summary").mkdir(parents=True)
        for document_id in (
            "application-summary",
            "technical-report",
            "run-guide",
            "third-party-dependencies",
        ):
            (output / f"{document_id}.docx").write_bytes(b"controlled")
            (output / f"{document_id}.pdf").write_bytes(b"%PDF-controlled")
            page_directory = output / "rendered" / document_id
            page_directory.mkdir(parents=True, exist_ok=True)
            (page_directory / "page-001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (output / "visual-review-request.json").write_text("{}", encoding="utf-8")
        return output

    monkeypatch.setattr(document_module, "ingest_converted_documents", ingest)

    result = main(
        [
            "--metadata",
            str(metadata),
            "--evidence-dir",
            str(evidence),
            "--output-dir",
            str(output),
            "--converted-dir",
            str(converted),
            "--pdftoppm",
            str(pdftoppm),
            "--pdfinfo",
            str(pdfinfo),
        ]
    )

    assert result == 0
    assert len(list(output.glob("*.docx"))) == 4
    assert len(list(output.glob("*.pdf"))) == 4
    assert len(list((output / "rendered").glob("*/page-001.png"))) == 4
    assert (output / "visual-review-request.json").is_file()
    assert calls == [
        {
            "markdown_output_directory": output,
            "converted_directory": converted,
            "pdftoppm": pdftoppm,
            "pdfinfo": pdfinfo,
        }
    ]


def test_documents_verify_only_requires_current_visual_approval(
    repository_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _write_metadata(repository_tmp_path / "metadata.json")
    output = repository_tmp_path / "documents"
    output.mkdir()
    monkeypatch.setattr(document_module, "_verify_existing_documents", lambda *_: False)
    assert main(
        [
            "--metadata",
            str(metadata),
            "--output-dir",
            str(output),
            "--verify-only",
        ]
    ) == 1
    approvals = repository_tmp_path / "visual-approvals.json"
    approvals.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(document_module, "_verify_existing_documents", lambda *_: True)

    assert main(
        [
            "--metadata",
            str(metadata),
            "--output-dir",
            str(output),
            "--verify-only",
            "--approvals",
            str(approvals),
        ]
    ) == 0


def test_documents_command_rejects_verify_only_with_converted_dir(
    repository_tmp_path: Path,
) -> None:
    metadata = _write_metadata(repository_tmp_path / "metadata.json")

    with pytest.raises(SystemExit):
        main(
            [
                "--metadata",
                str(metadata),
                "--verify-only",
                "--converted-dir",
                str(repository_tmp_path / "converted"),
            ]
        )
