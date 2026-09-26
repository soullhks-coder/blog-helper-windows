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


class _ConfirmLocator:
    def __init__(self, enabled=True, on_click=None) -> None:
        self.clicked = 0
        self.enabled = enabled
        self.on_click = on_click

    @property
    def first(self):
        return self

    def count(self) -> int:
        return 1

    def nth(self, _index):
        return self

    def is_visible(self, timeout=None) -> bool:
        return True

    def is_enabled(self, timeout=None) -> bool:
        return self.enabled

    def click(self, timeout=None) -> None:
        self.clicked += 1
        if self.on_click:
            self.on_click()


class _DetachedConfirmLocator(_ConfirmLocator):
    def click(self, timeout=None) -> None:
        self.clicked += 1
        raise RuntimeError("Node is detached from document")


class _ConfirmTarget:
    def __init__(self, confirm=None) -> None:
        self.confirm = confirm or _ConfirmLocator(enabled=False)
        self.search = _ConfirmLocator(on_click=self._enable_confirm)
        self.last_options = {}
        self.option_history = []

    def _enable_confirm(self) -> None:
        self.confirm.enabled = True

    def get_by_role(self, role, name=None, exact=None):
        if role == "button" and name == "검색":
            return self.search
        if role == "button" and name == "확인":
            return self.confirm
        return _EmptyLocator()

    def evaluate(self, _script, options):
        self.last_options = dict(options)
        self.option_history.append(dict(options))
        return {"text": "확인", "className": "se-popup-button-confirm", "score": 10000}

    def locator(self, selector):
        if "oglink-search" in selector:
            return self.search
        return self.confirm


class _PopupInput:
    def __init__(self, target=None, value="") -> None:
        self.target = target
        self.value = value
        self.fill_history = []

    def is_visible(self, timeout=None) -> bool:
        return True

    def fill(self, value) -> None:
        self.value = str(value)
        self.fill_history.append(self.value)
        if not self.value and self.target is not None:
            self.target.confirm.enabled = False

    def input_value(self, timeout=None) -> str:
        return self.value

    def locator(self, _selector):
        return _EmptyLocator()


