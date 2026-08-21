# 3S Markdown Document Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blocked programmatic DOCX-to-Word/PDF path with four deterministic Markdown content sources, accept eight user-converted DOCX/PDF files, and retain the existing visual, release, privacy, and hash gates.

**Architecture:** A focused Markdown builder owns content and a manifest; a converted-document receiver owns exact-file validation, DOCX/PDF text checks, Poppler rendering, and visual-review requests. The existing document CLI orchestrates source-only generation, converted-file ingestion, and approval verification, while the release runner requires ingested and approved converted files. Legacy `.doc` conversion remains isolated and is not part of the normal material path.

**Tech Stack:** Python 3, UTF-8 Markdown, standard-library ZIP/XML for read-only DOCX inspection, `python-docx==1.2.0` and `reportlab==4.4.9` for controlled test fixtures, `pypdf==6.10.0` for PDF text extraction, Poppler `pdfinfo`/`pdftoppm` for page verification and PNG rendering, pytest, Node.js npm runners.

**Spec:** `docs/superpowers/specs/2026-08-21-3s-markdown-document-handoff-design.md`

## Global Constraints

- The project name is exactly `仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统`.
- Markdown is the only generated content source; the normal material path must not generate DOCX or invoke Microsoft Word.
- The exact document IDs are `application-summary`, `technical-report`, `run-guide`, and `third-party-dependencies`.
- User-converted input is read from the Git-ignored directory `competition/3s/local/converted-documents/` and contains exactly four same-basename DOCX/PDF pairs.
- `application-summary` is explicitly a non-official information summary and cannot be represented as the organizer's official application form.
- Generated Markdown, local metadata, and the converted-document working directory must not be copied into the formal release.
- The formal release continues to contain only verified DOCX/PDF files at the existing exact destinations.
- A converted-file ingest is not a document-gate pass; every current PDF page must be rendered and explicitly approved by its current PNG SHA-256.
- Missing official procedures, converted files, visual approval, recording, or final hashes must fail closed and must not claim formal-submission readiness.
- Real personal data may appear in formal application content, but logs, exceptions, manifests outside the ignored output, source archives, and test snapshots must not disclose it.
- Preserve the existing scheduling algorithm and backend business API models; do not merge or push.
- All new behavior follows strict TDD: run each new test and observe the expected failure before implementation.

---

### Task 1: Preserve the verified Task 5 metadata and legacy-template foundation

**Files:**
- Modify: `.gitignore`
- Create: `backend/competition/submission.py`
- Create: `backend/competition/legacy_doc.py`
- Create: `backend/tests/test_competition_submission.py`
- Create: `backend/tests/test_competition_legacy_doc.py`
- Create: `competition/3s/schemas/submission-metadata.schema.json`
- Create: `competition/3s/submission-metadata.example.json`
- Create: `competition/convert-legacy-doc-worker.ps1`
- Create: `competition/word-process.ps1`

**Interfaces:**
- Produces: `load_verified_submission_metadata(metadata_path: Path, repository_root: Path) -> dict[str, object]` and `SubmissionGateError` from `backend.competition.submission`.
- Produces: `LegacyDocConverter.convert(source: Path, destination: Path, manifest_path: Path) -> None` and `LegacyDocConversionError` from `backend.competition.legacy_doc`.
- Produces: the exact ignored roots `output/`, `submission/warehouse-patrol-3s/`, and `competition/3s/local/`.

- [ ] **Step 1: Review the already-written foundation against the approved design**

Confirm that the current unstaged metadata loader rejects unignored paths, missing required keys, `verified != true`, placeholder text, weak source records, and malformed hashes/timestamps. Confirm that the legacy converter accepts only an explicit `.doc` source, records the original SHA-256 before conversion, never overwrites source or destination, and terminates only the Word process it owns. This step preserves previously TDD-developed code from the stopped Word path; it adds no new behavior.

