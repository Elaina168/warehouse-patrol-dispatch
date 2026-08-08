import json
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_exact_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        requirement = Requirement(line)
        specifiers = tuple(requirement.specifier)
        assert requirement.url is None, f"{path}: 不允许 URL 依赖: {line}"
        assert requirement.marker is None, f"{path}: 不允许条件依赖: {line}"
        assert len(specifiers) == 1, f"{path}: 依赖必须是单一精确版本: {line}"
        assert specifiers[0].operator == "==", (
            f"{path}: 依赖必须使用 == 精确锁定: {line}"
        )
        try:
            Version(specifiers[0].version)
        except InvalidVersion as error:
            raise AssertionError(
                f"{path}: 依赖版本必须是有效的 PEP 440 精确版本: {line}"
            ) from error

        normalized_name = canonicalize_name(requirement.name)
        assert normalized_name not in pins, f"{path}: 依赖名称重复: {line}"
        pins[normalized_name] = specifiers[0].version
    return pins


def test_backend_lock_preserves_all_direct_exact_versions() -> None:
    direct_pins = _read_exact_pins(PROJECT_ROOT / "backend" / "requirements.txt")
    lock_pins = _read_exact_pins(
        PROJECT_ROOT / "backend" / "requirements.lock.txt"
    )

    mismatches = {
        name: {
            "direct": direct_version,
            "lock": lock_pins.get(name),
        }
        for name, direct_version in direct_pins.items()
        if lock_pins.get(name) != direct_version
    }

    assert not mismatches, f"后端直接依赖与锁文件不一致: {mismatches}"


def test_exact_pin_parser_rejects_wildcard_version(tmp_path: Path) -> None:
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text("demo==1.*\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="必须是有效的 PEP 440 精确版本"):
        _read_exact_pins(requirements_path)


def test_frontend_lock_preserves_reviewed_security_floors() -> None:
    lock = json.loads(
        (PROJECT_ROOT / "frontend" / "package-lock.json").read_text(
            encoding="utf-8"
        )
    )
    packages = lock["packages"]

    assert Version(packages["node_modules/postcss"]["version"]) >= Version(
        "8.5.23"
    )
    assert Version(packages["node_modules/nanoid"]["version"]) >= Version(
        "3.3.17"
    )
