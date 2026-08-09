import tempfile
import unittest
from pathlib import Path

import main


class CodexCLICompatibilityTests(unittest.TestCase):
    def test_version_parser_prefers_newer_numeric_release(self) -> None:
        self.assertGreater(
            main.parse_codex_cli_version("codex-cli 0.147.0-alpha.6.5"),
            main.parse_codex_cli_version("codex-cli 0.140.0"),
        )

    def test_automatic_model_is_not_forwarded(self) -> None:
        settings = main.WordPressSettings(
            codex_cli_model=main.CODEX_MODEL_AUTO,
            codex_cli_extra_args="--skip-git-repo-check",
        )
        command = main.build_codex_exec_command(
            settings,
            "/tmp/codex",
            Path("/tmp/output.txt"),
            "hello",
        )
        self.assertNotIn("--model", command)
        self.assertEqual(command.count("exec"), 1)

    def test_legacy_full_command_is_sanitized(self) -> None:
        settings = main.WordPressSettings(
            codex_cli_model="gpt-5.6-sol",
            codex_cli_extra_args=(
                "--model old-model -c model_reasoning_effort=medium "
                "--ask-for-approval never exec --sandbox workspace-write "
                "--output-last-message old.txt --color always --skip-git-repo-check"
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "result.txt"
            command = main.build_codex_exec_command(settings, "/tmp/codex", output_file, "hello")
        self.assertEqual(command.count("exec"), 1)
        self.assertEqual(command.count("--model"), 1)
        self.assertNotIn("old-model", command)
        self.assertNotIn("--ask-for-approval", command)
        self.assertNotIn("old.txt", command)
        self.assertIn("read-only", command)
        self.assertIn("model_reasoning_effort=medium", command)


if __name__ == "__main__":
    unittest.main()
