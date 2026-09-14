from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class BlogspotPlaywrightTests(unittest.TestCase):
    def test_writing_targets_include_blogspot_after_tistory(self) -> None:
        source = inspect.getsource(main.KeywordApp._build_writing_page)
        wordpress = source.index('("wordpress", "워드프레스")')
        tistory = source.index('("tistory", "티스토리")')
        blogspot = source.index('("blogspot", "블로그스팟")')
        self.assertLess(wordpress, tistory)
        self.assertLess(tistory, blogspot)

    def test_blogspot_settings_are_playwright_only(self) -> None:
        source = inspect.getsource(main.KeywordApp._build_blogspot_card)
        self.assertIn("Playwright 전용 Chrome", source)
        self.assertIn("로그인/프로필 확인", source)
        self.assertIn("blogspot_save_mode_var", source)
        self.assertNotIn("Client Secret", source)
        self.assertNotIn("Google 인증", source)

    def test_publish_pipeline_uses_playwright_instead_of_blogger_api(self) -> None:
        source = inspect.getsource(main.PublishPipelineWorker.run)
        self.assertIn("run_blogspot_playwright_automation", source)
        blogspot_block = source[source.index("blogspot_result = None") : source.index("threads_result = None")]
        self.assertNotIn("BlogspotClient", blogspot_block)
        self.assertNotIn("blogspot_access_token", blogspot_block)

    def test_blogspot_profile_and_publish_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                blogspot_blog_id="123456",
                blogspot_blog_url="https://example.blogspot.com/",
                blogspot_blog_name="테스트 블로그",
                blogspot_save_mode=main.TISTORY_SAVE_MODE_DRAFT,
                blogspot_reference_image_protection_mode=True,
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()
        self.assertEqual(loaded.blogspot_blog_id, "123456")
        self.assertEqual(loaded.blogspot_blog_url, "https://example.blogspot.com/")
        self.assertEqual(loaded.blogspot_blog_name, "테스트 블로그")
        self.assertEqual(loaded.blogspot_save_mode, main.TISTORY_SAVE_MODE_DRAFT)
        self.assertTrue(loaded.blogspot_reference_image_protection_mode)

    def test_local_images_are_removed_from_html_and_queued_for_native_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "card.png"
            thumbnail_path = Path(directory) / "thumb.png"
            image_path.write_bytes(b"image")
            thumbnail_path.write_bytes(b"thumbnail")
            html = (
                f'<p>본문</p><figure><img src="{image_path}"></figure>'
                f'<figure class="blog-helper-inline-image" '
                f'data-blog-helper-inline-image-path="{image_path}">참고 이미지</figure>'
                '<img src="data:image/png;base64,AAAA">'
                '<img src="https://example.com/remote.jpg">'
            )
            cleaned, images = main.prepare_blogspot_html_and_images(
                html,
                str(thumbnail_path),
            )
        self.assertNotIn("data:image", cleaned)
        self.assertNotIn(str(image_path), cleaned)
        self.assertIn("https://example.com/remote.jpg", cleaned)
        self.assertEqual(images, [str(thumbnail_path), str(image_path)])

    def test_image_slots_are_distributed_and_removed_after_upload(self) -> None:
        html = "".join(
            [
                "<h2>첫째</h2><p>첫 문단</p>",
                "<h2>둘째</h2><p>둘째 문단</p>",
                "<h2>셋째</h2><p>셋째 문단</p>",
                "<h2>넷째</h2><p>넷째 문단</p>",
            ]
        )
        slotted, markers = main.insert_blogspot_image_slot_markers(html, 3)
        self.assertEqual(len(markers), 3)
        self.assertEqual(slotted.count(main.BLOGSPOT_IMAGE_SLOT_PREFIX), 3)
        self.assertLess(slotted.index(markers[0]), slotted.index(markers[1]))
        self.assertLess(slotted.index(markers[1]), slotted.index(markers[2]))
        cleaned = slotted
        for marker in markers:
            cleaned = main.remove_blogspot_image_slot_marker(cleaned, marker)
        self.assertNotIn(main.BLOGSPOT_IMAGE_SLOT_PREFIX, cleaned)

    def test_thumbnail_slot_is_pinned_above_article_body(self) -> None:
        html = "".join(
            [
                "<h2>첫째</h2><p>첫 문단</p>",
                "<h2>둘째</h2><p>둘째 문단</p>",
                "<h2>셋째</h2><p>셋째 문단</p>",
            ]
        )
        slotted, markers = main.insert_blogspot_image_slot_markers(
            html,
            3,
            first_image_at_top=True,
        )
        self.assertTrue(slotted.startswith(f"<p>{markers[0]}</p>\n<h2>"))
        self.assertLess(slotted.index(markers[0]), slotted.index("<h2>"))
        self.assertGreater(slotted.index(markers[1]), slotted.index("<h2>"))
        self.assertGreater(slotted.index(markers[2]), slotted.index(markers[1]))

    def test_prompt_tag_footer_is_moved_to_blogspot_labels(self) -> None:
        html = (
            "<h2>본문 제목</h2><p>본문 내용입니다.</p>"
            "<p><strong>주요 태그:</strong> 블로그스팟, 자동 포스팅, AI 글쓰기</p>"
        )
        cleaned, labels = main.extract_blogspot_prompt_labels(html)
        self.assertEqual(labels, ["블로그스팟", "자동 포스팅", "AI 글쓰기"])
        self.assertNotIn("주요 태그", cleaned)
        self.assertIn("본문 내용입니다.", cleaned)

    def test_unlabeled_comma_tag_footer_is_supported(self) -> None:
        html = (
            "<h2>본문 제목</h2><p>본문 내용입니다.</p>"
            "<p>#블로그스팟, #자동포스팅, #Blogger</p>"
        )
        cleaned, labels = main.extract_blogspot_prompt_labels(html)
        self.assertEqual(labels, ["블로그스팟", "자동포스팅", "Blogger"])
        self.assertNotIn("#블로그스팟", cleaned)

    def test_normal_final_sentence_is_not_used_as_blogspot_labels(self) -> None:
        html = "<h2>마무리</h2><p>가격, 후기, 신청 방법을 차례로 확인해 보세요.</p>"
        cleaned, labels = main.extract_blogspot_prompt_labels(html)
        self.assertEqual(labels, [])
        self.assertEqual(cleaned, html)

    def test_blogger_editor_uses_open_html_option_and_codemirror(self) -> None:
        source = inspect.getsource(main.run_blogspot_playwright_automation)
        html_switch_source = inspect.getsource(main.open_blogspot_html_editor)
        view_switch_source = inspect.getsource(main._open_blogspot_view_option)
        html_fill_source = inspect.getsource(main.fill_blogspot_html_editor)
        upload_source = inspect.getsource(main.upload_blogspot_images)
        current_layout_source = inspect.getsource(
            main.configure_blogspot_inserted_image
        )
        publish_confirm_source = inspect.getsource(
            main.confirm_blogspot_publish_dialog
        )
        self.assertIn("fill_blogspot_html_editor", source)
        self.assertIn('[role="listbox"][aria-label="보기 전환"]:visible', view_switch_source)
        self.assertIn('[role="option"][data-value="{option_value}"]:visible', view_switch_source)
        self.assertIn('option.first.press("Enter")', view_switch_source)
        self.assertIn('page.keyboard.press("Escape")', view_switch_source)
        self.assertIn("switcher.click(force=True)", view_switch_source)
        self.assertIn('page.locator(".CodeMirror:visible")', html_switch_source)
        self.assertNotIn('get_by_text("HTML 보기", exact=True)', html_switch_source)
        self.assertIn("element.CodeMirror.setValue", html_fill_source)
        self.assertIn("element.CodeMirror.getValue", html_fill_source)
        self.assertIn('textarea[aria-label*="라벨을 구분"]', source)
        self.assertIn("extract_blogspot_prompt_labels(article_html)", source)
        self.assertIn('join(blogspot_prompt_labels)', source)
        self.assertNotIn('str(tag or "").strip() for tag in tag_names', source)
        self.assertIn('[role="menuitem"]:visible', upload_source)
        self.assertIn('upload_option.press("Enter")', upload_source)
        self.assertNotIn('get_by_text("컴퓨터에서 업로드"', upload_source)
        self.assertIn('iframe[src*="docs.google.com/picker"]:visible', upload_source)
        self.assertIn("expect_file_chooser", upload_source)
        self.assertIn("set_files(valid_paths)", upload_source)
        self.assertIn("page.wait_for_timeout(2_500)", upload_source)
        self.assertIn('re.compile(r"^\\s*레이아웃 선택\\s*$")', upload_source)
        self.assertIn("last_picker_action_at", upload_source)
        self.assertIn("page.wait_for_timeout(2_000)", upload_source)
        self.assertIn("collect_blogspot_compose_image_sources", upload_source)
        self.assertIn("configure_blogspot_inserted_image", upload_source)
        self.assertIn('"blogger.googleusercontent.com"', upload_source)
        self.assertIn('name="가운데 정렬"', current_layout_source)
        self.assertIn('(\"매우 크게\", \"아주 크게\")', current_layout_source)
        self.assertIn('_select_blogspot_layout_choice(page, "아주 크게")', upload_source)
        self.assertIn('_select_blogspot_layout_choice(page, "가운데")', upload_source)
        self.assertIn('get_by_role("button", name="확인", exact=True)', upload_source)
        self.assertIn("return len(valid_paths)", upload_source)
        self.assertIn("글을\\s*게시하시겠습니까", publish_confirm_source)
        self.assertIn('name="확인", exact=True', publish_confirm_source)
        self.assertIn("confirm_button.click(force=True)", publish_confirm_source)
        self.assertIn("확인창 닫힘 확인", publish_confirm_source)
        self.assertIn("confirm_blogspot_publish_dialog(page)", source)

    def test_blogger_toggles_compose_mode_for_each_body_image(self) -> None:
        source = inspect.getsource(main.run_blogspot_playwright_automation)
        compose_source = inspect.getsource(main.open_blogspot_compose_editor)
        focus_source = inspect.getsource(main.focus_blogspot_image_slot)
        pipeline_source = inspect.getsource(main.PublishPipelineWorker.run)
        settings_source = inspect.getsource(main.KeywordApp._build_blogspot_card)
        self.assertIn("insert_blogspot_image_slot_markers", source)
        self.assertIn("first_image_at_top=thumbnail_at_top", source)
        self.assertIn("focus_blogspot_image_slot", source)
        self.assertIn("remove_blogspot_image_slot_marker", source)
        self.assertIn('_open_blogspot_view_option(page, "compose")', compose_source)
        self.assertIn("range.collapse(true)", focus_source)
        self.assertIn("collect_tistory_reference_image_files", source)
        self.assertIn("reference_image_protection_mode", source)
        self.assertIn("blogspot_reference_image_protection_mode", pipeline_source)
        self.assertIn("저작권 보호 모드", settings_source)


if __name__ == "__main__":
    unittest.main()
