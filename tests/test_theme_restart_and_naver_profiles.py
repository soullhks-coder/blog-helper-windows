import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


class _WidgetStub:
    def __init__(self) -> None:
        self.config = {}

    def configure(self, **kwargs) -> None:
        self.config.update(kwargs)


class ThemeRestartTests(unittest.TestCase):
    def test_theme_change_saves_then_schedules_restart_without_live_rebuild(self) -> None:
        scheduled = []
        saved = []
        app = SimpleNamespace(
            wordpress_settings=main.WordPressSettings(app_theme="블랙테마"),
            _theme_restart_pending=False,
            theme_menu=_WidgetStub(),
            theme_status_label=_WidgetStub(),
        )
        app._normalize_app_theme = main.KeywordApp._normalize_app_theme.__get__(app)
        app._read_wordpress_settings = lambda include_prompts=False: main.WordPressSettings(
            app_theme="블랙테마"
        )
        app._theme_palette = lambda _theme=None: {"accent": "accent"}
        app._restart_after_theme_change = lambda: None
        app.after = lambda delay, callback: scheduled.append((delay, callback))

        with patch.object(
            main.AppStateStore,
            "save",
            side_effect=lambda settings, save_secrets=False: saved.append(
                (settings.app_theme, save_secrets)
            ),
        ):
            main.KeywordApp._on_theme_changed(app, "화이트테마")

        self.assertEqual(saved, [("화이트테마", False)])
        self.assertEqual(app.wordpress_settings.app_theme, "화이트테마")
        self.assertTrue(app._theme_restart_pending)
        self.assertEqual(app.theme_menu.config.get("state"), "disabled")
        self.assertEqual(scheduled[0][0], 350)
        self.assertIs(scheduled[0][1], app._restart_after_theme_change)

    def test_restart_launches_fresh_process_then_closes_current_app(self) -> None:
        events = []
        app = SimpleNamespace(
            naver_blog_worker=None,
            _on_app_close=lambda: events.append("closed"),
        )
        with patch.object(
            main,
            "launch_application_restart",
            side_effect=lambda: events.append("launched"),
        ):
            main.KeywordApp._restart_after_theme_change(app)

        self.assertEqual(events, ["launched", "closed"])

    def test_source_restart_uses_python_and_main_script(self) -> None:
        with (
            patch.object(main, "is_frozen_app", return_value=False),
            patch.object(main.sys, "argv", ["main.py", "--sample"]),
            patch.object(main.subprocess, "Popen", return_value=object()) as popen,
        ):
            main.launch_application_restart()

        command = popen.call_args.args[0]
        self.assertEqual(command[0], main.sys.executable)
        self.assertEqual(Path(command[1]), main.SCRIPT_DIR / "main.py")
        self.assertEqual(command[2:], ["--sample"])


