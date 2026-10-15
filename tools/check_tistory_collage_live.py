"""Visible, two-image check of the app's Tistory uploader and collage gesture.

Never saves/publishes a post or writes the app's settings/login snapshots.
Run only for a user-authorized live editor test with the supplied image files.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import time
import uuid
from urllib.parse import urlsplit


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--images", nargs=2, type=Path, required=True)
    parser.add_argument("--storage-state", type=Path)
    parser.add_argument("--hold-seconds", type=int, default=600)
    parser.add_argument("--split", action="store_true", help="Show the native unmerge gesture after forming the collage.")
    args = parser.parse_args()
    host = urlsplit(args.url).hostname or ""
    if not host.endswith(".tistory.com") or "/manage/newpost" not in urlsplit(args.url).path:
        raise ValueError("A Tistory new-post URL is required.")
    if any(not path.is_file() for path in args.images):
        raise ValueError("Both sample image files must exist.")
    with tempfile.TemporaryDirectory(prefix="blog-helper-live-collage-runtime-") as directory:
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main
        import tistory_v2_editor as collage
        from playwright.sync_api import sync_playwright

        report = lambda text: print(text, flush=True)
        state = {"cookies": [], "origins": []}
        if args.storage_state and args.storage_state.is_file():
            saved = json.loads(args.storage_state.read_text(encoding="utf-8"))
            # Reuse sign-in cookies without restoring any editor drafts.
            state["cookies"] = [c for c in saved.get("cookies", []) if main.is_tistory_auth_cookie(c)]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(main.require_google_chrome_executable()),
                headless=False, args=["--start-maximized"],
            )
            context = browser.new_context(storage_state=state, no_viewport=True)
            page = context.new_page()
            page.on("dialog", lambda dialog: dialog.dismiss())
            report("보이는 티스토리 콜라주 테스트 창을 엽니다. 샘플 사진 2장만 첨부합니다.")
            page.goto(args.url, wait_until="domcontentloaded")
            page.bring_to_front()
            deadline = time.monotonic() + 300
            last_location = ""
            report("로그인이 필요하면 이 테스트 창에서 로그인해 주세요. 글쓰기 화면이 열리면 자동으로 계속합니다.")
            while True:
                try:
                    if page.evaluate("() => Boolean(window.tinymce?.activeEditor?.getBody?.())"):
                        break
                except Exception:
                    pass
                if page.is_closed() or time.monotonic() > deadline:
                    raise RuntimeError("글쓰기 화면을 열지 못했습니다.")
                location = urlsplit(page.url).hostname or ""
                location += urlsplit(page.url).path
                if location != last_location:
                    report("현재 테스트 창: " + location + " · " + page.title())
                    last_location = location
                if "tistory.com" in page.url and "auth/login" in page.url:
                    main.advance_tistory_kakao_login(page)
                page.wait_for_timeout(500)
            # This is a new isolated test browser, not the user's existing tab.
            page.evaluate("() => { const e=window.tinymce.activeEditor; e.setContent('<p>콜라주 동작 확인용 샘플</p>'); e.focus(); }")
            run_id = uuid.uuid4().hex
            page.evaluate(collage._PREPARE, run_id)
            frame = collage._body_frame(page, run_id)
            page.evaluate(collage._SELECT_SLOT, f"{run_id}-0")
            urls = []
            for i, path in enumerate(args.images):
                report(f"사진 {i + 1}/2를 티스토리에 직접 첨부합니다.")
                url = main.upload_tistory_image_file(page, str(path), queue.Queue(), set(urls))
                collage._mark_upload(page, url, f"{run_id}-slide-{i}", None)
                urls.append(url)
            report("사진 2장 첨부 완료. 두 번째 사진을 첫 번째 사진 위로 드래그합니다.")
            try:
                collage._drag_pair(page, frame, run_id, 0, urls, None, report)
                verified = page.evaluate(collage._VERIFY_PAIR, {"urls": urls, "marker": f"{run_id}-0"})
                if not verified:
                    raise RuntimeError("콜라주 완성 상태를 확인하지 못했습니다.")
                report("LIVE_COLLAGE_OK: 사진 2장이 순서대로 하나의 티스토리 콜라주에 들어갔습니다. 발행하지 않았습니다.")
                if args.split:
                    report("완성된 콜라주를 8초 동안 보여드린 뒤 해제합니다.")
                    page.wait_for_timeout(8000)
                    first = frame.locator(f'img[data-blog-helper-v2-upload="{run_id}-slide-0"]')
                    second = frame.locator(f'img[data-blog-helper-v2-upload="{run_id}-slide-1"]')
                    report("두 번째 사진을 아래쪽으로 빼서 콜라주를 해제합니다.")
                    collage._held_image_drag(page, first, second, .75, None, target_y_fraction=.98)
                    def split_ready():
                        return frame.locator('img').evaluate_all("nodes => nodes.length===2 && nodes.every(n=>n.closest('figure')?.getAttribute('data-ke-type')==='image') && nodes[0].closest('figure')!==nodes[1].closest('figure')")
                    collage._wait(page, split_ready, None, 6, "콜라주 해제 결과를 확인하지 못했습니다.")
                    report("LIVE_SPLIT_OK: 두 사진이 개별 이미지로 분리된 것을 확인했습니다.")
            except Exception as exc:
                report("LIVE_COLLAGE_FAILED: " + str(exc))
                # Show only structural diagnostics, never cookies or image URLs.
                report(json.dumps(frame.locator('img').evaluate_all("nodes => nodes.map(n=>({tag:n.tagName,draggable:n.draggable,parent:n.parentElement.tagName,type:n.closest('figure')?.getAttribute('data-ke-type')}))"), ensure_ascii=False))
            page.bring_to_front()
            report("검토용으로 창을 유지합니다. 확인 후 테스트 Chrome 창을 닫아 주세요.")
            deadline = time.monotonic() + max(30, min(args.hold_seconds, 1200))
            while not page.is_closed() and browser.is_connected() and time.monotonic() < deadline:
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    break
            browser.close()


if __name__ == "__main__":
    run()
