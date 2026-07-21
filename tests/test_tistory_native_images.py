import tempfile
import unittest
from pathlib import Path

import main


class TistoryNativeImageTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
