import queue
import unittest
from unittest.mock import patch

import main


class WebmasterSubmissionWorkerTests(unittest.TestCase):
    def test_daum_runs_even_when_naver_raises(self) -> None:
        result_queue: queue.Queue = queue.Queue()
        worker = main.NaverSearchAdvisorWorker(
            "https://blog.soullhk.kr/example-post",
            result_queue,
            show_feedback=False,
        )

        with (
            patch(
                "main.run_naver_search_advisor_playwright",
                side_effect=RuntimeError("네이버 내역 확인 지연"),
            ),
            patch(
                "main.run_daum_webmaster_playwright",
                return_value=(True, "다음 수집 요청 완료"),
            ) as daum_submit,
            patch(
                "main.run_google_search_console_playwright",
                return_value=(True, "Google 색인 요청 완료"),
            ) as google_submit,
        ):
            worker.run()

        daum_submit.assert_called_once_with(worker.published_url, result_queue)
        google_submit.assert_called_once_with(worker.published_url, result_queue)
        event_type, payload = result_queue.get_nowait()
        self.assertEqual(event_type, "naver_search_advisor_done")
        self.assertTrue(payload["partial"])
        self.assertIn("다음 수집 요청 완료", payload["message"])
        self.assertIn("네이버 내역 확인 지연", payload["message"])

    def test_all_search_engines_report_complete(self) -> None:
        result_queue: queue.Queue = queue.Queue()
        worker = main.NaverSearchAdvisorWorker(
            "https://blog.soullhk.kr/example-post",
            result_queue,
            show_feedback=True,
        )

        with (
            patch(
                "main.run_naver_search_advisor_playwright",
                return_value=(True, "네이버 수집 요청 완료"),
            ),
            patch(
                "main.run_daum_webmaster_playwright",
                return_value=(True, "다음 수집 요청 완료"),
            ),
            patch(
                "main.run_google_search_console_playwright",
                return_value=(True, "Google 색인 요청 완료"),
            ),
        ):
            worker.run()

        event_type, payload = result_queue.get_nowait()
        self.assertEqual(event_type, "naver_search_advisor_done")
        self.assertFalse(payload["partial"])
        self.assertIn("네이버 수집 요청 완료", payload["message"])
        self.assertIn("다음 수집 요청 완료", payload["message"])
        self.assertIn("Google 색인 요청 완료", payload["message"])

    def test_google_runs_even_when_naver_and_daum_raise(self) -> None:
        result_queue: queue.Queue = queue.Queue()
        worker = main.NaverSearchAdvisorWorker(
            "https://blog.soullhk.kr/example-post",
            result_queue,
            show_feedback=False,
        )

        with (
            patch(
                "main.run_naver_search_advisor_playwright",
                side_effect=RuntimeError("네이버 실패"),
            ),
            patch(
                "main.run_daum_webmaster_playwright",
                side_effect=RuntimeError("다음 실패"),
            ),
            patch(
                "main.run_google_search_console_playwright",
                return_value=(True, "Google 색인 요청 완료"),
            ) as google_submit,
        ):
            worker.run()

        google_submit.assert_called_once_with(worker.published_url, result_queue)
        event_type, payload = result_queue.get_nowait()
        self.assertEqual(event_type, "naver_search_advisor_done")
        self.assertTrue(payload["partial"])
        self.assertIn("Google 색인 요청 완료", payload["message"])


if __name__ == "__main__":
    unittest.main()
