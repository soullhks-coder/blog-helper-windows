from __future__ import annotations

import inspect
import queue
import unittest
from unittest.mock import patch

import main


class ThreadsPlaywrightTests(unittest.TestCase):
    def test_threads_session_cookie_is_extended_and_deduplicated(self) -> None:
        state = {
            "cookies": [
                {
                    "domain": ".threads.com",
                    "path": "/",
                    "name": "sessionid",
                    "value": "one",
                    "expires": -1,
                },
                {
                    "domain": ".threads.com",
                    "path": "/",
                    "name": "sessionid",
                    "value": "duplicate",
                    "expires": -1,
                },
                {
                    "domain": ".threads.com",
                    "path": "/",
                    "name": "_ga",
                    "value": "analytics",
                    "expires": -1,
                },
            ],
            "origins": [],
        }

        with patch("main.time.time", return_value=1_000):
            normalized = main.normalize_threads_storage_state(state)

        self.assertEqual(len(normalized["cookies"]), 2)
        session_cookie = next(
            cookie for cookie in normalized["cookies"] if cookie["name"] == "sessionid"
        )
        self.assertEqual(
            session_cookie["expires"],
            1_000 + (main.THREADS_COOKIE_KEEP_DAYS * 24 * 60 * 60),
        )
        analytics_cookie = next(
            cookie for cookie in normalized["cookies"] if cookie["name"] == "_ga"
        )
        self.assertEqual(analytics_cookie["expires"], -1)

    def test_threads_profile_worker_uses_playwright_bootstrap(self) -> None:
        result_queue: queue.Queue = queue.Queue()
        profile = {"username": "tester", "profile_url": "https://www.threads.com/@tester"}
        with patch(
            "main.run_threads_playwright_bootstrap",
            return_value=(True, profile),
        ) as bootstrap:
            worker = main.ThreadsProfileWorker(result_queue)
            worker.run()

        bootstrap.assert_called_once_with(result_queue)
        self.assertEqual(result_queue.get_nowait(), ("threads_profile_done", profile))

    def test_publish_pipeline_uses_playwright_instead_of_graph_api(self) -> None:
        source = inspect.getsource(main.PublishPipelineWorker.run)
        self.assertIn("run_threads_playwright_publish", source)
        self.assertNotIn("ThreadsClient", source)
        self.assertNotIn("threads_access_token", source)
        self.assertNotIn("threads_user_id", source)
        self.assertFalse(hasattr(main, "ThreadsClient"))

    def test_threads_settings_ui_requires_no_api_credentials(self) -> None:
        card_source = inspect.getsource(main.KeywordApp._build_threads_card)
        save_source = inspect.getsource(main.KeywordApp._save_threads_settings)
        test_source = inspect.getsource(main.KeywordApp._test_threads_connection)

        self.assertIn("Threads Playwright", card_source)
        self.assertIn("로그인 · 프로필 확인", card_source)
        self.assertNotIn("Access Token", card_source)
        self.assertNotIn("Threads User ID", card_source)
        self.assertNotIn("threads_access_token", save_source)
        self.assertNotIn("threads_user_id", test_source)
        self.assertIn("ThreadsProfileWorker", test_source)
        settings_fields = main.WordPressSettings.__dataclass_fields__
        self.assertNotIn("threads_access_token", settings_fields)
        self.assertNotIn("threads_user_id", settings_fields)
        self.assertNotIn("threads_app_secret", settings_fields)

    def test_threads_publisher_targets_accessible_composer_controls(self) -> None:
        source = inspect.getsource(main.run_threads_playwright_publish)
        self.assertIn('get_by_role("dialog")', source)
        self.assertIn('[contenteditable="true"][role="textbox"]', source)
        self.assertIn('r"^(게시|Post)$"', source)
        self.assertIn("dialog.wait_for(state=\"hidden\"", source)
        self.assertIn("save_threads_storage_state", source)


if __name__ == "__main__":
    unittest.main()
