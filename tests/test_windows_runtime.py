from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import windows_runtime


class WindowsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.driver = self.root / "driver"
        (self.driver / "package").mkdir(parents=True)
        (self.driver / "node.exe").write_bytes(b"fake-node" * 1000)
        (self.driver / "package/cli.js").write_text("test driver", encoding="utf-8")
        (self.driver / "package/LICENSE").write_text("license", encoding="utf-8")
        self.bundle = self.root / "bundle"
        self.cache = self.root / "cache"
        windows_runtime.build_driver_archive(self.driver, self.bundle)

    def test_prepare_preserves_files_and_licenses(self):
        directory = windows_runtime.prepare_driver_cache(self.bundle, self.cache)
        self.assertEqual((directory / "node.exe").read_bytes(), (self.driver / "node.exe").read_bytes())
        self.assertEqual((directory / "package/LICENSE").read_text(), "license")

    def test_valid_cache_never_reads_archive_again(self):
        directory = windows_runtime.prepare_driver_cache(self.bundle, self.cache)
        (self.bundle / windows_runtime.ARCHIVE_NAME).unlink()
        with patch.object(windows_runtime, "_sha256_file", side_effect=AssertionError("Archive read on hot path")):
            self.assertEqual(windows_runtime.prepare_driver_cache(self.bundle, self.cache), directory)

    def test_missing_file_repairs_cache_without_affecting_other_data(self):
        directory = windows_runtime.prepare_driver_cache(self.bundle, self.cache)
        (directory / "package/cli.js").unlink()
        unrelated = self.cache / "user-file.txt"
        unrelated.write_text("preserved")
        self.assertEqual(windows_runtime.prepare_driver_cache(self.bundle, self.cache), directory)
        self.assertTrue((directory / "package/cli.js").exists())
        self.assertEqual(unrelated.read_text(), "preserved")

    def test_changed_driver_uses_new_version_cache(self):
        first = windows_runtime.prepare_driver_cache(self.bundle, self.cache)
        (self.driver / "node.exe").write_bytes(b"new driver")
        windows_runtime.build_driver_archive(self.driver, self.bundle)
        second = windows_runtime.prepare_driver_cache(self.bundle, self.cache)
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists())

    def test_same_driver_build_reuses_cache_despite_installation_timestamps(self):
        first = windows_runtime.prepare_driver_cache(self.bundle, self.cache)
        os.utime(self.driver / "node.exe", (1_000_000_000, 1_000_000_000))
        windows_runtime.build_driver_archive(self.driver, self.bundle)
        self.assertEqual(windows_runtime.prepare_driver_cache(self.bundle, self.cache), first)

    def test_concurrent_workers_extract_once(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            directories = list(executor.map(lambda _: windows_runtime.prepare_driver_cache(self.bundle, self.cache), range(4)))
        self.assertEqual(len(set(directories)), 1)
        self.assertEqual(len(list(self.cache.glob("*/ready.json"))), 1)
        self.assertEqual(list(self.cache.glob("preparing-*")), [])

    def test_bad_archive_fails_before_cache_is_published(self):
        with (self.bundle / windows_runtime.ARCHIVE_NAME).open("ab") as archive:
            archive.write(b"corrupt archive")
        with self.assertRaisesRegex(RuntimeError, "검증"):
            windows_runtime.prepare_driver_cache(self.bundle, self.cache)
        self.assertFalse(self.cache.exists())

    def test_unsafe_archive_path_is_rejected(self):
        archive_path = self.bundle / windows_runtime.ARCHIVE_NAME
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../outside.txt", "unsafe")
        metadata_path = self.bundle / windows_runtime.METADATA_NAME
        metadata = json.loads(metadata_path.read_text())
        metadata["sha256"] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        metadata_path.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(RuntimeError, "안전하지"):
            windows_runtime.prepare_driver_cache(self.bundle, self.cache)
        self.assertFalse((self.root / "outside.txt").exists())

    def test_standard_bundle_is_not_changed(self):
        self.assertFalse(windows_runtime.install_windows_playwright_runtime(self.root / "missing", self.cache))

    def test_transport_is_lazy_and_uses_cached_driver(self):
        from playwright._impl import _transport
        with patch.object(_transport, "compute_driver_executable"):
            self.assertTrue(windows_runtime.install_windows_playwright_runtime(self.bundle, self.cache))
            self.assertFalse(self.cache.exists())
            node, cli = _transport.compute_driver_executable()
            self.assertTrue(Path(node).is_file())
            self.assertTrue(Path(cli).is_file())


if __name__ == "__main__":
    unittest.main()
