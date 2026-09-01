import ast
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class DailyPublishLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MAIN_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.methods = {
            node.name: node
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _method_source(self, method_name: str) -> str:
        return ast.get_source_segment(self.source, self.methods[method_name]) or ""

    def test_limits_are_normalized_without_blocking_existing_users(self) -> None:
        self.assertEqual(main.normalize_daily_publish_limit(""), 0)
        self.assertEqual(main.normalize_daily_publish_limit("15"), 15)
        self.assertEqual(main.normalize_daily_publish_limit(-3), 0)
        self.assertEqual(main.normalize_daily_publish_limit(5000), 999)

    def test_counts_are_separated_by_tistory_account_and_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            count_file = Path(directory) / "daily-publish-counts.json"
            with (
                patch.object(main, "DAILY_PUBLISH_COUNTS_FILE", count_file),
                patch.object(main.DailyPublishLimitStore, "_today", return_value="2026-09-01"),
            ):
                main.DailyPublishLimitStore.record_success(
                    "tistory", "https://mom.tistory.com/"
                )
                main.DailyPublishLimitStore.record_success(
                    "tistory", "mom.tistory.com"
                )

                self.assertEqual(
                    main.DailyPublishLimitStore.count(
                        "tistory", "https://mom.tistory.com"
                    ),
                    2,
                )
                self.assertEqual(
                    main.DailyPublishLimitStore.count(
                        "tistory", "https://mine.tistory.com"
                    ),
                    0,
                )
                with self.assertRaisesRegex(
                    RuntimeError,
                    "티스토리 오늘 하루 발행 글 수를 초과했습니다",
                ):
                    main.DailyPublishLimitStore.ensure_can_publish(
                        "tistory",
                        "https://mom.tistory.com",
                        2,
                    )

    def test_count_resets_automatically_on_the_next_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            count_file = Path(directory) / "daily-publish-counts.json"
            with patch.object(main, "DAILY_PUBLISH_COUNTS_FILE", count_file):
                with patch.object(
                    main.DailyPublishLimitStore,
                    "_today",
                    return_value="2026-09-01",
                ):
                    main.DailyPublishLimitStore.record_success(
                        "wordpress", "https://example.com|owner"
                    )
                with patch.object(
                    main.DailyPublishLimitStore,
                    "_today",
                    return_value="2026-09-02",
                ):
                    self.assertEqual(
                        main.DailyPublishLimitStore.count(
                            "wordpress", "example.com|owner"
                        ),
                        0,
                    )

    def test_in_progress_publish_reserves_the_last_available_slot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            count_file = Path(directory) / "daily-publish-counts.json"
            with (
                patch.object(main, "DAILY_PUBLISH_COUNTS_FILE", count_file),
                patch.object(main.DailyPublishLimitStore, "_today", return_value="2026-09-01"),
            ):
                reservation = main.DailyPublishLimitStore.reserve_publish(
                    "wordpress",
                    "https://example.com|owner",
                    1,
                )
                try:
                    with self.assertRaisesRegex(RuntimeError, "발행 중 1개 포함"):
                        main.DailyPublishLimitStore.reserve_publish(
                            "wordpress",
                            "example.com/|owner",
                            1,
                        )
                finally:
                    main.DailyPublishLimitStore.cancel_reservation(reservation)

    def test_daily_limits_survive_app_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                wordpress_daily_publish_limit=40,
                tistory_daily_publish_limit=15,
                blogspot_daily_publish_limit=25,
            )
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

        self.assertEqual(loaded.wordpress_daily_publish_limit, 40)
        self.assertEqual(loaded.tistory_daily_publish_limit, 15)
        self.assertEqual(loaded.blogspot_daily_publish_limit, 25)

    def test_wordpress_public_publish_counts_only_successful_public_posts(self) -> None:
        settings = main.WordPressSettings(
            blog_url="example.com/",
            username="owner",
            wordpress_daily_publish_limit=1,
        )
        with tempfile.TemporaryDirectory() as directory:
            count_file = Path(directory) / "daily-publish-counts.json"
            with (
                patch.object(main, "DAILY_PUBLISH_COUNTS_FILE", count_file),
                patch.object(main.DailyPublishLimitStore, "_today", return_value="2026-09-01"),
            ):
                client = main.WordPressClient(settings)
                with patch.object(
                    client,
                    "_request_json",
                    return_value={
                        "id": 1,
                        "link": "https://example.com/post",
                        "status": "publish",
                        "title": {"rendered": "제목"},
                    },
                ):
                    result = client.publish_post(
                        "제목",
                        "본문",
                        None,
                        "publish",
                    )
                    self.assertEqual(result["daily_publish_count"], 1)
                    with self.assertRaisesRegex(RuntimeError, "설정 한도 1개"):
                        client.publish_post("두 번째", "본문", None, "publish")

                    draft = client.publish_post("초안", "본문", None, "draft")
                    self.assertNotIn("daily_publish_count", draft)

    def test_blogspot_public_publish_uses_its_own_daily_limit(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _traceback):
                return False

            def read(self):
                return (
                    b'{"id":"post-1","url":"https://example.blogspot.com/post-1",'
                    b'"title":"Blogspot title"}'
                )

        with tempfile.TemporaryDirectory() as directory:
            count_file = Path(directory) / "daily-publish-counts.json"
            client = main.BlogspotClient(
                blog_id="blog-123",
                access_token="token",
                daily_publish_limit=1,
            )
            with (
                patch.object(main, "DAILY_PUBLISH_COUNTS_FILE", count_file),
                patch.object(main.DailyPublishLimitStore, "_today", return_value="2026-09-01"),
                patch.object(main, "urlopen", return_value=FakeResponse()),
            ):
                result = client.publish_post("제목", "<p>본문</p>", "publish")
                self.assertEqual(result["daily_publish_count"], 1)
                with self.assertRaisesRegex(RuntimeError, "블로그스팟 오늘 하루"):
                    client.publish_post("두 번째", "<p>본문</p>", "publish")

    def test_all_three_ai_tabs_expose_daily_limit_controls(self) -> None:
        self.assertIn(
            "wordpress_daily_publish_limit_entry",
            self._method_source("_build_wordpress_card"),
        )
        self.assertIn(
            "tistory_daily_publish_limit_entry",
            self._method_source("_build_tistory_card"),
        )
        self.assertIn(
            "blogspot_daily_publish_limit_entry",
            self._method_source("_build_blogspot_card"),
        )

    def test_tistory_counts_only_after_real_published_url_is_confirmed(self) -> None:
        worker_source = next(
            ast.get_source_segment(self.source, node) or ""
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "TistoryAutomationWorker"
        )

        self.assertIn("reserve_publish", worker_source)
        self.assertIn('done_payload["published_url"]', worker_source)
        self.assertIn("record_reserved_success", worker_source)

        with tempfile.TemporaryDirectory() as directory:
            count_file = Path(directory) / "daily-publish-counts.json"
            result_queue = queue.Queue()
            worker = main.TistoryAutomationWorker(
                title="첫 글",
                article_html="<p>본문</p>",
                result_queue=result_queue,
                publish_after_input=True,
                public_blog_url="https://mom.tistory.com",
                write_url="https://mom.tistory.com/manage/newpost",
                ads_enabled=False,
                daily_publish_limit=1,
            )
            with (
                patch.object(main, "DAILY_PUBLISH_COUNTS_FILE", count_file),
                patch.object(main.DailyPublishLimitStore, "_today", return_value="2026-09-01"),
                patch.object(main, "GOOGLE_IMAGE_COLLAGE_ENABLED", False),
                patch.object(
                    main,
                    "prepare_tistory_native_attachment_html",
                    return_value=("<p>본문</p>", {}),
                ),
                patch.object(
                    main,
                    "build_tistory_editor_automation_script",
                    return_value="() => ({titleOk: true, bodyOk: true})",
                ),
                patch.object(
                    main,
                    "run_tistory_playwright_automation",
                    return_value=(
                        True,
                        {
                            "message": "공개발행 완료",
                            "published_url": "https://mom.tistory.com/entry/first",
                            "save_mode": main.TISTORY_SAVE_MODE_PUBLISH,
                        },
                    ),
                ),
                patch.object(main, "cleanup_tistory_automation_files"),
            ):
                worker.run()
                event_type, payload = result_queue.get_nowait()

                self.assertEqual(event_type, "tistory_progress")
                while event_type != "tistory_automation_done":
                    event_type, payload = result_queue.get_nowait()
                self.assertEqual(payload["daily_publish_count"], 1)
                self.assertEqual(
                    main.DailyPublishLimitStore.count(
                        "tistory", "https://mom.tistory.com"
                    ),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
