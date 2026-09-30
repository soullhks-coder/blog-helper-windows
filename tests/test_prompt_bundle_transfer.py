import unittest
from types import SimpleNamespace

import main


class PromptBundleTransferTests(unittest.TestCase):
    def test_named_prompt_round_trip_keeps_korean_multiline_text(self) -> None:
        title = "축제 제목 지침\n{topic}을 포함해 작성"
        article = "첫 문단\n\n둘째 문단: {keyword}\n#태그, 축제"

        exported = main.format_prompt_bundle(
            "wordpress", "축제전용", title, article
        )
        imported = main.parse_prompt_bundle(exported)

        self.assertEqual(
            imported,
            {
                "platform": "wordpress",
                "name": "축제전용",
                "title_prompt": title,
                "article_prompt": article,
            },
        )

    def test_single_automation_prompt_round_trip(self) -> None:
        exported = main.format_prompt_bundle(
            "tistory_automation", "티스토리 자동화", "", "첫 단계\n두 번째 단계"
        )

        self.assertEqual(
            main.parse_prompt_bundle(exported)["article_prompt"],
            "첫 단계\n두 번째 단계",
        )

    def test_prompt_separator_lines_are_preserved_as_content(self) -> None:
        article = (
            "첫 문단\n"
            f"{main.PROMPT_BUNDLE_ARTICLE_MARKER}\n"
            "\\문자열도 유지"
        )

        exported = main.format_prompt_bundle("wordpress", "축제전용", "제목", article)

        self.assertEqual(main.parse_prompt_bundle(exported)["article_prompt"], article)

    def test_invalid_or_incomplete_bundle_is_rejected(self) -> None:
        for contents in (
            "일반 텍스트",
            f"{main.PROMPT_BUNDLE_HEADER}\n플랫폼: wordpress\n이름: 축제전용\n",
            main.format_prompt_bundle("wordpress", "축제전용", "", "본문"),
        ):
            with self.subTest(contents=contents[:50]), self.assertRaises(ValueError):
                main.parse_prompt_bundle(contents)

    def test_other_platform_cannot_replace_selected_editor(self) -> None:
        app = SimpleNamespace(
            active_prompt_platform="tistory",
            prompt_title_boxes={},
        )
        exported = main.format_prompt_bundle(
            "wordpress", "축제전용", "제목", "본문"
        )

        with self.assertRaisesRegex(ValueError, "플랫폼"):
            main.KeywordApp._apply_prompt_bundle_to_editor(app, exported)


if __name__ == "__main__":
    unittest.main()
