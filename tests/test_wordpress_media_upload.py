import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import main


class WordPressMediaUploadTests(unittest.TestCase):
    def test_korean_local_filename_uses_ascii_http_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "후티_반군_통행료_부과.png"
            image_path.write_bytes(b"png-data")

            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = b'{"id": 42, "source_url": "https://example.com/image.png"}'

            settings = main.WordPressSettings(
                blog_url="https://example.com",
                username="user",
                app_password="application-password",
            )
            client = main.WordPressClient(settings)

            with patch("main.urlopen", return_value=response) as mocked_urlopen:
                result = client.upload_media(image_path, "후티 반군 통행료 부과 검토")

            request = mocked_urlopen.call_args.args[0]
            disposition = dict(request.header_items())["Content-disposition"]
            disposition.encode("latin-1")

            self.assertEqual(result["id"], 42)
            self.assertEqual(image_path.name, "후티_반군_통행료_부과.png")
            self.assertRegex(
                disposition,
                r'^attachment; filename="blog-helper-media-[a-f0-9]{12}\.png"$',
            )

    def test_media_content_type_matches_file_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "카드뉴스.jpg"
            image_path.write_bytes(b"jpeg-data")

            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = b'{"id": 7}'

            settings = main.WordPressSettings(
                blog_url="https://example.com",
                username="user",
                app_password="application-password",
            )
            client = main.WordPressClient(settings)

            with patch("main.urlopen", return_value=response) as mocked_urlopen:
                client.upload_media(image_path, "카드뉴스")

            request = mocked_urlopen.call_args.args[0]
            self.assertEqual(dict(request.header_items())["Content-type"], "image/jpeg")


if __name__ == "__main__":
    unittest.main()
