from __future__ import annotations

import hashlib
import json
import queue
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app_updater import (
    APP_VERSION,
    UpdateCheckWorker,
    UpdateDownloadWorker,
    _write_macos_update_script,
    _write_windows_update_script,
    is_newer_version,
    platform_asset_name,
    version_key,
)


class _ReleaseServer:
    def __init__(self, binary: bytes) -> None:
        self.binary = binary
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def _handler(self):
        binary = self.binary
        port_getter = lambda: self.server.server_address[1]

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path == "/release":
                    payload = {
                        "tag_name": "v9.9.9",
                        "name": "테스트 릴리스",
                        "assets": [
                            {
                                "name": platform_asset_name(),
                                "size": len(binary),
                                "digest": f"sha256:{hashlib.sha256(binary).hexdigest()}",
                                "browser_download_url": f"http://127.0.0.1:{port_getter()}/asset",
                            }
                        ],
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


class AppUpdaterTests(unittest.TestCase):
    def test_semantic_version_comparison(self):
        self.assertEqual(version_key("v1.2.3-beta"), (1, 2, 3))
        self.assertTrue(is_newer_version("1.0.1", "1.0.0"))
        self.assertTrue(is_newer_version("2.0.0", "1.9.9"))
        self.assertFalse(is_newer_version("v1.0.0", "1.0.0"))

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

    def test_install_scripts_preserve_quoted_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir) / "folder with spaces"
            base.mkdir()
            windows_script = base / "apply update.ps1"
            mac_script = base / "apply update.zsh"
            _write_windows_update_script(windows_script)
            _write_macos_update_script(mac_script)
            self.assertIn("-LiteralPath $Source", windows_script.read_text(encoding="utf-8-sig"))
            self.assertIn('"$TARGET_APP"', mac_script.read_text(encoding="utf-8"))
            self.assertTrue(mac_script.stat().st_mode & 0o100)


if __name__ == "__main__":
    unittest.main()
