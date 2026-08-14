import os
import signal
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.request import urlopen

import pytest
from fastapi import FastAPI
from packaging.requirements import Requirement

from competition.launcher import (
    LocalServer,
    PortUnavailableError,
    launch_desktop,
    parse_arguments,
    reserve_loopback_port,
)
from competition.packaging import PYINSTALLER_DATA_MAPPINGS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_PWSH = PROJECT_ROOT / ".tools" / "powershell" / "pwsh.exe"
SOURCE_PACKAGE_SCRIPT = PROJECT_ROOT / "competition" / "create-source-package.ps1"


def test_headless_arguments_require_an_explicit_port() -> None:
    arguments = parse_arguments(["--headless", "--port", "8123"])

    assert arguments.headless is True
    assert arguments.port == 8123


def test_headless_arguments_reject_a_missing_port() -> None:
    with pytest.raises(SystemExit) as error:
        parse_arguments(["--headless"])

    assert error.value.code == 2


def test_occupied_port_fails_without_stopping_the_existing_listener() -> None:
    existing_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    existing_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    existing_listener.bind(("127.0.0.1", 0))
    existing_listener.listen()
    port = existing_listener.getsockname()[1]
    try:
        with pytest.raises(PortUnavailableError, match=f"127.0.0.1:{port}"):
            reserve_loopback_port(port)

        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    finally:
        existing_listener.close()


def test_headless_cli_reports_occupied_port_without_a_traceback_or_process_takeover() -> None:
    existing_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    existing_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    existing_listener.bind(("127.0.0.1", 0))
    existing_listener.listen()
    port = existing_listener.getsockname()[1]
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "competition.launcher",
                "--headless",
                "--port",
                str(port),
            ],
            cwd=PROJECT_ROOT,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )

        assert completed.returncode == 2
        assert completed.stdout == ""
        assert completed.stderr.strip() == (
            f"无法启动：127.0.0.1:{port} 已被占用，请先关闭占用该端口的程序。"
        )
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    finally:
        existing_listener.close()


def test_owned_server_stops_its_thread_and_listener() -> None:
    application = FastAPI()

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    listener = reserve_loopback_port(0)
    port = listener.getsockname()[1]
    server = LocalServer(application, listener)
    server.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.02)
        else:
            raise AssertionError("服务未开始监听")
    finally:
        server.stop()

    assert server.thread.is_alive() is False
    assert listener.fileno() == -1


def test_desktop_launch_opens_loopback_url_and_exposes_stop_to_gui_boundary() -> None:
    application = FastAPI()
    opened_urls: list[str] = []
    gui_urls: list[str] = []
    stop_callbacks = []

    def show_window(url: str, stop_callback) -> None:
        gui_urls.append(url)
        stop_callbacks.append(stop_callback)
        stop_callback()

    launch_desktop(
        port=0,
        application_loader=lambda: application,
        browser_opener=opened_urls.append,
        gui_runner=show_window,
    )

    assert len(opened_urls) == 1
    assert opened_urls == gui_urls
    assert opened_urls[0].startswith("http://127.0.0.1:")
    assert len(stop_callbacks) == 1


def test_pyinstaller_resource_mapping_includes_frontend_and_competition_assets() -> None:
    assert PYINSTALLER_DATA_MAPPINGS == (
        ("frontend/dist", "frontend/dist"),
        ("competition/3s", "competition/3s"),
    )


def test_competition_build_dependencies_are_all_exactly_locked() -> None:
    lock_path = PROJECT_ROOT / "competition" / "requirements-build.lock.txt"
    requirements = [
        Requirement(line)
        for line in lock_path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]

    assert {requirement.name.lower() for requirement in requirements} == {
        "altgraph",
        "packaging",
        "pefile",
        "pyinstaller",
        "pyinstaller-hooks-contrib",
        "pywin32-ctypes",
        "setuptools",
    }
    assert all(
        len(tuple(requirement.specifier)) == 1
        and next(iter(requirement.specifier)).operator == "=="
        for requirement in requirements
    )


def test_headless_launcher_serves_health_on_the_requested_loopback_port() -> None:
    temporary_listener = reserve_loopback_port(0)
    port = temporary_listener.getsockname()[1]
    temporary_listener.close()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "competition.launcher",
            "--headless",
            "--port",
            str(port),
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"无头启动器过早退出：stdout={stdout!r}, stderr={stderr!r}"
                )
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2) as response:
                    assert response.read() == b'{"status":"ok"}'
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("无头启动器未提供健康检查")
    finally:
        if process.poll() is None:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=10)

    assert process.returncode == 0
    released_listener = reserve_loopback_port(port)
    released_listener.close()


def test_source_package_archives_clean_head_without_git_metadata(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    required_files = {
        ".gitignore": ".env\n.venv/\nnode_modules/\noutput/\nsubmission/warehouse-patrol-3s/\n",
        "backend/requirements.lock.txt": "fastapi==0.115.14\n",
        "backend/competition/evidence.py": "print('evidence')\n",
        "competition/requirements-build.lock.txt": "pyinstaller==6.16.0\n",
        "competition/BUILDING.md": "# build\n",
        "competition/launcher.py": "print('launcher')\n",
        "competition/3s/manifests/main-demo.json": "{}\n",
    }
    for relative_path, contents in required_files.items():
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repository, check=True, capture_output=True)
    ignored_files = {
        ".env": "API_KEY=never-package-this\n",
        ".venv/secret.txt": "venv\n",
        "node_modules/secret.txt": "node\n",
        "output/secret.txt": "output\n",
        "submission/warehouse-patrol-3s/application.docx": "personal\n",
    }
    for relative_path, contents in ignored_files.items():
        path = repository / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    output_path = tmp_path / "WarehousePatrol-source.zip"

    completed = subprocess.run(
        [
            str(PROJECT_PWSH),
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(SOURCE_PACKAGE_SCRIPT),
            "-RepositoryRoot",
            str(repository),
            "-OutputPath",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stderr
    with zipfile.ZipFile(output_path) as archive:
        names = set(archive.namelist())
    assert set(required_files).issubset(names)
    assert not any(name.startswith(".git/") for name in names)
    assert set(ignored_files).isdisjoint(names)


def test_source_package_rejects_a_dirty_worktree(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    (repository / "tracked.txt").write_text("clean\n", encoding="utf-8")
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repository, check=True, capture_output=True)
    (repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    output_path = tmp_path / "WarehousePatrol-source.zip"

    completed = subprocess.run(
        [
            str(PROJECT_PWSH),
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(SOURCE_PACKAGE_SCRIPT),
            "-RepositoryRoot",
            str(repository),
            "-OutputPath",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    assert "工作树不干净" in completed.stderr
    assert output_path.exists() is False
