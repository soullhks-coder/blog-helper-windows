"""Real Chrome/iframe resize timing against localhost only; never a real blog."""
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from PIL import Image


FIXTURE = """<!doctype html><meta charset="utf-8">
<iframe class="editable" style="width:900px;height:750px"></iframe>
<button id="center" onclick="record('center')">가운데 정렬</button>
<button onclick="document.querySelector('#choices').hidden=false;record('size-menu')">이미지 크기</button>
<div id="choices" hidden><button onclick="choose()">매우 크게</button></div>
<div id="dialog" role="dialog" hidden><h2>이미지 수정</h2>
<button role="radio" aria-checked="false" onclick="choose(true)">아주 크게</button>
<button onclick="record('update');document.querySelector('#dialog').hidden=true;apply()">업데이트</button></div>
<button onclick="record('next')">다음 작업</button>
<script>
const options = OPTIONS;
window.events = [];
function record(action) { events.push({action, time:performance.now()}); }
function target() {return document.querySelector('iframe').contentDocument.querySelector('#target');}
function resize(width, height) {
    const img = target(); img.width=width; img.height=height; record('applied-'+width+'x'+height);
}
function apply() {
    if (options.partial) {
        resize(400,400); setTimeout(()=>resize(640,640), options.delay);
    } else if (options.widthFirst) {
        resize(640,320); setTimeout(()=>resize(640,640), options.delay);
    } else if (options.unchanged) {
        record('ignored');
    } else setTimeout(()=>{
        if (options.replace) {const old=target();old.replaceWith(old.cloneNode(true));record('replace');}
        resize(options.targetWidth||640, options.targetHeight||640);
        if (options.loading) {
            target().src='/image.png?delay=2&reload=1';
            target().addEventListener('load', ()=>record('loaded'), {once:true});
        }
    }, options.delay||0);
}
function choose(dialog=false) {
    record('choice');document.querySelector('#choices').hidden=true;
    if (dialog) document.querySelector('[role=radio]').setAttribute('aria-checked','true');
    else apply();
}
// Realistic popup rows: child text, a hover-driven active state, held pointer
// input and an occasionally swallowed first activation.
window.menuAttempts=0;
if (options.nestedMenu) {
    const menu=document.querySelector('#choices');
    menu.setAttribute('role','menu');
    menu.innerHTML=`<div role="menuitemradio" aria-checked="true">중간</div>
        <div id="extra-large" role="menuitemradio" aria-checked="false"
        style="position:relative;width:180px;height:40px;background:#eee"
        onmouseenter="this.dataset.hover=performance.now()"
        onmousedown="this.dataset.down=performance.now();record('menu-down')"
        onclick="menuClick(event,this)"><span style="position:absolute;left:40px;top:10px">매우 크게</span></div>`;
}
function menuClick(event,row) {
    menuAttempts++;
    if ((options.parentTargetOnly && event.target!==row)
        || (options.hoverDelay && performance.now()-Number(row.dataset.hover)<options.hoverDelay)
        || (options.heldPress && performance.now()-Number(row.dataset.down)<100)
        || menuAttempts<=(options.ignoreClicks||0)) {record('ignored-choice');return;}
    row.setAttribute('aria-checked','true');choose();
}
document.querySelector('#dialog').hidden=!options.dialog;
const w=options.originalWidth||1024, h=options.originalHeight||1024;
const imageUrl='/image.png?w='+w+'&h='+h+'&target=1';
const original=options.noOriginal ? '' : `data-original-width="${w}" data-original-height="${h}"`;
const preceding=Array.from({length:options.existingImages||0}, (_,i)=>
    `<p><img src="/image.png?existing=${i}" width="640" height="640"></p>`).join('');
document.querySelector('iframe').srcdoc=preceding+`<img id="target" src="${imageUrl}" width="320" height="${options.initialHeight||320}" ${original}>`;
</script>"""


