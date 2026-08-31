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
        self.assertEqual(labels["settings"], "⚙️ 환경설정")

    def test_custom_menu_labels_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                sidebar_menu_labels=main.normalize_sidebar_menu_labels(
                    {
                        "writing": "✍️ 블로그글쓰기",
                        "settings": "⚙️ 환경설정",
                    }
                )
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
            loaded.sidebar_menu_labels["settings"],
            "⚙️ 환경설정",
        )

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
        self.assertIn("sidebar_menu_label", layout_source)
        self.assertIn("writing_nav_button", apply_source)
        self.assertIn("settings_nav_button", apply_source)


if __name__ == "__main__":
    unittest.main()
