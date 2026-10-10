import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class ThumbnailPresetTests(unittest.TestCase):
    def test_legacy_thumbnail_becomes_first_of_five_presets(self):
        legacy = {
            "thumbnail_text": "기존 썸네일",
            "thumbnail_border_color": "파란색",
            "thumbnail_width": 640,
            "cardnews_border_color": "민트색",
            "cardnews_signature": "기존 카드",
            "cardnews_slide_styles": [{"border_color": "민트색"}],
        }

        presets = main.normalize_thumbnail_presets(None, legacy=legacy)

        self.assertEqual(len(presets), 5)
        self.assertEqual(presets[0]["name"], "썸네일·카드1")
        self.assertEqual(presets[0]["text"], "기존 썸네일")
        self.assertEqual(presets[0]["border_color"], "파란색")
        self.assertEqual(presets[0]["width"], 640)
        self.assertEqual(presets[0]["cardnews_style"]["border_color"], "민트색")
        self.assertEqual(presets[0]["cardnews_style"]["signature"], "기존 카드")
        self.assertEqual(presets[0]["cardnews_slide_styles"][0]["border_color"], "민트색")
        self.assertEqual(presets[1]["name"], "썸네일·카드2")
        self.assertEqual(presets[1]["text"], "")
        self.assertEqual(presets[1]["cardnews_style"]["border_color"], "민트색")
        self.assertEqual(presets[2]["name"], "썸네일·카드3")

        presets[0]["cardnews_style"]["border_color"] = "초록색"
        self.assertEqual(presets[1]["cardnews_style"]["border_color"], "민트색")

    def test_preset_indexes_are_clamped(self):
        self.assertEqual(main.normalize_thumbnail_preset_index(-10), 0)
        self.assertEqual(main.normalize_thumbnail_preset_index("1"), 1)
        self.assertEqual(main.normalize_thumbnail_preset_index(99), 4)
        self.assertEqual(main.normalize_thumbnail_preset_index("4"), 4)
        self.assertEqual(main.normalize_thumbnail_preset_index("invalid"), 0)

    def test_preset_backgrounds_are_migrated_to_managed_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.png"
            source.write_bytes(b"thumbnail-preset-image")
            managed = root / "managed"
            payload = {
                "thumbnail_presets": [
                    {
                        "background_image_path": str(source),
                        "cardnews_style": {"background_image_path": str(source)},
                        "cardnews_slide_styles": [
                            {"background_image_path": str(source)}
                        ],
                    },
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
            cardnews_migrated = Path(
                payload["thumbnail_presets"][0]["cardnews_style"]["background_image_path"]
            )
            slide_migrated = Path(
                payload["thumbnail_presets"][0]["cardnews_slide_styles"][0]["background_image_path"]
            )
            self.assertTrue(cardnews_migrated.name.startswith("thumbnail-card-set-1-cardnews-"))
            self.assertTrue(slide_migrated.name.startswith("thumbnail-card-set-1-slide-1-"))

    def test_settings_expose_preset_state_fields(self):
        settings = main.WordPressSettings()
        self.assertEqual(settings.thumbnail_presets, [])
        self.assertEqual(settings.thumbnail_active_preset, 0)
        self.assertEqual(settings.thumbnail_default_preset, 0)

    def test_three_saved_sets_are_preserved_when_expanding_to_five(self):
        old = main.normalize_thumbnail_presets(None)[:3]
        for index, preset in enumerate(old):
            preset["text"] = f"기존 {index}"
            preset["cardnews_style"]["signature"] = f"카드 {index}"
        expanded = main.normalize_thumbnail_presets(old)
        self.assertEqual(expanded[:3], old)
        self.assertEqual([item["name"] for item in expanded[3:]], ["썸네일·카드4", "썸네일·카드5"])
        expanded[4]["cardnews_style"]["signature"] = "5번 전용"
        self.assertNotEqual(expanded[3]["cardnews_style"]["signature"], "5번 전용")

    def test_fifth_preset_and_per_blog_default_survive_save_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = main.WordPressSettings(
                thumbnail_presets=main.normalize_thumbnail_presets(None),
                thumbnail_active_preset=4, thumbnail_default_preset=4,
                blog_writing_preferences={"wordpress": {"thumbnail_preset": 4, "prompt_id": "wordpress-default"}},
            )
            settings.thumbnail_presets[4]["text"] = "다섯 번째"
            settings.thumbnail_presets[4]["cardnews_style"]["signature"] = "다섯 번째 카드"
            with (patch.object(main, "STATE_FILE", Path(directory) / "state.json"),
                  patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                  patch.object(main.KeychainStore, "load_secret", return_value="")):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()
            self.assertEqual(loaded.thumbnail_presets[4]["text"], "다섯 번째")
            self.assertEqual(loaded.thumbnail_presets[4]["cardnews_style"]["signature"], "다섯 번째 카드")
            self.assertEqual(loaded.thumbnail_default_preset, 4)
            self.assertEqual(loaded.thumbnail_active_preset, 4)
            self.assertEqual(loaded.blog_writing_preferences["wordpress"]["thumbnail_preset"], 4)


if __name__ == "__main__":
    unittest.main()
