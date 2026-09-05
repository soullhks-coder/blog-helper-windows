import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class WritingFixedProgressTests(unittest.TestCase):
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

    def test_panel_is_outside_scrollable_workflow(self) -> None:
        page_source = self._method_source("_build_writing_page")
        panel_source = self._method_source("_build_fixed_writing_progress_panel")

        self.assertIn("self.writing_scroll.grid(row=1", page_source)
        self.assertIn("self._build_fixed_writing_progress_panel()", page_source)
        self.assertIn("self.writing_page", panel_source)
        self.assertIn("panel.grid(row=2", panel_source)

    def test_panel_displays_all_four_workflow_stages(self) -> None:
        panel_source = self._method_source("_build_fixed_writing_progress_panel")

        self.assertIn("WRITING_STAGE_LABELS.values()", panel_source)
        self.assertIn('f"{index}-circle"', panel_source)

    def test_overall_progress_combines_stage_and_stage_fraction(self) -> None:
        progress_source = self._method_source("_set_writing_progress")

        self.assertIn("((stage - 1) + fraction) / 4", progress_source)
        self.assertIn("self.writing_fixed_progress_bar.set(overall_progress)", progress_source)

    def test_queue_events_update_fixed_progress(self) -> None:
        queue_source = self._method_source("_poll_queue")

        for event_name in (
            'event_type == "progress"',
            'event_type == "reference_collect_progress"',
            'event_type == "article_progress"',
            'event_type == "publish_progress"',
            'event_type == "tistory_progress"',
        ):
            self.assertIn(event_name, queue_source)
        self.assertGreaterEqual(queue_source.count("self._set_writing_progress("), 15)

    def test_completion_and_reset_update_panel(self) -> None:
        completion_source = self._method_source("_show_writing_complete_dialog")
        reset_source = self._method_source("_reset_writing_accordion_state")

        self.assertIn('state="complete"', completion_source)
        self.assertIn('state="idle"', reset_source)


if __name__ == "__main__":
    unittest.main()
