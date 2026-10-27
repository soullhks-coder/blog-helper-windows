import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from test_writing_links import LinkUIStub, link


class LinkSetStub(LinkUIStub):
    _save_writing_link_set = main.KeywordApp._save_writing_link_set
    _load_writing_link_set = main.KeywordApp._load_writing_link_set

    def __init__(self, settings=None):
        super().__init__(settings)
        self.writing_link_set_menu = Mock()
        self.writing_links_enabled_var = SimpleNamespace(
            set=self.set_enabled, get=lambda: self.wordpress_settings.writing_links_enabled)
        self.link_hint_label = Mock()
        self._save_ui_state = Mock()
        self._on_writing_links_enabled_changed = Mock()

    def set_enabled(self, value):
        self.wordpress_settings.writing_links_enabled = value

    def _load_link_rows(self, links, preserve_transient=False):
        self.preserve_transient = preserve_transient
        self.links = copy.deepcopy(links)


class WritingLinkSetTests(unittest.TestCase):
    def make_app(self):
        app = LinkSetStub()
        app._restore_writing_links_for_blog("wordpress")
        app.links = [link("상단", position="본문상단", width="320"),
                     link("하단", position="본문하단", full_width=True)]
        return app

    def save(self, app, name="내 버튼", overwrite=True):
        with patch.object(main.simpledialog, "askstring", return_value=name), \
             patch.object(main.messagebox, "askyesno", return_value=overwrite), \
             patch.object(main.AppStateStore, "save_writing_link_set") as store:
            result = app._save_writing_link_set()
        return result, store

    def test_save_snapshot_captures_all_fields_and_visibility(self):
        app = self.make_app()
        app.wordpress_settings.writing_links_enabled = False
        result, store = self.save(app)
        self.assertTrue(result)
        expected = {"name": "내 버튼", "links": app.links, "enabled": False}
        self.assertEqual(app.wordpress_settings.writing_link_sets, [expected])
        self.assertEqual(store.call_args.args, (expected,))
        self.assertIsNone(store.call_args.kwargs['expected'])
        self.assertEqual(app.writing_link_set_menu.set.call_args.args, ("내 버튼",))
        self.assertEqual(app.wordpress_settings.blog_writing_preferences["wordpress"]["writing_link_set_name"], "내 버튼")
        app.links[0]["url"] = "https://changed.example"
        self.assertNotEqual(app.links, app.wordpress_settings.writing_link_sets[0]["links"])

    def test_cancel_empty_and_overlong_name_leave_sets_unchanged(self):
        for name in (None, "  ", "x" * 81):
            with self.subTest(name=name):
                app = self.make_app()
                with patch.object(main.messagebox, "showwarning"):
                    result, store = self.save(app, name)
                self.assertFalse(result)
                store.assert_not_called()
                self.assertEqual(app.wordpress_settings.writing_link_sets, [])

    def test_no_buttons_does_not_open_name_dialog(self):
        app = self.make_app()
        app.links = []
        with patch.object(main.messagebox, "showinfo"), patch.object(main.simpledialog, "askstring") as prompt:
            self.assertFalse(app._save_writing_link_set())
        prompt.assert_not_called()

    def test_duplicate_requires_confirmation_and_replaces_only_named_set(self):
        app = self.make_app()
        self.save(app, "A")
        self.save(app, "B")
        before = copy.deepcopy(app.wordpress_settings.writing_link_sets)
        app.links = [link("변경")]
        result, store = self.save(app, "A", overwrite=False)
        self.assertFalse(result)
        store.assert_not_called()
        self.assertEqual(app.wordpress_settings.writing_link_sets, before)
        result, _ = self.save(app, "A")
        self.assertTrue(result)
        self.assertEqual(len(app.wordpress_settings.writing_link_sets), 2)
        self.assertEqual(app.wordpress_settings.writing_link_sets[0]["links"], app.links)
        self.assertEqual(app.wordpress_settings.writing_link_sets[1], before[1])

    def test_save_disk_failure_does_not_claim_success_or_replace_library(self):
        app = self.make_app()
        before = copy.deepcopy(app.wordpress_settings.blog_writing_preferences)
        with patch.object(main.simpledialog, "askstring", return_value="새 세트"), \
             patch.object(main.AppStateStore, "save_writing_link_set", side_effect=OSError("fixture")), \
             patch.object(main.messagebox, "showerror") as error:
            self.assertFalse(app._save_writing_link_set())
        error.assert_called_once()
        self.assertEqual(app.wordpress_settings.writing_link_sets, [])
        self.assertEqual(app.wordpress_settings.blog_writing_preferences, before)

    def test_load_is_detached_and_keeps_other_blogs_and_named_library(self):
        app = self.make_app()
        app.wordpress_settings.writing_links_enabled = False
        self.save(app)
        original = copy.deepcopy(app.wordpress_settings.writing_link_sets)
        app._restore_writing_links_for_blog("tistory")
        app.links = [link("티스토리 값")]
        app._restore_writing_links_for_blog("wordpress")
        with patch.object(main.AppStateStore, "update_fields"):
            self.assertTrue(app._load_writing_link_set("내 버튼"))
        self.assertTrue(app.preserve_transient)
        self.assertFalse(app.wordpress_settings.writing_links_enabled)
        app.links[0]["button_text"] = "불러온 후 편집"
        app._restore_writing_links_for_blog("tistory")
        self.assertEqual(app.links, [link("티스토리 값")])
        self.assertEqual(app.wordpress_settings.writing_link_sets, original)
        app._restore_writing_links_for_blog("wordpress")
        self.assertEqual(app.links[0]["button_text"], "불러온 후 편집")
        self.assertEqual(app.writing_link_set_menu.set.call_args.args, ("내 버튼",))

    def test_invalid_selection_is_noop_and_empty_menu_is_disabled(self):
        app = self.make_app()
        self.assertFalse(app._load_writing_link_set("없는 세트"))
        self.assertEqual(app.links[0]["button_text"], "상단")
        app._refresh_writing_link_set_menu()
        self.assertEqual(app.writing_link_set_menu.configure.call_args.kwargs["state"], "disabled")

    def test_normalization_filters_invalid_and_transient_links_and_deduplicates_names(self):
        value = [None, {"name": "empty", "links": []},
                 {"name": "generated", "links": [link("자동", transient=True)]},
                 {"name": "  한글\n 세트  ", "links": [link("버튼")]},
                 {"name": "한글 세트", "links": [link("중복")]},
                 {"name": "x" * 81, "links": [link("긴 이름")]}]
        self.assertEqual(main.normalize_writing_link_sets(value), [
            {"name": "한글 세트", "links": [link("버튼")], "enabled": True}])
        self.assertEqual(main.normalize_writing_link_sets({}), [])

    def test_named_sets_and_per_blog_selection_survive_disk_round_trip(self):
        app = self.make_app()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main, "STATE_FILE", Path(directory) / "app_state.json"), \
             patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value), \
             patch.object(main.KeychainStore, "load_secret", return_value=""), \
             patch.object(main.simpledialog, "askstring", return_value="가족 버튼"):
            self.assertTrue(app._save_writing_link_set())
            main.AppStateStore.save(app.wordpress_settings, save_secrets=False)
            loaded = main.AppStateStore.load()
        self.assertEqual(loaded.writing_link_sets, app.wordpress_settings.writing_link_sets)
        self.assertEqual(loaded.blog_writing_preferences["wordpress"]["writing_link_set_name"], "가족 버튼")
        restarted = LinkSetStub(loaded)
        restarted._restore_writing_links_for_blog("wordpress")
        self.assertEqual(restarted.writing_link_set_menu.set.call_args.args, ("가족 버튼",))
        self.assertEqual(restarted.links, app.links)

    def test_real_row_loader_keeps_transient_widgets_and_replaces_manual_rows(self):
        generated = {"transient": True, "source": "festival", "frame": Mock()}
        old = {"transient": False, "frame": Mock()}
        app = SimpleNamespace(link_list_frame=object(), link_rows=[old, generated],
                              _regrid_link_rows=Mock(), _update_add_link_button_state=Mock())
        app._add_link_row = lambda value: app.link_rows.append({"value": value})
        main.KeywordApp._load_link_rows(app, [link("new")], preserve_transient=True)
        old["frame"].destroy.assert_called_once()
        generated["frame"].destroy.assert_not_called()
        self.assertEqual(app.link_rows[0]["value"], link("new"))
        self.assertIs(app.link_rows[1], generated)

    def test_named_set_normalization_keeps_five_editable_buttons_maximum(self):
        raw = [{"name": "five", "links": [link(str(i)) for i in range(8)]}]
        result = main.normalize_writing_link_sets(raw)
        self.assertEqual(len(result[0]["links"]), 5)
        result[0]["links"][0]["url"] = "changed"
        self.assertNotEqual(result[0]["links"][0]["url"], raw[0]["links"][0]["url"])


if __name__ == "__main__":
    unittest.main()