def main():
    with tempfile.TemporaryDirectory(prefix="blog-helper-blogspot-size-") as directory, ExitStack() as stack:
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as app
        import blogspot_image_layout as layout
        from playwright.sync_api import sync_playwright
        stack.enter_context(patch.object(app, "append_runtime_log"))

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                query = parse_qs(urlparse(self.path).query)
                if self.path.startswith("/image.png"):
                    threading.Event().wait(float(query.get("delay", [0])[0]))
                    width, height = int(query.get("w", [1024])[0]), int(query.get("h", [1024])[0])
                    buffer = io.BytesIO()
                    Image.new("RGB", (width, height), "#3485d5").save(buffer, "PNG")
                    body, mime = buffer.getvalue(), "image/png"
                else:
                    body, mime = b"<!doctype html><title>Local fixture</title>", "text/html"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=str(app.require_google_chrome_executable()),
                    headless=True, args=["--disable-background-networking", "--disable-component-update"])
                try:
                    page = browser.new_page(viewport={"width": 1200, "height": 1000})
                    page.set_default_timeout(5_000)
                    page.route("**/*", lambda route: route.continue_()
                               if route.request.url.startswith(origin + "/") else route.abort())
                    cases = (
                        ("slow-third-image", {"delay": 10_500, "existingImages": 2}, 10_500),
                        ("portrait-height-only", {"originalWidth": 512, "originalHeight": 1024,
                            "targetWidth": 320, "targetHeight": 640, "delay": 200}, 0),
                        ("small-original", {"originalWidth": 320, "originalHeight": 200,
                            "initialHeight": 200, "unchanged": True, "noOriginal": True}, 0),
                        ("loading-after-dimensions", {"delay": 0, "loading": True}, 2_000),
                        ("replaced-image-node", {"delay": 200, "replace": True}, 0),
                        ("intermediate-growth", {"delay": 3_000, "partial": True}, 3_000),
                        ("height-still-pending", {"delay": 3_000, "widthFirst": True}, 3_000),
                        ("edit-dialog-update", {"delay": 200, "dialog": True}, 0),
                        ("nested-hover-menu-row", {"nestedMenu": True, "parentTargetOnly": True,
                            "hoverDelay": 150, "heldPress": True}, 0),
                        ("ignored-first-menu-click", {"nestedMenu": True, "ignoreClicks": 1}, 0),
                    )
                    for name, options, minimum in cases:
                        page.goto(origin)
                        page.set_content(FIXTURE.replace("OPTIONS", json.dumps(options)))
                        image = page.frame_locator("iframe.editable").locator("img#target")
                        image.wait_for()
                        image.evaluate("el => el.decode()")
                        source = image.get_attribute("src")
                        app.configure_blogspot_inserted_image(page, source, original_size=(
                            options.get("originalWidth", 1024), options.get("originalHeight", 1024)))
                        page.get_by_role("button", name="다음 작업", exact=True).click()
                        events = page.evaluate("events")
                        choice = next(item for item in events if item["action"] == "choice")
                        next_action = next(item for item in events if item["action"] == "next")
                        assert next_action["time"] - choice["time"] >= max(1_500, minimum), (name, events)
                        after = app._blogspot_image_size_snapshot(image)
                        assert layout.verified_size({"originalWidth": options.get("originalWidth", 1024),
                            "originalHeight": options.get("originalHeight", 1024)}, after), (name, after)
                        if options.get("existingImages"):
                            previous = page.frame_locator("iframe.editable").locator("img:not(#target)")
                            assert previous.count() == 2
                            assert previous.evaluate_all("images => images.every(img => img.width === 640 && img.height === 640)")
                        if options.get("dialog"):
                            update = next(item for item in events if item["action"] == "update")
                            assert update["time"] - choice["time"] >= 1_500, events
                            assert next_action["time"] - update["time"] >= 750, events
                        else:
                            assert not [item for item in events if choice["time"] < item["time"] < next_action["time"]
                                        and item["action"] in ("center", "size-menu", "update")], events
                        if options.get("nestedMenu"):
                            assert page.evaluate("menuAttempts") == 1 + options.get("ignoreClicks", 0), events
                        print("Blogger local Chrome:", name, "passed", flush=True)
                    page.goto(origin)
                    page.set_content(FIXTURE.replace("OPTIONS", '{"nestedMenu":true,"ignoreClicks":99}'))
                    image = page.frame_locator("iframe.editable").locator("img#target")
                    image.wait_for()
                    image.evaluate("el => el.decode()")
                    try:
                        app.configure_blogspot_inserted_image(page, image.get_attribute("src"), original_size=(1024,1024))
                    except RuntimeError as exc:
                        assert "선택이 접수되지" in str(exc), str(exc)
                    else:
                        raise AssertionError("An unselected open menu unexpectedly continued")
                    assert page.evaluate("menuAttempts") == 2
                    assert not [item for item in page.evaluate("events") if item["action"] == "next"]
                    print("Blogger local Chrome: two rejected menu clicks preserve the draft and stop", flush=True)
                    # A swallowed size click must still stop the pipeline, not
                    # silently continue. Accelerate this negative fixture only.
                    page.goto(origin)
                    page.set_content(FIXTURE.replace("OPTIONS", '{"unchanged":true}'))
                    image = page.frame_locator("iframe.editable").locator("img#target")
                    image.wait_for()
                    image.evaluate("el => el.decode()")
                    with patch.object(layout, "SIZE_TIMEOUT_MS", 1_000):
                        try:
                            app.configure_blogspot_inserted_image(page, image.get_attribute("src"), original_size=(1024, 1024))
                        except RuntimeError as exc:
                            assert "반영되지 않아 다음 작업" in str(exc)
                        else:
                            raise AssertionError("Unapplied resize unexpectedly continued")
                    assert not [item for item in page.evaluate("events") if item["action"] == "next"]
                    print("Blogger local Chrome: unapplied resize blocks next action; no real post edited/published", flush=True)
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
