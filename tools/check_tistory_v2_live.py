"""Visible, isolated V2 integration check; never saves or publishes a post."""
from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import time
from urllib.parse import urlsplit


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--deck", type=Path, required=True)
    parser.add_argument("--storage-state", type=Path, required=True)
    parser.add_argument("--thumbnail", type=Path)
    parser.add_argument("--pipeline-thumbnail", type=Path,
                        help="Upload the thumbnail before body input, as the shared writing pipeline does.")
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if not (parsed.hostname or "").endswith(".tistory.com") or parsed.path != "/manage/newpost":
        raise ValueError("A Tistory new-post URL is required.")
    deck = json.loads(args.deck.read_text(encoding="utf-8"))
    paths = [args.deck.parent / f"slide-{i:02d}.png" for i in range(1, 11)]
    if any(not path.is_file() for path in paths):
        raise ValueError("Ten exported slides are required.")
    report = lambda message: print(message, flush=True)
    with tempfile.TemporaryDirectory(prefix="blog-helper-v2-live-runtime-") as directory:
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main
        import tistory_v2_editor as native
        from tistory_v2_publish import _source_credits
        from playwright.sync_api import sync_playwright

        saved = json.loads(args.storage_state.read_text(encoding="utf-8"))
        state = {"cookies": [c for c in saved.get("cookies", []) if main.is_tistory_auth_cookie(c)], "origins": []}
        body, _ = main.extract_tistory_prompt_tags(main.normalize_generated_article_html(deck["body"]))
        body += _source_credits(deck["slides"])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(main.require_google_chrome_executable()),
                                                  headless=False, args=["--start-maximized"])
            context = browser.new_context(storage_state=state, no_viewport=True)
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.dismiss())
            report("V2 전체 입력 테스트: 별도 Chrome에 본문과 슬라이드 10장만 입력합니다. 저장·발행하지 않습니다.")
            page.goto(args.url, wait_until="domcontentloaded")
            page.bring_to_front()
            deadline = time.monotonic() + 180
            while not page.evaluate("() => Boolean(window.tinymce?.activeEditor?.getBody?.())"):
                if page.is_closed() or time.monotonic() > deadline:
                    raise RuntimeError("테스트 편집기를 열지 못했습니다.")
                if "auth/login" in page.url:
                    main.advance_tistory_kakao_login(page)
                page.wait_for_timeout(500)
            # An editor instance can already exist while its initial toolbar is
            # still loading or an HTML-mode preference hides the image menu.
            page.evaluate(main.build_tistory_editor_automation_script(
                "", "", mode_only=True, automation_actions=["switch_basic", "confirm_dialog"]))
            page.locator('#mceu_0-open').wait_for(state='visible', timeout=30000)
            used = set()

            def upload(path):
                url = main.upload_tistory_image_file(page, path, queue.Queue(), used)
                used.add(url)
                return url

            thumbnail_url = upload(str(args.pipeline_thumbnail)) if args.pipeline_thumbnail else ""
            script = main.build_tistory_editor_automation_script(
                deck["title"], body, publish_after_input=False,
                thumbnail_content_url=thumbnail_url,
                automation_actions=["switch_html", "confirm_dialog", "set_title", "set_body",
                                    "switch_basic", "confirm_dialog", "set_rich_body"],
            )
            page.evaluate(script)
            page.wait_for_timeout(1600)
            read_text = "() => {" + native._EDITOR + "return originalText();}"
            before = page.evaluate(read_text)
            try:
                result = native.insert_native_slide_pairs(page, [str(p) for p in paths],
                                                         str(args.thumbnail or ""), upload, report)
                report("LIVE_V2_OK: " + json.dumps(result, ensure_ascii=False))
            except Exception as exc:
                report("LIVE_V2_FAILED: " + str(exc))
            after = page.evaluate(read_text)
            differences = []
            for kind, a1, a2, b1, b2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
                if kind != "equal":
                    differences.append({"kind": kind, "before_pos": a1, "after_pos": b1,
                                        "before": repr(before[a1:a2])[:240], "after": repr(after[b1:b2])[:240]})
            report("TEXT_DIFF: " + json.dumps({"before_length": len(before), "after_length": len(after),
                                             "changes": differences[:12]}, ensure_ascii=False))
            report("테스트 창을 자동 종료하지 않습니다. 검토 후 이 별도 Chrome 창을 닫아 주세요.")
            while not page.is_closed() and browser.is_connected():
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    break
            browser.close()


if __name__ == "__main__":
    run()
