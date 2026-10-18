from __future__ import annotations

import copy
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


def link(text: str, **changes) -> dict:
    return {
        "button_text": text,
        "url": "https://example.com/" + text,
        "width": "240",
        "full_width": False,
        "position": "본문중간",
        **changes,
    }


class LinkUIStub:
    _blog_writing_identity = main.KeywordApp._blog_writing_identity
    _blog_writing_preference = main.KeywordApp._blog_writing_preference
    _remember_current_writing_links = main.KeywordApp._remember_current_writing_links
    _restore_writing_links_for_blog = main.KeywordApp._restore_writing_links_for_blog
    _writing_links_are_enabled = main.KeywordApp._writing_links_are_enabled

    def __init__(self, settings=None):
        self.wordpress_settings = settings or main.WordPressSettings()
        self.link_list_frame = object()
        self.links = []

    def _remembered_writing_prompt_id(self, _platform):
        return "saved-prompt"

    def _current_writing_links(self, include_transient=True):
        return [copy.deepcopy(item) for item in self.links if include_transient or not item.get("transient")]

    def _load_link_rows(self, links):
        self.links = copy.deepcopy(links)


class WritingLinksTests(unittest.TestCase):
    def test_visibility_defaults_on_for_existing_settings(self):
        self.assertTrue(main.WordPressSettings().writing_links_enabled)

    def test_off_preserves_settings_and_restores_independently_per_blog(self):
        app = LinkUIStub()
        app._restore_writing_links_for_blog("wordpress")
        app.links = [link("keep", width="320", full_width=True), {"button_text": "미완성", "url": ""}]
        app.wordpress_settings.writing_links_enabled = False
        app._remember_current_writing_links()
        saved = copy.deepcopy(app.wordpress_settings.blog_writing_preferences["wordpress"])
        self.assertFalse(saved["writing_links_enabled"])
        self.assertEqual(saved["writing_links"][0], app.links[0])
        self.assertEqual(saved["writing_links"][1]["button_text"], "미완성")
        app._restore_writing_links_for_blog("tistory")
        self.assertTrue(app._writing_links_are_enabled())
        app.links = [link("tistory")]
        app._restore_writing_links_for_blog("wordpress")
        self.assertFalse(app._writing_links_are_enabled())
        self.assertEqual(app.links, saved["writing_links"])
        restarted = LinkUIStub(app.wordpress_settings)
        restarted._restore_writing_links_for_blog("wordpress")
        self.assertFalse(restarted._writing_links_are_enabled())
        self.assertEqual(restarted.links, saved["writing_links"])

    def test_legacy_visibility_only_applies_to_startup_blog(self):
        app = LinkUIStub(main.WordPressSettings(writing_links=[link("legacy")], writing_links_enabled=False))
        app._restore_writing_links_for_blog("wordpress")
        self.assertFalse(app._writing_links_are_enabled())
        app._restore_writing_links_for_blog("blogspot")
        self.assertTrue(app._writing_links_are_enabled())
        app._restore_writing_links_for_blog("wordpress")
        self.assertFalse(app._writing_links_are_enabled())
        self.assertEqual(app.links, [link("legacy")])

    def test_enabled_inserts_all_positions_and_disabled_removes_only_managed_buttons(self):
        body = ('<h1>본문</h1><p>설명</p><h2>소제목</h2><p><a href="https://ordinary.example">일반 링크</a></p>'
                '<p class="not-blog-helper-link-button-wrap">일반 문단</p>')
        links = [link(position, position=position) for position in main.LINK_POSITION_OPTIONS]
        before = copy.deepcopy(links)
        inserted = main.append_external_link_buttons(body, links)
        self.assertEqual(inserted.count(main.CUSTOM_LINK_BUTTON_MARKER), 3)
        self.assertEqual(main.append_external_link_buttons(inserted, links), inserted)
        hidden = main.append_external_link_buttons(inserted, links, enabled=False)
        self.assertNotIn(main.CUSTOM_LINK_BUTTON_MARKER, hidden)
        self.assertIn('href="https://ordinary.example"', hidden)
        self.assertIn('<p class="not-blog-helper-link-button-wrap">일반 문단</p>', hidden)
        self.assertNotIn('class="blog-helper-link-button-wrap"', hidden)
        self.assertEqual(main.append_external_link_buttons(body, links, enabled=False), body)
        self.assertEqual(links, before)

    def test_preview_and_publish_body_gate_does_not_change_editor_or_saved_inputs(self):
        body = "<h2>원본 소제목</h2><p>원본 본문</p>"
        app = LinkUIStub()
        app.article_editor = SimpleNamespace(get=lambda *_: body)
        app.article_title_entry = SimpleNamespace(get=lambda: "제목")
        app.generated_article_title = ""
        app.links = [link("keep")]
        for enabled in (True, False, True):
            app.wordpress_settings.writing_links_enabled = enabled
            result = main.KeywordApp._get_current_article_html(app)
            self.assertEqual(main.CUSTOM_LINK_BUTTON_MARKER in result, enabled)
            self.assertEqual(app.article_editor.get("1.0", "end"), body)
            self.assertEqual(app.links, [link("keep")])

    def test_preferences_round_trip_with_links_and_explicit_empty_list(self):
        preferences = {
            "wordpress": {"thumbnail_preset": 1, "prompt_id": "wordpress-default", "writing_links": [link("wp")], "writing_links_enabled": False},
            "tistory:tistory_1": {"thumbnail_preset": 2, "prompt_id": "tistory-default", "writing_links": []},
            "blogspot:blogspot_2": {"thumbnail_preset": 0, "prompt_id": "blogspot-default", "writing_links": [link("blogger")]},
        }
        with tempfile.TemporaryDirectory() as directory:
            settings = main.WordPressSettings(blog_writing_preferences=preferences, writing_links_enabled=False, writing_links=[link("wp")])
            with (
                patch.object(main, "STATE_FILE", Path(directory) / "app_state.json"),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()
        self.assertEqual(loaded.blog_writing_preferences, preferences)
        self.assertFalse(loaded.writing_links_enabled)
        self.assertEqual(loaded.writing_links, [link("wp")])

    def test_links_are_isolated_between_platforms_and_profile_slots(self):
        app = LinkUIStub()
        choices = [
            ("wordpress", None), ("tistory", "티스토리 1"), ("tistory", "티스토리 2"),
            ("blogspot", "블로그스팟 1"), ("blogspot", "블로그스팟 2"),
        ]
        for index, (platform, profile_name) in enumerate(choices):
            if profile_name:
                setattr(app.wordpress_settings, platform + "_active_profile", profile_name)
            app._restore_writing_links_for_blog(platform)
            self.assertEqual(app.links, [])
            app.links = [link(str(index))]
        for index, (platform, profile_name) in enumerate(choices):
            if profile_name:
                setattr(app.wordpress_settings, platform + "_active_profile", profile_name)
            app._restore_writing_links_for_blog(platform)
            self.assertEqual(app.links, [link(str(index))])

    def test_quick_profile_switch_saves_edit_under_previous_identity(self):
        app = LinkUIStub()
        app._restore_writing_links_for_blog("tistory")
        app.links = [link("newly-pasted")]
        app.wordpress_settings.tistory_active_profile = "티스토리 2"
        app._restore_writing_links_for_blog("tistory")
        self.assertEqual(app.links, [])
        self.assertEqual(app.wordpress_settings.blog_writing_preferences["tistory:tistory_1"]["writing_links"], [link("newly-pasted")])
        self.assertEqual(app.wordpress_settings.blog_writing_preferences["tistory:tistory_2"]["writing_links"], [])

    def test_deleted_links_stay_empty_on_switch_and_restart(self):
        app = LinkUIStub(main.WordPressSettings(writing_links=[link("old")]))
        app._restore_writing_links_for_blog("wordpress")
        app.links = []
        app._restore_writing_links_for_blog("blogspot")
        app.links = [link("different")]
        app._restore_writing_links_for_blog("wordpress")
        self.assertEqual(app.links, [])
        restarted = LinkUIStub(app.wordpress_settings)
        restarted._restore_writing_links_for_blog("wordpress")
        self.assertEqual(restarted.links, [])

    def test_legacy_shared_links_migrate_only_to_startup_blog(self):
        app = LinkUIStub(main.WordPressSettings(
            home_target_platform="tistory", writing_links=[link("legacy")], thumbnail_default_preset=2
        ))
        app._restore_writing_links_for_blog("tistory")
        self.assertEqual(app.links, [link("legacy")])
        self.assertEqual(app.wordpress_settings.blog_writing_preferences["tistory:tistory_1"]["thumbnail_preset"], 2)
        self.assertEqual(app.wordpress_settings.blog_writing_preferences["tistory:tistory_1"]["prompt_id"], "saved-prompt")
        app._restore_writing_links_for_blog("wordpress")
        self.assertEqual(app.links, [])
        app._restore_writing_links_for_blog("tistory")
        self.assertEqual(app.links, [link("legacy")])

    def test_generated_links_never_replace_saved_user_buttons(self):
        app = LinkUIStub()
        app._restore_writing_links_for_blog("wordpress")
        app.links = [link("manual"), link("festival", transient=True)]
        app._remember_current_writing_links()
        self.assertEqual(app.wordpress_settings.blog_writing_preferences["wordpress"]["writing_links"], [link("manual")])

    def test_focus_out_during_row_destruction_does_not_corrupt_saved_links(self):
        app = LinkUIStub()
        app._restore_writing_links_for_blog("wordpress")
        app.links = [link("manual")]
        app._remember_current_writing_links()
        with patch.object(app, "_current_writing_links", side_effect=main.tk.TclError("destroyed entry")):
            app._remember_current_writing_links()
        self.assertEqual(app.wordpress_settings.blog_writing_preferences["wordpress"]["writing_links"], [link("manual")])
        app.links = []
        app._remember_current_writing_links()
        self.assertEqual(app.wordpress_settings.blog_writing_preferences["wordpress"]["writing_links"], [])

    def test_partial_settings_and_maximum_five_links_are_preserved(self):
        raw = [{"width": "180", "position": "unknown"}, {"full_width": True}] + [link(str(i)) for i in range(7)]
        normalized = main.normalize_writing_links(raw)
        self.assertEqual(len(normalized), 5)
        self.assertEqual(normalized[0]["width"], "180")
        self.assertEqual(normalized[0]["position"], "본문하단")
        self.assertTrue(normalized[1]["full_width"])

    def test_link_controls_follow_benchmark_in_keyword_finding_section(self):
        source = inspect.getsource(main.KeywordApp._build_writing_workflow)
        self.assertLess(source.index("self.benchmark_button.grid"), source.index("self._build_writing_link_controls(topic_card)"))
        self.assertLess(source.index("self._build_writing_link_controls(topic_card)"), source.index("options_row ="))
        self.assertNotIn("self.link_card = ctk.CTkFrame(keyword_card", source)


if __name__ == "__main__":
    unittest.main()
