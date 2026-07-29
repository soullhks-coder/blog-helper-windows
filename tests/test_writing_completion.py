import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class WritingCompletionFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MAIN_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.methods = {
            node.name: node
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _called_methods(self, method_name: str) -> set[str]:
        method = self.methods[method_name]
        return {
            node.func.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

    def test_confirmation_resets_accordion(self) -> None:
        calls = self._called_methods("_close_writing_complete_dialog_and_reset")
        self.assertIn("_reset_writing_accordion_state", calls)

    def test_publish_success_opens_completion_dialog(self) -> None:
        calls = self._called_methods("_handle_publish_pipeline_success")
        self.assertIn("_show_writing_complete_dialog", calls)

    def test_published_wordpress_post_submits_to_naver_search_advisor(self) -> None:
        method_source = ast.get_source_segment(
            self.source,
            self.methods["_handle_publish_pipeline_success"],
        )
        self.assertIn("self._queue_naver_search_advisor_submission", method_source)
        self.assertIn("published_url", method_source)
        self.assertIn('wordpress.get("status")', method_source)
        self.assertIn('== "publish"', method_source)

    def test_naver_search_advisor_uses_crawl_request_url(self) -> None:
        method_source = ast.get_source_segment(
            self.source,
            self.methods["run_naver_search_advisor_playwright"],
        )
        self.assertIn("NAVER_SEARCH_ADVISOR_CRAWL_URL", method_source)
        self.assertIn("NAVER_BLOG_CHROME_PROFILE_DIR", method_source)
        self.assertIn("url_input.fill(published_url)", method_source)
        self.assertIn("confirm_button.click()", method_source)
        self.assertIn("context.close()", method_source)
        self.assertIn(
            '"https://searchadvisor.naver.com/console/site/request/crawl"',
            self.source,
        )
        self.assertIn("?site=https%3A%2F%2Fblog.lhksoul.com", self.source)

    def test_automation_wordpress_publish_also_queues_crawl_request(self) -> None:
        method_source = ast.get_source_segment(
            self.source,
            self.methods["_handle_automation_publish_success"],
        )
        self.assertIn("self._queue_naver_search_advisor_submission", method_source)
        self.assertIn("show_feedback=False", method_source)

    def test_reset_returns_to_topic_section(self) -> None:
        method_source = ast.get_source_segment(
            self.source,
            self.methods["_reset_writing_accordion_state"],
        )
        self.assertIn('key == "topic"', method_source)
        self.assertIn('self.active_writing_section = "topic"', method_source)

    def test_manual_tistory_completion_opens_dialog(self) -> None:
        self.assertIn(
            'cleanup_tistory_automation_files()\n                        self._set_writing_section_completed("publish")\n                        self._show_writing_complete_dialog()',
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
