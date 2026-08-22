import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class NaverBlogContentWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MAIN_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.methods = {
            node.name: node
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _method_source(self, name: str) -> str:
        return ast.get_source_segment(self.source, self.methods[name]) or ""

    def test_article_generation_uses_nblog_prompts_and_selected_writing_model(self) -> None:
        source = self._method_source("generate_naver_blog_article")

        self.assertIn("naver_blog_title_prompt", source)
        self.assertIn("naver_blog_topic_prompt", source)
        self.assertIn("naver_blog_conversion_rules", source)
        self.assertGreaterEqual(source.count("generate_text_with_writing_model"), 2)

    def test_workflow_collects_reference_article_and_images(self) -> None:
        source = self._method_source("build_naver_blog_workflow_payload")

        self.assertIn("_collect_reference_text_for_keyword", source)
        self.assertIn("generate_naver_blog_article", source)
        self.assertIn("collect_naver_blog_image_files", source)
        self.assertIn('"manifest.json"', source)

    def test_editor_fills_title_body_and_attaches_files_without_publish(self) -> None:
        source = self._method_source("fill_naver_blog_editor")

        self.assertIn("_replace_naver_editor_text(", source)
        self.assertIn("title_locator,", source)
        self.assertIn("naver_blog_editor_blocks_from_html", source)
        self.assertIn("distribute_naver_blog_image_groups", source)
        self.assertIn("_attach_naver_blog_image_group", source)
        self.assertIn("apply_naver_blog_quote_style", source)
        self.assertIn('keyboard.press(f"{modifier}+B")', source)
        self.assertNotIn(":has-text('발행')", source)

    def test_image_attachment_uses_collage_for_each_multi_image_group(self) -> None:
        source = self._method_source("_attach_naver_blog_image_group")

        self.assertIn(":has-text('사진')", source)
        self.assertIn("set_input_files(existing_files)", source)
        self.assertIn("attached_count >= 2", source)
        self.assertIn("select_naver_image_collage", source)

    def test_images_are_limited_and_distributed_over_three_body_positions(self) -> None:
        source = self._method_source("distribute_naver_blog_image_groups")

        self.assertIn("[:10]", source)
        self.assertIn("divmod(len(paths), 3)", source)
        self.assertIn("range(3)", source)

    def test_body_input_uses_real_enter_keys_for_smarteditor_paragraphs(self) -> None:
        source = self._method_source("_replace_naver_editor_multiline_text")

        self.assertIn('split("\\n")', source)
        self.assertIn('keyboard.press("Enter")', source)
        self.assertIn("keyboard.insert_text(line)", source)

    def test_readability_normalizer_splits_inline_headings_and_sentences(self) -> None:
        source = self._method_source("normalize_naver_blog_paragraph_spacing")

        self.assertIn("Korean model output occasionally omits a space", source)
        self.assertIn('"\\n\\n".join(paragraphs)', source)
        self.assertIn("len(current) >= 2", source)

    def test_article_prompt_requires_short_paragraphs_and_blank_lines(self) -> None:
        source = self._method_source("generate_naver_blog_article")

        self.assertIn("한 문단은 2~4문장", source)
        self.assertIn("문단 사이에는 반드시 빈 줄", source)
        self.assertIn("소제목을 3~6개", source)
        self.assertIn("`**중요 문장**`", source)
        self.assertIn("normalize_naver_blog_paragraph_spacing", source)

    def test_collage_selector_scans_pages_and_frames(self) -> None:
        source = self._method_source("select_naver_image_collage")

        self.assertIn("editor_page.context.pages", source)
        self.assertIn("page.frames", source)
        self.assertIn("콜라주", source)
        self.assertIn("get_by_role", source)

    def test_settings_expose_persistent_image_count(self) -> None:
        source = self._method_source("_build_naver_blog_settings_tab")
        save_source = self._method_source("_save_naver_blog_settings")

        self.assertIn('text="첨부 이미지 수"', source)
        self.assertIn('range(1, 11)', source)
        self.assertIn("naver_blog_image_count", save_source)
        self.assertIn(", 10)", save_source)

    def test_worker_preserves_the_ten_image_limit(self) -> None:
        worker = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ClassDef) and node.name == "NaverBlogBootstrapWorker"
        )
        source = ast.get_source_segment(self.source, worker) or ""

        self.assertIn("min(int(image_count or 2), 10)", source)
        self.assertNotIn("min(int(image_count or 2), 4)", source)

    def test_append_cursor_excludes_media_captions_and_quote_sources(self) -> None:
        locator_source = self._method_source("_visible_last_naver_editor_locator")
        focus_source = self._method_source("_focus_naver_blog_editor_end")

        self.assertIn("inTextComponent", locator_source)
        self.assertIn("inMediaComponent", locator_source)
        self.assertIn("isCaptionOrSource", locator_source)
        self.assertIn("NAVER_BLOG_TEXT_PARAGRAPH_SELECTORS", focus_source)
        self.assertIn("require_text_component=True", focus_source)
        self.assertIn("range.collapse(false)", focus_source)

    def test_heading_is_filled_inside_quote_and_never_in_source_field(self) -> None:
        quote_source = self._method_source("apply_naver_blog_quote_style")
        filler_source = self._method_source("_fill_latest_naver_quote_component")
        editor_source = self._method_source("fill_naver_blog_editor")

        self.assertIn("_fill_latest_naver_quote_component", quote_source)
        self.assertIn("출처|source|cite", filler_source)
        self.assertIn("_clean_naver_blog_heading_text", filler_source)
        self.assertIn("data-blog-helper-quote-target", filler_source)
        self.assertIn("heading_text=block_text", editor_source)
        self.assertIn("_clean_naver_blog_heading_text(block_text)", editor_source)

    def test_editor_starts_and_continues_only_in_normal_text_components(self) -> None:
        source = self._method_source("fill_naver_blog_editor")
        bootstrap_source = self._method_source("_wait_for_safe_naver_blog_body_locator")

        self.assertIn("_wait_for_safe_naver_blog_body_locator", source)
        self.assertIn("NAVER_BLOG_TEXT_PARAGRAPH_SELECTORS", bootstrap_source)
        self.assertIn("exclude_quote=True", bootstrap_source)
        self.assertIn("require_text_component=True", bootstrap_source)

    def test_blank_editor_bootstrap_activates_only_a_safe_body_field(self) -> None:
        source = self._method_source("_wait_for_safe_naver_blog_body_locator")

        self.assertIn("NAVER_BLOG_BODY_ACTIVATION_SELECTORS", source)
        self.assertIn("require_text_component=True", source)
        self.assertIn("require_editable=False", source)
        self.assertIn("exclude_title=True", source)
        self.assertIn("exclude_quote=True", source)
        self.assertNotIn('keyboard.press("Enter")', source)
        self.assertIn("NAVER_BLOG_TEXT_PARAGRAPH_SELECTORS", source)


if __name__ == "__main__":
    unittest.main()
