"""Shared modal dimming without an extra native window or screen capture.

Tk has no alpha channel for child windows. Blend the visible background's
colors and images in place so in-app dialogs keep the main window active.
Only restore values we still own: background workers may update the UI while
the dialog is open, and their newer values must not be overwritten.
"""
from __future__ import annotations

import base64
import io
import threading
import tkinter as tk
from contextlib import contextmanager

import customtkinter as ctk
from PIL import Image, ImageEnhance, ImageTk


def dim_color(widget, value):
    if isinstance(value, (tuple, list)):
        return tuple(dim_color(widget, color) for color in value)
    if value in (None, "", "transparent"):
        return value
    try:
        rgb = widget.winfo_rgb(value)
    except (tk.TclError, TypeError):
        return value
    # Equivalent to a 55% dark scrim; keep the same tone in both themes.
    return "#" + "".join(f"{round(channel / 257 * 0.45):02x}" for channel in rgb)


class ModalDimmer:
    def __init__(self, root):
        self.root = root
        self.dialogs = []
        self._depth = 0
        self._saved = []
        self._images = []
        self._refresh_job = None

    @property
    def active(self):
        return bool(self._depth)

    def attach(self, dialog):
        self.dialogs.append(dialog)
        self.begin()

        def closed(event):
            if event.widget is not dialog:
                return
            if dialog in self.dialogs:
                self.dialogs.remove(dialog)
                self.end()
                if self.dialogs:
                    # Closing one of two concurrent completion dialogs must
                    # not release the remaining dialog's modal input grab.
                    try:
                        self.dialogs[-1].lift()
                        self.dialogs[-1].grab_set()
                    except tk.TclError:
                        pass

        # CTkFrame.bind delegates to its internal drawing canvas. We need the
        # frame's own lifecycle event, not the canvas's earlier destruction.
        if isinstance(dialog, ctk.CTkBaseClass):
            tk.Misc.bind(dialog, "<Destroy>", closed, add="+")
        else:
            dialog.bind("<Destroy>", closed, add="+")

    def begin(self):
        self._depth += 1
        if self._depth == 1:
            self.refresh()

    def end(self):
        self._depth = max(0, self._depth - 1)
        if self._depth == 0:
            if self._refresh_job:
                try:
                    self.root.after_cancel(self._refresh_job)
                except tk.TclError:
                    pass
                self._refresh_job = None
            self._restore()

    @contextmanager
    def temporarily(self):
        self.begin()
        try:
            self.root.update_idletasks()
            yield
        finally:
            self.end()

    def _restore(self):
        for getter, setter, original, dimmed in self._saved:
            try:
                if getter() == dimmed:
                    setter(original)
            except (tk.TclError, ValueError):
                pass
        self._saved.clear()
        self._images.clear()

    def refresh(self):
        self._refresh_job = None
        if not self.active:
            return
        self._restore()
        changes = []

        def visit(widget):
            if widget in self.dialogs or isinstance(widget, (tk.Toplevel, ctk.CTkToplevel)):
                return
            if widget is not self.root and not widget.winfo_ismapped():
                return
            # CTk owns its internal canvas/label/entry colors; changing those
            # independently would leave stale native fills after a redraw.
            is_ctk = isinstance(widget, (ctk.CTk, ctk.CTkBaseClass))
            options = (
                ("fg_color", "bg_color", "border_color", "text_color", "text_color_disabled",
                 "hover_color", "button_color", "button_hover_color", "progress_color",
                 "placeholder_text_color", "checkmark_color")
                if is_ctk else ("background", "foreground", "insertbackground", "selectbackground")
            )
            for option in options:
                try:
                    original = widget.cget(option)
                    dimmed = dim_color(widget, original)
                    if dimmed != original:
                        changes.append((
                            lambda w=widget, o=option: w.cget(o),
                            lambda value, w=widget, o=option: w.configure(**{o: value}),
                            original, dimmed,
                        ))
                except (tk.TclError, ValueError):
                    pass
            if is_ctk:
                try:
                    original = widget.cget("image")
                    if isinstance(original, ctk.CTkImage):
                        dimmed = ctk.CTkImage(
                            light_image=self._dark_image(original.cget("light_image")),
                            dark_image=self._dark_image(original.cget("dark_image")),
                            size=original.cget("size"),
                        )
                        self._images.append(dimmed)
                        changes.append((lambda w=widget: w.cget("image"),
                                        lambda value, w=widget: w.configure(image=value), original, dimmed))
                except (tk.TclError, ValueError):
                    pass
            elif isinstance(widget, tk.Canvas):
                self._dim_canvas(widget, changes)
            for child in widget.winfo_children():
                if is_ctk and child in (
                    getattr(widget, "_canvas", None), getattr(widget, "_label", None),
                    getattr(widget, "_entry", None), getattr(widget, "_textbox", None),
                ):
                    continue
                visit(child)

        visit(self.root)
        # Collect before applying: CTk frame background changes propagate to
        # children, so reading while painting would compound the dim effect.
        for change in changes:
            try:
                change[1](change[3])
                self._saved.append(change)
            except (tk.TclError, ValueError):
                pass
        # Keep live progress and newly drawn previews dimmed too.
        self._refresh_job = self.root.after(500, self.refresh)

    @staticmethod
    def _dark_image(image):
        return ImageEnhance.Brightness(image).enhance(0.45) if image is not None else None

    def _dim_canvas(self, canvas, changes):
        for item in canvas.find_all():
            for option in ("fill", "outline", "image"):
                try:
                    original = canvas.itemcget(item, option)
                    if not original:
                        continue
                    dimmed = dim_color(canvas, original)
                    if option == "image":
                        data = canvas.tk.call(original, "data", "-format", "png")
                        if isinstance(data, str):
                            data = base64.b64decode(data)
                        with Image.open(io.BytesIO(data)) as source:
                            dimmed = ImageTk.PhotoImage(self._dark_image(source), master=canvas)
                        self._images.append(dimmed)
                        dimmed = str(dimmed)
                    if original != dimmed:
                        changes.append((lambda c=canvas, i=item, o=option: c.itemcget(i, o),
                                        lambda value, c=canvas, i=item, o=option: c.itemconfigure(i, **{o: value}),
                                        original, dimmed))
                except (tk.TclError, ValueError, OSError):
                    continue


def modal_dimmer(root):
    dimmer = getattr(root, "_modal_dimmer", None)
    if dimmer is None:
        dimmer = root._modal_dimmer = ModalDimmer(root)
    return dimmer


class DimmedDialogApi:
    """Preserve native message/file/input dialog APIs with the same scrim."""
    def __init__(self, module):
        self._module = module

    def __getattr__(self, name):
        method = getattr(self._module, name)
        if not name.startswith(("show", "ask")):
            return method

        def show(*args, **kwargs):
            parent = kwargs.get("parent") or tk._default_root
            if parent is None or threading.current_thread() is not threading.main_thread():
                return method(*args, **kwargs)
            root = parent.winfo_toplevel()
            with modal_dimmer(root).temporarily():
                return method(*args, **kwargs)

        return show
