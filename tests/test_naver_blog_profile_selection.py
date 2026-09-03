import ast
import queue
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class _ValueStub:
    def __init__(self, value="") -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class _EntryStub(_ValueStub):
    def delete(self, _start, _end) -> None:
        self.value = ""

    def insert(self, _index, value) -> None:
        self.value = value


class _WidgetStub:
    def __init__(self) -> None:
        self.config = {}

    def configure(self, **kwargs) -> None:
        self.config.update(kwargs)


class NaverBlogProfileSelectionTests(unittest.TestCase):
    def _app_stub(self, profiles, active="블로그 1"):
        profile_vars = {}
        for index, profile in enumerate(profiles):
            for field_key in ("blog_id", "nickname", "write_url"):
                profile_vars[f"{index}:{field_key}"] = _ValueStub(
                    str(profile.get(field_key) or "")
                )
        app = SimpleNamespace(
            wordpress_settings=main.WordPressSettings(
                naver_blog_profiles=[dict(profile) for profile in profiles],
                naver_blog_active_profile=active,
            ),
            naver_blog_profile_vars=profile_vars,
            naver_blog_active_profile_var=_ValueStub(active),
            naver_blog_write_url_entry=_EntryStub("old-url"),
        )
        for method_name in (
            "_naver_blog_profiles_from_state",
            "_current_naver_blog_profiles",
            "_normalize_naver_blog_id",
            "_sync_naver_blog_write_url_from_profile",
            "_set_naver_active_profile",
        ):
            setattr(
                app,
                method_name,
                getattr(main.KeywordApp, method_name).__get__(app),
            )
        return app

    def test_profile_url_resolver_uses_url_then_id_without_cross_profile_fallback(self) -> None:
        self.assertEqual(
            main.resolved_naver_blog_profile_write_url(
                {"write_url": "blog.naver.com/second?Redirect=Write&"}
            ),
            "https://blog.naver.com/second?Redirect=Write&",
        )
        self.assertEqual(
            main.resolved_naver_blog_profile_write_url({"blog_id": "second"}),
            "https://blog.naver.com/second?Redirect=Write&",
        )
        self.assertEqual(main.resolved_naver_blog_profile_write_url({}), "")

    def test_radio_selection_updates_writing_url_and_persists_active_blog(self) -> None:
        app = self._app_stub(
            [
                {"name": "블로그 1", "blog_id": "first", "write_url": ""},
                {
                    "name": "블로그 2",
                    "blog_id": "second",
                    "write_url": "https://blog.naver.com/second?Redirect=Write&",
                },
            ]
        )

        with patch.object(main.AppStateStore, "save") as save:
            main.KeywordApp._set_naver_active_profile(app, "블로그 2")

        expected = "https://blog.naver.com/second?Redirect=Write&"
        self.assertEqual(app.naver_blog_active_profile_var.get(), "블로그 2")
        self.assertEqual(app.wordpress_settings.naver_blog_active_profile, "블로그 2")
        self.assertEqual(app.wordpress_settings.naver_blog_write_url, expected)
        self.assertEqual(app.naver_blog_write_url_entry.get(), expected)
        save.assert_called_once_with(app.wordpress_settings, save_secrets=False)

    def test_radio_selection_generates_writing_url_from_blog_id(self) -> None:
        app = self._app_stub(
            [
                {"name": "블로그 1", "blog_id": "first", "write_url": ""},
                {"name": "블로그 2", "blog_id": "second", "write_url": ""},
            ]
        )

        with patch.object(main.AppStateStore, "save"):
            app._sync_naver_blog_write_url_from_profile("블로그 2", persist=True)

        expected = "https://blog.naver.com/second?Redirect=Write&"
        self.assertEqual(app.naver_blog_write_url_entry.get(), expected)
        self.assertEqual(app.naver_blog_profile_vars["1:write_url"].get(), expected)

    def test_existing_profiles_are_migrated_to_three_distinct_browser_scopes(self) -> None:
        profiles = main.normalize_naver_blog_profiles(
            [
                {"name": "블로그 1", "blog_id": "mine"},
                {"name": "블로그 2", "blog_id": "mom"},
                {"name": "블로그 3", "blog_id": "third"},
            ]
        )

        self.assertEqual(
            [profile["profile_scope"] for profile in profiles],
            list(main.NAVER_BLOG_PROFILE_SCOPES),
        )
        self.assertEqual(len({profile["profile_path"] for profile in profiles}), 3)

    def test_writing_choices_show_only_registered_blogs(self) -> None:
        choices = main.selectable_naver_blog_profiles(
            [
                {"name": "블로그 1", "blog_id": "mine"},
                {"name": "블로그 2", "write_url": "https://blog.naver.com/mom"},
                {"name": "블로그 3"},
            ]
        )

        self.assertEqual(
            [profile["name"] for profile in choices],
            ["블로그 1", "블로그 2"],
        )

    def test_writing_choices_keep_all_slots_when_none_are_registered(self) -> None:
        choices = main.selectable_naver_blog_profiles([])

        self.assertEqual(len(choices), 3)

    def test_profile_check_starts_browser_with_clicked_blog_scope(self) -> None:
        profiles = main.normalize_naver_blog_profiles(
            [
                {"name": "블로그 1", "blog_id": "mine"},
                {"name": "블로그 2", "blog_id": "mom"},
                {"name": "블로그 3", "blog_id": "third"},
            ]
        )
        app = SimpleNamespace(
            naver_blog_worker=None,
            wordpress_settings=main.WordPressSettings(
                naver_blog_profiles=profiles,
                naver_blog_active_profile="블로그 1",
            ),
            naver_blog_active_profile_var=_ValueStub("블로그 1"),
            result_queue=queue.Queue(),
            naver_blog_status_label=_WidgetStub(),
        )

        def select_profile(profile_name, persist=False):
            app.naver_blog_active_profile_var.set(profile_name)
            app.wordpress_settings.naver_blog_active_profile = profile_name
            return "https://blog.naver.com/mom?Redirect=Write&"

        app._sync_naver_blog_write_url_from_profile = select_profile
        app._save_naver_blog_settings = lambda silent=True: None
        app._selected_naver_blog_profile = lambda: profiles[1]
        app._normalize_naver_blog_id = main.normalize_naver_blog_id

        with patch.object(main, "NaverBlogBootstrapWorker") as worker_class:
            main.KeywordApp._start_naver_blog_bootstrap(app, "블로그 2")

        self.assertEqual(
            worker_class.call_args.kwargs["profile_scope"],
            main.NAVER_PLAYWRIGHT_PROFILE_BLOG_2,
        )
        worker_class.return_value.start.assert_called_once_with()

    def test_profile_result_is_saved_to_its_own_slot_even_if_selection_changes(self) -> None:
        app = self._app_stub(
            [
                {"name": "블로그 1", "blog_id": "mine", "write_url": ""},
                {"name": "블로그 2", "blog_id": "mom-old", "write_url": ""},
                {"name": "블로그 3", "blog_id": "third", "write_url": ""},
            ],
            active="블로그 1",
        )

        with patch.object(main.AppStateStore, "save"):
            message = main.KeywordApp._apply_naver_blog_bootstrap_result(
                app,
                {
                    "message": "로그인 확인 완료.",
                    "profile_scope": main.NAVER_PLAYWRIGHT_PROFILE_BLOG_2,
                    "blog_id": "mom-new",
                    "nickname": "엄마",
                    "write_url": "https://blog.naver.com/mom-new?Redirect=Write&",
                },
            )

        saved_profiles = app.wordpress_settings.naver_blog_profiles
        self.assertEqual(saved_profiles[0]["blog_id"], "mine")
        self.assertEqual(saved_profiles[1]["blog_id"], "mom-new")
        self.assertEqual(saved_profiles[1]["nickname"], "엄마")
        self.assertEqual(app.naver_blog_write_url_entry.get(), "old-url")
        self.assertIn("블로그 2 전용 프로필", message)

    def test_settings_ui_keeps_radio_selection_and_focus_sync_hooks(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        build_source = ast.get_source_segment(
            source,
            methods["_refresh_naver_profile_cards"],
        ) or ""
        settings_source = ast.get_source_segment(
            source,
            methods["_build_naver_blog_settings_tab"],
        ) or ""
        writing_source = ast.get_source_segment(
            source,
            methods["_build_naver_blog_writing_tab"],
        ) or ""
        writing_choices_source = ast.get_source_segment(
            source,
            methods["_refresh_naver_blog_writing_profile_choices"],
        ) or ""

        self.assertIn("CTkRadioButton", build_source)
        self.assertIn("_set_naver_active_profile", build_source)
        self.assertIn('entry.bind(', build_source)
        self.assertIn("_on_naver_profile_field_changed", build_source)
        self.assertIn("글작성 메뉴에 즉시 반영", settings_source)
        self.assertIn("naver_blog_writing_profile_frame", writing_source)
        self.assertIn("작성할 블로그", writing_choices_source)
        self.assertIn("CTkRadioButton", writing_choices_source)
        self.assertIn("_set_naver_active_profile", writing_choices_source)


if __name__ == "__main__":
    unittest.main()
