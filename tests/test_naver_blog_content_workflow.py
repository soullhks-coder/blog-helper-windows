import ast
import unittest
from pathlib import Path
from unittest.mock import patch

import main


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
        self.assertIn("generate_naver_blog_tags_with_ai", source)
        self.assertIn('"tag_names": tag_names', source)
        self.assertIn('"manifest.json"', source)

    def test_ai_tags_are_extracted_from_the_finished_article(self) -> None:
        settings = main.WordPressSettings(naver_blog_topic_category="정치·시사")
        events = __import__("queue").Queue()
        response = '["이재명", "더불어민주당", "민생지원 정책", "정치 후기", "정치 가격"]'
        body = "이재명 대통령과 더불어민주당은 민생지원 정책의 추진 방향을 논의했습니다."

        with patch.object(
            main,
            "generate_text_with_writing_model",
            return_value=(response, "테스트 AI"),
        ) as generate:
            tags = main.generate_naver_blog_tags_with_ai(
                settings,
                "민생지원 정책 발표",
                "이재명 대통령 민생지원 정책 논의",
                body,
                events,
            )

        prompt = generate.call_args.args[1]
        self.assertIn("최종 본문", prompt)
        self.assertIn(body, prompt)
        self.assertEqual(tags, ["이재명", "더불어민주당", "민생지원 정책"])
        self.assertNotIn("후기", " ".join(tags))
        self.assertNotIn("가격", " ".join(tags))

    def test_ai_tag_parser_accepts_json_object_and_rejects_ungrounded_terms(self) -> None:
        tags = main.parse_naver_blog_ai_tags(
            '{"tags": ["국회 본회의", "여야 협상", "구매 추천", "뉴스"]}',
            "국회 본회의 일정",
            "국회 본회의를 앞둔 여야 협상",
            "국회 본회의를 앞두고 여야 협상이 이어졌습니다.",
        )

        self.assertEqual(tags, ["국회 본회의", "여야 협상"])

    def test_fallback_tags_only_use_finished_article_words(self) -> None:
        tags = main.build_naver_blog_grounded_fallback_tags(
            "국회 예산안 협상",
            "여야, 국회 예산안 협상 재개",
            "여야 지도부가 국회에서 예산안 협상을 다시 시작했습니다.",
        )

        self.assertTrue(tags)
        self.assertFalse(any(word in " ".join(tags) for word in ("가격", "후기", "추천")))

    def test_full_automation_opens_publish_settings_and_enters_tags(self) -> None:
        bootstrap_source = self._method_source("run_naver_blog_playwright_bootstrap")
        tag_source = self._method_source("fill_naver_blog_publish_tags")
        panel_source = self._method_source("_open_naver_blog_publish_panel")

        self.assertIn("NAVER_BLOG_AUTOMATION_MODE_FULL", bootstrap_source)
        self.assertIn("fill_naver_blog_publish_tags", bootstrap_source)
        self.assertIn("_enter_naver_blog_tag", tag_source)
        self.assertIn("committed_count", tag_source)
        self.assertIn("입력·검증 완료", tag_source)
        self.assertIn("len(normalized_tags) >= 10", tag_source)
        self.assertIn('get_by_role("button", name=publish_pattern)', panel_source)

        enter_source = self._method_source("_enter_naver_blog_tag")
        verify_source = self._method_source("_naver_blog_tag_committed")
        self.assertIn("press_sequentially", enter_source)
        self.assertNotIn("range(2)", enter_source)
        self.assertIn('tag_input.press("Enter", delay=140)', enter_source)
        self.assertEqual(enter_source.count('press("Enter"'), 1)
        self.assertNotIn('press(f"{modifier}+A")', enter_source)
        self.assertIn("if not remaining_value", enter_source)
        self.assertIn("_naver_blog_tag_committed", enter_source)
        self.assertIn("chipFound", verify_source)
        self.assertIn("태그 확정 확인 실패(작업은 계속 유지)", tag_source)
        self.assertNotIn("retry=True", tag_source)

    def test_editor_fills_title_body_and_attaches_files_without_publish(self) -> None:
        source = self._method_source("fill_naver_blog_editor")

        self.assertIn("_replace_naver_editor_text(", source)
        self.assertIn("title_locator,", source)
        self.assertIn("naver_blog_editor_blocks_from_html", source)
        self.assertIn("distribute_naver_blog_image_groups", source)
        self.assertIn("_attach_naver_blog_image_group", source)
        self.assertIn("insert_naver_blog_quote_heading_and_click_below", source)
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
        insert_source = self._method_source("insert_naver_blog_quote_heading_and_click_below")
        editor_source = self._method_source("fill_naver_blog_editor")

        self.assertIn("_fill_latest_naver_quote_component", quote_source)
        self.assertIn("출처|source|cite", filler_source)
        self.assertIn("_clean_naver_blog_heading_text", filler_source)
        self.assertIn("data-blog-helper-quote-target", filler_source)
        self.assertIn("_clean_naver_blog_heading_text", insert_source)
        self.assertLess(
            insert_source.index("apply_naver_blog_quote_style"),
            insert_source.index("_leave_naver_blog_quote"),
        )
        self.assertIn("quote_click_distance_px=click_distance", insert_source)
        self.assertNotIn('keyboard.press("Enter")', insert_source)
        self.assertIn("insert_naver_blog_quote_heading_and_click_below", editor_source)
        self.assertIn("heading_text=block_text", editor_source)
        self.assertIn("_clean_naver_blog_heading_text(block_text)", editor_source)

    def test_quote_exit_clicks_body_below_without_pressing_enter(self) -> None:
        focus_source = self._method_source("_focus_naver_blog_paragraph_after_latest_quote")
        leave_source = self._method_source("_leave_naver_blog_quote")
        caret_source = self._method_source("_naver_blog_active_normal_paragraph")

        self.assertIn("locator.click", focus_source)
        self.assertIn("sourceSelector", focus_source)
        self.assertIn("ancestor.querySelectorAll", focus_source)
        self.assertIn("hasSourceField", focus_source)
        self.assertIn("other.contains(candidate)", focus_source)
        self.assertIn("quoteRect.bottom + clickDistance", focus_source)
        self.assertIn("offset: clickDistance", focus_source)
        self.assertIn('"clickDistance": click_distance', focus_source)
        self.assertIn("quote.contains(hit)", focus_source)
        self.assertIn("position=", focus_source)
        self.assertIn("preferred_target=target", focus_source)
        self.assertIn("selection.anchorNode", caret_source)
        self.assertIn("preferred_target", caret_source)
        self.assertNotIn('keyboard.press("Enter")', leave_source)

    def test_quote_fields_are_never_reused_as_normal_body_paragraphs(self) -> None:
        last_locator_source = self._method_source("_visible_last_naver_editor_locator")
        focus_end_source = self._method_source("_focus_naver_blog_editor_end")

        for source in (last_locator_source, focus_end_source):
            self.assertIn('[class*="quotation"]', source)
            self.assertIn('[class*="quote"]', source)
            self.assertIn('.se-quotation', source)
            self.assertIn('.se-quote', source)

    def test_quote_heading_uses_only_one_line_then_immediately_clicks_below(self) -> None:
        calls = []

        def apply_quote(_page, **kwargs):
            calls.append(("input", kwargs["heading_text"]))
            return True

        def click_below(_page, **kwargs):
            calls.append(("click", kwargs["quote_click_distance_px"]))
            return True

        with (
            patch.object(main, "apply_naver_blog_quote_style", side_effect=apply_quote),
            patch.object(main, "_leave_naver_blog_quote", side_effect=click_below),
        ):
            completed = main.insert_naver_blog_quote_heading_and_click_below(
                object(),
                "첫 번째 소제목\n두 번째 줄은 입력하면 안 됨",
                quote_click_distance_px=275,
            )

        self.assertTrue(completed)
        self.assertEqual(calls, [("input", "첫 번째 소제목"), ("click", 275)])

    def test_quote_click_distance_setting_reaches_the_editor(self) -> None:
        ui_source = self._method_source("_build_naver_blog_writing_tab")
        save_source = self._method_source("_save_naver_blog_settings")
        read_source = self._method_source("_read_wordpress_settings")
        start_source = self._method_source("_start_naver_blog_bootstrap")
        editor_source = self._method_source("fill_naver_blog_editor")
        bootstrap_source = self._method_source("run_naver_blog_playwright_bootstrap")

        self.assertIn('text="인용구 하단 클릭거리 (px)"', ui_source)
        self.assertIn("naver_blog_quote_click_distance_entry", ui_source)
        self.assertIn("naver_blog_quote_click_distance_px", save_source)
        self.assertIn("naver_blog_quote_click_distance_px", read_source)
        self.assertIn("quote_click_distance_px=", start_source)
        self.assertIn("quote_click_distance_px=quote_click_distance_px", editor_source)
        self.assertIn("quote_click_distance_px=quote_click_distance_px", bootstrap_source)
        self.assertIn("인용구 하단 클릭거리 설정", bootstrap_source)

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
