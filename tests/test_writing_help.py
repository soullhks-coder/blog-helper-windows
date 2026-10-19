import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import main
from writing_help import WRITING_GUIDES, WritingHelpDialog


class WritingHelpTests(unittest.TestCase):
    def test_guides_cover_all_context_icons_with_local_images_and_steps(self):
        source = Path(main.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        topics = set()
        for call in ast.walk(tree):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "_create_writing_help_button"
                    and len(call.args) >= 2 and isinstance(call.args[1], ast.Constant)):
                topics.add(call.args[1].value)
        topics.update(("topic", "keyword", "article", "publish", "thumbnail", "cardnews"))
        self.assertEqual(topics, set(WRITING_GUIDES))
        for topic, guide in WRITING_GUIDES.items():
            with self.subTest(topic=topic):
                self.assertTrue(guide["title"] and guide["intro"] and guide["note"])
                self.assertEqual(len(guide["steps"]), 3)
                self.assertTrue(all(title and body for title, body in guide["steps"]))
                path = main.RESOURCE_DIR / "assets" / "writing-guide" / f"{topic}.png"
                with Image.open(path) as image:
                    image.verify()
                with Image.open(path) as image:
                    self.assertGreater(image.width, 150)
                    self.assertGreater(image.height, 20)

    def test_all_images_are_bundled_in_both_platform_specs(self):
        for spec in ("BlogHelper.spec", "BlogHelperMac.spec"):
            source = (main.RESOURCE_DIR / spec).read_text(encoding="utf-8")
            self.assertIn('("assets/writing-guide", "assets/writing-guide")', source)
        self.assertIsNotNone(main.bootstrap_icon_codepoint("question-circle"))

    def test_unknown_topic_does_nothing_and_existing_window_is_reused(self):
        app = SimpleNamespace(_tag_mousewheel_descendants=Mock())
        main.KeywordApp._open_writing_help(app, "unknown")
        self.assertNotIn("writing_help_dialog", app.__dict__)
        app.writing_help_dialog = Mock()
        app.writing_help_dialog.winfo_exists.return_value = True
        main.KeywordApp._open_writing_help(app, "links")
        app.writing_help_dialog.show_topic.assert_called_once_with("links")
        app.writing_help_dialog.lift.assert_called_once()

    def test_new_guide_opens_without_triggering_writing_or_saving(self):
        app = SimpleNamespace(_theme_palette=lambda: {"shell": "#ffffff"},
                              _tag_mousewheel_descendants=Mock())
        with patch("writing_help.WritingHelpDialog") as dialog:
            main.KeywordApp._open_writing_help(app, "topic")
        dialog.assert_called_once_with(app, "topic", resource_dir=main.RESOURCE_DIR, palette={"shell": "#ffffff"})
        app._tag_mousewheel_descendants.assert_called_once_with(dialog.return_value)

    def test_viewer_is_read_only_and_uses_shared_modal_dimming(self):
        source = inspect.getsource(WritingHelpDialog)
        for forbidden in ("webbrowser", "requests", "subprocess", "AppStateStore", "_run_publish", "_save_ui_state"):
            self.assertNotIn(forbidden, source)
        self.assertIn("modal_dimmer(parent).attach(self)", source)
        self.assertIn('self.bind("<Escape>"', source)
        self.assertIn("예시 이미지를 불러오지", source)
        self.assertIn("except (OSError, ValueError)", source)

    def test_instructions_distinguish_search_start_visibility_and_publication(self):
        topic = str(WRITING_GUIDES["topic"])
        self.assertIn("[검색]", topic)
        self.assertIn("[시작]", topic)
        self.assertIn("[버튼 저장]", str(WRITING_GUIDES["links"]))
        self.assertIn("ON/OFF", str(WRITING_GUIDES["links"]))
        self.assertIn("공개 발행이 방지되는 것은 아니", WRITING_GUIDES["publish"]["note"])


if __name__ == "__main__":
    unittest.main()
