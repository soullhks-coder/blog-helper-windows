import inspect
import queue
import unittest
from unittest.mock import patch

import main


class GoogleTrendsKeywordTests(unittest.TestCase):
    def test_extracts_first_ten_ranked_rows_and_deduplicates_keywords(self) -> None:
        rows = []
        for index in range(1, 13):
            keyword = "급상승 검색어 1" if index == 2 else f"급상승 검색어 {index}"
            rows.append(
                f'''<tr role="row" data-row-id="{index - 1}">
                  <td></td><td><div class="mZ3RIc">{keyword}</div>
                  <div class="qNpYPd">검색 {index}천+회</div>
                  <div class="A7jE4">{index}시간 전</div></td>
                </tr>'''
            )

        extracted = main.extract_google_trends_rows("<table>" + "".join(rows) + "</table>")

        self.assertEqual(len(extracted), 10)
        self.assertEqual(extracted[0]["keyword"], "급상승 검색어 1")
        self.assertEqual(extracted[0]["volume"], "검색 1천+회")
        self.assertEqual(extracted[0]["started_at"], "1시간 전")
        self.assertEqual(extracted[-1]["keyword"], "급상승 검색어 11")

    def test_builds_recent_four_hour_top_ten_payload(self) -> None:
        html = "".join(
            f'''<tr role="row" data-row-id="{index - 1}">
              <td></td><td><div class="mZ3RIc">키워드 {index}</div>
              <div class="qNpYPd">검색 {index}백+회</div>
              <div class="A7jE4">{index}시간 전</div></td>
            </tr>'''
            for index in range(1, 11)
        )
        worker = main.GoogleTrendsKeywordWorker(queue.Queue())

        insights, reference_map = worker._build_google_payload(html)

        self.assertEqual(len(insights), 10)
        self.assertEqual(insights[0].keyword, "키워드 1")
        self.assertIn("최근 4시간 1위", insights[0].reasons[0])
        self.assertEqual(
            insights[0].source_urls["Google Trends"],
            "https://trends.google.co.kr/trending?geo=KR&hours=4",
        )
        self.assertIn("검색 1백+회", reference_map["키워드 1"])

    def test_automation_recommendation_ui_uses_loword_instead_of_google(self) -> None:
        source = inspect.getsource(main.KeywordApp._build_automation_page)

        expected = (
            '("daum", "다음"',
            '("signal", "시그널"',
            '("newneek", "뉴닉"',
            '("loword", "로워드"',
            '("naver", "네이버"',
        )
        positions = [source.index(token) for token in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn('("google", "구글"', source)

    def test_automation_fetch_supports_loword_and_naver_candidates(self) -> None:
        worker = main.AutomationKeywordQueueWorker(
            main.WordPressSettings(),
            ["loword", "naver"],
            queue.Queue(),
        )
        loword_insight = main.KeywordInsight(
            keyword="로워드 키워드",
            score=90,
            reasons=["실시간 검색어"],
            sources=["로워드"],
            categories=["로워드"],
            source_urls={"로워드": main.HomeDashboardKeywordWorker.LOWORD_TRENDS_URL},
        )
        naver_insight = main.KeywordInsight(
            keyword="네이버 제목",
            score=90,
            reasons=["메인 유입"],
            sources=["Naver Creator Advisor"],
            categories=["네이버"],
            source_urls={"Naver Creator Advisor": "https://blog.naver.com/example/1"},
        )

        with (
            patch.object(
                main.LowordKeywordWorker,
                "_fetch_loword_keywords",
                return_value=([loword_insight], {"로워드 키워드": "로워드 참고"}),
            ),
            patch.object(
                main.NaverCreatorAdvisorKeywordWorker,
                "_collect_items",
                return_value=[{"title": "네이버 제목", "url": "https://blog.naver.com/example/1"}],
            ),
            patch.object(
                main.NaverCreatorAdvisorKeywordWorker,
                "_build_payload",
                return_value=([naver_insight], {"네이버 제목": "네이버 참고"}),
            ),
        ):
            payloads = worker._fetch_keyword_payloads()

        self.assertEqual([item["keyword"] for item in payloads], ["로워드 키워드", "네이버 제목"])
        self.assertEqual([item["source_name"] for item in payloads], ["로워드", "네이버"])

    def test_loword_worker_emits_writing_events(self) -> None:
        result_queue: queue.Queue = queue.Queue()
        insight = main.KeywordInsight(
            keyword="로워드 검색어",
            score=100,
            reasons=["1위"],
            sources=["로워드"],
            categories=["실시간"],
            source_urls={"로워드": main.HomeDashboardKeywordWorker.LOWORD_TRENDS_URL},
        )
        worker = main.LowordKeywordWorker(result_queue)

        with patch.object(
            worker,
            "_fetch_loword_keywords",
            return_value=([insight], {"로워드 검색어": "참고"}),
        ):
            worker.run()

        progress = result_queue.get_nowait()
        done = result_queue.get_nowait()
        self.assertEqual(progress[0], "loword_progress")
        self.assertEqual(done[0], "loword_done")
        self.assertEqual(done[1]["insights"][0].keyword, "로워드 검색어")


if __name__ == "__main__":
    unittest.main()
