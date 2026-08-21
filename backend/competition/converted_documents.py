"""接收、验证并渲染用户人工转换的 DOCX/PDF 材料。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile, is_zipfile

from backend.competition.document_support import (
    DocumentGenerationError,
    publish_directory_atomically,
    sha256_file,
)
from backend.competition.markdown_documents import (
    DOCUMENT_IDS,
    DOCUMENT_REQUIRED_PHRASES,
    DOCUMENT_TITLES,
)
from backend.competition.submission import PROJECT_NAME


@dataclass(frozen=True, slots=True)
class ConvertedDocument:
    """同一文档标识对应的一组人工转换文件。"""

    document_id: str
    docx_path: Path
    pdf_path: Path


def validate_converted_file_set(
    converted_directory: Path,
) -> tuple[ConvertedDocument, ...]:
    """按固定顺序返回四组安全且非空的 DOCX/PDF。"""

    if converted_directory.is_symlink() or not converted_directory.is_dir():
        raise DocumentGenerationError("convertedDirectoryInvalid")
    expected = {
        f"{document_id}{extension}"
        for document_id in DOCUMENT_IDS
        for extension in (".docx", ".pdf")
    }
    entries = list(converted_directory.iterdir())
    if {entry.name for entry in entries} != expected:
        raise DocumentGenerationError("convertedFileSetMismatch")
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise DocumentGenerationError("convertedFileUnsafe")
        if entry.stat().st_size == 0:
            raise DocumentGenerationError("convertedFileEmpty")
    return tuple(
        ConvertedDocument(
            document_id=document_id,
            docx_path=converted_directory / f"{document_id}.docx",
            pdf_path=converted_directory / f"{document_id}.pdf",
        )
        for document_id in DOCUMENT_IDS
    )


def extract_docx_text(path: Path) -> str:
    """从未修改的 OOXML 包中提取正文文本。"""

    try:
        if not is_zipfile(path):
            raise ValueError("invalidDocx")
        with ZipFile(path, "r") as archive:
            if archive.testzip() is not None:
                raise ValueError("invalidDocx")
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ValueError("invalidDocx")
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        values = [
            node.text or ""
            for node in root.iter()
            if node.tag.endswith("}t")
        ]
        return "\n".join(value for value in values if value)
    except (BadZipFile, ElementTree.ParseError, KeyError, OSError) as exc:
        raise ValueError("invalidDocx") from exc


def extract_pdf_text(path: Path) -> str:
    """从每一页 PDF 中提取可搜索文本。"""

    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) < 1:
            raise ValueError("invalidPdf")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("invalidPdf") from exc


def _content_failure(document_id: str, reason_code: str) -> DocumentGenerationError:
    return DocumentGenerationError(
        f"documentId={document_id};reasonCode={reason_code}"
    )


def validate_converted_content(document: ConvertedDocument) -> None:
    """验证人工转换文件中的固定标题、章节与能力边界。"""

    try:
        docx_text = extract_docx_text(document.docx_path)
    except ValueError as exc:
        raise _content_failure(document.document_id, "docxInvalid") from exc
    try:
        pdf_text = extract_pdf_text(document.pdf_path)
    except ValueError as exc:
        raise _content_failure(document.document_id, "pdfInvalid") from exc
    required = (
        DOCUMENT_TITLES[document.document_id],
        *DOCUMENT_REQUIRED_PHRASES[document.document_id],
    )
    if any(phrase not in docx_text for phrase in required):
        raise _content_failure(document.document_id, "docxRequiredTextMissing")
    if not pdf_text.strip() or any(phrase not in pdf_text for phrase in required):
        raise _content_failure(document.document_id, "pdfRequiredTextMissing")


def _load_markdown_manifest(output_directory: Path) -> dict[str, object]:
    manifest_path = output_directory / "markdown-manifest.json"
    markdown_directory = output_directory / "markdown"
    if (
        output_directory.is_symlink()
        or not output_directory.is_dir()
        or markdown_directory.is_symlink()
        or not markdown_directory.is_dir()
        or not manifest_path.is_file()
        or manifest_path.is_symlink()
    ):
        raise DocumentGenerationError("markdownHandoffInvalid")
    if any(path.is_symlink() for path in output_directory.rglob("*")):
        raise DocumentGenerationError("markdownHandoffUnsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DocumentGenerationError("markdownManifestInvalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schemaVersion") != 1
        or manifest.get("status") != "awaitingUserConversion"
        or manifest.get("projectName") != PROJECT_NAME
    ):
        raise DocumentGenerationError("markdownManifestInvalid")
    records = manifest.get("documents")
    if not isinstance(records, dict) or set(records) != set(DOCUMENT_IDS):
        raise DocumentGenerationError("markdownManifestDocumentSetMismatch")
    expected_names = {f"{document_id}.md" for document_id in DOCUMENT_IDS}
    if {path.name for path in markdown_directory.iterdir()} != expected_names:
        raise DocumentGenerationError("markdownFileSetMismatch")
    for document_id in DOCUMENT_IDS:
        record = records.get(document_id)
        expected_path = f"markdown/{document_id}.md"
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "sha256"}
            or record.get("path") != expected_path
        ):
            raise DocumentGenerationError("markdownManifestRecordInvalid")
        source = output_directory / expected_path
        if (
            source.is_symlink()
            or not source.is_file()
            or record.get("sha256") != sha256_file(source)
        ):
            raise DocumentGenerationError("markdownHashMismatch")
    return manifest


def _render_pdf_pages(
    document_id: str,
    pdf_path: Path,
    output_directory: Path,
    pdftoppm: Path,
    pdfinfo: Path,
) -> list[dict[str, object]]:
    if not pdftoppm.is_file() or not pdfinfo.is_file():
        raise _content_failure(document_id, "popplerExecutableMissing")
    info = subprocess.run(
        [str(pdfinfo), str(pdf_path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if info.returncode != 0:
        raise _content_failure(document_id, "pdfinfoFailed")
    match = re.search(r"(?mi)^Pages:\s*(\d+)\s*$", info.stdout)
    if match is None or int(match.group(1)) < 1:
        raise _content_failure(document_id, "pdfPageCountInvalid")
    page_count = int(match.group(1))
    page_directory = output_directory / "rendered" / document_id
    page_directory.mkdir(parents=True, exist_ok=True)
    prefix = page_directory / "page"
    rendered = subprocess.run(
        [str(pdftoppm), "-png", "-r", "144", str(pdf_path), str(prefix)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if rendered.returncode != 0:
        raise _content_failure(document_id, "pdftoppmFailed")
    pages: list[dict[str, object]] = []
    expected_names: set[str] = set()
    for page_number in range(1, page_count + 1):
        source = page_directory / f"page-{page_number}.png"
        target = page_directory / f"page-{page_number:03d}.png"
        if not source.is_file():
            raise _content_failure(document_id, "renderedPageMissing")
        source.replace(target)
        expected_names.add(target.name)
        pages.append(
            {
                "documentId": document_id,
                "pageNumber": page_number,
                "imagePath": target.relative_to(output_directory).as_posix(),
                "imageSha256": sha256_file(target),
                "status": "pending",
            }
        )
    if {path.name for path in page_directory.glob("page-*.png")} != expected_names:
        raise _content_failure(document_id, "renderedPageCountMismatch")
    return pages


def ingest_converted_documents(
    *,
    markdown_output_directory: Path,
    converted_directory: Path,
    pdftoppm: Path,
    pdfinfo: Path,
) -> Path:
    """验证、复制、渲染并原子发布人工转换文件。"""

    markdown_output_directory = markdown_output_directory.resolve()
    _load_markdown_manifest(markdown_output_directory)
    converted = validate_converted_file_set(converted_directory)
    for document in converted:
        validate_converted_content(document)

    staging = markdown_output_directory.with_name(
        f".{markdown_output_directory.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        shutil.copytree(markdown_output_directory, staging)
        request_documents: dict[str, dict[str, object]] = {}
        file_hashes: dict[str, str] = {}
        for document in converted:
            docx_target = staging / document.docx_path.name
            pdf_target = staging / document.pdf_path.name
            shutil.copy2(document.docx_path, docx_target)
            shutil.copy2(document.pdf_path, pdf_target)
            pages = _render_pdf_pages(
                document.document_id,
                pdf_target,
                staging,
                pdftoppm,
                pdfinfo,
            )
            docx_hash = sha256_file(docx_target)
            pdf_hash = sha256_file(pdf_target)
            file_hashes[docx_target.name] = docx_hash
            file_hashes[pdf_target.name] = pdf_hash
            request_documents[document.document_id] = {
                "docxSha256": docx_hash,
                "pdfSha256": pdf_hash,
                "pages": pages,
            }
        markdown_manifest_hash = sha256_file(staging / "markdown-manifest.json")
        request = {
            "schemaVersion": 1,
            "status": "awaitingVisualApproval",
            "markdownManifestSha256": markdown_manifest_hash,
            "documents": request_documents,
        }
        request_path = staging / "visual-review-request.json"
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        document_manifest = {
            "schemaVersion": 1,
            "status": "awaitingVisualApproval",
            "projectName": PROJECT_NAME,
            "markdownManifestSha256": markdown_manifest_hash,
            "files": dict(sorted(file_hashes.items())),
            "visualApproved": False,
        }
        (staging / "document-manifest.json").write_text(
            json.dumps(document_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        publish_directory_atomically(staging, markdown_output_directory)
        return markdown_output_directory
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_visual_approvals(
    output_directory: Path,
    approvals_path: Path | None,
) -> bool:
    """验证批准文件是否绑定当前全部内容与页图。"""

    if approvals_path is None or not approvals_path.is_file():
        return False
    request_path = output_directory / "visual-review-request.json"
    if not request_path.is_file():
        return False
    try:
        _load_markdown_manifest(output_directory)
        request = json.loads(request_path.read_text(encoding="utf-8"))
        approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
    except (DocumentGenerationError, OSError, UnicodeError, json.JSONDecodeError):
        return False
    if (
        not isinstance(request, dict)
        or request.get("schemaVersion") != 1
        or request.get("status") != "awaitingVisualApproval"
        or request.get("markdownManifestSha256")
        != sha256_file(output_directory / "markdown-manifest.json")
    ):
        return False
    if (
        not isinstance(approvals, dict)
        or set(approvals) != {"schemaVersion", "requestSha256", "pages"}
        or approvals.get("schemaVersion") != 1
        or approvals.get("requestSha256") != sha256_file(request_path)
    ):
        return False
    request_documents = request.get("documents")
    if not isinstance(request_documents, dict) or set(request_documents) != set(
        DOCUMENT_IDS
    ):
        return False
    expected: dict[tuple[str, int], str] = {}
    for document_id in DOCUMENT_IDS:
        record = request_documents.get(document_id)
        if not isinstance(record, dict) or set(record) != {
            "docxSha256",
            "pdfSha256",
            "pages",
        }:
            return False
        docx_path = output_directory / f"{document_id}.docx"
        pdf_path = output_directory / f"{document_id}.pdf"
        if (
            not docx_path.is_file()
            or not pdf_path.is_file()
            or record.get("docxSha256") != sha256_file(docx_path)
            or record.get("pdfSha256") != sha256_file(pdf_path)
        ):
            return False
        pages = record.get("pages")
        if not isinstance(pages, list) or not pages:
            return False
        for page in pages:
            if not isinstance(page, dict) or set(page) != {
                "documentId",
                "pageNumber",
                "imagePath",
                "imageSha256",
                "status",
            }:
                return False
            key = (page.get("documentId"), page.get("pageNumber"))
            image_path = page.get("imagePath")
            digest = page.get("imageSha256")
            if (
                key[0] != document_id
                or not isinstance(key[1], int)
                or key in expected
                or not isinstance(image_path, str)
                or not isinstance(digest, str)
                or page.get("status") != "pending"
            ):
                return False
            image = output_directory / image_path
            if not image.is_file() or sha256_file(image) != digest:
                return False
            expected[key] = digest
    approval_pages = approvals.get("pages")
    if not isinstance(approval_pages, list):
        return False
    actual: dict[tuple[str, int], str] = {}
    for approval in approval_pages:
        if not isinstance(approval, dict) or set(approval) != {
            "documentId",
            "pageNumber",
            "imageSha256",
            "approved",
        }:
            return False
        key = (approval.get("documentId"), approval.get("pageNumber"))
        digest = approval.get("imageSha256")
        if (
            not isinstance(key[0], str)
            or not isinstance(key[1], int)
            or not isinstance(digest, str)
            or approval.get("approved") is not True
            or key in actual
        ):
            return False
        actual[key] = digest
    return actual == expected
