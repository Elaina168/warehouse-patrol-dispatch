"""Windows 竞赛包的本地启动入口。"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

import uvicorn


LOOPBACK_HOST = "127.0.0.1"
PROJECT_NAME = "仓巡智调——面向动态仓储的多机器人在线调度与安全决策系统"


class PortUnavailableError(RuntimeError):
    """本地启动器所需端口已被其他进程占用。"""


def reserve_loopback_port(port: int) -> socket.socket:
    """预先绑定回环地址，确保不接管或终止其他进程。"""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((LOOPBACK_HOST, port))
        listener.listen()
        return listener
    except OSError as error:
        listener.close()
        raise PortUnavailableError(
            f"无法启动：{LOOPBACK_HOST}:{port} 已被占用，请先关闭占用该端口的程序。"
        ) from error


class LocalServer:
    """拥有预绑定监听器的 Uvicorn 服务线程。"""

    def __init__(self, application, listener: socket.socket) -> None:
        self._listener = listener
        self._server = uvicorn.Server(
            uvicorn.Config(
                application,
                log_level="warning",
                access_log=False,
                lifespan="off",
                ws="none",
            )
        )
        self.thread = threading.Thread(target=self._run, name="warehouse-patrol-server")

    def _run(self) -> None:
        try:
            self._server.run(sockets=[self._listener])
        finally:
            self._listener.close()

    def start(self) -> None:
        """在后台启动持有监听器的服务。"""
        self.thread.start()

    def stop(self) -> None:
        """停止自身服务，并确认其线程和监听器均已退出。"""
        self._server.should_exit = True
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self._listener.close()
            self._server.force_exit = True
            self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("本地服务线程未能停止")


def show_desktop_window(url: str, stop_callback: Callable[[], None]) -> None:
    """显示最小控制窗口；Tk 仅在交互模式延迟导入。"""
    import tkinter
    from tkinter import ttk

    window = tkinter.Tk()
    window.title(PROJECT_NAME)
    window.resizable(False, False)
    frame = ttk.Frame(window, padding=20)
    frame.grid()
    ttk.Label(frame, text=f"{PROJECT_NAME}正在本机运行").grid(column=0, row=0, pady=(0, 8))
    ttk.Label(frame, text=url).grid(column=0, row=1, pady=(0, 12))

    def stop_and_close() -> None:
        stop_callback()
        window.destroy()

    ttk.Button(frame, text="停止服务", command=stop_and_close).grid(column=0, row=2)
    window.protocol("WM_DELETE_WINDOW", stop_and_close)
    window.mainloop()


def launch_desktop(
    port: int,
    *,
    application_loader: Callable[[], object],
    browser_opener: Callable[[str], object] = webbrowser.open,
    gui_runner: Callable[[str, Callable[[], None]], None] = show_desktop_window,
) -> None:
    """启动本机服务、浏览器与可停止的轻量窗口。"""
    listener = reserve_loopback_port(port)
    server = LocalServer(application_loader(), listener)
    actual_port = listener.getsockname()[1]
    url = f"http://{LOOPBACK_HOST}:{actual_port}/"
    try:
        server.start()
        browser_opener(url)
        gui_runner(url, server.stop)
    finally:
        server.stop()


def runtime_root() -> Path:
    """返回源码运行或 PyInstaller 解包后的资源根目录。"""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root is not None:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[1]


def load_application() -> object:
    """在导入 FastAPI 应用前指定与可执行文件同包的前端产物。"""
    os.environ["WAREHOUSE_PATROL_FRONTEND_DIST"] = str(
        runtime_root() / "frontend" / "dist"
    )
    from backend.app.main import app

    return app


def run_headless(port: int, *, application_loader: Callable[[], object] = load_application) -> None:
    """以阻塞方式运行，供自动化和录制流程调用。"""
    listener = reserve_loopback_port(port)
    server = LocalServer(application_loader(), listener)
    if sys.platform != "win32":
        server._run()
        return

    original_break_handler = signal.getsignal(signal.SIGBREAK)

    def ignore_replayed_break(_signal_number, _frame) -> None:
        """让 Uvicorn 完成清理后重放的 Ctrl+Break 正常返回。"""

    signal.signal(signal.SIGBREAK, ignore_replayed_break)
    try:
        server._run()
    finally:
        signal.signal(signal.SIGBREAK, original_break_handler)


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """解析启动器命令行参数。"""
    parser = argparse.ArgumentParser(description=f"{PROJECT_NAME}本地启动器")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--port", type=int)
    parsed = parser.parse_args(arguments)
    if parsed.headless and parsed.port is None:
        parser.error("--headless 必须同时提供 --port")
    return parsed


def main(arguments: list[str] | None = None) -> int:
    """运行图形或无头启动入口。"""
    parsed = parse_arguments(arguments)
    try:
        if parsed.headless:
            run_headless(parsed.port)
            return 0
        launch_desktop(
            parsed.port if parsed.port is not None else 0,
            application_loader=load_application,
        )
    except PortUnavailableError as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
