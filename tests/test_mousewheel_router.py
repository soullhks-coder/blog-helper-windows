import unittest
from types import SimpleNamespace
from unittest.mock import patch

import main


class _ViewStub:
    def __init__(self, view):
        self._view = view

    def yview(self):
        return self._view


class MousewheelRouterTests(unittest.TestCase):
    def test_mousewheel_units_keep_platform_direction(self) -> None:
        with patch.object(main.sys, "platform", "darwin"):
            self.assertEqual(
                main.KeywordApp._mousewheel_units(
                    SimpleNamespace(delta=4, num=0)
                ),
                -4,
            )
        with patch.object(main.sys, "platform", "win32"):
            self.assertEqual(
                main.KeywordApp._mousewheel_units(
                    SimpleNamespace(delta=-120, num=0)
                ),
                3,
            )
        with patch.object(main.sys, "platform", "linux"):
            self.assertEqual(
                main.KeywordApp._mousewheel_units(
                    SimpleNamespace(delta=0, num=4)
                ),
                -1,
            )

    def test_vertical_view_moves_only_when_direction_has_room(self) -> None:
        self.assertFalse(
            main.KeywordApp._vertical_view_can_move(_ViewStub((0.0, 0.4)), -1)
        )
        self.assertTrue(
            main.KeywordApp._vertical_view_can_move(_ViewStub((0.0, 0.4)), 1)
        )
        self.assertTrue(
            main.KeywordApp._vertical_view_can_move(_ViewStub((0.6, 1.0)), -1)
        )
        self.assertFalse(
            main.KeywordApp._vertical_view_can_move(_ViewStub((0.6, 1.0)), 1)
        )

    def test_nested_scroll_distance_prefers_inner_frame(self) -> None:
        outer_canvas = SimpleNamespace(master=None)
        outer_frame = SimpleNamespace(master=outer_canvas)
        inner_canvas = SimpleNamespace(master=outer_frame)
        inner_frame = SimpleNamespace(master=inner_canvas)
        child = SimpleNamespace(master=inner_frame)

        self.assertEqual(
            main.KeywordApp._widget_distance_to_scroll_frame(
                child,
                SimpleNamespace(_parent_canvas=inner_canvas),
            ),
            2,
        )
        self.assertEqual(
            main.KeywordApp._widget_distance_to_scroll_frame(
                child,
                SimpleNamespace(_parent_canvas=outer_canvas),
            ),
            4,
        )


if __name__ == "__main__":
    unittest.main()
