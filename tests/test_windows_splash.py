import ast
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_SPEC_PATH = PROJECT_ROOT / "BlogHelper.spec"


def _windows_splash_options() -> dict[str, object]:
    tree = ast.parse(WINDOWS_SPEC_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "Splash":
            continue
        return {
            keyword.arg: ast.literal_eval(keyword.value)
            for keyword in node.keywords
            if keyword.arg
            and keyword.arg
            not in {"binaries", "datas"}
        }
    raise AssertionError("BlogHelper.spec does not define a Windows splash screen")


class WindowsSplashTests(unittest.TestCase):
    def test_splash_displays_the_requested_korean_startup_message(self) -> None:
        options = _windows_splash_options()

        self.assertEqual(
            options["text_default"],
            "실행중...\n잠시만기다려주세요.",
        )
        # Braces keep the Windows font family's space intact in generated Tcl.
        self.assertEqual(options["text_font"], "{Malgun Gothic}")
        self.assertEqual(options["text_color"], "#FFFFFF")
        self.assertEqual(options["text_pos"], (80, 306))
        self.assertEqual(options["text_size"], -16)

    def test_splash_stays_visible_and_centered_on_the_active_monitor(self) -> None:
        options = _windows_splash_options()

        self.assertTrue(options["always_on_top"])
        self.assertEqual(options["center"], "active")


if __name__ == "__main__":
    unittest.main()
