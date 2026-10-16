"""Independent Tistory V2 slide images and keyword-scoped wiki collection.

No publishing or mutation of the original writing workflow happens here.
Network work is confined to the two requested wiki documents and their images.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import tempfile
import threading
import uuid
from datetime import datetime
from functools import lru_cache
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlparse

from PIL import Image, ImageDraw, ImageFont, ImageOps


SLIDE_SIZE = (600, 300)
SLIDE_WIDTH_RANGE = (300, 2400)
SLIDE_HEIGHT_RANGE = (150, 2400)
TEXT_HORIZONTAL_POSITIONS = ("왼쪽", "중앙", "오른쪽")
TEXT_VERTICAL_POSITIONS = ("기본", "위", "중앙", "아래")
SLIDE_FONT_SIZE_RANGE = (6, 96)
SLIDE_FONT_SIZES = {"eyebrow": 10, "title": 26, "summary": 15, "bullets": 14}


def validate_slide_font_size(value) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,3}", str(value).strip()):
        raise ValueError("글자 크기는 6~96 사이의 정수로 입력해 주세요.")
    number = int(str(value).strip())
    if not SLIDE_FONT_SIZE_RANGE[0] <= number <= SLIDE_FONT_SIZE_RANGE[1]:
        raise ValueError("글자 크기는 6~96 사이의 정수로 입력해 주세요.")
    return number


def slide_font_size_overrides(value) -> dict[str, int]:
    """Ignore invalid legacy/user-edited metadata without changing its text."""
    result = {}
    if isinstance(value, dict):
        for key in SLIDE_FONT_SIZES:
            try:
                result[key] = validate_slide_font_size(value[key])
            except (ValueError, KeyError, TypeError):
                pass
    return result


def slide_has_photo(slide: dict, index=1) -> bool:
    asset = slide.get("image") or {}
    return index <= 2 and slide.get("layout") != "공통 스타일" and Path(str(asset.get("path") or "")).is_file()


def slide_font_sizes(slide: dict, index=1) -> dict[str, int]:
    defaults = {**SLIDE_FONT_SIZES, "title": 24 if slide_has_photo(slide, index) else 26}
    return {**defaults, **slide_font_size_overrides(slide.get("font_sizes"))}


def apply_slide_font_sizes(slides: list[dict], sizes: dict) -> None:
    values = slide_font_size_overrides(sizes)
    if not isinstance(sizes, dict) or len(values) != len(sizes):
        raise ValueError("올바른 항목별 글자 크기를 입력해 주세요.")
    for slide in slides:
        if values:
            slide["font_sizes"] = dict(values)


def text_position_defaults(values: dict) -> dict[str, str]:
    stored = values.get("text_position_defaults")
    stored = stored if isinstance(stored, dict) else {}
    return {key: stored.get(key) if stored.get(key) in choices else choices[0]
            for key, choices in (("text_horizontal", TEXT_HORIZONTAL_POSITIONS),
                                 ("text_vertical", TEXT_VERTICAL_POSITIONS))}


def apply_text_positions(slides: list[dict], horizontal: str, vertical: str) -> None:
    """Change only positions, leaving each slide's content and photos intact."""
    if horizontal not in TEXT_HORIZONTAL_POSITIONS or vertical not in TEXT_VERTICAL_POSITIONS:
        raise ValueError("문구 가로·세로 위치를 선택해 주세요.")
    if not slides or any(not isinstance(slide, dict) for slide in slides):
        raise ValueError("슬라이드를 먼저 만들어 주세요.")
    for slide in slides:
        slide.update(text_horizontal=horizontal, text_vertical=vertical)


def validate_slide_size(value=None) -> tuple[int, int]:
    """Validate output pixels before allocating images or touching the editor."""
    if value is None:
        return SLIDE_SIZE
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError("슬라이드 가로·세로 크기를 입력해 주세요.")
    result = []
    for label, item, limits in zip(("가로", "세로"), value, (SLIDE_WIDTH_RANGE, SLIDE_HEIGHT_RANGE)):
        if isinstance(item, bool) or not re.fullmatch(r"[0-9]+", str(item).strip()):
            raise ValueError(f"{label} 크기는 정수(px)로 입력해 주세요.")
        number = int(str(item).strip())
        if not limits[0] <= number <= limits[1]:
            raise ValueError(f"{label} 크기는 {limits[0]}~{limits[1]}px로 입력해 주세요.")
        result.append(number)
    return tuple(result)


def deck_slide_size(deck: dict) -> tuple[int, int]:
    # Old decks have no size metadata and retain their original 600×300 output.
    return validate_slide_size(deck.get("slide_size", SLIDE_SIZE))


