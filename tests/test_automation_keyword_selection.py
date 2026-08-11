import queue
import unittest
from unittest.mock import patch

import main


class AutomationKeywordSelectionTests(unittest.TestCase):
    def test_selected_payloads_skip_top_ten_fetch_and_only_generate_selected_items(self) -> None:
        result_queue: queue.Queue = queue.Queue()
        selected_payloads = [
            {"source_name": "다음 실시간", "rank": 2, "keyword": "선택 키워드 A"},
            {"source_name": "다음 실시간", "rank": 7, "keyword": "선택 키워드 B"},
        ]
        worker = main.AutomationKeywordQueueWorker(
            main.WordPressSettings(),
            ["daum"],
            result_queue,
            selected_payloads=selected_payloads,
        )

        with (
            patch.object(worker, "_fetch_keyword_payloads", side_effect=AssertionError("전체 목록을 다시 조회하면 안 됩니다.")),
            patch.object(worker, "_collect_reference_text_for_keyword", return_value="사실 기반 참고내용"),
            patch.object(
                worker,
                "_generate_article_payload",
                side_effect=[
                    ("선택 키워드 A 제목", "<p>A 본문</p>", "Codex CLI"),
                    ("선택 키워드 B 제목", "<p>B 본문</p>", "Codex CLI"),
                ],
            ),
        ):
            worker.run()

        events = []
        while not result_queue.empty():
            events.append(result_queue.get_nowait())
        completed_items = [payload for event, payload in events if event == "automation_collect_item_done"]
        done_payloads = [payload for event, payload in events if event == "automation_collect_done"]

        self.assertEqual([item["keyword"] for item in completed_items], ["선택 키워드 A", "선택 키워드 B"])
        self.assertEqual(done_payloads, [{"count": 2, "total": 2}])

    def test_discovery_returns_only_first_ten_candidates_without_generating_articles(self) -> None:
        result_queue: queue.Queue = queue.Queue()
        payloads = [
            {"source_name": "시그널", "rank": index, "keyword": f"후보 {index}"}
            for index in range(1, 13)
        ]
        worker = main.AutomationKeywordDiscoveryWorker(
            main.WordPressSettings(),
            "signal",
            result_queue,
        )

        with patch.object(main.AutomationKeywordQueueWorker, "_fetch_keyword_payloads", return_value=payloads):
            worker.run()

        event, result = result_queue.get_nowait()
        self.assertEqual(event, "automation_keyword_candidates_done")
        self.assertEqual(result["source"], "signal")
        self.assertEqual(len(result["items"]), 10)
        self.assertEqual(result["items"][-1]["keyword"], "후보 10")

    def test_keyword_identity_ignores_spacing_case_and_symbols(self) -> None:
        normalize = main.KeywordApp._automation_keyword_identity
        self.assertEqual(normalize("  Signal-Keyword! "), normalize("signal keyword"))
        self.assertEqual(normalize("청년도약 계좌"), normalize("청년도약계좌"))


if __name__ == "__main__":
    unittest.main()