class _EditorPage:
    def wait_for_timeout(self, _milliseconds) -> None:
        return None


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

    def test_rss_parser_returns_actual_latest_two_in_feed_order(self):
        rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss><channel>
          <item><link>https://blog.naver.com/first/900?fromRss=true</link>
                <guid>https://blog.naver.com/first/900</guid></item>
          <item><link>https://blog.naver.com/first/800?fromRss=true</link></item>
          <item><guid>https://blog.naver.com/first/700</guid></item>
        </channel></rss>"""

        self.assertEqual(
            main.parse_naver_blog_recent_post_urls_from_rss(
                rss_xml,
                blog_id="first",
                limit=2,
            ),
            [
                "https://blog.naver.com/first/900",
                "https://blog.naver.com/first/800",
            ],
        )

    def test_editor_ready_payload_replaces_stale_profile_history_with_rss_urls(self):
        profiles = main.normalize_naver_blog_profiles(
            [
                {
                    "name": "블로그 1",
                    "blog_id": "first",
                    "recent_post_urls": ["https://blog.naver.com/first/100"],
                }
            ]
        )
        app = self._app_stub(profiles, active="블로그 1")

        with patch.object(main.AppStateStore, "save"):
            main.KeywordApp._apply_naver_blog_bootstrap_result(
                app,
                {
                    "message": "에디터 준비 완료.",
                    "profile_scope": main.NAVER_PLAYWRIGHT_PROFILE_BLOG,
                    "blog_id": "first",
                    "write_url": "https://blog.naver.com/first?Redirect=Write&",
                    "previous_post_urls": [
                        "https://blog.naver.com/first/900",
                        "https://blog.naver.com/first/800",
                    ],
                },
            )

        self.assertEqual(
            app.wordpress_settings.naver_blog_profiles[0]["recent_post_urls"],
            [
                "https://blog.naver.com/first/900",
                "https://blog.naver.com/first/800",
            ],
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

    def test_exact_requested_url_clicks_the_link_popup_confirm_button(self):
        requested_url = "https://blog.naver.com/soullhk/224395703881"
        target = _ConfirmTarget()
        with patch.object(
            main,
            "_naver_blog_editor_targets",
            return_value=[target],
        ), patch.object(
            main,
            "_naver_blog_oglink_component_count",
            return_value=1,
        ):
            clicked = main._click_naver_blog_link_confirm(
                _EditorPage(),
                target,
                _PopupInput(),
                requested_url,
                previous_component_count=0,
                timeout_seconds=5,
            )

        self.assertTrue(clicked)
        self.assertEqual(target.last_options["url"], requested_url)
        self.assertEqual(target.confirm.clicked, 1)

    def test_exact_requested_url_clicks_search_before_confirm(self):
        requested_url = "https://blog.naver.com/soullhk/224395703881"
        target = _ConfirmTarget()
        popup_input = _PopupInput(target)
        searched = main._click_naver_blog_link_search(
            _EditorPage(),
            target,
            popup_input,
            requested_url,
            timeout_seconds=3,
        )

        self.assertTrue(searched)
        self.assertEqual(target.search.clicked, 1)
        self.assertEqual(target.confirm.clicked, 0)
        self.assertEqual(target.option_history, [])
        self.assertTrue(target.confirm.enabled)
        self.assertEqual(popup_input.value, requested_url)

    def test_stale_confirm_is_reset_and_requested_url_is_searched_again(self):
        requested_url = "https://blog.naver.com/soullhk/224395703881"
        target = _ConfirmTarget()
        target.confirm.enabled = True
        popup_input = _PopupInput(
            target,
            value="https://blog.naver.com/soullhk/224423519503",
        )
        searched = main._click_naver_blog_link_search(
            _EditorPage(),
            target,
            popup_input,
            requested_url,
            timeout_seconds=3,
        )

        self.assertTrue(searched)
        self.assertEqual(target.search.clicked, 1)
        self.assertEqual(target.confirm.clicked, 0)
        self.assertEqual(target.option_history, [])
        self.assertEqual(popup_input.fill_history, ["", requested_url])
        self.assertEqual(popup_input.value, requested_url)

    def test_detached_confirm_after_click_counts_inserted_card_as_success(self):
        requested_url = "https://blog.naver.com/soullhk/224395703881"
        target = _ConfirmTarget(_DetachedConfirmLocator())
        with patch.object(
            main,
            "_naver_blog_editor_targets",
            return_value=[target],
        ), patch.object(
            main,
            "_naver_blog_oglink_component_count",
            return_value=1,
        ):
            clicked = main._click_naver_blog_link_confirm(
                _EditorPage(),
                target,
                _PopupInput(),
                requested_url,
                previous_component_count=0,
                timeout_seconds=5,
            )

        self.assertTrue(clicked)
        self.assertEqual(target.confirm.clicked, 1)

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

    def test_single_link_flow_searches_after_fill_and_before_confirm(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_insert_one_naver_blog_previous_post_link"
        )
        function_source = ast.get_source_segment(source, function) or ""
        fill_position = function_source.index("popup_input.fill(post_url)")
        search_position = function_source.index("_click_naver_blog_link_search(")
        confirm_position = function_source.index("_click_naver_blog_link_confirm(")
        self.assertLess(fill_position, search_position)
        self.assertLess(search_position, confirm_position)
        self.assertNotIn('popup_input.press("Tab")', function_source)

    def test_search_helper_supports_icon_only_right_edge_button(self):
        source = MAIN_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_click_naver_blog_link_search"
        )
        function_source = ast.get_source_segment(source, function) or ""
        self.assertIn("node.textContent", function_source)
        self.assertIn("isRightEdgeButton", function_source)
        self.assertIn("!isConfirm && !isClose", function_source)
        self.assertIn("inputRect.width * 0.72", function_source)


if __name__ == "__main__":
    unittest.main()
