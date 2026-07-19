# -*- mode: python ; coding: utf-8 -*-
import json

from PyInstaller.utils.hooks import collect_all


datas = []
binaries = []
hiddenimports = []

datas += [("version.json", ".")]
with open("version.json", "r", encoding="utf-8") as version_file:
    app_version = str(json.load(version_file).get("version") or "1.0.0")

for package in ("customtkinter", "playwright", "yt_dlp", "certifi", "keyring", "PIL"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

hiddenimports += [
    "playwright.sync_api",
    "playwright._impl._connection",
    "playwright._impl._browser_type",
    "keyring.backends.macOS",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BlogHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
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
    name="BlogHelper",
)

app = BUNDLE(
    coll,
    name="BlogHelper.app",
    icon=None,
    bundle_identifier="kr.soullhk.bloghelper",
    info_plist={
        "CFBundleDisplayName": "Blog Helper Pro",
        "CFBundleShortVersionString": app_version,
        "CFBundleVersion": app_version,
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    },
)
