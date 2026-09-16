from __future__ import annotations

import inspect
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class ManualPublishModeTests(unittest.TestCase):
    def test_auto_publish_defaults_on_for_existing_profiles(self) -> None:
        settings = main.WordPressSettings()
        tistory_profiles = main.normalize_tistory_profiles([])
        blogspot_profiles = main.normalize_blogspot_profiles([])

        self.assertTrue(settings.tistory_auto_publish)
        self.assertTrue(settings.blogspot_auto_publish)
        self.assertTrue(all(item["auto_publish"] for item in tistory_profiles))
        self.assertTrue(all(item["auto_publish"] for item in blogspot_profiles))

    def test_profile_auto_publish_preferences_round_trip_independently(self) -> None:
        tistory_profiles = main.normalize_tistory_profiles([])
        blogspot_profiles = main.normalize_blogspot_profiles([])
        tistory_profiles[1]["auto_publish"] = False
        blogspot_profiles[2]["auto_publish"] = False
        settings = main.WordPressSettings(
            tistory_profiles=tistory_profiles,
            tistory_active_profile="티스토리 2",
            tistory_auto_publish=False,
            blogspot_profiles=blogspot_profiles,
            blogspot_active_profile="블로그스팟 3",
            blogspot_auto_publish=False,
        )

        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(
                    main.PromptFileStore,
                    "load_into",
                    side_effect=lambda value: value,
                ),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

        self.assertFalse(loaded.tistory_auto_publish)
        self.assertFalse(loaded.tistory_profiles[1]["auto_publish"])
        self.assertTrue(loaded.tistory_profiles[0]["auto_publish"])
        self.assertFalse(loaded.blogspot_auto_publish)
        self.assertFalse(loaded.blogspot_profiles[2]["auto_publish"])
        self.assertTrue(loaded.blogspot_profiles[0]["auto_publish"])

    def test_manual_completion_store_targets_platform_and_profile(self) -> None:
        event = main.ManualPublishCompletionStore.begin(
            "tistory", "manual-test-profile"
        )
        try:
            self.assertEqual(
                main.ManualPublishCompletionStore.waiting_scopes("tistory"),
                ["manual-test-profile"],
            )
            self.assertFalse(
                main.ManualPublishCompletionStore.complete(
                    "blogspot", "manual-test-profile"
                )
            )
            self.assertTrue(
                main.ManualPublishCompletionStore.complete(
                    "tistory", "manual-test-profile"
                )
            )
            self.assertTrue(event.wait(0.1))
        finally:
            main.ManualPublishCompletionStore.finish(
                "tistory", "manual-test-profile", event
            )

    def test_settings_cards_expose_toggle_disabled_mode_and_manual_completion(self) -> None:
        tistory_card = inspect.getsource(main.KeywordApp._build_tistory_card)
        blogspot_card = inspect.getsource(main.KeywordApp._build_blogspot_card)
        tistory_handler = inspect.getsource(
            main.KeywordApp._on_tistory_auto_publish_changed
        )
        blogspot_handler = inspect.getsource(
            main.KeywordApp._on_blogspot_auto_publish_changed
        )

        for source in (tistory_card, blogspot_card):
            self.assertIn('text="자동발행"', source)
            self.assertIn("수동으로 완료하기 (직접 발행 후)", source)
        self.assertIn('state="normal" if enabled else "disabled"', tistory_handler)
        self.assertIn('state="normal" if enabled else "disabled"', blogspot_handler)
        self.assertIn("ManualPublishCompletionStore.waiting_scopes", tistory_handler)
        self.assertIn("ManualPublishCompletionStore.waiting_scopes", blogspot_handler)

    def test_automation_stops_after_tags_or_labels_when_auto_publish_is_off(self) -> None:
        tistory_source = inspect.getsource(
            main.run_tistory_playwright_automation
        )
        blogspot_source = inspect.getsource(
            main.run_blogspot_playwright_automation
        )

        self.assertLess(
            tistory_source.index("enter_tistory_tags_native"),
            tistory_source.index("wait_for_manual_publish_completion"),
        )
        self.assertLess(
            blogspot_source.index("fill_blogspot_labels"),
            blogspot_source.index("wait_for_manual_publish_completion"),
        )
        self.assertIn('"tistory",\n                    profile_scope', tistory_source)
        self.assertIn('"blogspot",\n                        profile_scope', blogspot_source)

    def test_tistory_editor_script_never_runs_prompt_publish_actions(self) -> None:
        script = main.build_tistory_editor_automation_script(
            "검수 제목",
            "<p>검수 본문</p>",
            automation_actions=[
                "set_title",
                "set_body",
                "set_tags",
                "click_complete",
                "attach_representative_image",
                "set_publish_now",
                "click_public_publish",
            ],
            publish_after_input=False,
            save_mode=main.TISTORY_SAVE_MODE_PUBLISH,
        )

        actions_json = script.split("const automationActions = ", 1)[1].split(
            ";", 1
        )[0]
        self.assertIn('"set_title"', actions_json)
        self.assertIn('"set_body"', actions_json)
        self.assertNotIn('"set_tags"', actions_json)
        self.assertNotIn('"click_complete"', actions_json)
        self.assertNotIn('"attach_representative_image"', actions_json)
        self.assertNotIn('"set_publish_now"', actions_json)
        self.assertNotIn('"click_public_publish"', actions_json)

    def test_manual_tistory_completion_records_daily_publish_count(self) -> None:
        events: queue.Queue = queue.Queue()
        with tempfile.TemporaryDirectory() as directory:
            count_file = Path(directory) / "daily-publish-counts.json"
            with (
                patch.object(main, "DAILY_PUBLISH_COUNTS_FILE", count_file),
                patch.object(main, "GOOGLE_IMAGE_COLLAGE_ENABLED", False),
                patch.object(
                    main,
                    "run_tistory_playwright_automation",
                    return_value=(
                        True,
                        {
                            "message": "수동 발행 완료",
                            "published_url": "",
                            "save_mode": main.TISTORY_SAVE_MODE_PUBLISH,
                            "manual_completed": True,
                        },
                    ),
                ),
                patch.object(main, "cleanup_tistory_automation_files"),
            ):
                worker = main.TistoryAutomationWorker(
                    "수동 검수 글",
                    "<p>본문</p><p><strong>주요태그:</strong> 검수, 수동발행</p>",
                    events,
                    write_url="https://manual.tistory.com/manage/newpost",
                    public_blog_url="https://manual.tistory.com",
                    daily_publish_limit=1,
                    auto_publish=False,
                )
                worker.run()

                self.assertEqual(
                    main.DailyPublishLimitStore.count(
                        "tistory", "https://manual.tistory.com"
                    ),
                    1,
                )
                event_type, payload = events.get_nowait()
                while event_type != "tistory_automation_done":
                    event_type, payload = events.get_nowait()
                self.assertTrue(payload["manual_completed"])
                self.assertEqual(payload["daily_publish_count"], 1)


if __name__ == "__main__":
    unittest.main()
