"""3S 提交手续元数据的严格验证入口。"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_NAME = "仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统"


class SubmissionGateError(ValueError):
    """表示提交手续或本地输入未通过严格门禁。"""


def load_verified_submission_metadata(
    metadata_path: Path,
    repository_root: Path,
) -> dict[str, object]:
    root = repository_root.resolve()
    path = metadata_path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SubmissionGateError("本地元数据路径必须位于仓库内并被 Git 忽略。") from exc
    if not path.is_file():
        raise SubmissionGateError("本地元数据文件不存在。")
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", "--", str(path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if ignored.returncode != 0:
        raise SubmissionGateError("本地元数据路径必须被 Git 忽略。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SubmissionGateError("本地元数据不是有效的 UTF-8 JSON。") from exc
    _validate_metadata(payload)
    return payload


_PLACEHOLDER_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\s*$",
        r"\b(?:tbd|todo|placeholder|unknown)\b",
        r"example\.(?:com|org|net)",
        r"(?:待确认|待填写|未核验|请填写|占位)",
        r"^x{2,}$",
        r"^<[^>]+>$",
    )
)
_SOURCE_KEYS = {"url", "attachmentSha256", "verifiedAt", "writtenReply"}
_PROCEDURE_KEYS = {
    "eligibility",
    "majorAndSchoolAcceptance",
    "advisorAndRecommenderRequirements",
    "collegeAndSchoolRecommendation",
    "signaturesAndSeals",
    "internalDeadline",
    "registrationSystem",
    "materialsMailbox",
    "attachmentConstraints",
}
_MATERIAL_KEYS = {
    "sourceCode",
    "runnablePackage",
    "video",
    "applicationForm",
    "technicalReport",
}


def _fail(pointer: str, reason: str) -> None:
    raise SubmissionGateError(f"提交元数据字段 {pointer} {reason}。")


def _object(value: Any, pointer: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(pointer, "必须是对象")
    missing = keys - value.keys()
    if missing:
        _fail(f"{pointer}/{sorted(missing)[0]}", "缺失")
    extra = value.keys() - keys
    if extra:
        _fail(f"{pointer}/{sorted(extra)[0]}", "不允许出现")
    return value


def _text(value: Any, pointer: str) -> str:
    if not isinstance(value, str):
        _fail(pointer, "必须是字符串")
    if any(pattern.search(value) for pattern in _PLACEHOLDER_PATTERNS):
        _fail(pointer, "不得为空或使用占位文本")
    return value


def _true(value: Any, pointer: str) -> None:
    if value is not True:
        _fail(pointer, "必须精确为 true")


def _timestamp(value: Any, pointer: str) -> None:
    text = _text(value, pointer)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SubmissionGateError(f"提交元数据字段 {pointer} 必须是 ISO 8601 时间。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(pointer, "必须包含时区")


def _source(value: Any, pointer: str) -> None:
    source = _object(value, pointer, _SOURCE_KEYS)
    url = _text(source["url"], f"{pointer}/url")
    if not re.fullmatch(r"https://[^\s/]+(?:/[^\s]*)?", url):
        _fail(f"{pointer}/url", "必须是 HTTPS 来源")
    digest = _text(source["attachmentSha256"], f"{pointer}/attachmentSha256")
    if re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
        _fail(f"{pointer}/attachmentSha256", "必须是 64 位 SHA-256")
    _timestamp(source["verifiedAt"], f"{pointer}/verifiedAt")
    _text(source["writtenReply"], f"{pointer}/writtenReply")


def _requirement(
    value: Any,
    pointer: str,
    extra_keys: set[str] | None = None,
) -> dict[str, Any]:
    extras = extra_keys or set()
    item = _object(value, pointer, {"verified", "statement", "source", *extras})
    _true(item["verified"], f"{pointer}/verified")
    _text(item["statement"], f"{pointer}/statement")
    _source(item["source"], f"{pointer}/source")
    return item


def _validate_metadata(payload: Any) -> None:
    root = _object(
        payload,
        "",
        {
            "schemaVersion",
            "projectName",
            "applicant",
            "advisors",
            "procedures",
            "requiredMaterials",
        },
    )
    if root["schemaVersion"] != 1:
        _fail("/schemaVersion", "必须精确为 1")
    if root["projectName"] != PROJECT_NAME:
        _fail("/projectName", "必须使用固定项目名")

    applicant = _object(
        root["applicant"],
        "/applicant",
        {"name", "studentId", "phone", "email", "school", "major"},
    )
    for key in ("name", "studentId", "phone", "email", "school", "major"):
        _text(applicant[key], f"/applicant/{key}")
    if "@" not in applicant["email"] or any(char.isspace() for char in applicant["email"]):
        _fail("/applicant/email", "必须是电子邮箱")

    advisors = root["advisors"]
    if not isinstance(advisors, list) or not advisors:
        _fail("/advisors", "必须是非空数组")
    for index, raw_advisor in enumerate(advisors):
        pointer = f"/advisors/{index}"
        advisor = _object(
            raw_advisor,
            pointer,
            {"name", "role", "professionalTitle"},
        )
        for key in ("name", "role", "professionalTitle"):
            _text(advisor[key], f"{pointer}/{key}")

    procedures = _object(root["procedures"], "/procedures", _PROCEDURE_KEYS)
    for key in (
        "eligibility",
        "majorAndSchoolAcceptance",
        "advisorAndRecommenderRequirements",
        "collegeAndSchoolRecommendation",
        "signaturesAndSeals",
    ):
        _requirement(procedures[key], f"/procedures/{key}")
    deadline = _requirement(
        procedures["internalDeadline"],
        "/procedures/internalDeadline",
        {"deadline"},
    )
    _timestamp(deadline["deadline"], "/procedures/internalDeadline/deadline")
    registration = _requirement(
        procedures["registrationSystem"],
        "/procedures/registrationSystem",
        {"systemUrl"},
    )
    system_url = _text(
        registration["systemUrl"],
        "/procedures/registrationSystem/systemUrl",
    )
    if not system_url.startswith("https://"):
        _fail("/procedures/registrationSystem/systemUrl", "必须是 HTTPS 地址")
    mailbox = _requirement(
        procedures["materialsMailbox"],
        "/procedures/materialsMailbox",
        {"email"},
    )
    email = _text(mailbox["email"], "/procedures/materialsMailbox/email")
    if "@" not in email or any(char.isspace() for char in email):
        _fail("/procedures/materialsMailbox/email", "必须是电子邮箱")
    constraints = _requirement(
        procedures["attachmentConstraints"],
        "/procedures/attachmentConstraints",
        {"maximumBytes", "allowedFormats"},
    )
    if not isinstance(constraints["maximumBytes"], int) or constraints["maximumBytes"] <= 0:
        _fail("/procedures/attachmentConstraints/maximumBytes", "必须是正整数")
    formats = constraints["allowedFormats"]
    if not isinstance(formats, list) or not formats:
        _fail("/procedures/attachmentConstraints/allowedFormats", "必须是非空数组")
    normalized_formats = []
    for index, raw_format in enumerate(formats):
        normalized_formats.append(
            _text(raw_format, f"/procedures/attachmentConstraints/allowedFormats/{index}")
        )
    if len(set(normalized_formats)) != len(normalized_formats):
        _fail("/procedures/attachmentConstraints/allowedFormats", "不得重复")

    materials = _object(root["requiredMaterials"], "/requiredMaterials", _MATERIAL_KEYS)
    for key in sorted(_MATERIAL_KEYS):
        pointer = f"/requiredMaterials/{key}"
        material = _object(
            materials[key],
            pointer,
            {"verified", "required", "relationship", "source"},
        )
        _true(material["verified"], f"{pointer}/verified")
        if not isinstance(material["required"], bool):
            _fail(f"{pointer}/required", "必须是布尔值")
        _text(material["relationship"], f"{pointer}/relationship")
        _source(material["source"], f"{pointer}/source")
