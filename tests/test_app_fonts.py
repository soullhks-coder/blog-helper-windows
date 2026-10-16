import ast
from contextlib import ExitStack
import inspect
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import ImageFont

import app_fonts
import main
import windows_fonts


ROOT = Path(__file__).resolve().parents[1]


class AppFontRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for mapping in (app_fonts._registered_mac, app_fonts._default_ctk_families, windows_fonts._registered):
            self.stack.enter_context(patch.dict(mapping, {}, clear=True))
        for name in ("_selected_family", "_selected_platform", "_system_family"):
            self.stack.enter_context(patch.object(app_fonts, name, None))
        self.stack.enter_context(patch.object(windows_fonts, "_family", "Malgun Gothic"))
        self.ctk = SimpleNamespace(ThemeManager=SimpleNamespace(theme={"CTkFont": {"family": "Native default"}}))
        self.gdi = SimpleNamespace(AddFontResourceExW=Mock(return_value=1), RemoveFontResourceExW=Mock(return_value=True))
        self.mac = SimpleNamespace(register=Mock(return_value=True), unregister=Mock(return_value=True))

    def configure(self, selected, platform="darwin", resource_dir=ROOT):
        return app_fonts.configure_app_fonts(self.ctk, resource_dir, selected, platform_name=platform,
                                             gdi32=self.gdi, mac_api=self.mac)

    def test_system_is_the_default_and_does_not_inspect_or_register_assets(self):
        for platform in ("darwin", "win32"):
            with self.subTest(platform=platform), patch.object(Path, "is_file", side_effect=AssertionError("Unneeded font IO")):
                self.assertTrue(self.configure(app_fonts.SYSTEM_FONT, platform))
                self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], "Native default")
        self.mac.register.assert_not_called()
        self.gdi.AddFontResourceExW.assert_not_called()

    def test_invalid_settings_fall_back_to_system(self):
        for value in (None, "", "unknown font", {}, []):
            self.assertEqual(app_fonts.normalize_app_font(value), app_fonts.SYSTEM_FONT)

    def test_macos_registers_regular_and_bold_once_for_each_selected_font(self):
        for selected in app_fonts.FONT_OPTIONS[1:]:
            self.assertTrue(self.configure(selected))
            self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], app_fonts.FONT_FAMILIES[selected])
            self.assertEqual(app_fonts.ui_font_family(platform_name="darwin"), app_fonts.FONT_FAMILIES[selected])
            self.assertTrue(self.configure(selected))
        self.assertEqual(self.mac.register.call_count, 4)
        self.gdi.AddFontResourceExW.assert_not_called()
        app_fonts.release_macos_fonts()
        self.assertEqual(self.mac.unregister.call_count, 4)
        self.assertFalse(app_fonts._registered_mac)

    def test_windows_registers_selected_faces_privately_and_does_not_call_coretext(self):
        for selected in app_fonts.FONT_OPTIONS[1:]:
            self.assertTrue(self.configure(selected, "win32"))
            family = "NanumGothic" if selected == "나눔고딕" else "NanumSquare Neo Regular"
            self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], family)
            self.assertEqual(app_fonts.ui_font_family(platform_name="win32"), family)
            self.assertTrue(self.configure(selected, "win32"))
        self.assertEqual(self.gdi.AddFontResourceExW.call_count, 4)
        self.assertTrue(all(call.args[1:] == (0x10, None) for call in self.gdi.AddFontResourceExW.call_args_list))
        self.mac.register.assert_not_called()

    def test_macos_partial_registration_is_rolled_back_and_system_remains_usable(self):
        self.mac.register.side_effect = [True, False]
        self.assertFalse(self.configure("나눔스퀘어 네오"))
        self.mac.unregister.assert_called_once()
        self.assertFalse(app_fonts._registered_mac)
        self.assertIsNone(app_fonts._selected_family)
        self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], "Native default")

    def test_windows_partial_registration_is_rolled_back_and_system_remains_usable(self):
        self.gdi.AddFontResourceExW.side_effect = [1, 0]
        self.assertFalse(self.configure("나눔스퀘어 네오", "win32"))
        self.gdi.RemoveFontResourceExW.assert_called_once()
        self.assertFalse(windows_fonts._registered)
        self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], "Native default")

    def test_missing_assets_and_unsupported_platforms_fail_without_installing_anything(self):
        for platform in ("darwin", "win32", "linux"):
            self.assertFalse(self.configure("나눔고딕", platform, ROOT / "missing-assets"))
        self.mac.register.assert_not_called()
        self.gdi.AddFontResourceExW.assert_not_called()

    def test_system_named_fonts_are_kept_and_native_default_is_used_by_ctk(self):
        self.configure(app_fonts.SYSTEM_FONT)
        native_font = Mock()
        native_font.actual.return_value = ".AppleSystemUIFont"
        root = Mock()
        with patch.object(app_fonts.tkfont, "nametofont", return_value=native_font):
            app_fonts.configure_app_named_fonts(root, self.ctk)
        native_font.configure.assert_not_called()
        root.option_add.assert_not_called()
        self.assertEqual(self.ctk.ThemeManager.theme["CTkFont"]["family"], ".AppleSystemUIFont")
        self.assertEqual(app_fonts.ui_font_family(platform_name="darwin"), ".AppleSystemUIFont")

    def test_custom_named_fonts_only_change_family_not_size_or_weight(self):
        self.configure("나눔스퀘어 네오")
        fonts = {}

        def named_font(name, **kwargs):
            fonts.setdefault(name, Mock())
            return fonts[name]

        root = Mock()
        with patch.object(app_fonts.tkfont, "nametofont", side_effect=named_font):
            app_fonts.configure_app_named_fonts(root, self.ctk)
        for font in fonts.values():
            font.configure.assert_called_once_with(family="NanumSquare Neo")
        root.option_add.assert_called_once_with("*Font", "TkDefaultFont")

    def test_supplied_neo_faces_are_unmodified_valid_regular_and_bold_fonts(self):
        for filename, style in zip(app_fonts.FONT_FILES["나눔스퀘어 네오"], ("Regular", "Bold")):
            self.assertEqual(ImageFont.truetype(str(ROOT / "assets/fonts" / filename), 16).getname(), ("NanumSquare Neo", style))
        license_text = (ROOT / "assets/fonts/NanumSquareNeo-OFL.txt").read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE", license_text)
        self.assertIn("2022 NAVER", license_text)
        self.assertIn("Sandoll", license_text)

    def test_both_bundles_include_all_faces_and_licenses(self):
        for spec in ("BlogHelper.spec", "BlogHelperMac.spec"):
            tuples = []
            for node in ast.walk(ast.parse((ROOT / spec).read_text(encoding="utf-8"))):
                if isinstance(node, ast.Tuple):
                    try:
                        tuples.append(ast.literal_eval(node))
                    except (ValueError, TypeError):
                        pass
            for filename in (*app_fonts.FONT_FILES["나눔고딕"], *app_fonts.FONT_FILES["나눔스퀘어 네오"],
                             "NanumGothic-OFL.txt", "NanumSquareNeo-OFL.txt"):
                self.assertIn((f"assets/fonts/{filename}", "assets/fonts"), tuples)


