from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from backend.competition.submission import (
    PROJECT_NAME,
    SubmissionGateError,
    load_verified_submission_metadata,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def verified_metadata() -> dict[str, object]:
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


def write_metadata(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_json_schema_composes_extended_requirements_without_rejecting_their_fields() -> None:
    schema = json.loads(
        (
            REPOSITORY_ROOT
            / "competition/3s/schemas/submission-metadata.schema.json"
        ).read_text(encoding="utf-8")
    )

    requirement = schema["$defs"]["requirement"]
    assert requirement.get("additionalProperties") is not False
    assert "(?i)" not in schema["$defs"]["verifiedText"]["not"]["pattern"]
    procedures = schema["$defs"]["procedures"]["properties"]
    for key in (
        "eligibility",
        "majorAndSchoolAcceptance",
        "advisorAndRecommenderRequirements",
        "collegeAndSchoolRecommendation",
        "signaturesAndSeals",
        "internalDeadline",
        "registrationSystem",
        "materialsMailbox",
        "attachmentConstraints",
    ):
        assert procedures[key]["unevaluatedProperties"] is False


@pytest.mark.parametrize(
    ("mutate", "expected_pointer"),
    [
        (
            lambda payload: payload["procedures"]["eligibility"].__setitem__(
                "verified", False
            ),
            "/procedures/eligibility/verified",
        ),
        (
            lambda payload: payload["procedures"]["signaturesAndSeals"].__setitem__(
                "statement", "TBD"
            ),
            "/procedures/signaturesAndSeals/statement",
        ),
        (
            lambda payload: payload["procedures"]["registrationSystem"][
                "source"
            ].__setitem__("attachmentSha256", "abc"),
            "/procedures/registrationSystem/source/attachmentSha256",
        ),
        (
            lambda payload: payload["requiredMaterials"].pop("video"),
            "/requiredMaterials/video",
        ),
    ],
)
def test_metadata_gate_rejects_unverified_placeholder_weak_source_or_missing_field(
    tmp_path: Path,
    mutate,
    expected_pointer: str,
) -> None:
    payload = verified_metadata()
    payload["applicant"]["name"] = "绝不允许进入异常的敏感姓名"
    mutate(payload)
    metadata_path = tmp_path / "submission-metadata.json"
    write_metadata(metadata_path, payload)

    with pytest.raises(SubmissionGateError) as captured:
        load_verified_submission_metadata(metadata_path, REPOSITORY_ROOT)

    assert expected_pointer in str(captured.value)
    assert "绝不允许进入异常的敏感姓名" not in str(captured.value)


def test_metadata_gate_accepts_only_a_git_ignored_local_path(tmp_path: Path) -> None:
    ignored_path = tmp_path / "submission-metadata.json"
    write_metadata(ignored_path, verified_metadata())

    loaded = load_verified_submission_metadata(ignored_path, REPOSITORY_ROOT)

    assert loaded["projectName"] == PROJECT_NAME

    unignored_path = REPOSITORY_ROOT / "task5-controlled-unignored-metadata.json"
    write_metadata(unignored_path, verified_metadata())
    try:
        with pytest.raises(SubmissionGateError, match="Git 忽略"):
            load_verified_submission_metadata(unignored_path, REPOSITORY_ROOT)
    finally:
        unignored_path.unlink(missing_ok=True)
