"""Native, offline checks of opt-in UI fonts, each in a fresh process."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tkinter.font as tkfont

import customtkinter as ctk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_fonts import (FONT_OPTIONS, SYSTEM_FONT, configure_app_fonts,
                       configure_app_named_fonts, ui_font_family)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", choices=FONT_OPTIONS)
    args = parser.parse_args()
    if args.font is None:
        for choice in FONT_OPTIONS:
            subprocess.run([sys.executable, __file__, "--font", choice], check=True, timeout=35)
        return
    assert configure_app_fonts(ctk, ROOT, args.font), f"Font registration failed: {args.font}"
    root = ctk.CTk()
    root.title("Blog Helper font check (isolated, offline)")
    root.geometry("740x400")
    native_family = tkfont.nametofont("TkDefaultFont", root=root).actual("family")
    configure_app_named_fonts(root, ctk)
    expected = ui_font_family()
    if args.font == SYSTEM_FONT:
        assert expected == native_family
    errors = []
    root.report_callback_exception = lambda *error: errors.append(error)
    card = ctk.CTkFrame(root)
    card.pack(fill="both", expand=True, padx=18, pady=18)
    title = ctk.CTkLabel(card, text="글꼴 설정 · Blog Helper 123", font=ctk.CTkFont(size=26, weight="bold"))
    title.pack(pady=12)
    label = ctk.CTkLabel(card, text="가나다라마바사 · 프롬프트 관리 · 썸네일 발행")
    label.pack(pady=5)
    entry = ctk.CTkEntry(card, placeholder_text="선택한 글꼴로 입력합니다.", width=400)
    entry.pack(pady=8)
    menu = ctk.CTkOptionMenu(card, values=list(FONT_OPTIONS), width=400)
    menu.set(args.font)
    menu.pack(pady=8)
    button = ctk.CTkButton(card, text="시스템 기본 · 나눔고딕 · 나눔스퀘어 네오", width=400)
    button.pack(pady=8)
    try:
        for theme in ("dark", "light"):
            ctk.set_appearance_mode(theme)
            root.after(250, root.quit)
            root.mainloop()
            root.update_idletasks()
            for widget in (title, label, entry, menu, button):
                assert widget.cget("font").cget("family") == expected
            for weight in ("normal", "bold"):
                font = tkfont.Font(root=root, family=expected, size=15, weight=weight)
                actual = font.actual()
                assert actual["family"].lower().replace(" ", "") == expected.lower().replace(" ", ""), actual
                assert actual["weight"] == weight, actual
                assert font.measure("가나다라마바사 Blog Helper 123") > 100
            assert not errors, errors
            # Windows CI's redirected stdout can be cp1252 even when Tk itself
            # correctly displays Korean. Keep diagnostics console-encoding safe.
            print(f"PASS {sys.platform} {theme}: font option {FONT_OPTIONS.index(args.font)} -> {ascii(expected)}, native normal/bold and CTk widgets", flush=True)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
