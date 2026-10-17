"""NBlog image selection must use real metadata, never search-query labels."""
import base64
from contextlib import ExitStack
import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image
import main


TOPIC = "암투병 지예은"


def photo(title="지예은, 갑상선암 투병 고백", key="good"):
    return {"title": title, "image_url": f"https://media.example.com/{key}.jpg",
            "source_url": f"https://news.example.com/article/{key}", "data_url": "image-data",
            "source": "기사", "verification": "article_title_and_representative_image", "matched_topic": TOPIC}


class ImageRelevanceTests(unittest.TestCase):
    def test_original_topic_not_generated_title_shortened_query_or_domain_popularity_is_evidence(self):
        collector = main.NaverBlogImageCollector(TOPIC)
        for caption in ("포켓몬스터 스타팅 티어표", "지예은 기자", "암투병 환자의 치료",
                        "", "관련 최신 사진", "유재석, 바타 최신 소식"):
            with self.subTest(caption=caption):
                self.assertFalse(collector._is_relevant_text("지예은", caption))
        self.assertTrue(collector._is_relevant_text("short query", "지예은 '암 투병' 고백"))
        self.assertTrue(collector._is_relevant_text("", "배우 지예은, 갑상선암 투병 이후 복귀"))
        self.assertFalse(collector._article_title_is_relevant(TOPIC, "접근 차단", TOPIC))
        self.assertEqual(collector._rank_web_image_candidates(TOPIC, [
            {**photo("포켓몬 티어표"), "source_url": "https://news.kbs.co.kr/article/wrong"},
            {**photo(), "source_url": "https://www.bing.com/images/search?q=" + TOPIC},
            photo(),
        ]), [photo()])

    def test_bing_missing_caption_never_gets_the_query_as_a_fake_caption(self):
        collector = main.NaverBlogImageCollector(TOPIC)
        html = '<a m=\'' + json.dumps({"murl": "https://images.example.com/pokemon.jpg",
                                     "purl": "https://example.com/pokemon"}) + '\'></a>'
        with patch.object(collector, "_fetch_search_html", return_value=html), \
             patch.object(collector, "_download_as_data_url") as download:
            self.assertEqual(collector._collect_bing_images(TOPIC, 6), [])
            download.assert_not_called()

    def test_no_relaxed_query_caption_url_only_screenshot_or_generic_fallback(self):
        collector = main.NaverBlogImageCollector(TOPIC)
        with ExitStack() as stack:
            forbidden = [stack.enter_context(patch.object(collector, method, side_effect=AssertionError(method)))
                         for method in ("collect_web", "collect_licensed", "_collect_commons_images",
                                        "_collect_naver_image_search", "_collect_google_web_images",
                                        "_collect_browser_image_elements")]
            news = stack.enter_context(patch.object(collector, "_collect_naver_news_images", return_value=[photo("포켓몬 티어표")]))
            stack.enter_context(patch.object(collector, "_collect_bing_images", return_value=[photo("갑상선 치료")]))
            self.assertEqual(collector.collect_verified([], 6), [])
            self.assertTrue(news.call_args_list)
            self.assertTrue(all(call.kwargs["relaxed"] is False for call in news.call_args_list))
            for mocked in forbidden:
                mocked.assert_not_called()

    def test_actual_reference_photos_come_first_and_missing_count_is_not_padded(self):
        collector = main.NaverBlogImageCollector(TOPIC)
        related = {"title": "지예은 암투병 고백", "url": "https://news.example.com/1"}
        unrelated = {"title": "암투병 지원 제도", "url": "https://news.example.com/2"}
        with patch.object(collector, "collect_article_reference_images", return_value=[photo()]) as refs, \
             patch.object(collector, "_collect_naver_news_images", return_value=[photo()]), \
             patch.object(collector, "_collect_bing_images", return_value=[photo("포켓몬스터 티어표", "bad")]):
            selected = collector.collect_verified([related, unrelated], 6)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["verification"], "article_title_and_representative_image")
        self.assertEqual(refs.call_args.args[1], [related])
        self.assertEqual(refs.call_args.kwargs["image_limit"], 10)

    def test_unrelated_body_recommendations_do_not_become_article_photos(self):
        payload = {"title": "지예은 암투병 고백", "images": [
            {"url": "portrait.jpg", "kind": "og", "alt": ""},
            {"url": "pokemon.jpg", "kind": "body", "alt": "포켓몬스터 티어표"},
            {"url": "reporter.jpg", "kind": "body", "alt": "지예은 기자"},
            {"url": "related.jpg", "kind": "body", "alt": "지예은 암 투병 고백"},
        ]}
        with patch.object(main.GoogleImageCollageCollector, "_extract_article_image_metadata", return_value=payload):
            result = main.NaverBlogImageCollector(TOPIC)._extract_article_image_metadata(object())
        self.assertEqual([image["url"] for image in result["images"]], ["portrait.jpg", "related.jpg"])

    def test_cancellation_propagates_instead_of_running_a_fallback(self):
        cancelled = threading.Event()
        collector = main.NaverBlogImageCollector(TOPIC, cancelled)
        cancelled.set()
        with patch.object(collector, "_collect_naver_news_images") as fetch, \
             self.assertRaises(main.NaverBlogAutomationCancelled):
            collector.collect_verified([], 6)
        fetch.assert_not_called()
        cancelled.clear()
        with patch.object(collector, "_collect_naver_news_images", side_effect=lambda *a, **k: cancelled.set() or []), \
             patch.object(collector, "_collect_bing_images") as fetch, \
             self.assertRaises(main.NaverBlogAutomationCancelled):
            collector.collect_verified([], 6)
        fetch.assert_not_called()


