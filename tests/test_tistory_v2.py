import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

from tistory_v2 import (THEMES, Cancelled, V2Store, allowed_image_url, build_slides,
                        collect_wiki_images, export_deck, plain_text, render_slide,
                        wiki_document_url, wrap_text, _font, insert_slide_figures)


def png_bytes(color):
    output = io.BytesIO()
    Image.new("RGB", (360, 260), color).save(output, "PNG")
    return output.getvalue()


class TistoryV2Tests(unittest.TestCase):
    def test_body_insertion_does_not_nest_images_inside_other_figure_paragraphs(self):
        figures = [f"<figure><p>슬라이드 {index}</p></figure>" for index in range(5)]
        result = insert_slide_figures("<p>원본 문단</p>", figures)
        self.assertEqual(result.count("<figure>"), 5)
        self.assertEqual(result, "<p>원본 문단</p>" + "\n".join(figures))
        original_figure = "<figure><p>기존 사진</p></figure>"
        self.assertIn(original_figure, insert_slide_figures(original_figure, figures))

    def test_keyword_urls_are_encoded_and_do_not_accept_empty_inputs(self):
        self.assertEqual(wiki_document_url("namu", "서울/여행?x=1"), "https://namu.wiki/w/%EC%84%9C%EC%9A%B8%2F%EC%97%AC%ED%96%89%3Fx%3D1")
        with self.assertRaises(ValueError):
            wiki_document_url("wikipedia", "  ")

    def test_downloads_only_accept_the_requested_sources_image_hosts(self):
        for source, good in (("wikipedia", "https://upload.wikimedia.org/example.png"), ("namu", "https://i.namu.wiki/i/photo.png")):
            self.assertTrue(allowed_image_url(source, good))
            for bad in ("https://wikimedia.org.attacker.test/p.png", "file:///tmp/a.png", "http://127.0.0.1/x", "https://user:pass@i.namu.wiki/x"):
                self.assertFalse(allowed_image_url(source, bad))

    def test_original_html_is_converted_without_scripts_or_inventing_content(self):
        self.assertEqual(plain_text("<h2>제목</h2><p>첫 문장</p><script>hide()</script><p>다음 문장</p>"), "제목\n첫 문장\n다음 문장")
        slides = build_slides("서울", "<p>한강을 산책해요.</p><p>지하철을 이용해요.</p>", 10)
        self.assertEqual(len(slides), 2)
        self.assertEqual(slides[1]["title"], "한강을 산책해요.")
        self.assertEqual(slides[1]["bullets"], ["지하철을 이용해요."])
        self.assertEqual(len(build_slides("키워드만", "", 10)), 1)
        self.assertLessEqual(len(build_slides("긴 글", "\n".join("문장입니다." for _ in range(90)), 20)), 10)

    def test_preferences_and_separate_decks_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            store = V2Store(Path(directory))
            self.assertEqual(store.preferences(), {})
            store.save_preferences({"mode": "수동 모드", "keyword": "추석", "theme": "민트"})
            self.assertEqual(store.preferences()["keyword"], "추석")
            first = store.new_deck("서울", "서울 여행", "원본 글", 3, "스카이")
            second = store.new_deck("제주", "제주 여행", "독립 원본", 3, "민트")
            self.assertNotEqual(first["id"], second["id"])
            store.save_deck(first)
            store.save_deck(second)
            self.assertEqual(store.load_deck(first["id"])["body"], "원본 글")
            self.assertEqual(len(store.list_decks()), 2)
            with self.assertRaises(ValueError):
                store.deck_directory({"id": "../../app_state"})

    def test_bad_preferences_do_not_prevent_opening_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            store = V2Store(Path(directory))
            (Path(directory) / "preferences.json").write_text("broken", encoding="utf-8")
            self.assertEqual(store.preferences(), {})

    def test_every_theme_renders_korean_text_and_both_picture_layouts(self):
        slide = {"eyebrow": "시작 가이드", "title": "기본 기능부터 하나씩 익혀요", "summary": "처음 쓰는 분이 놓치기 쉬운 순서를 정리했어요.",
                 "bullets": ["구성품과 준비 단계 먼저 마치기", "기본 기능 하나만 가볍게 시험", "결과가 설명서와 같은지 확인"], "layout": "사진 오른쪽"}
        for theme in THEMES:
            with self.subTest(theme=theme):
                image = render_slide(slide, theme, 4, 10)
                self.assertEqual(image.size, (1600, 900))
                self.assertEqual(image.mode, "RGB")
        slide["layout"] = "사진 왼쪽"
        self.assertEqual(render_slide(slide).size, (1600, 900))

    def test_long_text_is_bounded_and_original_slide_is_not_changed(self):
        text = "긴 제목을 그대로 편집할 수 있어요 " * 20
        lines = wrap_text(text, _font(64, True), 792, 3)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("…"))
        self.assertTrue(all(_font(64, True).getlength(line) <= 792 for line in lines))
        slide = {"title": text, "bullets": [text] * 10}
        render_slide(slide)
        self.assertEqual(slide["title"], text)

    def test_export_has_png_body_preview_source_credit_and_editable_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            store = V2Store(Path(directory))
            photo = Path(directory) / "photo.png"
            photo.write_bytes(png_bytes("#ff0000"))
            deck = store.new_deck("서울", "서울 <여행>", "한강 산책\n준비물 확인", 3, "스카이")
            deck["slides"][0]["image"] = {"path": str(photo), "source": "위키백과", "title": "서울", "source_page": wiki_document_url("wikipedia", "서울")}
            paths = export_deck(store, deck)
            self.assertEqual(len(paths), 2)
            with Image.open(paths[0]) as image:
                self.assertEqual(image.size, (1600, 900))
                # Real chosen photo, not a page screenshot or stock placeholder.
                self.assertEqual(image.getpixel((320, 450)), (255, 0, 0))
            html = (store.deck_directory(deck) / "article.html").read_text(encoding="utf-8")
            self.assertIn("서울 &lt;여행&gt;", html)
            self.assertIn('src="slide-01.png"', html)
            self.assertIn("사진 출처", html)
            self.assertEqual(store.load_deck(deck["id"])["slides"][0]["image"]["path"], str(photo))

    def test_cancelled_export_does_not_claim_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            store = V2Store(Path(directory))
            deck = store.new_deck("서울", "서울", "본문", 3, "스카이")
            cancel = threading.Event()
            cancel.set()
            with self.assertRaises(Cancelled):
                export_deck(store, deck, cancel)
            self.assertFalse((store.deck_directory(deck) / "article.html").exists())

    def test_mid_export_cancellation_preserves_previous_pngs_and_html(self):
        with tempfile.TemporaryDirectory() as directory:
            store = V2Store(Path(directory))
            deck = store.new_deck("서울", "원래 제목", "본문\n다음 본문", 3, "스카이")
            paths = export_deck(store, deck)
            original_png = paths[0].read_bytes()
            html_path = store.deck_directory(deck) / "article.html"
            original_html = html_path.read_text(encoding="utf-8")
            deck["slides"][0]["title"] = "수정한 제목"
            cancel = threading.Event()
            def cancel_after_first_slide(message):
                if "2/" in message:
                    cancel.set()
            with self.assertRaises(Cancelled):
                export_deck(store, deck, cancel, cancel_after_first_slide)
            self.assertEqual(paths[0].read_bytes(), original_png)
            self.assertEqual(html_path.read_text(encoding="utf-8"), original_html)

    def _collect(self, directory, namu_error=False, svg=False):
        page = MagicMock()
        def goto(url, **kwargs):
            page.url = url
            if namu_error and url.startswith("https://namu.wiki"):
                raise RuntimeError("접근 제한")
            return SimpleNamespace(status=200)
        def metadata(script, args):
            source = args["source"]
            host = "upload.wikimedia.org" if source == "wikipedia" else "i.namu.wiki"
            return {"title": "서울", "body_found": True, "images": [{"url": f"https://{host}/photo.png", "key": source + "-7"}]}
        def download(url, **kwargs):
            if svg and "namu.wiki" in url:
                data = b"<svg/>"
            else:
                data = png_bytes("red" if "wikimedia" in url else "blue")
            return SimpleNamespace(ok=True, url=url, headers={"content-type": "image/svg+xml" if svg and "namu.wiki" in url else "image/png"}, body=lambda: data)
        page.goto.side_effect = goto
        page.evaluate.side_effect = metadata
        page.locator.return_value.screenshot.return_value = png_bytes("blue")
        context = MagicMock()
        context.new_page.return_value = page
        context.request.get.side_effect = download
        browser = MagicMock()
        browser.new_context.return_value = context
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        with patch("playwright.sync_api.sync_playwright") as factory:
            factory.return_value.__enter__.return_value = playwright
            result = collect_wiki_images("서울", directory, Path("chrome"))
        self.assertTrue(browser.close.called)
        self.assertEqual([call.args[0] for call in page.goto.call_args_list], [wiki_document_url("wikipedia", "서울"), wiki_document_url("namu", "서울")])
        return result, page

    def test_collector_visits_both_requested_documents_and_logs_exact_image_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            (images, warnings), page = self._collect(Path(directory))
            self.assertEqual(len(images), 2)
            self.assertEqual(warnings, [])
            self.assertTrue(all(Path(item["path"]).is_file() for item in images))
            self.assertIn("source_page", (Path(directory) / "sources.jsonl").read_text(encoding="utf-8"))
            self.assertIn("img[data-blog-helper-v2-image='namu-7']", [call.args[0] for call in page.locator.call_args_list])

    def test_one_site_failure_preserves_the_other_site_image_without_web_search(self):
        with tempfile.TemporaryDirectory() as directory:
            (images, warnings), _ = self._collect(Path(directory), namu_error=True)
            self.assertEqual(len(images), 1)
            self.assertIn("나무위키", warnings[0])

    def test_svg_fallback_captures_only_the_exact_marked_image(self):
        with tempfile.TemporaryDirectory() as directory:
            (images, warnings), page = self._collect(Path(directory), svg=True)
            self.assertEqual(len(images), 2)
            self.assertFalse(warnings)
            page.locator.assert_called_with("img[data-blog-helper-v2-image='namu-7']")
            self.assertTrue(page.locator.return_value.screenshot.called)

    def test_collection_cancelled_before_start_does_not_launch_a_browser(self):
        cancel = threading.Event()
        cancel.set()
        with tempfile.TemporaryDirectory() as directory, patch("playwright.sync_api.sync_playwright") as factory:
            with self.assertRaises(Cancelled):
                collect_wiki_images("서울", Path(directory), Path("chrome"), cancel)
            factory.assert_not_called()
