import queue
import unittest
from types import SimpleNamespace
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

    def test_only_selected_webmaster_tool_runs(self) -> None:
        result_queue: queue.Queue = queue.Queue()
        worker = main.NaverSearchAdvisorWorker(
            "https://blog.soullhk.kr/example-post",
            result_queue,
            show_feedback=False,
            enabled_tools=[main.WEBMASTER_TOOL_DAUM],
        )

        with (
            patch("main.run_naver_search_advisor_playwright") as naver_submit,
            patch(
                "main.run_daum_webmaster_playwright",
                return_value=(True, "다음 수집 요청 완료"),
            ) as daum_submit,
            patch("main.run_google_search_console_playwright") as google_submit,
        ):
            worker.run()

        naver_submit.assert_not_called()
        daum_submit.assert_called_once_with(worker.published_url, result_queue)
        google_submit.assert_not_called()
        event_type, payload = result_queue.get_nowait()
        self.assertEqual(event_type, "naver_search_advisor_done")
        self.assertFalse(payload["partial"])
        self.assertEqual(payload["selected_labels"], "다음")

    def test_existing_settings_default_to_all_webmaster_tools(self) -> None:
        settings = main.WordPressSettings()
        self.assertTrue(settings.webmaster_submit_naver)
        self.assertTrue(settings.webmaster_submit_daum)
        self.assertTrue(settings.webmaster_submit_google)
        self.assertFalse(settings.webmaster_completion_message)

    def test_completion_alert_is_hidden_when_setting_is_off(self) -> None:
        app = SimpleNamespace(
            wordpress_settings=main.WordPressSettings(
                webmaster_completion_message=False
            ),
            naver_search_advisor_worker=object(),
            _update_quick_status=lambda *_args: None,
            _start_next_naver_search_advisor_submission=lambda: None,
            after=lambda _delay, _callback: None,
        )

        with (
            patch.object(main.messagebox, "showinfo") as showinfo,
            patch.object(main.messagebox, "showwarning") as showwarning,
        ):
            main.KeywordApp._handle_naver_search_advisor_done(
                app,
                {
                    "message": "검색엔진 등록 완료",
                    "show_feedback": True,
                    "partial": False,
                },
            )
            main.KeywordApp._handle_naver_search_advisor_done(
                app,
                {
                    "message": "검색엔진 일부 등록 완료",
                    "show_feedback": True,
                    "partial": True,
                },
            )

        showinfo.assert_not_called()
        showwarning.assert_not_called()

    def test_completion_alert_is_shown_when_setting_is_on(self) -> None:
        app = SimpleNamespace(
            wordpress_settings=main.WordPressSettings(
                webmaster_completion_message=True
            ),
            naver_search_advisor_worker=object(),
            _update_quick_status=lambda *_args: None,
            _start_next_naver_search_advisor_submission=lambda: None,
            after=lambda _delay, _callback: None,
        )

        with patch.object(main.messagebox, "showinfo") as showinfo:
            main.KeywordApp._handle_naver_search_advisor_done(
                app,
                {
                    "message": "검색엔진 등록 완료",
                    "show_feedback": True,
                    "partial": False,
                },
            )

        showinfo.assert_called_once_with(
            "검색엔진 수집 요청 완료",
            "검색엔진 등록 완료",
        )


if __name__ == "__main__":
    unittest.main()
