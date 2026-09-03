import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class SidebarMenuLabelTests(unittest.TestCase):
    def test_defaults_cover_requested_editable_menus(self) -> None:
        self.assertEqual(
            main.SIDEBAR_MENU_DEFAULT_LABELS,
            {
                "writing": "블로그글쓰기",
                "automation": "블로그자동화",
                "naver_blog": "N블로그자동화",
                "naver_kin": "N지식인자동화",
                "public_data": "공공데이터",
                "prompts": "프롬프트관리",
                "settings": "환경설정",
            },
        )

    def test_emoji_labels_are_preserved_and_blank_labels_use_defaults(self) -> None:
        labels = main.normalize_sidebar_menu_labels(
            {
                "writing": "✍️ 블로그글쓰기",
                "automation": "   ",
                "settings": "⚙️ 환경설정",
            }
        )

        self.assertEqual(labels["writing"], "✍️ 블로그글쓰기")
        self.assertEqual(labels["automation"], "블로그자동화")
        self.assertEqual(labels["naver_blog"], "N블로그자동화")
        self.assertEqual(labels["settings"], "⚙️ 환경설정")

    def test_bootstrap_icon_defaults_cover_every_sidebar_menu(self) -> None:
        self.assertEqual(
            set(main.SIDEBAR_MENU_DEFAULT_ICONS),
            set(main.SIDEBAR_MENU_DEFAULT_LABELS),
        )
        self.assertEqual(
            main.SIDEBAR_MENU_DEFAULT_ICONS["writing"],
            "pencil-square",
        )
        self.assertEqual(
            main.BOOTSTRAP_ICON_CODEPOINTS["pencil-square"],
            0xF4CA,
        )

    def test_bootstrap_icon_selection_supports_none_and_rejects_unknown_names(self) -> None:
        icons = main.normalize_sidebar_menu_icons(
            {
                "writing": "",
                "automation": "stars",
                "settings": "not-a-bootstrap-icon",
            }
        )

        self.assertEqual(icons["writing"], "")
        self.assertEqual(icons["automation"], "stars")
        self.assertEqual(icons["settings"], "gear")

    def test_bootstrap_icon_manual_input_accepts_classes_html_and_codepoints(self) -> None:
        expected = "pencil-square"
        for value in (
            "bi-pencil-square",
            "pencil-square",
            '<i class="bi bi-pencil-square"></i>',
            '<svg class="bi bi-pencil-square"></svg>',
        ):
            with self.subTest(value=value):
                self.assertEqual(main.normalize_bootstrap_icon_spec(value), expected)

        for value in ("U+F4CA", r"\F4CA", r"\uF4CA", "&#xF4CA;"):
            with self.subTest(value=value):
                self.assertEqual(main.normalize_bootstrap_icon_spec(value), "U+F4CA")
                self.assertEqual(main.bootstrap_icon_codepoint(value), 0xF4CA)

    def test_manual_input_can_use_icons_outside_the_curated_dropdown(self) -> None:
        self.assertTrue(main.BOOTSTRAP_ICONS_CSS_PATH.is_file())
        self.assertGreater(len(main.bootstrap_icon_catalog()), 2_000)
        self.assertEqual(
            main.normalize_bootstrap_icon_spec("bi-airplane-engines"),
            "airplane-engines",
        )
        self.assertEqual(
            main.bootstrap_icon_codepoint("airplane-engines"),
            0xF7CB,
        )

    def test_custom_menu_labels_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                sidebar_menu_labels=main.normalize_sidebar_menu_labels(
                    {
                        "writing": "✍️ 블로그글쓰기",
                        "naver_blog": "🟢 N블로그자동화",
                        "settings": "⚙️ 환경설정",
                    }
                ),
                sidebar_menu_icons=main.normalize_sidebar_menu_icons(
                    {
                        "writing": "airplane-engines",
                        "naver_blog": "image",
                        "settings": "",
                    }
                ),
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(
                    main.PromptFileStore,
                    "load_into",
                    side_effect=lambda value: value,
                ),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

        self.assertEqual(
            loaded.sidebar_menu_labels["writing"],
            "✍️ 블로그글쓰기",
        )
        self.assertEqual(
            loaded.sidebar_menu_labels["naver_blog"],
            "🟢 N블로그자동화",
        )
        self.assertEqual(
            loaded.sidebar_menu_labels["settings"],
            "⚙️ 환경설정",
        )
        self.assertEqual(loaded.sidebar_menu_icons["writing"], "airplane-engines")
        self.assertEqual(loaded.sidebar_menu_icons["naver_blog"], "image")
        self.assertEqual(loaded.sidebar_menu_icons["settings"], "")

    def test_bootstrap_icon_font_is_bundled_and_can_render_pencil_square(self) -> None:
        self.assertTrue(main.BOOTSTRAP_ICONS_FONT_PATH.is_file())
        app = type("AppStub", (), {"_sidebar_icon_image_cache": {}})()

        image = main.KeywordApp._bootstrap_sidebar_icon_image(
            app,
            "pencil-square",
            "#6dadff",
        )

        self.assertIsNotNone(image)

    def test_theme_page_exposes_menu_settings_below_theme_settings(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        theme_source = ast.get_source_segment(
            source,
            methods["_build_theme_settings_card"],
        ) or ""
        menu_source = ast.get_source_segment(
            source,
            methods["_build_menu_settings_card"],
        ) or ""
        layout_source = ast.get_source_segment(
            source,
            methods["_build_layout"],
        ) or ""
        apply_source = ast.get_source_segment(
            source,
            methods["_apply_sidebar_menu_labels"],
        ) or ""

        self.assertIn("self._build_menu_settings_card()", theme_source)
        self.assertIn('text="메뉴 설정"', menu_source)
        self.assertIn("SIDEBAR_MENU_DEFAULT_LABELS.items()", menu_source)
        self.assertIn("_on_sidebar_menu_label_changed", menu_source)
        self.assertIn("BOOTSTRAP_ICON_OPTIONS", menu_source)
        self.assertIn("ctk.CTkComboBox", menu_source)
        self.assertIn('"<Return>"', menu_source)
        self.assertIn('"<FocusOut>"', menu_source)
        self.assertIn("_on_sidebar_menu_icon_changed", menu_source)
        self.assertIn("_on_sidebar_menu_icon_manual_input", menu_source)
        self.assertIn("sidebar_menu_label", layout_source)
        self.assertIn("writing_nav_button", apply_source)
        self.assertIn("naver_blog_nav_button", apply_source)
        self.assertIn("settings_nav_button", apply_source)


if __name__ == "__main__":
    unittest.main()
