from __future__ import annotations

import hashlib
import json
import queue
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app_updater import (
    APP_VERSION,
    ReleaseVersionProbeWorker,
    UpdateCheckWorker,
    UpdateDownloadWorker,
    _windows_restart_environment,
    _write_macos_update_script,
    _write_windows_update_script,
    delta_asset_name,
    is_newer_version,
    platform_asset_name,
    version_key,
)
from tools.build_delta_patch import build_windows_patch

import bsdiff4


class _ReleaseServer:
    def __init__(self, binary: bytes, patch: bytes | None = None) -> None:
        self.binary = binary
        self.patch = patch
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _handler(self):
        binary = self.binary
        patch = self.patch
        port_getter = lambda: self.server.server_address[1]

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):  # noqa: N802
                if self.path == "/latest":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{port_getter()}/releases/tag/v9.9.9")
                    self.end_headers()
                    return
                if self.path == "/releases/tag/v9.9.9":
                    self.send_response(200)
                    self.end_headers()
                    return
                self.send_error(404)

            def do_GET(self):  # noqa: N802
                if self.path == "/latest":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{port_getter()}/releases/tag/v9.9.9")
                    self.end_headers()
                    return
                if self.path == "/releases/tag/v9.9.9":
                    self.send_response(200)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/release":
                    assets = [
                        {
                            "name": platform_asset_name(),
                            "size": len(binary),
                            "digest": f"sha256:{hashlib.sha256(binary).hexdigest()}",
                            "browser_download_url": f"http://127.0.0.1:{port_getter()}/asset",
                        }
                    ]
                    if patch is not None:
                        assets.append(
                            {
                                "name": delta_asset_name(APP_VERSION, "9.9.9"),
                                "size": len(patch),
                                "digest": f"sha256:{hashlib.sha256(patch).hexdigest()}",
                                "browser_download_url": f"http://127.0.0.1:{port_getter()}/patch",
                            }
                        )
                    payload = {
                        "tag_name": "v9.9.9",
                        "name": "테스트 릴리스",
                        "assets": assets,
                    }
                    encoded = json.dumps(payload).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                if self.path == "/asset":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Length", str(len(binary)))
                    self.end_headers()
                    self.wfile.write(binary)
                    return
                if self.path == "/patch" and patch is not None:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(len(patch)))
                    self.end_headers()
                    self.wfile.write(patch)
                    return
                self.send_error(404)

            def log_message(self, format, *args):
                return

        return Handler

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    @property
    def release_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/release"

    @property
    def latest_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/latest"


