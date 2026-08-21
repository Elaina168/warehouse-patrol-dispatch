from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from docx import Document
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

import backend.competition.converted_documents as converted_module
from backend.competition.converted_documents import (
    extract_docx_text,
    extract_pdf_text,
    ingest_converted_documents,
    validate_converted_content,
    validate_converted_file_set,
    verify_visual_approvals,
)
from backend.competition.document_support import DocumentGenerationError
from backend.competition.markdown_documents import (
    DOCUMENT_REQUIRED_PHRASES,
    DOCUMENT_TITLES,
)
from backend.competition.submission import PROJECT_NAME


EXPECTED_CONVERTED = {
    "application-summary.docx",
    "application-summary.pdf",
    "technical-report.docx",
    "technical-report.pdf",
    "run-guide.docx",
    "run-guide.pdf",
    "third-party-dependencies.docx",
    "third-party-dependencies.pdf",
}


def _write_file_set(path: Path) -> Path:
    path.mkdir(parents=True)
    for name in EXPECTED_CONVERTED:
        (path / name).write_bytes(b"controlled")
    return path


def _document_lines(document_id: str) -> list[str]:
    return [
        PROJECT_NAME,
        DOCUMENT_TITLES[document_id],
        *DOCUMENT_REQUIRED_PHRASES[document_id],
        "受控测试申请人甲",
        "受控测试指导人乙",
    ]


def _write_docx(path: Path, lines: list[str]) -> None:
    document = Document()
    for line in lines:
        document.add_paragraph(line)
    document.save(path)


def _write_pdf(path: Path, lines: list[str]) -> None:
    font_name = "ControlledSimHei"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, r"C:\Windows\Fonts\simhei.ttf"))
    page = canvas.Canvas(str(path))
    page.setFont(font_name, 9)
    y = 810
    for line in lines:
        page.drawString(20, y, line)
        y -= 14
        if y < 30:
            page.showPage()
            page.setFont(font_name, 9)
            y = 810
    page.save()


def _write_valid_converted_set(path: Path) -> Path:
    path.mkdir(parents=True)
    for document_id in (
        "application-summary",
        "technical-report",
        "run-guide",
        "third-party-dependencies",
    ):
        lines = _document_lines(document_id)
        _write_docx(path / f"{document_id}.docx", lines)
        _write_pdf(path / f"{document_id}.pdf", lines)
    return path


