"""Offline real-Tk regression for dimmed dropdown/label background leakage."""
from pathlib import Path
import sys
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import customtkinter as ctk
from modal_backdrop import ModalDimmer


def check(theme, platform):
    ctk.set_appearance_mode(theme)
    ctk.set_widget_scaling(.82)
    root = ctk.CTk()
    color = '#f6f8fc' if theme == 'light' else '#05080e'
    fill = '#d7e3f4' if theme == 'light' else '#314761'
    root.configure(fg_color=color)
    root.geometry('850x500')
    native_row = tk.Frame(root, bg=color)
    native_row.pack(fill='x', padx=20, pady=20)
    transparent = ctk.CTkFrame(native_row, fg_color='transparent')
    transparent.pack(fill='x')
    baseline = ctk.CTkOptionMenu(transparent, values=['first', 'second'], height=27,
                               fg_color=fill, button_color=fill)
    baseline.pack()
    old_hidden_parent = ctk.CTkFrame(transparent, fg_color='transparent')
    old_hidden = ctk.CTkOptionMenu(old_hidden_parent, values=['previously hidden'], fg_color=fill)
    old_hidden.pack()
    root.update()
    dimmer = ModalDimmer(root, platform_name=platform)
    try:
        for refresh in (False, True):
            dialog = ctk.CTkFrame(transparent, fg_color=color)
            dialog.pack()
            ctk.CTkLabel(dialog, text='completion popup').pack()
            root.update_idletasks()
            dimmer.attach(dialog)
            parent = ctk.CTkFrame(transparent, fg_color='transparent')
            parent.pack(fill='x')
            label = ctk.CTkLabel(parent, text='Thumbnail / prompt')
            label.pack(side='left')
            menu = ctk.CTkOptionMenu(parent, values=['one', 'two'], height=27,
                                   fg_color=fill, button_color=fill)
            menu.pack(side='left')
            hidden = ctk.CTkOptionMenu(parent, values=['hidden'], fg_color=fill)
            root.update_idletasks()
            for _ in range(3 if refresh else 0):
                baseline.configure(state='disabled')
                menu.configure(state='disabled')
                dimmer.refresh()
                root.update_idletasks()
                baseline.configure(state='normal')
                menu.configure(state='normal')
            # Close immediately too: a 500 ms refresh is not guaranteed to see
            # children created just before the popup is dismissed.
            dialog.destroy()
            root.update_idletasks()
            old_hidden_parent.pack()
            hidden.pack()
            root.update_idletasks()
            for control in (parent, label, menu, hidden, baseline, old_hidden_parent, old_hidden):
                assert control.cget('bg_color') == color, (theme, platform, refresh, type(control).__name__, control.cget('bg_color'))
                assert control._canvas.cget('bg') == color
            assert menu._text_label.cget('bg') == fill
            assert baseline._text_label.cget('bg') == fill
            assert label._label.cget('bg') == color
            assert not dimmer.active and not dimmer._windows_saved
            parent.destroy()
            old_hidden_parent.pack_forget()
        print(f'{sys.platform} {theme} {platform}: late/hidden dropdowns, nested transparent/native parents, redraw and restoration passed', flush=True)
    finally:
        while dimmer.active:
            dimmer.end()
        root.destroy()


if __name__ == '__main__':
    for theme in ('light', 'dark'):
        for platform in ('darwin', 'win32'):
            check(theme, platform)
