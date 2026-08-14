import os
import signal
import socket
import struct
import subprocess
import sys
import time
import types
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
    show_desktop_window,
)
from competition.packaging import PYINSTALLER_DATA_MAPPINGS
from competition import packaging as competition_packaging


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_PWSH = PROJECT_ROOT / ".tools" / "powershell" / "pwsh.exe"
SOURCE_PACKAGE_SCRIPT = PROJECT_ROOT / "competition" / "create-source-package.ps1"
BUILD_PACKAGE_SCRIPT = PROJECT_ROOT / "competition" / "build-windows-package.ps1"


def test_headless_arguments_require_an_explicit_port() -> None:
    arguments = parse_arguments(["--headless", "--port", "8123"])

    assert arguments.headless is True
    assert arguments.port == 8123


def test_headless_arguments_reject_a_missing_port() -> None:
    with pytest.raises(SystemExit) as error:
        parse_arguments(["--headless"])

    assert error.value.code == 2


def test_launcher_cli_and_window_use_the_full_project_name(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_name = "仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统"
    window_titles: list[str] = []
    label_texts: list[str] = []

    class FakeWindow:
        def title(self, value: str) -> None:
            window_titles.append(value)

        def resizable(self, _width: bool, _height: bool) -> None:
            pass

        def protocol(self, _name: str, _callback) -> None:
            pass

        def mainloop(self) -> None:
            pass

        def destroy(self) -> None:
            pass

    class FakeWidget:
        def __init__(self, *_args, **kwargs) -> None:
            if "text" in kwargs:
                label_texts.append(kwargs["text"])

        def grid(self, **_kwargs) -> None:
            pass

    fake_ttk = types.SimpleNamespace(
        Frame=FakeWidget,
        Label=FakeWidget,
        Button=FakeWidget,
    )
    fake_tkinter = types.ModuleType("tkinter")
    fake_tkinter.Tk = FakeWindow
    fake_tkinter.ttk = fake_ttk
    monkeypatch.setitem(sys.modules, "tkinter", fake_tkinter)

    show_desktop_window("http://127.0.0.1:8123/", lambda: None)
    with pytest.raises(SystemExit) as error:
        parse_arguments(["--help"])

    assert error.value.code == 0
    assert window_titles == [project_name]
    assert f"{project_name}正在本机运行" in label_texts
    assert project_name in capsys.readouterr().out


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


def test_build_environment_requires_windows_and_64_bit_python() -> None:
    with pytest.raises(RuntimeError, match="仅支持 Windows x64"):
        competition_packaging.validate_windows_x64_environment(
            platform_name="linux",
            pointer_width_bits=64,
        )
    with pytest.raises(RuntimeError, match="64 位 Python"):
        competition_packaging.validate_windows_x64_environment(
            platform_name="win32",
            pointer_width_bits=32,
        )

    competition_packaging.validate_windows_x64_environment(
        platform_name="win32",
        pointer_width_bits=64,
    )


def test_pe_validation_rejects_x86_and_accepts_amd64(tmp_path: Path) -> None:
    def write_pe(path: Path, machine: int) -> None:
        contents = bytearray(0x88)
        contents[0:2] = b"MZ"
        struct.pack_into("<I", contents, 0x3C, 0x80)
        contents[0x80:0x84] = b"PE\0\0"
        struct.pack_into("<H", contents, 0x84, machine)
        path.write_bytes(contents)

    x86_executable = tmp_path / "x86.exe"
    amd64_executable = tmp_path / "amd64.exe"
    write_pe(x86_executable, 0x014C)
    write_pe(amd64_executable, 0x8664)

    with pytest.raises(RuntimeError, match="AMD64"):
        competition_packaging.validate_amd64_pe(x86_executable)

    competition_packaging.validate_amd64_pe(amd64_executable)


@pytest.mark.parametrize(
    ("machine", "expected_return_code", "expected_error"),
    [
        (0x8664, 0, ""),
        (0x014C, 2, "AMD64"),
    ],
)
def test_source_tree_rebuilds_without_git_or_project_tools_and_rejects_x86_pe(
    tmp_path: Path,
    machine: int,
    expected_return_code: int,
    expected_error: str,
) -> None:
    repository = tmp_path / f"source-{machine:04x}"
    competition_directory = repository / "competition"
    competition_directory.mkdir(parents=True)
    for filename in (
        "build-windows-package.ps1",
        "packaging.py",
        "warehouse_patrol.spec",
    ):
        (competition_directory / filename).write_text(
            (PROJECT_ROOT / "competition" / filename).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    fake_pyinstaller = repository / "PyInstaller" / "__main__.py"
    fake_pyinstaller.parent.mkdir(parents=True)
    (fake_pyinstaller.parent / "__init__.py").write_text("", encoding="utf-8")
    fake_pyinstaller.write_text(
        "\n".join(
            [
                "import struct",
                "import sys",
                "from pathlib import Path",
                "arguments = sys.argv[1:]",
                "dist = Path(arguments[arguments.index('--distpath') + 1])",
                "executable = dist / 'WarehousePatrol' / 'WarehousePatrol.exe'",
                "executable.parent.mkdir(parents=True, exist_ok=True)",
                "contents = bytearray(0x88)",
                "contents[0:2] = b'MZ'",
                "struct.pack_into('<I', contents, 0x3C, 0x80)",
                "contents[0x80:0x84] = b'PE\\0\\0'",
                f"struct.pack_into('<H', contents, 0x84, {machine})",
                "executable.write_bytes(contents)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    fake_npm = repository / "fake-npm.cmd"
    fake_npm.write_text("@exit /b 0\n", encoding="utf-8")
    environment = {
        **os.environ,
        "PYTHONPATH": str(repository),
        "PYTHONIOENCODING": "utf-8",
    }

    completed = subprocess.run(
        [
            str(PROJECT_PWSH),
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(competition_directory / "build-windows-package.ps1"),
            "-NpmPath",
            str(fake_npm),
            "-PythonPath",
            sys.executable,
            "-SkipSourcePackage",
        ],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert completed.returncode == expected_return_code, completed.stderr
    assert expected_error in completed.stderr
    assert "Traceback" not in completed.stderr
    assert (repository / ".git").exists() is False
    assert (repository / ".tools").exists() is False
    assert (
        repository
        / "output"
        / "3s-competition-build"
        / "WarehousePatrol"
        / "WarehousePatrol.exe"
    ).exists()
    assert (
        repository / "output" / "3s-competition-build" / "WarehousePatrol-source.zip"
    ).exists() is False


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
        "package.json": "{}\n",
        "frontend/package.json": "{}\n",
        "frontend/package-lock.json": "{}\n",
        "backend/requirements.lock.txt": "fastapi==0.115.14\n",
        "backend/competition/evidence.py": "print('evidence')\n",
        "competition/requirements-build.lock.txt": "pyinstaller==6.16.0\n",
        "competition/BUILDING.md": "# build\n",
        "competition/build-windows-package.ps1": "Write-Output 'build'\n",
        "competition/launcher.py": "print('launcher')\n",
        "competition/packaging.py": "print('packaging')\n",
        "competition/warehouse_patrol.spec": "# spec\n",
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


def test_source_package_rejects_a_tracked_windows_user_profile_path(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    required_files = {
        "package.json": "{}\n",
        "frontend/package.json": "{}\n",
        "frontend/package-lock.json": "{}\n",
        "backend/requirements.lock.txt": "fastapi==0.115.14\n",
        "backend/competition/evidence.py": "print('evidence')\n",
        "competition/requirements-build.lock.txt": "pyinstaller==6.16.0\n",
        "competition/BUILDING.md": "# build\n",
        "competition/build-windows-package.ps1": "Write-Output 'build'\n",
        "competition/launcher.py": "print('launcher')\n",
        "competition/packaging.py": "print('packaging')\n",
        "competition/warehouse_patrol.spec": "# spec\n",
        "competition/3s/manifests/main-demo.json": "{}\n",
        "docs/personal.md": "Do not package C:" + "\\Users\\Example User\\secret.txt\n",
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
    assert "Windows 用户目录绝对路径" in completed.stderr
    assert output_path.exists() is False
