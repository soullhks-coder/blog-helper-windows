"""Selectable, process-private UI fonts on macOS and Windows.

No system font installation, registry writes, admin rights or third-party font
packages are required. Register before Tk starts so its font cache sees faces.
"""
from __future__ import annotations

import atexit
import ctypes
from ctypes.util import find_library
import os
from pathlib import Path
import sys
import tkinter as tk
import tkinter.font as tkfont

from windows_fonts import configure_windows_fonts


SYSTEM_FONT = "시스템 기본"
FONT_OPTIONS = (SYSTEM_FONT, "나눔고딕", "나눔스퀘어 네오")
FONT_FILES = {
    "나눔고딕": ("NanumGothic.ttf", "NanumGothicBold.ttf"),
    "나눔스퀘어 네오": ("NanumSquareNeo-bRg.ttf", "NanumSquareNeo-cBd.ttf"),
}
FONT_FAMILIES = {
    "나눔고딕": "NanumGothic",
    # GDI/Tk uses the legacy family (name ID 1); CoreText uses ID 16.
    "나눔스퀘어 네오": "NanumSquare Neo",
}
_selected_family: str | None = None
_selected_platform: str | None = None
_system_family: str | None = None
_default_ctk_families: dict[str, str] = {}
_registered_mac: dict[str, object] = {}


def normalize_app_font(value: object) -> str:
    return str(value) if value in FONT_OPTIONS else SYSTEM_FONT


class _CoreTextFonts:
    def __init__(self):
        self.cf = ctypes.CDLL(find_library("CoreFoundation") or
                              "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.ct = ctypes.CDLL(find_library("CoreText") or
                              "/System/Library/Frameworks/CoreText.framework/CoreText")
        self.cf.CFURLCreateFromFileSystemRepresentation.argtypes = (
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool,
        )
        self.cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
        self.cf.CFRelease.argtypes = (ctypes.c_void_p,)
        self.cf.CFRelease.restype = None
        for name in ("CTFontManagerRegisterFontsForURL", "CTFontManagerUnregisterFontsForURL"):
            api = getattr(self.ct, name)
            api.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)
            api.restype = ctypes.c_bool

    def _call(self, name: str, path: str) -> bool:
        encoded = os.fsencode(path)
        url = self.cf.CFURLCreateFromFileSystemRepresentation(None, encoded, len(encoded), False)
        if not url:
            return False
        try:
            # kCTFontManagerScopeProcess = 1: never install for the user/session.
            return bool(getattr(self.ct, name)(url, 1, None))
        finally:
            self.cf.CFRelease(url)

    def register(self, path: str) -> bool:
        return self._call("CTFontManagerRegisterFontsForURL", path)

    def unregister(self, path: str) -> bool:
        return self._call("CTFontManagerUnregisterFontsForURL", path)


def release_macos_fonts() -> None:
    for path, api in list(_registered_mac.items()):
        try:
            api.unregister(path)
        except (AttributeError, OSError):
            pass
        _registered_mac.pop(path, None)


atexit.register(release_macos_fonts)


def configure_app_fonts(ctk, resource_dir: Path, font_name=SYSTEM_FONT, *,
                        platform_name=None, gdi32=None, mac_api=None) -> bool:
    """Set the selected CTk family, falling back to OS defaults on any failure."""
    global _selected_family, _selected_platform, _system_family
    platform = sys.platform if platform_name is None else platform_name
    selected = normalize_app_font(font_name)
    _selected_family, _selected_platform, _system_family = None, platform, None
    original = _default_ctk_families.setdefault(platform, ctk.ThemeManager.theme["CTkFont"]["family"])
    ctk.ThemeManager.theme["CTkFont"]["family"] = original
    if selected == SYSTEM_FONT:
        return True
    if platform not in ("win32", "darwin"):
        return False
    family = FONT_FAMILIES[selected]
    paths = [Path(resource_dir) / "assets" / "fonts" / name for name in FONT_FILES[selected]]
    if not all(path.is_file() for path in paths):
        return False
    if platform == "win32":
        if selected == "나눔스퀘어 네오":
            family = "NanumSquare Neo Regular"
        loaded = configure_windows_fonts(
            ctk, resource_dir, platform_name=platform, gdi32=gdi32,
            font_family=family, font_files=FONT_FILES[selected],
        )
    else:
        added: list[str] = []
        loaded = False
        try:
            api = mac_api if mac_api is not None else _CoreTextFonts()
            for path in paths:
                key = str(path.resolve())
                if key in _registered_mac:
                    continue
                if not api.register(key):
                    raise OSError(f"Font registration failed: {path.name}")
                _registered_mac[key] = api
                added.append(key)
            loaded = True
        except (AttributeError, OSError):
            for key in added:
                try:
                    _registered_mac[key].unregister(key)
                except (AttributeError, OSError):
                    pass
                _registered_mac.pop(key, None)
    if loaded:
        _selected_family = family
        ctk.ThemeManager.theme["CTkFont"]["family"] = family
    else:
        ctk.ThemeManager.theme["CTkFont"]["family"] = original
    return loaded


def configure_app_named_fonts(root, ctk) -> None:
    """Preserve native named fonts in system mode, only replace custom families."""
    global _system_family
    try:
        _system_family = tkfont.nametofont("TkDefaultFont", root=root).actual("family")
    except tk.TclError:
        _system_family = ctk.ThemeManager.theme["CTkFont"]["family"]
    if _selected_family is None:
        ctk.ThemeManager.theme["CTkFont"]["family"] = _system_family
        return
    for name in ("TkDefaultFont", "TkTextFont", "TkFixedFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(name, root=root).configure(family=_selected_family)
        except tk.TclError:
            pass
    root.option_add("*Font", "TkDefaultFont")


def ui_font_family(system_fallback=None, *, platform_name=None) -> str:
    platform = sys.platform if platform_name is None else platform_name
    if platform == _selected_platform:
        return _selected_family or _system_family or system_fallback or (
            "Segoe UI" if platform == "win32" else "Apple SD Gothic Neo"
        )
    return system_fallback or ("Segoe UI" if platform == "win32" else "Apple SD Gothic Neo")
