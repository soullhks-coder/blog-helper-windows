import queue
import unittest
from unittest.mock import patch

import main


class WritingModelSelectionTests(unittest.TestCase):
    def test_model_names_are_normalized_for_ui_and_storage(self) -> None:
        self.assertEqual(main.normalize_writing_model("CLI"), main.WRITING_MODEL_CODEX)
        self.assertEqual(main.normalize_writing_model("GPT API"), main.WRITING_MODEL_GPT)
        self.assertEqual(main.normalize_writing_model("제미나이 API"), main.WRITING_MODEL_GEMINI)
        self.assertEqual(main.writing_model_label("codex"), "CLI")

    def test_existing_installation_migrates_to_codex_once(self) -> None:
        payload = {
            "preferred_ai_provider": "gpt",
            "applied_settings_migrations": [main.TISTORY_PROTECTION_OFF_MIGRATION],
        }
        migrated, changed = main.AppStateStore._apply_settings_migrations(payload)
        self.assertTrue(changed)
        self.assertEqual(migrated["preferred_ai_provider"], main.WRITING_MODEL_CODEX)
        self.assertTrue(migrated["codex_cli_enabled"])
        self.assertIn(
            main.CODEX_DEFAULT_WRITING_MODEL_MIGRATION,
            migrated["applied_settings_migrations"],
        )

        migrated["preferred_ai_provider"] = main.WRITING_MODEL_GEMINI
        migrated_again, changed_again = main.AppStateStore._apply_settings_migrations(migrated)
        self.assertFalse(changed_again)
        self.assertEqual(migrated_again["preferred_ai_provider"], main.WRITING_MODEL_GEMINI)

    def test_codex_model_uses_cli_without_api_key(self) -> None:
        settings = main.WordPressSettings(
            preferred_ai_provider=main.WRITING_MODEL_CODEX,
            gpt_api_key="",
            gemini_api_key="",
        )
        result = main.CodexCLIExecutionResult(True, output="CLI 본문")
        with (
            patch("main.resolve_codex_cli_path", return_value="/tmp/codex"),
            patch("main.execute_codex_cli_text", return_value=result) as mocked_cli,
            patch("main.OpenAIClient") as mocked_openai,
            patch("main.GeminiClient") as mocked_gemini,
        ):
            output, provider = main.generate_text_with_writing_model(settings, "긴 프롬프트")

        self.assertEqual((output, provider), ("CLI 본문", "Codex CLI"))
        mocked_cli.assert_called_once_with(settings, "긴 프롬프트")
        mocked_openai.assert_not_called()
        mocked_gemini.assert_not_called()

    def test_article_and_automation_workers_share_cli_dispatch(self) -> None:
        settings = main.WordPressSettings(preferred_ai_provider=main.WRITING_MODEL_CODEX)
        with patch(
            "main.generate_text_with_writing_model",
            return_value=("공통 결과", "Codex CLI"),
        ) as mocked_generate:
            article_worker = main.ArticleGenerationWorker(
                settings,
                "주제",
                "키워드",
                "",
                queue.Queue(),
            )
            automation_worker = main.AutomationKeywordQueueWorker(
                settings,
                [],
                queue.Queue(),
            )
            self.assertEqual(
                article_worker._generate_with_provider("일반 글", progress=0.4),
                ("공통 결과", "Codex CLI"),
            )
            self.assertEqual(
                automation_worker._generate_with_provider("자동화 글"),
                ("공통 결과", "Codex CLI"),
            )

        self.assertEqual(mocked_generate.call_count, 2)

    def test_codex_command_reads_prompt_from_stdin(self) -> None:
        command = main.build_codex_exec_command(
            main.WordPressSettings(),
            "/tmp/codex",
            main.Path("/tmp/codex-output.txt"),
            "x" * 20_000,
        )
        self.assertEqual(command[-1], "-")
        self.assertNotIn("x" * 20_000, command)


if __name__ == "__main__":
    unittest.main()
