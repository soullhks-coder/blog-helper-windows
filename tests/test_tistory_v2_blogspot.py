"""V2 Blogger routing/pairs/review tests, without real accounts or publication."""
import copy
from contextlib import ExitStack
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from PIL import Image

import main
from image_uploads import webp_upload_files
from tistory_v2 import V2Store
from tistory_v2_publish import prepare_blogspot_collages
from tistory_v2_workflow import TistoryV2Workflow
from tistory_v2_writing import TistoryV2WritingControls
from writing_run import ScopedWritingQueue, WritingCancelled


def prepared_slides(root, *, credits="", body=None):
    paths = []
    for i in range(10):
        path = root / f"slide-{i + 1}.png"
        Image.new("RGB", (60, 30), (i * 20, 80, 120)).save(path)
        paths.append(path)
    return {"paths": paths, "article_html": body or "<h2>제목</h2><p>검토한 본문</p>",
            "source_credits_html": credits}


class BloggerV2SettingsTests(unittest.TestCase):
    def setUp(self):
        self.prompts = [{"id": "t", "platform": "tistory", "title_prompt": "티 제목", "article_prompt": "티 본문"},
                        {"id": "b", "platform": "blogspot", "title_prompt": "B 제목", "article_prompt": "B 본문"}]
        self.base = main.WordPressSettings(target_platforms=["wordpress"],
            blogspot_profiles=[{"blog_url": "https://first.example.com", "blog_id": "111"},
                               {"blog_url": "https://second.example.com", "blog_id": "222",
                                "daily_publish_limit": 15, "save_mode": main.TISTORY_SAVE_MODE_DRAFT}],
            tistory_profiles=[{"blog_url": "https://example.tistory.com"}])
        self.app = SimpleNamespace(wordpress_settings=self.base, _prompt_sets=lambda: self.prompts,
                                   _prompt_set_label=lambda p: p["platform"] + " · " + p["id"])
        self.selection = {"platform": "blogspot", "profile": "블로그스팟 2", "prompt_id": "b", "thumbnail": 4}
        self.controls = object.__new__(TistoryV2WritingControls)
        self.controls.app, self.controls.api = self.app, main
        self.controls.selection = lambda: self.selection
        self.controls.review_publish_var = Mock()
        self.controls.review_publish_var.get.return_value = False

    def test_selected_profile_prompt_and_quota_are_detached_from_normal_writing(self):
        before = copy.deepcopy(self.base)
        settings = self.controls.execution_settings(self.base)
        self.assertEqual(settings.target_platforms, ["blogspot"])
        self.assertEqual(settings.writing_prompt_active_target, "blogspot")
        self.assertEqual(settings.title_prompt_template, "B 제목")
        self.assertEqual(settings.article_prompt_template, "B 본문")
        self.assertEqual(settings.blogspot_active_profile, "블로그스팟 2")
        self.assertEqual(settings.blogspot_blog_id, "222")
        self.assertEqual(settings.blogspot_blog_url, "https://second.example.com")
        self.assertEqual(settings.blogspot_daily_publish_limit, 15)
        self.assertEqual(settings.blogspot_save_mode, main.TISTORY_SAVE_MODE_DRAFT)
        self.assertFalse(settings.inline_images_enabled)
        self.assertEqual(self.base, before)

    def test_review_stops_blogger_before_post_without_changing_tistory(self):
        self.controls.review_publish_var.get.return_value = True
        settings = self.controls.execution_settings(self.base)
        self.assertFalse(settings.blogspot_auto_publish)
        self.assertEqual(settings.blogspot_save_mode, main.TISTORY_SAVE_MODE_PUBLISH)
        self.assertEqual(settings.tistory_auto_publish, self.base.tistory_auto_publish)
        self.controls.review_publish_var.get.return_value = False
        self.assertFalse(settings.blogspot_auto_publish)  # Frozen in-flight setting.
        self.assertTrue(self.controls.execution_settings(self.base).blogspot_auto_publish)

    def test_wrong_platform_prompt_and_missing_profile_fail_closed(self):
        for change in ({"prompt_id": "t"}, {"profile": "등록 안 됨"}, {"platform": "wordpress"}):
            with self.subTest(change=change):
                self.selection = {**self.selection, **change}
                with self.assertRaises(ValueError):
                    self.controls.execution_settings(self.base)
                self.selection = {"platform": "blogspot", "profile": "블로그스팟 2", "prompt_id": "b", "thumbnail": 4}

    def test_summaries_and_generation_options_follow_blogger_not_tistory(self):
        workflow = TistoryV2Workflow(self.app, main)
        options = workflow.options("blogspot")
        self.assertEqual([p["id"] for p in options["prompts"]], ["b"])
        self.assertEqual([p["id"] for p in options["profiles"]], ["블로그스팟 1", "블로그스팟 2"])
        snapshot = workflow.snapshot(self.selection)
        self.assertEqual(snapshot["settings"].article_prompt_template, "B 본문")
        self.assertEqual(snapshot["settings"].target_platforms, ["blogspot"])
        self.assertEqual(snapshot["profile"]["profile_scope"], main.BLOGSPOT_PROFILE_SCOPES[1])

    def test_selected_targets_for_automatic_progress_follow_the_radio(self):
        app = SimpleNamespace(_is_tistory_v2_writing=lambda: True, tistory_v2_controls=self.controls)
        self.assertEqual(main.KeywordApp._selected_writing_targets(app), ["blogspot"])

    def test_queued_or_live_work_prevents_target_change(self):
        for queued in (False, True):
            with self.subTest(queued=queued):
                scope = ScopedWritingQueue(queue.Queue())
                self.app._writing_run_queue = scope
                self.app.result_queue = scope.target
                self.app._sidebar_activity_map = lambda: {"tistory_v2": not queued}
                if queued:
                    scope.put(("article_done", {"title": "이전 작업"}))
                self.controls.refresh = Mock()
                self.controls.store = Mock()
                with patch("tistory_v2_writing.messagebox.showinfo") as notice:
                    self.controls.changed("platform")
                notice.assert_called_once()
                self.controls.refresh.assert_called_once()
                self.controls.store.save_preferences.assert_not_called()


