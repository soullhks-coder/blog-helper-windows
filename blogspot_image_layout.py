"""Blogger resize observations: no browser actions or edits to article HTML."""
from __future__ import annotations

import math
import re

SIZE_TIMEOUT_MS = 30_000
SIZE_POLL_MS = 250
SIZE_STABLE_POLLS = 4  # Four observations spanning at least 750 ms.


def pixels(value) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:px)?", str(value or "").strip())
    number = float(match.group(1)) if match else 0.0
    return number if math.isfinite(number) else 0.0


def verified_size(before: dict, after: dict) -> tuple[float, float] | None:
    if after.get("connected") is False or after.get("complete") is False:
        return None
    for key in ("naturalWidth", "naturalHeight", "renderedWidth", "renderedHeight"):
        if key in after and pixels(after[key]) <= 0:
            return None
    # The source file dimensions are authoritative when available. A resized
    # Google image URL's naturalWidth can itself be only 320 px: do not mistake
    # that preview for a small original and accept an unapplied size selection.
    original = tuple(pixels(before.get(key) or after.get(key)) for key in ("originalWidth", "originalHeight"))
    target = min(640, max(original)) if all(original) else 640
    pairs = (
        (after.get("computedWidth"), after.get("computedHeight")),
        (after.get("styleWidth") or after.get("width"), after.get("styleHeight") or after.get("height")),
        (after.get("renderedWidth"), after.get("renderedHeight")),
    )
    for pair in pairs:
        width, height = map(pixels, pair)
        if not width or not height or max(width, height) < target - 2:
            continue
        if all(original) and abs((width / height) / (original[0] / original[1]) - 1) > 0.03:
            continue  # Width was updated, but height/aspect ratio is still stale.
        return width, height
    return None


def size_summary(snapshot: dict) -> str:
    """Diagnostic dimensions only: never log signed image URLs or post text."""
    return (f"attr={snapshot.get('width', '?')}x{snapshot.get('height', '?')}, "
            f"css={snapshot.get('computedWidth') or snapshot.get('styleWidth', '?')}x"
            f"{snapshot.get('computedHeight') or snapshot.get('styleHeight', '?')}, "
            f"render={snapshot.get('renderedWidth', '?')}x{snapshot.get('renderedHeight', '?')}, "
            f"original={snapshot.get('originalWidth', '?')}x{snapshot.get('originalHeight', '?')}, "
            f"loaded={snapshot.get('complete', '?')}")


class ImageResizeSettler:
    def __init__(self, before: dict):
        self.before = before
        self.key = None
        self.stable = 0
        self.size = None

    def observe(self, after: dict) -> bool:
        self.size = verified_size(self.before, after)
        if self.size is None:
            self.key, self.stable = None, 0
            return False
        key = tuple(after.get(field) for field in (
            "src", "width", "height", "styleWidth", "styleHeight", "computedWidth", "computedHeight",
            "renderedWidth", "renderedHeight", "naturalWidth", "naturalHeight", "complete", "connected"))
        self.stable = self.stable + 1 if key == self.key else 1
        self.key = key
        return self.stable >= SIZE_STABLE_POLLS

    def reset(self):
        self.key, self.stable, self.size = None, 0, None
