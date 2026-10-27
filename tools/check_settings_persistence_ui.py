"""Real Tk/settings-store regression checks with disposable data and no network."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import copy
import faulthandler
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", choices=("dark", "light"))
    parser.add_argument("--case", choices=("profiles", "corrupt"), default="profiles")
    args = parser.parse_args()
    if not args.theme:
        for theme, case in (("dark", "profiles"), ("light", "profiles"), ("light", "corrupt")):
            subprocess.run([sys.executable, __file__, "--theme", theme, "--case", case],
                           check=True, timeout=180)
        return

    with tempfile.TemporaryDirectory(prefix="blog-helper-settings-ui-") as directory, ExitStack() as stack:
        faulthandler.dump_traceback_later(150)
        stack.callback(faulthandler.cancel_dump_traceback_later)
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        root = Path(directory)
        # Precreate the file before importing main so legacy migration cannot
        # consult an existing user's old application settings.
        state_path = root / "app_state.json"
        original_corrupt = b"{fixture-invalid-settings"
        state_path.write_bytes(original_corrupt if args.case == "corrupt" else b"{}")
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as module
        from settings_persistence import SettingsFile, SettingsReadError

        stack.enter_context(patch.object(module, "PROMPT_DIR", root / "Prompt Shortcut"))
        for name in ("load_secret", "save_secret", "delete_secret"):
            stack.enter_context(patch.object(module.KeychainStore, name,
                return_value="" if name == "load_secret" else True))
        for name in (
            "_start_remote_agent_if_enabled", "_start_update_check", "_start_update_probe", "_poll_queue",
            "_automation_publish_scheduler_tick", "_load_version_catalog", "_on_window_configure",
            "_load_home_dashboard_keywords", "_start_adsense_refresh", "_start_naver_kin_clipboard_monitor",
            "_shared_tistory_usage_tick", "_refresh_shared_tistory_usage_async",
        ):
            stack.enter_context(patch.object(module.KeywordApp, name, return_value=None))
        # A forgotten entry point must fail instead of contacting a real account.
        stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline fixture network guard")))
        stack.enter_context(patch.object(module.threading.Thread, "start", side_effect=AssertionError("Unexpected fixture worker")))
        warning = stack.enter_context(patch.object(module.messagebox, "showwarning", return_value=None))
        stack.enter_context(patch.object(module.messagebox, "showerror", side_effect=AssertionError("Unexpected fixture error dialog")))

        tistory = module.normalize_tistory_profiles([
            {"blog_url": f"https://fixture-tistory-{i}.tistory.com",
             "write_url": f"https://fixture-tistory-{i}.tistory.com/manage/newpost",
             "kakao_account": f"fixture-account-{i}", "last_prompt_id": "tistory-default"}
            for i in range(1, 4)
        ])
        blogspot = module.normalize_blogspot_profiles([
            {"blog_url": f"https://fixture-blogspot-{i}.example.com", "blog_id": str(8000 + i),
             "blog_name": f"Fixture Blogger {i}", "last_prompt_id": "blogspot-default"}
            for i in range(1, 4)
        ])
        naver = module.normalize_naver_blog_profiles([
            {"blog_id": f"fixture_naver_{i}", "nickname": f"Fixture Naver {i}",
             "write_url": f"https://blog.naver.com/fixture_naver_{i}/postwrite"}
            for i in range(1, 4)
        ])
        if args.case == "profiles":
            settings = module.WordPressSettings(
                app_theme="화이트테마" if args.theme == "light" else "블랙테마",
                window_geometry="1450x950", app_title="Fixture initial title",
                blog_url='https://fixture-wp.example', username='fixture-wp',
                thumbnail_presets=module.normalize_thumbnail_presets(None),
                tistory_profiles=tistory, tistory_active_profile=tistory[0]["name"],
                tistory_blog_url=tistory[0]["blog_url"], tistory_write_url=tistory[0]["write_url"],
                blogspot_profiles=blogspot, blogspot_active_profile=blogspot[0]["name"],
                blogspot_blog_url=blogspot[0]["blog_url"], blogspot_blog_id=blogspot[0]["blog_id"],
                blogspot_blog_name=blogspot[0]["blog_name"], naver_blog_profiles=naver,
                writing_auto_progress=False, target_platforms=["wordpress"],
            )
            module.AppStateStore.save(settings, save_secrets=False)

        print(f"Settings offline Tk: {sys.platform} {args.theme} {args.case}; constructing real app/store", flush=True)
        app = module.KeywordApp()
        app.withdraw()
        errors = []
        app.report_callback_exception = lambda *error: errors.append(error)

        def settle(delay=100):
            # CTk's first Windows mainloop changes the native title bar and
            # pumps Tk events before entering tk.mainloop(). On a slow runner
            # that can consume the scheduled quit too early, then wait forever.
            # Initialize the real window/event queue before arming our bound.
            app.update()
            app.after(delay, app.quit)
            app.mainloop()
            app.update_idletasks()
            assert not errors, errors

        def identities(payload):
            return (
                [{key: row.get(key) for key in ("profile_scope", "blog_url", "write_url", "kakao_account")}
                 for row in payload["tistory_profiles"]],
                [{key: row.get(key) for key in ("profile_scope", "blog_url", "blog_id", "blog_name")}
                 for row in payload["blogspot_profiles"]],
            )

        store = SettingsFile(module.STATE_FILE)
        try:
            if args.case == "corrupt":
                settle(1200)
                assert getattr(app.wordpress_settings, "_state_read_error", "")
                warning.assert_called()
                assert state_path.read_bytes() == original_corrupt
                module.AppStateStore.save(app.wordpress_settings, save_secrets=False)
                module.AppStateStore.update_fields(window_geometry="1200x800")
                assert state_path.read_bytes() == original_corrupt
                try:
                    module.AppStateStore.save(app.wordpress_settings, save_secrets=True)
                except SettingsReadError:
                    pass
                else:
                    raise AssertionError("Corrupt settings were allowed to be overwritten")
                assert state_path.read_bytes() == original_corrupt
                assert not state_path.with_suffix(".json.bak").exists()
                print("Settings offline Tk: corrupt startup warned, app constructed, autosave blocked, original bytes preserved", flush=True)
                return

            settle()
            expected = identities(store.read())
            app._switch_page("settings")
            app._switch_settings_section("ai")
            app._switch_settings_tab("tistory")
            for profile in tistory:
                app.tistory_profile_radios[profile["name"]].invoke()
                settle()
                assert app.tistory_active_profile_var.get() == profile["name"]
                assert app.tistory_blog_url_entry.get() == profile["blog_url"]
                assert app.tistory_write_url_entry.get() == profile["write_url"]
                assert identities(store.read()) == expected
            app._switch_settings_tab("blogspot")
            for profile in blogspot:
                app.blogspot_profile_radios[profile["name"]].invoke()
                settle()
                assert app.blogspot_active_profile_var.get() == profile["name"]
                assert app.blogspot_blog_url_entry.get() == profile["blog_url"]
                assert app.blogspot_blog_id_entry.get() == profile["blog_id"]
                assert identities(store.read()) == expected
            app.app_title_var.set("Fixture unrelated UI edit")
            app._save_ui_state_now()
            assert identities(store.read()) == expected
            print("Settings offline Tk: three Tistory and three Blogger radio callbacks plus unrelated autosave preserved all identities", flush=True)

            # The real app retains an older object and populated settings fields,
            # while another writer updates the active Tistory connection/geometry.
            updated_tistory = copy.deepcopy(store.read()["tistory_profiles"])
            updated_tistory[2].update(blog_url="https://fixture-reconnected.tistory.com",
                write_url="https://fixture-reconnected.tistory.com/manage/newpost",
                kakao_account="fixture-reconnected-account")
            module.AppStateStore.update_fields(tistory_profiles=updated_tistory,
                tistory_blog_url=updated_tistory[2]["blog_url"], tistory_write_url=updated_tistory[2]["write_url"],
                window_geometry="1210x810", fixture_external_marker="keep-external-field")
            expected = identities(store.read())
            for title in ("Fixture stale UI save one", "Fixture stale UI save two"):
                app.app_title_var.set(title)
                rebuilt = app._read_wordpress_settings(include_prompts=False)
                assert getattr(rebuilt, "_state_baseline", None) is not None
                assert rebuilt._state_source == str(module.STATE_FILE.resolve())
                app.wordpress_settings = rebuilt
                app._save_ui_state_now()
                persisted = store.read()
                assert persisted["app_title"] == title
                assert persisted["window_geometry"] == "1210x810"
                assert persisted["fixture_external_marker"] == "keep-external-field"
                assert identities(persisted) == expected
            print("Settings offline Tk: UI-reader tracking survived two stale-object autosaves; external connection/geometry preserved", flush=True)

            # Real named-save dialog callback commits only this set, then an
            # unrelated full settings save must retain another writer's set.
            app._switch_page('writing')
            app._load_link_rows([{'button_text': 'Fixture link', 'url': 'https://fixture.example/button',
                                 'full_width': True, 'position': '본문하단'}])
            with patch.object(module.simpledialog, 'askstring', return_value='Fixture button set'):
                assert app._save_writing_link_set()
            module.AppStateStore.save_writing_link_set({
                'name': 'Fixture external set', 'enabled': False,
                'links': [{'button_text': 'External', 'url': 'https://fixture.example/external'}],
            }, expected=None, identity='tistory:tistory_2')
            external_designs = copy.deepcopy(store.read()['thumbnail_presets'])
            external_designs[2].update(font_size=99, border_color='노란색', background_style='메모지')
            store.update(lambda current: {**current, 'thumbnail_presets': external_designs})
            for _ in range(2):
                app._save_ui_state_now()
                saved = store.read()
                assert len(saved['writing_link_sets']) == 2
                assert saved['thumbnail_presets'][2]['font_size'] == 99
            protected = {key: copy.deepcopy(saved[key]) for key in (
                'writing_link_sets', 'thumbnail_presets', 'tistory_profiles', 'blogspot_profiles',
                'blog_writing_preferences', 'app_title', 'app_theme')}
            app._reset_wordpress_settings()
            app._save_ui_state_now()
            saved = store.read()
            assert {key: saved[key] for key in protected} == protected
            assert not saved['blog_url'] and not saved['username']
            assert store.history_paths()
            print('Settings offline Tk: real button save, external set/design, repeated stale autosaves and WordPress-only reset preserved libraries/accounts', flush=True)

            app._reset_naver_profile(1)
            app._save_ui_state_now()
            persisted = store.read()
            assert identities(persisted) == expected
            assert not any(persisted["naver_blog_profiles"][1].get(key) for key in ("blog_id", "nickname", "write_url"))
            assert persisted["naver_blog_profiles"][0]["blog_id"] == naver[0]["blog_id"]
            assert persisted["naver_blog_profiles"][2]["blog_id"] == naver[2]["blog_id"]
            print("Settings offline Tk: explicit Naver reset remained cleared after subsequent UI autosave", flush=True)

            # Use the backup generated by a real settings transaction, then
            # corrupt only the disposable primary file and load normally.
            module.AppStateStore.update_fields(fixture_recovery_probe=True)
            state_path.write_bytes(original_corrupt)
            recovered = module.AppStateStore.load()
            assert not getattr(recovered, "_state_read_error", "")
            assert identities(store.read()) == expected
            assert state_path.with_suffix(".json.corrupt").read_bytes() == original_corrupt
            assert recovered.tistory_profiles[2]["kakao_account"] == "fixture-reconnected-account"
            assert not errors, errors
            print("Settings offline Tk: real last-good backup restored profiles; corrupt bytes retained separately", flush=True)
        finally:
            # Do not run a final application save after deliberately corrupting
            # a fixture, and cancel Tk callbacks by destroying the isolated root.
            app.destroy()


if __name__ == "__main__":
    main()
