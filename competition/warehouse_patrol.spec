# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from competition.packaging import PYINSTALLER_DATA_MAPPINGS


PROJECT_ROOT = Path(SPECPATH).parent
PACKAGED_DATA = [
    (str(PROJECT_ROOT / source), destination)
    for source, destination in PYINSTALLER_DATA_MAPPINGS
]

analysis = Analysis(
    [str(PROJECT_ROOT / "competition" / "launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=PACKAGED_DATA,
    hiddenimports=["backend.app.main"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="WarehousePatrol",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WarehousePatrol",
)
