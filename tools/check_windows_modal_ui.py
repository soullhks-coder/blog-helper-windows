"""Offline real-Tk checks of Windows dim updates (also runnable on a Mac).

Uses isolated test windows, not the running Blog Helper app or user settings.
On a Mac this exercises Windows refresh logic, not Windows native rendering.
"""
from pathlib import Path
import sys
import tkinter as tk
import tkinter.font as tkfont
from unittest.mock import patch

import customtkinter as ctk
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modal_backdrop import ModalDimmer, dim_color
from input_surfaces import InputOptionMenu
from windows_fonts import configure_windows_fonts, configure_windows_named_fonts


def check_theme(theme):
    ctk.set_appearance_mode(theme)
    font_loaded = configure_windows_fonts(ctk, Path(__file__).resolve().parents[1])
    root = ctk.CTk()
    configure_windows_named_fonts(root)
    errors = []
    root.report_callback_exception = lambda *error: errors.append(error)
    root.title(f"Blog Helper Windows dim test · {theme} (offline)")
    root.geometry("920x680")
    color = "#ffffff" if theme == "light" else "#222c3b"
    root.configure(fg_color=color)
    card = ctk.CTkFrame(root, fg_color=color)
    card.pack(fill="both", expand=True, padx=20, pady=20)
    button = ctk.CTkButton(card, text="메뉴 · 완료 팝업 테스트", hover=False)
    button.pack(pady=8)
    menu = InputOptionMenu(card, values=["나눔고딕 테스트", "다른 항목"])
    menu.pack(pady=8)
    progress = ctk.CTkProgressBar(card)
    progress.pack(pady=8)
    progress.set(0.5)
    scroll = ctk.CTkScrollableFrame(card, height=100, fg_color=color)
    scroll.pack(fill="x", padx=12)
    for index in range(3):
        ctk.CTkButton(scroll, text=f"추천 키워드 {index + 1}", hover=False).pack(pady=3)
    source_image = ctk.CTkImage(light_image=Image.new("RGB", (40, 30), "white"), size=(40, 30))
    preview = ctk.CTkLabel(card, text="", image=source_image)
    preview.pack()
    canvas = tk.Canvas(card, width=200, height=60, background=color, highlightthickness=0)
    canvas.pack(pady=5)
    rectangle = canvas.create_rectangle(0, 0, 100, 60, fill="#e0e0e0")
    photo = tk.PhotoImage(master=root, width=30, height=30)
    photo.put("#ffffff", to=(0, 0, 30, 30))
    canvas_image = canvas.create_image(150, 30, image=photo)
    dialog = ctk.CTkFrame(root, fg_color="#ecf2fa", width=360, height=130)
    dialog.place(relx=0.5, rely=0.5, anchor="center")
    ctk.CTkLabel(dialog, text="글작성이 완료되었습니다.").pack(padx=25, pady=25)

    def settle(milliseconds=100):
        root.after(milliseconds, root.quit)
        root.mainloop()
        root.update_idletasks()
        assert not errors, errors

    dimmer = ModalDimmer(root, platform_name="win32")
    try:
        if sys.platform == "win32":
            assert font_loaded, "Bundled Nanum Gothic registration failed"
            for weight in ("normal", "bold"):
                font = tkfont.Font(root=root, family="NanumGothic", size=14, weight=weight)
                family = font.actual("family").lower().replace(" ", "")
                assert family in ("nanumgothic", "나눔고딕"), font.actual()
                assert font.actual("weight") == weight
            assert ctk.CTkFont().cget("family") == "NanumGothic"
        settle()
        dimmer.attach(dialog)
        settle(100)
        dark_image = preview.cget("image")
        dark_canvas_image = canvas.itemcget(canvas_image, "image")
        assert dark_image is not source_image
        assert canvas.itemcget(rectangle, "fill") == "#656565"
        assert dialog.cget("fg_color") == "#ecf2fa"
        assert card.cget("fg_color") == dim_color(card, color)
        assert tk.Frame.cget(scroll, "background") == dim_color(scroll, color)
        assert all(value in (114, 115) for value in dark_image.cget("light_image").getpixel((0, 0)))
        # Repeat periodic and manual refreshes. Any original-color writes or
        # image recreation here would reintroduce flicker/resource growth.
        with patch.object(card, "configure", wraps=card.configure) as writes:
            settle(1600)
            for _ in range(6):
                dimmer.refresh()
                root.update_idletasks()
            writes.assert_not_called()
        assert preview.cget("image") is dark_image
        assert canvas.itemcget(canvas_image, "image") == dark_canvas_image

        # New children of dimmed parents must not inherit a permanent dark bg.
        child = ctk.CTkLabel(card, text="추가된 항목")
        child.pack()
        settle()
        dimmer.refresh()
        assert child.cget("bg_color") == dim_color(card, color)

        # Live widget/canvas/image updates are dimmed once and restored to the
        # NEW source value when the final modal is closed.
        new_image = ctk.CTkImage(light_image=Image.new("RGB", (40, 30), "green"), size=(40, 30))
        preview.configure(image=new_image)
        new_photo = tk.PhotoImage(master=root, width=30, height=30)
        new_photo.put("#00ff00", to=(0, 0, 30, 30))
        canvas.itemconfigure(canvas_image, image=new_photo)
        canvas.itemconfigure(rectangle, fill="#00ff00")
        progress.configure(progress_color="#00ff00")
        tk.Frame.configure(scroll, background="#00ff00")
        card.configure(fg_color="#102030")
        child.configure(bg_color="#abcdef")
        dimmer.refresh()
        assert card.cget("fg_color") == dim_color(card, "#102030")
        assert child.cget("bg_color") == dim_color(child, "#abcdef")
        assert progress.cget("progress_color") == "#007300"
        assert canvas.itemcget(rectangle, "fill") == "#007300"
        assert tk.Frame.cget(scroll, "background") == "#007300"
        assert preview.cget("image") is not new_image

        second = ctk.CTkFrame(root, fg_color="#ecf2fa", width=160, height=90)
        second.place(relx=0.7, rely=0.4)
        dimmer.attach(second)
        second.destroy()
        assert dimmer.active
        assert card.cget("fg_color") == dim_color(card, "#102030")
        dialog.destroy()
        settle()
        assert not dimmer.active
        assert not dimmer._windows_saved
        assert dimmer._refresh_job is None
        assert card.cget("fg_color") == "#102030"
        assert child.cget("bg_color") == "#abcdef"
        assert progress.cget("progress_color") == "#00ff00"
        assert canvas.itemcget(rectangle, "fill") == "#00ff00"
        assert canvas.itemcget(canvas_image, "image") == str(new_photo)
        assert preview.cget("image") is new_image
        assert tk.Frame.cget(scroll, "background") == "#00ff00"
        print(f"{sys.platform} {theme}: Windows dim logic, stable images, live updates, nested modals, restoration passed", flush=True)
    finally:
        while dimmer.active:
            dimmer.end()
        root.destroy()


if __name__ == "__main__":
    for theme in ("dark", "light"):
        check_theme(theme)
