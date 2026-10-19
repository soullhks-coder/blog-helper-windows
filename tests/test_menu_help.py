import inspect
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image

import main
from menu_help import MENU_GUIDES, MENU_SECTIONS, attach_heading_help
from writing_help import ALL_GUIDES, WRITING_GUIDES


class MenuHelpTests(unittest.TestCase):
    def test_every_menu_and_registered_guide_has_complete_content(self):
        self.assertEqual(set(ALL_GUIDES), set(WRITING_GUIDES) | set(MENU_GUIDES))
        pages = {page for page, topic, titles in MENU_SECTIONS}
        self.assertEqual(pages, {"home", "automation", "naver_blog", "naver_kin", "public_data",
                                 "thumbnails", "prompts", "settings", "tistory_v2"})
        mapped = {topic for page, topic, titles in MENU_SECTIONS}
        self.assertEqual(mapped | {"v2.review", "v2.publish"}, set(MENU_GUIDES))
        for topic, guide in MENU_GUIDES.items():
            with self.subTest(topic=topic):
                for field in ("title", "stage", "intro", "note"):
                    self.assertTrue(guide[field])
                self.assertEqual(len(guide["steps"]), 3)
                self.assertTrue(all(title and body for title, body in guide["steps"]))
                with Image.open(main.RESOURCE_DIR / "assets" / "writing-guide" / f"{topic}.png") as image:
                    self.assertGreater(image.width, 150)
                    self.assertGreater(image.height, 30)
                    image.verify()

    def test_guide_headings_are_unique_within_each_menu(self):
        seen = set()
        for page, topic, headings in MENU_SECTIONS:
            self.assertIn(topic, MENU_GUIDES)
            for heading in headings:
                self.assertNotIn((page, heading), seen)
                seen.add((page, heading))

    def test_shared_opener_accepts_all_topics_and_does_not_run_actions(self):
        for topic in MENU_GUIDES:
            app = SimpleNamespace(_theme_palette=lambda: {"shell": "#fff"},
                                  _tag_mousewheel_descendants=Mock())
            with self.subTest(topic=topic), patch("writing_help.WritingHelpDialog") as create:
                main.KeywordApp._open_writing_help(app, topic)
                create.assert_called_once_with(app, topic, resource_dir=main.RESOURCE_DIR,
                                               palette={"shell": "#fff"})

    def test_shared_writing_publish_help_is_variant_specific(self):
        for variant, expected in (("writing", "publish"), ("tistory_v2", "v2.publish")):
            app = SimpleNamespace(writing_variant=variant, _theme_palette=lambda: {},
                                  _tag_mousewheel_descendants=Mock())
            with patch("writing_help.WritingHelpDialog") as create:
                main.KeywordApp._open_writing_help(app, "publish")
                self.assertEqual(create.call_args.args[1], expected)

    def test_existing_heading_identity_is_preserved_and_attachment_is_idempotent(self):
        group = Mock()
        label = Mock()
        label._menu_help_group = None
        label.winfo_manager.return_value = "grid"
        parent = object()
        original = {"in": parent, "row": 3, "column": 2, "columnspan": 2,
                    "padx": (10, 12), "pady": 4, "sticky": "ew"}
        label.grid_info.return_value = dict(original)
        make = Mock()
        with patch("customtkinter.CTkFrame", return_value=group):
            button = attach_heading_help(label, make)
            self.assertIs(button, make.return_value)
            group.grid.assert_called_once_with(**{key: value for key, value in original.items() if key != "in"})
            label.grid.assert_called_once_with(in_=group, row=0, column=0, sticky="w")
            self.assertIs(button._help_heading, label)
            self.assertIsNone(attach_heading_help(label, make))
            make.assert_called_once_with(group)
        label.destroy.assert_not_called()

    def test_guides_distinguish_v2_review_sources_and_real_publication(self):
        self.assertIn("마지막 발행 버튼을 누르지 않고", str(MENU_GUIDES["v2.review"]))
        self.assertIn("저작권", MENU_GUIDES["v2.theme"]["note"])
        self.assertIn("WebP", str(MENU_GUIDES["settings.tistory"]))
        self.assertIn("15건", str(MENU_GUIDES["settings.limit"]))
        self.assertIn("준비 영역", MENU_GUIDES["naver.rules"]["note"])
        source = inspect.getsource(attach_heading_help)
        self.assertNotIn(".destroy(", source)
        self.assertNotIn(".after(", source)


if __name__ == "__main__":
    unittest.main()
