"""Prepare native Tistory V2 slides from the final, already edited article.

This module does not generate or rewrite article text, authenticate, or publish.
The caller supplies the ``DATA_DIR / 'Tistory V2'`` store directory and handles
the normal writing/publishing stages, including native two-image collages.
"""
from __future__ import annotations

import copy
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from PIL import Image

from tistory_v2 import (Cancelled, V2Store, assign_slide_images,
                        collect_wiki_images, deck_slide_size, deck_slide_sizes, export_deck, plain_text,
                        wiki_document_url)
from writing_run import WritingCancelled, check_writing_cancelled


def _export_cancellable(store, deck, cancel_event, report):
    """Translate the renderer's cancellation into the shared writing signal."""
    check_writing_cancelled(cancel_event)
    try:
        paths = export_deck(store, deck, cancel=cancel_event, progress=report)
    except WritingCancelled:
        raise
    except Cancelled:
        check_writing_cancelled(cancel_event)
        raise
    check_writing_cancelled(cancel_event)
    return paths


class _GeneratedFigures(HTMLParser):
    """Locate marked figures without reserializing any authored HTML."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_starts = [0]
        self.line_starts.extend(index + 1 for index, char in enumerate(source) if char == "\n")
        self.figures = []
        self.credit_starts = []
        self.ranges = []

    def _offset(self):
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set(str(attributes.get("class") or "").split())
        if tag == "p" and "blog-helper-tistory-v2-credits" in classes:
            self.credit_starts.append(self._offset())
        if tag != "figure":
            return
        generated = bool(classes & {"blog-helper-cardnews-image", "blog-helper-tistory-v2-image"})
        generated = generated or any(name in attributes for name in (
            "data-blog-helper-cardnews-index", "data-blog-helper-v2-pair"))
        self.figures.append((self._offset(), generated))

    def handle_endtag(self, tag):
        if tag == "p" and self.credit_starts:
            self.ranges.append((self.credit_starts.pop(), self.source.find(">", self._offset()) + 1))
        if tag == "figure" and self.figures:
            start, generated = self.figures.pop()
            end = self.source.find(">", self._offset()) + 1
            if generated and end:
                self.ranges.append((start, end))


def _without_generated_figures(article_html: str) -> str:
    parser = _GeneratedFigures(article_html)
    parser.feed(article_html)
    parser.close()
    # Merge nested marked figures before applying offsets to the source string.
    ranges = []
    for start, end in sorted(parser.ranges):
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(end, ranges[-1][1]))
        else:
            ranges.append((start, end))
    for start, end in reversed(ranges):
        article_html = article_html[:start] + article_html[end:]
    return article_html


def _source_credits(slides: list[dict]) -> str:
    credits = {}
    for slide in slides[:2]:
        if slide.get("layout") == "공통 스타일":
            continue
        asset = slide.get("image") or {}
        url = str(asset.get("source_page") or "")
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if (parsed.scheme != "https" or parsed.hostname not in {"ko.wikipedia.org", "namu.wiki"}
                or parsed.username or parsed.password):
            continue
        label = str(asset.get("source") or "사진 출처")
        credits[url] = f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'
    if not credits:
        return ""
    return '<p class="blog-helper-tistory-v2-credits">사진 출처: ' + " · ".join(credits.values()) + '</p>'


def source_credits_html(deck: dict) -> str:
    return _source_credits(deck.get("slides", [])) if deck.get("show_photo_sources", True) else ""


def deck_matches_article(deck: dict | None, title: str, article_html: str, keyword: str, slide_summaries=None) -> bool:
    """Slide text can differ; the source article must still match stage 3."""
    return bool(deck and len(deck.get("slides", [])) == 10
                and str(deck.get("title", "")).strip() == title.strip()
                and _without_generated_figures(str(deck.get("body", ""))).strip()
                == _without_generated_figures(article_html).strip()
                and str(deck.get("keyword", "")).strip() == str(keyword or title).strip())


def export_edited_slide_deck(deck: dict, title: str, article_html: str, keyword: str,
                           directory: Path, progress=None, cancel_event=None) -> dict:
    """Render the editor snapshot, never recreate slides or recollect photos."""
    check_writing_cancelled(cancel_event)
    if not deck_matches_article(deck, title, article_html, keyword):
        raise ValueError("슬라이드 원본과 현재 글이 다릅니다. ④ 단계에서 최신 본문으로 슬라이드를 다시 만들어 주세요.")
    deck = copy.deepcopy(deck)
    size = deck_slide_size(deck)
    sizes = deck_slide_sizes(deck)
    if any(slide.get("image") for slide in deck["slides"][2:]):
        raise ValueError("사진은 1·2번 슬라이드에만 넣을 수 있습니다.")
    body = _without_generated_figures(article_html)
    if not plain_text(body).strip():
        raise ValueError("슬라이드로 만들 본문이 없습니다.")
    store = V2Store(Path(directory).expanduser().resolve())
    report = progress if progress is not None else lambda message: None
    paths = [Path(path).resolve() for path in _export_cancellable(store, deck, cancel_event, report)]
    if len(paths) != 10 or len(set(paths)) != 10:
        raise ValueError("티스토리 V2 이미지 10장을 모두 저장하지 못했습니다.")
    for path, expected in zip(paths, sizes):
        check_writing_cancelled(cancel_event)
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != expected:
                raise ValueError(f"티스토리 V2 이미지는 {expected[0]}×{expected[1]} PNG여야 합니다: {path.name}")
            image.verify()
    check_writing_cancelled(cancel_event)
    return {"deck": deck, "paths": paths, "slide_size": list(size), "slide_sizes": [list(s) for s in sizes], "article_html": body,
            "source_credits_html": source_credits_html(deck), "warnings": []}


def prepare_slide_deck(
    title: str,
    article_html: str,
    keyword: str,
    directory: Path,
    chrome_path: Path | str,
    progress: Callable[[str], None] | None = None,
    cancel_event=None,
) -> dict:
    """Return ten configured-size PNG Paths and the unchanged, cleaned final article.

    Only explicitly marked generated card/slide figures are removed. Source
    credits are returned separately so the caller can append them *after* its
    existing trailing-tag extraction, without changing tag detection.
    """
    check_writing_cancelled(cancel_event)
    report = progress if progress is not None else lambda message: None
    body = _without_generated_figures(article_html)
    if not plain_text(body).strip():
        raise ValueError("티스토리 V2 슬라이드를 만들 최종 본문이 없습니다.")
    keyword = str(keyword or title).strip()
    wiki_document_url("wikipedia", keyword)
    store = V2Store(Path(directory).expanduser().resolve())
    report("최종 편집 본문으로 티스토리 V2 슬라이드 10장을 준비합니다...")
    check_writing_cancelled(cancel_event)
    deck = store.new_deck(keyword, title, body, 10, store.preferences().get("theme", "스카이"))
    size = deck_slide_size(deck)
    sizes = deck_slide_sizes(deck)
    if len(deck["slides"]) != 10:
        raise ValueError("티스토리 V2 슬라이드는 정확히 10장이어야 합니다.")
    try:
        images, warnings = collect_wiki_images(
            keyword, store.deck_directory(deck) / "images", Path(chrome_path),
            cancel=cancel_event, progress=report,
        )
    except WritingCancelled:
        raise
    except Cancelled:
        check_writing_cancelled(cancel_event)
        raise
    except MemoryError:
        raise
    except Exception as exc:
        check_writing_cancelled(cancel_event)
        images = []
        warnings = [f"위키 사진 수집 실패: {str(exc)[:220]} · 공통 스타일로 계속합니다."]
    check_writing_cancelled(cancel_event)
    warnings = [str(warning) for warning in warnings]
    for warning in warnings:
        report(warning)
    deck["images"] = list(images[:2])
    assign_slide_images(deck["slides"], deck["images"])
    # Rendering and validation errors must reach the publisher: no partial deck
    # should accidentally proceed to native uploads or publication.
    paths = [Path(path).resolve() for path in _export_cancellable(store, deck, cancel_event, report)]
    if len(paths) != 10 or len(set(paths)) != 10:
        raise ValueError("티스토리 V2 이미지 10장을 모두 저장하지 못했습니다.")
    for path, expected in zip(paths, sizes):
        check_writing_cancelled(cancel_event)
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != expected:
                raise ValueError(f"티스토리 V2 이미지는 {expected[0]}×{expected[1]} PNG여야 합니다: {path.name}")
            image.verify()
    check_writing_cancelled(cancel_event)
    report("티스토리 V2 슬라이드 10장 준비 완료 · 2장씩 콜라주 5개로 입력합니다.")
    check_writing_cancelled(cancel_event)
    return {"deck": deck, "paths": paths, "slide_size": list(size), "slide_sizes": [list(s) for s in sizes], "article_html": body,
            "source_credits_html": source_credits_html(deck), "warnings": warnings}
