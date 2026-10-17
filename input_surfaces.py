"""Shared input outlines that native text widgets cannot paint over.

CTk stretches its native Entry vertically into the rounded canvas border.
At fractional UI scales (the app uses 0.82), the canvas rounds its height
down separately, so that native background can hide the bottom outline.
Keep a single-line editor vertically centered and inset instead. Preserve
CTk's normal grid rebuilding for font, theme, border and DPI changes.
"""
from __future__ import annotations

import math
import tkinter as tk

import customtkinter as ctk


OUTLINE_WIDTH = 2
MIN_ENTRY_HEIGHT = 32
OUTLINE_COLOR = ("#c9d4e3", "#334760")
INPUT_COLOR = ("#f7f9fc", "#0b1220")
TEXT_COLOR = ("#1f2937", "#e6edf7")
MUTED_COLOR = ("#667085", "#9aa7bb")


def outline_width(value):
    return max(OUTLINE_WIDTH, OUTLINE_WIDTH if value is None else value)


def entry_vertical_inset(border_width, scale):
    return max(2, math.ceil(border_width * scale) + 1)


class InputEntry(ctk.CTkEntry):
    def __init__(self, master, **kwargs):
        kwargs["border_width"] = outline_width(kwargs.get("border_width"))
        kwargs["height"] = max(MIN_ENTRY_HEIGHT, kwargs.get("height", MIN_ENTRY_HEIGHT))
        kwargs.setdefault("border_color", OUTLINE_COLOR)
        kwargs.setdefault("fg_color", INPUT_COLOR)
        kwargs.setdefault("text_color", TEXT_COLOR)
        kwargs.setdefault("placeholder_text_color", MUTED_COLOR)
        super().__init__(master, **kwargs)

    def _create_grid(self):
        super()._create_grid()
        inset = entry_vertical_inset(self._border_width, self._get_widget_scaling())
        self._entry.grid_configure(sticky="ew", pady=(inset, inset))

    def configure(self, require_redraw=False, **kwargs):
        if "border_width" in kwargs:
            kwargs["border_width"] = outline_width(kwargs["border_width"])
        if "height" in kwargs:
            kwargs["height"] = max(MIN_ENTRY_HEIGHT, kwargs["height"])
        super().configure(require_redraw=require_redraw, **kwargs)


class InputTextbox(ctk.CTkTextbox):
    def __init__(self, master, **kwargs):
        kwargs["border_width"] = outline_width(kwargs.get("border_width"))
        kwargs.setdefault("border_color", OUTLINE_COLOR)
        kwargs.setdefault("fg_color", INPUT_COLOR)
        kwargs.setdefault("text_color", TEXT_COLOR)
        super().__init__(master, **kwargs)

    def configure(self, require_redraw=False, **kwargs):
        if "border_width" in kwargs:
            kwargs["border_width"] = outline_width(kwargs["border_width"])
        super().configure(require_redraw=require_redraw, **kwargs)


class InputOptionMenu(ctk.CTkOptionMenu):
    """A real outlined dropdown, including the arrow side of the control.

    CTkOptionMenu hardcodes a zero-width border. Draw the same native CTk
    shape with an inset on both halves; retain its menu, variables and events.
    No wrapper frames or extra windows are needed, so compact header selectors
    and modal background restoration keep their original geometry/lifecycle.
    """

    def __init__(self, master, **kwargs):
        self._border_width = outline_width(kwargs.pop("border_width", None))
        self._border_color = self._check_color_type(kwargs.pop("border_color", OUTLINE_COLOR))
        super().__init__(master, **kwargs)

    def _create_grid(self):
        super()._create_grid()
        inset = entry_vertical_inset(self._border_width, self._get_widget_scaling())
        self._text_label.configure(borderwidth=0)
        self._text_label.grid_configure(pady=(inset, inset))

    def _update_font(self):
        super()._update_font()
        self._create_grid()

    def _draw(self, no_color_updates=False):
        ctk.CTkBaseClass._draw(self, no_color_updates)
        recolor = self._draw_engine.draw_rounded_rect_with_border_vertical_split(
            self._apply_widget_scaling(self._current_width),
            self._apply_widget_scaling(self._current_height),
            self._apply_widget_scaling(self._corner_radius),
            self._apply_widget_scaling(self._border_width),
            self._apply_widget_scaling(self._current_width - self._current_height),
        )
        arrow_recolor = self._draw_engine.draw_dropdown_arrow(
            self._apply_widget_scaling(self._current_width - self._current_height / 2),
            self._apply_widget_scaling(self._current_height / 2),
            self._apply_widget_scaling(self._current_height / 3),
        )
        if not no_color_updates or recolor or arrow_recolor:
            self._canvas.configure(bg=self._apply_appearance_mode(self._bg_color))
            for tag, color in (
                ("border_parts_left", self._border_color),
                ("border_parts_right", self._border_color),
                ("inner_parts_left", self._fg_color),
                ("inner_parts_right", self._button_color),
            ):
                color = self._apply_appearance_mode(color)
                self._canvas.itemconfigure(tag, outline=color, fill=color)
            text = self._text_color_disabled if self._state == tk.DISABLED else self._text_color
            self._text_label.configure(bg=self._apply_appearance_mode(self._fg_color),
                                       fg=self._apply_appearance_mode(text))
            self._canvas.itemconfigure("dropdown_arrow", fill=self._apply_appearance_mode(text))
        self._canvas.update_idletasks()

    def configure(self, require_redraw=False, **kwargs):
        if "border_width" in kwargs:
            self._border_width = outline_width(kwargs.pop("border_width"))
            self._create_grid()
            require_redraw = True
        if "border_color" in kwargs:
            self._border_color = self._check_color_type(kwargs.pop("border_color"))
            require_redraw = True
        super().configure(require_redraw=require_redraw, **kwargs)

    def cget(self, attribute_name):
        if attribute_name == "border_width":
            return self._border_width
        if attribute_name == "border_color":
            return self._border_color
        return super().cget(attribute_name)


class InputComboBox(ctk.CTkComboBox):
    """Editable dropdown with the same complete outline and safe text inset."""

    def __init__(self, master, **kwargs):
        kwargs["border_width"] = outline_width(kwargs.get("border_width"))
        kwargs.setdefault("border_color", OUTLINE_COLOR)
        super().__init__(master, **kwargs)

    def _create_grid(self):
        super()._create_grid()
        inset = entry_vertical_inset(self._border_width, self._get_widget_scaling())
        self._entry.grid_configure(pady=(inset, inset))

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        # CTk paints the right border with the button fill, hiding that edge.
        self._draw_arrow_outline()

    def _draw_arrow_outline(self):
        color = self._apply_appearance_mode(self._border_color)
        self._canvas.itemconfigure("border_parts_right", outline=color, fill=color)

    def _on_enter(self, event=0):
        super()._on_enter(event)
        self._draw_arrow_outline()

    def _on_leave(self, event=0):
        super()._on_leave(event)
        self._draw_arrow_outline()

    def configure(self, require_redraw=False, **kwargs):
        if "border_width" in kwargs:
            kwargs["border_width"] = outline_width(kwargs["border_width"])
        super().configure(require_redraw=require_redraw, **kwargs)
