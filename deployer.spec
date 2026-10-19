# -*- mode: python ; coding: utf-8 -*-
"""Build one portable Windows directory; no serial port is opened while building."""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


root = Path(SPECPATH).resolve()
required = [root / "main.py", root / "deployer_core.py", root / "updater.py",
            root / "tools" / "arduino-cli.exe", root / "tools" / "manifest.json",
            root / "firmware" / "agent-n16r8.bin", root / "firmware" / "manifest.json"]
for path in required:
    if not path.is_file():
        raise FileNotFoundError(f"Required build input is missing: {path}")
if not (root / "licenses").is_dir():
    raise FileNotFoundError("A licenses/ directory is required before packaging.")

# esptool needs the target JSON stubs (both stub_flasher/1 and /2). pyserial
# imports platform and URL handlers dynamically. Tkinter's built-in PyInstaller
# hooks collect _tkinter.pyd, Tcl/Tk DLLs, _tcl_data, _tk_data and its runtime hook.
datas = [(str(root / "firmware"), "firmware"),
         (str(root / "tools"), "tools"),
         (str(root / "licenses"), "licenses")]
datas += collect_data_files("esptool")
datas += copy_metadata("esptool") + copy_metadata("pyserial")
hiddenimports = sorted(set(collect_submodules("esptool")
                           + collect_submodules("serial")
                           + collect_submodules("rich_click")))

a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["bleak", "pytest", "IPython", "notebook", "matplotlib", "numpy"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ESP32-OneClick",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    contents_directory="_internal",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ESP32-OneClick",
)
