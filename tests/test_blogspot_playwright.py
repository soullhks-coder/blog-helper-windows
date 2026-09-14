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

    def test_local_images_are_removed_from_html_and_queued_for_native_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "card.png"
            thumbnail_path = Path(directory) / "thumb.png"
            image_path.write_bytes(b"image")
            thumbnail_path.write_bytes(b"thumbnail")
            html = (
                f'<p>본문</p><figure><img src="{image_path}"></figure>'
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

    def test_blogger_editor_uses_open_html_option_and_codemirror(self) -> None:
        source = inspect.getsource(main.run_blogspot_playwright_automation)
        html_switch_source = inspect.getsource(main.open_blogspot_html_editor)
        html_fill_source = inspect.getsource(main.fill_blogspot_html_editor)
        upload_source = inspect.getsource(main.upload_blogspot_images)
        self.assertIn("fill_blogspot_html_editor", source)
        self.assertIn('[role="listbox"][aria-label="보기 전환"]:visible', html_switch_source)
        self.assertIn('[role="option"][data-value="html"]:visible', html_switch_source)
        self.assertIn('page.locator(".CodeMirror:visible")', html_switch_source)
        self.assertNotIn('get_by_text("HTML 보기", exact=True)', html_switch_source)
        self.assertIn("element.CodeMirror.setValue", html_fill_source)
        self.assertIn("element.CodeMirror.getValue", html_fill_source)
        self.assertIn('textarea[aria-label*="라벨을 구분"]', source)
        self.assertIn('[role="menuitem"]:visible', upload_source)
        self.assertIn('upload_option.press("Enter")', upload_source)
        self.assertNotIn('get_by_text("컴퓨터에서 업로드"', upload_source)
        self.assertIn('iframe[src*="docs.google.com/picker"]:visible', upload_source)
        self.assertIn("expect_file_chooser", upload_source)
        self.assertIn("set_files(valid_paths)", upload_source)


if __name__ == "__main__":
    unittest.main()
