import ast
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class NaverBlogReferenceTextTests(unittest.TestCase):
    def test_reference_text_defaults_to_empty(self) -> None:
        self.assertEqual(main.WordPressSettings().naver_blog_reference_text, "")

    def test_manual_and_portal_references_are_combined(self) -> None:
        combined = main.combine_naver_blog_reference_text(
            "직접 입력한 핵심 정보",
            "포털에서 자동 수집한 최신 정보",
        )

        self.assertIn("[직접 입력한 참고내용]", combined)
        self.assertIn("직접 입력한 핵심 정보", combined)
        self.assertIn("[포털 자동수집 참고내용]", combined)
        self.assertIn("포털에서 자동 수집한 최신 정보", combined)

    def test_empty_manual_reference_keeps_portal_reference_only(self) -> None:
        self.assertEqual(
            main.combine_naver_blog_reference_text("", "포털 최신 정보"),
            "포털 최신 정보",
        )

    def test_long_references_keep_both_sources_within_prompt_limit(self) -> None:
        combined = main.combine_naver_blog_reference_text("직접" * 5000, "포털" * 5000)

        self.assertLessEqual(len(combined), 12000)
        self.assertIn("[직접 입력한 참고내용]", combined)
        self.assertIn("[포털 자동수집 참고내용]", combined)
        self.assertIn("직접", combined)
        self.assertIn("포털", combined)

    def test_reference_text_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                naver_blog_reference_text="반드시 포함할 사용자 참고내용",
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(
                    main.PromptFileStore,
                    "load_into",
                    side_effect=lambda value: value,
                ),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

        self.assertEqual(
            loaded.naver_blog_reference_text,
            "반드시 포함할 사용자 참고내용",
        )

    def test_workflow_combines_saved_reference_with_portal_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = main.WordPressSettings(
                naver_blog_reference_text="사용자가 직접 입력한 내용",
            )
            with (
                patch.object(
                    main.AutomationKeywordQueueWorker,
                    "_collect_reference_text_for_keyword",
                    return_value="포털에서 자동 수집한 내용",
                ) as collect_reference,
                patch.object(
                    main,
                    "generate_naver_blog_article",
                    return_value=("제목", "<p>본문</p>", "본문", "테스트 모델"),
                ),
                patch.object(main, "collect_naver_blog_image_files", return_value=[]),
            ):
                payload = main.build_naver_blog_workflow_payload(
                    settings,
                    "테스트 주제",
                    2,
                    directory,
                    queue.Queue(),
                )

        collection_payload = collect_reference.call_args.args[0]
        self.assertEqual(collection_payload["reference_text"], "")
        self.assertIn("사용자가 직접 입력한 내용", payload["reference_text"])
        self.assertIn("포털에서 자동 수집한 내용", payload["reference_text"])

    def test_manual_reference_allows_writing_when_portal_collection_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = main.WordPressSettings(
                naver_blog_reference_text="사용자가 제공한 단독 참고내용",
            )
            with (
                patch.object(
                    main.AutomationKeywordQueueWorker,
                    "_collect_reference_text_for_keyword",
                    return_value="",
                ),
                patch.object(
                    main,
                    "generate_naver_blog_article",
                    return_value=("제목", "<p>본문</p>", "본문", "테스트 모델"),
                ) as generate_article,
                patch.object(main, "collect_naver_blog_image_files", return_value=[]),
            ):
                payload = main.build_naver_blog_workflow_payload(
                    settings,
                    "테스트 주제",
                    2,
                    directory,
                    queue.Queue(),
                )

        generate_article.assert_called_once()
        self.assertIn("사용자가 제공한 단독 참고내용", payload["reference_text"])

    def test_writing_tab_exposes_reference_textbox_below_prompt(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        build_source = ast.get_source_segment(
            source,
            methods["_build_naver_blog_writing_tab"],
        ) or ""
        save_source = ast.get_source_segment(
            source,
            methods["_save_naver_blog_settings"],
        ) or ""

        self.assertIn('text="참고내용 (선택)"', build_source)
        self.assertIn("naver_blog_reference_textbox", build_source)
        self.assertIn("_on_naver_blog_reference_text_changed", build_source)
        self.assertIn("naver_blog_reference_text", save_source)


if __name__ == "__main__":
    unittest.main()
