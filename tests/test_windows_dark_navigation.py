import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class _ButtonStub:
    def configure(self, **_kwargs) -> None:
        pass


class _CompatButtonStub:
    def __init__(self, *, state: str = "normal", children=None) -> None:
        self._state = state
        self._mouse_inside = False
        self._children = list(children or [])
        self.bindings = []

    def bind(self, sequence, callback, add=None) -> None:
        self.bindings.append((sequence, callback, add))

    def winfo_children(self):
        return list(self._children)


class WindowsDarkNavigationTests(unittest.TestCase):
    def _app_stub(self, current_page: str = "writing"):
        app = SimpleNamespace(
            current_page=current_page,
            wordpress_settings=main.WordPressSettings(app_theme="블랙테마"),
        )
        app._normalize_app_theme = main.KeywordApp._normalize_app_theme.__get__(app)
        app._is_windows_dark_theme = main.KeywordApp._is_windows_dark_theme.__get__(app)
        return app

    def test_release_fallback_opens_automation_page_only_on_windows_dark_theme(self) -> None:
        app = self._app_stub()
        opened_pages = []
        app._switch_page = opened_pages.append
        app.after_idle = lambda callback: callback()

        with patch.object(main.os, "name", "nt"):
            main.KeywordApp._recover_windows_dark_automation_navigation(app)

        self.assertEqual(opened_pages, ["automation"])

    def test_automation_page_is_shown_before_windows_queue_refresh(self) -> None:
        app = self._app_stub()
        events = []
        app._page_frame_map = lambda: {"automation": object()}
        app._theme_palette = lambda: {
            "accent": "accent",
            "muted": "muted",
            "selected": "selected",
            "hover": "hover",
        }
        for name in (
            "writing_nav_button",
            "automation_nav_button",
            "naver_blog_nav_button",
            "naver_kin_nav_button",
            "public_data_nav_button",
            "prompt_nav_button",
            "settings_nav_button",
        ):
            setattr(app, name, _ButtonStub())
        app._show_only_page_frame = lambda page: events.append(("show", page))
        app._refresh_automation_queue = lambda: events.append(("refresh", "automation"))
        app.automation_page = object()
        app._install_windows_dark_button_compatibility = lambda _root: None
        app._schedule_automation_queue_refresh = lambda: app._refresh_automation_queue()
        app.after_idle = lambda callback: callback()

        with patch.object(main.os, "name", "nt"):
            main.KeywordApp._switch_page(app, "automation")

        self.assertEqual(events, [("show", "automation"), ("refresh", "automation")])

    def test_windows_dark_button_press_restores_mouse_inside_state(self) -> None:
        app = self._app_stub()
        button = _CompatButtonStub()

        with patch.object(main.os, "name", "nt"):
            main.KeywordApp._prime_windows_dark_ctk_button(app, button)

        self.assertTrue(button._mouse_inside)

    def test_windows_dark_button_compatibility_binds_existing_and_dynamic_buttons_once(self) -> None:
        app = self._app_stub()
        first_button = _CompatButtonStub()
        second_button = _CompatButtonStub()
        root = _CompatButtonStub(children=[first_button, second_button])
        app._prime_windows_dark_ctk_button = main.KeywordApp._prime_windows_dark_ctk_button.__get__(app)

        with (
            patch.object(main.os, "name", "nt"),
            patch.object(main.ctk, "CTkButton", _CompatButtonStub),
        ):
            main.KeywordApp._install_windows_dark_button_compatibility(app, root)
            main.KeywordApp._install_windows_dark_button_compatibility(app, root)
            for button in (root, first_button, second_button):
                self.assertEqual(len(button.bindings), 1)
                sequence, callback, add = button.bindings[0]
                self.assertEqual(sequence, "<ButtonPress-1>")
                self.assertEqual(add, "+")
                callback(None)
                self.assertTrue(button._mouse_inside)


if __name__ == "__main__":
    unittest.main()
