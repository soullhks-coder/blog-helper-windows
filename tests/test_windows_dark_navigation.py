import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class _ButtonStub:
    def configure(self, **_kwargs) -> None:
        pass


def _release_button_class():
    class ReleaseButtonStub:
        def __init__(self, *, bounds=(100, 200, 120, 48), state="normal") -> None:
            self._state = state
            self._mouse_inside = False
            self._bounds = bounds
            self.command_calls = 0
            self.release_calls = 0

        def _on_release(self, _event=None):
            self.release_calls += 1
            if self._mouse_inside and self._state != main.tk.DISABLED:
                self.command_calls += 1

        def winfo_rootx(self):
            return self._bounds[0]

        def winfo_rooty(self):
            return self._bounds[1]

        def winfo_width(self):
            return self._bounds[2]

        def winfo_height(self):
            return self._bounds[3]

        def winfo_pointerxy(self):
            return (self._bounds[0] + 1, self._bounds[1] + 1)

    return ReleaseButtonStub


class WindowsDarkNavigationTests(unittest.TestCase):
    def _app_stub(self, current_page: str = "writing"):
        app = SimpleNamespace(
            current_page=current_page,
            wordpress_settings=main.WordPressSettings(app_theme="블랙테마"),
        )
        app._normalize_app_theme = main.KeywordApp._normalize_app_theme.__get__(app)
        app._is_windows_dark_theme = main.KeywordApp._is_windows_dark_theme.__get__(app)
        return app

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
        app._schedule_automation_queue_refresh = lambda: app._refresh_automation_queue()

        with patch.object(main.os, "name", "nt"):
            main.KeywordApp._switch_page(app, "automation")

        self.assertEqual(events, [("show", "automation"), ("refresh", "automation")])

    def test_windows_dark_release_uses_real_pointer_bounds(self) -> None:
        button_class = _release_button_class()
        button = button_class()
        event = SimpleNamespace(x_root=150, y_root=220)

        with (
            patch.object(main.os, "name", "nt"),
            patch.object(main.ctk, "get_appearance_mode", return_value="Dark"),
        ):
            self.assertTrue(main._install_windows_dark_ctk_button_release_fix(button_class))
            button._on_release(event)

        self.assertEqual(button.release_calls, 1)
        self.assertEqual(button.command_calls, 1)
        self.assertTrue(button._mouse_inside)

    def test_windows_dark_release_rejects_pointer_outside_button(self) -> None:
        button_class = _release_button_class()
        button = button_class()
        event = SimpleNamespace(x_root=50, y_root=50)

        with (
            patch.object(main.os, "name", "nt"),
            patch.object(main.ctk, "get_appearance_mode", return_value="Dark"),
        ):
            main._install_windows_dark_ctk_button_release_fix(button_class)
            button._on_release(event)

        self.assertEqual(button.release_calls, 1)
        self.assertEqual(button.command_calls, 0)
        self.assertFalse(button._mouse_inside)

    def test_light_theme_keeps_customtkinter_release_behavior(self) -> None:
        button_class = _release_button_class()
        button = button_class()
        event = SimpleNamespace(x_root=150, y_root=220)

        with (
            patch.object(main.os, "name", "nt"),
            patch.object(main.ctk, "get_appearance_mode", return_value="Light"),
        ):
            main._install_windows_dark_ctk_button_release_fix(button_class)
            button._on_release(event)

        self.assertEqual(button.release_calls, 1)
        self.assertEqual(button.command_calls, 0)

    def test_windows_release_fix_is_installed_only_once_per_button_class(self) -> None:
        button_class = _release_button_class()
        with patch.object(main.os, "name", "nt"):
            self.assertTrue(main._install_windows_dark_ctk_button_release_fix(button_class))
            self.assertFalse(main._install_windows_dark_ctk_button_release_fix(button_class))


if __name__ == "__main__":
    unittest.main()
