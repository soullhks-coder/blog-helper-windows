"""Shared modal dimming without an extra native window or screen capture.

Tk has no alpha channel for child windows. Blend the visible background's
colors and images in place so in-app dialogs keep the main window active.
Only restore values we still own: background workers may update the UI while
the dialog is open, and their newer values must not be overwritten.
"""
from __future__ import annotations

import base64
import io
import sys
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
    def __init__(self, root, *, platform_name=None):
        self.root = root
        self.dialogs = []
        self._depth = 0
        self._saved = []
        self._images = []
        self._refresh_job = None
        self._windows = (sys.platform if platform_name is None else platform_name) == "win32"
        self._windows_saved = {}

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
        if self._windows:
            # Snapshot before restoring parents: CTk parent fills propagate to
            # children, including children with newer externally set colors.
            restore = []

            def depth(key):
                widget, level = key[0], 0
                while getattr(widget, "master", None) is not None:
                    widget = widget.master
                    level += 1
                return level

            for key in sorted(self._windows_saved, key=depth):
                getter, setter, original, dimmed, _image = self._windows_saved[key]
                try:
                    current = getter()
                    restore.append((getter, setter, original if current == dimmed else current))
                except (tk.TclError, ValueError):
                    pass
            for getter, setter, value in restore:
                try:
                    if getter() != value:
                        setter(value)
                except (tk.TclError, ValueError):
                    pass
        for getter, setter, original, dimmed in self._saved:
            try:
                if getter() == dimmed:
                    setter(original)
            except (tk.TclError, ValueError):
                pass
        self._saved.clear()
        self._windows_saved.clear()
        self._images.clear()

    def refresh(self):
        if self._windows and self._refresh_job:
            # Manual refreshes must not start additional timer chains.
            try:
                self.root.after_cancel(self._refresh_job)
            except tk.TclError:
                pass
        self._refresh_job = None
        if not self.active:
            return
        if not self._windows:
            self._restore()
        # On Windows, restoring every 500 ms exposes bright intermediate
        # frames. Keep existing fills/images dimmed and only paint changes.
        changes = []
        observed = []

        def unchanged(key, value):
            observed.append(key)
            if not self._windows:
                return False
            previous = self._windows_saved.get(key)
            if previous is not None:
                if value == previous[3]:
                    return True
                # A live UI update replaces the old original, even when the
                # new value is transparent or no longer contains an image.
                self._windows_saved.pop(key)
            return False

        def visit(widget):
            if widget in self.dialogs or isinstance(widget, (tk.Toplevel, ctk.CTkToplevel)):
                return
            if widget is not self.root and not widget.winfo_ismapped():
                return
            # CTk owns its internal canvas/label/entry colors; changing those
            # independently would leave stale native fills after a redraw.
            is_ctk = isinstance(widget, (ctk.CTk, ctk.CTkBaseClass))
            is_scroll_content = isinstance(widget, ctk.CTkScrollableFrame)
            if is_ctk:
                options = ("fg_color", "bg_color", "border_color", "text_color", "text_color_disabled",
                           "hover_color", "button_color", "button_hover_color", "progress_color",
                           "placeholder_text_color", "checkmark_color")
            elif is_scroll_content:
                options = ("background",)
            else:
                options = ("background", "foreground", "insertbackground", "selectbackground")
            for option in options:
                try:
                    if is_scroll_content:
                        # ScrollableFrame is a native inner Frame, but its
                        # public color API delegates to the outer CTkFrame.
                        # Paint this actual content surface too, otherwise
                        # padding between dimmed rows stays bright white.
                        getter = lambda w=widget, o=option: tk.Frame.cget(w, o)
                        setter = lambda value, w=widget, o=option: tk.Frame.configure(w, **{o: value})
                    else:
                        getter = lambda w=widget, o=option: w.cget(o)
                        setter = lambda value, w=widget, o=option: w.configure(**{o: value})
                    original = getter()
                    key = (widget, option)
                    if unchanged(key, original):
                        continue
                    if self._windows and is_ctk and option == "bg_color":
                        # Newly mapped CTk children may inherit an already
                        # dimmed parent fill. Recover its original background
                        # rather than dimming that inherited color twice.
                        parent = getattr(widget, "master", None)
                        parent_fill = self._windows_saved.get((parent, "fg_color"))
                        if parent_fill is not None and original == parent_fill[3]:
                            original = parent_fill[2]
                    dimmed = dim_color(widget, original)
                    if dimmed != original:
                        changes.append((key, getter, setter, original, dimmed, None))
                except (tk.TclError, ValueError):
                    pass
            if is_ctk:
                try:
                    original = widget.cget("image")
                    if not unchanged((widget, "image"), original) and isinstance(original, ctk.CTkImage):
                        dimmed = ctk.CTkImage(
                            light_image=self._dark_image(original.cget("light_image")),
                            dark_image=self._dark_image(original.cget("dark_image")),
                            size=original.cget("size"),
                        )
                        changes.append(((widget, "image"), lambda w=widget: w.cget("image"),
                                        lambda value, w=widget: w.configure(image=value), original, dimmed, dimmed))
                except (tk.TclError, ValueError):
                    pass
            elif isinstance(widget, tk.Canvas):
                self._dim_canvas(widget, changes, unchanged)
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
        for key, getter, setter, original, dimmed, image in changes:
            try:
                setter(dimmed)
                if self._windows:
                    # Keep only the current image per property, not every
                    # historical preview rendered while a popup is open.
                    self._windows_saved[key] = (getter, setter, original, dimmed, image)
                else:
                    self._saved.append((getter, setter, original, dimmed))
                    if image is not None:
                        self._images.append(image)
            except (tk.TclError, ValueError):
                pass
        if self._windows:
            for key in observed:
                record = self._windows_saved.get(key)
                if record is None:
                    continue
                getter, setter, _original, dimmed, _image = record
                try:
                    # CTk parent configuration propagates backgrounds into
                    # children. Reassert our dimmed values, never bright ones.
                    if getter() != dimmed:
                        setter(dimmed)
                except (tk.TclError, ValueError):
                    pass
        # Keep live progress and newly drawn previews dimmed too.
        self._refresh_job = self.root.after(500, self.refresh)

    @staticmethod
    def _dark_image(image):
        return ImageEnhance.Brightness(image).enhance(0.45) if image is not None else None

    def _dim_canvas(self, canvas, changes, unchanged):
        for item in canvas.find_all():
            for option in ("fill", "outline", "image"):
                try:
                    original = canvas.itemcget(item, option)
                    key = (canvas, item, option)
                    if unchanged(key, original):
                        continue
                    if not original:
                        continue
                    image = None
                    dimmed = dim_color(canvas, original)
                    if option == "image":
                        data = canvas.tk.call(original, "data", "-format", "png")
                        if isinstance(data, str):
                            data = base64.b64decode(data)
                        with Image.open(io.BytesIO(data)) as source:
                            dimmed = ImageTk.PhotoImage(self._dark_image(source), master=canvas)
                        image = dimmed
                        dimmed = str(dimmed)
                    if original != dimmed:
                        changes.append((key, lambda c=canvas, i=item, o=option: c.itemcget(i, o),
                                        lambda value, c=canvas, i=item, o=option: c.itemconfigure(i, **{o: value}),
                                        original, dimmed, image))
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
