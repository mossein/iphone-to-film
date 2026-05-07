# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Film desktop app.

Build:    pyinstaller film.spec --noconfirm
Output:   dist/Film.app  (double-click to launch)
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

# spectral_film_lut imports stocks lazily by attribute name (sfl.KODAK_PORTRA_400
# etc.), so PyInstaller's static analysis misses them. Pull every submodule.
sfl_datas, sfl_binaries, sfl_hiddenimports = collect_all("spectral_film_lut")
colour_datas, colour_binaries, colour_hiddenimports = collect_all("colour")
rawpy_datas, rawpy_binaries, rawpy_hiddenimports = collect_all("rawpy")
# cv2 has a custom __init__.py that does dynamic imports — collect_all
# captures the native extension and avoids the "recursion detected" error
# that happens when PyInstaller only ships the Python wrapper.
cv2_datas, cv2_binaries, cv2_hiddenimports = collect_all("cv2")

hiddenimports = (
    sfl_hiddenimports
    + colour_hiddenimports
    + rawpy_hiddenimports
    + cv2_hiddenimports
    + collect_submodules("core")
    + collect_submodules("web")
    + ["pillow_heif", "PIL.ImageOps"]
)

datas = (
    sfl_datas
    + colour_datas
    + rawpy_datas
    + cv2_datas
    + [("web/static", "web/static")]
)

binaries = sfl_binaries + colour_binaries + rawpy_binaries + cv2_binaries

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=["pyi_rthook_cv2.py"],
    excludes=["matplotlib", "PyQt6", "PyQt5", "tkinter"],
    noarchive=False,
    module_collection_mode={"cv2": "pyz+py"},
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Film",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Film",
)

app = BUNDLE(
    coll,
    name="Film.app",
    icon=None,
    bundle_identifier="com.mossein.film",
    info_plist={
        "CFBundleName": "Film",
        "CFBundleDisplayName": "Film",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
    },
)
