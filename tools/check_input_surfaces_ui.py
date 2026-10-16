"""Native input layout matrix: fractional DPI, themes, fonts, short fields."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import customtkinter as ctk

from app_fonts import FONT_OPTIONS, configure_app_fonts, ui_font_family
from input_surfaces import InputEntry, InputTextbox


def main():
    families = []
    for font in FONT_OPTIONS:
        assert configure_app_fonts(ctk, Path(__file__).resolve().parents[1], font)
        families.append(ui_font_family())
    root = ctk.CTk()
    root.geometry('650x400')
    try:
        checked = 0
        for theme in ('light', 'dark'):
            ctk.set_appearance_mode(theme)
            for scale in (.65, .82, 1, 1.25, 1.5, 2):
                ctk.set_widget_scaling(scale)
                for family in families:
                    for height, font_size in ((28, 12), (32, 13), (38, 14), (40, 16), (52, 16)):
                        entry = InputEntry(root, height=height, width=220, border_width=0,
                                           font=ctk.CTkFont(family=family, size=font_size),
                                           placeholder_text='search word')
                        entry.pack()
                        root.update()
                        # Rebuild paths used by theme/font/border updates must
                        # not restore CTk's vertically stretched native editor.
                        entry.configure(border_width=1, corner_radius=12)
                        root.update()
                        native = entry._entry
                        border = round(entry._apply_widget_scaling(entry.cget('border_width')))
                        safe_bottom = entry.winfo_height() - border - 1
                        assert native.winfo_y() >= border, (theme, scale, family, height)
                        assert native.winfo_y() + native.winfo_height() <= safe_bottom, (
                            theme, scale, family, height, native.winfo_y(), native.winfo_height(), safe_bottom)
                        assert native.winfo_height() >= native.winfo_reqheight(), (theme, scale, family, height, 'text clipped')
                        entry.insert(0, 'saved text')
                        entry.configure(state='disabled')
                        assert entry.get() == 'saved text'
                        entry.destroy()
                        checked += 1
        box = InputTextbox(root, border_width=0)
        box.pack()
        box.insert('1.0', 'first line\nsecond line')
        box.configure(border_width=1)
        assert box.cget('border_width') == 2
        assert box.get('1.0', 'end-1c') == 'first line\nsecond line'
        print(f'{sys.platform}: {checked} native entry layouts, three fonts, both themes and multiline editing passed', flush=True)
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