def slide_output_size(slide: dict, default=SLIDE_SIZE) -> tuple[int, int]:
    return validate_slide_size(slide.get("slide_size", default))


def deck_slide_sizes(deck: dict) -> list[tuple[int, int]]:
    default = deck_slide_size(deck)
    return [slide_output_size(slide, default) for slide in deck.get("slides", [])]


def validate_slide_sizes(values, count=10) -> list[tuple[int, int]]:
    if not isinstance(values, (tuple, list)) or len(values) != count:
        raise ValueError(f"슬라이드 {count}장의 개별 크기 정보가 필요합니다.")
    if any(not isinstance(size, (tuple, list)) for size in values):
        raise ValueError("각 슬라이드의 가로·세로 크기를 입력해 주세요.")
    return [validate_slide_size(size) for size in values]


THEMES = {
    "스카이": ("#edf4ff", "#2768d1", "#112d52", "#c7dcff"),
    "골드": ("#fff8e6", "#be880e", "#423615", "#f7e2a3"),
    "민트": ("#edf8f3", "#158366", "#153f35", "#bde5d6"),
    "코랄": ("#fff2ec", "#db5634", "#542c23", "#facdbf"),
    "라벤더": ("#f5f0ff", "#7651d2", "#34274f", "#dcd0f9"),
    "다크": ("#202a3b", "#ffd452", "#ffffff", "#35455e"),
}
THEME_CHOICES = (*THEMES, "랜덤")


def assign_deck_themes(deck: dict, theme: str | None = None, *, reshuffle=False) -> bool:
    """Freeze random choices in the manifest; rendering itself never draws lots."""
    selected = deck.get("theme", "스카이") if theme is None else theme
    old_theme = deck.get("theme")
    deck["theme"] = selected if selected in THEME_CHOICES else "스카이"
    changed = old_theme != deck["theme"]
    previous = None
    for slide in deck.get("slides", []):
        if deck["theme"] != "랜덤":
            changed = changed or "color_theme" in slide
            slide.pop("color_theme", None)
        elif reshuffle or not isinstance(slide.get("color_theme"), str) or slide["color_theme"] not in THEMES:
            slide["color_theme"] = random.choice([name for name in THEMES if name != previous])
            changed = True
        previous = slide.get("color_theme")
    return changed
SOURCES = {
    "wikipedia": ("위키백과", "https://ko.wikipedia.org/wiki/", "#mw-content-text .mw-parser-output"),
    "namu": ("나무위키", "https://namu.wiki/w/", "article, .wiki-content"),
}
COPYRIGHT_NOTE = "사진마다 이용 조건이 다릅니다. 발행 전 원본 출처에서 저작권·이용 조건을 확인해 주세요."


class Cancelled(Exception):
    pass


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled("작업을 취소했습니다.")


def wiki_document_url(source: str, keyword: str) -> str:
    keyword = keyword.strip()
    if not keyword or len(keyword) > 200:
        raise ValueError("검색어를 1~200자로 입력해 주세요.")
    return SOURCES[source][1] + quote(keyword, safe="")


