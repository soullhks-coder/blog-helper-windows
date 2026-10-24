"""Actual Chrome/Google-shaped iframes with every request intercepted offline."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

from PIL import Image


EDITOR = r'''<!doctype html><meta charset="utf-8">
<iframe class="editable" srcdoc="<p>BODY_KEEP_원문</p>"></iframe>
<button id="image" onclick="document.querySelector('#menu').hidden=false">이미지 삽입</button>
<div id="menu" hidden><div id="upload" role="menuitem" tabindex="0"
 style="width:220px;height:40px"><span>컴퓨터에서 업로드</span></div></div>
<script>
const options = OPTIONS;
window.requests = []; window.transfers = []; window.rejected = [];
let hoverAt=0, downAt=0;
function picker() {
  const frame = document.createElement('iframe'); frame.id='picker';
  frame.src='https://docs.google.com/picker?delay='+(options.browseDelay||0)+'&ignore='+Number(!!options.ignoreBrowse);
  document.body.appendChild(frame);
}
function open(method) {
  requests.push(method); document.querySelector('#menu').hidden=true;
  if (options.guardedAfter !== undefined && transfers.length >= options.guardedAfter
      && (method!=='pointer' || performance.now()-hoverAt<150 || performance.now()-downAt<100)) {
    rejected.push(method); return;
  }
  if (options.never || (options.swallow||[]).includes(requests.length)) return;
  if (options.frameDelay) setTimeout(picker, options.frameDelay); else picker();
}
document.querySelector('#upload').addEventListener('keydown', e=>{
  if(e.key==='Enter') {e.preventDefault();open('keyboard');}
});
document.querySelector('#upload').addEventListener('click', ()=>open('pointer'));
document.querySelector('#upload').addEventListener('mouseenter', ()=>hoverAt=performance.now());
document.querySelector('#upload').addEventListener('mousedown', ()=>downAt=performance.now());
document.querySelector('#image').addEventListener('click', ()=>{
  if (!options.overlay) return;
  const block=document.createElement('div');
  block.style='position:fixed;inset:0;background:transparent;z-index:9999';
  document.body.appendChild(block);setTimeout(()=>block.remove(),350);
});
document.addEventListener('keydown', e=>{
  if(e.key==='Escape') {document.querySelector('#menu').hidden=true;document.querySelector('#picker')?.remove();}
});
window.addEventListener('message', e=>{
  if(e.data?.type!=='fixture-files')return;
  transfers.push(e.data.files);document.querySelector('#picker')?.remove();
});
if(options.hidden) {
  const stale=document.createElement('iframe');stale.src='https://docs.google.com/picker?hidden=1';
  stale.style.display='none';document.body.appendChild(stale);
}
if(options.existing)picker();
</script>'''

PICKER = r'''<!doctype html><meta charset="utf-8">
<button id="browse" hidden onclick="choose(event)">찾아보기</button>
<input id="file" type="file" multiple hidden>
<script>
setTimeout(()=>document.querySelector('#browse').hidden=false, DELAY);
function choose(event) {
  if(IGNORE && event.detail>0)return;
  document.querySelector('#file').click();
}
document.querySelector('#file').addEventListener('change', e=>{
  parent.postMessage({type:'fixture-files',files:[...e.target.files].map(f=>f.name)}, '*');
});
</script>'''


def main():
    with tempfile.TemporaryDirectory(prefix='blog-helper-picker-ui-') as directory:
        os.environ['BLOG_HELPER_DATA_DIR'] = directory
        os.environ['BLOG_HELPER_DISABLE_UPDATES'] = '1'
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as app
        from blogspot_upload import BlogspotImageUploadError, open_image_picker, select_upload_files
        from blogspot_recovery import save_failed_editor, wait_for_failed_editor
        from writing_run import WritingCancelled, check_writing_cancelled
        from playwright.sync_api import sync_playwright
        from urllib.parse import parse_qs, urlparse

        image_path = Path(directory) / 'fixture.png'
        Image.new('RGB', (60, 60), 'green').save(image_path)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(app.require_google_chrome_executable()),
                headless=True, args=['--disable-background-networking', '--disable-component-update'])
            try:
                page = browser.new_page()
                print('Blogger offline Chrome version:', browser.version, flush=True)
                page.on('console', lambda message: print('fixture console:', message.type, message.text, flush=True)
                    if message.type == 'error' else None)
                scenario = {}
                def respond(route):
                    url = route.request.url
                    if url == 'http://127.0.0.1/blogger-fixture':
                        route.fulfill(body=EDITOR.replace('OPTIONS', json.dumps(scenario)), content_type='text/html')
                    elif url.startswith('https://docs.google.com/picker?'):
                        delay = int(parse_qs(urlparse(url).query).get('delay', [0])[0])
                        ignore = int(parse_qs(urlparse(url).query).get('ignore', [0])[0])
                        route.fulfill(body=PICKER.replace('DELAY', str(delay)).replace('IGNORE', str(ignore)), content_type='text/html')
                    else:
                        route.abort()
                page.route('**/*', respond)

                def open_picker(*, wait=1.5, check=lambda: None):
                    return open_image_picker(page, find_image_button=lambda p: p.get_by_role('button', name='이미지 삽입'),
                        describe_state=lambda p: 'offline fixture', log=lambda message: None,
                        check_cancel=check, wait_seconds=wait)

                cases = (
                    ('normal', {}, 1), ('swallowed-pointer', {'swallow':[1]}, 2),
                    ('late-frame', {'frameDelay':350}, 1), ('late-browse', {'browseDelay':350}, 1),
                    ('hidden-stale-frame', {'hidden':True}, 1), ('existing-picker', {'existing':True}, 0),
                    ('swallowed-browse', {'ignoreBrowse':True}, 1),
                    ('hover-and-held-pointer', {'guardedAfter':0, 'overlay':True}, 1),
                )
                for name, options, requests in cases:
                    scenario.clear(); scenario.update(options)
                    page.goto('http://127.0.0.1/blogger-fixture')
                    frame, browse = open_picker()
                    print(f'Blogger offline Chrome: starting {name}', flush=True)
                    select_upload_files(page, browse, [str(image_path)], log=lambda message: print(
                        'Blogger offline Chrome: '+str(message).encode('ascii', 'backslashreplace').decode(), flush=True),
                        check_cancel=lambda: None)
                    page.wait_for_function('transfers.length===1')
                    assert len(page.evaluate('requests')) == requests, (name, page.evaluate('requests'))
                    assert page.evaluate('transfers') == [['fixture.png']], name
                    assert page.frame_locator('iframe.editable').locator('body').inner_text() == 'BODY_KEEP_원문'
                    print(f'Blogger offline Chrome: {name}; one transfer, original body preserved', flush=True)

                scenario.clear(); scenario.update({'swallow':[3], 'guardedAfter':2, 'overlay':True})
                page.goto('http://127.0.0.1/blogger-fixture')
                for index in range(3):
                    _, browse = open_picker()
                    select_upload_files(page, browse, [str(image_path)], log=lambda message: None, check_cancel=lambda: None)
                    page.wait_for_function(f'transfers.length==={index+1}')
                assert page.evaluate('requests') == ['pointer']*4
                assert page.evaluate('transfers') == [['fixture.png']]*3
                assert page.evaluate('rejected') == []
                print('Blogger offline Chrome: swallowed third open recovered; first two files not retransmitted', flush=True)

                scenario.clear(); scenario.update({'never':True})
                page.goto('http://127.0.0.1/blogger-fixture')
                try:
                    open_picker(wait=.4)
                except BlogspotImageUploadError:
                    pass
                else:
                    raise AssertionError('Unopened picker was accepted')
                assert len(page.evaluate('requests')) == 3 and page.evaluate('transfers') == []
                print('Blogger offline Chrome: permanent failure stopped after three pre-transfer attempts', flush=True)

                page.goto('http://127.0.0.1/blogger-fixture')
                page.wait_for_function('window.requests !== undefined')
                from threading import Event
                cancel = Event()
                def stop_after_activation():
                    if page.evaluate('requests.length'):
                        cancel.set()
                    check_writing_cancelled(cancel)
                try:
                    open_picker(check=stop_after_activation)
                except WritingCancelled:
                    pass
                else:
                    raise AssertionError('Stop was ignored')
                assert len(page.evaluate('requests')) == 1 and page.evaluate('transfers') == []
                print('Blogger offline Chrome: cancellation prevents another attempt or transfer', flush=True)

                folder = save_failed_editor(page, Path(directory)/'recovery', title='제목', article_html='<p>원문</p>',
                    image_paths=[str(image_path)], blog_id='fixture', profile_scope='fixture', completed_images=2)
                payload = json.loads((folder/'recovery.json').read_text(encoding='utf-8'))
                assert 'BODY_KEEP_원문' in payload['editor_html']
                assert (folder/payload['images'][0]).read_bytes() == image_path.read_bytes()
                page.on('console', lambda message: page.close() if message.text == 'fixture-close' else None)
                page.evaluate("setTimeout(()=>console.log('fixture-close'), 250)")
                wait_for_failed_editor(page, check_cancel=lambda: None)
                assert page.is_closed()
                print('Blogger offline Chrome: live HTML/images backed up; worker waited for close without saving/publishing', flush=True)
            finally:
                browser.close()


if __name__ == '__main__':
    main()
