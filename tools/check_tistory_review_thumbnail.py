"""Exercise native representative replacement on offline Chrome; never publish."""
import base64
import hashlib
import io
import os
from pathlib import Path
import queue
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix='blog-helper-review-thumbnail-') as directory:
        root = Path(directory)
        (root / 'app_state.json').write_text('{}', encoding='utf-8')
        os.environ['BLOG_HELPER_DATA_DIR'] = directory
        os.environ['BLOG_HELPER_DISABLE_UPDATES'] = '1'
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as api
        from PIL import Image
        from playwright.sync_api import sync_playwright

        source = root / 'current-liver.png'
        Image.new('RGB', (80, 80), '#16a34a').save(source)
        old = io.BytesIO()
        Image.new('RGB', (80, 80), '#dc2626').save(old, format='PNG')
        old_src = 'data:image/png;base64,' + base64.b64encode(old.getvalue()).decode('ascii')
        html = '''<style>.sheet{display:none;position:fixed;left:200px;top:100px;width:400px;height:260px;background:white}
            .box_thumb img{width:120px;height:120px}button{width:150px;height:40px}</style>
            <h1>Current liver article</h1><p id="body">Current article body</p>
            <button onclick="document.querySelector('.sheet').style.display='block'">완료</button>
            <div class="ReactModal__Content--after-open sheet"><div class="box_thumb">
            <img id="preview" src="OLD_IMAGE"><button class="ico_delete" onclick="document.getElementById('preview')?.remove()">삭제</button>
            <input type="file" accept="image/*" onchange="const f=this.files[0]; if(!f)return;
              window.selectedName=f.name;const reader=new FileReader();reader.onload=()=>{
                let im=document.getElementById('preview');if(!im){im=document.createElement('img');im.id='preview';this.parentNode.prepend(im)}
                im.src=reader.result;window.selectedData=reader.result;};reader.readAsDataURL(f)">
            </div><button id="publish-btn" onclick="window.publishClicks++">공개 발행</button></div>
            <script>window.publishClicks=0;window.selectedName=undefined;window.selectedData=undefined</script>'''.replace('OLD_IMAGE', old_src)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(api.require_google_chrome_executable()), headless=True)
            try:
                page = browser.new_page(viewport={'width': 1000, 'height': 800})
                page.route('**/*', lambda route: route.abort())
                for webp in (False, True):
                    page.set_content(html)
                    api.prepare_tistory_publish_thumbnail(page, str(source), queue.Queue(), upload_webp=webp)
                    assert page.locator('#publish-btn').is_visible()
                    assert page.evaluate('window.publishClicks') == 0
                    assert page.locator('#body').inner_text() == 'Current article body'
                    selected = page.evaluate('window.selectedData')
                    actual = base64.b64decode(selected.split(',', 1)[1])
                    expected = api.encode_webp_upload(source) if webp else source.read_bytes()
                    assert hashlib.sha256(actual).digest() == hashlib.sha256(expected).digest()
                    assert page.evaluate('window.selectedName') == source.with_suffix('.webp' if webp else '.png').name
                    print(f'Offline review thumbnail: {"WebP" if webp else "PNG"} replaced previous article; current bytes verified; zero publish clicks', flush=True)
                page.set_content(html)
                page.locator('.ico_delete').evaluate("node => node.style.display='none'")
                try:
                    api.prepare_tistory_publish_thumbnail(page, str(source), queue.Queue())
                except RuntimeError as error:
                    assert '대표이미지' in str(error)
                else:
                    raise AssertionError('Unremovable previous representative was accepted')
                assert page.evaluate('window.publishClicks') == 0
                assert page.evaluate('window.selectedName === undefined')
                print('Offline review thumbnail: blocked stale representative stops safely; zero file transfers/publications', flush=True)
            finally:
                browser.close()


if __name__ == '__main__':
    main()
