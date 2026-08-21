"""编排 3S Markdown 生成、人工转换接收与逐页批准验证。"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from backend.competition.converted_documents import (
    ConvertedDocument,
    _load_markdown_manifest,
    ingest_converted_documents,
    validate_converted_content,
    verify_visual_approvals,
)
from backend.competition.document_support import DocumentGenerationError
from backend.competition.markdown_documents import (
    DOCUMENT_IDS,
    generate_markdown_handoff,
)
from backend.competition.submission import (
    SubmissionGateError,
    load_verified_submission_metadata,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _verify_existing_documents(
    output_directory: Path,
    approvals_path: Path | None,
) -> bool:
    """验证当前转换文件、内容、来源哈希和逐页批准。"""

    try:
        _load_markdown_manifest(output_directory)
        for document_id in DOCUMENT_IDS:
            document = ConvertedDocument(
                document_id=document_id,
                docx_path=output_directory / f"{document_id}.docx",
                pdf_path=output_directory / f"{document_id}.pdf",
            )
            if (
                document.docx_path.is_symlink()
                or document.pdf_path.is_symlink()
                or not document.docx_path.is_file()
                or not document.pdf_path.is_file()
            ):
                return False
            validate_converted_content(document)
        return verify_visual_approvals(output_directory, approvals_path)
    except (DocumentGenerationError, OSError):
        return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成并验证 3S 申报材料")
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=REPOSITORY_ROOT / "output/3s-submission-evidence",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / "output/3s-submission-documents",
    )
    parser.add_argument("--converted-dir", type=Path)
    parser.add_argument("--approvals", type=Path)
    parser.add_argument("--pdftoppm", type=Path)
    parser.add_argument("--pdfinfo", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """执行 Markdown 生成、转换接收或批准验证模式。"""

    parser = _parser()
    args = parser.parse_args(argv)
    if args.verify_only and args.converted_dir is not None:
        parser.error("--verify-only 不能与 --converted-dir 同时使用。")
    try:
        load_verified_submission_metadata(args.metadata, REPOSITORY_ROOT)
        if args.verify_only:
            if not _verify_existing_documents(args.output_dir, args.approvals):
                raise DocumentGenerationError(
                    "documentId=all;reasonCode=visualApprovalMissingOrStale"
                )
            print("3S 文档门已通过当前逐页哈希批准。")
            return 0

        generate_markdown_handoff(
            metadata_path=args.metadata,
            evidence_directory=args.evidence_dir,
            output_directory=args.output_dir,
        )
        if args.converted_dir is None:
            print("3S Markdown 已生成，状态 awaitingUserConversion。")
            return 0

        pdftoppm = args.pdftoppm or (
            Path(value) if (value := shutil.which("pdftoppm")) else None
        )
        pdfinfo = args.pdfinfo or (
            Path(value) if (value := shutil.which("pdfinfo")) else None
        )
        if pdftoppm is None or pdfinfo is None:
            raise DocumentGenerationError(
                "documentId=all;reasonCode=popplerExecutableMissing"
            )
        ingest_converted_documents(
            markdown_output_directory=args.output_dir,
            converted_directory=args.converted_dir,
            pdftoppm=pdftoppm,
            pdfinfo=pdfinfo,
        )
        print("3S 转换文件已接收并渲染，状态 awaitingVisualApproval。")
        return 0
    except (DocumentGenerationError, SubmissionGateError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError:
        print("文档流程无法完成（reasonCode=ioFailure）。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