class NaverPlaywrightProfileTests(unittest.TestCase):
    def test_menu_and_nblog_profiles_use_unique_directories_and_state_files(self) -> None:
        paths = [
            main.naver_playwright_profile_paths(scope)
            for scope in main.NAVER_PLAYWRIGHT_PROFILE_SCOPES
        ]

        self.assertEqual(len(main.NAVER_PLAYWRIGHT_PROFILE_SCOPES), 6)
        self.assertEqual(len({profile_dir for profile_dir, _state_file in paths}), 6)
        self.assertEqual(len({state_file for _profile_dir, state_file in paths}), 6)
        self.assertEqual(
            main.naver_playwright_profile_paths(main.NAVER_PLAYWRIGHT_PROFILE_BLOG)[0],
            main.NAVER_BLOG_CHROME_PROFILE_DIR,
        )

    def test_three_nblog_slots_have_separate_browser_and_cookie_paths(self) -> None:
        paths = [
            main.naver_playwright_profile_paths(scope)
            for scope in main.NAVER_BLOG_PROFILE_SCOPES
        ]

        self.assertEqual(len({profile_dir for profile_dir, _state_file in paths}), 3)
        self.assertEqual(len({state_file for _profile_dir, state_file in paths}), 3)
        self.assertEqual(
            paths[0][0],
            main.NAVER_BLOG_CHROME_PROFILE_DIR,
        )

    def test_webmaster_source_labels_select_different_profiles(self) -> None:
        self.assertEqual(
            main.naver_webmaster_profile_scope("블로그글쓰기"),
            main.NAVER_PLAYWRIGHT_PROFILE_WRITING,
        )
        self.assertEqual(
            main.naver_webmaster_profile_scope("블로그자동화"),
            main.NAVER_PLAYWRIGHT_PROFILE_AUTOMATION,
        )

    def test_storage_state_is_saved_only_to_selected_profile_file(self) -> None:
        class ContextStub:
            def storage_state(self):
                return {"cookies": [], "origins": []}

            def add_cookies(self, _cookies):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_map = {
                scope: (root / scope, root / f"{scope}.json")
                for scope in main.NAVER_PLAYWRIGHT_PROFILE_SCOPES
            }
            with patch.object(main, "NAVER_PLAYWRIGHT_PROFILE_PATHS", profile_map):
                main.save_naver_blog_storage_state(
                    ContextStub(),
                    main.NAVER_PLAYWRIGHT_PROFILE_AUTOMATION,
                )

            self.assertTrue(
                (root / f"{main.NAVER_PLAYWRIGHT_PROFILE_AUTOMATION}.json").exists()
            )
            self.assertFalse(
                (root / f"{main.NAVER_PLAYWRIGHT_PROFILE_WRITING}.json").exists()
            )

    def test_webmaster_worker_passes_its_profile_scope_to_naver(self) -> None:
        result_queue = queue.Queue()
        worker = main.NaverSearchAdvisorWorker(
            "https://blog.soullhk.kr/example-post",
            result_queue,
            enabled_tools=[main.WEBMASTER_TOOL_NAVER],
            profile_scope=main.NAVER_PLAYWRIGHT_PROFILE_AUTOMATION,
        )

        with patch.object(
            main,
            "run_naver_search_advisor_playwright",
            return_value=(True, "완료"),
        ) as submit:
            worker.run()

        submit.assert_called_once_with(
            worker.published_url,
            result_queue,
            profile_scope=main.NAVER_PLAYWRIGHT_PROFILE_AUTOMATION,
        )

    def test_nblog_worker_passes_selected_blog_profile_scope(self) -> None:
        result_queue = queue.Queue()
        worker = main.NaverBlogBootstrapWorker(
            "https://blog.naver.com/mom?Redirect=Write&",
            "mom",
            result_queue,
            profile_scope=main.NAVER_PLAYWRIGHT_PROFILE_BLOG_2,
        )

        with patch.object(
            main,
            "run_naver_blog_playwright_bootstrap",
            return_value=(True, {"message": "완료"}),
        ) as bootstrap:
            worker.run()

        self.assertEqual(
            bootstrap.call_args.kwargs["profile_scope"],
            main.NAVER_PLAYWRIGHT_PROFILE_BLOG_2,
        )

    def test_automation_publish_queue_keeps_automation_profile_scope(self) -> None:
        queued = []
        app = SimpleNamespace(
            wordpress_settings=main.WordPressSettings(),
            after=lambda _delay, callback: callback(),
            _queue_naver_search_advisor_submission=lambda *args, **kwargs: queued.append(
                (args, kwargs)
            ),
        )

        result = main.KeywordApp._queue_published_wordpress_webmaster_submission(
            app,
            {
                "link": "https://blog.soullhk.kr/example-post",
                "status": "publish",
            },
            show_feedback=False,
            source_label="블로그자동화",
        )

        self.assertEqual(result, "https://blog.soullhk.kr/example-post")
        self.assertEqual(
            queued[0][1]["profile_scope"],
            main.NAVER_PLAYWRIGHT_PROFILE_AUTOMATION,
        )


if __name__ == "__main__":
    unittest.main()
