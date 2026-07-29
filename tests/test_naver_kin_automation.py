import ast
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

    def test_collector_opens_detail_pages_and_extracts_body(self) -> None:
        source = self._method_source("run_naver_kin_playwright_bootstrap")

        self.assertIn("detail_page.goto", source)
        self.assertIn("extract_question_body(detail_page)", source)
        self.assertIn("naver_kin_question_ready(question)", source)
        self.assertIn('sort_mode == "최신순"', source)

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


if __name__ == "__main__":
    unittest.main()
