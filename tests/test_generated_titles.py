"""Numeric title preservation from model output to Tistory automation input."""
import json
import queue
import re
import unittest
from unittest.mock import patch

import main


TITLE = "2026년 10월 18일 운세확인하기"
BODY = f"<h1>{TITLE}</h1><p>숫자가 포함된 제목을 보존합니다.</p>"


class GeneratedTitleTests(unittest.TestCase):
    def test_meaningful_numeric_prefixes_are_preserved(self):
        for title in (TITLE, "2026. 10. 18. 운세", "10. 18. 운세", "10.18 운세",
                      "2026 운세", "2026", "24시간 운영 매장", "1인 가구 지원",
                      "100% 할인 안내", "3.5% 예금 금리", "-3.5% 수익률",
                      "5G 요금제", "1.2 버전 변경사항", "2026. 운세"):
            with self.subTest(title=title):
                self.assertEqual(main.clean_generated_title(title, "기본 제목"), title)

    def test_formatting_and_clear_list_markers_do_not_consume_title_numbers(self):
        for raw, expected in (
            (f"### {TITLE}", TITLE), (f"- {TITLE}", TITLE), (f"• {TITLE}", TITLE),
            (f"“{TITLE}”", TITLE), (f"<h1>{TITLE}</h1>", TITLE),
            (f"```html\n{TITLE}\n```", TITLE), (f"제목: {TITLE}", TITLE),
            (f"블로그 제목: '{TITLE}'", TITLE), (f"1) {TITLE}", TITLE),
            ("1. 운세확인하기", "운세확인하기"), ("2) 24시간 운영 매장", "24시간 운영 매장"),
            ("1. 2026년 운세", "1. 2026년 운세"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(main.clean_generated_title(raw, "기본 제목"), expected)
        self.assertEqual(main.clean_generated_title("", TITLE), TITLE)
        self.assertEqual(main.clean_generated_title("###", TITLE), TITLE)

    def test_core_keyword_keeps_numeric_subject_for_title_metadata(self):
        for keyword in ("2026년 운세", "24시간 매장", "3.5% 예금", "5G 요금제", "1인 가구"):
            with self.subTest(keyword=keyword):
                self.assertEqual(main.derive_core_keyword(keyword), keyword)

    def test_article_generation_preserves_title_and_passes_it_to_body_prompt(self):
        events = queue.Queue()
        with patch.object(main, "generate_text_with_writing_model", side_effect=[
            (TITLE, "fixture"), (BODY, "fixture")]) as generate:
            main.ArticleGenerationWorker(main.WordPressSettings(), "운세", TITLE, "", events).run()
        done = next(payload for kind, payload in events.queue if kind == "article_done")
        self.assertEqual(done["title"], TITLE)
        self.assertEqual(done["html"], BODY)
        self.assertIn(f"글 제목은 '{TITLE}'", generate.call_args_list[1].args[1])

    def test_tistory_worker_keeps_numeric_and_manually_edited_titles_at_input_boundary(self):
        for title in (TITLE, "3.5% 예금 금리", "24시간 운영 매장"):
            with self.subTest(title=title), \
                 patch.object(main, "GOOGLE_IMAGE_COLLAGE_ENABLED", False), \
                 patch.object(main, "append_runtime_log"), \
                 patch.object(main, "cleanup_tistory_automation_files"), \
                 patch.object(main, "run_tistory_playwright_automation", return_value=(False, "fixture")) as browser:
                main.TistoryAutomationWorker(title, BODY, queue.Queue(), ads_enabled=False).run()
                browser.assert_called_once()
                self.assertEqual(browser.call_args.kwargs["expected_title"], title)
                script_title = re.search(r"const title = (.*);", browser.call_args.args[1]).group(1)
                self.assertEqual(json.loads(script_title), title)


if __name__ == "__main__":
    unittest.main()
