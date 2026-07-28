import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import main

ROOT = Path(__file__).resolve().parents[1]


class RemoteQueueUITests(unittest.TestCase):
    def test_remote_page_exposes_queue_controls(self) -> None:
        html = (ROOT / "remote_gateway" / "public" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "remote_gateway" / "public" / "app.js").read_text(encoding="utf-8")

        self.assertIn("자동화 대기열", html)
        self.assertIn('id="previewDialog"', html)
        self.assertIn("등록 예정시간", script)
        self.assertIn("대기 없이 즉시발행", script)
        self.assertIn('data-action="preview"', script)
        self.assertIn('id="daumTrendButton"', html)
        self.assertIn('id="clearJobsButton"', html)
        self.assertIn("/api/trends/daum", script)
        self.assertIn('method: "DELETE"', script)
        self.assertIn("publishedUrl", script)

    def test_worker_has_pc_queue_endpoints(self) -> None:
        worker = (ROOT / "remote_gateway" / "src" / "worker.js").read_text(encoding="utf-8")

        self.assertIn('url.pathname === "/api/queue"', worker)
        self.assertIn("queue.schedule.update", worker)
        self.assertIn("queue.publish.now", worker)
        self.assertIn("queue.snapshot", worker)
        self.assertIn("cleanPreviewHtml", worker)
        self.assertIn('url.pathname === "/api/trends/daum"', worker)
        self.assertIn("hideDevice", worker)
        self.assertIn("clearJobHistory", worker)
        self.assertIn('payload.type === "queue.published"', worker)

    def test_schedule_command_updates_exact_queue_item(self) -> None:
        first = {"id": "first", "status": "대기 중", "scheduled_at": 100}
        second = {"id": "second", "status": "대기 중", "scheduled_at": 200}
        agent = MagicMock()
        app = SimpleNamespace(
            remote_agent=agent,
            automation_queue=[first, second],
            _find_automation_queue_item=lambda item_id: next(
                (item for item in (first, second) if item["id"] == item_id),
                None,
            ),
            _save_automation_queue=MagicMock(),
            _refresh_automation_queue=MagicMock(),
            _send_remote_queue_snapshot=MagicMock(),
        )

        main.KeywordApp._handle_remote_queue_command(
            app,
            {
                "type": "queue.schedule.update",
                "commandId": "command-1",
                "itemId": "second",
                "scheduledAt": 1_900_000_000,
            },
        )

        self.assertEqual(first["scheduled_at"], 100)
        self.assertEqual(second["scheduled_at"], 1_900_000_000)
        app._save_automation_queue.assert_called_once()
        agent.command_result.assert_called_once_with(
            "command-1",
            True,
            "등록 예정시간을 변경했습니다.",
        )

    def test_publish_command_targets_requested_queue_item(self) -> None:
        item = {"id": "queue-2", "status": "대기 중"}
        agent = MagicMock()
        publish = MagicMock(return_value=True)
        app = SimpleNamespace(
            remote_agent=agent,
            _find_automation_queue_item=lambda item_id: item if item_id == "queue-2" else None,
            _publish_next_automation_queue_item=publish,
            _send_remote_queue_snapshot=MagicMock(),
        )

        main.KeywordApp._handle_remote_queue_command(
            app,
            {
                "type": "queue.publish.now",
                "commandId": "command-2",
                "itemId": "queue-2",
            },
        )

        publish.assert_called_once_with(scheduled=False, item_id="queue-2")
        agent.command_result.assert_called_once_with(
            "command-2",
            True,
            "즉시발행을 시작했습니다.",
        )


if __name__ == "__main__":
    unittest.main()
