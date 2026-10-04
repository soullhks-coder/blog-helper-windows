import json
import queue
import unittest
from unittest.mock import patch

from remote_control import RemoteAgentConfig
from shared_publish_limit import (
    SHARED_TISTORY_DAILY_LIMIT,
    SharedTistoryPublishLimitClient,
    is_shared_tistory_blog,
    tistory_shared_blog_key,
)
import main


class SharedTistoryLimitTests(unittest.TestCase):
    def test_only_requested_domain_is_shared(self) -> None:
        self.assertTrue(is_shared_tistory_blog("https://tip.lhksoul.com/manage/newpost"))
        self.assertTrue(is_shared_tistory_blog("TIP.LHKSOUL.COM/"))
        self.assertFalse(is_shared_tistory_blog("https://other.lhksoul.com"))
        self.assertFalse(is_shared_tistory_blog("https://tip.lhksoul.com.evil.example"))
        self.assertEqual(
            tistory_shared_blog_key("https://tip.lhksoul.com/"),
            tistory_shared_blog_key("tip.lhksoul.com/manage/newpost"),
        )
        self.assertEqual(main.effective_tistory_daily_limit("tip.lhksoul.com", 0), 15)
        self.assertEqual(main.effective_tistory_daily_limit("other.lhksoul.com", 7), 7)
        self.assertEqual(main.effective_tistory_daily_limit("other.lhksoul.com", 0), 0)

    def test_missing_pairing_fails_closed_for_shared_blog(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "원격 서버 등록이 필요"):
            SharedTistoryPublishLimitClient(
                RemoteAgentConfig(device_id="mom-pc", agent_token=""),
                "tip.lhksoul.com",
                SHARED_TISTORY_DAILY_LIMIT,
                0,
            )

    def test_reserve_sends_device_baseline_and_commit(self) -> None:
        config = RemoteAgentConfig(
            gateway_url="https://ai.lhksoul.com",
            device_id="mom-pc",
            agent_token="signed-device-token",
        )
        client = SharedTistoryPublishLimitClient(config, "tip.lhksoul.com", 15, 4)
        requests = []

        class FakeResponse:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return json.dumps(self.data).encode("utf-8")

        def fake_urlopen(request, **_kwargs):
            requests.append(request)
            return FakeResponse({
                "ok": True,
                "reservationId": "a" * 36,
                "date": "2026-10-04",
                "published": 4,
                "remaining": 10,
            })

        with patch("shared_publish_limit.urlopen", side_effect=fake_urlopen):
            reserved = client.reserve()
            client.commit(reserved["reservationId"], reserved["date"])

        self.assertEqual(len(requests), 2)
        self.assertIn("deviceId=mom-pc", requests[0].full_url)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer signed-device-token")
        self.assertEqual(json.loads(requests[0].data)["localCount"], 4)
        self.assertEqual(json.loads(requests[1].data)["action"], "commit")
        self.assertEqual(json.loads(requests[1].data)["reservationDate"], "2026-10-04")

    def test_tistory_worker_uses_shared_reservation_only_for_tip(self) -> None:
        events = queue.Queue()
        worker = main.TistoryAutomationWorker(
            title="테스트",
            article_html="<p>본문</p>",
            result_queue=events,
            write_url="https://tip.lhksoul.com/manage/newpost",
            public_blog_url="https://tip.lhksoul.com",
            publish_after_input=True,
            save_mode=main.TISTORY_SAVE_MODE_PUBLISH,
            ads_enabled=False,
            daily_publish_limit=0,
        )
        with (
            patch.object(main, "GOOGLE_IMAGE_COLLAGE_ENABLED", False),
            patch.object(main, "prepare_tistory_native_attachment_html", return_value=("<p>본문</p>", {})),
            patch.object(main, "build_tistory_editor_automation_script", return_value="script"),
            patch.object(main, "run_tistory_playwright_automation", return_value=(True, {
                "published_url": "https://tip.lhksoul.com/1",
                "save_mode": main.TISTORY_SAVE_MODE_PUBLISH,
            })),
            patch.object(main, "cleanup_tistory_automation_files"),
            patch.object(main, "cleanup_generated_upload_images"),
            patch.object(main.DailyPublishLimitStore, "count", return_value=3),
            patch.object(main.DailyPublishLimitStore, "record_success", return_value=4) as record_local,
            patch.object(main.DailyPublishLimitStore, "reserve_publish") as reserve_local,
            patch.object(main, "SharedTistoryPublishLimitClient") as shared_class,
        ):
            shared_class.return_value.reserve.return_value = {
                "reservationId": "a" * 36,
                "date": "2026-10-04",
                "published": 12,
                "remaining": 2,
            }
            shared_class.return_value.commit.return_value = {
                "published": 13,
                "remaining": 2,
                "pending": 0,
            }
            worker.run()
            shared_class.return_value.reserve.assert_called_once()
            shared_class.return_value.commit.assert_called_once_with("a" * 36, "2026-10-04")
            reserve_local.assert_not_called()
            record_local.assert_called_once()
        results = [payload for kind, payload in list(events.queue) if kind == "tistory_automation_done"]
        self.assertEqual(results[0]["shared_daily_publish_count"], 13)


if __name__ == "__main__":
    unittest.main()
