import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class _FakeTextWidget:
    def __init__(self, widget_class="Entry") -> None:
        self.widget_class = widget_class
        self.generated_events = []
        self.selection = None
        self.insert_cursor = None

    def winfo_class(self):
        return self.widget_class

    def event_generate(self, event_name):
        self.generated_events.append(event_name)

    def selection_range(self, start, end):
        self.selection = (start, end)

    def icursor(self, index):
        self.insert_cursor = index


class TextEditingShortcutTests(unittest.TestCase):
    def test_latin_keysym_resolves_on_every_platform(self) -> None:
        self.assertEqual(main._text_editing_shortcut_action("c", 0, os_name="posix"), "copy")
        self.assertEqual(main._text_editing_shortcut_action("V", 0, os_name="posix"), "paste")
        self.assertEqual(main._text_editing_shortcut_action("x", 0, os_name="posix"), "cut")
        self.assertEqual(main._text_editing_shortcut_action("A", 0, os_name="posix"), "select_all")

    def test_windows_physical_keycode_works_when_korean_ime_changes_keysym(self) -> None:
        self.assertEqual(main._text_editing_shortcut_action("Hangul", 67, os_name="nt"), "copy")
        self.assertEqual(main._text_editing_shortcut_action("Hangul", 86, os_name="nt"), "paste")
        self.assertEqual(main._text_editing_shortcut_action("Hangul", 88, os_name="nt"), "cut")
        self.assertEqual(main._text_editing_shortcut_action("Hangul", 65, os_name="nt"), "select_all")

    def test_non_windows_keycode_fallback_does_not_capture_unrelated_keys(self) -> None:
        self.assertEqual(main._text_editing_shortcut_action("Hangul", 65, os_name="posix"), "")

    def test_handler_generates_one_paste_virtual_event(self) -> None:
        widget = _FakeTextWidget()
        activity = []
        app = SimpleNamespace(
            _is_text_input_widget=lambda candidate: candidate is widget,
            _mark_text_input_activity=lambda event: activity.append(event),
        )
        event = SimpleNamespace(widget=widget, keysym="Hangul", keycode=86)

        with patch.object(main.os, "name", "nt"):
            result = main.KeywordApp._handle_text_editing_shortcut(app, event)

        self.assertEqual(result, "break")
        self.assertEqual(widget.generated_events, ["<<Paste>>"])
        self.assertEqual(activity, [event])

    def test_handler_selects_all_entry_text(self) -> None:
        widget = _FakeTextWidget()
        app = SimpleNamespace(
            _is_text_input_widget=lambda candidate: candidate is widget,
            _mark_text_input_activity=lambda _event: None,
        )
        event = SimpleNamespace(widget=widget, keysym="a", keycode=0)

        result = main.KeywordApp._handle_text_editing_shortcut(app, event)

        self.assertEqual(result, "break")
        self.assertEqual(widget.selection, (0, "end"))
        self.assertEqual(widget.insert_cursor, "end")

    def test_handler_ignores_non_text_widgets(self) -> None:
        widget = object()
        app = SimpleNamespace(
            _is_text_input_widget=lambda _candidate: False,
            _mark_text_input_activity=lambda _event: None,
        )
        event = SimpleNamespace(widget=widget, keysym="v", keycode=86)

        self.assertIsNone(main.KeywordApp._handle_text_editing_shortcut(app, event))


if __name__ == "__main__":
    unittest.main()
