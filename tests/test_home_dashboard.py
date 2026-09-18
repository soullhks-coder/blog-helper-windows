import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


def _insight(keyword: str, source: str) -> main.KeywordInsight:
    return main.KeywordInsight(
        keyword=keyword,
        score=90,
        reasons=["테스트"],
        sources=[source],
        categories=["테스트"],
        source_urls={source: "https://example.com"},
    )


class HomeDashboardTests(unittest.TestCase):
    def test_adsense_dashboard_parser_extracts_requested_summary_values(self) -> None:
        dashboard_text = """
        예상 수입
        오늘 현재까지
        US$1.15
        어제
        US$0.91
        지난 7일
        US$8.20
        이번 달
        US$21.10
        잔고
        $136.18
        최종 지급일
        $118.29
        """

        self.assertEqual(
            main.parse_adsense_dashboard_text(dashboard_text),
            {
                "today": "US$1.15",
                "yesterday": "US$0.91",
                "last_7_days": "US$8.20",
                "month_to_date": "US$21.10",
                "balance": "$136.18",
            },
        )

    def test_adsense_connection_and_cached_summary_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                adsense_connected=True,
                adsense_account_label="pub-1234567890",
                adsense_dashboard_url="https://www.google.com/adsense/new/u/0/pub-1234567890/home",
                adsense_summary={
                    "today": "US$1.15",
                    "balance": "$136.18",
                    "updated_at": "2026-09-17 21:00",
                },
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(
                    main.PromptFileStore,
                    "load_into",
                    side_effect=lambda value: value,
                ),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

        self.assertTrue(loaded.adsense_connected)
        self.assertEqual(loaded.adsense_account_label, "pub-1234567890")
        self.assertEqual(loaded.adsense_summary["today"], "US$1.15")
        self.assertEqual(loaded.adsense_summary["balance"], "$136.18")

    def test_home_publish_counts_use_the_active_platform_accounts(self) -> None:
        class LabelStub:
            def __init__(self) -> None:
                self.text = ""

            def configure(self, **kwargs) -> None:
                self.text = str(kwargs.get("text") or "")

        count_labels = {
            "wordpress": LabelStub(),
            "tistory": LabelStub(),
            "blogspot": LabelStub(),
        }
        remaining_labels = {
            "wordpress": LabelStub(),
            "tistory": LabelStub(),
            "blogspot": LabelStub(),
        }
        app = SimpleNamespace(
            home_publish_count_labels=count_labels,
            home_publish_remaining_labels=remaining_labels,
        )
        app._daily_publish_account = lambda platform: f"{platform}-active-profile"
        app._home_daily_publish_limit = lambda platform: {
            "wordpress": 0,
            "tistory": 5,
            "blogspot": 1,
        }[platform]

        with patch.object(
            main.DailyPublishLimitStore,
            "count",
            side_effect=lambda platform, _account: {
                "wordpress": 3,
                "tistory": 2,
                "blogspot": 1,
            }[platform],
        ) as count_mock:
            main.KeywordApp._refresh_home_publish_counts(app)

        self.assertEqual(count_labels["wordpress"].text, "오늘 발행 3건")
        self.assertEqual(count_labels["tistory"].text, "오늘 발행 2건")
        self.assertEqual(count_labels["blogspot"].text, "오늘 발행 1건")
        self.assertEqual(
            remaining_labels["wordpress"].text,
            "남은 발행 가능건수 제한없음",
        )
        self.assertEqual(
            remaining_labels["tistory"].text,
            "남은 발행 가능건수 3건",
        )
        self.assertEqual(
            remaining_labels["blogspot"].text,
            "남은 발행 가능건수 0건",
        )
        self.assertEqual(
            [call.args for call in count_mock.call_args_list],
            [
                ("wordpress", "wordpress-active-profile"),
                ("tistory", "tistory-active-profile"),
                ("blogspot", "blogspot-active-profile"),
            ],
        )

    def test_home_preferences_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                home_target_platform="blogspot",
                home_selected_prompt_id="blogspot-custom",
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(
                    main.PromptFileStore,
                    "load_into",
                    side_effect=lambda value: value,
                ),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

        self.assertEqual(loaded.home_target_platform, "blogspot")
        self.assertEqual(loaded.home_selected_prompt_id, "blogspot-custom")

    def test_home_worker_reports_each_keyword_source(self) -> None:
        result_queue = queue.Queue()
        worker = main.HomeDashboardKeywordWorker(result_queue)
        payloads = {
            "daum": ([_insight("다음 키워드", "Daum")], {"다음 키워드": "참고"}),
            "signal": ([_insight("시그널 키워드", "Signal")], {"시그널 키워드": "참고"}),
            "google": ([_insight("구글 키워드", "Google Trends")], {"구글 키워드": "참고"}),
        }

        with patch.object(worker, "_fetch_source", side_effect=lambda source: payloads[source]):
            worker.run()

        events = []
        while not result_queue.empty():
            events.append(result_queue.get_nowait())
        completed_sources = [
            payload["source"]
            for event, payload in events
            if event == "home_keywords_source_done"
        ]
        self.assertEqual(completed_sources, ["daum", "signal", "google"])
        self.assertEqual(events[-1][0], "home_keywords_done")
        self.assertEqual(events[-1][1]["completed"], 3)

    def test_reference_completion_hands_home_flow_to_writing_step_three(self) -> None:
        calls = []
        app = SimpleNamespace(
            home_reference_launch_context={"keyword": "테스트 키워드"},
        )
        app._set_home_launch_busy = lambda busy, message: calls.append(
            ("busy", busy, message)
        )
        app._switch_page = lambda page: calls.append(("page", page))
        app._open_writing_section = lambda section, complete_previous=False: calls.append(
            ("section", section, complete_previous)
        )

        handled = main.KeywordApp._complete_home_reference_handoff(
            app,
            "테스트 키워드",
            "수집 완료",
        )

        self.assertTrue(handled)
        self.assertIsNone(app.home_reference_launch_context)
        self.assertIn(("page", "writing"), calls)
        self.assertIn(("section", "article", True), calls)


if __name__ == "__main__":
    unittest.main()
