"""Deterministic backgrounds, backwards-compatible presets and color picking."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import xml.etree.ElementTree as ET

import main
import design_backgrounds as design


class BackgroundTests(unittest.TestCase):
    def test_seven_styles_and_safe_fallback(self):
        self.assertEqual(design.BACKGROUND_STYLES, ("단색", "그라데이션", "모눈종이", "메모지", "도트", "사선", "줄무늬"))
        for value in (None, "", "unknown", {}, ["도트"], '<script>bad</script>'):
            self.assertEqual(design.normalize_style(value), "단색")

    def test_colors_allow_custom_hex_but_never_markup(self):
        self.assertEqual(design.normalize_color(" #AbC "), "#aabbcc")
        self.assertEqual(main.thumbnail_color_value("#AfE0Cb"), "#afe0cb")
        self.assertEqual(main.thumbnail_color_value("민트색"), "#a7ece7")
        self.assertEqual(main.thumbnail_color_value("없음"), "")
        for value in (None, 'red" onclick="bad', "url(https://example.com)", "#fff0", "#zzzzzz"):
            self.assertEqual(design.normalize_color(value), design.DEFAULT_PATTERN_COLOR)

    def test_flat_background_is_exactly_the_old_solid_fill(self):
        self.assertEqual(design.background_shapes(600, 300), [("rectangle", (0, 0, 600, 300), "#ffffff", 0)])

    def test_svg_and_canvas_use_identical_shapes_colors_and_order(self):
        for style in design.BACKGROUND_STYLES:
            with self.subTest(style=style):
                shapes = design.background_shapes(600, 300, style, "#fffaf1", "#ec74a0")
                canvas = Mock()
                design.draw_background(canvas, 600, 300, style, "#fffaf1", "#ec74a0")
                calls = canvas.method_calls
                group = ET.fromstring(design.background_svg(600, 300, style, "#fffaf1", "#ec74a0"))
                if style == "그라데이션":
                    stops = group.findall('.//stop')
                    self.assertEqual([item.get('stop-color') for item in stops], [shapes[1][2], shapes[-1][2]])
                    self.assertIsNotNone(group.find('rect'))
                    continue
                self.assertEqual(len(group), len(shapes))
                self.assertEqual(len(calls), len(shapes))
                for (kind, coords, color, _stroke), call, element in zip(shapes, calls, group):
                    self.assertEqual(call[0], "create_" + kind)
                    self.assertEqual(call[1], coords)
                    self.assertEqual(call[2]["fill"], color)
                    self.assertEqual(element.get("stroke" if kind == "line" else "fill"), color)

    def test_gradient_ends_use_both_requested_colors(self):
        shapes = design.background_shapes(400, 400, "그라데이션", "#fffaf1", "#c0d8ff")
        self.assertEqual(shapes[1][2], "#fffaf1")
        self.assertEqual(shapes[-1][2], "#c0d8ff")
        self.assertEqual(shapes[-1][1][-1], 400)

    def test_memo_margin_and_grid_have_different_geometry(self):
        memo = design.background_shapes(400, 400, "메모지")
        grid = design.background_shapes(400, 400, "모눈종이")
        self.assertEqual(sum(c[0] == "line" and c[1][0] == c[1][2] for c in memo), 1)
        self.assertGreater(sum(c[0] == "line" and c[1][0] == c[1][2] for c in grid), 10)

    def test_patterns_scale_to_wide_portrait_and_large_exports_without_unbounded_items(self):
        for width, height in ((400, 400), (600, 300), (400, 640), (2400, 150), (2400, 2400)):
            for style in design.BACKGROUND_STYLES:
                with self.subTest(size=(width, height), style=style):
                    shapes = design.background_shapes(width, height, style)
                    self.assertLess(len(shapes), 1800)
                    self.assertEqual(shapes[0][1], (0, 0, width, height))
                    if style != "단색":
                        self.assertGreater(len(shapes), 1)

    def test_non_finite_and_zero_dimensions_are_rejected(self):
        for size in ((0, 100), (-5, 200), (float("inf"), 300), (500, float("nan"))):
            with self.assertRaises(ValueError):
                design.background_shapes(*size)

    def test_export_markup_contains_no_external_resources(self):
        for style in design.BACKGROUND_STYLES:
            svg = design.background_svg(400, 400, style, '" onload="bad', "url(https://bad)")
            ET.fromstring(svg)
            self.assertNotIn("onload", svg)
            self.assertNotIn("https://", svg)


class BackgroundPersistenceTests(unittest.TestCase):
    def test_non_square_macos_uses_exact_size_renderer_not_square_quicklook(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wide.png'
            context = MagicMock()
            page = context.__enter__.return_value.chromium.launch.return_value.new_page.return_value
            def screenshot(**kwargs):
                Image.new('RGB', (600, 300), 'white').save(kwargs['path'])
            page.locator.return_value.screenshot.side_effect = screenshot
            with patch.object(main.sys, 'platform', 'darwin'), \
                 patch('playwright.sync_api.sync_playwright', return_value=context), \
                 patch.object(main, 'require_google_chrome_executable', return_value='/fake/chrome'), \
                 patch.object(main.subprocess, 'run') as quicklook:
                main.render_svg_to_png('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300"/>', path, 600, '배경')
            quicklook.assert_not_called()
            with Image.open(path) as image:
                self.assertEqual(image.size, (600, 300))

    def test_old_settings_and_all_five_presets_remain_solid(self):
        presets = main.normalize_thumbnail_presets(None, legacy={"thumbnail_bg_color": "핑크색"})
        self.assertEqual(presets[0]["bg_color"], "핑크색")
        for preset in presets:
            self.assertEqual(preset["background_style"], "단색")
            self.assertEqual(preset["cardnews_style"]["background_style"], "단색")

    def test_legacy_flat_styles_migrate_and_bad_new_values_are_sanitized(self):
        presets = main.normalize_thumbnail_presets(None, legacy={"thumbnail_background_style": "메모지",
            "thumbnail_pattern_color": "#abc", "cardnews_background_style": "도트", "cardnews_pattern_color": "#ffeefa"})
        self.assertEqual(presets[0]["background_style"], "메모지")
        self.assertEqual(presets[0]["pattern_color"], "#aabbcc")
        self.assertEqual(presets[0]["cardnews_style"]["background_style"], "도트")
        malformed = main.normalize_thumbnail_presets([{"background_style": [], "pattern_color": "bad"}])[0]
        self.assertEqual(malformed["background_style"], "단색")
        self.assertEqual(malformed["pattern_color"], design.DEFAULT_PATTERN_COLOR)

    def test_custom_styles_colors_and_individual_cards_survive_state_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = main.WordPressSettings(thumbnail_background_style="그라데이션", thumbnail_pattern_color="#3ac3a7",
                cardnews_background_style="모눈종이", cardnews_pattern_color="#61a2cc",
                thumbnail_presets=main.normalize_thumbnail_presets(None), thumbnail_active_preset=4)
            chosen = settings.thumbnail_presets[4]
            chosen.update(background_style="메모지", pattern_color="#ca75a1", bg_color="#fff6ed")
            chosen["cardnews_style"].update(background_style="도트", pattern_color="#f0a156")
            chosen["cardnews_slide_styles"] = [{"background_style": "사선", "pattern_color": "#5ba574"}]
            with patch.object(main, "STATE_FILE", Path(directory) / "state.json"), \
                 patch.object(main.PromptFileStore, "load_into", side_effect=lambda s: s), \
                 patch.object(main.KeychainStore, "load_secret", return_value=""):
                main.AppStateStore.save(settings, save_secrets=False)
                restored = main.AppStateStore.load()
            self.assertEqual(restored.thumbnail_presets[4], chosen)
            self.assertEqual(restored.thumbnail_background_style, "그라데이션")
            self.assertEqual(restored.cardnews_pattern_color, "#61a2cc")
            self.assertEqual(restored.thumbnail_presets[0]["background_style"], "단색")

    def test_static_automation_exporters_receive_selected_backgrounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            settings = main.WordPressSettings(thumbnail_background_style="메모지", thumbnail_pattern_color="#568fbc",
                thumbnail_bg_color="#fff8ea", cardnews_background_style="도트", cardnews_pattern_color="#ec89a1")
            with patch.object(main, "render_svg_to_png", return_value=path) as render:
                main.create_default_thumbnail_png(settings, "제목", path)
                self.assertIn('data-blog-helper-background-style="메모지"', render.call_args.args[0])
                self.assertIn('fill="#fff8ea"', render.call_args.args[0])
                main.create_default_cardnews_png(settings, "제목", "요약", path, 1, 2)
                self.assertIn('data-blog-helper-background-style="도트"', render.call_args.args[0])


class DesignColorPickerTests(unittest.TestCase):
    def test_preview_image_keeps_transparency_over_pattern_and_does_not_modify_source(self):
        from PIL import Image
        with Image.new('RGBA', (10, 10), (255, 0, 0, 128)) as source:
            before = source.tobytes()
            with patch.object(main.ImageTk, 'PhotoImage', side_effect=lambda img: img.copy()):
                rendered = main.KeywordApp._pillow_resized_photo(SimpleNamespace(), source, 20, 20, '#ffffff', 50, False)
            try:
                self.assertEqual(rendered.getpixel((5, 5)), (255, 0, 0, 64))
                self.assertEqual(source.tobytes(), before)
            finally:
                rendered.close()

    def test_custom_color_updates_only_the_chosen_design(self):
        for kind, field in (("thumbnail", "bg_color"), ("thumbnail", "pattern_color"), ("cardnews", "pattern_color")):
            with self.subTest(kind=kind, field=field):
                menu = Mock()
                menu.get.return_value = "흰색"
                app = SimpleNamespace(_on_thumbnail_control_changed=Mock(), _on_cardnews_control_changed=Mock())
                setattr(app, f"{kind}_{field}_menu", menu)
                with patch.object(main.colorchooser, "askcolor", return_value=((10, 20, 30), "#AaDDee")):
                    main.KeywordApp._choose_design_color(app, kind, field)
                menu.set.assert_called_once_with("#aaddee")
                getattr(app, f"_on_{kind}_control_changed").assert_called_once()

    def test_cancel_and_invalid_color_leave_previous_value_unchanged(self):
        for response in ((None, None), (None, "bad")):
            app = SimpleNamespace(thumbnail_bg_color_menu=Mock(), _on_thumbnail_control_changed=Mock())
            app.thumbnail_bg_color_menu.get.return_value = "#fffaf1"
            with patch.object(main.colorchooser, "askcolor", return_value=response):
                main.KeywordApp._choose_design_color(app, "thumbnail", "bg_color")
            app.thumbnail_bg_color_menu.set.assert_not_called()
            app._on_thumbnail_control_changed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
