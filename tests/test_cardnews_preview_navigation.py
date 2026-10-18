import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import main


class CardnewsPreviewNavigationTests(unittest.TestCase):
    def make_app(self, count, index=0, icon=object()):
        app = SimpleNamespace(
            cardnews_specs=[{"heading": f"Card {i}"} for i in range(count)],
            active_cardnews_slide_index=index,
            _theme_palette=lambda: {"text": "#111111", "muted": "#777777"},
            _bootstrap_sidebar_icon_image=Mock(return_value=icon),
        )
        for name in ("cardnews_prev_button", "cardnews_next_button",
                     "writing_cardnews_prev_button", "writing_cardnews_next_button",
                     "writing_cardnews_slide_label"):
            setattr(app, name, Mock())
        return app

    def test_empty_and_single_image_disable_both_directions(self):
        for count, label in ((0, "이미지 없음"), (1, "1 / 1")):
            with self.subTest(count=count):
                app = self.make_app(count)
                main.KeywordApp._update_cardnews_preview_navigation(app)
                app.writing_cardnews_slide_label.configure.assert_called_once_with(text=label)
                for name in ("cardnews_prev_button", "cardnews_next_button",
                             "writing_cardnews_prev_button", "writing_cardnews_next_button"):
                    self.assertEqual(getattr(app, name).configure.call_args.kwargs["state"], "disabled")

    def test_first_middle_and_last_images_sync_both_navigators(self):
        for index, states in ((0, ("disabled", "normal")), (1, ("normal", "normal")),
                              (2, ("normal", "disabled"))):
            with self.subTest(index=index):
                app = self.make_app(3, index)
                main.KeywordApp._update_cardnews_preview_navigation(app)
                app.writing_cardnews_slide_label.configure.assert_called_once_with(text=f"{index + 1} / 3")
                for suffix, state in zip(("prev", "next"), states):
                    for prefix in ("", "writing_"):
                        button = getattr(app, f"{prefix}cardnews_{suffix}_button")
                        self.assertEqual(button.configure.call_args.kwargs["state"], state)

    def test_bootstrap_chevrons_and_text_fallback(self):
        self.assertIsNotNone(main.bootstrap_icon_codepoint("chevron-left"))
        self.assertIsNotNone(main.bootstrap_icon_codepoint("chevron-right"))
        app = self.make_app(2, icon=None)
        main.KeywordApp._update_cardnews_preview_navigation(app)
        self.assertEqual(app.writing_cardnews_prev_button.configure.call_args.kwargs["text"], "<")
        self.assertEqual(app.writing_cardnews_next_button.configure.call_args.kwargs["text"], ">")
        self.assertEqual(app._bootstrap_sidebar_icon_image.call_args_list[0].args, ("chevron-left", "#777777", 20))
        self.assertEqual(app._bootstrap_sidebar_icon_image.call_args_list[1].args, ("chevron-right", "#111111", 20))

    def test_navigation_saves_edits_and_preserves_generated_image_paths(self):
        app = self.make_app(3)
        app.cardnews_image_paths = ["first.png", "second.png", "third.png"]
        app._save_active_cardnews_slide = Mock()
        app._render_active_cardnews_slide = Mock()
        app._show_cardnews_preview = Mock()
        before = copy.deepcopy(app.cardnews_specs)
        for direction, expected in ((1, 1), (1, 2), (1, 2), (-1, 1), (-1, 0), (-1, 0)):
            main.KeywordApp._move_cardnews_slide(app, direction)
            self.assertEqual(app.active_cardnews_slide_index, expected)
        self.assertEqual(app._save_active_cardnews_slide.call_count, 6)
        self.assertEqual(app._render_active_cardnews_slide.call_count, 6)
        self.assertEqual(app.cardnews_image_paths, ["first.png", "second.png", "third.png"])
        self.assertEqual(app.cardnews_specs, before)

    def test_no_images_do_not_trigger_edit_or_export_actions(self):
        app = self.make_app(0)
        app._save_active_cardnews_slide = Mock()
        main.KeywordApp._move_cardnews_slide(app, 1)
        app._save_active_cardnews_slide.assert_not_called()

    def test_responsive_layout_includes_arrows_and_keeps_v2_panel_hidden(self):
        panels = [Mock(), Mock()]
        panels[0].winfo_reqwidth.return_value = 320
        panels[1].winfo_reqwidth.return_value = 408
        app = SimpleNamespace(writing_design_preview_panels=panels, _is_tistory_v2_writing=lambda: False)
        host = SimpleNamespace(_get_widget_scaling=lambda: 1, grid_columnconfigure=Mock())
        main.KeywordApp._layout_publish_design_previews(app, host, 700)
        self.assertEqual(panels[1].grid.call_args.kwargs["row"], 1)
        main.KeywordApp._layout_publish_design_previews(app, host, 900)
        self.assertEqual(panels[1].grid.call_args.kwargs["row"], 0)
        self.assertEqual(panels[1].grid.call_args.kwargs["column"], 1)
        app._is_tistory_v2_writing = lambda: True
        main.KeywordApp._layout_publish_design_previews(app, host, 1200)
        panels[1].grid_remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()
