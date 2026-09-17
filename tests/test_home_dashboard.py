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
            "newneek": ([_insight("뉴닉 키워드", "Newneek")], {"뉴닉 키워드": "참고"}),
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
        self.assertEqual(completed_sources, ["daum", "signal", "newneek"])
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
