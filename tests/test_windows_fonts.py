import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import ImageFont

import windows_fonts


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WindowsFontTests(unittest.TestCase):
    def setUp(self):
        self.registry = patch.dict(windows_fonts._registered, {}, clear=True)
        self.registry.start()
        self.addCleanup(self.registry.stop)
        family = patch.object(windows_fonts, "_family", "Malgun Gothic")
        family.start()
        self.addCleanup(family.stop)
        self.ctk = SimpleNamespace(ThemeManager=SimpleNamespace(theme={"CTkFont": {"family": "Mac font"}}))
        self.gdi = SimpleNamespace(AddFontResourceExW=Mock(return_value=1),
                                   RemoveFontResourceExW=Mock(return_value=True))

    def configure(self, resource_dir=PROJECT_ROOT, platform_name="win32"):
        return windows_fonts.configure_windows_fonts(
            self.ctk, resource_dir, platform_name=platform_name, gdi32=self.gdi,
        )

    def test_supplied_regular_and_bold_fonts_are_valid(self):
        for filename, style in (("NanumGothic.ttf", "Regular"), ("NanumGothicBold.ttf", "Bold")):
            font = ImageFont.truetype(str(PROJECT_ROOT / "assets" / "fonts" / filename), 16)
            self.assertEqual(font.getname(), ("NanumGothic", style))
        license_text = (PROJECT_ROOT / "assets/fonts/NanumGothic-OFL.txt").read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE", license_text)
        self.assertIn("NHN", license_text)

    def test_windows_loads_both_fonts_privately_once_before_widgets(self):
        self.assertTrue(self.configure())
        self.assertEqual(self.gdi.AddFontResourceExW.call_count, 2)
        for call in self.gdi.AddFontResourceExW.call_args_list:
            path, flags, reserved = call.args
            self.assertIn(Path(path).name, windows_fonts.FONT_FILES)
            self.assertEqual(flags, 0x10)
            self.assertIsNone(reserved)
        self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], "NanumGothic")
        self.assertEqual(windows_fonts.ui_font_family(platform_name="win32"), "NanumGothic")
        self.assertTrue(self.configure())
        self.assertEqual(self.gdi.AddFontResourceExW.call_count, 2)
        windows_fonts.release_windows_fonts()
        self.assertEqual(self.gdi.RemoveFontResourceExW.call_count, 2)
        self.assertFalse(windows_fonts._registered)

    def test_macos_does_not_load_fonts_or_change_theme(self):
        with patch.object(Path, "is_file", side_effect=AssertionError("Mac must not inspect Windows fonts")):
            self.assertFalse(self.configure(platform_name="darwin"))
        self.gdi.AddFontResourceExW.assert_not_called()
        self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], "Mac font")
        self.assertEqual(windows_fonts.ui_font_family(platform_name="darwin"), "Apple SD Gothic Neo")
        self.assertEqual(windows_fonts.ui_font_family("Menlo", platform_name="darwin"), "Menlo")

    def test_missing_assets_fall_back_without_crashing(self):
        self.assertFalse(self.configure(resource_dir=PROJECT_ROOT / "missing-font-resources"))
        self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], "Malgun Gothic")
        self.gdi.AddFontResourceExW.assert_not_called()

    def test_partial_failure_rolls_back_registration_and_falls_back(self):
        self.gdi.AddFontResourceExW.side_effect = [1, 0]
        self.assertFalse(self.configure())
        self.gdi.RemoveFontResourceExW.assert_called_once()
        self.assertFalse(windows_fonts._registered)
        self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], "Malgun Gothic")

    def test_registration_api_failure_is_nonfatal(self):
        self.gdi.AddFontResourceExW.side_effect = OSError("GDI unavailable")
        self.assertFalse(self.configure())
        self.assertFalse(windows_fonts._registered)

    def test_unicode_resource_paths_are_passed_to_wide_character_api(self):
        resource_dir = PROJECT_ROOT / "윈도우 사용자 · 테스트"
        with patch.object(Path, "is_file", return_value=True):
            self.assertTrue(self.configure(resource_dir=resource_dir))
        self.assertIn("윈도우 사용자 · 테스트", self.gdi.AddFontResourceExW.call_args.args[0])

    def test_failed_cleanup_does_not_block_startup_fallback(self):
        self.gdi.AddFontResourceExW.side_effect = [1, 0]
        self.gdi.RemoveFontResourceExW.side_effect = OSError("GDI cleanup unavailable")
        self.assertFalse(self.configure())
        self.assertFalse(windows_fonts._registered)
        self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], "Malgun Gothic")

    def test_windows_named_fonts_change_only_the_family(self):
        self.configure()
        fonts = {}

        def font(name, **_kwargs):
            fonts.setdefault(name, Mock())
            return fonts[name]

        root = Mock()
        with patch.object(windows_fonts.tkfont, "nametofont", side_effect=font):
            windows_fonts.configure_windows_named_fonts(root, platform_name="win32")
        self.assertIn("TkMenuFont", fonts)
        self.assertIn("TkTextFont", fonts)
        for item in fonts.values():
            item.configure.assert_called_once_with(family="NanumGothic")
        root.option_add.assert_called_once_with("*Font", "TkDefaultFont")

    def test_macos_named_fonts_remain_untouched(self):
        root = Mock()
        with patch.object(windows_fonts.tkfont, "nametofont") as font:
            windows_fonts.configure_windows_named_fonts(root, platform_name="darwin")
        font.assert_not_called()
        root.option_add.assert_not_called()

    def test_windows_bundle_includes_font_files_and_license_only(self):
        tree = ast.parse((PROJECT_ROOT / "BlogHelper.spec").read_text(encoding="utf-8"))
        entries = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Tuple):
                try:
                    entries.append(ast.literal_eval(node))
                except (ValueError, TypeError):
                    pass
        for name in (*windows_fonts.FONT_FILES, "NanumGothic-OFL.txt"):
            self.assertIn((f"assets/fonts/{name}", "assets/fonts"), entries)
        # macOS now bundles the same opt-in font choices, too.
        self.assertIn("assets/fonts/NanumGothic.ttf", (PROJECT_ROOT / "BlogHelperMac.spec").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
