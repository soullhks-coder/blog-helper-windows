"""Small, shared vector backgrounds for Canvas previews and saved SVG/PNG.

No remote assets or fonts: the same bounded geometry/colors are used for both
renderers, including non-square thumbnails and independently styled cards.
"""
from __future__ import annotations

import math
import re

BACKGROUND_STYLES = ("단색", "그라데이션", "모눈종이", "메모지", "도트", "사선", "줄무늬")
DEFAULT_PATTERN_COLOR = "#94a3b8"


def normalize_style(value):
    return value if isinstance(value, str) and value in BACKGROUND_STYLES else "단색"


def normalize_color(value, fallback=DEFAULT_PATTERN_COLOR):
    value = str(value or "").strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        return "#" + "".join(char * 2 for char in value[1:])
    return value if re.fullmatch(r"#[0-9a-f]{6}", value) else fallback


def _blend(base, accent, amount):
    return "#" + "".join(f"{round(int(base[i:i+2], 16) * (1-amount) + int(accent[i:i+2], 16) * amount):02x}"
                          for i in (1, 3, 5))


def background_shapes(width, height, style="단색", base="#ffffff", accent=DEFAULT_PATTERN_COLOR):
    """Return (Canvas primitive, coordinates, fill, stroke width) tuples."""
    width, height = float(width), float(height)
    if not all(math.isfinite(x) and x > 0 for x in (width, height)):
        raise ValueError("배경의 가로·세로 크기는 양수여야 합니다.")
    style, base, accent = normalize_style(style), normalize_color(base, "#ffffff"), normalize_color(accent)
    shapes = [("rectangle", (0, 0, width, height), base, 0)]
    unit = min(width, height) / 400
    step = max(20 * unit, math.sqrt(width * height / 1600))
    ink = _blend(base, accent, .32)
    if style == "그라데이션":
        for i in range(128):
            shapes.append(("rectangle", (0, height*i/128, width, min(height, height*(i+1)/128+.01)),
                           _blend(base, accent, i/127), 0))
    elif style in {"모눈종이", "메모지"}:
        for row in range(1, math.ceil(height / step)):
            y = row * step
            shapes.append(("line", (0, y, width, y), ink, .75 * unit))
        if style == "모눈종이":
            for column in range(1, math.ceil(width / step)):
                x = column * step
                shapes.append(("line", (x, 0, x, height), ink, .75 * unit))
        else:
            shapes.append(("line", (width*.12, 0, width*.12, height), _blend(base, accent, .5), unit))
    elif style == "도트":
        radius = 1.15 * unit
        for row in range(math.ceil(height / step)):
            for column in range(math.ceil(width / step)):
                x, y = (column+.5)*step, (row+.5)*step
                shapes.append(("oval", (x-radius, y-radius, x+radius, y+radius), ink, 0))
    elif style == "사선":
        for column in range(-math.ceil(height/step), math.ceil(width/step)+1):
            x = column * step
            shapes.append(("line", (x, height, x+height, 0), _blend(base, accent, .19), 1.4*unit))
    elif style == "줄무늬":
        for row in range(0, math.ceil(height / step), 2):
            shapes.append(("rectangle", (0, row*step, width, min(height, (row+1)*step)),
                           _blend(base, accent, .14), 0))
    return shapes


def draw_background(canvas, width, height, style="단색", base="#ffffff", accent=DEFAULT_PATTERN_COLOR):
    for kind, coordinates, color, stroke in background_shapes(width, height, style, base, accent):
        options = {"fill": color, "tags": ("design-background",)}
        if kind == "line":
            options["width"] = stroke
        else:
            options["outline"] = ""
        getattr(canvas, "create_" + kind)(*coordinates, **options)


def background_svg(width, height, style="단색", base="#ffffff", accent=DEFAULT_PATTERN_COLOR):
    if normalize_style(style) == "그라데이션":
        # Native gradients avoid antialias seams between the Canvas preview's
        # adjacent color bands when Quick Look/Chrome rasterizes a saved SVG.
        background_shapes(width, height, style, base, accent)  # Validate dimensions.
        base, accent = normalize_color(base, "#ffffff"), normalize_color(accent)
        return (f'<g data-blog-helper-background-style="그라데이션"><defs>'
                '<linearGradient id="blog-helper-design-gradient" x1="0%" y1="0%" x2="0%" y2="100%">'
                f'<stop offset="0%" stop-color="{base}"/><stop offset="100%" stop-color="{accent}"/>'
                '</linearGradient></defs>'
                f'<rect width="{float(width):g}" height="{float(height):g}" fill="url(#blog-helper-design-gradient)"/></g>')
    elements = [f'<g data-blog-helper-background-style="{normalize_style(style)}">']
    for kind, (x1, y1, x2, y2), color, stroke in background_shapes(width, height, style, base, accent):
        if kind == "line":
            elements.append(f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" stroke="{color}" stroke-width="{stroke:g}"/>')
        elif kind == "oval":
            elements.append(f'<ellipse cx="{(x1+x2)/2:g}" cy="{(y1+y2)/2:g}" rx="{(x2-x1)/2:g}" ry="{(y2-y1)/2:g}" fill="{color}"/>')
        else:
            elements.append(f'<rect x="{x1:g}" y="{y1:g}" width="{x2-x1:g}" height="{y2-y1:g}" fill="{color}"/>')
    return "\n".join(elements + ["</g>"])
