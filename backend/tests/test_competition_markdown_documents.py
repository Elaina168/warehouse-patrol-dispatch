from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

import backend.competition.markdown_documents as markdown_module
from backend.competition.markdown_documents import generate_markdown_handoff
from backend.competition.submission import PROJECT_NAME


pytest_plugins = ("backend.tests.competition_fixtures",)


EXPECTED_FILES = {
    "application-summary.md",
    "technical-report.md",
    "run-guide.md",
    "third-party-dependencies.md",
}


def _verified_metadata() -> dict[str, object]:
    source = {
        "url": "https://official.invalid.local/rules",
        "attachmentSha256": "a" * 64,
        "verifiedAt": "2026-08-14T09:30:00+08:00",
        "writtenReply": "受控测试书面回复已核实该项要求。",
    }
    requirement = {
        "verified": True,
        "statement": "受控测试已核实要求。",
        "source": source,
    }
    return {
        "schemaVersion": 1,
        "projectName": PROJECT_NAME,
        "applicant": {
            "name": "受控测试申请人甲",
            "studentId": "TEST-2026-0001",
            "phone": "13000000000",
            "email": "controlled-applicant@invalid.local",
            "school": "受控测试学校",
            "major": "受控测试专业",
        },
        "advisors": [
            {
                "name": "受控测试指导人乙",
                "role": "指导教师",
                "professionalTitle": "受控测试职称",
            }
        ],
        "procedures": {
            "eligibility": copy.deepcopy(requirement),
            "majorAndSchoolAcceptance": copy.deepcopy(requirement),
            "advisorAndRecommenderRequirements": copy.deepcopy(requirement),
            "collegeAndSchoolRecommendation": copy.deepcopy(requirement),
            "signaturesAndSeals": copy.deepcopy(requirement),
            "internalDeadline": {
                **copy.deepcopy(requirement),
                "deadline": "2026-09-01T17:00:00+08:00",
            },
            "registrationSystem": {
                **copy.deepcopy(requirement),
                "systemUrl": "https://official.invalid.local/register",
            },
            "materialsMailbox": {
                **copy.deepcopy(requirement),
                "email": "materials@invalid.local",
            },
            "attachmentConstraints": {
                **copy.deepcopy(requirement),
                "maximumBytes": 104857600,
                "allowedFormats": ["pdf", "docx", "zip", "mp4"],
            },
        },
        "requiredMaterials": {
            key: {
                "verified": True,
                "required": True,
                "relationship": "该材料由受控测试规则明确列为必交。",
                "source": copy.deepcopy(source),
            }
            for key in (
                "sourceCode",
                "runnablePackage",
                "video",
                "applicationForm",
                "technicalReport",
            )
        },
    }


def _write_metadata(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_verified_metadata(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _write_controlled_evidence(path: Path) -> Path:
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
                "metrics": {
                    "completedTaskCount": 7,
                    "activeConflictCount": 0,
                    "failureCount": 0,
                    "deadlineMissCount": 0,
                },
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
    manifest = {
        "schemaVersion": 1,
        "generatedAt": "2026-08-21T04:12:41Z",
        "files": {
            "results.json": hashlib.sha256(results_path.read_bytes()).hexdigest(),
        },
    }
    (path / "evidence-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _generate(tmp_path: Path) -> Path:
    return generate_markdown_handoff(
        metadata_path=_write_metadata(tmp_path / "metadata.json"),
        evidence_directory=_write_controlled_evidence(tmp_path / "evidence"),
        output_directory=tmp_path / "documents",
    )


def test_markdown_handoff_contains_exact_documents_sections_and_boundaries(
    repository_tmp_path: Path,
) -> None:
    output = _generate(repository_tmp_path)
    markdown = output / "markdown"
    assert {path.name for path in markdown.iterdir()} == EXPECTED_FILES
    for path in markdown.iterdir():
        assert PROJECT_NAME in path.read_text(encoding="utf-8")

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
    assert "受控测试申请人甲" in application
    assert "受控测试指导人乙" in application

    run_guide = (markdown / "run-guide.md").read_text(encoding="utf-8")
    for heading in (
        "# 运行说明",
        "## 启动步骤",
        "## 主要操作",
        "## 运行边界",
        "## 故障排查",
    ):
        assert heading in run_guide

    dependencies = (markdown / "third-party-dependencies.md").read_text(
        encoding="utf-8"
    )
    assert "# 知识产权与第三方依赖清单" in dependencies
    assert "锁文件未声明" in dependencies
    assert "正式提交前" in dependencies


def test_markdown_manifest_hashes_are_recomputable_and_private(
    repository_tmp_path: Path,
) -> None:
    output = _generate(repository_tmp_path)
    manifest_path = output / "markdown-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schemaVersion"] == 1
    assert manifest["status"] == "awaitingUserConversion"
    assert manifest["projectName"] == PROJECT_NAME
    assert manifest["evidenceId"] == "20260821T041241Z"
    assert set(manifest["documents"]) == {
        "application-summary",
        "technical-report",
        "run-guide",
        "third-party-dependencies",
    }
    for record in manifest["documents"].values():
        source = output / record["path"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == record["sha256"]

    serialized = manifest_path.read_text(encoding="utf-8")
    instructions = (output / "conversion-instructions.md").read_text(encoding="utf-8")
    for private_value in (
        "受控测试申请人甲",
        "受控测试指导人乙",
        "受控测试学校",
        str(repository_tmp_path.resolve()),
    ):
        assert private_value not in serialized
        assert private_value not in instructions
    assert "metadata.json" not in serialized
    assert "使用 task5-anonymous-metadata.json 时属于匿名转换排版演练，禁止正式提交" in instructions

    technical = output / "markdown/technical-report.md"
    technical.write_text(technical.read_text(encoding="utf-8") + "变更\n", encoding="utf-8")
    old_hash = manifest["documents"]["technical-report"]["sha256"]
    assert hashlib.sha256(technical.read_bytes()).hexdigest() != old_hash


def test_markdown_generation_failure_preserves_existing_output(
    repository_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = _generate(repository_tmp_path)
    before = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    real_write_text = markdown_module._write_text

    def fail_on_technical(path: Path, content: str) -> None:
        if path.name == "technical-report.md":
            raise OSError("controlled staging failure")
        real_write_text(path, content)

    monkeypatch.setattr(markdown_module, "_write_text", fail_on_technical)
    with pytest.raises(OSError, match="controlled staging failure"):
        generate_markdown_handoff(
            metadata_path=repository_tmp_path / "metadata.json",
            evidence_directory=repository_tmp_path / "evidence",
            output_directory=output,
        )

    after = {
        path.relative_to(output).as_posix(): path.read_bytes()
        for path in output.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not list(repository_tmp_path.glob(".documents.*.tmp"))
