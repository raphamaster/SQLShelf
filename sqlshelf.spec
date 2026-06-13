# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SQLShelf — Windows single-folder build.
# Run: pyinstaller sqlshelf.spec

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Include the schema.sql resource shipped inside the package
datas = [
    ("sqlshelf/core/schema.sql", "sqlshelf/core"),
]
datas += collect_data_files("qt_material")

a = Analysis(
    ["main.py"],
    pathex=[str(Path(".").resolve())],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "sqlglot",
        "yaml",
        "watchdog.observers",
        "watchdog.observers.polling",
        "qt_material",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "scipy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SQLShelf",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SQLShelf",
)
