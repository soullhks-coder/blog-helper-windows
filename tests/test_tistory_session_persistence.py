"""Local cookie/session regressions; no browser, credentials or network."""
import json
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import Mock, MagicMock, patch

import main
import tistory_session as session


def cookie(value="fixture", *, name="TSSESSION", expires=-1, domain=".tistory.com"):
    return {"name": name, "value": value, "domain": domain, "path": "/", "expires": expires,
            "httpOnly": True, "secure": True, "sameSite": "Lax"}


def state(value="fixture"):
    return {"cookies": [cookie(value)], "origins": []}


class TistorySessionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "storage-state.json"

    def save(self, payload, authenticated=True):
        return session.save_snapshot(self.path, payload, authenticated=authenticated,
                                     is_auth_cookie=main.is_tistory_auth_cookie)

    def test_newer_persistent_cookie_wins_over_old_snapshot(self):
        old = cookie("old")
        current = cookie("current", domain="tistory.com")
        self.assertEqual(session.missing_cookies([old], [current], now=100), [])
        self.assertEqual(current["value"], "current")

    def test_only_valid_missing_cookies_restore_without_duplicates(self):
        missing = cookie("missing", name="KAKAO", domain=".kakao.com", expires=200)
        expired = cookie("expired", name="expired", expires=50)
        malformed = cookie("bad", name="bad", expires="not-a-number")
        self.assertEqual(session.missing_cookies(
            [cookie("old"), missing, expired, malformed, missing, None], [cookie("current")], now=100), [missing])

    def test_failed_login_and_empty_verified_state_keep_existing_snapshot(self):
        self.save(state("good"))
        original = self.path.read_bytes()
        self.assertFalse(self.save(state("bad"), authenticated=False))
        self.assertFalse(self.save({"cookies": [], "origins": []}))
        self.assertFalse(self.save({"cookies": [cookie("", name="TSSESSION")], "origins": []}))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.path.with_name(self.path.name + ".backup").exists())

    def test_verified_state_is_atomic_and_retains_previous_snapshot(self):
        first, second = state("first"), state("second")
        self.assertTrue(self.save(first))
        self.assertTrue(self.save(second))
        self.assertEqual(json.loads(self.path.read_text()), second)
        self.assertEqual(json.loads(self.path.with_name(self.path.name + ".backup").read_text()), first)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_identical_success_and_finally_snapshots_do_not_write_twice(self):
        payload = state()
        self.save(payload)
        with patch.object(session.os, "replace") as replace:
            self.assertFalse(self.save(payload))
        replace.assert_not_called()

    def test_interrupted_primary_replace_preserves_good_snapshot(self):
        first = state("first")
        self.save(first)
        original_replace = session.os.replace
        def replace(source, destination):
            if Path(destination) == self.path:
                raise OSError("fixture interrupted write")
            return original_replace(source, destination)
        with patch.object(session.os, "replace", side_effect=replace):
            with self.assertRaises(OSError):
                self.save(state("second"))
        self.assertEqual(json.loads(self.path.read_text()), first)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_broken_json_uses_last_complete_backup_without_rewriting_primary(self):
        first = state("first")
        self.save(first)
        self.save(state("second"))
        self.path.write_text('{"cookies": [', encoding="utf-8")
        payload, recovered = session.load_snapshot(self.path)
        self.assertTrue(recovered)
        self.assertEqual(payload, first)
        self.assertEqual(self.path.read_text(), '{"cookies": [')

    def test_session_keep_days_still_applies_but_zero_expiry_stays_expired(self):
        normalized = main.normalize_tistory_storage_state({"cookies": [
            cookie("session"), cookie("expired", name="zero", expires=0),
            cookie("bad", name="bad", expires="broken"), cookie("nan", name="nan", expires=float("nan"))],
            "origins": []})
        self.assertEqual([entry["name"] for entry in normalized["cookies"]], ["TSSESSION", "zero"])
        self.assertGreater(normalized["cookies"][0]["expires"], time.time())
        self.assertEqual(normalized["cookies"][1]["expires"], 0)
        self.assertEqual([entry["name"] for entry in session.missing_cookies(normalized["cookies"], [])], ["TSSESSION"])

    def test_restore_calls_add_only_for_missing_cookie_and_logs_no_values(self):
        context = Mock()
        context.cookies.return_value = [cookie("live")]
        missing = cookie("private-missing", name="other", domain=".kakao.com")
        with patch.object(main, "load_tistory_storage_state", return_value={"cookies": [cookie("private-old"), missing]}), \
                patch.object(main, "append_runtime_log") as log:
            self.assertEqual(main.restore_tistory_session_cookies(context, "tistory_2"), 1)
        context.add_cookies.assert_called_once_with([missing])
        self.assertNotIn("private", str(log.call_args_list))

    def test_save_does_not_reinject_cookies_into_live_browser(self):
        context = Mock()
        context.storage_state.return_value = state("live")
        with patch.object(main, "tistory_profile_paths", return_value=(self.directory, self.path)), \
                patch.object(main, "append_runtime_log"):
            main.save_tistory_storage_state(context, authenticated=True)
        self.assertTrue(self.path.is_file())
        context.add_cookies.assert_not_called()

    def test_unverified_save_does_not_read_or_replace_browser_state(self):
        context = Mock()
        with patch.object(main, "tistory_profile_paths", return_value=(self.directory, self.path)):
            main.save_tistory_storage_state(context)
        context.storage_state.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_login_failure_in_bootstrap_or_publish_preserves_good_snapshot(self):
        self.save(state("good"))
        original = self.path.read_bytes()
        for operation in ("bootstrap", "publish"):
            with self.subTest(operation=operation):
                page, context = MagicMock(), MagicMock()
                context.pages = [page]
                context.cookies.return_value = []
                with patch("playwright.sync_api.sync_playwright", return_value=MagicMock()), \
                        patch.object(main, "launch_tistory_persistent_context", return_value=context), \
                        patch.object(main, "tistory_profile_paths", return_value=(self.directory, self.path)), \
                        patch.object(main, "wait_for_tistory_editor", side_effect=RuntimeError("fixture login failed")), \
                        patch.object(main, "append_runtime_log"):
                    with self.assertRaisesRegex(RuntimeError, "fixture login failed"):
                        if operation == "bootstrap":
                            main.run_tistory_profile_bootstrap("https://example.tistory.com/manage/newpost", queue.Queue())
                        else:
                            main.run_tistory_playwright_automation("https://example.tistory.com/manage/newpost", "fixture", queue.Queue())
                self.assertEqual(self.path.read_bytes(), original)
                context.storage_state.assert_not_called()
                context.close.assert_called_once()

    def test_bootstrap_close_does_not_replace_verified_session_with_logout_cookies(self):
        page, context = MagicMock(), MagicMock()
        context.pages = [page]
        context.cookies.return_value = []
        good = state("verified")
        logged_out = {"cookies": [cookie("analytics-only", name="wcs_bt")], "origins": []}
        context.storage_state.side_effect = [good, logged_out]
        with patch("playwright.sync_api.sync_playwright", return_value=MagicMock()), \
                patch.object(main, "launch_tistory_persistent_context", return_value=context), \
                patch.object(main, "tistory_profile_paths", return_value=(self.directory, self.path)), \
                patch.object(main, "wait_for_tistory_editor", return_value=(page, "")), \
                patch.object(main, "append_runtime_log"):
            main.run_tistory_profile_bootstrap("https://example.tistory.com/manage/newpost", queue.Queue())
        snapshot = json.loads(self.path.read_text())
        self.assertEqual(snapshot["cookies"][0]["value"], "verified")
        context.storage_state.assert_called_once()
        context.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
