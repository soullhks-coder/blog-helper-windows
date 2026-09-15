from __future__ import annotations

import inspect
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class ServiceMultiProfileTests(unittest.TestCase):
    def test_tistory_profile_publish_limit_uses_value_or_fallback(self) -> None:
        self.assertEqual(
            main.resolve_tistory_publish_limit({"daily_publish_limit": 15}, 30),
            15,
        )
        self.assertEqual(main.resolve_tistory_publish_limit({}, 30), 30)
        self.assertEqual(
            main.resolve_tistory_publish_limit({"daily_publish_limit": None}, 30),
            30,
        )

    def test_tistory_pipeline_handlers_use_profile_limit_resolver(self) -> None:
        manual_source = inspect.getsource(
            main.KeywordApp._handle_publish_pipeline_success
        )
        automatic_source = inspect.getsource(
            main.KeywordApp._handle_automation_publish_success
        )
        self.assertIn("resolve_tistory_publish_limit", manual_source)
        self.assertIn("resolve_tistory_publish_limit", automatic_source)

    def test_queue_poll_reports_callback_failures_instead_of_freezing(self) -> None:
        source = inspect.getsource(main.KeywordApp._poll_queue)
        self.assertIn("except Exception as exc", source)
        self.assertIn('append_runtime_log(\n                "QUEUE"', source)
        self.assertIn('current_event_type == "publish_pipeline_done"', source)

    def test_tistory_and_blogspot_have_three_isolated_profiles(self) -> None:
        tistory = main.normalize_tistory_profiles(
            [],
            legacy={
                "blog_url": "https://mine.tistory.com",
                "daily_publish_limit": 30,
            },
        )
        blogspot = main.normalize_blogspot_profiles(
            [],
            legacy={
                "blog_id": "123",
                "blog_url": "https://mine.blogspot.com",
            },
        )

        self.assertEqual(len(tistory), 3)
        self.assertEqual(len(blogspot), 3)
        self.assertEqual(tistory[0]["blog_url"], "https://mine.tistory.com")
        self.assertEqual(tistory[0]["daily_publish_limit"], 30)
        self.assertEqual(tistory[1]["blog_url"], "")
        self.assertEqual(blogspot[0]["blog_id"], "123")
        self.assertEqual(blogspot[1]["blog_id"], "")
        self.assertEqual(len({item["profile_path"] for item in tistory}), 3)
        self.assertEqual(len({item["profile_path"] for item in blogspot}), 3)

    def test_active_profile_round_trip_restores_per_account_settings(self) -> None:
        tistory_profiles = main.normalize_tistory_profiles([])
        tistory_profiles[1].update(
            {
                "blog_url": "https://mom.tistory.com",
                "write_url": "https://mom.tistory.com/manage/newpost",
                "daily_publish_limit": 15,
                "save_mode": main.TISTORY_SAVE_MODE_DRAFT,
                "last_prompt_id": "tistory-mom",
            }
        )
        blogspot_profiles = main.normalize_blogspot_profiles([])
        blogspot_profiles[2].update(
            {
                "blog_id": "987654",
                "blog_url": "https://third.blogspot.com",
                "blog_name": "세 번째 블로그",
                "daily_publish_limit": 12,
                "last_prompt_id": "blogspot-third",
            }
        )
        settings = main.WordPressSettings(
            tistory_profiles=tistory_profiles,
            tistory_active_profile="티스토리 2",
            tistory_blog_url="https://mom.tistory.com",
            tistory_write_url="https://mom.tistory.com/manage/newpost",
            tistory_daily_publish_limit=15,
            blogspot_profiles=blogspot_profiles,
            blogspot_active_profile="블로그스팟 3",
            blogspot_blog_id="987654",
            blogspot_blog_url="https://third.blogspot.com",
            blogspot_blog_name="세 번째 블로그",
            blogspot_daily_publish_limit=12,
        )

        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

        self.assertEqual(loaded.tistory_active_profile, "티스토리 2")
        self.assertEqual(loaded.tistory_blog_url, "https://mom.tistory.com")
        self.assertEqual(loaded.tistory_daily_publish_limit, 15)
        self.assertEqual(loaded.tistory_save_mode, main.TISTORY_SAVE_MODE_DRAFT)
        self.assertEqual(
            loaded.tistory_profiles[1]["last_prompt_id"], "tistory-mom"
        )
        self.assertEqual(loaded.blogspot_active_profile, "블로그스팟 3")
        self.assertEqual(loaded.blogspot_blog_id, "987654")
        self.assertEqual(loaded.blogspot_daily_publish_limit, 12)
        self.assertEqual(
            loaded.blogspot_profiles[2]["last_prompt_id"], "blogspot-third"
        )

    def test_tistory_footer_tags_are_removed_and_used_instead_of_old_guesses(self) -> None:
        article = (
            "<h2>정치 뉴스</h2><p>본문입니다.</p>"
            "<p><strong>주요태그:</strong> 국회, 정책, 정치 뉴스</p>"
        )
        cleaned, tags = main.extract_tistory_prompt_tags(article)
        self.assertEqual(tags, ["국회", "정책", "정치 뉴스"])
        self.assertNotIn("주요태그", cleaned)

        events: queue.Queue = queue.Queue()
        with (
            patch.object(main, "GOOGLE_IMAGE_COLLAGE_ENABLED", False),
            patch.object(
                main,
                "run_tistory_playwright_automation",
                return_value=(True, "완료"),
            ) as automation,
            patch.object(main, "cleanup_tistory_automation_files"),
        ):
            worker = main.TistoryAutomationWorker(
                "정치 뉴스",
                article,
                events,
                tag_names=["후기", "가격"],
                save_mode=main.TISTORY_SAVE_MODE_DRAFT,
                write_url="https://mine.tistory.com/manage/newpost",
                profile_scope=main.TISTORY_PROFILE_SCOPES[1],
            )
            worker.run()

        script = automation.call_args.args[1]
        self.assertIn('const tagNames = ["국회", "정책", "정치 뉴스"]', script)
        self.assertNotIn('const tagNames = ["후기", "가격"]', script)
        self.assertNotIn("주요태그", script)
        self.assertEqual(
            automation.call_args.kwargs["profile_scope"],
            main.TISTORY_PROFILE_SCOPES[1],
        )

    def test_login_recovery_clicks_kakao_and_first_saved_account(self) -> None:
        source = inspect.getsource(main.advance_tistory_kakao_login)
        wait_source = inspect.getsource(main.wait_for_tistory_editor)
        self.assertIn("카카오계정으로", source)
        self.assertIn("로그인할\\s*카카오계정", source)
        self.assertIn("새로운 계정", source)
        self.assertIn("candidates.nth(index)", source)
        self.assertIn("advance_tistory_kakao_login", wait_source)

    def test_settings_cards_offer_three_profile_choices(self) -> None:
        tistory_source = inspect.getsource(main.KeywordApp._build_tistory_card)
        blogspot_source = inspect.getsource(main.KeywordApp._build_blogspot_card)
        self.assertIn("range(3)", tistory_source)
        self.assertIn("tistory_active_profile_var", tistory_source)
        self.assertIn("로그인/프로필 확인", tistory_source)
        self.assertIn("range(3)", blogspot_source)
        self.assertIn("blogspot_active_profile_var", blogspot_source)


if __name__ == "__main__":
    unittest.main()
