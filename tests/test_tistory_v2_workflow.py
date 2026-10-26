import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main
from tistory_v2 import Cancelled, V2Store, export_deck
from tistory_v2_workflow import TistoryV2Workflow
from test_tistory_v2_summary import summaries


class TistoryV2WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.settings = main.WordPressSettings(
            gpt_api_key="test-only", preferred_ai_provider="gpt",
            tistory_profiles=[{"blog_url": "https://first.tistory.com"}, {"blog_url": "https://second.tistory.com"}],
        )
        self.prompts = [{"id": "own-tistory", "platform": "tistory", "name": "내 지침",
                         "title_prompt": "내 제목 지침 {keyword}", "article_prompt": "내 본문 지침 {title}"},
                        {"id": "wordpress", "platform": "wordpress", "name": "다른 지침", "title_prompt": "WP", "article_prompt": "WP"}]
        self.app = SimpleNamespace(wordpress_settings=self.settings, _prompt_sets=lambda: self.prompts,
                                   _prompt_set_label=lambda item: "티스토리 · " + item["name"])
        self.workflow = TistoryV2Workflow(self.app, main)
        self.selection = {"prompt_id": "own-tistory", "profile": "티스토리 2", "thumbnail": 3, "model": "GPT API"}

    def test_only_tistory_prompts_and_registered_profiles_with_five_thumbnail_choices(self):
        options = self.workflow.options()
        self.assertEqual(options["prompts"], [{"id": "own-tistory", "label": "티스토리 · 내 지침"}])
        self.assertEqual([profile["label"] for profile in options["profiles"]], ["first.tistory.com", "second.tistory.com"])
        self.assertEqual(len(options["thumbnails"]), 5)

    def test_generation_uses_selected_prompt_and_provider_without_mutating_original_settings(self):
        before = copy.deepcopy(self.settings)
        snapshot = self.workflow.snapshot(self.selection, generation=True)
        article = "".join(f"<h2>소제목 {i}</h2><p>설명 {i}</p>" for i in range(1, 11))
        response = json.dumps({"article_html": article, "slides": summaries()}, ensure_ascii=False)
        with patch.object(main, "generate_text_with_writing_model", side_effect=[("서울 안내", "GPT API"), (response, "GPT API")]) as generate:
            result = self.workflow.generate(snapshot, "서울", "참고 사실", threading.Event(), lambda _: None)
        self.assertEqual(result["title"], "서울 안내")
        self.assertEqual(result["html"].count("<h2>"), 10)
        self.assertIn("내 제목 지침 서울", generate.call_args_list[0].args[1])
        self.assertIn("내 본문 지침 서울 안내", generate.call_args_list[1].args[1])
        self.assertIn("슬라이드 요약 지침", generate.call_args_list[1].args[1])
        self.assertEqual(len(result["slide_summaries"]), 10)
        self.assertFalse(snapshot["settings"].inline_images_enabled)
        self.assertEqual(self.settings, before)

    def test_generation_error_and_cancel_do_not_return_empty_success(self):
        snapshot = self.workflow.snapshot(self.selection, generation=True)
        with patch.object(main, "generate_text_with_writing_model", side_effect=RuntimeError("모델 연결 실패")):
            with self.assertRaisesRegex(RuntimeError, "모델 연결 실패"):
                self.workflow.generate(snapshot, "서울", "", threading.Event(), lambda _: None)
        cancelled = threading.Event()
        cancelled.set()
        with patch.object(main, "generate_text_with_writing_model") as generate:
            with self.assertRaises(Cancelled):
                self.workflow.generate(snapshot, "서울", "", cancelled, lambda _: None)
            generate.assert_not_called()

    def test_deleted_or_other_platform_prompt_is_rejected(self):
        for prompt_id in ("deleted", "wordpress"):
            with self.subTest(prompt_id=prompt_id), self.assertRaisesRegex(ValueError, "프롬프트"):
                self.workflow.snapshot({**self.selection, "prompt_id": prompt_id}, generation=True)

    def test_editor_reuses_profile_login_and_does_not_save_publish_or_reserve_quota(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = V2Store(Path(temporary))
            body = "".join(f"<h2>소제목 {i}</h2><p>설명 {i}</p>" for i in range(1, 11))
            deck = store.new_deck("서울", "서울 안내", body, 10, "스카이")
            deck["slide_size"] = [900, 600]
            paths = export_deck(store, deck)
            page = MagicMock()
            page.is_closed.return_value = False
            page.evaluate.return_value = '{"titleOk":true,"bodyOk":true,"richBodyOk":true}'
            context = MagicMock()
            context.new_page.return_value = page
            playwright = MagicMock()
            session = MagicMock()
            session.__enter__.return_value = playwright
            notify = MagicMock()
            profile = main.normalize_tistory_profiles(self.settings.tistory_profiles)[1]
            snapshot = {"profile": profile, "write_url": "https://second.tistory.com/manage/newpost",
                        "thumbnail": {"svg": "<svg/>", "size": 400}}
            with patch("playwright.sync_api.sync_playwright", return_value=session), \
                 patch.object(main, "launch_tistory_persistent_context", return_value=context) as launch, \
                 patch.object(main, "restore_tistory_session_cookies") as restore, \
                 patch.object(main, "save_tistory_storage_state") as save_session, \
                 patch.object(main, "wait_for_tistory_editor", return_value=(page, "")), \
                 patch.object(main, "render_svg_to_png", return_value=Path(temporary) / "thumb.png"), \
                 patch.object(main, "enter_tistory_tags_native"), \
                 patch.object(main, "append_runtime_log"), \
                 patch("tistory_v2_editor.insert_native_slide_pairs", return_value={"slide_count": 10, "collage_count": 5}) as insert, \
                 patch.object(self.workflow, "_wait_for_browser") as wait, \
                 patch.object(main, "click_tistory_complete_native") as complete, \
                 patch.object(main, "click_tistory_public_publish_native") as publish, \
                 patch.object(main, "click_tistory_draft_save_native") as draft, \
                 patch.object(main.DailyPublishLimitStore, "reserve_publish") as reserve:
                result = self.workflow.fill_editor(snapshot, deck, paths, Path(temporary), threading.Event(), lambda _: None, notify)
            self.assertEqual(result["collage_count"], 5)
            launch.assert_called_once_with(playwright, profile["profile_scope"])
            restore.assert_called_once_with(context, profile["profile_scope"])
            save_session.assert_called_once_with(context, profile["profile_scope"], authenticated=True)
            page.goto.assert_called_once_with(snapshot["write_url"], wait_until="domcontentloaded")
            insert.assert_called_once()
            self.assertEqual(len(insert.call_args.args[1]), 10)
            self.assertEqual(insert.call_args.kwargs["expected_size"], (900, 600))
            notify.assert_called_once()
            self.assertEqual(notify.call_args.args[0], "editor_ready")
            wait.assert_called_once()
            context.close.assert_called_once()
            for operation in (complete, publish, draft, reserve):
                operation.assert_not_called()

    def test_editor_errors_keep_browser_for_inspection_then_close(self):
        # Check the lifecycle separately from native upload tests: missing title/body
        # must not report editor_ready and must leave the tab visible for inspection.
        with tempfile.TemporaryDirectory() as temporary:
            store = V2Store(Path(temporary))
            deck = store.new_deck("서울", "서울 안내", "<p>설명</p>", 10, "스카이")
            paths = export_deck(store, deck)
            page, context = MagicMock(), MagicMock()
            page.is_closed.return_value = False
            page.evaluate.return_value = '{"titleOk":false,"bodyOk":false}'
            context.new_page.return_value = page
            session = MagicMock()
            snapshot = {"profile": main.normalize_tistory_profiles(self.settings.tistory_profiles)[1],
                        "write_url": "https://second.tistory.com/manage/newpost", "thumbnail": {"svg": "<svg/>", "size": 400}}
            notify = MagicMock()
            with patch("playwright.sync_api.sync_playwright", return_value=session), \
                 patch.object(main, "launch_tistory_persistent_context", return_value=context), \
                 patch.object(main, "load_tistory_storage_state", return_value={}), \
                 patch.object(main, "save_tistory_storage_state"), \
                 patch.object(main, "wait_for_tistory_editor", return_value=(page, "")), \
                 patch.object(main, "render_svg_to_png", return_value=Path(temporary) / "thumb.png"), \
                 patch.object(self.workflow, "_wait_for_browser") as wait:
                with self.assertRaisesRegex(RuntimeError, "제목·본문"):
                    self.workflow.fill_editor(snapshot, deck, paths, Path(temporary), threading.Event(), lambda _: None, notify)
            self.assertEqual(notify.call_args.args[0], "editor_attention")
            wait.assert_called_once()
            context.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
