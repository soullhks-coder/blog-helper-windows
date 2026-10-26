"""Credential-store behavior with fake backends; never access OS credentials."""
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import main


class KeychainStoreTests(unittest.TestCase):
    def setUp(self):
        self.platform = patch.object(main, "os", SimpleNamespace(name="posix"))
        self.platform.start()
        self.addCleanup(self.platform.stop)
        run_patch = patch.object(main.subprocess, "run")
        self.run = run_patch.start()
        self.addCleanup(run_patch.stop)

    def test_mac_updates_existing_item_once_without_deleting_it(self):
        vault = {"fixture-account": "fixture-old"}

        def update(command, **kwargs):
            self.assertEqual(command[:2], ["security", "add-generic-password"])
            self.assertIn("-U", command)
            self.assertGreater(kwargs["timeout"], 0)
            vault[command[command.index("-a") + 1]] = command[command.index("-w") + 1]
            return SimpleNamespace(returncode=0)

        self.run.side_effect = update
        self.assertTrue(main.KeychainStore.save_secret("fixture-account", "fixture-new"))
        self.assertEqual(vault, {"fixture-account": "fixture-new"})
        self.run.assert_called_once()

    def test_failed_update_preserves_the_existing_item(self):
        for failure in ("return-code", "timeout", "missing-command"):
            with self.subTest(failure=failure):
                vault = {"fixture-account": "fixture-old"}

                def failing(command, **_kwargs):
                    if command[1] == "delete-generic-password":
                        vault.pop("fixture-account", None)
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(["security"], 5)
                    if failure == "missing-command":
                        raise FileNotFoundError("fixture missing executable")
                    return SimpleNamespace(returncode=1)

                self.run.reset_mock()
                self.run.side_effect = failing
                self.assertFalse(main.KeychainStore.save_secret("fixture-account", "fixture-new"))
                self.assertEqual(vault, {"fixture-account": "fixture-old"})
                self.run.assert_called_once()

    def test_mac_load_has_a_deadline_and_normalizes_the_returned_value(self):
        self.run.return_value = SimpleNamespace(stdout=" fixture-value\n")
        self.assertEqual(main.KeychainStore.load_secret("fixture-account"), "fixture-value")
        self.assertEqual(self.run.call_args.args[0][:2], ["security", "find-generic-password"])
        self.assertGreater(self.run.call_args.kwargs["timeout"], 0)
        self.assertTrue(self.run.call_args.kwargs["check"])

    def test_mac_load_failure_returns_empty_so_existing_fallback_can_be_used(self):
        for error in (
            subprocess.TimeoutExpired(["security"], 5),
            subprocess.CalledProcessError(1, ["security"]),
            FileNotFoundError("fixture missing executable"),
        ):
            with self.subTest(error=type(error).__name__):
                self.run.reset_mock()
                self.run.side_effect = error
                self.assertEqual(main.KeychainStore.load_secret("fixture-account"), "")
                self.run.assert_called_once()

    def test_only_explicit_delete_uses_the_delete_command_and_is_bounded(self):
        main.KeychainStore.delete_secret("fixture-account")
        self.run.assert_called_once()
        self.assertEqual(self.run.call_args.args[0][:2], ["security", "delete-generic-password"])
        self.assertGreater(self.run.call_args.kwargs["timeout"], 0)
        self.run.side_effect = subprocess.TimeoutExpired(["security"], 5)
        main.KeychainStore.delete_secret("fixture-account")

    def test_empty_input_never_touches_a_credential_backend(self):
        self.assertEqual(main.KeychainStore.load_secret(""), "")
        self.assertFalse(main.KeychainStore.save_secret("", "fixture-value"))
        self.assertFalse(main.KeychainStore.save_secret("fixture-account", ""))
        main.KeychainStore.delete_secret("")
        self.run.assert_not_called()

    def test_windows_keyring_behavior_remains_available_without_security_processes(self):
        keyring = SimpleNamespace(get_password=Mock(return_value="fixture-value"),
                                  set_password=Mock(), delete_password=Mock())
        with patch.object(main, "os", SimpleNamespace(name="nt")), patch.dict(sys.modules, keyring=keyring):
            self.assertEqual(main.KeychainStore.load_secret("fixture-account"), "fixture-value")
            self.assertTrue(main.KeychainStore.save_secret("fixture-account", "fixture-new"))
            main.KeychainStore.delete_secret("fixture-account")
        keyring.get_password.assert_called_once_with(main.KEYCHAIN_SERVICE, "fixture-account")
        keyring.set_password.assert_called_once_with(main.KEYCHAIN_SERVICE, "fixture-account", "fixture-new")
        keyring.delete_password.assert_called_once_with(main.KEYCHAIN_SERVICE, "fixture-account")
        self.run.assert_not_called()

    def test_windows_keyring_failures_are_nonfatal(self):
        keyring = SimpleNamespace(get_password=Mock(side_effect=RuntimeError("fixture locked")),
                                  set_password=Mock(side_effect=RuntimeError("fixture locked")),
                                  delete_password=Mock(side_effect=RuntimeError("fixture locked")))
        with patch.object(main, "os", SimpleNamespace(name="nt")), patch.dict(sys.modules, keyring=keyring):
            self.assertEqual(main.KeychainStore.load_secret("fixture-account"), "")
            self.assertFalse(main.KeychainStore.save_secret("fixture-account", "fixture-new"))
            main.KeychainStore.delete_secret("fixture-account")
        self.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