def allowed_image_url(source: str, url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower()
    roots = ("wikimedia.org", "wikipedia.org") if source == "wikipedia" else ("namu.wiki",)
    return any(host == root or host.endswith("." + root) for root in roots)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class V2Store:
    def __init__(self, directory: Path):
        self.directory = directory

    def preferences(self) -> dict:
        try:
            payload = json.loads((self.directory / "preferences.json").read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_preferences(self, values: dict) -> None:
        atomic_json(self.directory / "preferences.json", values)

    def slide_position_defaults(self) -> dict[str, str]:
        values = self.preferences()
        # One-time migration for work saved before next-deck defaults existed.
        # Reading another deck later must never replace an explicit preference.
        if "text_position_defaults" not in values and values.get("deck_id"):
            try:
                slides = self.load_deck(values["deck_id"])["slides"]
                if slides and isinstance(slides[0], dict):
                    values = {**values, "text_position_defaults": slides[0]}
            except (OSError, ValueError, TypeError):
                pass
        return text_position_defaults(values)

    def new_deck(self, keyword: str, title: str, body: str, count: int, theme: str) -> dict:
        deck_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        try:
            size = deck_slide_size(self.preferences())
        except ValueError:
            size = SLIDE_SIZE
        positions = self.slide_position_defaults()
        font_sizes = slide_font_size_overrides(self.preferences().get("font_size_defaults"))
        slides = build_slides(title or keyword, body, count)
        apply_text_positions(slides, positions["text_horizontal"], positions["text_vertical"])
        apply_slide_font_sizes(slides, font_sizes)
        deck = {"id": deck_id, "keyword": keyword, "title": title or keyword,
                "body": body, "theme": theme,
                "slide_size": list(size),
                "text_position_defaults": positions,
                "font_size_defaults": font_sizes,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "slides": slides, "images": []}
        assign_deck_themes(deck)
        return deck

    def deck_directory(self, deck: dict) -> Path:
        deck_id = str(deck.get("id", ""))
        if not re.fullmatch(r"[\w-]{1,80}", deck_id):
            raise ValueError("저장된 작업 ID가 올바르지 않습니다.")
        return self.directory / "decks" / deck_id

    def save_deck(self, deck: dict) -> Path:
        path = self.deck_directory(deck) / "deck.json"
        atomic_json(path, deck)
        return path

    def load_deck(self, deck_id: str) -> dict:
        path = self.deck_directory({"id": deck_id}) / "deck.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("id") != deck_id or not isinstance(payload.get("slides"), list):
            raise ValueError("작업 파일 형식이 올바르지 않습니다.")
        return payload

    def list_decks(self) -> list[dict]:
        result = []
        for path in sorted((self.directory / "decks").glob("*/deck.json"), reverse=True):
            try:
                deck = self.load_deck(path.parent.name)
                result.append({"id": deck["id"], "title": deck.get("title", "제목 없음"),
                               "count": len(deck["slides"]), "created_at": deck.get("created_at", "")})
            except (OSError, ValueError, KeyError):
                continue
            if len(result) >= 30:
                break
        return result


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"p", "div", "h1", "h2", "h3", "li", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif tag in {"p", "div", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(value: str) -> str:
    parser = _PlainText()
    parser.feed(value)
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


class _ArticleSections(HTMLParser):
    """Read semantic headings, retaining inline heading and paragraph text."""
    def __init__(self):
        super().__init__()
        self.sections = []
        self.heading_parts = None
        self.heading = ""
        self.heading_level = 0
        self.parts = []
        self.hidden = 0

    def _flush(self):
        text = "\n".join(line.strip() for line in "".join(self.parts).splitlines() if line.strip())
        if self.heading or text:
            self.sections.append({"heading": self.heading, "text": text, "level": self.heading_level})
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden:
            if re.fullmatch(r"h[1-6]", tag):
                self._flush()
                self.heading_parts = []
                self.heading_level = int(tag[1])
            elif tag in {"p", "div", "li", "br", "tr", "blockquote"}:
                self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden:
            if re.fullmatch(r"h[1-6]", tag) and self.heading_parts is not None:
                self.heading = "".join(self.heading_parts).strip()
                self.heading_parts = None
            elif tag in {"p", "div", "li", "tr", "blockquote"}:
                self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            (self.heading_parts if self.heading_parts is not None else self.parts).append(data)


def article_sections(body: str, title: str = "") -> list[dict]:
    """Extract HTML or Markdown subheadings without generating new article text."""
    if re.search(r"<h[1-6]\b", body, re.I):
        parser = _ArticleSections()
        parser.feed(body)
        parser._flush()
        sections = parser.sections
    else:
        sections, parts, heading = [], [], ""
        lines = plain_text(body).splitlines()
        for line in lines:
            match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$", line)
            if match:
                if heading or parts:
                    sections.append({"heading": heading, "text": "\n".join(parts)})
                heading = re.sub(r"\*\*|__", "", match.group(1)).strip()
                parts = []
            else:
                parts.append(line)
        if heading or parts:
            sections.append({"heading": heading, "text": "\n".join(parts)})
    # An introduction belongs with the first subheading, rather than becoming
    # an unrelated cover image. Likewise a body-level article title is not an
    # extra section when the generator also supplied a separate title field.
    while len(sections) > 1 and (sections[0].get("level") == 1 or sections[0]["heading"] in {"", title.strip()}):
        introduction = sections.pop(0)["text"]
        sections[0]["text"] = "\n".join(part for part in (introduction, sections[0]["text"]) if part)
    if not sections:
        sections = [{"heading": title.strip() or "새 슬라이드", "text": ""}]
    return [{"heading": section["heading"] or title.strip() or "새 슬라이드",
             "text": section["text"], "index": index}
            for index, section in enumerate(sections)]


def _split_section_text(text: str, count: int) -> list[str]:
    """Prefer paragraph/sentence/word boundaries, retaining every original word."""
    result, remaining = [], text.strip()
    for index in range(count - 1):
        target = max(1, round(len(remaining) / (count - index)))
        candidates = [match.end() for match in re.finditer(r"\n+|(?<=[.!?。！？])\s+|\s+", remaining)
                      if max(1, target // 2) <= match.end() <= min(len(remaining) - 1, target * 3 // 2)]
        boundary = min(candidates, key=lambda position: abs(position - target)) if candidates else target
        result.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    return result + [remaining]


def build_slides(title: str, body: str, count: int = 10) -> list[dict]:
    """Create an exact-size, ordered deck from the article's subheadings.

    Long sections can span multiple cards; more headings than available cards
    are grouped adjacently. Original section text stays in source_sections so
    compact image text never destroys the editable article or its association.
    """
    count = max(1, min(10, int(count)))
    sections = article_sections(body, title)
    groups = []
    if len(sections) > count:
        for index in range(count):
            selected = sections[index * len(sections) // count:(index + 1) * len(sections) // count]
            groups.append((selected, "\n".join(section["text"] for section in selected), 1, 1))
    else:
        allocations = [1] * len(sections)
        for _ in range(count - len(sections)):
            slot = max(range(len(sections)), key=lambda index: max(1, len(sections[index]["text"])) / allocations[index])
            allocations[slot] += 1
        for section, allocation in zip(sections, allocations):
            for part, content in enumerate(_split_section_text(section["text"], allocation), 1):
                groups.append(([section], content, part, allocation))
    slides = []
    for index, (selected, content, part, allocation) in enumerate(groups):
        points = [re.sub(r"^\s*[-*•]\s+", "", item.strip()) for item in re.split(r"\n+|(?<=[.!?。！？])\s+", content) if item.strip()]
        heading = " · ".join(section["heading"] for section in selected)
        slides.append({"eyebrow": f"핵심 내용 {index + 1}" + (f" · {part}/{allocation}" if allocation > 1 else ""),
                       "title": heading, "summary": points[0] if points else "", "bullets": points[1:],
                       "source_text": content, "source_sections": [dict(section) for section in selected],
                       "image": None, "layout": "사진 왼쪽" if index == 0 else "사진 오른쪽" if index == 1 else "공통 스타일"})
    return slides


def assign_slide_images(slides: list[dict], images: list[dict]) -> None:
    """Use each collected entry once in the first two cards, never cycle them."""
    for index, slide in enumerate(slides):
        slide["image"] = dict(images[index]) if index < min(2, len(images)) else None
        slide["layout"] = ("사진 왼쪽" if index == 0 else "사진 오른쪽") if slide["image"] else "공통 스타일"


def insert_slide_figures(article_html: str, figures: list[str]) -> str:
    """Distribute against ORIGINAL paragraph anchors, never into other images."""
    if not figures:
        return article_html
    image_ranges = [match.span() for match in re.finditer(r"<figure\b[^>]*>.*?</figure>", article_html, re.I | re.S)]
    anchors = [match.end() for match in re.finditer(r"</(?:p|h2|h3|ul|ol|table)>", article_html, re.I)
               if not any(start <= match.start() < end for start, end in image_ranges)]
    if not anchors:
        return article_html + "\n" + "\n".join(figures)
    insertions = {}
    for index, figure in enumerate(figures, 1):
        position = anchors[min(len(anchors) - 1, int(len(anchors) * index / (len(figures) + 1)))]
        insertions.setdefault(position, []).append(figure)
    for position in sorted(insertions, reverse=True):
        article_html = article_html[:position] + "\n".join(insertions[position]) + article_html[position:]
    return article_html


def collage_preview_body(body: str, figures: list[str]) -> str:
    """Preview two adjacent slides per group against the original subheadings."""
    if not re.search(r"</?[a-z][^>]*>", body, re.I):
        body = "\n".join(f"<p>{escape(line)}</p>" for line in body.splitlines() if line.strip())
    groups = ['<div class="v2-collage">' + "".join(figures[i:i + 2]) + '</div>'
              for i in range(0, len(figures), 2)]
    headings = list(re.finditer(r"<h[2-6]\b[^>]*>", body, re.I))
    if not headings:
        return insert_slide_figures(body, groups)
    insertions = {}
    for index, group in enumerate(groups, 1):
        end_index = (index * len(headings) + len(groups) - 1) // len(groups)
        position = headings[end_index].start() if end_index < len(headings) else len(body)
        insertions.setdefault(position, []).append(group)
    for position in sorted(insertions, reverse=True):
        body = body[:position] + "\n".join(insertions[position]) + body[position:]
    return body


@lru_cache(maxsize=32)
def _font(size: int, bold: bool = False):
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / ("malgunbd.ttf" if bold else "malgun.ttf"),
        Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
        Path("/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size, index=6 if bold and "AppleSD" in path.name else 0)
    raise RuntimeError("한글 글꼴을 찾지 못했습니다. 맑은 고딕 또는 Noto Sans CJK 글꼴을 설치해 주세요.")


def wrap_text(text: str, font, width: int, max_lines: int) -> list[str]:
    lines, current = [], ""
    for char in str(text).strip():
        if char == "\n" or (current and font.getlength(current + char) > width):
            lines.append(current.strip())
            current = "" if char == "\n" else char
        else:
            current += char
    if current.strip():
        lines.append(current.strip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and font.getlength(lines[-1] + "…") > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


def _draw_positioned_text(draw, items, dots, text_x, text_width, top, bottom,
                          horizontal="왼쪽", vertical="기본", bullet_indent=15, dot_color="#2768d1"):
    """Position the whole text block within its photo-safe area, not the footer."""
    bullet_width = max((item[3].getlength(item[2]) + bullet_indent for item in items if item[5]), default=0)

    def shift_for_width(width):
        if horizontal == "중앙":
            return (text_width - width) / 2
        if horizontal == "오른쪽":
            return text_width - width
        return 0

    bullet_shift = shift_for_width(bullet_width)
    bounds = [draw.textbbox((x, y), text, font=font) for x, y, text, font, color, bullet in items]
    bounds.extend(dots)
    y_shift = 0
    if bounds and vertical in {"위", "중앙", "아래"}:
        block_top = min(box[1] for box in bounds)
        block_bottom = max(box[3] for box in bounds)
        target_top = top
        if vertical == "중앙":
            target_top += max(0, (bottom - top - (block_bottom - block_top)) / 2)
        elif vertical == "아래":
            target_top += max(0, bottom - top - (block_bottom - block_top))
        y_shift = target_top - block_top
    for left, upper, right, lower in dots:
        draw.ellipse((left + bullet_shift, upper + y_shift, right + bullet_shift, lower + y_shift), fill=dot_color)
    for x, y, text, font, color, bullet in items:
        x = text_x + bullet_indent + bullet_shift if bullet else text_x + shift_for_width(font.getlength(text))
        draw.text((x, y + y_shift), text, font=font, fill=color)


def _sized_slide_text(slide, eyebrow, font_sizes, font, px, text_x, text_width, top, bottom, ink, accent):
    """Lay out custom sizes using actual glyph bounds; never invade the footer."""
    items, dots = [], []
    y = top

    def block(text, key, max_lines, *, bullet=False):
        nonlocal y
        face = font(font_sizes[key], key in {"eyebrow", "title"})
        sample = face.getbbox("가Ag")
        line_height = max(px(1), sample[3] - min(0, sample[1]))
        gap = px(5 if key == "title" else 4)
        available = max(0, int((bottom - y + gap) // (line_height + gap)))
        if not available:
            return
        indent = px(15) if bullet else 0
        first_line = True
        for line in wrap_text(text, face, text_width - indent, min(max_lines, available)):
            bounds = face.getbbox(line)
            # The drawing API uses baseline-relative y offsets, not ink-top.
            items.append((text_x + indent, y - bounds[1], line, face,
                          accent if key == "eyebrow" else ink, bullet))
            if bullet and first_line:
                radius = px(5)
                dot_y = y + max(0, (bounds[3] - bounds[1] - radius) / 2)
                dots.append((text_x, dot_y, text_x + radius, dot_y + radius))
            first_line = False
            y += line_height + gap

    block(eyebrow, "eyebrow", 1)
    y += px(9)
    block(slide.get("title", ""), "title", 2)
    y += px(10)
    bullets = slide.get("bullets") or []
    block(slide.get("summary", ""), "summary", 2 if bullets else 5)
    y += px(8)
    for item in bullets[:3]:
        block(item, "bullets", 2, bullet=True)
        y += px(5)
    return items, dots


def render_slide(slide: dict, theme: str = "스카이", index: int = 1, total: int = 1,
                 show_numbers: bool = True, size=SLIDE_SIZE) -> Image.Image:
    width, height = slide_output_size(slide, size)
    # Draw at the requested resolution, scaling type and spacing uniformly.
    # Wider/taller canvases get extra room; neither text nor photos are stretched.
    scale = min(width / SLIDE_SIZE[0], height / SLIDE_SIZE[1])
    px = lambda value: max(1, round(value * scale))
    font = lambda value, bold=False: _font(px(value), bold)
    vertical_offset = (height - px(300)) // 2
    if theme == "랜덤":
        theme = slide.get("color_theme")
        if not isinstance(theme, str) or theme not in THEMES:
            raise ValueError("슬라이드의 랜덤 색상 테마가 지정되지 않았습니다.")
    background, accent, ink, tint = THEMES.get(theme, THEMES["스카이"])
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    text_items, bullet_dots = [], []
    asset = slide.get("image") or {}
    photo_path = Path(str(asset.get("path") or ""))
    has_photo = slide_has_photo(slide, index)
    left_photo = slide.get("layout") == "사진 왼쪽"
    panel_width = round(width * .35)
    photo_x = 0 if left_photo else width - panel_width
    text_x = panel_width + px(26) if has_photo and left_photo else px(28)
    text_width = width - panel_width - px(54) if has_photo else width - px(56)
    if has_photo:
        decoration = Image.new("RGBA", (panel_width, height), (0, 0, 0, 0))
        deco = ImageDraw.Draw(decoration)
        for box, color in [((-90, 20, 170, 310), ink + "25"),
                           ((30, 70, 270, 280), "#ffffff80"),
                           ((90, -50, 300, 140), accent + "25")]:
            deco.ellipse(tuple(round(value * (panel_width / 210 if i % 2 == 0 else height / 300))
                               for i, value in enumerate(box)), fill=color)
        picture_panel = Image.alpha_composite(Image.new("RGBA", (panel_width, height), tint), decoration).convert("RGB")
        image.paste(picture_panel, (photo_x, 0))
        with Image.open(photo_path) as source:
            photo = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGBA"),
                                     (panel_width - px(28), height - px(66)))
        photo_left, photo_top = photo_x + (panel_width - photo.width) // 2, (height - photo.height) // 2
        image.paste(photo, (photo_left, photo_top), photo)
    else:
        # The remaining cards share one clean text-led design, without empty
        # photo placeholders or repeated wiki photographs.
        draw.rounded_rectangle((px(28), vertical_offset + px(23), px(65), vertical_offset + px(27)), radius=px(2), fill=accent)
        draw.ellipse((width - px(70), -px(52), width + px(50), px(68)), fill=tint)
    draw = ImageDraw.Draw(image)
    eyebrow_y = vertical_offset + px(25 if has_photo else 37)
    eyebrow_text = str(slide.get("eyebrow", ""))
    if not show_numbers:
        eyebrow_text = re.sub(r"\s*·\s*\d+/\d+\s*$", "", eyebrow_text)
        eyebrow_text = re.sub(r"^핵심 내용\s+\d+", "핵심 내용", eyebrow_text)
    eyebrow = wrap_text(eyebrow_text, font(10, True), text_width, 1)
    if eyebrow:
        text_items.append((text_x, eyebrow_y, eyebrow[0], font(10, True), accent, False))
    title_font = font(24 if has_photo else 26, True)
    title_lines = wrap_text(slide.get("title", ""), title_font, text_width, 2)
    y = vertical_offset + px(52 if has_photo else 61)
    for line in title_lines:
        text_items.append((text_x, y, line, title_font, ink, False))
        y += px(32)
    y += px(13)
    bullets = slide.get("bullets") or []
    summary_lines = min(2 if bullets else 5, max(0, (height - px(44) - y) // px(22)))
    if summary_lines:
        for line in wrap_text(slide.get("summary", ""), font(15), text_width, summary_lines):
            text_items.append((text_x, y, line, font(15), ink, False))
            y += px(22)
    y += px(10)
    for bullet in bullets[:3]:
        max_lines = min(2, (height - px(40) - y) // px(21))
        if max_lines < 1:
            break
        bullet_dots.append((text_x, y + px(8), text_x + px(5), y + px(13)))
        for line in wrap_text(bullet, font(14), text_width - px(15), max_lines):
            text_items.append((text_x + px(15), y, line, font(14), ink, True))
            y += px(21)
        y += px(7)
    if slide_font_size_overrides(slide.get("font_sizes")):
        text_items, bullet_dots = _sized_slide_text(
            slide, eyebrow_text, slide_font_sizes(slide, index), font, px, text_x, text_width,
            vertical_offset + px(25 if has_photo else 37), height - px(40), ink, accent,
        )
    _draw_positioned_text(draw, text_items, bullet_dots, text_x, text_width,
                          px(25 if has_photo else 37), height - px(40),
                          slide.get("text_horizontal", "왼쪽"), slide.get("text_vertical", "기본"), px(15), accent)
    if has_photo and asset.get("source"):
        caption = f"사진: {asset['source']} · {asset.get('title', '')}"
        draw.text((photo_x + px(12), height - px(20)),
                  wrap_text(caption, font(8), panel_width - px(24), 1)[0], font=font(8), fill=ink)
    if show_numbers:
        page_number = f"{index} / {total}"
        page_x = text_x + text_width - font(10).getlength(page_number)
        draw.text((page_x, height - px(23)), page_number, font=font(10), fill=ink)
    return image


def export_deck(store: V2Store, deck: dict, cancel=None, progress=lambda message: None) -> list[Path]:
    size = deck_slide_size(deck)
    deck_slide_sizes(deck)  # Validate every override before rendering any file.
    check_cancel(cancel)
    assign_deck_themes(deck)
    directory = store.deck_directory(deck)
    directory.mkdir(parents=True, exist_ok=True)
    slides = deck.get("slides") or []
    if not slides:
        raise ValueError("저장할 슬라이드가 없습니다.")
    paths, figures = [], []
    # Do not overwrite a previously exported deck halfway through a cancelled
    # render. Stage every slide first, then publish the completed local files.
    with tempfile.TemporaryDirectory(prefix=".render-", dir=directory) as staging_directory:
        staging = Path(staging_directory)
        for index, slide in enumerate(slides, 1):
            check_cancel(cancel)
            progress(f"슬라이드 {index}/{len(slides)} 이미지 저장 중...")
            path = directory / f"slide-{index:02d}.png"
            render_slide(slide, deck.get("theme", "스카이"), index, len(slides),
                         deck.get("show_numbers", True), size).save(staging / path.name, "PNG")
            paths.append(path)
            asset = (slide.get("image") or {}) if index <= 2 and slide.get("layout") != "공통 스타일" else {}
            credit = ""
            if asset.get("source_page"):
                credit = f'<figcaption>사진 출처: <a href="{escape(asset["source_page"], quote=True)}">{escape(asset.get("source", ""))}</a> · 이용 조건 확인 필요</figcaption>'
            figures.append(f'<figure><img src="{path.name}" alt="{escape(slide.get("title", ""), quote=True)}" style="width:100%;height:auto">{credit}</figure>')
        check_cancel(cancel)
        preview = collage_preview_body(str(deck.get("body") or ""), figures)
        document = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>' + escape(deck.get("title", ""))
                    + '</title><style>body{max-width:1200px;margin:32px auto;padding:0 20px;font-family:sans-serif;line-height:1.7}'
                    + '.v2-collage{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:24px 0}'
                    + 'figure{margin:0}figcaption{font-size:12px;color:#667085}</style><body><h1>'
                    + escape(deck.get("title", "")) + '</h1>' + preview + '</body></html>')
        (staging / "article.html").write_text(document, encoding="utf-8")
        for path in paths:
            (staging / path.name).replace(path)
        (staging / "article.html").replace(directory / "article.html")
        store.save_deck(deck)
    return paths


_EXTRACT_IMAGES_JS = r"""async ({source}) => {
    let root = document.querySelector(source === 'wikipedia'
        ? '#mw-content-text .mw-parser-output' : 'article, .wiki-content, [role="main"]');
    // Namu's current CSS class names are obfuscated. Locate the document by
    // its semantic title/section structure, not a brittle generated class.
    if (!root && source === 'namu') {
        let node = document.querySelector('h1')?.parentElement;
        while (node && node !== document.body) {
            if (node.querySelector('h2') && node.querySelector('img')) {root = node; break;}
            node = node.parentElement;
        }
    }
    const title = document.querySelector('h1')?.innerText || document.title;
    if (!root) return {title, body_found: false, images: []};
    const all = Array.from(document.images);
    const images = Array.from(root.querySelectorAll('img')).filter(img => {
        let r = img.getBoundingClientRect();
        if (img.dataset.src && r.height < 100) r = img.parentElement.getBoundingClientRect();
        return !img.closest('nav, header, footer, .navbox, .ambox, .metadata, .hatnote, [role="navigation"]')
            && r.width >= 120 && r.height >= 100
            && (img.dataset.src || (img.naturalWidth >= 160 && img.naturalHeight >= 120));
    }).map(img => {
        const key = source + '-' + all.indexOf(img);
        img.setAttribute('data-blog-helper-v2-image', key);
        return {url: new URL(img.dataset.src || img.currentSrc || img.src, document.baseURI).href, alt: img.alt || '',
            key, top: img.getBoundingClientRect().top + window.scrollY};
    });
    images.sort((a, b) => a.top - b.top || a.index - b.index);
    return {title, body_found: true, images};
}"""


def collect_wiki_images(keyword: str, directory: Path, chrome_path: Path, cancel=None,
                        progress=lambda message: None, visible: bool = True) -> tuple[list[dict], list[str]]:
    """One top content image per requested document, not arbitrary web search."""
    from playwright.sync_api import sync_playwright

    check_cancel(cancel)
    directory.mkdir(parents=True, exist_ok=True)
    images, warnings = [], []
    log_path = directory / "sources.jsonl"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(chrome_path), headless=not visible,
                                             args=["--no-first-run", "--no-default-browser-check"])
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 1100}, locale="ko-KR")
            page = context.new_page()
            for source, (label, _, _) in SOURCES.items():
                check_cancel(cancel)
                document_url = wiki_document_url(source, keyword)
                progress(f"{label}: ‘{keyword}’ 문서의 상단 사진을 확인합니다...")
                try:
                    response = page.goto(document_url, wait_until="domcontentloaded", timeout=35000)
                    if response is not None and response.status >= 400:
                        raise RuntimeError(f"사이트 응답 {response.status}")
                    # Lazy images need a short paint/decode interval, not a
                    # network-idle wait that never ends on ad-heavy pages.
                    page.wait_for_timeout(1000)
                    check_cancel(cancel)
                    if urlparse(page.url).hostname not in {"ko.wikipedia.org", "namu.wiki"}:
                        raise RuntimeError("요청한 위키 문서 밖으로 이동했습니다.")
                    metadata = page.evaluate(_EXTRACT_IMAGES_JS, {"source": source})
                    if not metadata.get("body_found"):
                        raise RuntimeError("본문을 찾지 못했습니다. 문서 없음 또는 사이트 접근 확인이 필요합니다.")
                    selected = None
                    for candidate in metadata.get("images", [])[:8]:
                        check_cancel(cancel)
                        url = candidate.get("url", "")
                        if not allowed_image_url(source, url):
                            continue
                        filename = Path(urlparse(url).path).name
                        if re.search(r"^(?:commons-logo|wikipedia-logo|wikimedia-logo)|speaker-icon|magnify-clip|disambig", filename, re.I):
                            continue
                        key = str(candidate.get("key", ""))
                        if not re.fullmatch(source + r"-\d+", key):
                            continue
                        try:
                            # Keep the exact image selected before lazy/ad DOM
                            # updates; a document-wide nth(img) can drift.
                            locator = page.locator(f"img[data-blog-helper-v2-image='{key}']")
                            locator.scroll_into_view_if_needed(timeout=8000)
                            page.wait_for_function("img => img.complete && img.naturalWidth > 0", arg=locator.element_handle(), timeout=8000)
                            result = context.request.get(url, headers={"Referer": page.url}, timeout=12000)
                            if not result.ok or not allowed_image_url(source, result.url):
                                continue
                            content_type = result.headers.get("content-type", "")
                            if not content_type.startswith("image/"):
                                continue
                            data = result.body()
                            if len(data) > 12_000_000:
                                continue
                            with Image.open(io.BytesIO(data)) as original:
                                if original.width * original.height > 30_000_000:
                                    continue
                                photo = ImageOps.exif_transpose(original).convert("RGBA")
                        except (OSError, ValueError):
                            # Rasterize the selected SVG/image itself, never
                            # screenshot the page or a different image slot.
                            data = locator.screenshot(timeout=10000)
                            with Image.open(io.BytesIO(data)) as original:
                                photo = original.convert("RGBA")
                        photo.thumbnail((2048, 2048))
                        digest = hashlib.sha256(photo.tobytes()).hexdigest()
                        path = directory / f"{source}-{digest[:12]}.png"
                        photo.save(path, "PNG")
                        selected = {"path": str(path.resolve()), "source": label, "source_key": source,
                                    "title": str(metadata.get("title", keyword)).strip(), "source_page": page.url,
                                    "image_url": url, "alt": candidate.get("alt", ""), "rights": COPYRIGHT_NOTE}
                        images.append(selected)
                        with log_path.open("a", encoding="utf-8") as log:
                            log.write(json.dumps({"keyword": keyword, **selected}, ensure_ascii=False) + "\n")
                        progress(f"{label}: 상단 사진을 저장했습니다.")
                        break
                    if selected is None:
                        warnings.append(f"{label}: 본문의 사용 가능한 상단 사진을 찾지 못했습니다.")
                except Cancelled:
                    raise
                except Exception as exc:
                    warnings.append(f"{label}: {str(exc)[:220]}")
                    progress(warnings[-1])
        finally:
            browser.close()
    return images, warnings
