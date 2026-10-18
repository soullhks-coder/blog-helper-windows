"""Run the real editor-input script on an isolated page, never publish a post."""
import json
import os
from pathlib import Path
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix="blog-helper-numeric-title-") as directory:
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as app
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(app.require_google_chrome_executable()), headless=True)
            try:
                page = browser.new_page()
                page.route("**/*", lambda route: route.abort())
                for mode in app.TEXT_INPUT_MODE_OPTIONS:
                    for raw in ("2026년 10월 18일 운세확인하기", "3.5% 예금 금리"):
                        title = app.clean_generated_title(raw, "기본 제목")
                        assert title == raw
                        page.set_content('''<textarea id="post-title-inp" placeholder="제목"></textarea>
                            <textarea id="editor-tistory"></textarea>
                            <button id="publish" onclick="window.published=true">발행</button>''')
                        script = app.build_tistory_editor_automation_script(
                            title, "<p>로컬 입력 검사</p>", input_mode=mode,
                            automation_actions=["set_title", "set_body"], publish_after_input=False)
                        result = json.loads(page.evaluate(script))
                        assert result["titleOk"] and result["bodyOk"], result
                        assert page.locator("#post-title-inp").input_value() == raw
                        assert page.evaluate("window.published !== true")
                        print(f"{mode}: numeric prefix preserved in real editor input: {raw}", flush=True)
            finally:
                browser.close()


if __name__ == "__main__":
    main()
