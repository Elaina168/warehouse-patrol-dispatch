from __future__ import annotations

import importlib.util
import io
import json
import hashlib
import os
import re
import struct
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

import backend.competition.documents as document_module
from backend.competition.documents import (
    DOCUMENT_IDS,
    DocumentGenerationError,
    WordPdfExporter,
    audit_standard_business_brief,
    generate_documents,
    verify_visual_approvals,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_PWSH = REPOSITORY_ROOT / ".tools/powershell/pwsh.exe"


def _anonymous_verified_metadata() -> dict[str, object]:
    payload = json.loads(
        (REPOSITORY_ROOT / "competition/3s/submission-metadata.example.json").read_text(
            encoding="utf-8"
        )
    )

    def approve(value: object) -> None:
        if isinstance(value, dict):
            if "verified" in value:
                value["verified"] = True
            for child in value.values():
                approve(child)
        elif isinstance(value, list):
            for child in value:
                approve(child)

    approve(payload)
    payload["applicant"].update(
        {
            "name": "受控匿名申请人甲",
            "studentId": "CONTROLLED-0001",
            "phone": "13000000000",
            "email": "controlled@invalid.local",
            "school": "受控匿名学校",
            "major": "受控匿名专业",
        }
    )
    payload["advisors"] = [
        {
            "name": "受控匿名指导人乙",
            "role": "指导教师",
            "professionalTitle": "受控匿名职称",
        }
    ]
    return payload


def _write_controlled_evidence(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    results = {
        "schemaVersion": 1,
        "runs": [
            {
                "caseId": "integrated-demo-without-conflict-avoidance",
                "outcome": "completed",
                "accepted": True,
                "metrics": {"conflictCount": 4, "failureCount": 0},
            },
            {
                "caseId": "integrated-demo-with-conflict-avoidance",
                "outcome": "completed",
                "accepted": True,
                "metrics": {"conflictCount": 0, "failureCount": 0},
            },
            {
                "caseId": "main-demo-online",
                "outcome": "completed",
                "accepted": True,
                "metrics": {"completedTaskCount": 7, "failureCount": 0},
            },
            {
                "caseId": "seeded-pressure-r4-t18",
                "outcome": "completed",
                "accepted": True,
                "robotCount": 4,
                "taskCount": 20,
                "metrics": {"replanTimeMs": 9.5, "conflictCount": 0},
            },
            {
                "caseId": "safety-gate-boundary",
                "outcome": "completed",
                "accepted": True,
                "metrics": {
                    "collisionFree": True,
                    "safetyInterventionCount": 3,
                    "safetyStallCount": 3,
                },
            },
        ],
    }
    results_path = path / "results.json"
    results_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (path / "evidence-manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "accepted": True,
                "files": {
                    "results.json": hashlib.sha256(results_path.read_bytes()).hexdigest()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_word_pid_resolution_uses_owned_probe_window_when_application_hwnd_is_null(
    tmp_path: Path,
) -> None:
    harness = tmp_path / "word-pid-probe.ps1"
    harness.write_text(
        r'''param(
  [Parameter(Mandatory = $true)][string]$HelperPath
)

$ErrorActionPreference = "Stop"
. $HelperPath
Add-Type -AssemblyName System.Windows.Forms

$form = [System.Windows.Forms.Form]::new()
$null = $form.Handle
$script:probeCloseCount = 0
$window = [pscustomobject]@{ Hwnd = $form.Handle }
$script:probeDocument = [pscustomobject]@{ ActiveWindow = $window }
$script:probeDocument | Add-Member -MemberType ScriptMethod -Name Close -Value {
  param($saveChanges)
  $script:probeCloseCount += 1
}
$documents = [pscustomobject]@{}
$documents | Add-Member -MemberType ScriptMethod -Name Add -Value {
  return $script:probeDocument
}
$word = [pscustomobject]@{
  Hwnd = $null
  Documents = $documents
}

try {
  $resolvedPid = Get-IsolatedWordProcessId -WordApplication $word
  [pscustomobject]@{
    resolvedCurrentProcess = ($resolvedPid -eq $PID)
    probeCloseCount = $script:probeCloseCount
  } | ConvertTo-Json -Compress
} finally {
  $form.Dispose()
}
''',
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            str(PROJECT_PWSH),
            "-NoLogo",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-HelperPath",
            str(REPOSITORY_ROOT / "competition/word-process.ps1"),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    result = json.loads(completed.stdout.strip())
    assert result == {
        "resolvedCurrentProcess": True,
        "probeCloseCount": 1,
    }


def test_word_pdf_worker_reports_safe_failure_stage_without_raw_values(
    tmp_path: Path,
) -> None:
    source = tmp_path / "受控匿名申请人甲-private.docx"
    source.write_bytes(b"controlled input")
    destination = tmp_path / "受控匿名学校-existing.pdf"
    destination.write_bytes(b"existing output")
    pid_file = tmp_path / "word.pid"

    completed = subprocess.run(
        [
            str(PROJECT_PWSH),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(REPOSITORY_ROOT / "competition/docx-to-pdf-worker.ps1"),
            "-SourcePath",
            str(source),
            "-DestinationPath",
            str(destination),
            "-PidFile",
            str(pid_file),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr.strip().startswith(
        "WORD_PDF_DIAGNOSTIC stage=validateDestination "
        "exceptionType=System.Management.Automation.RuntimeException hresult=0x"
    )
    assert len(completed.stderr.strip().rsplit("0x", maxsplit=1)[1]) == 8
    assert str(source) not in completed.stderr
    assert str(destination) not in completed.stderr
    assert "受控匿名申请人甲" not in completed.stderr
    assert "受控匿名学校" not in completed.stderr
    assert destination.read_bytes() == b"existing output"
    assert not pid_file.exists()


def test_word_pdf_exporter_exposes_only_the_safe_worker_diagnostic(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.docx"
    source.write_bytes(b"controlled input")
    destination = tmp_path / "output.pdf"
    unsafe_detail = "D:\\Users\\Private\\受控匿名申请人甲.docx"

    def command_builder(
        source_path: Path,
        destination_path: Path,
        pid_path: Path,
    ) -> list[str]:
        del source_path, destination_path, pid_path
        script = (
            "import sys; "
            "sys.stderr.write("
            "'WORD_PDF_DIAGNOSTIC stage=openDocument "
            "exceptionType=System.UnauthorizedAccessException "
            "hresult=0x80070005\\n'"
            "+ sys.argv[1] + '\\n'); "
            "raise SystemExit(7)"
        )
        return [sys.executable, "-c", script, unsafe_detail]

    with pytest.raises(DocumentGenerationError) as raised:
        WordPdfExporter(command_builder=command_builder).export(source, destination)

    assert str(raised.value) == (
        "隔离 Word 未能导出 PDF"
        "（stage=openDocument; "
        "exceptionType=System.UnauthorizedAccessException; "
        "hresult=0x80070005）。"
    )
    assert unsafe_detail not in str(raised.value)
    assert not destination.exists()


def test_word_pdf_exporter_passes_absolute_paths_to_the_external_worker(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.docx"
    source.write_bytes(b"controlled input")
    destination = tmp_path / "output.pdf"
    relative_source = source.relative_to(REPOSITORY_ROOT)
    relative_destination = destination.relative_to(REPOSITORY_ROOT)
    observed: dict[str, Path] = {}

    def command_builder(
        source_path: Path,
        destination_path: Path,
        pid_path: Path,
    ) -> list[str]:
        observed.update(
            {
                "source": source_path,
                "destination": destination_path,
                "pid": pid_path,
            }
        )
        return [
            sys.executable,
            "-c",
            (
                "import pathlib,sys; "
                "pathlib.Path(sys.argv[1]).write_bytes(b'%PDF-controlled')"
            ),
            str(destination_path),
        ]

    WordPdfExporter(command_builder=command_builder).export(
        relative_source,
        relative_destination,
    )

    assert observed == {
        "source": source.resolve(),
        "destination": destination.resolve(),
        "pid": observed["pid"].resolve(),
    }
    assert observed["pid"].is_absolute()
    assert destination.read_bytes() == b"%PDF-controlled"


def test_document_chain_failure_identifies_only_the_safe_document_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = tmp_path / "submission-metadata.json"
    metadata.write_text(
        json.dumps(_anonymous_verified_metadata(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence"
    _write_controlled_evidence(evidence)

    class ControlledDocument:
        def save(self, path: Path) -> None:
            path.write_bytes(b"controlled-docx")

    class ControlledFailingExporter:
        def __init__(self, *, timeout_seconds: float) -> None:
            assert timeout_seconds == 60

        def export(self, source: Path, destination: Path) -> None:
            del source, destination
            raise DocumentGenerationError(
                "隔离 Word 未能导出 PDF"
                "（stage=openDocument; "
                "exceptionType=System.UnauthorizedAccessException; "
                "hresult=0x80070005）。"
            )

    for builder_name in (
        "_build_application",
        "_build_technical_report",
        "_build_run_guide",
        "_build_dependency_inventory",
    ):
        monkeypatch.setattr(
            document_module,
            builder_name,
            lambda *arguments: ControlledDocument(),
        )
    monkeypatch.setattr(
        document_module,
        "_scrub_docx_metadata",
        lambda path: None,
    )
    monkeypatch.setattr(
        document_module,
        "audit_standard_business_brief",
        lambda path: [],
    )
    monkeypatch.setattr(document_module, "WordPdfExporter", ControlledFailingExporter)

    with pytest.raises(DocumentGenerationError) as raised:
        generate_documents(
            metadata_path=metadata,
            evidence_directory=evidence,
            output_directory=tmp_path / "documents",
            pdftoppm=tmp_path / "pdftoppm.exe",
            pdfinfo=tmp_path / "pdfinfo.exe",
        )

    assert str(raised.value) == (
        "documentId=application-summary；"
        "隔离 Word 未能导出 PDF"
        "（stage=openDocument; "
        "exceptionType=System.UnauthorizedAccessException; "
        "hresult=0x80070005）。"
    )
    assert "受控匿名申请人甲" not in str(raised.value)
    assert not (tmp_path / "documents").exists()
    assert not list(tmp_path.glob(".documents.*.tmp"))


@pytest.fixture(scope="module")
def anonymous_documents(tmp_path_factory: pytest.TempPathFactory) -> Path:
    required_environment = (
        "WAREHOUSE_PATROL_DOCUMENTS_PYTHON",
        "WAREHOUSE_PATROL_PDFTOPPM",
        "WAREHOUSE_PATROL_PDFINFO",
    )
    missing = [name for name in required_environment if not os.environ.get(name)]
    if missing:
        pytest.skip("真实 Word/Poppler 文档集成测试需要显式本地工具路径。")
    documents_python = Path(os.environ["WAREHOUSE_PATROL_DOCUMENTS_PYTHON"])
    pdftoppm = Path(os.environ["WAREHOUSE_PATROL_PDFTOPPM"])
    pdfinfo = Path(os.environ["WAREHOUSE_PATROL_PDFINFO"])
    root = tmp_path_factory.mktemp("anonymous-documents")
    metadata = root / "submission-metadata.json"
    metadata.write_text(
        json.dumps(_anonymous_verified_metadata(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    evidence = root / "evidence"
    _write_controlled_evidence(evidence)
    output = root / "documents"
    environment = os.environ.copy()
    environment["WAREHOUSE_PATROL_PWSH"] = str(
        REPOSITORY_ROOT / ".tools/powershell/pwsh.exe"
    )
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    command = [
        str(documents_python),
        "-m",
        "backend.competition.documents",
        "--metadata",
        str(metadata),
        "--evidence-dir",
        str(evidence),
        "--output-dir",
        str(output),
        "--pdftoppm",
        str(pdftoppm),
        "--pdfinfo",
        str(pdfinfo),
        "--word-timeout-seconds",
        "60",
    ]

    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return output


def _docx_part(path: Path, part: str) -> str:
    with ZipFile(path) as archive:
        return archive.read(part).decode("utf-8")


def test_docx_metadata_scrub_keeps_ignorable_namespace_prefixes_declared(
    tmp_path: Path,
) -> None:
    if importlib.util.find_spec("lxml") is None:
        pytest.skip("文档 XML 保真回归需要锁定的 lxml 文档运行环境。")

    path = tmp_path / "controlled.docx"
    source_document = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
  xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
  xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
  xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
  mc:Ignorable="w14 wp14">
  <w:body><w:p w:rsidR="00112233"/></w:body>
</w:document>'''
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", source_document)

    source_root_tag = re.search(rb"<w:document\b[^>]*>", source_document, re.DOTALL)
    assert source_root_tag is not None
    assert set(re.findall(rb"\bxmlns:([A-Za-z_][A-Za-z0-9_.-]*)=", source_root_tag.group())) >= {
        b"w14",
        b"wp14",
    }

    document_module._scrub_docx_metadata(path)

    with ZipFile(path) as archive:
        scrubbed_document = archive.read("word/document.xml")
    root_tag = re.search(
        rb"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?document\b[^>]*>",
        scrubbed_document,
        re.DOTALL,
    )
    assert root_tag is not None
    declared_prefixes = {
        prefix.decode("ascii")
        for prefix in re.findall(
            rb"\bxmlns:([A-Za-z_][A-Za-z0-9_.-]*)=", root_tag.group()
        )
    }
    ignorable = re.search(
        rb"\b[A-Za-z_][A-Za-z0-9_.-]*:Ignorable=\"([^\"]*)\"",
        root_tag.group(),
    )
    ignorable_prefixes = (
        set() if ignorable is None else set(ignorable.group(1).decode("ascii").split())
    )

    assert ignorable_prefixes <= declared_prefixes, (
        f"mc:Ignorable 引用了未声明前缀: "
        f"{sorted(ignorable_prefixes - declared_prefixes)}"
    )


def test_docx_metadata_scrub_preserves_root_nsmap_and_non_rsid_expanded_tree(
    tmp_path: Path,
) -> None:
    if importlib.util.find_spec("lxml") is None:
        pytest.skip("文档 XML 保真回归需要锁定的 lxml 文档运行环境。")

    path = tmp_path / "controlled-fidelity.docx"
    word_namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    relationship_namespace = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    )
    compatibility_namespace = (
        "http://schemas.openxmlformats.org/markup-compatibility/2006"
    )
    source_document = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
  xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
  xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
  xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
  xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
  mc:Ignorable="w14 wp14">
  <w:body>
    <w:tbl>
      <w:tblPr><w:tblW w:w="9360" w:type="dxa"/></w:tblPr>
      <w:tr><w:tc><w:p w:rsidR="00112233" w14:paraId="1234ABCD"><w:r><w:t>受控表格文本</w:t></w:r></w:p></w:tc></w:tr>
    </w:tbl>
    <w:sectPr><w:headerReference w:type="default" r:id="rId9"/></w:sectPr>
  </w:body>
</w:document>'''.encode("utf-8")
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", source_document)

    def namespace_map(data: bytes) -> dict[str, str]:
        return {
            prefix: uri
            for _event, (prefix, uri) in ElementTree.iterparse(
                io.BytesIO(data), events=("start-ns",)
            )
        }

    def expanded_signature_without_rsids(node: ElementTree.Element) -> tuple:
        return (
            node.tag,
            tuple(
                sorted(
                    (name, value)
                    for name, value in node.attrib.items()
                    if not name.startswith(f"{{{word_namespace}}}rsid")
                )
            ),
            node.text,
            node.tail,
            tuple(expanded_signature_without_rsids(child) for child in node),
        )

    expected_namespace_map = {
        "w": word_namespace,
        "r": relationship_namespace,
        "mc": compatibility_namespace,
        "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
        "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    }
    assert namespace_map(source_document) == expected_namespace_map
    source_root = ElementTree.fromstring(source_document)

    document_module._scrub_docx_metadata(path)

    with ZipFile(path) as archive:
        scrubbed_document = archive.read("word/document.xml")
    scrubbed_root = ElementTree.fromstring(scrubbed_document)
    assert namespace_map(scrubbed_document) == expected_namespace_map
    assert scrubbed_root.get(f"{{{compatibility_namespace}}}Ignorable") == "w14 wp14"
    assert expanded_signature_without_rsids(scrubbed_root) == (
        expanded_signature_without_rsids(source_root)
    )


def test_generated_document_set_contains_required_sections_and_honest_boundaries(
    anonymous_documents: Path,
) -> None:
    for document_id in DOCUMENT_IDS:
        assert (anonymous_documents / f"{document_id}.docx").is_file()
        assert (anonymous_documents / f"{document_id}.pdf").read_bytes().startswith(b"%PDF")
        assert audit_standard_business_brief(
            anonymous_documents / f"{document_id}.docx"
        ) == []

    technical_xml = _docx_part(
        anonymous_documents / "technical-report.docx",
        "word/document.xml",
    )
    for heading in (
        "问题背景",
        "系统架构",
        "任务分配",
        "时空规划",
        "安全执行",
        "动态恢复",
        "实验方法与结果",
        "同类技术比较",
        "应用价值",
        "限制",
        "参考文献",
        "知识产权",
    ):
        assert heading in technical_xml
    for disclosure in (
        "软件仿真",
        "进程内会话",
        "单进程并发边界",
        "无持久化",
        "无认证",
        "不提供完整 MAPF 保证",
    ):
        assert disclosure in technical_xml

    application_xml = _docx_part(
        anonymous_documents / "application-summary.docx",
        "word/document.xml",
    )
    assert "非官方申报表" in application_xml
    assert "受控匿名申请人甲" in application_xml


def test_documents_use_explicit_standard_business_tokens_and_scrub_core_metadata(
    anonymous_documents: Path,
) -> None:
    technical = anonymous_documents / "technical-report.docx"
    styles = _docx_part(technical, "word/styles.xml")
    document = _docx_part(technical, "word/document.xml")
    numbering = _docx_part(technical, "word/numbering.xml")
    core = _docx_part(technical, "docProps/core.xml")

    assert 'w:styleId="Normal"' in styles
    assert 'w:after="120"' in styles
    assert 'w:line="264"' in styles
    assert 'w:styleId="Heading1"' in styles
    assert 'w:before="320"' in styles
    assert 'w:pgSz w:w="12240" w:h="15840"' in document
    assert 'w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"' in document
    assert 'w:header="708" w:footer="708"' in document
    assert 'w:numFmt w:val="decimal"' in numbering
    assert 'w:left="720" w:hanging="360"' in numbering
    assert 'w:line="280"' in numbering
    assert "受控匿名申请人甲" not in core
    assert "受控匿名指导人乙" not in core


def test_visual_gate_requires_explicit_approval_bound_to_every_current_page_hash(
    anonymous_documents: Path,
) -> None:
    request = json.loads(
        (anonymous_documents / "visual-review-request.json").read_text(encoding="utf-8")
    )
    assert set(request["documents"]) == set(DOCUMENT_IDS)
    pages = [
        page
        for document in request["documents"].values()
        for page in document["pages"]
    ]
    assert pages
    assert all(page["status"] == "pending" for page in pages)
    for page in pages:
        image = anonymous_documents / page["imagePath"]
        payload = image.read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", payload[16:24])
        assert width >= 1000 and height >= 1000

    assert verify_visual_approvals(anonymous_documents, None) is False
    approvals_path = anonymous_documents / "visual-approvals.json"
    approvals_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "pages": [
                    {
                        "documentId": page["documentId"],
                        "pageNumber": page["pageNumber"],
                        "imageSha256": page["imageSha256"],
                        "approved": True,
                    }
                    for page in pages
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    assert verify_visual_approvals(anonymous_documents, approvals_path) is True

    image = anonymous_documents / pages[0]["imagePath"]
    original = image.read_bytes()
    try:
        image.write_bytes(original + b"changed")
        assert verify_visual_approvals(anonymous_documents, approvals_path) is False
    finally:
        image.write_bytes(original)


def test_non_formal_manifests_do_not_contain_applicant_or_advisor_values(
    anonymous_documents: Path,
) -> None:
    for name in ("document-manifest.json", "visual-review-request.json"):
        text = (anonymous_documents / name).read_text(encoding="utf-8")
        assert "受控匿名申请人甲" not in text
        assert "受控匿名指导人乙" not in text
