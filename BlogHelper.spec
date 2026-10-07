# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path
import playwright

from windows_runtime import build_driver_archive


datas = []
binaries = []
hiddenimports = []

datas += [
    ("version.json", "."),
    ("assets/blog_helper_icon.png", "assets"),
    ("assets/wordpress-logo.png", "assets"),
    ("assets/tistory-logo.png", "assets"),
    ("assets/blogspot-logo.png", "assets"),
    ("assets/bootstrap-icons.woff", "assets"),
    ("assets/bootstrap-icons.css", "assets"),
    ("assets/bootstrap-icons-LICENSE.txt", "assets"),
]

for package in ("customtkinter", "tkinterdnd2", "playwright", "yt_dlp", "certifi", "keyring", "PIL", "pillow_heif", "bsdiff4", "websocket"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package, include_py_files=False)
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

# Do not extract the large Node runtime and driver tree on every double-click.
# Keep them compressed, then prepare a persistent verified cache on first use.
driver_archive, driver_metadata = build_driver_archive(
    Path(playwright.__file__).parent / "driver", Path("build/windows-runtime"),
)
def is_driver_entry(entry):
    return str(entry[0]).replace("\\", "/").startswith("playwright/driver/")
a.binaries = [entry for entry in a.binaries if not is_driver_entry(entry)]
a.datas = [entry for entry in a.datas if not is_driver_entry(entry)]
a.datas += [
    (driver_archive.name, str(driver_archive), "DATA"),
    (driver_metadata.name, str(driver_metadata), "DATA"),
]

splash = Splash(
    "assets/blog_helper_icon.png",
    binaries=a.binaries,
    datas=a.datas,
    max_img_size=(320, 320),
    # Keep a clear startup message visible while the one-file executable is loading.
    # Coordinates are based on the resized 320 x 320 splash image; the lower area
    # is dark enough for this centered, two-line white message to remain legible.
    text_pos=(80, 306),
    text_size=-16,
    text_font="{Malgun Gothic}",
    text_color="#FFFFFF",
    text_default="실행중...\n잠시만기다려주세요.",
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
