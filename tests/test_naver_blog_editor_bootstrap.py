import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class NaverBlogEditorBootstrapTests(unittest.TestCase):
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

    def test_writing_tab_uses_topic_label_and_theme_palette(self) -> None:
        source = self._method_source("_build_naver_blog_writing_tab")

        self.assertIn('text="글 주제"', source)
        self.assertNotIn('text="발행명"', source)
        self.assertIn("palette = self._theme_palette()", source)
        self.assertIn('placeholder_text="예: 이번 주 연예 이슈 정리"', source)

    def test_page_and_tab_switch_use_theme_palette(self) -> None:
        page_source = self._method_source("_build_naver_blog_page")
        switch_source = self._method_source("_switch_naver_blog_tab")

        self.assertIn("palette = self._theme_palette()", page_source)
        self.assertIn('fg_color=palette["shell"]', page_source)
        self.assertIn("palette = self._theme_palette()", switch_source)
        self.assertIn('fg_color=palette["selected"]', switch_source)

    def test_bootstrap_waits_for_real_editor_and_keeps_it_open(self) -> None:
        source = self._method_source("run_naver_blog_playwright_bootstrap")

        self.assertIn("wait_for_naver_blog_editor", source)
        self.assertIn('("naver_blog_editor_ready", payload)', source)
        self.assertIn("editor_page.is_closed()", source)
        self.assertNotIn("return True, {", source)

    def test_bootstrap_logs_each_stage_and_translates_profile_collisions(self) -> None:
        bootstrap_source = self._method_source("run_naver_blog_playwright_bootstrap")
        friendly_source = self._method_source("friendly_naver_blog_automation_error")

        self.assertIn('append_runtime_log("NBlog"', bootstrap_source)
        self.assertIn("traceback.format_exc()", bootstrap_source)
        self.assertIn("friendly_naver_blog_automation_error", bootstrap_source)
        self.assertIn("user data directory is already in use", friendly_source)
        self.assertIn("Blog Helper가 두 개 실행 중이면 하나만 남겨 주세요", friendly_source)
        self.assertIn('append_runtime_log("NBlog", f"자동화 예외:', self.source)

    def test_editor_ready_event_is_handled_by_ui(self) -> None:
        source = self._method_source("_poll_queue")

        self.assertIn('event_type == "naver_blog_editor_ready"', source)
        self.assertIn('"에디터 열림 · 작성 대기"', source)
        self.assertIn('"작성 완료 · 사진 {attached_count}개 첨부"', source)

    def test_writing_tab_exposes_real_automation_stop_control(self) -> None:
        build_source = self._method_source("_build_naver_blog_writing_tab")
        stop_source = self._method_source("_stop_naver_blog_bootstrap")

        self.assertIn("self.naver_blog_stop_button", build_source)
        self.assertIn('text="자동화 중단"', build_source)
        self.assertIn("command=self._stop_naver_blog_bootstrap", build_source)
        self.assertIn("worker.cancel()", stop_source)

    def test_cancel_event_reaches_browser_editor_and_resets_ui(self) -> None:
        bootstrap_source = self._method_source("run_naver_blog_playwright_bootstrap")
        poll_source = self._method_source("_poll_queue")

        self.assertIn("cancel_event=self.cancel_event", self.source)
        self.assertIn("_raise_if_naver_blog_cancelled(cancel_event)", bootstrap_source)
        self.assertIn(
            '("naver_blog_cancelled", "자동화를 중단하고 처음 상태로 돌아왔습니다.")',
            self.source,
        )
        self.assertIn('event_type == "naver_blog_cancelled"', poll_source)
        self.assertIn("_reset_naver_blog_automation_controls()", poll_source)

    def test_editor_locator_rejects_hidden_clipboard_helpers(self) -> None:
        locator_source = self._method_source("_visible_naver_editor_locator")
        fill_source = self._method_source("fill_naver_blog_editor")
        replace_source = self._method_source("_replace_naver_editor_text")
        body_selectors = self.source[
            self.source.index("NAVER_BLOG_BODY_SELECTORS ="):self.source.index("\n\n\ndef _focus_naver_blog_editor_end")
        ]

        self.assertIn("aria-hidden", locator_source)
        self.assertIn("clipboard-read", locator_source)
        self.assertIn("getBoundingClientRect", locator_source)
        self.assertIn("require_editable", locator_source)
        self.assertIn("[role='textbox'][contenteditable='true']", body_selectors)
        self.assertIn(".se-documentTitle .se-title-text", fill_source)
        self.assertIn("[data-a11y-title='본문'] .se-module-text", body_selectors)
        self.assertIn("require_editable=False", fill_source)
        self.assertIn("_replace_naver_editor_text", fill_source)
        self.assertIn("keyboard.insert_text", replace_source)

    def test_existing_draft_popup_is_cancelled_before_editor_fill(self) -> None:
        cancel_source = self._method_source("_cancel_naver_existing_draft_popup_in_target")
        dismiss_source = self._method_source("dismiss_naver_existing_draft_popup")
        bootstrap_source = self._method_source("run_naver_blog_playwright_bootstrap")

        self.assertIn("작성 중인 글이 있습니다", cancel_source)
        self.assertIn("label === '취소'", cancel_source)
        self.assertNotIn("label === '확인'", cancel_source)
        self.assertIn('get_by_role("button", name="취소", exact=True)', cancel_source)
        self.assertIn("_cancel_naver_existing_draft_popup_in_target", dismiss_source)
        self.assertLess(
            bootstrap_source.index("dismiss_naver_existing_draft_popup"),
            bootstrap_source.index("fill_naver_blog_editor"),
        )

    def test_writing_tab_exposes_semi_and_full_automation_modes(self) -> None:
        build_source = self._method_source("_build_naver_blog_writing_tab")
        fill_source = self._method_source("fill_naver_blog_editor")

        self.assertIn('values=["반자동", "완전자동"]', build_source)
        self.assertIn("NAVER_BLOG_AUTOMATION_MODE_SEMI", self.source)
        self.assertIn("automation_mode=self.wordpress_settings.naver_blog_automation_mode", self.source)
        self.assertIn("use_rich_formatting = automation_mode == NAVER_BLOG_AUTOMATION_MODE_FULL", fill_source)
        self.assertIn("if not use_rich_formatting", fill_source)
        self.assertIn('keyboard.press("Enter")', fill_source)


if __name__ == "__main__":
    unittest.main()
