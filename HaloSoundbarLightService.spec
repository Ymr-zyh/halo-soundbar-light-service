# Build from this spec's directory on any Windows checkout.
from pathlib import Path

project_root = Path(SPECPATH)
source_root = project_root / "src"

a = Analysis(
    [str(source_root / "halo_soundbar_service" / "__main__.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "win32timezone", "hid", "servicemanager", "win32service",
        "win32serviceutil", "win32event", "pywintypes", "pythoncom",
        "win32api", "win32con", "win32security",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HaloSoundbarLightService",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
