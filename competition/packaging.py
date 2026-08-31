"""竞赛 Windows 包与源码包共享的资源声明。"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path


PYINSTALLER_DATA_MAPPINGS = (
    ("frontend/dist", "frontend/dist"),
)
PE_MACHINE_AMD64 = 0x8664


def validate_windows_x64_environment(
    *,
    platform_name: str = sys.platform,
    pointer_width_bits: int = struct.calcsize("P") * 8,
) -> None:
    """拒绝不能生成 Windows x64 产物的构建解释器。"""
    if platform_name != "win32":
        raise RuntimeError(
            f"竞赛包仅支持 Windows x64 构建；当前 Python 平台为 {platform_name}。"
        )
    if pointer_width_bits != 64:
        raise RuntimeError(
            f"竞赛包必须使用 64 位 Python；当前 Python 为 {pointer_width_bits} 位。"
        )


def validate_amd64_pe(executable_path: Path) -> None:
    """读取 PE COFF Machine 字段并要求 AMD64。"""
    try:
        with executable_path.open("rb") as executable:
            if executable.read(2) != b"MZ":
                raise RuntimeError(f"产物不是有效的 PE 文件：{executable_path}")
            executable.seek(0x3C)
            pe_offset_data = executable.read(4)
            if len(pe_offset_data) != 4:
                raise RuntimeError(f"产物缺少 PE 头偏移：{executable_path}")
            pe_offset = struct.unpack("<I", pe_offset_data)[0]
            executable.seek(pe_offset)
            if executable.read(4) != b"PE\0\0":
                raise RuntimeError(f"产物缺少 PE 签名：{executable_path}")
            machine_data = executable.read(2)
            if len(machine_data) != 2:
                raise RuntimeError(f"产物缺少 PE Machine 字段：{executable_path}")
            machine = struct.unpack("<H", machine_data)[0]
    except OSError as error:
        raise RuntimeError(f"无法读取打包产物：{executable_path}") from error

    if machine != PE_MACHINE_AMD64:
        raise RuntimeError(
            f"打包产物必须是 AMD64 PE（0x8664）；实际 Machine=0x{machine:04X}。"
        )


def main(arguments: list[str] | None = None) -> int:
    """为 PowerShell 构建脚本提供可复用的架构门。"""
    parser = argparse.ArgumentParser(description="竞赛 Windows x64 构建验证")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-build-environment")
    pe_parser = subparsers.add_parser("validate-amd64-pe")
    pe_parser.add_argument("executable", type=Path)
    parsed = parser.parse_args(arguments)

    try:
        if parsed.command == "validate-build-environment":
            validate_windows_x64_environment()
        else:
            validate_amd64_pe(parsed.executable)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