class AppFontSettingsTests(unittest.TestCase):
    def test_preferences_round_trip_without_overwriting_other_choices(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(main, "STATE_FILE", Path(directory) / "state.json"))
            stack.enter_context(patch.object(main.PromptFileStore, "load_into", side_effect=lambda settings: settings))
            stack.enter_context(patch.object(main.KeychainStore, "load_secret", return_value=""))
            for choice in app_fonts.FONT_OPTIONS:
                settings = main.WordPressSettings(app_font=choice, app_theme="화이트테마", blog_url="https://saved.example")
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()
                self.assertEqual(loaded.app_font, choice)
                self.assertEqual(loaded.app_theme, "화이트테마")
                self.assertEqual(loaded.blog_url, "https://saved.example")

    def test_old_and_invalid_preferences_default_to_system(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state = Path(directory) / "state.json"
            stack.enter_context(patch.object(main, "STATE_FILE", state))
            stack.enter_context(patch.object(main.PromptFileStore, "load_into", side_effect=lambda settings: settings))
            stack.enter_context(patch.object(main.KeychainStore, "load_secret", return_value=""))
            self.assertEqual(main.AppStateStore.load().app_font, app_fonts.SYSTEM_FONT)
            # AppStateStore's field update exercises the real atomic JSON writer.
            for value in (None, "unknown"):
                main.AppStateStore.update_fields(app_font=value)
                self.assertEqual(main.AppStateStore.load().app_font, app_fonts.SYSTEM_FONT)

    def make_app(self):
        app = SimpleNamespace(
            wordpress_settings=main.WordPressSettings(app_font=app_fonts.SYSTEM_FONT),
            _theme_restart_pending=False, theme_menu=Mock(), font_menu=Mock(), font_status_label=Mock(),
            _read_wordpress_settings=Mock(return_value=main.WordPressSettings(blog_url="https://unsaved.example")),
            _theme_palette=lambda: {"accent": "accent"}, _restart_after_theme_change=Mock(), after=Mock(),
        )
        return app

    def test_change_saves_current_fields_and_schedules_exactly_one_restart_without_live_mutation(self):
        app = self.make_app()
        with patch.object(main.AppStateStore, "save") as save, patch.object(main.ctk, "set_appearance_mode") as live_theme:
            main.KeywordApp._on_font_changed(app, "나눔스퀘어 네오")
            main.KeywordApp._on_font_changed(app, "나눔고딕")
        self.assertTrue(app._theme_restart_pending)
        self.assertEqual(app.wordpress_settings.app_font, "나눔스퀘어 네오")
        self.assertEqual(app.wordpress_settings.blog_url, "https://unsaved.example")
        self.assertEqual(app._appearance_restart_label, "글꼴")
        save.assert_called_once_with(app.wordpress_settings, save_secrets=False)
        app.after.assert_called_once_with(350, app._restart_after_theme_change)
        live_theme.assert_not_called()
        app.font_menu.configure.assert_called_once_with(state="disabled")
        app.theme_menu.configure.assert_called_once_with(state="disabled")

    def test_selecting_existing_font_does_not_save_or_restart(self):
        app = self.make_app()
        with patch.object(main.AppStateStore, "save") as save:
            main.KeywordApp._on_font_changed(app, app_fonts.SYSTEM_FONT)
        save.assert_not_called()
        app.after.assert_not_called()

    def test_partial_ui_falls_back_to_font_only_persistence(self):
        app = self.make_app()
        app._read_wordpress_settings.side_effect = RuntimeError("UI unavailable")
        with patch.object(main.AppStateStore, "update_fields") as update:
            main.KeywordApp._on_font_changed(app, "나눔고딕")
        update.assert_called_once_with(app_font="나눔고딕")
        self.assertEqual(app.wordpress_settings.app_font, "나눔고딕")
        app.after.assert_called_once()

    def test_failed_persistence_does_not_restart_or_claim_success(self):
        app = self.make_app()
        app._read_wordpress_settings.side_effect = RuntimeError("UI unavailable")
        with patch.object(main.AppStateStore, "update_fields", side_effect=OSError("disk full")), patch.object(main.messagebox, "showerror") as error:
            main.KeywordApp._on_font_changed(app, "나눔고딕")
        error.assert_called_once()
        app.font_menu.set.assert_called_once_with(app_fonts.SYSTEM_FONT)
        self.assertFalse(app._theme_restart_pending)
        app.after.assert_not_called()

    def test_restart_failure_leaves_settings_saved_but_menus_enabled(self):
        app = self.make_app()
        app._appearance_restart_label = "글꼴"
        app._theme_restart_pending = True
        app._on_app_close = Mock()
        with patch.object(main, "launch_application_restart", side_effect=OSError("launch failed")), patch.object(main.messagebox, "showerror") as error:
            main.KeywordApp._restart_after_theme_change(app)
        self.assertFalse(app._theme_restart_pending)
        self.assertIn("글꼴 설정은 저장", error.call_args.args[1])
        app.font_menu.configure.assert_called_once_with(state="normal")
        app._on_app_close.assert_not_called()

    def test_startup_registers_fonts_before_tk_and_menu_sits_below_theme_above_menu_settings(self):
        source = inspect.getsource(main.KeywordApp.__init__)
        self.assertLess(source.index("configure_app_fonts("), source.index("super().__init__()"))
        self.assertLess(source.index("configure_app_named_fonts("), source.index("self._build_layout()"))
        theme = inspect.getsource(main.KeywordApp._build_theme_settings_card)
        self.assertLess(theme.index("self._build_font_settings_card()"), theme.index("self._build_menu_settings_card()"))
        self.assertIn("row=1", inspect.getsource(main.KeywordApp._build_font_settings_card))
        self.assertIn("row=2", inspect.getsource(main.KeywordApp._build_menu_settings_card))
        self.assertIn("app_font=normalize_app_font", inspect.getsource(main.KeywordApp._read_wordpress_settings))


if __name__ == "__main__":
    unittest.main()