- [ ] **Step 2: Run the focused foundation tests**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=output\pytest-task5-foundation backend\tests\test_competition_submission.py backend\tests\test_competition_legacy_doc.py
```

Expected: all selected tests pass. A desktop-dependent Word smoke may skip only when its exact prerequisite is unavailable; record the skip separately.

- [ ] **Step 3: Verify schema, privacy, and worker boundaries**

```powershell
rg -n "verified|attachmentSha256|verifiedAt|writtenReply" competition\3s\schemas\submission-metadata.schema.json competition\3s\submission-metadata.example.json
rg -n "SourcePath|DestinationPath|PidFile|ReadOnly|SaveAs" competition\convert-legacy-doc-worker.ps1 competition\word-process.ps1
git diff --check -- .gitignore backend\competition\submission.py backend\competition\legacy_doc.py backend\tests\test_competition_submission.py backend\tests\test_competition_legacy_doc.py competition\3s\schemas\submission-metadata.schema.json competition\3s\submission-metadata.example.json competition\convert-legacy-doc-worker.ps1 competition\word-process.ps1
```

Expected: the schema contains all exact verification-source keys, the example remains explicitly non-formal, the worker uses explicit paths/PID ownership, and `git diff --check` reports nothing.

- [ ] **Step 4: Commit the foundation separately**

```powershell
git add .gitignore backend/competition/submission.py backend/competition/legacy_doc.py backend/tests/test_competition_submission.py backend/tests/test_competition_legacy_doc.py competition/3s/schemas/submission-metadata.schema.json competition/3s/submission-metadata.example.json competition/convert-legacy-doc-worker.ps1 competition/word-process.ps1
git commit -m "feat: add competition submission metadata gate"
```

---

### Task 2: Deterministic Markdown sources and handoff manifest

**Files:**
- Create: `backend/competition/document_support.py`
- Create: `backend/competition/markdown_documents.py`
- Modify: `backend/competition/documents.py`
- Modify: `backend/competition/release.py`
- Modify: `backend/tests/test_competition_documents.py`
- Test: `backend/tests/test_competition_markdown_documents.py`

**Interfaces:**
- Consumes: `load_verified_submission_metadata(metadata_path, repository_root)` from `backend.competition.submission`; frontend/backend lock files already used by the dependency inventory.
- Produces shared support from `backend.competition.document_support`: `DocumentGenerationError`, `sha256_file(path: Path) -> str`, `load_verified_evidence(path: Path) -> dict[str, object]`, `resolve_latest_evidence(base: Path) -> Path`, and `publish_directory_atomically(staging: Path, destination: Path) -> None`.
- Produces: `MarkdownDocument(document_id: str, title: str, required_phrases: tuple[str, ...], content: str)` and `generate_markdown_handoff(metadata_path: Path, evidence_directory: Path, output_directory: Path) -> Path`.
- Produces output: `markdown/*.md`, `markdown-manifest.json`, and `conversion-instructions.md` under `output/3s-submission-documents/`.

- [ ] **Step 1: Write failing tests for the four exact Markdown documents**

Create `backend/tests/test_competition_markdown_documents.py` with a controlled verified metadata fixture and controlled evidence fixture. Assert literal filenames and headings rather than deriving expectations from production constants:

```python
EXPECTED_FILES = {
    "application-summary.md",
    "technical-report.md",
    "run-guide.md",
    "third-party-dependencies.md",
}

def test_markdown_handoff_contains_exact_documents_sections_and_boundaries(tmp_path: Path) -> None:
    output = generate_markdown_handoff(
        metadata_path=_write_verified_metadata(tmp_path),
        evidence_directory=_write_controlled_evidence(tmp_path),
        output_directory=tmp_path / "documents",
    )
    markdown = output / "markdown"
    assert {path.name for path in markdown.iterdir()} == EXPECTED_FILES
    for path in markdown.iterdir():
        assert "仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统" in path.read_text(encoding="utf-8")
    technical = (markdown / "technical-report.md").read_text(encoding="utf-8")
    for heading in (
        "# 技术报告",
        "## 问题背景",
        "## 系统架构",
        "## 任务分配",
        "## 时空规划",
        "## 安全执行",
        "## 动态恢复",
        "## 实验方法与结果",
        "## 同类技术比较",
        "## 应用价值",
        "## 限制",
        "## 参考文献",
        "## 知识产权",
    ):
        assert heading in technical
    for boundary in (
        "软件仿真",
        "进程内会话",
        "单进程并发边界",
        "无持久化",
        "无认证",
        "不提供完整 MAPF 保证",
        "不声称任意输入零冲突",
        "不使用机器学习",
        "未接入实体物联网、低空通信或云平台",
    ):
        assert boundary in technical
    application = (markdown / "application-summary.md").read_text(encoding="utf-8")
    assert "非官方信息汇总" in application
    assert "不能替代主办方官方申报表" in application
    run_guide = (markdown / "run-guide.md").read_text(encoding="utf-8")
    for heading in ("# 运行说明", "## 启动步骤", "## 主要操作", "## 运行边界", "## 故障排查"):
        assert heading in run_guide
    dependencies = (markdown / "third-party-dependencies.md").read_text(encoding="utf-8")
    assert "# 知识产权与第三方依赖清单" in dependencies
    assert "锁文件未声明" in dependencies
    assert "正式提交前" in dependencies
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=output\pytest-markdown-red backend\tests\test_competition_markdown_documents.py
```

Expected: collection or import failure because `backend.competition.markdown_documents` and `generate_markdown_handoff` do not exist.

- [ ] **Step 3: Implement the Markdown builder without document libraries**

First move the evidence loading, evidence-directory resolution, SHA-256, and rollback-safe directory publication primitives from `backend.competition.documents` into `backend.competition.document_support`. Update both `documents.py` and `release.py` to import those primitives from the support module. `document_support.py` must not import `documents.py`, `markdown_documents.py`, `converted_documents.py`, or `release.py`.

Then define in `backend/competition/markdown_documents.py`:

```python
@dataclass(frozen=True, slots=True)
class MarkdownDocument:
    document_id: str
    title: str
    required_phrases: tuple[str, ...]
    content: str

def build_markdown_documents(
    metadata: dict[str, object],
    evidence: dict[str, object],
) -> tuple[MarkdownDocument, ...]:
    """Return the four documents in DOCUMENT_IDS order."""

def generate_markdown_handoff(
    *,
    metadata_path: Path,
    evidence_directory: Path,
    output_directory: Path,
) -> Path:
    """Validate inputs and atomically publish the Markdown handoff."""
```

These are exact public signatures; implement their bodies in this step rather than leaving docstring-only functions. Use plain UTF-8 strings and a `_markdown_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str` helper that escapes `|` as `\|` and converts embedded newlines to `<br>`. Port the existing four document bodies without changing project claims or experiment terminology. Build in a sibling staging directory whose name matches `.3s-submission-documents.` plus a generated UUID plus `.tmp`, then call `publish_directory_atomically` only after every file and hash is complete. `markdown_documents.py` must not import or call `backend.competition.documents`.

Write `markdown-manifest.json` with exact top-level fields:

```json
{
  "schemaVersion": 1,
  "status": "awaitingUserConversion",
  "projectName": "仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统",
  "generatedAtUtc": "2026-08-21T10:00:00Z",
  "evidenceId": "20260821T041241Z",
  "evidenceManifestSha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "documents": {
    "application-summary": {"path": "markdown/application-summary.md", "sha256": "1111111111111111111111111111111111111111111111111111111111111111"},
    "technical-report": {"path": "markdown/technical-report.md", "sha256": "2222222222222222222222222222222222222222222222222222222222222222"},
    "run-guide": {"path": "markdown/run-guide.md", "sha256": "3333333333333333333333333333333333333333333333333333333333333333"},
    "third-party-dependencies": {"path": "markdown/third-party-dependencies.md", "sha256": "4444444444444444444444444444444444444444444444444444444444444444"}
  }
}
```

`evidenceId` is derived only from the verified evidence manifest's `generatedAt` value, normalized to the UTC run-ID pattern of eight digits, `T`, six digits, and `Z`; reject a missing or invalid timestamp rather than deriving an identifier from a local path. The manifest must not include absolute paths, applicant values, advisor values, school values, or local metadata filenames.

`conversion-instructions.md` lists the eight exact returned basenames and the fixed directory `competition/3s/local/converted-documents/`. It also contains the exact warning `使用 task5-anonymous-metadata.json 时属于匿名转换排版演练，禁止正式提交` and explains that ingest success remains `awaitingVisualApproval`.

- [ ] **Step 4: Add manifest, atomic rollback, and privacy tests**

Add tests that hand-calculate every Markdown SHA-256, mutate one Markdown file and assert the old manifest no longer describes it, inject a staging write failure and assert an existing output directory is byte-for-byte unchanged, and recursively scan all non-Markdown output files plus captured stdout/stderr for controlled applicant/advisor strings.

- [ ] **Step 5: Run Task 2 tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=output\pytest-markdown-green backend\tests\test_competition_markdown_documents.py backend\tests\test_competition_documents.py backend\tests\test_competition_release.py backend\tests\test_competition_submission.py
```

Expected: all selected tests pass with no warning other than existing platform line-ending notices outside pytest.

- [ ] **Step 6: Commit Task 2**

```powershell
git add backend/competition/document_support.py backend/competition/markdown_documents.py backend/competition/documents.py backend/competition/release.py backend/tests/test_competition_markdown_documents.py backend/tests/test_competition_documents.py
git commit -m "feat: generate competition markdown handoff"
```

---

### Task 3: Converted DOCX/PDF validation, Poppler rendering, and visual request

**Files:**
- Create: `backend/competition/converted_documents.py`
- Modify: `backend/competition/documents.py`
- Modify: `competition/requirements-documents.lock.txt`
- Test: `backend/tests/test_competition_converted_documents.py`
- Modify: `backend/tests/test_competition_documents.py`

**Interfaces:**
- Consumes: `MarkdownDocument` and the current `markdown-manifest.json`; explicit converted directory; explicit `pdftoppm` and `pdfinfo` executable paths.
- Produces: `ConvertedDocument(document_id: str, docx_path: Path, pdf_path: Path)`; `ingest_converted_documents(markdown_output_directory: Path, converted_directory: Path, pdftoppm: Path, pdfinfo: Path) -> Path`.
- Produces output: four root-level DOCX/PDF files, zero-padded page images such as `rendered/application-summary/page-001.png`, and `visual-review-request.json` with status `awaitingVisualApproval`.

- [ ] **Step 1: Lock PDF extraction dependencies and write failing exact-set tests**

Append exact locked dependencies verified in the bundled runtime:

```text
pypdf==6.10.0
reportlab==4.4.9
```

Use reportlab only in tests to create searchable controlled PDFs; production uses pypdf for read-only extraction. Add tests with literal expected basenames:

```python
EXPECTED_CONVERTED = {
    "application-summary.docx", "application-summary.pdf",
    "technical-report.docx", "technical-report.pdf",
    "run-guide.docx", "run-guide.pdf",
    "third-party-dependencies.docx", "third-party-dependencies.pdf",
}

@pytest.mark.parametrize("mutation", ["missing", "extra", "empty", "symlink"])
def test_converted_directory_requires_exact_safe_file_set(tmp_path: Path, mutation: str) -> None:
    converted = _write_valid_converted_set(tmp_path)
    _apply_file_set_mutation(converted, mutation)
    with pytest.raises(DocumentGenerationError):
        validate_converted_file_set(converted)
```

- [ ] **Step 2: Run the exact-set tests and verify RED**

Run with the bundled documents runtime so pypdf/reportlab availability is part of the test:

```powershell
$env:PYTHONPATH=(Resolve-Path '.\.venv\Lib\site-packages').Path
& '.\.documents-venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --noconftest --basetemp=output\pytest-converted-red backend\tests\test_competition_converted_documents.py
```

Expected: collection or import failure because `backend.competition.converted_documents` does not exist.

- [ ] **Step 3: Implement exact input validation and text extraction**

Define in `backend/competition/converted_documents.py`:

```python
@dataclass(frozen=True, slots=True)
class ConvertedDocument:
    document_id: str
    docx_path: Path
    pdf_path: Path

def validate_converted_file_set(converted_directory: Path) -> tuple[ConvertedDocument, ...]:
    """Return the four exact safe DOCX/PDF pairs in document-ID order."""
def extract_docx_text(path: Path) -> str:
    """Read text from a validated OOXML package without modifying it."""

def extract_pdf_text(path: Path) -> str:
    """Read searchable text from every PDF page."""

def validate_converted_content(document: ConvertedDocument) -> None:
    """Require the exact project, title, sections, and capability boundaries."""

def ingest_converted_documents(
    *,
    markdown_output_directory: Path,
    converted_directory: Path,
    pdftoppm: Path,
    pdfinfo: Path,
) -> Path:
    """Validate, copy, render, and atomically publish converted documents."""
```

Implement all five bodies in this step. The signatures and return types are fixed; docstrings in the plan are behavioral summaries, not implementation stubs.

Reject a missing/non-directory converted path, recursive entries, any entry outside the exact eight names, symbolic links, non-files, and zero-byte files. For DOCX require a valid ZIP, `testzip() is None`, `[Content_Types].xml`, and `word/document.xml`; extract all `w:t` values without modifying the archive. For PDF use `pypdf.PdfReader`, require at least one page, and concatenate `page.extract_text() or ""`.

Validate both formats for the exact project name, exact document title, and per-document required phrases from a literal mapping. Require the PDF extracted text to be non-empty and contain the same required phrases; report only `documentId` and a stable reason code on failure.

- [ ] **Step 4: Write and run content-failure tests**

Add real DOCX/PDF fixtures for all four document IDs, then mutate one format at a time to remove the project name, title, a required section, and the non-complete-MAPF boundary. Verify each mutation is rejected without printing the controlled applicant/advisor strings.

Run:

```powershell
$env:PYTHONPATH=(Resolve-Path '.\.venv\Lib\site-packages').Path
& '.\.documents-venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --noconftest --basetemp=output\pytest-converted-content backend\tests\test_competition_converted_documents.py
```

Expected: all tests added through this step pass.

- [ ] **Step 5: Reuse Poppler rendering and create the visual request atomically**

Move or expose the existing `_render_pdf_pages` and visual-approval hash logic through `converted_documents.py` without changing its page numbering or SHA-256 semantics. Ingest into a sibling staging directory that begins with a fresh Markdown handoff copy, then copies the eight validated formal files, renders all PDF pages, and writes:

```json
{
  "schemaVersion": 1,
  "status": "awaitingVisualApproval",
  "markdownManifestSha256": "8888888888888888888888888888888888888888888888888888888888888888",
  "documents": {
    "application-summary": {
      "docxSha256": "5555555555555555555555555555555555555555555555555555555555555555",
      "pdfSha256": "6666666666666666666666666666666666666666666666666666666666666666",
      "pages": [{"documentId": "application-summary", "pageNumber": 1, "imagePath": "rendered/application-summary/page-001.png", "imageSha256": "7777777777777777777777777777777777777777777777777777777777777777", "status": "pending"}]
    }
  }
}
```

The approval file has the exact top-level keys `schemaVersion`, `requestSha256`, and `pages`. `requestSha256` is the SHA-256 of the current `visual-review-request.json`; each page entry retains the exact keys `documentId`, `pageNumber`, `imageSha256`, and `approved`. This preserves explicit approval for every page while also binding the approval to current Markdown, DOCX, PDF, and page hashes. Any source or page change invalidates the old approval.

- [ ] **Step 6: Add rendering, rollback, and stale-approval tests**

Use a controlled executable test double only at the external Poppler boundary; assert the actual receiver behavior and generated page hashes rather than assertions on the double. Cover page-count mismatch, a Poppler nonzero exit, staging rollback, all four documents, a changed Markdown file, a changed DOCX, a changed PDF, a changed page image, a stale `requestSha256`, and a missing page approval.

- [ ] **Step 7: Run Task 3 tests and verify GREEN**

```powershell
$env:PYTHONPATH=(Resolve-Path '.\.venv\Lib\site-packages').Path
& '.\.documents-venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --noconftest --basetemp=output\pytest-converted-green backend\tests\test_competition_converted_documents.py backend\tests\test_competition_documents.py
```

Expected: all selected tests pass. The normal path never invokes Word; a real-Poppler integration may skip only when the executable prerequisite is unavailable, and its exact skip reason is recorded.

- [ ] **Step 8: Commit Task 3**

```powershell
git add backend/competition/converted_documents.py backend/competition/documents.py competition/requirements-documents.lock.txt backend/tests/test_competition_converted_documents.py backend/tests/test_competition_documents.py
git commit -m "feat: validate converted competition documents"
```

---

### Task 4: CLI, release-chain migration, and removal of the blocked formal Word path

**Files:**
- Modify: `backend/competition/documents.py`
- Modify: `backend/competition/release.py`
- Modify: `competition/run-documents.mjs`
- Modify: `competition/run-release.mjs`
- Modify: `package.json`
- Delete: `competition/docx-to-pdf-worker.ps1`
- Modify: `competition/convert-legacy-doc-worker.ps1`
- Keep: `competition/word-process.ps1`
- Modify: `backend/tests/test_competition_submission_commands.py`
- Modify: `backend/tests/test_competition_release.py`
- Modify: `backend/tests/test_competition_documents.py`

**Interfaces:**
- Consumes: `generate_markdown_handoff`, optional `--converted-dir`, explicit Poppler paths, optional `--verify-only --approvals`.
- Produces CLI modes: source-only generation; converted-file ingest and render; existing visual-approval verification.
- Produces release behavior: `competition:release` forwards the converted directory to `competition:documents`, then the existing release builder consumes only validated root-level DOCX/PDF files.

- [ ] **Step 1: Write failing CLI mode and release-forwarding tests**

Add subprocess-level tests for exact behavior:

```python
def test_documents_command_without_converted_dir_generates_markdown_only() -> None:
    completed = run_documents("--metadata", controlled_metadata)
    assert completed.returncode == 0
    assert exact_markdown_handoff_exists()

def test_documents_command_with_converted_dir_ingests_and_renders() -> None:
    completed = run_documents("--metadata", controlled_metadata, "--converted-dir", converted_dir)
    assert completed.returncode == 0
    assert exact_converted_set_and_page_images_exist()

def test_release_command_requires_and_forwards_converted_dir_before_release_build() -> None:
    completed = run_release("--metadata", controlled_metadata)
    assert completed.returncode != 0
    assert "--converted-dir" in completed.stderr

def test_release_never_packages_markdown_or_local_conversion_directory() -> None:
    release = build_controlled_release()
    assert not any(path.suffix == ".md" for path in release.rglob("*"))
    assert "competition/3s/local" not in release_file_listing(release)
```

Implement the named test helpers inside the test modules using the existing subprocess and fixture patterns; the names above define the required assertions and are not production APIs.

The release plan output remains exactly:

```json
{"steps":["fullCheck","evidence","portablePackage","documents","recordingGate","sensitiveScan","hashes"],"task6Default":"blocked"}
```

- [ ] **Step 2: Run the CLI tests and verify RED**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=output\pytest-markdown-cli-red backend\tests\test_competition_submission_commands.py backend\tests\test_competition_release.py
```

Expected: failures because `--converted-dir` is not accepted/forwarded and the old command still enters the Word export path.

- [ ] **Step 3: Replace the document CLI orchestration**

Make `backend.competition.documents.main()` implement exactly:

- `--metadata PATH` required in all modes;
- `--evidence-dir PATH` and `--output-dir PATH` keep current defaults;
- no `--converted-dir`: call `generate_markdown_handoff`, print only a safe handoff status, and return 0;
- `--converted-dir PATH`: regenerate Markdown, require explicit/available `pdftoppm` and `pdfinfo`, call `ingest_converted_documents`, print only a safe `awaitingVisualApproval` status, and return 0;
- `--verify-only --approvals PATH`: call `_verify_existing_documents` and return nonzero when any document/page/hash is missing or stale;
- reject `--verify-only` combined with `--converted-dir`.

Remove `WordPdfExporter`, `_default_pdf_command`, python-docx authoring helpers, programmatic DOCX builders, metadata story-part rewrite, and `audit_standard_business_brief` from the normal document module. Delete the now-unused `competition/docx-to-pdf-worker.ps1` and its tests. Retain `competition/word-process.ps1` only for `convert-legacy-doc-worker.ps1` and retain legacy `.doc` tests.

- [ ] **Step 4: Update npm runners and dependency checks**

Change `competition/run-documents.mjs` dependency smoke to:

```javascript
spawnSync(python, ["-c", "import docx, pypdf"], {
  cwd: repositoryRoot,
  stdio: "ignore",
  windowsHide: true,
})
```

Continue forwarding `WAREHOUSE_PATROL_PDFTOPPM` and `WAREHOUSE_PATROL_PDFINFO`. Update `competition/run-release.mjs` to require `--converted-dir`, add it to `documentArgs`, and stop before full checks with a clear error if the directory argument is absent. Do not log directory contents or metadata values.

- [ ] **Step 5: Keep the release boundary exact**

Keep `assemble_validated_release` copying only:

```text
application-summary.docx/pdf -> 01-application/
technical-report.docx/pdf -> 02-technical-report/
run-guide.docx/pdf -> 03-software/
third-party-dependencies.docx/pdf -> 07-licenses/
```

Add assertions that no `.md`, `markdown-manifest.json`, `conversion-instructions.md`, `competition/3s/local`, or local metadata filename appears in the staged or published release. Preserve the existing six-gate manifest, atomic rollback, sensitive scan, independent hash verification, and stale-hash failure tests.

- [ ] **Step 6: Run Task 4 tests and verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=output\pytest-markdown-cli-green backend\tests\test_competition_submission_commands.py backend\tests\test_competition_release.py backend\tests\test_competition_documents.py backend\tests\test_competition_legacy_doc.py
```

Expected: all non-desktop tests pass; no test enters Microsoft Word for the normal Markdown path.

- [ ] **Step 7: Commit Task 4**

```powershell
git add package.json backend/competition/documents.py backend/competition/release.py competition/run-documents.mjs competition/run-release.mjs competition/convert-legacy-doc-worker.ps1 competition/word-process.ps1 backend/tests/test_competition_submission_commands.py backend/tests/test_competition_release.py backend/tests/test_competition_documents.py backend/tests/test_competition_legacy_doc.py
git add -u -- competition/docx-to-pdf-worker.ps1
git commit -m "refactor: use markdown document handoff"
```

---

### Task 5: Generate the real Markdown handoff and verify the Task 5 checkpoint

**Files:**
- Modify: `.superpowers/sdd/2026-08-12-3s-submission-ready/task-5-report.md` (Git-ignored ledger workspace)
- Modify: `.superpowers/sdd/2026-08-12-3s-submission-ready/progress.md` (Git-ignored ledger workspace)
- Output: `output/3s-submission-documents/markdown/*.md`
- Output: `output/3s-submission-documents/markdown-manifest.json`
- Output: `output/3s-submission-documents/conversion-instructions.md`

**Interfaces:**
- Consumes: the user's Git-ignored verified metadata path and the current accepted evidence directory.
- Produces: four user-facing Markdown files and a handoff manifest; does not mark Task 5 complete until eight converted files return and visual approval passes.

- [ ] **Step 1: Run the complete focused Task 5 suite**

Run both environments deliberately:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=output\pytest-task5-markdown-focused backend\tests\test_competition_submission.py backend\tests\test_competition_legacy_doc.py backend\tests\test_competition_documents.py backend\tests\test_competition_markdown_documents.py backend\tests\test_competition_converted_documents.py backend\tests\test_competition_release.py backend\tests\test_competition_submission_commands.py
```

```powershell
$env:PYTHONPATH=(Resolve-Path '.\.venv\Lib\site-packages').Path
& '.\.documents-venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --noconftest --basetemp=output\pytest-task5-documents-runtime backend\tests\test_competition_documents.py backend\tests\test_competition_markdown_documents.py backend\tests\test_competition_converted_documents.py
```

Record pass/skip counts separately. Do not count a skipped real Poppler/Word integration as a pass.

- [ ] **Step 2: Run the repository-wide check**

```powershell
.\.tools\powershell\pwsh.exe -NoLogo -NoProfile -File scripts\test-all.ps1
```

Expected: frontend production build, all frontend tests, and all backend tests exit 0. Record exact module/test counts from this run.

- [ ] **Step 3: Generate the real Markdown handoff**

With the user's actual ignored metadata path `competition/3s/local/submission-metadata.json` when it exists:

```powershell
$env:WAREHOUSE_PATROL_DOCUMENTS_PYTHON=(Resolve-Path '.\.documents-venv\Scripts\python.exe').Path
& 'C:\nvm4w\nodejs\npm.cmd' run competition:documents -- --metadata 'competition\3s\local\submission-metadata.json' --evidence-dir 'output\3s-submission-evidence'
```

If that exact real metadata file does not exist, run:

```powershell
& 'C:\nvm4w\nodejs\npm.cmd' run competition:documents -- --metadata 'competition\3s\local\task5-anonymous-metadata.json' --evidence-dir 'output\3s-submission-evidence' --output-dir 'output\3s-submission-documents-anonymous-rehearsal'
```

`conversion-instructions.md` always includes the exact warning `使用 task5-anonymous-metadata.json 时属于匿名转换排版演练，禁止正式提交`; assert that warning here, keep the real external-procedure gate failed, and never rename or copy rehearsal output into the formal document output directory.

- [ ] **Step 4: Independently verify the Markdown handoff**

In a fresh process, recompute all four SHA-256 values and compare them with `markdown-manifest.json`. Scan the generated Markdown for placeholders, unsupported claims, API keys, local absolute paths, and inconsistent project names. Confirm the output contains no DOCX, PDF, rendered PNG, or visual approval before the user conversion is returned.

- [ ] **Step 5: Run final source checks and self-review**

```powershell
git diff --check
git status --short
git log -5 --oneline
```

Review the full Task 5 diff from base `bcf582bb04da89de4f3293279585d88f0df46155` to HEAD, plus remaining unstaged changes, for dead Word-path code, personal data, absolute paths, weak tests, and claims that the document gate passed.

- [ ] **Step 6: Update the Task 5 report and checkpoint**

Record every RED/GREEN command, focused/full test result, actual Markdown hashes, whether real or anonymous metadata was used, and the exact remaining user action. Set the ledger status to:

```text
Task 5: awaiting user conversion — four Markdown sources generated and verified; eight DOCX/PDF files and visual approval still required
```

Do not mark Task 5 complete, do not start Task 6, and do not claim formal-submission readiness until the user returns all eight files and the visual gate passes.

- [ ] **Step 7: Record the verified implementation checkpoint**

Stage only source, tests, schemas, runners, and documentation. Do not stage `output/`, `competition/3s/local/`, `.superpowers/sdd/`, or personal metadata.

Run `git status --short`. If Step 5 or Step 6 produced a verified tracked correction, stage only that correction and commit it with `git commit -m "fix: finalize markdown competition handoff"`. If there is no tracked correction, do not create an empty commit. Generated Markdown remains ignored and is handed to the user separately.

Expected: the branch contains the reviewed Task 5 implementation commits, while `output/`, `competition/3s/local/`, `.superpowers/sdd/`, and all personal metadata remain untracked by Git.
