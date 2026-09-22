import json
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
            "loword": ([_insight("로워드 키워드", "Google 검색")], {"로워드 키워드": "참고"}),
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
        self.assertEqual(completed_sources, ["daum", "signal", "loword"])
        self.assertEqual(events[-1][0], "home_keywords_done")
        self.assertEqual(events[-1][1]["completed"], 3)

    def test_loword_naver_ranking_starts_playwright_collection_from_google_search(self) -> None:
        worker = main.HomeDashboardKeywordWorker(queue.Queue())
        rows = [{"rank": str(index), "keyword": f"검색어 {index}"} for index in range(1, 12)]
        rows.insert(2, {"rank": "3", "keyword": "검색어 1"})
        payload = {
            "rsltCd": "00",
            "data": {
                "regDtm": "2026-09-22 15:00",
                "keywordTrend": {"naver": rows, "google": [{"keyword": "구글 전용"}]},
            },
        }

        insights, references = worker._build_loword_payload(payload)

        self.assertEqual(len(insights), 10)
        self.assertEqual(insights[0].keyword, "검색어 1")
        self.assertNotIn("구글 전용", [insight.keyword for insight in insights])
        self.assertIn("2026-09-22 15:00", references["검색어 1"])
        google_url = "https://www.google.com/search?q=%EA%B2%80%EC%83%89%EC%96%B4+1"
        naver_url = "https://search.naver.com/search.naver?query=%EA%B2%80%EC%83%89%EC%96%B4+1"
        self.assertEqual(main.KeywordApp._primary_source_url(None, insights[0]), google_url)
        self.assertEqual(insights[0].source_urls["네이버 검색"], naver_url)
        app = SimpleNamespace(current_insights=insights)
        app._primary_source_url = lambda insight: main.KeywordApp._primary_source_url(None, insight)
        selected_source_url = main.KeywordApp._source_url_for_keyword(app, "검색어 1")
        self.assertEqual(selected_source_url, google_url)
        self.assertEqual(
            main.ReferenceCollectionWorker(insights[0].keyword, queue.Queue(), selected_source_url).source_url,
            google_url,
        )

    def test_loword_ranking_uses_http_json_without_playwright(self) -> None:
        payload = {
            "rsltCd": "00",
            "data": {"keywordTrend": {"naver": [{"rank": "1", "keyword": "실시간 키워드"}]}},
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(payload).encode("utf-8")

        worker = main.HomeDashboardKeywordWorker(queue.Queue())
        with patch.object(main, "urlopen", return_value=FakeResponse()) as opened:
            insights, _ = worker._fetch_loword_keywords()

        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, worker.LOWORD_TRENDS_API_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertRegex(json.loads(request.data)["date"], r"^\d{4}-\d{2}-\d{2} \d{2}:00$")
        self.assertEqual(insights[0].keyword, "실시간 키워드")

    def test_loword_invalid_response_is_reported_instead_of_showing_stale_keywords(self) -> None:
        worker = main.HomeDashboardKeywordWorker(queue.Queue())
        with self.assertRaises(RuntimeError):
            worker._build_loword_payload({"rsltCd": "ERROR", "data": {}})

    def test_google_navigation_failure_still_searches_naver_with_playwright(self) -> None:
        visited = []

        class FakePage:
            url = "about:blank"

            def __init__(self, fail: bool = False) -> None:
                self.fail = fail

            def set_default_timeout(self, _timeout):
                pass

            def set_default_navigation_timeout(self, _timeout):
                pass

            def goto(self, url, **_kwargs):
                visited.append(url)
                if self.fail:
                    raise RuntimeError("Google temporarily unavailable")
                self.url = url

            def wait_for_timeout(self, _timeout):
                pass

            def close(self):
                pass

        class FakeContext:
            pages = [FakePage(fail=True)]

            def new_page(self):
                return FakePage()

            def close(self):
                pass

        class FakePlaywright:
            def __init__(self):
                self.chromium = self

            def launch_persistent_context(self, **_kwargs):
                return FakeContext()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        worker = main.ReferenceCollectionWorker(
            "실시간 키워드",
            queue.Queue(),
            "https://www.google.com/search?q=%EC%8B%A4%EC%8B%9C%EA%B0%84+%ED%82%A4%EC%9B%8C%EB%93%9C",
        )
        helper = main.SignalKeywordWorker(queue.Queue())
        with (
            patch("playwright.sync_api.sync_playwright", return_value=FakePlaywright()),
            patch.object(main, "require_google_chrome_executable", return_value=Path("/fake/chrome")),
            patch.object(worker, "_extract_ai_summary", return_value=""),
            patch.object(worker, "_extract_relevant_article_links", return_value=[]),
        ):
            self.assertEqual(worker._fetch_playwright_facts(helper), [])

        self.assertEqual(len(visited), 2)
        self.assertTrue(visited[0].startswith("https://www.google.com/search?q="))
        self.assertTrue(visited[1].startswith("https://search.naver.com/search.naver?query="))

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
