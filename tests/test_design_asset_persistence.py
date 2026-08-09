import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class DesignAssetPersistenceTests(unittest.TestCase):
    def test_managed_copy_survives_original_file_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "family thumbnail.png"
            source.write_bytes(b"stable-thumbnail-image")
            managed_dir = root / "app-data" / "Design Assets"

            with patch.object(main, "DESIGN_ASSET_DIR", managed_dir):
                stored_path = Path(
                    main.persist_design_asset(source, "thumbnail-background")
                )
                source.unlink()

                self.assertTrue(stored_path.exists())
                self.assertEqual(stored_path.parent, managed_dir.resolve())
                self.assertEqual(stored_path.read_bytes(), b"stable-thumbnail-image")
                self.assertEqual(
                    main.persist_design_asset(stored_path, "thumbnail-background"),
                    str(stored_path.resolve()),
                )

    def test_legacy_thumbnail_and_cardnews_paths_are_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            thumbnail = root / "thumbnail.png"
            cardnews = root / "cardnews.png"
            slide = root / "slide.png"
            thumbnail.write_bytes(b"thumbnail")
            cardnews.write_bytes(b"cardnews")
            slide.write_bytes(b"slide")
            managed_dir = root / "app-data" / "Design Assets"
            payload = {
                "thumbnail_background_image_path": str(thumbnail),
                "cardnews_background_image_path": str(cardnews),
                "cardnews_slide_styles": [
                    {"background_image_path": str(slide)},
                ],
            }

            with patch.object(main, "DESIGN_ASSET_DIR", managed_dir):
                self.assertTrue(main.migrate_design_assets_payload(payload))
                migrated_paths = (
                    payload["thumbnail_background_image_path"],
                    payload["cardnews_background_image_path"],
                    payload["cardnews_slide_styles"][0]["background_image_path"],
                )
                self.assertTrue(all(Path(item).exists() for item in migrated_paths))
                self.assertTrue(
                    all(Path(item).parent == managed_dir.resolve() for item in migrated_paths)
                )
                self.assertFalse(main.migrate_design_assets_payload(payload))

    def test_missing_legacy_path_is_preserved_instead_of_erased(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing-image.png"
            managed_dir = Path(directory) / "managed"
            with patch.object(main, "DESIGN_ASSET_DIR", managed_dir):
                self.assertEqual(
                    main.persist_design_asset(missing, "thumbnail-background"),
                    str(missing),
                )


class WritingUiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MAIN_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.methods = {
            node.name: node
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _method_source(self, method_name: str) -> str:
        return ast.get_source_segment(self.source, self.methods[method_name]) or ""

    def test_new_analysis_does_not_reset_thumbnail_design(self) -> None:
        method_source = self._method_source("start_analysis")

        self.assertNotIn('self.thumbnail_background_image_path = ""', method_source)
        self.assertNotIn("self.thumbnail_image_scale_var.set(100)", method_source)
        self.assertNotIn("self.thumbnail_image_opacity_var.set(100)", method_source)

    def test_link_rows_use_theme_palette_and_empty_area_collapses(self) -> None:
        add_row_source = self._method_source("_add_link_row")
        visibility_source = self._method_source("_update_link_rows_visibility")

        self.assertIn("palette = self._theme_palette()", add_row_source)
        self.assertNotIn('fg_color="#111826"', add_row_source)
        self.assertNotIn('fg_color="#0b1220"', add_row_source)
        self.assertIn("self.link_hint_label.grid_remove()", visibility_source)
        self.assertIn("self.link_list_frame.grid_remove()", visibility_source)

    def test_sidebar_uses_theme_contrast_without_vertical_divider(self) -> None:
        layout_source = self._method_source("_build_layout")
        palette_source = self._method_source("_theme_palette")

        self.assertNotIn("sidebar_divider", self.source)
        self.assertIn('fg_color=palette["sidebar"]', layout_source)
        self.assertIn('"shell": "#05080e"', palette_source)
        self.assertIn('"sidebar": "#0a111b"', palette_source)


if __name__ == "__main__":
    unittest.main()
