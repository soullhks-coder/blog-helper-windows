import ast
import queue
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class _ValueStub:
    def __init__(self, value="") -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _EntryStub(_ValueStub):
    def delete(self, _start, _end) -> None:
        self.value = ""

    def insert(self, _index, value) -> None:
        self.value = value


class _EmptyLocator:
    @property
    def first(self):
        return self

    def count(self) -> int:
        return 0


class _PageStub:
    def __init__(self, url: str) -> None:
        self.url = url

    def is_closed(self) -> bool:
        return False

    def locator(self, _selector):
        return _EmptyLocator()


class NaverBlogPreviousPostTests(unittest.TestCase):
    def _app_stub(self, profiles, active="블로그 1"):
        profile_vars = {}
        for index, profile in enumerate(profiles):
            for field_key in ("blog_id", "nickname", "write_url"):
                profile_vars[f"{index}:{field_key}"] = _ValueStub(
                    str(profile.get(field_key) or "")
                )
        app = SimpleNamespace(
            wordpress_settings=main.WordPressSettings(
                naver_blog_profiles=[dict(profile) for profile in profiles],
                naver_blog_active_profile=active,
            ),
            naver_blog_profile_vars=profile_vars,
            naver_blog_active_profile_var=_ValueStub(active),
            naver_blog_write_url_entry=_EntryStub("old-url"),
        )
        app._naver_blog_profiles_from_state = (
            main.KeywordApp._naver_blog_profiles_from_state.__get__(app)
        )
        app._current_naver_blog_profiles = (
            main.KeywordApp._current_naver_blog_profiles.__get__(app)
        )
        app._normalize_naver_blog_id = main.normalize_naver_blog_id
        return app

    def test_post_url_variants_are_canonicalized_and_editor_urls_are_rejected(self):
        expected = "https://blog.naver.com/soullhk/123456789"
        self.assertEqual(
            main.normalize_naver_blog_post_url(
                "https://m.blog.naver.com/soullhk/123456789?referrerCode=1"
            ),
            expected,
        )
        self.assertEqual(
            main.normalize_naver_blog_post_url(
                "https://blog.naver.com/PostView.naver?blogId=soullhk&logNo=123456789"
            ),
            expected,
        )
        self.assertEqual(
            main.normalize_naver_blog_post_url(
                "https://blog.naver.com/soullhk?Redirect=Write&"
            ),
            "",
        )
        self.assertEqual(
            main.normalize_naver_blog_post_url(
                "https://blog.naver.com/PostWriteForm.naver?blogId=soullhk&logNo=123456789"
            ),
            "",
        )
        self.assertEqual(
            main.normalize_naver_blog_post_url(expected, blog_id="another"),
            "",
        )

    def test_recent_urls_keep_newest_two_unique_per_profile(self):
        profiles = main.normalize_naver_blog_profiles(
            [
                {
                    "name": "블로그 1",
                    "blog_id": "first",
                    "recent_post_urls": [
                        "https://blog.naver.com/first/300",
                        "https://m.blog.naver.com/first/200",
                        "https://blog.naver.com/first/100",
                    ],
                },
                {
                    "name": "블로그 2",
                    "blog_id": "second",
                    "recent_post_urls": [
                        "https://blog.naver.com/second/900",
                        "https://blog.naver.com/first/300",
                    ],
                },
            ]
        )

        self.assertEqual(
            profiles[0]["recent_post_urls"],
            [
                "https://blog.naver.com/first/300",
                "https://blog.naver.com/first/200",
            ],
        )
        self.assertEqual(
            profiles[1]["recent_post_urls"],
            ["https://blog.naver.com/second/900"],
        )

    def test_new_published_url_updates_only_matching_profile_scope(self):
        profiles = main.normalize_naver_blog_profiles(
            [
                {
                    "name": "블로그 1",
                    "blog_id": "first",
                    "recent_post_urls": ["https://blog.naver.com/first/200"],
                },
                {
                    "name": "블로그 2",
                    "blog_id": "second",
                    "recent_post_urls": [
                        "https://blog.naver.com/second/800",
                        "https://blog.naver.com/second/700",
                    ],
                },
            ]
        )
        app = self._app_stub(profiles, active="블로그 1")

        with patch.object(main.AppStateStore, "save") as save:
            message = main.KeywordApp._apply_naver_blog_bootstrap_result(
                app,
                {
                    "message": "발행 URL 확인.",
                    "profile_scope": main.NAVER_PLAYWRIGHT_PROFILE_BLOG_2,
                    "blog_id": "second",
                    "write_url": "https://blog.naver.com/second?Redirect=Write&",
                    "published_url": "https://m.blog.naver.com/second/900",
                },
            )

        saved = app.wordpress_settings.naver_blog_profiles
        self.assertEqual(
            saved[0]["recent_post_urls"],
            ["https://blog.naver.com/first/200"],
        )
        self.assertEqual(
            saved[1]["recent_post_urls"],
            [
                "https://blog.naver.com/second/900",
                "https://blog.naver.com/second/800",
            ],
        )
        self.assertIn("새 발행글 URL", message)
        save.assert_called_once_with(app.wordpress_settings, save_secrets=False)

    def test_published_url_detection_ignores_saved_previous_links(self):
        pages = [
            _PageStub("https://blog.naver.com/first/200"),
            _PageStub("https://blog.naver.com/first/300"),
        ]
        self.assertEqual(
            main.detect_naver_blog_published_url(
                pages,
                blog_id="first",
                excluded_urls=["https://blog.naver.com/first/200"],
            ),
            "https://blog.naver.com/first/300",
        )

    def test_links_are_inserted_newest_first_and_one_failure_does_not_stop_next(self):
        urls = [
            "https://blog.naver.com/first/300",
            "https://blog.naver.com/first/200",
        ]
        events = queue.Queue()
        with patch.object(
            main,
            "_insert_one_naver_blog_previous_post_link",
            side_effect=[False, True],
        ) as insert:
            inserted_count = main.insert_naver_blog_previous_post_links(
                object(),
                urls,
                events,
                blog_id="first",
            )

        self.assertEqual(inserted_count, 1)
        self.assertEqual(
            [call.args[1] for call in insert.call_args_list],
            urls,
        )
        event_messages = []
        while not events.empty():
            event_messages.append(events.get_nowait()[1])
        self.assertTrue(any("해당 링크만 건너뛰고" in item for item in event_messages))

    def test_body_links_run_after_body_and_before_tags(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "run_naver_blog_playwright_bootstrap"
        )
        function_source = ast.get_source_segment(source, function) or ""
        body_position = function_source.index("fill_naver_blog_editor(")
        links_position = function_source.index("insert_naver_blog_previous_post_links(")
        tags_position = function_source.index("fill_naver_blog_publish_tags(")
        self.assertLess(body_position, links_position)
        self.assertLess(links_position, tags_position)

    def test_link_helper_uses_exact_url_placeholder_and_skips_failure(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        self.assertIn("input[placeholder='URL을 입력하세요.']", source)
        helper_source = ast.get_source_segment(
            source,
            next(
                node
                for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.FunctionDef)
                and node.name == "insert_naver_blog_previous_post_links"
            ),
        ) or ""
        self.assertIn("해당 링크만 건너뛰고 태그 입력을 계속합니다", helper_source)
        self.assertIn("for index, post_url in enumerate(post_urls, start=1)", helper_source)


if __name__ == "__main__":
    unittest.main()