def _write_markdown_handoff(path: Path) -> Path:
    markdown = path / "markdown"
    markdown.mkdir(parents=True)
    documents: dict[str, dict[str, str]] = {}
    for document_id in (
        "application-summary",
        "technical-report",
        "run-guide",
        "third-party-dependencies",
    ):
        source = markdown / f"{document_id}.md"
        source.write_text("\n".join(_document_lines(document_id)) + "\n", encoding="utf-8")
        documents[document_id] = {
            "path": source.relative_to(path).as_posix(),
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    manifest = {
        "schemaVersion": 1,
        "status": "awaitingUserConversion",
        "projectName": PROJECT_NAME,
        "generatedAtUtc": "2026-08-21T10:00:00Z",
        "evidenceId": "20260821T041241Z",
        "evidenceManifestSha256": "a" * 64,
        "documents": documents,
    }
    (path / "markdown-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (path / "conversion-instructions.md").write_text("controlled\n", encoding="utf-8")
    return path


def _fake_poppler(
    monkeypatch: pytest.MonkeyPatch,
    *,
    page_count: int = 1,
    render_failure_document: str | None = None,
) -> tuple[Path, Path]:
    tools = Path(os.environ["PYTEST_TMPDIR"]) / "fake-poppler"
    tools.mkdir(parents=True, exist_ok=True)
    pdftoppm = tools / "pdftoppm.exe"
    pdfinfo = tools / "pdfinfo.exe"
    pdftoppm.write_bytes(b"fake")
    pdfinfo.write_bytes(b"fake")

    def run(
        command: list[str],
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        executable = Path(command[0]).name
        if executable == "pdfinfo.exe":
            return subprocess.CompletedProcess(command, 0, f"Pages: {page_count}\n", "")
        pdf_path = Path(command[-2])
        if render_failure_document and pdf_path.stem == render_failure_document:
            return subprocess.CompletedProcess(command, 1, "", "controlled failure")
        prefix = Path(command[-1])
        prefix.parent.mkdir(parents=True, exist_ok=True)
        (prefix.parent / f"{prefix.name}-1.png").write_bytes(
            b"\x89PNG\r\n\x1a\ncontrolled-page"
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(converted_module.subprocess, "run", run)
    return pdftoppm, pdfinfo


def _write_approvals(output: Path) -> Path:
    request_path = output / "visual-review-request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    pages = [
        {
            "documentId": page["documentId"],
            "pageNumber": page["pageNumber"],
            "imageSha256": page["imageSha256"],
            "approved": True,
        }
        for document in request["documents"].values()
        for page in document["pages"]
    ]
    approvals = output / "visual-approvals.json"
    approvals.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "requestSha256": hashlib.sha256(request_path.read_bytes()).hexdigest(),
                "pages": pages,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return approvals


@pytest.mark.parametrize("mutation", ["missing", "extra", "empty", "symlink"])
def test_converted_directory_requires_exact_safe_file_set(
    tmp_path: Path,
    mutation: str,
) -> None:
    converted = _write_file_set(tmp_path / "converted")
    target = converted / "application-summary.pdf"
    if mutation == "missing":
        target.unlink()
    elif mutation == "extra":
        (converted / "notes.txt").write_text("extra", encoding="utf-8")
    elif mutation == "empty":
        target.write_bytes(b"")
    else:
        target.unlink()
        source = tmp_path / "outside.pdf"
        source.write_bytes(b"controlled")
        try:
            os.symlink(source, target)
        except OSError as exc:
            pytest.skip(f"当前 Windows 环境不能创建符号链接：{exc}")

    with pytest.raises(DocumentGenerationError):
        validate_converted_file_set(converted)


def test_converted_directory_returns_four_pairs_in_fixed_order(tmp_path: Path) -> None:
    converted = _write_file_set(tmp_path / "converted")

    pairs = validate_converted_file_set(converted)

    assert [pair.document_id for pair in pairs] == [
        "application-summary",
        "technical-report",
        "run-guide",
        "third-party-dependencies",
    ]
    assert {pair.docx_path.name for pair in pairs} | {
        pair.pdf_path.name for pair in pairs
    } == EXPECTED_CONVERTED


def test_converted_content_accepts_valid_searchable_docx_and_pdf(tmp_path: Path) -> None:
    converted = _write_valid_converted_set(tmp_path / "converted")

    for pair in validate_converted_file_set(converted):
        validate_converted_content(pair)
        assert PROJECT_NAME in extract_docx_text(pair.docx_path)
        assert PROJECT_NAME in extract_pdf_text(pair.pdf_path)


@pytest.mark.parametrize("format_name", ["docx", "pdf"])
@pytest.mark.parametrize("missing", ["project", "title", "boundary"])
def test_converted_content_rejects_missing_required_text_without_private_values(
    tmp_path: Path,
    format_name: str,
    missing: str,
) -> None:
    converted = _write_valid_converted_set(tmp_path / "converted")
    document_id = "technical-report"
    lines = _document_lines(document_id)
    removed = {
        "project": PROJECT_NAME,
        "title": DOCUMENT_TITLES[document_id],
        "boundary": "不提供完整 MAPF 保证",
    }[missing]
    lines = [line for line in lines if line != removed]
    target = converted / f"{document_id}.{format_name}"
    if format_name == "docx":
        _write_docx(target, lines)
    else:
        _write_pdf(target, lines)

    pair = next(
        item
        for item in validate_converted_file_set(converted)
        if item.document_id == document_id
    )
    with pytest.raises(DocumentGenerationError) as captured:
        validate_converted_content(pair)

    message = str(captured.value)
    assert f"documentId={document_id}" in message
    assert "reasonCode=" in message
    assert "受控测试申请人甲" not in message
    assert "受控测试指导人乙" not in message


@pytest.mark.parametrize("format_name", ["docx", "pdf"])
def test_converted_content_rejects_corrupt_packages(
    tmp_path: Path,
    format_name: str,
) -> None:
    converted = _write_valid_converted_set(tmp_path / "converted")
    target = converted / f"run-guide.{format_name}"
    target.write_bytes(b"not-a-valid-document")
    pair = next(
        item
        for item in validate_converted_file_set(converted)
        if item.document_id == "run-guide"
    )

    with pytest.raises(DocumentGenerationError, match="documentId=run-guide"):
        validate_converted_content(pair)


def test_ingest_renders_all_documents_and_approval_binds_current_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_TMPDIR", str(tmp_path))
    output = _write_markdown_handoff(tmp_path / "documents")
    converted = _write_valid_converted_set(tmp_path / "converted")
    pdftoppm, pdfinfo = _fake_poppler(monkeypatch)

    result = ingest_converted_documents(
        markdown_output_directory=output,
        converted_directory=converted,
        pdftoppm=pdftoppm,
        pdfinfo=pdfinfo,
    )

    assert result == output
    request_path = output / "visual-review-request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["status"] == "awaitingVisualApproval"
    assert set(request["documents"]) == {
        "application-summary",
        "technical-report",
        "run-guide",
        "third-party-dependencies",
    }
    assert request["markdownManifestSha256"] == hashlib.sha256(
        (output / "markdown-manifest.json").read_bytes()
    ).hexdigest()
    pages = [page for item in request["documents"].values() for page in item["pages"]]
    assert len(pages) == 4
    assert all(page["imagePath"].endswith("page-001.png") for page in pages)
    for document_id, item in request["documents"].items():
        assert item["docxSha256"] == hashlib.sha256(
            (output / f"{document_id}.docx").read_bytes()
        ).hexdigest()
        assert item["pdfSha256"] == hashlib.sha256(
            (output / f"{document_id}.pdf").read_bytes()
        ).hexdigest()
    approvals = _write_approvals(output)
    assert verify_visual_approvals(output, approvals) is True


@pytest.mark.parametrize("changed", ["markdown", "docx", "pdf", "page", "request"])
def test_visual_approval_becomes_stale_when_any_bound_input_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed: str,
) -> None:
    monkeypatch.setenv("PYTEST_TMPDIR", str(tmp_path))
    output = _write_markdown_handoff(tmp_path / "documents")
    converted = _write_valid_converted_set(tmp_path / "converted")
    pdftoppm, pdfinfo = _fake_poppler(monkeypatch)
    ingest_converted_documents(
        markdown_output_directory=output,
        converted_directory=converted,
        pdftoppm=pdftoppm,
        pdfinfo=pdfinfo,
    )
    approvals = _write_approvals(output)
    target = {
        "markdown": output / "markdown/technical-report.md",
        "docx": output / "application-summary.docx",
        "pdf": output / "application-summary.pdf",
        "page": output / "rendered/application-summary/page-001.png",
        "request": output / "visual-review-request.json",
    }[changed]
    target.write_bytes(target.read_bytes() + b"changed")

    assert verify_visual_approvals(output, approvals) is False


def test_visual_approval_requires_every_current_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_TMPDIR", str(tmp_path))
    output = _write_markdown_handoff(tmp_path / "documents")
    converted = _write_valid_converted_set(tmp_path / "converted")
    pdftoppm, pdfinfo = _fake_poppler(monkeypatch)
    ingest_converted_documents(
        markdown_output_directory=output,
        converted_directory=converted,
        pdftoppm=pdftoppm,
        pdfinfo=pdfinfo,
    )
    approvals = _write_approvals(output)
    payload = json.loads(approvals.read_text(encoding="utf-8"))
    payload["pages"].pop()
    approvals.write_text(json.dumps(payload), encoding="utf-8")

    assert verify_visual_approvals(output, approvals) is False


@pytest.mark.parametrize("failure", ["render", "page-count"])
def test_ingest_failure_rolls_back_existing_markdown_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setenv("PYTEST_TMPDIR", str(tmp_path))
    output = _write_markdown_handoff(tmp_path / "documents")
    before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    converted = _write_valid_converted_set(tmp_path / "converted")
    pdftoppm, pdfinfo = _fake_poppler(
        monkeypatch,
        page_count=2 if failure == "page-count" else 1,
        render_failure_document="technical-report" if failure == "render" else None,
    )

    with pytest.raises(DocumentGenerationError):
        ingest_converted_documents(
            markdown_output_directory=output,
            converted_directory=converted,
            pdftoppm=pdftoppm,
            pdfinfo=pdfinfo,
        )

    after = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not list(tmp_path.glob(".documents.*.tmp"))
