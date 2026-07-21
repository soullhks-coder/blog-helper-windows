import tempfile
import unittest
from pathlib import Path

import main


class TistoryNativeImageTests(unittest.TestCase):
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
