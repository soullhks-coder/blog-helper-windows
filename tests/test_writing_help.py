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
    def test_close_restores_after_native_destroy_and_only_once(self):
        calls = []
        dimmer = SimpleNamespace(detach=lambda dialog: calls.append('detach'))
        dialog = SimpleNamespace(
            _closing=False, _cancel_jobs=lambda: calls.append('cancel'),
            grab_release=lambda: calls.append('grab_release'), destroy=lambda: calls.append('destroy'),
            _owner=SimpleNamespace(_modal_dimmer=dimmer))
        WritingHelpDialog.close(dialog)
        WritingHelpDialog.close(dialog)
        self.assertEqual(calls, ['cancel', 'grab_release', 'destroy', 'detach'])

    def test_activation_prepares_in_app_popup_before_first_map(self):
        calls = []
        owner = SimpleNamespace(update_idletasks=lambda: calls.append('prepare'))
        dialog = SimpleNamespace(
            _closing=False, _owner=owner, _resize_job='pending-layout',
            master=SimpleNamespace(winfo_ismapped=lambda: True),
            winfo_exists=lambda: True, update_idletasks=lambda: calls.append('flush'),
            _fit_to_parent=lambda: calls.append('fit'),
            after_cancel=lambda job: calls.append(('cancel', job)),
            _layout=lambda: calls.append('layout'), place=lambda **values: calls.append(('place', values)),
            lift=lambda: calls.append('lift'), grab_set=lambda: calls.append('grab'),
            focus_set=lambda: calls.append('focus'))
        with patch('writing_help.modal_dimmer', return_value=SimpleNamespace(attach=lambda window: calls.append('dim'))):
            WritingHelpDialog._activate(dialog)
        self.assertEqual(calls, ['prepare', 'fit', ('cancel', 'pending-layout'), 'layout', 'dim',
                                 ('place', {'relx': .5, 'rely': .5, 'anchor': 'center'}), 'lift', 'flush', 'grab', 'focus'])
        self.assertTrue(dialog._shown)

    def test_in_app_size_fits_parent_without_native_window_repositioning(self):
        dialog = SimpleNamespace(master=SimpleNamespace(winfo_width=lambda: 430, winfo_height=lambda: 550),
                                 _get_widget_scaling=lambda: 1.0)
        with patch('writing_help.configure_changed') as configure:
            WritingHelpDialog._fit_to_parent(dialog)
        configure.assert_called_once_with(dialog, width=398, height=518)
        source = inspect.getsource(WritingHelpDialog)
        self.assertNotIn('CTkToplevel', source)
        self.assertNotIn('.geometry(', source)
        self.assertNotIn('deiconify', source)

    def test_hidden_guide_is_closed_before_replacement(self):
        hidden = Mock()
        hidden._shown = True
        hidden.winfo_exists.return_value = True
        hidden.winfo_ismapped.return_value = False
        app = SimpleNamespace(writing_help_dialog=hidden, _theme_palette=lambda: {'shell': '#fff'},
                              _tag_mousewheel_descendants=Mock())
        with patch('writing_help.WritingHelpDialog') as create:
            main.KeywordApp._open_writing_help(app, 'links')
        hidden.close.assert_called_once_with()
        create.assert_called_once_with(app, 'links', resource_dir=main.RESOURCE_DIR, palette={'shell': '#fff'})

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
        self.assertIn("modal_dimmer(self._owner).attach(self)", source)
        self.assertIn('self._escape_tag, "<Escape>"', source)
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
