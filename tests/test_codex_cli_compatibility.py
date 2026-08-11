import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_windows_codex_environment_forces_utf8_without_changing_macos(self) -> None:
        with patch.dict(main.os.environ, {"BLOG_HELPER_TEST": "kept"}, clear=True):
            windows_environment = main.codex_cli_process_environment("nt")
            macos_environment = main.codex_cli_process_environment("posix")

        self.assertEqual(windows_environment["PYTHONUTF8"], "1")
        self.assertEqual(windows_environment["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(windows_environment["BLOG_HELPER_TEST"], "kept")
        self.assertNotIn("PYTHONUTF8", macos_environment)
        self.assertNotIn("PYTHONIOENCODING", macos_environment)

    def test_codex_prompt_and_response_streams_use_utf8(self) -> None:
        settings = main.WordPressSettings(codex_cli_model=main.CODEX_MODEL_AUTO)

        def fake_run(command, **kwargs):
            output_path = Path(command[command.index("--output-last-message") + 1])
            output_path.write_text("한글 응답", encoding="utf-8")
            self.assertEqual(kwargs["input"], "한글 프롬프트")
            self.assertEqual(kwargs["encoding"], "utf-8")
            self.assertEqual(kwargs["errors"], "replace")
            self.assertEqual(kwargs["env"]["PYTHONUTF8"], "1")
            self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with (
            patch("main.resolve_codex_cli_path", return_value="C:/codex.cmd"),
            patch("main.codex_cli_version", return_value=("codex-cli 0.147.0", (0, 147, 0, 1))),
            patch(
                "main.codex_cli_process_environment",
                return_value={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            ),
            patch("main.subprocess.run", side_effect=fake_run),
        ):
            result = main.execute_codex_cli_text(settings, "한글 프롬프트")

        self.assertTrue(result.success)
        self.assertEqual(result.output, "한글 응답")


if __name__ == "__main__":
    unittest.main()
