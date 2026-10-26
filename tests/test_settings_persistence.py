"""Isolated state-file recovery, concurrent saves and account retention."""
from contextlib import ExitStack
import copy
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from settings_persistence import SettingsFile, SettingsReadError, file_lock, merge_settings


def _process_updates(path, prefix):
    store = SettingsFile(path)
    for index in range(15):
        store.update(lambda payload: {**payload, f"{prefix}-{index}": index})


class SettingsFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "app_state.json"
        self.store = SettingsFile(self.path)

    def test_no_change_does_not_replace_or_backup_again(self):
        self.store.update(lambda _previous: {"connected": True})
        with patch("settings_persistence.os.replace") as replace:
            self.store.update(lambda previous: previous)
        replace.assert_not_called()
        self.assertFalse(self.store.backup.exists())

    def test_previous_complete_state_recovers_truncated_primary(self):
        self.store.update(lambda _old: {"account": "first"})
        self.store.update(lambda _old: {"account": "second"})
        corrupt = b'{"account":"\xec\x95'
        self.path.write_bytes(corrupt)
        self.assertEqual(self.store.read(), {"account": "first"})
        self.store.update(lambda previous: {**previous, "theme": "light"})
        self.assertEqual(self.store.read(), {"account": "first", "theme": "light"})
        self.assertEqual(self.path.with_suffix(".json.corrupt").read_bytes(), corrupt)

    def test_unrecoverable_file_is_never_overwritten_with_defaults(self):
        self.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(SettingsReadError):
            self.store.update(lambda _previous: {"account": "default"})
        self.assertEqual(self.path.read_text(), "broken")

    def test_interrupted_replace_keeps_primary_and_removes_unique_temporary(self):
        self.store.update(lambda _previous: {"account": "good"})
        import settings_persistence as persistence
        original = persistence.os.replace
        def replace(source, target):
            if Path(target) == self.path:
                raise OSError("fixture interruption")
            original(source, target)
        with patch.object(persistence.os, "replace", side_effect=replace):
            with self.assertRaises(OSError):
                self.store.update(lambda _previous: {"account": "new"})
        self.assertEqual(self.store.read(), {"account": "good"})
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_separate_processes_cannot_lose_each_others_updates(self):
        context = multiprocessing.get_context("spawn")
        processes = [context.Process(target=_process_updates, args=(str(self.path), name)) for name in ("a", "b")]
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            if process.is_alive():
                process.terminate()
                process.join()
                self.fail("Concurrent state transaction did not finish")
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(self.store.read()), 30)
        self.assertIsInstance(json.loads(self.store.backup.read_text()), dict)

    def test_same_process_lock_has_a_bounded_wait(self):
        errors = []
        def acquire():
            try:
                with file_lock(self.path, timeout=.05):
                    pass
            except SettingsReadError:
                errors.append("timeout")
        with file_lock(self.path):
            thread = threading.Thread(target=acquire)
            thread.start()
            thread.join(.5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["timeout"])


