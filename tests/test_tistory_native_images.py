import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class TistoryNativeImageTests(unittest.TestCase):
    def test_representative_image_replaces_old_preview_through_thumb_box_input(self) -> None:
        class FakeInput:
            selected_path = ""

            def set_input_files(self, value) -> None:
                if value:
                    self.selected_path = str(value)

            def evaluate(self, _script: str) -> str:
                return Path(self.selected_path).name

        class FakeLocator:
            def __init__(self, candidate: FakeInput) -> None:
                self.candidate = candidate

            def count(self) -> int:
                return 1

            def nth(self, _index: int) -> FakeInput:
                return self.candidate

        class FakePage:
            def __init__(self) -> None:
                self.input = FakeInput()
                self.selector = ""

            def locator(self, selector: str) -> FakeLocator:
                self.selector = selector
                return FakeLocator(self.input)

            def wait_for_timeout(self, _milliseconds: int) -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "현재_글_썸네일.png"
            image_path.write_bytes(b"current-thumbnail")
            page = FakePage()
            events: queue.Queue = queue.Queue()

            with (
                patch.object(main, "wait_for_tistory_publish_panel", return_value=True),
                patch.object(
                    main,
                    "collect_tistory_representative_preview_signatures",
                    side_effect=[["old-preview"], ["new-preview"]],
                ),
                patch.object(main, "remove_tistory_existing_representative_image", return_value=True) as remove,
            ):
                attached = main.attach_tistory_representative_image_file(
                    page,
                    str(image_path),
                    events,
                )

            self.assertTrue(attached)
            remove.assert_called_once_with(page)
            self.assertIn(".box_thumb", page.selector)
            self.assertEqual(Path(page.input.selected_path), image_path.resolve())

    def test_thumbnail_filename_uses_title_and_underscores(self) -> None:
        self.assertEqual(
            main.build_thumbnail_filename("심규덕 변호사 핵심 정보"),
            "심규덕_변호사_핵심_정보.png",
        )
        self.assertEqual(
            main.build_thumbnail_filename('제목: 테스트/확인?'),
            "제목_테스트확인.png",
        )

    def test_fresh_upload_url_never_reuses_previous_image(self) -> None:
        old_url = "https://blog.kakaocdn.net/dna/old/image/img.png?x=1"
        new_url = "https://blog.kakaocdn.net/dna/new/image/img.png?x=2"

        self.assertEqual(
            main.choose_fresh_tistory_image_url({old_url}, [old_url]),
            "",
        )
        self.assertEqual(
            main.choose_fresh_tistory_image_url({old_url}, [old_url, new_url]),
            new_url,
        )

    def test_attachment_response_json_returns_uploaded_url(self) -> None:
        uploaded_url = "https://blog.kakaocdn.net/dna/new-key/image/img.png?credential=test"
        response = '{"name":"card.png","url":"' + uploaded_url + '","size":123}'

        self.assertEqual(main.extract_tistory_attachment_url(response), uploaded_url)
        self.assertEqual(main.extract_tistory_attachment_url({"url": uploaded_url}), uploaded_url)

    def test_local_cardnews_placeholder_becomes_native_upload_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "body-cardnews-test.png"
            image_path.write_bytes(b"test-image")
            source = "<h2>핵심 내용</h2>" + main.build_cardnews_image_figure(image_path, "테스트", 1)

            prepared, native_files = main.prepare_tistory_native_attachment_html(source, "테스트")

            self.assertEqual(list(native_files.values()), [str(image_path)])
            self.assertIn("__BLOG_HELPER_TISTORY_NATIVE_IMAGE_1__", prepared)
            self.assertNotIn(str(image_path), prepared)
            self.assertNotIn("data:image", prepared)
            self.assertIn("blog-helper-cardnews-image", prepared)

    def test_thumbnail_body_uses_uploaded_content_url(self) -> None:
        script = main.build_tistory_editor_automation_script(
            "테스트 제목",
            "<p>본문</p>",
            thumbnail_data_url="data:image/png;base64,AA==",
            thumbnail_content_url="__BLOG_HELPER_TISTORY_NATIVE_THUMBNAIL__",
        )

        self.assertIn('const thumbnailContentUrl = "__BLOG_HELPER_TISTORY_NATIVE_THUMBNAIL__"', script)
        self.assertIn("const thumbnailHtml = thumbnailContentUrl", script)
        self.assertNotIn("const thumbnailHtml = thumbnailDataUrl", script)

    def test_publish_script_leaves_representative_image_to_playwright_file_chooser(self) -> None:
        script = main.build_tistory_editor_automation_script(
            "테스트 제목",
            "<p>본문</p>",
            thumbnail_data_url="data:image/png;base64,AA==",
            thumbnail_content_url="https://blog.kakaocdn.net/current-thumbnail.png",
            automation_actions=[
                "set_title",
                "set_body",
                "click_complete",
                "attach_representative_image",
                "click_public_publish",
            ],
            publish_after_input=True,
        )

        actions_start = script.index("const automationActions = ")
        actions_end = script.index(";", actions_start)
        actions_line = script[actions_start:actions_end]
        self.assertIn("click_complete", actions_line)
        self.assertNotIn("attach_representative_image", actions_line)
        self.assertNotIn("click_public_publish", actions_line)


if __name__ == "__main__":
    unittest.main()
