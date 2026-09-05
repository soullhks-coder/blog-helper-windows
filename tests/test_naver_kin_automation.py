import ast
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class NaverKinAutomationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MAIN_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.methods = {
            node.name: node
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _method_source(self, method_name: str) -> str:
        return ast.get_source_segment(self.source, self.methods[method_name]) or ""

    def test_question_title_does_not_include_list_metadata_or_body(self) -> None:
        raw = "청년도약계좌 가입 조건\n신청하려는데 소득 조건이 궁금합니다.\n답변 2 새 창"

        self.assertEqual(
            main.clean_naver_kin_question_title(raw),
            "청년도약계좌 가입 조건",
        )

    def test_browser_update_notice_is_not_used_as_question_title(self) -> None:
        self.assertEqual(
            main.clean_naver_kin_question_title("권장 브라우저 업데이트 안내"),
            "",
        )
        self.assertEqual(
            main.clean_naver_kin_question_title("크롬 브라우저 업데이트 후 오류 해결 방법"),
            "크롬 브라우저 업데이트 후 오류 해결 방법",
        )

    def test_question_body_removes_repeated_title(self) -> None:
        body = main.clean_naver_kin_question_body(
            "청년도약계좌 가입 조건: 신청하려는데 소득 조건과 준비 서류가 궁금합니다.",
            "청년도약계좌 가입 조건",
        )

        self.assertEqual(body, "신청하려는데 소득 조건과 준비 서류가 궁금합니다.")

    def test_only_detail_questions_with_body_are_automation_ready(self) -> None:
        ready = {
            "title": "청년도약계좌 가입 조건",
            "question_text": "신청하려는데 소득 조건과 준비 서류가 무엇인지 자세히 궁금합니다.",
            "url": "https://kin.naver.com/qna/detail.naver?d1id=1&docId=123",
        }
        title_only = {**ready, "question_text": ""}

        self.assertTrue(main.naver_kin_question_ready(ready))
        self.assertFalse(main.naver_kin_question_ready(title_only))

    def test_single_question_url_validation_accepts_detail_only(self) -> None:
        normalized = main.normalize_naver_kin_question_url(
            "kin.naver.com/qna/detail.naver?d1id=1&docId=123"
        )

        self.assertEqual(
            normalized,
            "https://kin.naver.com/qna/detail.naver?d1id=1&docId=123",
        )
        with self.assertRaisesRegex(ValueError, "질문 상세 URL"):
            main.normalize_naver_kin_question_url(
                "https://kin.naver.com/qna/questionList.naver"
            )
        with self.assertRaisesRegex(ValueError, "kin.naver.com"):
            main.normalize_naver_kin_question_url(
                "https://example.com/qna/detail.naver?docId=123"
            )

    def test_single_question_page_extracts_title_and_body(self) -> None:
        class FakeLocator:
            def __init__(self, text: str = "", attribute: str = "") -> None:
                self.text = text
                self.attribute = attribute

            @property
            def first(self):
                return self

            def count(self):
                return 1 if self.text or self.attribute else 0

            def nth(self, _index):
                return self

            def is_visible(self, timeout=0):
                return True

            def inner_text(self, timeout=0):
                return self.text

            def get_attribute(self, _name):
                return self.attribute

        class FakePage:
            url = "https://kin.naver.com/qna/detail.naver?docId=123"

            def locator(self, selector):
                if selector == ".questionDetail .c-heading__title":
                    return FakeLocator("청년도약계좌 가입 조건")
                if selector == ".questionDetail":
                    return FakeLocator(
                        "청년도약계좌 가입 조건: 신청하려는데 소득 조건과 필요한 준비 서류가 무엇인지 궁금합니다."
                    )
                return FakeLocator()

            def title(self):
                return "네이버 지식iN"

            def evaluate(self, _script):
                return ""

        question = main.extract_naver_kin_question_from_page(
            FakePage(),
            FakePage.url,
        )

        self.assertEqual(question["title"], "청년도약계좌 가입 조건")
        self.assertEqual(
            question["question_text"],
            "신청하려는데 소득 조건과 필요한 준비 서류가 무엇인지 궁금합니다.",
        )
        self.assertTrue(main.naver_kin_question_ready(question))

    def test_naver_kin_uses_explicit_wordpress_prompt(self) -> None:
        settings = main.WordPressSettings(
            prompt_sets=[
                {
                    "id": "wordpress-default",
                    "platform": "wordpress",
                    "name": "기본",
                    "title_prompt": "기본 제목",
                    "article_prompt": "기본 본문",
                },
                {
                    "id": "wordpress-kin",
                    "platform": "wordpress",
                    "name": "지식인 답변형",
                    "title_prompt": "지식인 제목",
                    "article_prompt": "지식인 본문",
                },
                {
                    "id": "tistory-news",
                    "platform": "tistory",
                    "name": "뉴스",
                    "title_prompt": "티스토리 제목",
                    "article_prompt": "티스토리 본문",
                },
            ],
            selected_prompt_id="tistory-news",
            naver_kin_wordpress_prompt_id="wordpress-kin",
        )

        selected = main.resolve_naver_kin_wordpress_prompt_set(settings)

        self.assertEqual(selected["id"], "wordpress-kin")
        self.assertEqual(selected["article_prompt"], "지식인 본문")

    def test_legacy_default_answer_prompt_is_migrated_without_touching_custom_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prompt_dir = Path(directory)
            prompt_path = prompt_dir / "naver_kin_answer_prompt.txt"
            prompt_path.write_text(main.LEGACY_NAVER_KIN_ANSWER_PROMPT, encoding="utf-8")
            with (
                patch.object(main, "PROMPT_STORAGE_DIR", prompt_dir),
                patch.object(main.PromptFileStore, "_ensure_desktop_shortcut"),
            ):
                settings = main.PromptFileStore.load_into(main.WordPressSettings())

            self.assertEqual(
                settings.naver_kin_answer_prompt,
                main.DEFAULT_NAVER_KIN_ANSWER_PROMPT,
            )
            self.assertEqual(
                prompt_path.read_text(encoding="utf-8").strip(),
                main.DEFAULT_NAVER_KIN_ANSWER_PROMPT,
            )

    def test_naver_kin_schedule_and_wordpress_prompt_selection_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                naver_kin_collect_interval_minutes=120,
                naver_kin_answer_interval_minutes=10,
                naver_kin_wordpress_prompt_id="wordpress-kin",
                naver_kin_next_action="collect",
                naver_kin_next_run_at=12345.0,
                naver_kin_direct_question_url="https://kin.naver.com/qna/detail.naver?docId=456",
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

            self.assertEqual(loaded.naver_kin_collect_interval_minutes, 120)
            self.assertEqual(loaded.naver_kin_answer_interval_minutes, 10)
            self.assertEqual(loaded.naver_kin_wordpress_prompt_id, "wordpress-kin")
            self.assertEqual(loaded.naver_kin_next_action, "collect")
            self.assertEqual(loaded.naver_kin_next_run_at, 12345.0)
            self.assertEqual(
                loaded.naver_kin_direct_question_url,
                "https://kin.naver.com/qna/detail.naver?docId=456",
            )

    def test_collector_opens_detail_pages_and_extracts_body(self) -> None:
        source = self._method_source("run_naver_kin_playwright_bootstrap")

        self.assertIn("detail_page.goto", source)
        self.assertIn("extract_question_body(detail_page)", source)
        self.assertIn("naver_kin_question_ready(question)", source)
        self.assertIn('sort_mode == "최신순"', source)
        self.assertLess(
            source.index('".questionTitle"'),
            source.index('for selector in ("main h1", "#content h1", "h1")'),
        )

    def test_answer_editor_uses_direct_keyboard_input_without_clipboard_paste(self) -> None:
        source = self._method_source("run_naver_kin_answer_playwright")

        self.assertIn("target_page.keyboard.type(line, delay=2)", source)
        self.assertIn("editor_contains_inserted_text(frame)", source)
        self.assertNotIn("paste_with_system_clipboard", source)
        self.assertNotIn('keyboard.press("Meta+V")', source)

    def test_collect_schedule_runs_fresh_playwright_collection(self) -> None:
        source = self._method_source("_run_naver_kin_automation_once")

        self.assertIn('scheduled_action == "collect"', source)
        self.assertIn("self._start_naver_kin_bootstrap()", source)

    def test_scheduler_skips_title_only_questions(self) -> None:
        source = self._method_source("_next_naver_kin_question_for_automation")

        self.assertIn("naver_kin_question_ready(question)", source)

    def test_worker_applies_wordpress_prompt_and_answer_prompt_separately(self) -> None:
        source = self._method_source("run")
        worker_source = self.source[
            self.source.index("class NaverKinAutomationWorker"):
            self.source.index("class ThumbnailAIWorker")
        ]

        self.assertIn("resolve_naver_kin_wordpress_prompt_set", worker_source)
        self.assertIn("wordpress_title_prompt_template", worker_source)
        self.assertIn("wordpress_article_prompt_template", worker_source)
        self.assertIn("build_naver_kin_answer_text", worker_source)
        self.assertTrue(source)

    def test_single_url_worker_collects_then_emits_question(self) -> None:
        result_queue = queue.Queue()
        question = {
            "title": "청년도약계좌 가입 조건",
            "question_text": "신청하려는데 소득 조건과 필요한 준비 서류가 무엇인지 궁금합니다.",
            "url": "https://kin.naver.com/qna/detail.naver?docId=123",
        }
        with patch.object(
            main,
            "run_naver_kin_single_question_playwright",
            return_value=(True, question),
        ):
            worker = main.NaverKinQuestionCollectorWorker(question["url"], result_queue)
            worker.run()

        self.assertEqual(result_queue.get_nowait(), ("naver_kin_direct_collected", question))

    def test_naver_kin_page_has_direct_url_flow_and_fixed_progress(self) -> None:
        source = self._method_source("_build_naver_kin_page")
        direct_handler = self._method_source("_handle_naver_kin_direct_collected")

        self.assertNotIn("네이버 지식인 최신 질문을 확인하고", source)
        self.assertNotIn('text="자동화 흐름"', source)
        self.assertNotIn("1. 지식인 질문 목록", source)
        self.assertIn('text="질문 목록 수집"', source)
        self.assertIn('text="지식인 URL"', source)
        self.assertIn("naver_kin_direct_collect_button", source)
        self.assertIn("naver_kin_fixed_progress_bar", source)
        self.assertIn("_start_naver_kin_question_worker", direct_handler)


if __name__ == "__main__":
    unittest.main()
