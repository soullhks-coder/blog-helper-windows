"""Exercise actual HTTP/file-chooser payloads against localhost, never a blog."""
from __future__ import annotations

from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
from unittest.mock import patch


def main():
    with tempfile.TemporaryDirectory(prefix="blog-helper-webp-check-") as directory:
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as app
        from PIL import Image
        from playwright.sync_api import sync_playwright

        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                body = b'''<!doctype html><button id="choose" onclick="files.click()">Choose</button>
                <input id="files" type="file" multiple hidden onchange="send(this, '/picker/upload')">
                <button id="mceu_0-open">Image</button>
                <button id="attach-image" onclick="native.click()">Photo</button>
                <input id="native" type="file" hidden onchange="send(this, '/manage/post/attach.json')">
                <script>
                window.uploadDone = false;
                async function send(input, url) {
                    window.fileInfo = [...input.files].map(f => ({name:f.name,type:f.type}));
                    const data = new FormData();
                    [...input.files].forEach(f => data.append('image', f));
                    await fetch(url, {method:'POST',body:data});
                    window.uploadDone = true;
                }
                </script>'''
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                data = self.rfile.read(int(self.headers["Content-Length"]))
                mime = self.headers["Content-Type"]
                if mime.startswith("multipart/"):
                    message = BytesParser(policy=policy.default).parsebytes(
                        b"Content-Type: " + mime.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + data)
                    parts = [{"name": part.get_filename(), "type": part.get_content_type(),
                              "data": part.get_payload(decode=True)}
                             for part in message.walk() if part.get_filename()]
                else:
                    parts = [{"name": self.headers.get("Content-Disposition", ""), "type": mime, "data": data}]
                received.append((self.path, parts))
                suffix = ".webp" if parts[0]["type"] == "image/webp" else ".png"
                result = {"id": 42, "source_url": "http://localhost/image.webp",
                          "url": "https://blog.kakaocdn.net/dna/fixture/img" + suffix}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            paths = [Path(directory) / "thumbnail.png", Path(directory) / "body.jpg"]
            Image.new("RGBA", (120, 80), (10, 50, 90, 80)).save(paths[0])
            Image.new("RGB", (90, 60), "red").save(paths[1])
            originals = [path.read_bytes() for path in paths]
            client = app.WordPressClient(app.WordPressSettings(blog_url=origin, username="fixture", app_password="fixture"))
            for path in paths:
                assert client.upload_media(path, "thumbnail/body")['id'] == 42
                route, parts = received[-1]
                assert route == "/wp-json/wp/v2/media"
                assert parts[0]["type"] == "image/webp" and parts[0]["name"].endswith('.webp"')
                with Image.open(io.BytesIO(parts[0]["data"])) as image:
                    assert image.format == "WEBP"
            print("WordPress: real HTTP headers, .webp filenames and WebP binary payloads passed", flush=True)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=str(app.require_google_chrome_executable()), headless=True)
                try:
                    page = browser.new_page()
                    staged_paths = []
                    def picker_upload(page, converted, _events):
                        staged_paths.extend(map(Path, converted))
                        page.goto(origin)
                        with page.expect_file_chooser() as chooser:
                            page.click("#choose")
                        chooser.value.set_files(converted)
                        page.wait_for_function("window.uploadDone === true")
                        assert all(info['type'] == 'image/webp' and info['name'].endswith('.webp')
                                   for info in page.evaluate("window.fileInfo"))
                        assert received[-1][0] == "/picker/upload"
                        assert len(received[-1][1]) == len(converted)
                        for part in received[-1][1]:
                            assert part['type'] == 'image/webp'
                            with Image.open(io.BytesIO(part['data'])) as image:
                                assert image.format == 'WEBP'
                        return len(converted)
                    with patch.object(app, "_upload_blogspot_webp_files", side_effect=picker_upload):
                        assert app.upload_blogspot_images(page, list(map(str, paths)), queue.Queue()) == 2
                    assert all(not path.exists() for path in staged_paths)
                    print("Blogger: native Chrome chooser, multipart MIME/bytes, ordered files and cleanup passed", flush=True)

                    for enabled in (False, True):
                        page.goto(origin)
                        result = app.upload_tistory_image_file(page, str(paths[0]), queue.Queue(), upload_webp=enabled)
                        assert result.endswith('.webp' if enabled else '.png')
                        route, parts = received[-1]
                        assert route == "/manage/post/attach.json"
                        assert parts[0]['type'] == ('image/webp' if enabled else 'image/png')
                        with Image.open(io.BytesIO(parts[0]['data'])) as image:
                            assert image.format == ('WEBP' if enabled else 'PNG')
                    print("Tistory: native chooser/HTTP payload changes with ON/OFF; original PNG preserved", flush=True)
                finally:
                    browser.close()
            assert [path.read_bytes() for path in paths] == originals
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)


if __name__ == "__main__":
    main()