class BloggerV2CollageTests(unittest.TestCase):
    def test_five_pairs_keep_order_all_pixels_and_lossless_webp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepared_slides(root)
            originals = [p.read_bytes() for p in prepared["paths"]]
            result = prepare_blogspot_collages(prepared, root / "pairs", '제목 <테스트>')
            self.assertEqual(len(result["paths"]), 5)
            self.assertEqual(main.extract_inline_image_paths(result["article_html"]), list(map(str, result["paths"])))
            clean, images = main.prepare_blogspot_html_and_images(result["article_html"])
            self.assertEqual(clean.replace("\n", ""), prepared["article_html"])
            self.assertEqual(images, list(map(str, result["paths"])))
            with webp_upload_files(result["paths"]) as converted:
                for i, path in enumerate(converted):
                    with Image.open(path) as image:
                        self.assertEqual(image.format, "WEBP")
                        self.assertEqual(image.size, (120, 30))
                        self.assertEqual(image.getpixel((1, 1)), (i * 40, 80, 120))
                        self.assertEqual(image.getpixel((61, 1)), (i * 40 + 20, 80, 120))
            self.assertEqual(originals, [p.read_bytes() for p in prepared["paths"]])

    def test_individual_sizes_are_not_cropped_or_stretched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepared_slides(root)
            Image.new("RGB", (30, 90), "red").save(prepared["paths"][0])
            result = prepare_blogspot_collages(prepared, root / "pairs", "제목")
            with Image.open(result["paths"][0]) as image:
                self.assertEqual(image.size, (90, 90))
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
                self.assertEqual(image.getpixel((31, 0)), (255, 255, 255))
                self.assertEqual(image.getpixel((31, 31)), (20, 80, 120))

    def test_source_credits_remain_separate_for_tag_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepared_slides(root, credits='<p class="blog-helper-tistory-v2-credits">사진 출처</p>')
            result = prepare_blogspot_collages(prepared, root / "pairs", "제목")
            self.assertNotIn("사진 출처", result["article_html"])
            self.assertEqual(result["source_credits_html"], prepared["source_credits_html"])

    def test_incomplete_duplicate_or_cancelled_decks_never_make_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared = prepared_slides(root)
            for paths in (prepared["paths"][:9], [prepared["paths"][0]] * 10):
                with self.assertRaises(ValueError):
                    prepare_blogspot_collages({**prepared, "paths": paths}, root / "invalid", "제목")
            event = threading.Event()
            event.set()
            with self.assertRaises(WritingCancelled):
                prepare_blogspot_collages(prepared, root / "cancelled", "제목", event)
            self.assertFalse(list((root / "cancelled").glob("*.png")))


