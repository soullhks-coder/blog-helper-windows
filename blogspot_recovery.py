"""Local-only snapshots of incomplete Blogger work; no save/publish clicks."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
import uuid


def save_failed_editor(page, directory, *, title, article_html, image_paths,
                       blog_id, profile_scope, completed_images):
    root = Path(directory) / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    root.mkdir(parents=True)
    editor_html = ""
    try:
        html_editor = page.locator(".CodeMirror:visible")
        if html_editor.count():
            editor_html = html_editor.first.evaluate("element => element.CodeMirror.getValue()")
        elif page.locator("iframe.editable:visible").count():
            editor_html = page.frame_locator("iframe.editable:visible").locator("body").inner_html(timeout=2_000)
    except Exception:
        # The app's original article is still available if Chrome disconnected.
        pass
    copies = []
    for index, path in enumerate(image_paths, 1):
        source = Path(path)
        if source.is_file():
            target = root / f"image-{index:02d}{source.suffix.lower()}"
            shutil.copy2(source, target)
            copies.append(target.name)
    manifest = root / "recovery.json"
    manifest.write_text(json.dumps({
        "format": 1, "status": "incomplete_not_auto_published", "title": title,
        "blog_id": blog_id, "profile_scope": profile_scope,
        "article_html": article_html, "editor_html": editor_html,
        "completed_images": completed_images, "images": copies,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def wait_for_failed_editor(page, *, check_cancel):
    """The worker retains profile ownership until the user closes/stops it."""
    check_cancel()
    if page.is_closed():
        return
    page.bring_to_front()
    while not page.is_closed():
        check_cancel()
        try:
            page.wait_for_timeout(400)
        except Exception:
            check_cancel()
            break
