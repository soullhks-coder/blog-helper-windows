from __future__ import annotations

import copy
import inspect
import tempfile
import unittest
from pathlib import Path
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
    def test_preferences_round_trip_with_links_and_explicit_empty_list(self):
        preferences = {
            "wordpress": {"thumbnail_preset": 1, "prompt_id": "wordpress-default", "writing_links": [link("wp")]},
            "tistory:tistory_1": {"thumbnail_preset": 2, "prompt_id": "tistory-default", "writing_links": []},
            "blogspot:blogspot_2": {"thumbnail_preset": 0, "prompt_id": "blogspot-default", "writing_links": [link("blogger")]},
        }
        with tempfile.TemporaryDirectory() as directory:
            settings = main.WordPressSettings(blog_writing_preferences=preferences)
            with (
                patch.object(main, "STATE_FILE", Path(directory) / "app_state.json"),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()
        self.assertEqual(loaded.blog_writing_preferences, preferences)

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