class BloggerV2PipelineTests(unittest.TestCase):
    def test_editor_snapshot_routes_only_to_selected_blogger_with_five_pairs_and_labels(self):
        for credits in ("", '<p class="blog-helper-tistory-v2-credits">사진 출처</p>'):
            with self.subTest(credits=bool(credits)), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory)
                prepared = prepared_slides(root, credits=credits, body="<p>짧은 본문</p><p>주요 태그: 요약, 안내</p>")
                scope = ScopedWritingQueue(queue.Queue())
                base = main.WordPressSettings(target_platforms=["blogspot"], blogspot_active_profile="블로그스팟 2",
                    blogspot_profiles=[{}, {"blog_id": "222", "blog_url": "https://second.example.com"}],
                    blogspot_blog_id="222", blogspot_blog_url="https://second.example.com",
                    blogspot_auto_publish=False, blogspot_daily_publish_limit=15, threads_auto_publish=False)
                stack.enter_context(patch.object(main, "DATA_DIR", root))
                stack.enter_context(patch.object(main.DailyPublishLimitStore, "ensure_can_publish"))
                stack.enter_context(patch.object(main, "append_runtime_log"))
                export = stack.enter_context(patch("tistory_v2_publish.export_edited_slide_deck", return_value=prepared))
                regenerate = stack.enter_context(patch("tistory_v2_publish.prepare_slide_deck"))
                exported_pairs = []
                def publish(**kwargs):
                    self.assertEqual(kwargs["blog_id"], "222")
                    self.assertEqual(kwargs["profile_scope"], main.BLOGSPOT_PROFILE_SCOPES[1])
                    self.assertFalse(kwargs["auto_publish"])
                    self.assertEqual(kwargs["daily_publish_limit"], 15)
                    self.assertTrue(kwargs["tistory_v2"])
                    self.assertEqual(kwargs["source_credits_html"], credits)
                    self.assertEqual(main.extract_blogspot_prompt_labels(kwargs["article_html"])[1], ["요약", "안내"])
                    exported_pairs.extend(map(Path, main.extract_inline_image_paths(kwargs["article_html"])))
                    self.assertEqual(len(exported_pairs), 5)
                    self.assertTrue(all(p.is_file() for p in exported_pairs))
                    return {"status": "manual", "manual_completed": True}
                blogger = stack.enter_context(patch.object(main, "run_blogspot_playwright_automation", side_effect=publish))
                main.PublishPipelineWorker(base, "제목", prepared["article_html"], "thumb.png", "키워드", [], "", "",
                    scope, tistory_v2=True, tistory_v2_deck={"edited": True}).run()
                export.assert_called_once()
                regenerate.assert_not_called()
                blogger.assert_called_once()
                self.assertFalse(any(p.exists() for p in exported_pairs))
                self.assertTrue(all(p.exists() for p in prepared["paths"]))
                self.assertFalse([event[1][1] for event in scope.target.queue if event[1][1][0] == "publish_pipeline_error"])
                payload = next(event[1][1][1] for event in scope.target.queue if event[1][1][0] == "publish_pipeline_done")
                self.assertIsNone(payload["tistory"])
                self.assertTrue(payload["blogspot"]["manual_completed"])

    def test_failed_collage_does_not_open_blogger(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            prepared = prepared_slides(root)
            prepared["paths"][-1].unlink()
            stack.enter_context(patch.object(main, "DATA_DIR", root))
            stack.enter_context(patch.object(main.DailyPublishLimitStore, "ensure_can_publish"))
            stack.enter_context(patch.object(main, "append_runtime_log"))
            stack.enter_context(patch("tistory_v2_publish.export_edited_slide_deck", return_value=prepared))
            blogger = stack.enter_context(patch.object(main, "run_blogspot_playwright_automation"))
            events = queue.Queue()
            main.PublishPipelineWorker(main.WordPressSettings(target_platforms=["blogspot"]), "제목", "<p>본문</p>",
                "", "키워드", [], "", "", events, tistory_v2=True, tistory_v2_deck={}).run()
            blogger.assert_not_called()
            self.assertTrue(any(kind == "publish_pipeline_error" for kind, _ in events.queue))
            self.assertFalse(list(root.glob("v2-blogspot-*")))


class BloggerV2BrowserTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.prepared = prepared_slides(self.root)
        self.collages = prepare_blogspot_collages(self.prepared, self.root / "pairs", "제목")
        self.thumb = self.root / "thumbnail.png"
        Image.new("RGB", (40, 40), "blue").save(self.thumb)
        self.scope = ScopedWritingQueue(queue.Queue())
        self.page, self.context = MagicMock(), MagicMock()
        self.context.pages = [self.page]
        self.page.url = "https://www.blogger.com/blog/posts/222"
        self.post = MagicMock()
        self.page.get_by_role.side_effect = lambda role, **kwargs: self.post if kwargs.get("name") == "게시" else MagicMock()
        self.stack.enter_context(patch("playwright.sync_api.sync_playwright", return_value=MagicMock()))
        self.stack.enter_context(patch.object(main, "launch_blogspot_persistent_context", return_value=self.context))
        for name in ("save_blogspot_storage_state", "append_runtime_log", "focus_blogspot_image_slot", "fill_blogspot_html_editor"):
            self.stack.enter_context(patch.object(main, name))
        self.stack.enter_context(patch.object(main, "load_blogspot_storage_state", return_value={}))
        self.stack.enter_context(patch.object(main, "is_blogspot_dashboard_ready", return_value=True))
        self.stack.enter_context(patch.object(main, "GOOGLE_IMAGE_COLLAGE_ENABLED", True))
        self.collect = self.stack.enter_context(patch.object(main, "collect_tistory_reference_image_files"))
        editor = MagicMock()
        editor.evaluate.return_value = "<p>완료 본문</p>"
        self.stack.enter_context(patch.object(main, "open_blogspot_html_editor", return_value=editor))
        self.upload = self.stack.enter_context(patch.object(main, "upload_blogspot_images", return_value=1))
        self.tags = self.stack.enter_context(patch.object(main, "fill_blogspot_labels", return_value=2))
        self.wait = self.stack.enter_context(patch.object(main, "wait_for_manual_publish_completion"))
        self.confirm = self.stack.enter_context(patch.object(main, "confirm_blogspot_publish_dialog"))
        self.stack.enter_context(patch.object(main, "fetch_blogspot_post_url_from_feed", return_value="https://second.example.com/post"))
        self.reserve = self.stack.enter_context(patch.object(main.DailyPublishLimitStore, "reserve_publish", return_value="reserved"))
        self.record = self.stack.enter_context(patch.object(main.DailyPublishLimitStore, "record_reserved_success", return_value=1))
        self.stack.enter_context(patch.object(main.DailyPublishLimitStore, "cancel_reservation"))

    def run_browser(self, **kwargs):
        return main.run_blogspot_playwright_automation("제목", self.collages["article_html"] + "<p>주요 태그: 요약, 안내</p>",
            [], self.scope, blog_id="222", blog_url="https://second.example.com", thumbnail_path=str(self.thumb),
            profile_scope=main.BLOGSPOT_PROFILE_SCOPES[1], tistory_v2=True, **kwargs)

    def test_review_uploads_thumbnail_then_five_pairs_and_labels_without_post_click(self):
        result = self.run_browser(auto_publish=False)
        self.collect.assert_not_called()
        self.assertEqual([c.args[1][0] for c in self.upload.call_args_list], [str(self.thumb), *map(str, self.collages["paths"])])
        self.tags.assert_called_once_with(self.page, ["요약", "안내"])
        self.wait.assert_called_once()
        self.post.first.click.assert_not_called()
        self.confirm.assert_not_called()
        self.assertEqual(result["image_count"], 6)
        self.assertTrue(result["manual_completed"])
        self.context.close.assert_called_once()

    def test_automatic_mode_posts_only_after_six_uploads(self):
        result = self.run_browser()
        self.assertEqual(self.upload.call_count, 6)
        self.post.first.click.assert_called_once()
        self.confirm.assert_called_once()
        self.wait.assert_not_called()
        self.assertEqual(result["status"], "publish")
        self.record.assert_called_once()

    def test_partial_upload_or_missing_thumbnail_never_posts(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.thumb.unlink()
                else:
                    self.upload.return_value = 0
                with self.assertRaisesRegex(RuntimeError, "중단"):
                    self.run_browser()
                self.post.first.click.assert_not_called()
                self.confirm.assert_not_called()
                self.wait.assert_not_called()

    def test_cancel_during_third_upload_skips_remaining_pairs_tags_and_post(self):
        def upload(*args):
            if self.upload.call_count == 3:
                self.scope.cancel()
            return 1
        self.upload.side_effect = upload
        with self.assertRaises(WritingCancelled):
            self.run_browser()
        self.assertEqual(self.upload.call_count, 3)
        self.tags.assert_not_called()
        self.post.first.click.assert_not_called()
        self.confirm.assert_not_called()
        self.context.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