class AppStateRetentionTests(unittest.TestCase):
    def setUp(self):
        import main
        self.main = main
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "app_state.json"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(main, "STATE_FILE", self.path))
        self.stack.enter_context(patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value))
        self.stack.enter_context(patch.object(main.KeychainStore, "load_secret", return_value=""))
        self.secret = self.stack.enter_context(patch.object(main.KeychainStore, "save_secret", return_value=True))
        self.profiles = main.normalize_tistory_profiles([])
        self.profiles[1].update(blog_url="https://second.tistory.com", write_url="https://second.tistory.com/manage/newpost", kakao_account="fixture")

    def save_initial(self):
        settings = self.main.WordPressSettings(tistory_profiles=self.profiles)
        self.main.AppStateStore.save(settings, save_secrets=False)
        return settings

    def test_normalized_three_empty_slots_do_not_erase_saved_accounts_on_autosave(self):
        self.save_initial()
        stale = self.main.WordPressSettings(tistory_profiles=self.main.normalize_tistory_profiles([]), writing_topic="new topic")
        self.main.AppStateStore.save(stale, save_secrets=False)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["tistory_profiles"][1]["blog_url"], self.profiles[1]["blog_url"])
        self.assertEqual(saved["tistory_profiles"][1]["kakao_account"], "fixture")
        self.assertEqual(saved["writing_topic"], "new topic")
        self.assertEqual(stale.tistory_profiles[1]["blog_url"], self.profiles[1]["blog_url"])

    def test_stale_object_keeps_other_slot_connection_and_new_field_updates(self):
        self.save_initial()
        active, stale = self.main.AppStateStore.load(), self.main.AppStateStore.load()
        active.tistory_profiles[2]["blog_url"] = "https://third.tistory.com"
        self.main.AppStateStore.save(active, save_secrets=False)
        self.main.AppStateStore.update_fields(window_geometry="1500x900", future_schema_field={"keep": True})
        stale.tistory_profiles[0]["last_prompt_id"] = "selected-prompt"
        stale.writing_topic = "edited topic"
        self.main.AppStateStore.save(stale, save_secrets=False)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["tistory_profiles"][2]["blog_url"], "https://third.tistory.com")
        self.assertEqual(saved["tistory_profiles"][0]["last_prompt_id"], "selected-prompt")
        self.assertEqual(saved["window_geometry"], "1500x900")
        self.assertEqual(saved["future_schema_field"], {"keep": True})

    def test_explicit_reset_can_still_clear_an_account(self):
        self.save_initial()
        settings = self.main.AppStateStore.load()
        settings.tistory_profiles[1].update(blog_url="", write_url="", kakao_account="")
        self.main.AppStateStore.save(settings)
        self.assertEqual(json.loads(self.path.read_text())["tistory_profiles"][1]["blog_url"], "")

    def test_explicit_profile_reset_without_secret_save_is_not_backfilled(self):
        self.save_initial()
        settings = self.main.AppStateStore.load()
        settings.tistory_profiles[1].update(blog_url="", write_url="", kakao_account="")
        self.main.AppStateStore.save(settings, save_secrets=False, allow_profile_clear=True)
        self.assertEqual(json.loads(self.path.read_text())["tistory_profiles"][1]["blog_url"], "")

    def test_wordpress_credential_uses_the_current_persisted_username(self):
        settings = self.main.WordPressSettings(username="old", app_password="old-password")
        self.main.AppStateStore.save(settings)
        stale = self.main.AppStateStore.load()
        self.main.AppStateStore.update_fields(username="current")
        self.secret.reset_mock()
        stale.app_password = "updated-password"
        self.main.AppStateStore.save(stale)
        self.secret.assert_called_once_with(self.main.KEYCHAIN_WP_PREFIX + "current", "updated-password")

    def test_credential_writes_cannot_finish_out_of_json_commit_order(self):
        started, release, second_done = threading.Event(), threading.Event(), threading.Event()
        calls, errors = [], []
        def store(_account, value):
            if value == "first":
                started.set()
                self.assertTrue(release.wait(2))
            calls.append(value)
            return True
        def save(value):
            try:
                self.main.AppStateStore.save(self.main.WordPressSettings(gpt_api_key=value))
            except Exception as exc:
                errors.append(exc)
            finally:
                if value == "second":
                    second_done.set()
        self.secret.side_effect = store
        first = threading.Thread(target=save, args=("first",))
        second = threading.Thread(target=save, args=("second",))
        first.start()
        try:
            self.assertTrue(started.wait(2))
            second.start()
            self.assertFalse(second_done.wait(.1))
        finally:
            release.set()
            first.join(3)
            if second.ident is not None:
                second.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(json.loads(self.path.read_text())["gpt_api_key_fallback"], "second")

    def test_unrelated_and_identical_saves_do_not_rewrite_any_credential(self):
        settings = self.main.WordPressSettings(gpt_api_key="fixture-gpt", gemini_api_key="fixture-gemini")
        self.main.AppStateStore.save(settings)
        self.assertEqual(self.secret.call_count, 2)
        self.secret.reset_mock()
        self.main.AppStateStore.save(settings)
        settings.app_title = "changed title"
        self.main.AppStateStore.save(settings)
        self.secret.assert_not_called()
        settings.gpt_api_key = "new-fixture-gpt"
        self.main.AppStateStore.save(settings)
        self.secret.assert_called_once_with(self.main.KEYCHAIN_GPT_ACCOUNT, "new-fixture-gpt")

    def test_ui_settings_clone_retains_its_baseline(self):
        original = self.save_initial()
        clone = self.main.WordPressSettings(tistory_profiles=copy.deepcopy(original.tistory_profiles))
        self.main.AppStateStore.copy_tracking(original, clone)
        clone._state_baseline["tistory_profiles"][1]["blog_url"] = "changed baseline"
        self.assertNotEqual(original._state_baseline["tistory_profiles"][1]["blog_url"], "changed baseline")

    def test_corruption_without_backup_blocks_later_autosave(self):
        self.path.write_text("broken", encoding="utf-8")
        settings = self.main.AppStateStore.load()
        self.main.AppStateStore.save(settings, save_secrets=False)
        self.main.AppStateStore.update_fields(tistory_profiles=[])
        with self.assertRaises(SettingsReadError):
            self.main.AppStateStore.save(settings)
        self.assertEqual(self.path.read_text(), "broken")

    def test_blank_profile_protection_handles_old_null_fields(self):
        result = merge_settings({"tistory_profiles": None}, {"tistory_profiles": []}, autosave=True)
        self.assertEqual(result["tistory_profiles"], [])


if __name__ == "__main__":
    unittest.main()
