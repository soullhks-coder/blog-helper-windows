import inspect
import json
import queue
import unittest
from unittest.mock import patch

import main


def _loword_flight_html(rows: list[dict]) -> str:
    payload = json.dumps(
        {"serpRelatedData": rows, "after": "kept outside the related rows"},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    chunk = json.dumps(payload, ensure_ascii=False)
    return f"<html><script>self.__next_f.push([1,{chunk}])</script></html>"


class LowordSerpRelatedKeywordTests(unittest.TestCase):
    def test_fetch_returns_only_the_rows_loword_supplies_when_fewer_than_ten(self) -> None:
        rows = [
            {
                "relKeyword": f"추석 연관어 {index}",
                "monthlySearchVolume": index * 1000,
                "relativeness": "높음",
                "compIdx": "보통",
            }
            for index in range(1, 7)
        ]
        client = main.LowordSerpRelatedKeywordClient()
        progress: list[tuple[float, str]] = []

        with patch.object(client, "_fetch_html", return_value=_loword_flight_html(rows)):
            insights = client.fetch("추석", lambda value, message: progress.append((value, message)))

        self.assertEqual(len(insights), 6)
        self.assertEqual([item.keyword for item in insights], [f"추석 연관어 {index}" for index in range(1, 7)])
        self.assertIn("월간 검색량 1,000", insights[0].reasons)
        self.assertEqual(insights[0].monthly_search_volume, "1,000")
        self.assertEqual(
            main.keyword_insight_choice_text(insights[0], 1),
            "1. 추석 연관어 1 • 1,000",
        )
        self.assertEqual(progress[-1], (1.0, "로워드 SERP 연관 검색어 6개를 불러왔습니다."))

    def test_build_insights_deduplicates_and_limits_results_to_ten(self) -> None:
        rows = [
            {"relKeyword": f"키워드 {index}", "monthlySearchVolume": index}
            for index in range(1, 13)
        ]
        rows.insert(2, {"relKeyword": "키워드 1", "monthlySearchVolume": 999})

        insights = main.LowordSerpRelatedKeywordClient._build_insights(rows)

        self.assertEqual(len(insights), 10)
        self.assertEqual(insights[0].keyword, "키워드 1")
        self.assertEqual(insights[-1].keyword, "키워드 10")
        self.assertEqual(
            insights[0].source_urls["Naver"],
            "https://search.naver.com/search.naver?query=%ED%82%A4%EC%9B%8C%EB%93%9C+1",
        )

    def test_extracts_serp_related_data_from_next_flight_payload(self) -> None:
        rows = [
            {"relKeyword": "추석선물", "monthlySearchVolume": 272000},
            {"relKeyword": "추석대체공휴일", "monthlySearchVolume": "검색량 없음"},
        ]

        extracted = main.LowordSerpRelatedKeywordClient._extract_serp_related_rows(
            _loword_flight_html(rows)
        )

        self.assertEqual(extracted, rows)
        insights = main.LowordSerpRelatedKeywordClient._build_insights(extracted)
        self.assertIn("검색량 없음", insights[1].reasons)
        self.assertEqual(
            main.keyword_insight_choice_text(insights[1], 2),
            "2. 추석대체공휴일 • 검색량 없음",
        )

    def test_non_loword_keyword_keeps_the_existing_plain_list_label(self) -> None:
        insight = main.KeywordInsight(
            keyword="일반 추천 키워드",
            score=90,
            reasons=["추천"],
            sources=["Naver"],
            categories=["일반"],
            source_urls={"Naver": "https://search.naver.com/"},
        )

        self.assertEqual(
            main.keyword_insight_choice_text(insight, 3),
            "3. 일반 추천 키워드",
        )

    def test_missing_payload_reports_a_clear_format_change_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "사이트 형식이 변경"):
            main.LowordSerpRelatedKeywordClient._extract_serp_related_rows(
                "<html><body>no related data</body></html>"
            )

    def test_analysis_worker_uses_http_client_and_emits_existing_done_event(self) -> None:
        result_queue: queue.Queue = queue.Queue()
        worker = main.AnalysisWorker("추석", result_queue)
        expected = [
            main.KeywordInsight(
                keyword="추석선물",
                score=100,
                reasons=["로워드 SERP 연관 검색어 1위"],
                sources=["Naver"],
                categories=["네이버 SERP 연관 검색어"],
                source_urls={"Naver": "https://search.naver.com/"},
            )
        ]

        with patch.object(worker.client, "fetch", return_value=expected) as fetch:
            worker.run()

        fetch.assert_called_once_with("추석", worker._report_progress)
        self.assertEqual(result_queue.get_nowait(), ("analysis_done", expected))

    def test_writing_search_ui_describes_loword_and_no_longer_requires_source_checkboxes(self) -> None:
        build_source = inspect.getsource(main.KeywordApp._build_writing_workflow)
        start_source = inspect.getsource(main.KeywordApp.start_analysis)

        self.assertIn("로워드 SERP(네이버 기준)", build_source)
        self.assertNotIn("최소 1개의 분석 소스", start_source)
        self.assertIn("AnalysisWorker(keyword, self.result_queue)", start_source)
        render_source = inspect.getsource(main.KeywordApp._render_keyword_choices)
        self.assertIn("keyword_insight_choice_text(insight, index)", render_source)


if __name__ == "__main__":
    unittest.main()
