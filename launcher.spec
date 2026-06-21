# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for ArchViz Director launcher.
Bundles:  launcher.py  +  archviz_director.py  +  core/  +  recipes/  +  nodes/
Run:  pyinstaller launcher.spec
Output:  dist/ArchVizDirector/ArchVizDirector.exe
"""

import os

ROOT = os.path.abspath(os.path.dirname(SPEC))   # project root

a = Analysis(
    [os.path.join(ROOT, "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # Pipeline script
        (os.path.join(ROOT, "archviz_director.py"), "."),
        # Core library
        (os.path.join(ROOT, "core"),    "core"),
        # Nodes library
        (os.path.join(ROOT, "nodes"),   "nodes"),
        # All recipe JSON files
        (os.path.join(ROOT, "recipes"), "recipes"),
    ],
    hiddenimports=[
        "tkinter", "tkinter.ttk", "tkinter.filedialog",
        "tkinter.scrolledtext", "tkinter.messagebox",
        "urllib.request", "json", "struct", "base64",
        "core.identity", "core.comfy_api", "core.prompts_archviz",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "PIL", "cv2"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ArchVizDirector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,        # no terminal window — everything goes in the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ArchVizDirector",
)
