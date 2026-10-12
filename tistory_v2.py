"""Independent Tistory V2 slide images and keyword-scoped wiki collection.

No publishing or mutation of the original writing workflow happens here.
Network work is confined to the two requested wiki documents and their images.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
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


SLIDE_SIZE = (1600, 900)
THEMES = {
    "스카이": ("#edf4ff", "#2768d1", "#112d52", "#c7dcff"),
    "골드": ("#fff8e6", "#be880e", "#423615", "#f7e2a3"),
    "민트": ("#edf8f3", "#158366", "#153f35", "#bde5d6"),
    "코랄": ("#fff2ec", "#db5634", "#542c23", "#facdbf"),
    "라벤더": ("#f5f0ff", "#7651d2", "#34274f", "#dcd0f9"),
    "다크": ("#202a3b", "#ffd452", "#ffffff", "#35455e"),
}
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

    def new_deck(self, keyword: str, title: str, body: str, count: int, theme: str) -> dict:
        deck_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        return {"id": deck_id, "keyword": keyword, "title": title or keyword,
                "body": body, "theme": theme if theme in THEMES else "스카이",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "slides": build_slides(title or keyword, body, count), "images": []}

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


def build_slides(title: str, body: str, count: int) -> list[dict]:
    """Group supplied text only; do not invent claims when the article is empty."""
    count = max(1, min(10, int(count)))
    lines = plain_text(body).splitlines()
    slides = [{"eyebrow": "핵심 가이드", "title": title.strip() or "새 슬라이드",
               "summary": lines[0] if lines else "", "bullets": [], "image": None, "layout": "사진 왼쪽"}]
    if count == 1 or not lines:
        return slides
    # Split long paragraphs at sentence boundaries before grouping up to 3
    # talking points per slide. The complete original remains in deck.body.
    points = []
    for line in lines:
        points.extend(part.strip() for part in re.split(r"(?<=[.!?。])\s+", line) if part.strip())
    groups = min(count - 1, max(1, (len(points) + 2) // 3))
    for index in range(groups):
        start, end = index * len(points) // groups, (index + 1) * len(points) // groups
        chunk = points[start:end]
        heading = chunk[0]
        slides.append({"eyebrow": f"핵심 내용 {index + 1}", "title": heading,
                       "summary": "", "bullets": chunk[1:] or chunk,
                       "image": None, "layout": "사진 오른쪽"})
    return slides


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


def render_slide(slide: dict, theme: str = "스카이", index: int = 1, total: int = 1) -> Image.Image:
    background, accent, ink, tint = THEMES.get(theme, THEMES["스카이"])
    image = Image.new("RGB", SLIDE_SIZE, background)
    draw = ImageDraw.Draw(image)
    left_photo = slide.get("layout") == "사진 왼쪽"
    photo_x = 0 if left_photo else 960
    text_x = 704 if left_photo else 88
    text_width = 792
    draw.rectangle((photo_x, 0, photo_x + 640, 900), fill=tint)
    decoration = Image.new("RGBA", (640, 900), (0, 0, 0, 0))
    deco = ImageDraw.Draw(decoration)
    for box, color in [((-120, 90, 410, 790), ink + "35"),
                       ((80, 210, 610, 740), "#ffffffb0"),
                       ((310, -100, 780, 340), accent + "25")]:
        deco.ellipse(box, fill=color)
    picture_panel = Image.alpha_composite(Image.new("RGBA", (640, 900), tint), decoration).convert("RGB")
    image.paste(picture_panel, (photo_x, 0))
    draw = ImageDraw.Draw(image)
    asset = slide.get("image") or {}
    photo_path = Path(str(asset.get("path") or ""))
    if photo_path.is_file():
        with Image.open(photo_path) as source:
            photo = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGBA"), (536, 660))
        px, py = photo_x + (640 - photo.width) // 2, (900 - photo.height) // 2
        image.paste(photo, (px, py), photo)
    else:
        draw.rounded_rectangle((photo_x + 160, 275, photo_x + 480, 605), radius=44, fill=background, outline=accent, width=8)
        draw.text((photo_x + 258, 327), "T", font=_font(156, True), fill=accent)
    draw.text((text_x, 110), str(slide.get("eyebrow", "")), font=_font(25, True), fill=accent)
    title_lines = wrap_text(slide.get("title", ""), _font(64, True), text_width, 3)
    y = 188
    for line in title_lines:
        draw.text((text_x, y), line, font=_font(64, True), fill=ink)
        y += 82
    y += 24
    for line in wrap_text(slide.get("summary", ""), _font(33), text_width, 3):
        draw.text((text_x, y), line, font=_font(33), fill=ink)
        y += 48
    y += 26
    for bullet in (slide.get("bullets") or [])[:3]:
        if y > 726:
            break
        draw.ellipse((text_x, y + 18, text_x + 14, y + 32), fill=accent)
        max_lines = min(2, max(1, (794 - y) // 42))
        for line in wrap_text(bullet, _font(31), text_width - 36, max_lines):
            draw.text((text_x + 36, y), line, font=_font(31), fill=ink)
            y += 42
        y += 22
    if asset.get("source"):
        caption = f"사진: {asset['source']} · {asset.get('title', '')}"
        draw.text((photo_x + 36, 850), wrap_text(caption, _font(17), 560, 1)[0], font=_font(17), fill=ink)
    draw.text((1470, 850), f"{index} / {total}", font=_font(22), fill=ink)
    return image


def export_deck(store: V2Store, deck: dict, cancel=None, progress=lambda message: None) -> list[Path]:
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
            render_slide(slide, deck.get("theme", "스카이"), index, len(slides)).save(staging / path.name, "PNG")
            paths.append(path)
            asset = slide.get("image") or {}
            credit = ""
            if asset.get("source_page"):
                credit = f'<figcaption>사진 출처: <a href="{escape(asset["source_page"], quote=True)}">{escape(asset.get("source", ""))}</a> · 이용 조건 확인 필요</figcaption>'
            figures.append(f'<figure><img src="{path.name}" alt="{escape(slide.get("title", ""), quote=True)}" style="width:100%;height:auto">{credit}</figure>')
        check_cancel(cancel)
        document = ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>' + escape(deck.get("title", ""))
                    + '</title><body style="max-width:960px;margin:32px auto;font-family:sans-serif"><h1>'
                    + escape(deck.get("title", "")) + '</h1>' + "\n".join(figures) + '</body></html>')
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
                        if re.search(r"commons-logo|wiki.*logo|speaker-icon|magnify-clip|disambig", url, re.I):
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
