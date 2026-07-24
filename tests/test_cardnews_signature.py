import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class CardnewsSignatureTests(unittest.TestCase):
    def test_default_cardnews_uses_custom_signature_and_border_color(self) -> None:
        settings = main.WordPressSettings(
            cardnews_signature="현기쿠 & 가족",
            cardnews_border_color="보라색",
        )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "cardnews.png"
            with (
                patch("main.random.choice", return_value="보라색"),
                patch("main.render_svg_to_png", return_value=destination) as render,
            ):
                result = main.create_default_cardnews_png(
                    settings,
                    "카드뉴스 제목",
                    "한 줄 요약",
                    destination,
                    1,
                    2,
                )

        svg_markup = render.call_args.args[0]
        self.assertEqual(result, destination)
        self.assertIn("현기쿠 &amp; 가족", svg_markup)
        self.assertIn('fill="#7c3aed"', svg_markup)
        self.assertNotIn(">BLOG HELPER CARD NEWS</text>", svg_markup)

    def test_cardnews_signature_defaults_for_existing_settings(self) -> None:
        self.assertEqual(
            main.WordPressSettings().cardnews_signature,
            "BLOG HELPER CARD NEWS",
        )


if __name__ == "__main__":
    unittest.main()
