"""Offline checks for V2-only, persisted review-before-publish settings."""
import copy
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from tistory_v2 import V2Store
from tistory_v2_writing import TistoryV2WritingControls


class ReviewPublishTests(unittest.TestCase):
    def controls(self, directory, *, review=False):
        # Settings logic is testable without constructing Tk or real Chrome.
        controls = object.__new__(TistoryV2WritingControls)
        controls.api = main
        controls.app = SimpleNamespace(_prompt_sets=lambda: [{
            "id": "v2-prompt", "platform": "tistory",
            "title_prompt": "제목 지침", "article_prompt": "본문 지침",
        }])
        controls.store = V2Store(Path(directory) / "Tistory V2")
        controls.values = controls.store.preferences()
        controls.review_publish_var = Mock()
        controls.review_publish_var.get.return_value = review
        controls.review_publish_switch = Mock()
        controls.selection = lambda: {"profile": "티스토리 2", "prompt_id": "v2-prompt", "thumbnail": 3}
        return controls

    def settings(self, *, auto_publish=True, save_mode=main.TISTORY_SAVE_MODE_PUBLISH):
        return main.WordPressSettings(
            target_platforms=["wordpress"], tistory_auto_publish=True,
            tistory_profiles=[{"blog_url": "https://first.tistory.com"}, {
                "blog_url": "https://second.tistory.com", "auto_publish": auto_publish,
                "save_mode": save_mode, "daily_publish_limit": 15,
            }],
        )

    def test_review_overrides_only_detached_v2_run_even_for_draft_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            controls = self.controls(directory, review=True)
            base = self.settings(save_mode=main.TISTORY_SAVE_MODE_DRAFT)
            before = copy.deepcopy(base)
            run = controls.execution_settings(base)
            self.assertFalse(run.tistory_auto_publish)
            self.assertEqual(run.tistory_save_mode, main.TISTORY_SAVE_MODE_PUBLISH)
            self.assertEqual(run.target_platforms, ["tistory"])
            self.assertEqual(run.tistory_blog_url, "https://second.tistory.com")
            self.assertEqual(run.tistory_daily_publish_limit, 15)
            self.assertEqual(base, before)
            self.assertEqual(run.tistory_profiles, base.tistory_profiles)

    def test_review_off_preserves_each_existing_profile_publish_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            controls = self.controls(directory)
            for enabled in (False, True):
                for mode in (main.TISTORY_SAVE_MODE_DRAFT, main.TISTORY_SAVE_MODE_PUBLISH):
                    with self.subTest(enabled=enabled, mode=mode):
                        run = controls.execution_settings(self.settings(auto_publish=enabled, save_mode=mode))
                        self.assertEqual(run.tistory_auto_publish, enabled)
                        self.assertEqual(run.tistory_save_mode, mode)

    def test_toggle_persists_separately_and_merges_newer_editor_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            controls = self.controls(directory, review=True)
            controls.values = {"theme": "스카이", "thumbnail": 3}
            latest = {"theme": "랜덤", "deck_id": "edited-deck", "show_numbers": False,
                      "blog_preferences": {"티스토리 2": {"thumbnail": 4}}}
            controls.store.save_preferences(latest)
            controls._on_review_publish_toggled()
            self.assertEqual(controls.store.preferences(), {"thumbnail": 3, **latest, "review_publish": True})
            controls.review_publish_switch.configure.assert_called_with(text="검토 발행 ON")
            restored = self.controls(directory)
            self.assertTrue(restored.values["review_publish"])
            controls.review_publish_var.get.return_value = False
            controls._on_review_publish_toggled()
            self.assertFalse(controls.store.preferences()["review_publish"])
            controls.review_publish_switch.configure.assert_called_with(text="검토 발행 OFF")

    def test_later_toggle_does_not_reenable_an_inflight_review_run(self):
        with tempfile.TemporaryDirectory() as directory:
            controls = self.controls(directory, review=True)
            base = self.settings()
            review_run = controls.execution_settings(base)
            controls.review_publish_var.get.return_value = False
            automatic_run = controls.execution_settings(base)
            self.assertFalse(review_run.tistory_auto_publish)
            self.assertTrue(automatic_run.tistory_auto_publish)
            self.assertTrue(base.tistory_auto_publish)

    def test_pipeline_carries_review_boundary_to_native_tistory_worker_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.controls(directory, review=True).execution_settings(self.settings())
            settings.threads_auto_publish = False
            events = queue.Queue()
            slides = {"paths": [root / f"{index}.png" for index in range(10)],
                      "article_html": "<h2>요점</h2><p>본문</p>", "source_credits_html": ""}
            with patch.object(main, "DATA_DIR", root), \
                 patch.object(main, "TISTORY_DRAFT_FILE", root / "draft.html"), \
                 patch.object(main, "require_google_chrome_executable", return_value=Path("/fake/chrome")), \
                 patch("tistory_v2_publish.prepare_slide_deck", return_value=slides):
                main.PublishPipelineWorker(
                    settings, "검토할 글", slides["article_html"], "", "검토", ["검토", "태그"],
                    "slug", "설명", events, tistory_v2=True,
                ).run()
            self.assertFalse(any(kind == "publish_pipeline_error" for kind, _ in events.queue))
            payload = next(value["tistory"] for kind, value in events.queue if kind == "publish_pipeline_done")
            self.assertFalse(payload["auto_publish"])
            self.assertEqual(payload["save_mode"], main.TISTORY_SAVE_MODE_PUBLISH)
            self.assertEqual(payload["profile_scope"], main.TISTORY_PROFILE_SCOPES[1])
            self.assertEqual(payload["tag_names"], ["검토", "태그"])
            self.assertEqual(len(payload["tistory_v2_slide_paths"]), 10)


if __name__ == "__main__":
    unittest.main()
