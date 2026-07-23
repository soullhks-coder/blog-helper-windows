import json
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

import main


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body


class AITimeoutRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = Request("https://example.test/generate")
        self.context = ssl.create_default_context()

    def test_read_timeout_retries_and_returns_second_response(self) -> None:
        retry_messages = []
        with (
            patch("main.urlopen", side_effect=[
                socket.timeout("The read operation timed out"),
                FakeResponse({"ok": True}),
            ]) as mocked_urlopen,
            patch("main.time.sleep"),
        ):
            payload = main.request_json_with_timeout_retry(
                self.request,
                self.context,
                operation_name="테스트 글 생성",
                timeout_seconds=45,
                max_attempts=2,
                on_retry=retry_messages.append,
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(mocked_urlopen.call_count, 2)
        self.assertEqual(len(retry_messages), 1)
        self.assertIn("자동으로 다시 시도", retry_messages[0])

    def test_repeated_timeout_returns_korean_error_and_writes_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_log = Path(temp_dir) / "runtime.log"
            with (
                patch("main.RUNTIME_LOG_FILE", runtime_log),
                patch(
                    "main.urlopen",
                    side_effect=socket.timeout("The read operation timed out"),
                ),
                patch("main.time.sleep"),
            ):
                with self.assertRaisesRegex(RuntimeError, "응답이 지연되어 2회 시도"):
                    main.request_json_with_timeout_retry(
                        self.request,
                        self.context,
                        operation_name="테스트 글 생성",
                        timeout_seconds=45,
                        max_attempts=2,
                    )

            log_text = runtime_log.read_text(encoding="utf-8")
            self.assertIn("읽기 시간 초과 (1/2", log_text)
            self.assertIn("읽기 시간 초과 (2/2", log_text)


if __name__ == "__main__":
    unittest.main()
