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


def owned_ctk_children(widget):
    """Native drawing/editor children belong to their composite CTk control."""
    return {child for name in ("_canvas", "_label", "_text_label", "_image_label", "_entry", "_textbox")
            if (child := getattr(widget, name, None)) is not None}


class ModalDimmer:
    def __init__(self, root, *, platform_name=None):
        self.root = root
        self.dialogs = []
        self._pending_closes = {}
        self._depth = 0
        self._refresh_job = None
        self._windows = (sys.platform if platform_name is None else platform_name) == "win32"
        # Shared snapshots; retain the diagnostic attribute name used by the
        # Windows no-flicker checks, but snapshot Mac restores atomically too.
        self._windows_saved = {}

    @property
    def active(self):
        return bool(self._depth)

    def attach(self, dialog):
        if dialog in self.dialogs:
            return
        self.dialogs.append(dialog)
        self.begin()
        if isinstance(dialog, ctk.CTkBaseClass):
            # Only the rounded-corner background follows its painted parent;
            # the dialog's foreground/content remains undimmed.
            dialog.configure(bg_color=dialog._detect_color_of_master())

        def closed(event):
            if event.widget is not dialog:
                return
            if dialog in self.dialogs:
                self.dialogs.remove(dialog)

                # Destroy fires before Tk has finished removing the dialog's
                # children. CTk parent.configure() propagates into those dying
                # controls and can raise before restoring its own background.
                # Finish after destruction, not from inside that native event.
                self._pending_closes[dialog] = None
                if hasattr(self.root, "after_idle"):
                    self._pending_closes[dialog] = self.root.after_idle(lambda: self.detach(dialog))
                else:
                    self.detach(dialog)

        # CTkFrame.bind delegates to its internal drawing canvas. We need the
        # frame's own lifecycle event, not the canvas's earlier destruction.
        if isinstance(dialog, ctk.CTkBaseClass):
            tk.Misc.bind(dialog, "<Destroy>", closed, add="+")
        else:
            dialog.bind("<Destroy>", closed, add="+")

    def detach(self, dialog):
        """Release only this dialog; explicit owners call after destroy returns.

        Generic Destroy handlers still defer until idle. A close/navigation
        action can finish restoration immediately after native destruction,
        before another page inherits or redraws temporary dimmed colors.
        """
        if dialog not in self.dialogs and dialog not in self._pending_closes:
            return
        job = self._pending_closes.pop(dialog, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        if dialog in self.dialogs:
            self.dialogs.remove(dialog)
        self.end()
        if self.dialogs:
            try:
                self.dialogs[-1].lift()
                self.dialogs[-1].grab_set()
            except tk.TclError:
                pass

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

    def _restoration_values(self):
        if not self._windows_saved:
            return {}
        # Freeze values before touching parents: their configure() calls can
        # propagate background changes into otherwise independent children.
        values = {}
        for key, (getter, setter, original, dimmed, _image) in self._windows_saved.items():
            try:
                current = getter()
                values[key] = (getter, setter, original if current == dimmed else current)
            except (tk.TclError, ValueError):
                pass

        def value(widget, option, getter):
            record = values.get((widget, option))
            return record[2] if record is not None else getter()

        def visit(widget, parent_surface=None):
            if widget in self.dialogs or isinstance(widget, (tk.Toplevel, ctk.CTkToplevel)):
                return
            is_ctk = isinstance(widget, (ctk.CTk, ctk.CTkBaseClass))
            is_scroll = isinstance(widget, ctk.CTkScrollableFrame)
            option = "bg_color" if is_ctk else "background"
            if is_scroll:
                getter = lambda w=widget: tk.Frame.cget(w, "background")
                setter = lambda color, w=widget: tk.Frame.configure(w, background=color)
            else:
                getter = lambda w=widget, o=option: w.cget(o)
                setter = lambda color, w=widget, o=option: w.configure(**{o: color})
            surface = parent_surface
            try:
                key = (widget, option)
                background = value(widget, option, getter)
                # A newly created (even hidden) control can inherit a dimmed
                # parent. It was never in the original snapshot; do not retain
                # that temporary grey as its permanent rounded-corner fill.
                if key not in values and parent_surface is not None:
                    normal, was_dimmed = parent_surface
                    if was_dimmed:
                        if background != normal and background == dim_color(widget, normal):
                            background = normal
                        # Freeze normal/explicit child backgrounds too. A
                        # transparent CTk parent propagates its OLD background
                        # before updating itself during configure(bg_color=...).
                        # Without a child snapshot that redraw can reintroduce
                        # the dim color even after we restored the parent.
                        values[key] = (getter, setter, background)
                surface = (background, key in values)
            except (tk.TclError, ValueError):
                pass
            if is_ctk:
                try:
                    foreground = value(widget, "fg_color", lambda: widget.cget("fg_color"))
                    if foreground not in (None, "", "transparent"):
                        surface = (foreground, (widget, "fg_color") in values)
                except (tk.TclError, ValueError):
                    pass
            owned = owned_ctk_children(widget) if is_ctk else set()
            try:
                children = widget.winfo_children()
            except tk.TclError:
                return
            for child in children:
                if child not in owned:
                    visit(child, surface)

        visit(self.root)
        return values

    def _restore(self):
        def depth(key):
            widget, level = key[0], 0
            while getattr(widget, "master", None) is not None:
                widget = widget.master
                level += 1
            return level

        restore = self._restoration_values()
        self._apply_values((key, *restore[key]) for key in sorted(restore, key=depth))
        self._windows_saved.clear()

    @staticmethod
    def _apply_values(records):
        """Paint a composite control once, not once per color property.

        CTk.configure redraws immediately; frame colors also propagate into
        descendants. Separate fg/bg/border/text writes multiply that work (and
        the scrollbar's nested update_idletasks). Keep parent-first order and
        compare each child only AFTER its parent has finished propagating.
        Native canvas items/scroll content retain their own setter semantics.
        """
        groups = {}
        for key, getter, setter, target in records:
            widget = key[0]
            composite = len(key) == 2 and isinstance(widget, (ctk.CTk, ctk.CTkBaseClass))
            group = widget if composite else key
            groups.setdefault(group, []).append((key, getter, setter, target))
        for group in groups.values():
            changed = []
            for key, getter, setter, target in group:
                try:
                    if getter() != target:
                        changed.append((key, setter, target))
                except (tk.TclError, ValueError):
                    pass
            if not changed:
                continue
            widget = changed[0][0][0]
            try:
                if len(changed[0][0]) == 2 and isinstance(widget, (ctk.CTk, ctk.CTkBaseClass)):
                    ModalDimmer._configure_colors(widget, {key[1]: target for key, _setter, target in changed})
                else:
                    _key, setter, target = changed[0]
                    setter(target)
            except (tk.TclError, ValueError):
                pass

    @staticmethod
    def _configure_colors(widget, values):
        # Dimming is a temporary visual paint, NOT a theme change. CTk's
        # public frame/root color setters recursively reconfigure ALL children,
        # including hidden pages and the dialog itself. We already snapshot
        # and paint each visible control explicitly, so those cascades are
        # both redundant and harmful. Update only the basic container's own
        # paint state; composite controls/subclasses keep their public API.
        if isinstance(widget, ctk.CTk) and set(values) == {'fg_color'}:
            widget._fg_color = values['fg_color']
            tk.Tk.configure(widget, bg=widget._apply_appearance_mode(widget._fg_color))
        elif (type(widget) is ctk.CTkFrame and set(values) <= {'fg_color', 'bg_color', 'border_color'}
              and all(hasattr(widget, '_' + option) for option in values)):
            for option, value in values.items():
                setattr(widget, '_' + option, value)
            widget._draw()
        else:
            widget.configure(**values)

    def refresh(self):
        if self._refresh_job:
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
        backgrounds = self._restoration_values() if self._windows else {}
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
            try:
                mapped = widget is self.root or widget.winfo_ismapped()
            except tk.TclError:
                return
            if not mapped:
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
                    inherited = backgrounds.get(key)
                    if self._windows and inherited is not None and key not in self._windows_saved:
                        original = inherited[2]
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
            try:
                children = widget.winfo_children()
            except tk.TclError:
                return
            for child in children:
                if is_ctk and child in owned_ctk_children(widget):
                    continue
                visit(child)

        visit(self.root)
        # Collect before applying: CTk frame background changes propagate to
        # children, so reading while painting would compound the dim effect.
        pending = {key: (getter, setter, original, dimmed, image)
                   for key, getter, setter, original, dimmed, image in changes}
        # Even when parent propagation already produced the target, retain
        # the original snapshot so closing restores that child's exact color.
        self._windows_saved.update(pending)
        self._apply_values((key, getter, setter, dimmed)
                           for key, getter, setter, _original, dimmed, _image in changes)
        if self._windows:
            reassert = []
            for key in observed:
                record = self._windows_saved.get(key)
                if record is None:
                    continue
                getter, setter, _original, dimmed, _image = record
                # CTk parent configuration propagates backgrounds into
                # children. Reassert our dimmed values, never bright ones.
                reassert.append((key, getter, setter, dimmed))
            self._apply_values(reassert)
        # Keep live progress and newly drawn previews dimmed too.
        self._refresh_job = self.root.after(500, self.refresh)

    @staticmethod
    def _dark_image(image):
        return ImageEnhance.Brightness(image).enhance(0.45) if image is not None else None

    def _dim_canvas(self, canvas, changes, unchanged):
        try:
            items = canvas.find_all()
        except tk.TclError:
            return
        for item in items:
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
