# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all


datas = []
binaries = []
hiddenimports = []

datas += [
    ("version.json", "."),
    ("assets/blog_helper_icon.png", "assets"),
    ("assets/bootstrap-icons.woff", "assets"),
    ("assets/bootstrap-icons-LICENSE.txt", "assets"),
]

for package in ("customtkinter", "playwright", "yt_dlp", "certifi", "keyring", "PIL", "pillow_heif", "bsdiff4", "websocket"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

hiddenimports += [
    "playwright.sync_api",
    "playwright._impl._connection",
    "playwright._impl._browser_type",
    "keyring.backends.Windows",
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

splash = Splash(
    "assets/blog_helper_icon.png",
    binaries=a.binaries,
    datas=a.datas,
    max_img_size=(320, 320),
    always_on_top=True,
    center="active",
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
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
    icon="assets/blog_helper_icon.ico",
)