class AppUpdaterTests(unittest.TestCase):
    def test_semantic_version_comparison(self):
        self.assertEqual(version_key("v1.2.3-beta"), (1, 2, 3))
        self.assertTrue(is_newer_version("1.0.1", "1.0.0"))
        self.assertTrue(is_newer_version("2.0.0", "1.9.9"))
        self.assertFalse(is_newer_version("v1.0.0", "1.0.0"))
        self.assertIn("v1.0.0-to-v1.0.1.patch.zip", delta_asset_name("1.0.0", "1.0.1"))

    def test_check_download_progress_and_digest(self):
        binary = ("Blog Helper 자동 업데이트 테스트\n".encode("utf-8")) * 100000
        result_queue: queue.Queue = queue.Queue()
        with _ReleaseServer(binary) as release_server, tempfile.TemporaryDirectory() as temp_dir:
            checker = UpdateCheckWorker(
                result_queue,
                current_version=APP_VERSION,
                api_url=release_server.release_url,
            )
            checker.run()
            event_type, payload = result_queue.get(timeout=2)
            self.assertEqual(event_type, "update_available")
            self.assertEqual(payload["version"], "9.9.9")
            self.assertEqual(payload["update_kind"], "full")

            downloader = UpdateDownloadWorker(result_queue, payload, Path(temp_dir) / "updates with spaces")
            downloader.run()
            events = []
            while not result_queue.empty():
                events.append(result_queue.get_nowait())

            progress_events = [event for event in events if event[0] == "update_progress"]
            done_events = [event for event in events if event[0] == "update_downloaded"]
            self.assertTrue(progress_events)
            self.assertEqual(len(done_events), 1)
            downloaded = done_events[0][1]
            self.assertEqual(Path(downloaded["local_path"]).read_bytes(), binary)
            self.assertEqual(downloaded["sha256"], hashlib.sha256(binary).hexdigest())
            self.assertEqual(progress_events[-1][1]["downloaded"], len(binary))

    def test_lightweight_release_probe_detects_new_version(self):
        result_queue: queue.Queue = queue.Queue()
        with _ReleaseServer(b"unused") as release_server:
            probe = ReleaseVersionProbeWorker(
                result_queue,
                current_version=APP_VERSION,
                latest_url=release_server.latest_url,
            )
            probe.run()
            event_type, payload = result_queue.get(timeout=2)
            self.assertEqual(event_type, "update_probe_available")
            self.assertEqual(payload, "9.9.9")

            current_probe = ReleaseVersionProbeWorker(
                result_queue,
                current_version="9.9.9",
                latest_url=release_server.latest_url,
            )
            current_probe.run()
            event_type, payload = result_queue.get(timeout=2)
            self.assertEqual(event_type, "update_probe_none")
            self.assertEqual(payload, "9.9.9")

    def test_install_scripts_preserve_quoted_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "folder with spaces"
            base.mkdir()
            windows_script = base / "apply update.ps1"
            mac_script = base / "apply update.zsh"
            _write_windows_update_script(windows_script)
            _write_macos_update_script(mac_script)
            windows_contents = windows_script.read_text(encoding="utf-8-sig")
            self.assertIn("-LiteralPath $Source", windows_contents)
            self.assertIn("Wait-ForParentExit", windows_contents)
            self.assertIn("Move-WithRetry", windows_contents)
            self.assertIn("function Get-SHA256", windows_contents)
            self.assertIn("$StartInfo.WorkingDirectory = $TargetDirectory", windows_contents)
            self.assertIn("$StartInfo.UseShellExecute = $true", windows_contents)
            self.assertIn("ProcessWindowStyle]::Normal", windows_contents)
            self.assertIn("Reset-PyInstallerRestartEnvironment", windows_contents)
            self.assertIn('PYINSTALLER_RESET_ENVIRONMENT = "1"', windows_contents)
            self.assertIn('$_' + '.Name -like "_PYI_*"', windows_contents)
            self.assertIn("BlogHelperWindowControl", windows_contents)
            self.assertIn("ShowWindowAsync", windows_contents)
            self.assertIn("MainWindowHandle", windows_contents)
            self.assertIn("새 프로그램 재실행 성공", windows_contents)
            self.assertIn('"$TARGET_APP"', mac_script.read_text(encoding="utf-8"))
            self.assertTrue(mac_script.stat().st_mode & 0o100)

    def test_windows_restart_environment_forces_fresh_onefile_unpack(self):
        environment = _windows_restart_environment(
            {
                "PATH": "C:\\Windows",
                "_PYI_APPLICATION_HOME_DIR": r"C:\\Temp\\_MEI12345",
                "_PYI_ARCHIVE_FILE": r"C:\\BlogHelper.exe",
                "_MEIPASS2": r"C:\\Temp\\_MEI12345",
            }
        )
        self.assertEqual(environment["PATH"], "C:\\Windows")
        self.assertEqual(environment["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", environment)
        self.assertNotIn("_PYI_ARCHIVE_FILE", environment)
        self.assertNotIn("_MEIPASS2", environment)

    def test_check_prefers_matching_delta_asset(self):
        binary = b"full update" * 1000
        patch = b"small patch"
        result_queue: queue.Queue = queue.Queue()
        with _ReleaseServer(binary, patch=patch) as release_server:
            checker = UpdateCheckWorker(
                result_queue,
                current_version=APP_VERSION,
                api_url=release_server.release_url,
            )
            checker.run()
            event_type, payload = result_queue.get(timeout=2)
            self.assertEqual(event_type, "update_available")
            self.assertEqual(payload["update_kind"], "delta")
            self.assertEqual(payload["size"], len(patch))
            self.assertEqual(payload["full_asset"]["update_kind"], "full")

    def test_windows_delta_patch_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            previous = root / "previous.exe"
            current = root / "current.exe"
            output = root / "update.patch.zip"
            previous.write_bytes((b"stable-runtime-" * 10000) + b"version=1.0.1")
            current.write_bytes((b"stable-runtime-" * 10000) + b"version=1.0.2 and a small fix")

            args = type(
                "Args",
                (),
                {
                    "previous": str(previous),
                    "current": str(current),
                    "from_version": "1.0.1",
                    "to_version": "1.0.2",
                    "output": str(output),
                },
            )()
            build_windows_patch(args)

            with zipfile.ZipFile(output) as archive:
                manifest = json.loads(archive.read("patch-manifest.json"))
                patch_bytes = archive.read(manifest["binary_patch"]["patch_file"])
            rebuilt = bsdiff4.patch(previous.read_bytes(), patch_bytes)
            self.assertEqual(rebuilt, current.read_bytes())
            self.assertLess(output.stat().st_size, current.stat().st_size)


if __name__ == "__main__":
    unittest.main()
