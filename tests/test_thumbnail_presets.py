import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class ThumbnailPresetTests(unittest.TestCase):
    def test_legacy_thumbnail_becomes_first_of_three_presets(self):
        legacy = {
            "thumbnail_text": "기존 썸네일",
            "thumbnail_border_color": "파란색",
            "thumbnail_width": 640,
        }

        presets = main.normalize_thumbnail_presets(None, legacy=legacy)

        self.assertEqual(len(presets), 3)
        self.assertEqual(presets[0]["name"], "썸네일1")
        self.assertEqual(presets[0]["text"], "기존 썸네일")
        self.assertEqual(presets[0]["border_color"], "파란색")
        self.assertEqual(presets[0]["width"], 640)
        self.assertEqual(presets[1]["name"], "썸네일2")
        self.assertEqual(presets[1]["text"], "")
        self.assertEqual(presets[2]["name"], "썸네일3")

    def test_preset_indexes_are_clamped(self):
        self.assertEqual(main.normalize_thumbnail_preset_index(-10), 0)
        self.assertEqual(main.normalize_thumbnail_preset_index("1"), 1)
        self.assertEqual(main.normalize_thumbnail_preset_index(99), 2)
        self.assertEqual(main.normalize_thumbnail_preset_index("invalid"), 0)

    def test_preset_backgrounds_are_migrated_to_managed_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"thumbnail-preset-image")
            managed = root / "managed"
            payload = {
                "thumbnail_presets": [
                    {"background_image_path": str(source)},
                    {},
                    {},
                ]
            }

            with patch.object(main, "DESIGN_ASSET_DIR", managed):
                changed = main.migrate_design_assets_payload(payload)

            migrated = Path(payload["thumbnail_presets"][0]["background_image_path"])
            self.assertTrue(changed)
            self.assertTrue(migrated.exists())
            self.assertEqual(migrated.parent, managed.resolve())
            self.assertTrue(migrated.name.startswith("thumbnail-preset-1-"))

    def test_settings_expose_three_preset_state_fields(self):
        settings = main.WordPressSettings()
        self.assertEqual(settings.thumbnail_presets, [])
        self.assertEqual(settings.thumbnail_active_preset, 0)
        self.assertEqual(settings.thumbnail_default_preset, 0)


if __name__ == "__main__":
    unittest.main()
