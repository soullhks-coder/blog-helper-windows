import ast
import unittest
from pathlib import Path

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class WritingAutoProgressTests(unittest.TestCase):
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

    def test_auto_progress_is_opt_in_by_default(self) -> None:
        self.assertFalse(main.WordPressSettings().writing_auto_progress)

    def test_old_subtitle_is_removed(self) -> None:
        self.assertNotIn(
            "키워드를 분석하고 바로 워드프레스 초안 저장 또는 발행까지 연결합니다.",
            self.source,
        )

    def test_keyword_selection_is_manual_before_auto_progress(self) -> None:
        self.assertNotIn("_auto_select_first_keyword", self.source)
        for method_name in (
            "start_analysis",
            "load_daum_keywords",
            "load_signal_keywords",
            "load_newneek_keywords",
        ):
            self.assertNotIn(
                "self._arm_writing_auto_progress()",
                self._method_source(method_name),
            )
        selection_source = self._method_source("_on_recommended_keyword_selected")
        self.assertIn("self._arm_writing_auto_progress()", selection_source)
        self.assertIn("self._auto_collect_reference_for_keyword", selection_source)

    def test_auto_progress_runs_article_and_publish_in_order(self) -> None:
        self.assertIn(
            "self._generate_article_from_selection()",
            self._method_source("_auto_continue_after_reference"),
        )
        publish_source = self._method_source("_auto_continue_after_article")
        self.assertIn("self._go_to_thumbnail_section()", publish_source)
        self.assertIn("self._auto_publish_current_article", publish_source)

    def test_collected_public_data_skips_duplicate_reference_collection(self) -> None:
        source = self._method_source("_start_auto_progress_with_collected_reference")

        self.assertIn("self._arm_writing_auto_progress()", source)
        self.assertIn("self._auto_continue_after_reference", source)
        self.assertNotIn("self._auto_collect_reference_for_keyword", source)

    def test_manual_reference_collection_shows_completion_dialog(self) -> None:
        queue_source = self._method_source("_poll_queue")
        self.assertIn("self._show_reference_collection_complete_dialog", queue_source)
        self.assertIn("if self.writing_auto_run_active", queue_source)

    def test_publish_completion_stops_auto_progress(self) -> None:
        self.assertIn(
            "self._stop_writing_auto_progress()",
            self._method_source("_handle_publish_pipeline_success"),
        )

    def test_top_targets_are_wordpress_and_tistory(self) -> None:
        page_source = self._method_source("_build_writing_page")
        self.assertIn('("wordpress", "워드프레스")', page_source)
        self.assertIn('("tistory", "티스토리")', page_source)


if __name__ == "__main__":
    unittest.main()
