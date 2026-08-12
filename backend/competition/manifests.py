"""严格加载 3S 提交清单，防止演示契约静默漂移。"""

import copy
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.app.schemas import DispatchOptions, Scenario, Task


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIRECTORY = PROJECT_ROOT / "competition" / "3s" / "manifests"
MAIN_DEMO_MANIFEST_PATH = MANIFESTS_DIRECTORY / "main-demo.json"
SAFETY_DEMO_MANIFEST_PATH = MANIFESTS_DIRECTORY / "safety-demo.json"


class ManifestValidationError(ValueError):
    """提交清单不符合固定版本或固定演示契约。"""


_MAIN_OPTIONS = {
    "avoidConflicts": True,
    "includeDynamic": True,
    "assignmentReplanWindow": 24,
    "adaptiveReplanWindow": False,
}
_SAFETY_OPTIONS = {
    "avoidConflicts": True,
    "includeDynamic": False,
    "assignmentReplanWindow": 24,
    "adaptiveReplanWindow": False,
}
_MAIN_STEPS = [
    {
        "time": 12,
        "action": "addTask",
        "task": {
            "id": "DEMO-URGENT-12",
            "type": "emergency",
            "title": "高优先级冷链复核",
            "priority": 5,
            "releaseTime": 12,
            "deadline": 120,
            "serviceTime": 2,
            "target": [1, 4],
        },
    },
    {"time": 20, "action": "addBlockedCell", "cell": [10, 6]},
    {"time": 28, "action": "failRobot", "robotId": "R2"},
    {"time": 36, "action": "removeBlockedCell", "cell": [10, 6]},
    {"time": 36, "action": "restoreRobot", "robotId": "R2"},
    {"time": 700, "action": "accept"},
]


def load_manifest(path: Path) -> dict[str, Any]:
    """读取并验证单个版本 1 的 3S 演示清单。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestValidationError(f"无法读取清单：{path}") from error
    if not isinstance(payload, dict):
        raise ManifestValidationError("清单根节点必须是对象")
    if payload.get("schemaVersion") != 1:
        raise ManifestValidationError("不支持的 schemaVersion")
    kind = payload.get("kind")
    if kind == "main-demo":
        _validate_main_demo(payload)
    elif kind == "safety-demo":
        _validate_safety_demo(payload)
    else:
        raise ManifestValidationError("不支持的清单 kind")
    return copy.deepcopy(payload)


def load_main_demo_manifest() -> dict[str, Any]:
    """加载固定主演示清单。"""
    return load_manifest(MAIN_DEMO_MANIFEST_PATH)


def load_safety_demo_manifest() -> dict[str, Any]:
    """加载固定安全门清单。"""
    return load_manifest(SAFETY_DEMO_MANIFEST_PATH)


def load_safety_demo_scenario() -> dict[str, Any]:
    """返回安全门夹具唯一数据源的深拷贝。"""
    return copy.deepcopy(load_safety_demo_manifest()["scenario"])


def _validate_main_demo(payload: dict[str, Any]) -> None:
    _require_exact_keys(payload, {"schemaVersion", "kind", "scenarioId", "options", "steps"})
    if payload["scenarioId"] != "integrated-demo":
        raise ManifestValidationError("主演示 scenarioId 必须是 integrated-demo")
    _validate_options(payload["options"], _MAIN_OPTIONS)
    if payload["steps"] != _MAIN_STEPS:
        if (
            isinstance(payload["steps"], list)
            and len(payload["steps"]) > 2
            and isinstance(payload["steps"][2], dict)
            and payload["steps"][2].get("robotId") != "R2"
        ):
            raise ManifestValidationError("主演示 robotId 必须是 R2")
        raise ManifestValidationError("主演示步骤顺序、标识符或字段不符合固定契约")
    try:
        Task.model_validate(payload["steps"][0]["task"])
    except ValidationError as error:
        raise ManifestValidationError("主演示任务字段无效") from error


def _validate_safety_demo(payload: dict[str, Any]) -> None:
    _require_exact_keys(payload, {"schemaVersion", "kind", "scenario", "options"})
    _validate_options(payload["options"], _SAFETY_OPTIONS)
    try:
        scenario = Scenario.model_validate(payload["scenario"])
    except ValidationError as error:
        raise ManifestValidationError("安全门场景字段无效") from error
    if scenario.id != "forced-safety-gate":
        raise ManifestValidationError("安全门场景标识符不一致")
    if scenario.width != 3 or scenario.height != 1:
        raise ManifestValidationError("安全门场景必须是 3×1")
    if [robot.id for robot in scenario.robots] != ["R1", "R2"]:
        raise ManifestValidationError("安全门机器人标识符不一致")
    if [task.id for task in scenario.tasks] != ["T1", "T2"]:
        raise ManifestValidationError("安全门任务标识符不一致")


def _validate_options(value: Any, expected: dict[str, Any]) -> None:
    if not isinstance(value, dict) or value != expected:
        raise ManifestValidationError("清单 options 不符合固定契约")
    try:
        DispatchOptions.model_validate(value)
    except ValidationError as error:
        raise ManifestValidationError("清单 options 字段无效") from error


def _require_exact_keys(value: dict[str, Any], expected: set[str]) -> None:
    if set(value) != expected:
        raise ManifestValidationError("清单字段不符合固定契约")
