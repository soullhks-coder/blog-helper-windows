import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from tistory_v2 import Cancelled
import tistory_v2_editor as native_editor
from tistory_v2_editor import insert_native_slide_pairs


class TistoryV2EditorTests(unittest.TestCase):
    def test_mixed_dimensions_validate_against_each_slide_before_touching_editor(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths, sizes = [], [[600, 300], [900, 600], [600, 900], [1200, 600], [300, 300]] * 2
            for i, size in enumerate(sizes):
                path = Path(temporary) / f"slide-{i}.png"
                Image.new("RGB", size, "white").save(path)
                paths.append(str(path))
            page, upload = MagicMock(), MagicMock()
            page.evaluate.side_effect = RuntimeError("validation passed")
            with self.assertRaisesRegex(RuntimeError, "validation passed"):
                insert_native_slide_pairs(page, paths, "", upload, expected_sizes=sizes)
            page.evaluate.assert_called_once()
            page.reset_mock()
            with self.assertRaises(ValueError):
                insert_native_slide_pairs(page, paths, "", upload, expected_sizes=sizes[:9])
            Image.new("RGB", (600, 300), "white").save(paths[2])
            with self.assertRaisesRegex(ValueError, "600×900"):
                insert_native_slide_pairs(page, paths, "", upload, expected_sizes=sizes)
            page.evaluate.assert_not_called()
            upload.assert_not_called()

    def test_selected_dimensions_are_accepted_and_mixed_sizes_stop_before_upload(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = []
            for i in range(10):
                path = Path(temporary) / f"slide-{i}.png"
                Image.new("RGB", (900, 600), "white").save(path)
                paths.append(str(path))
            page, upload = MagicMock(), MagicMock()
            page.evaluate.side_effect = RuntimeError("validation passed")
            with self.assertRaisesRegex(RuntimeError, "validation passed"):
                insert_native_slide_pairs(page, paths, "", upload, expected_size=(900, 600))
            page.evaluate.assert_called_once()
            upload.assert_not_called()
            page.reset_mock()
            Image.new("RGB", (600, 300), "white").save(paths[-1])
            with self.assertRaisesRegex(ValueError, "900×600"):
                insert_native_slide_pairs(page, paths, "", upload, expected_size=(900, 600))
            page.evaluate.assert_not_called()
            upload.assert_not_called()
            with self.assertRaises(ValueError):
                insert_native_slide_pairs(page, paths, "", upload, expected_size=(99999, 600))
            page.evaluate.assert_not_called()

    def test_failed_editor_is_kept_open_until_user_closes_and_never_saves(self):
        page = MagicMock()
        page.is_closed.side_effect = [False, False, False, True]
        native_editor.wait_for_failed_editor(page)
        page.bring_to_front.assert_called_once()
        self.assertEqual(page.wait_for_timeout.call_count, 2)
        page.evaluate.assert_not_called()
        page.close.assert_not_called()

    def test_already_closed_failed_editor_is_not_reopened(self):
        page = MagicMock()
        page.is_closed.return_value = True
        native_editor.wait_for_failed_editor(page)
        page.bring_to_front.assert_not_called()

    def test_held_drag_always_moves_second_to_first_and_releases(self):
        page, first, second = MagicMock(), MagicMock(), MagicMock()
        first.bounding_box.return_value = {"x": 20, "y": 30, "width": 300, "height": 150}
        second.bounding_box.return_value = {"x": 340, "y": 30, "width": 300, "height": 150}
        native_editor._held_image_drag(page, first, second, .74, None)
        moves = page.mouse.move.call_args_list
        self.assertEqual(moves[0].args, (490, 105))  # right-hand second image
        self.assertEqual(moves[-1].args, (242, 105))  # left-hand first image
        self.assertEqual(moves[2].kwargs["steps"], 18)
        page.wait_for_timeout.assert_any_call(650)
        page.mouse.up.assert_called_once()

    def test_cancel_during_held_drag_releases_mouse(self):
        page, first, second = MagicMock(), MagicMock(), MagicMock()
        second.bounding_box.return_value = {"x": 30, "y": 20, "width": 600, "height": 300}
        cancel = threading.Event()
        page.wait_for_timeout.side_effect = lambda _: cancel.set()
        with self.assertRaises(Cancelled):
            native_editor._held_image_drag(page, first, second, .74, cancel)
        page.mouse.up.assert_called_once()

    def test_gesture_timeout_is_retried_and_cancel_is_not_swallowed(self):
        page, frame = MagicMock(), MagicMock()
        page.evaluate.return_value = False
        with patch.object(native_editor, "_mark_upload"), patch.object(native_editor, "_wait"), \
             patch.object(native_editor, "_held_image_drag", side_effect=[TimeoutError("pointer blocked"), None]) as drag:
            native_editor._drag_pair(page, frame, "run", 0, ["first", "second"], None)
            self.assertEqual(drag.call_count, 2)
        with patch.object(native_editor, "_mark_upload"), patch.object(native_editor, "_wait"), \
             patch.object(native_editor, "_held_image_drag", side_effect=Cancelled()) as drag:
            with self.assertRaises(Cancelled):
                native_editor._drag_pair(page, frame, "run", 0, ["first", "second"], None)
            self.assertEqual(drag.call_count, 1)

    def test_all_assets_are_validated_before_touching_editor(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = []
            for i in range(10):
                path = Path(temporary) / f"slide-{i}.png"
                Image.new("RGB", (600, 300), "white").save(path)
                paths.append(str(path))
            page, upload = MagicMock(), MagicMock()
            with self.assertRaisesRegex(ValueError, "10장"):
                insert_native_slide_pairs(page, paths[:9], "", upload)
            with self.assertRaisesRegex(ValueError, "중복"):
                insert_native_slide_pairs(page, [paths[0]] * 10, "", upload)
            Image.new("RGB", (600, 301), "white").save(paths[-1])
            with self.assertRaisesRegex(ValueError, "600×300"):
                insert_native_slide_pairs(page, paths, "", upload)
            page.evaluate.assert_not_called()
            upload.assert_not_called()

    def test_cancel_does_not_start_upload_or_change_body(self):
        page, upload = MagicMock(), MagicMock()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            insert_native_slide_pairs(page, [], "", upload, cancel=cancel)
        page.evaluate.assert_not_called()
        upload.assert_not_called()


_EDITOR_FIXTURE = """<!doctype html><html><meta charset="utf-8">
<style>body{margin:24px;width:700px}figure{margin:10px 0}img{width:600px;height:300px;display:block}
figure.imagegrid{display:flex;width:620px}figure.imagegrid img{width:300px;height:150px}
</style><body contenteditable="true"></body><script>
const body=document.body;
const article=document.createElement('div');body.appendChild(article);
for(let i=1;i<=10;i++)article.insertAdjacentHTML('beforeend',`<h2>소제목 ${i}</h2><p>원문 설명 ${i}를 유지합니다.</p>`);
window.tinymce={activeEditor:{getBody:()=>body,focus:()=>body.focus(),selection:{setRng:r=>{
  const s=getSelection();s.removeAllRanges();s.addRange(r);}},nodeChanged:()=>{},setDirty:()=>{},fire:()=>{}}};
let dragged=null;window.realDragCount=0;window.publishCount=0;
body.addEventListener('dragstart',event=>{if(event.target.tagName==='IMG'){
  dragged=event.target;event.dataTransfer.setData('text/plain',dragged.src);}});
body.addEventListener('dragover',event=>{if(event.target.tagName==='IMG')event.preventDefault();});
body.addEventListener('drop',event=>{if(event.target.tagName!=='IMG'||!dragged||dragged===event.target)return;
  event.preventDefault();window.realDragCount++;
  const first=event.target,second=dragged,old=second.closest('figure'),grid=first.closest('figure');
  grid.className='imagegrid';grid.dataset.keType='imageGrid';grid.appendChild(second);old.remove();dragged=null;});
window.attachFile=url=>{
  const figure=document.createElement('figure');figure.className='imageblock';figure.dataset.keType='image';
  const image=document.createElement('img');image.src=url;image.draggable=true;figure.appendChild(image);
  const range=getSelection().getRangeAt(0);let slot=range.startContainer;
  if(slot.nodeType===Node.TEXT_NODE)slot=slot.parentElement;
  if(slot.hasAttribute?.('data-blog-helper-v2-slot')){
    let anchor=slot;
    while(anchor.nextElementSibling?.tagName==='FIGURE'&&anchor.nextElementSibling.dataset.fixtureSlot===slot.dataset.blogHelperV2Slot)anchor=anchor.nextElementSibling;
    figure.dataset.fixtureSlot=slot.dataset.blogHelperV2Slot;anchor.after(figure);
  }else range.insertNode(figure);
  const next=document.createRange();next.setStartAfter(figure);next.collapse(true);
  const selection=getSelection();selection.removeAllRanges();selection.addRange(next);
};
</script></html>"""


# A hostile regression fixture, not a claim to reproduce Tistory's private UI:
# drag the native FIGURE through an overlay, emit multiple dragovers and dwell
# over its right-hand merge region before release. The old one-step drag fails.
_STRICT_FIXTURE = _EDITOR_FIXTURE.replace(
    "let dragged=null;window.realDragCount=0;window.publishCount=0;",
    "let dragged=null,entered=0,overs=0;window.realDragCount=0;window.publishCount=0;window.rejectedDrops=0;"
).replace(
    "if(event.target.tagName==='IMG'){\n  dragged=event.target;event.dataTransfer.setData('text/plain',dragged.src);}",
    "const figure=event.target.closest('figure');if(figure){dragged=figure.querySelector('img');entered=0;overs=0;event.dataTransfer.setData('text/plain',dragged.src);}"
).replace(
    "if(event.target.tagName==='IMG')event.preventDefault();",
    "const image=event.target.closest('figure')?.querySelector('img');if(image&&image!==dragged){event.preventDefault();overs++;if(!entered)entered=performance.now();}"
).replace(
    "if(event.target.tagName!=='IMG'||!dragged||dragged===event.target)return;\n  event.preventDefault();window.realDragCount++;\n  const first=event.target,second=dragged,old=second.closest('figure'),grid=first.closest('figure');",
    "const first=event.target.closest('figure')?.querySelector('img');if(!first||!dragged||dragged===first)return;"
    "event.preventDefault();const box=first.getBoundingClientRect();"
    "if(!event.isTrusted||overs<2||performance.now()-entered<400||event.clientX<box.x+box.width*.6){window.rejectedDrops++;dragged=null;return;}"
    "window.realDragCount++;const second=dragged,old=second.closest('figure'),grid=first.closest('figure');"
    "grid.querySelector('.photo-surface')?.remove();"
).replace(
    "image.src=url;image.draggable=true;figure.appendChild(image);",
    "image.src=url;image.draggable=false;image.style.pointerEvents='none';figure.draggable=true;figure.style.position='relative';figure.appendChild(image);"
    "const overlay=document.createElement('span');overlay.className='photo-surface';overlay.style='position:absolute;inset:0;display:block';figure.appendChild(overlay);"
)


def check_mixed_size_drag():
    """Exercise real pointer gestures offline with differently sized PNG pairs."""
    from playwright.sync_api import sync_playwright
    import main
    sizes = [(600, 300), (900, 600), (600, 900), (1200, 600), (300, 300),
             (900, 600), (900, 900), (600, 900), (1200, 300), (600, 300)]
    with tempfile.TemporaryDirectory(prefix="v2-mixed-size-drag-") as temporary, sync_playwright() as playwright:
        paths = []
        for index, size in enumerate(sizes):
            path = Path(temporary) / f"slide-{index+1:02d}.png"
            Image.new("RGB", size, (20 + index*15, 100, 180)).save(path)
            paths.append(str(path))
        browser = playwright.chromium.launch(executable_path=str(main.require_google_chrome_executable()), headless=True)
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            assets = {Path(path).name: Path(path).read_bytes() for path in paths}
            page.route("**/*", lambda route: route.fulfill(status=200, content_type="image/png", body=assets[route.request.url.rsplit("/", 1)[-1]]))
            page.set_content(_EDITOR_FIXTURE.replace("height:300px", "height:auto").replace("height:150px", "height:auto"))
            uploaded = []
            def upload(path):
                url = "https://blog.kakaocdn.net/offline-mixed/" + Path(path).name
                page.evaluate("url=>window.attachFile(url)", url)
                uploaded.append(Path(path).name)
                return url
            result = insert_native_slide_pairs(page, paths, "", upload, expected_sizes=sizes)
            assert result["ok"] and result["slide_count"] == 10 and result["collage_count"] == 5, result
            assert uploaded == [Path(path).name for path in paths]
            assert page.evaluate("window.realDragCount") == 5
            assert page.evaluate("window.publishCount") == 0
            print("OFFLINE mixed dimensions: 10 PNGs / five native drag collages / article preserved / no publication: PASS")
        finally:
            browser.close()


def check_real_editor_drag():
    """Real Chromium mouse/file-order check against an offline native-drag fixture.

    This verifies trusted drag/drop and result validation, not Tistory's current
    authenticated implementation. Run explicitly; unit discovery stays fast.
    """
    from playwright.sync_api import sync_playwright
    import main

    with tempfile.TemporaryDirectory(prefix="blog-helper-v2-editor-") as temporary, sync_playwright() as playwright:
        root = Path(temporary)
        paths = []
        for i in range(10):
            path = root / f"slide-{i + 1:02d}.png"
            Image.new("RGB", (600, 300), (20 + i * 15, 100, 180)).save(path)
            paths.append(str(path))
        thumbnail = root / "thumbnail.png"
        Image.new("RGB", (400, 400), "#7755cc").save(thumbnail)
        browser = playwright.chromium.launch(executable_path=str(main.require_google_chrome_executable()), headless=True)
        try:
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            response_image = io.BytesIO()
            Image.new("RGB", (600, 300), "#3399aa").save(response_image, "PNG")
            page.route("**/*", lambda route: route.fulfill(status=200, content_type="image/png", body=response_image.getvalue()))
            # Reproduce the real editor's whitespace-only reserialization after
            # uploading a thumbnail. Meaningful inline word spaces still matter.
            page.set_content('<body contenteditable="true"><h1>제목</h1>\n\n<p>첫 문단.</p>\n'
                             '<h2>소제목</h2>\n<p>꼭 <strong>확인</strong>합니다.</p>\n'
                             '<table><tr><td>성인</td><td>100</td></tr></table>\n'
                             '<figure><blockquote>원본 인용문</blockquote></figure></body>')
            read_body = '() => {' + native_editor._EDITOR + 'return originalText();}'
            compare_body = 'before => {' + native_editor._EDITOR + 'return bodyChange(before);}'
            before = page.evaluate(read_body)
            page.evaluate("""() => {
                const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT), blanks=[];
                while(walker.nextNode()) if(!walker.currentNode.textContent.trim()) blanks.push(walker.currentNode);
                blanks.forEach(node=>node.remove());
                document.body.insertAdjacentHTML('beforeend','<p data-blog-helper-v2-slot="empty"><br></p>' +
                    '<div data-mce-bogus="all">편집기 임시 UI</div><p>\\u200b\\ufeff</p>');
            }""")
            assert page.evaluate(compare_body, before) is None
            page.evaluate("document.querySelector('p').textContent='실제 다른 본문입니다.'")
            changed = page.evaluate(compare_body, before)
            assert changed and '실제 다른 본문' in changed['after_excerpt'], changed
            # A real word-boundary change must not be treated as indentation.
            page.evaluate("document.body.innerHTML='<p>꼭 <strong>확인</strong>합니다.</p>'")
            before_inline = page.evaluate(read_body)
            page.evaluate("document.querySelector('p').firstChild.textContent='꼭'")
            assert page.evaluate(compare_body, before_inline) is not None
            # A slot with authored text must not be excluded from the guard.
            page.evaluate("document.body.innerHTML='<p data-blog-helper-v2-slot=slot>보존할 실제 문장</p>'")
            before_slot = page.evaluate(read_body)
            page.evaluate("document.querySelector('p').textContent=''")
            assert page.evaluate(compare_body, before_slot) is not None
            print('Tistory V2 body guard: editor-only formatting tolerated; authored words/inline spaces/slot text protected')
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            page.route("**/*", lambda route: route.fulfill(status=200, content_type="image/png", body=response_image.getvalue()))
            page.set_content(_EDITOR_FIXTURE)
            uploaded = []

            def upload(path):
                url = "https://blog.kakaocdn.net/v2/" + Path(path).name
                page.evaluate("url=>window.attachFile(url)", url)
                uploaded.append(Path(path).name)
                return url

            result = insert_native_slide_pairs(page, paths, str(thumbnail), upload)
            assert result == {"ok": True, "slide_count": 10, "collage_count": 5, "thumbnail": True}, result
            assert uploaded == ["thumbnail.png", *[Path(p).name for p in paths]], uploaded
            assert page.evaluate("window.realDragCount") == 5
            assert page.locator("figure.imagegrid").count() == 5
            assert page.locator("body > figure.imageblock").count() == 1
            assert page.locator("h2").count() == 10
            assert page.evaluate("""()=>{
                const grid=document.querySelector('figure.imagegrid');
                const headings=document.querySelectorAll('h2');
                return (headings[1].compareDocumentPosition(grid)&Node.DOCUMENT_POSITION_FOLLOWING)
                  && (grid.compareDocumentPosition(headings[2])&Node.DOCUMENT_POSITION_FOLLOWING);
            }""")
            assert page.evaluate("window.publishCount") == 0
            # Repeating in an already-filled editor is rejected before uploader calls.
            try:
                insert_native_slide_pairs(page, paths, str(thumbnail), upload)
            except Exception as exc:
                assert "이미 넣었습니다" in str(exc), exc
            else:
                raise AssertionError("duplicate editor input was accepted")
            assert len(uploaded) == 11
            print("Tistory V2 offline Chromium: thumbnail +10 slides, five real drags/native grids, original text/order, duplicate guard passed")

            # Shared writing pipeline has already inserted its thumbnail in HTML.
            # The V2 hook must add only ten slides and preserve that thumbnail.
            page = browser.new_page(viewport={"width": 1100, "height": 900})
            page.route("**/*", lambda route: route.fulfill(status=200, content_type="image/png", body=response_image.getvalue()))
            page.set_content(_EDITOR_FIXTURE)
            page.evaluate("document.body.insertAdjacentHTML('afterbegin', '<p><img id=existing-thumbnail src=https://blog.kakaocdn.net/v2/existing-thumbnail.png></p>')")
            uploaded.clear()
            result = insert_native_slide_pairs(page, paths, "", upload)
            assert result["slide_count"] == 10 and result["collage_count"] == 5
            assert len(uploaded) == 10
            assert page.evaluate("document.querySelectorAll('img').length") == 11
            assert page.evaluate("document.querySelector('img').id") == "existing-thumbnail"
            assert page.evaluate("window.realDragCount") == 5
            print("Tistory V2 shared pipeline: existing thumbnail preserved, only10 new uploads, five native collages passed")

            # Real mouse events inside a scrolled iframe, with a non-draggable
            # IMG under a native draggable wrapper/overlay and delayed drop UI.
            page = browser.new_page(viewport={"width": 1100, "height": 820})
            page.route("**/*", lambda route: route.fulfill(status=200, content_type="image/png", body=response_image.getvalue()))
            def reset_iframe_fixture(content):
                # Replacing the iframe gives fresh lexical bindings AND keeps
                # its inherited same-origin relation to the parent editor page.
                page.set_content('<iframe id="editor-tistory_ifr" style="margin:30px;width:800px;height:650px"></iframe>')
                frame = page.frames[-1]
                frame.set_content(content)
                return frame

            editor_frame = reset_iframe_fixture(_STRICT_FIXTURE)
            native_uploaded = []

            def iframe_upload(path):
                url = "https://blog.kakaocdn.net/v2/" + Path(path).name
                editor_frame.evaluate("url=>window.attachFile(url)", url)
                native_uploaded.append(Path(path).name)
                return url

            # Prove that the old direct IMG drag does not pass this fixture.
            page.evaluate(native_editor._PREPARE, "old")
            page.evaluate(native_editor._SELECT_SLOT, "old-0")
            iframe_upload(paths[0])
            iframe_upload(paths[1])
            old_images = editor_frame.locator('figure.imageblock img')
            try:
                old_images.nth(1).drag_to(old_images.nth(0), timeout=1000)
            except Exception:
                pass
            finally:
                page.mouse.up()
            assert editor_frame.evaluate("window.realDragCount") == 0
            editor_frame = reset_iframe_fixture(_STRICT_FIXTURE)
            editor_frame.evaluate("""() => {
                window.dragTelemetry=[];
                for(const type of ['dragstart','dragenter','dragover','drop','dragend'])
                    document.addEventListener(type,e=>window.dragTelemetry.push({type,target:e.target.tagName,
                        x:e.clientX,y:e.clientY,time:Math.round(performance.now()),trusted:e.isTrusted}),true);
            }""")
            native_uploaded.clear()
            messages = []
            try:
                result = insert_native_slide_pairs(page, paths, "", iframe_upload, messages.append)
            except Exception:
                print("Strict fixture diagnostics:", editor_frame.evaluate("({events:window.dragTelemetry.slice(-8),rejected:window.rejectedDrops,grids:window.realDragCount})"))
                raise
            assert result["slide_count"] == 10 and result["collage_count"] == 5
            assert editor_frame.evaluate("window.realDragCount") == 5
            assert editor_frame.evaluate("window.rejectedDrops") == 0
            assert native_uploaded == [Path(path).name for path in paths]
            assert sum("묶인 것을 확인" in message for message in messages) == 5
            print("Tistory V2 strict iframe: wrapper/overlay drag, multiple trusted moves, hover dwell, five ordered collages passed")

            # A drop that never becomes a native grid must stop after the first
            # two attachments, not continue uploading or pretend it succeeded.
            editor_frame = reset_iframe_fixture(_STRICT_FIXTURE.replace("if(!event.isTrusted||overs<2", "if(true||!event.isTrusted||overs<2"))
            native_uploaded.clear()
            try:
                insert_native_slide_pairs(page, paths, "", iframe_upload)
            except RuntimeError as exc:
                assert "발행하지 않고 중단" in str(exc), exc
            else:
                raise AssertionError("Unmerged images were accepted as a collage")
            assert len(native_uploaded) == 2
            assert editor_frame.locator("figure.imagegrid").count() == 0
            assert editor_frame.locator("h2").count() == 10
            assert editor_frame.evaluate("window.publishCount") == 0
            print("Tistory V2 rejected-drop safety: bounded retry, only two uploads, no false collage or publish passed")
        finally:
            browser.close()


if __name__ == "__main__":
    import sys
    if "--mixed-browser" in sys.argv:
        check_mixed_size_drag()
    elif "--browser" in sys.argv:
        check_real_editor_drag()
    else:
        unittest.main()
