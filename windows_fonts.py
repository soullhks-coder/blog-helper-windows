"""Process-private Windows UI fonts. Never install fonts or alter macOS fonts."""
from __future__ import annotations

import atexit
import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import tkinter as tk
import tkinter.font as tkfont


FONT_FAMILY = "NanumGothic"
FONT_FILES = ("NanumGothic.ttf", "NanumGothicBold.ttf")
FR_PRIVATE = 0x10
_registered = {}
_family = "Malgun Gothic"


def release_windows_fonts():
    for key, api in list(_registered.items()):
        try:
            api.RemoveFontResourceExW(key, FR_PRIVATE, None)
        except (AttributeError, OSError):
            pass
        _registered.pop(key, None)


atexit.register(release_windows_fonts)


def configure_windows_fonts(ctk, resource_dir: Path, *, platform_name=None, gdi32=None,
                            font_family=FONT_FAMILY, font_files=FONT_FILES) -> bool:
    """Register regular/bold before Tk creates widgets; no registry/admin writes."""
    global _family
    if (sys.platform if platform_name is None else platform_name) != "win32":
        return False
    paths = [Path(resource_dir) / "assets" / "fonts" / name for name in font_files]
    if not all(path.is_file() for path in paths):
        _family = "Malgun Gothic"
        ctk.ThemeManager.theme["CTkFont"]["family"] = _family
        return False
    added = []
    try:
        api = gdi32 if gdi32 is not None else ctypes.WinDLL("gdi32")
        api.AddFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
        api.AddFontResourceExW.restype = ctypes.c_int
        api.RemoveFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
        api.RemoveFontResourceExW.restype = wintypes.BOOL
        for path in paths:
            key = str(path.resolve())
            if key in _registered:
                continue
            if not api.AddFontResourceExW(key, FR_PRIVATE, None):
                raise OSError(f"Font registration failed: {path.name}")
            _registered[key] = api
            added.append(key)
        _family = font_family
    except (AttributeError, OSError):
        for key in added:
            try:
                api.RemoveFontResourceExW(key, FR_PRIVATE, None)
            except (AttributeError, OSError):
                pass
            _registered.pop(key, None)
        _family = "Malgun Gothic"
    ctk.ThemeManager.theme["CTkFont"]["family"] = _family
    return _family == font_family


def ui_font_family(mac_family="Apple SD Gothic Neo", *, platform_name=None) -> str:
    return _family if (sys.platform if platform_name is None else platform_name) == "win32" else mac_family


def configure_windows_named_fonts(root, *, platform_name=None):
    if (sys.platform if platform_name is None else platform_name) != "win32":
        return
    for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(name, root=root).configure(family=_family)
        except tk.TclError:
            pass
    root.option_add("*Font", "TkDefaultFont")