class ImagePackageTests(unittest.TestCase):
    def test_same_second_multi_account_runs_cannot_overwrite_each_others_images(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main.time, "strftime", return_value="20261018-064759"):
            paths = [main._naver_blog_work_directory(directory, TOPIC) for _ in range(6)]
            self.assertEqual(len(set(paths)), 6)
            self.assertTrue(all(path.name.startswith("20261018-064759-암투병_지예은-") for path in paths))

    def test_save_records_real_sources_and_deduplicates_identical_image_bytes(self):
        buffer = io.BytesIO()
        Image.effect_noise((320, 240), 80).convert("RGB").save(buffer, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        found = [{**photo(key=key), "data_url": data_url} for key in ("one", "same-pixels-different-url")]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main.NaverBlogImageCollector, "collect_verified", return_value=found), \
             patch.object(main, "capture_reference_image_region") as screenshot:
            audit, events = [], queue.Queue()
            paths = main.collect_naver_blog_image_files(TOPIC, "포켓몬 뉴스로 오염된 생성 제목", 6,
                                                        Path(directory), events, image_sources=audit)
            self.assertEqual(len(paths), 1)
            self.assertEqual(len(audit), 1)
            self.assertEqual(audit[0]["source_url"], photo(key="one")["source_url"])
            self.assertEqual(len(audit[0]["sha256"]), 64)
            self.assertNotIn("data_url", audit[0])
            self.assertEqual(len(list(Path(directory).glob("image_*.png"))), 1)
            self.assertIn("관련 없는 사진을 추가하지 않습니다", events.queue[-1][1])
            screenshot.assert_not_called()

    def test_zero_verified_images_stops_before_opening_or_publishing_editor(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main.NaverBlogImageCollector, "collect_verified", return_value=[]), \
             patch.object(main, "capture_reference_image_region") as screenshot, \
             self.assertRaisesRegex(RuntimeError, "이미지 수동 모드"):
            main.collect_naver_blog_image_files(TOPIC, "제목", 6, Path(directory), queue.Queue())
        screenshot.assert_not_called()

    def test_six_accounts_use_the_original_topic_despite_different_generated_titles(self):
        buffer = io.BytesIO()
        Image.effect_noise((320, 240), 80).convert("RGB").save(buffer, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        observed = []
        def collect(collector, sources, count):
            observed.append((collector.topic, list(sources)))
            return [{**photo(), "data_url": data_url}]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main.NaverBlogImageCollector, "collect_verified", autospec=True, side_effect=collect):
            for account in range(6):
                root = main._naver_blog_work_directory(directory, TOPIC)
                audit = []
                paths = main.collect_naver_blog_image_files(TOPIC, f"계정{account}별 다른 제목", 6,
                                                            root, queue.Queue(), image_sources=audit)
                self.assertEqual(len(paths), 1)
                self.assertEqual(audit[0]["matched_topic"], TOPIC)
                self.assertEqual(Path(paths[0]).parent, root)
        self.assertEqual(observed, [(TOPIC, [])] * 6)

    def test_manifest_keeps_selection_provenance_without_base64_image_data(self):
        reference = "1. 지예은 암투병 고백\n출처: 뉴스 / https://news.example.com/article/1"
        def collect(*args, **kwargs):
            kwargs["image_sources"].append({"path": "saved.png", "source_url": "https://news.example.com/article/1",
                                             "title": "지예은 암투병 고백", "verification": "article_title_and_representative_image"})
            return ["saved.png"]
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(main.AutomationKeywordQueueWorker, "_collect_reference_text_for_keyword", return_value=reference))
            stack.enter_context(patch.object(main, "generate_naver_blog_article", return_value=("제목", "<p>본문</p>", "본문", "test")))
            stack.enter_context(patch.object(main, "generate_naver_blog_tags_with_ai", return_value=["지예은"]))
            stack.enter_context(patch.object(main, "collect_naver_blog_image_files", side_effect=collect))
            payload = main.build_naver_blog_workflow_payload(main.WordPressSettings(), TOPIC, 6, directory, queue.Queue())
            manifest = json.loads((Path(payload["work_dir"]) / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["image_sources"], payload["image_sources"])
            self.assertEqual(manifest["image_count"], 1)
            self.assertNotIn("data_url", str(manifest["image_sources"]))

    def test_automatic_workflow_passes_reference_articles_and_keeps_generated_text_if_photos_fail(self):
        reference = "1. 지예은 암투병 고백\n출처: 뉴스 / https://news.example.com/article/1"
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(main.AutomationKeywordQueueWorker, "_collect_reference_text_for_keyword", return_value=reference))
            stack.enter_context(patch.object(main, "generate_naver_blog_article", return_value=("검토 제목", "<p>본문</p>", "본문", "test")))
            stack.enter_context(patch.object(main, "generate_naver_blog_tags_with_ai", return_value=["지예은"]))
            collect = stack.enter_context(patch.object(main, "collect_naver_blog_image_files", side_effect=RuntimeError("검증 실패")))
            with self.assertRaisesRegex(RuntimeError, "작성한 글과 참고자료"):
                main.build_naver_blog_workflow_payload(main.WordPressSettings(), TOPIC, 6, directory, queue.Queue())
            self.assertEqual(collect.call_args.kwargs["article_sources"][0]["title"], "지예은 암투병 고백")
            work = next(Path(directory).iterdir())
            self.assertEqual((work / "title.txt").read_text(encoding="utf-8"), "검토 제목")
            self.assertEqual((work / "body.txt").read_text(encoding="utf-8"), "본문")
            self.assertEqual((work / "reference.txt").read_text(encoding="utf-8"), reference)


if __name__ == "__main__":
    unittest.main()
