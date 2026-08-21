from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NPM = Path(r"C:\nvm4w\nodejs\npm.cmd")
NODE = Path(r"C:\nvm4w\nodejs\node.exe")


def _environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["WAREHOUSE_PATROL_DOCUMENTS_PYTHON"] = sys.executable
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def test_root_documents_command_forwards_double_dash_arguments_to_the_python_cli() -> None:
    completed = subprocess.run(
        [str(NPM), "run", "competition:documents", "--", "--help"],
        cwd=REPOSITORY_ROOT,
        env=_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--metadata" in completed.stdout
    assert "--converted-dir" in completed.stdout
    assert "--verify-only" in completed.stdout


def test_root_release_command_exposes_the_required_final_step_order_without_executing_it() -> None:
    completed = subprocess.run(
        [
            str(NPM),
            "run",
            "competition:release",
            "--",
            "--metadata",
            "controlled-metadata.json",
            "--print-plan",
        ],
        cwd=REPOSITORY_ROOT,
        env=_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout[completed.stdout.index("{") :])
    assert payload["steps"] == [
        "fullCheck",
        "evidence",
        "portablePackage",
        "documents",
        "recordingGate",
        "sensitiveScan",
        "hashes",
    ]
    assert payload["task6Default"] == "blocked"


def _fake_npm_cli(path: Path) -> Path:
    path.write_text(
        """import { appendFileSync } from "node:fs";
appendFileSync(process.env.CONTROLLED_NPM_LOG, `${JSON.stringify(process.argv.slice(2))}\\n`);
process.exit(0);
""",
        encoding="utf-8",
    )
    return path


def test_release_command_requires_converted_dir_before_any_full_check(
    tmp_path: Path,
) -> None:
    log = tmp_path / "npm-calls.jsonl"
    environment = _environment()
    environment["npm_execpath"] = str(_fake_npm_cli(tmp_path / "fake-npm.mjs"))
    environment["CONTROLLED_NPM_LOG"] = str(log)
    environment["WAREHOUSE_PATROL_RELEASE_PYTHON"] = sys.executable

    completed = subprocess.run(
        [
            str(NODE),
            str(REPOSITORY_ROOT / "competition/run-release.mjs"),
            "--metadata",
            "controlled-metadata.json",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )

    assert completed.returncode == 1
    assert "--converted-dir" in completed.stderr
    assert not log.exists()


def test_release_command_forwards_converted_dir_to_documents(
    tmp_path: Path,
) -> None:
    log = tmp_path / "npm-calls.jsonl"
    environment = _environment()
    environment["npm_execpath"] = str(_fake_npm_cli(tmp_path / "fake-npm.mjs"))
    environment["CONTROLLED_NPM_LOG"] = str(log)
    environment["WAREHOUSE_PATROL_RELEASE_PYTHON"] = sys.executable
    converted = tmp_path / "converted"

    subprocess.run(
        [
            str(NODE),
            str(REPOSITORY_ROOT / "competition/run-release.mjs"),
            "--metadata",
            "controlled-metadata.json",
            "--converted-dir",
            str(converted),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )

    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    documents_call = next(call for call in calls if "competition:documents" in call)
    assert documents_call == [
        "run",
        "competition:documents",
        "--",
        "--metadata",
        "controlled-metadata.json",
        "--converted-dir",
        str(converted),
    ]
