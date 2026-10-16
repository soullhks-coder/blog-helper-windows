"""Shared input outlines that native text widgets cannot paint over.

CTk stretches its native Entry vertically into the rounded canvas border.
At fractional UI scales (the app uses 0.82), the canvas rounds its height
down separately, so that native background can hide the bottom outline.
Keep a single-line editor vertically centered and inset instead. Preserve
CTk's normal grid rebuilding for font, theme, border and DPI changes.
"""
from __future__ import annotations

import math

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
