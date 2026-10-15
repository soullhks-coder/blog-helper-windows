"""Insert V2 assets through Tistory's existing uploader and native image drag UI.

The caller owns authentication, title/body input, browser lifetime and draft saving.
This module deliberately has no completion/publication controls. A collage is accepted
only after the editor itself creates a native image-grid containing the two uploads.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Callable

from PIL import Image

from tistory_v2 import SLIDE_SIZE, Cancelled, check_cancel, validate_slide_size, validate_slide_sizes


_EDITOR = r"""
const editor = window.tinymce?.activeEditor;
const frame = document.querySelector('#editor-tistory_ifr, iframe[title*="편집기"]');
const body = editor?.getBody?.() || frame?.contentDocument?.body ||
  document.querySelector('[contenteditable="true"]');
if (!body) throw new Error('티스토리 기본모드 본문 편집기를 찾지 못했습니다.');
const doc = body.ownerDocument;
const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
const originalText = () => {
  const copy = body.cloneNode(true);
  // Native attachment reserializes HTML, removing indentation/newlines between
  // blocks. Derive those separators from block boundaries, not incidental text
  // nodes. Keep meaningful inline spaces and every authored word in order.
  copy.querySelectorAll('figure').forEach(node => {
    if (node.querySelector('img') || /^(image|imageGrid|imageCollage)$/.test(node.getAttribute('data-ke-type') || '')) node.remove();
  });
  copy.querySelectorAll('[data-mce-bogus="all"],.mce-offscreen-selection,' +
    '.mce-visual-caret,[data-mce-caret],script,style').forEach(node => node.remove());
  const blocks = new Set(['ADDRESS','ARTICLE','ASIDE','BLOCKQUOTE','DIV','DL','DT','DD',
    'FIGURE','FIGCAPTION','H1','H2','H3','H4','H5','H6','LI','OL','P','PRE','SECTION',
    'TABLE','THEAD','TBODY','TFOOT','TR','TD','TH','UL']);
  const read = node => {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent.replace(/[\u200b\ufeff]/g, '');
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    if (node.tagName === 'BR') return ' ';
    const text = [...node.childNodes].map(read).join('');
    return blocks.has(node.tagName) ? ' ' + text + ' ' : text;
  };
  // Do not discard slots wholesale: a native editor may merge real text into
  // their paragraphs. Empty insertion/caret paragraphs contribute only spaces.
  return normalize(read(copy));
};
const bodyChange = before => {
  const after = originalText();
  if (after === before) return null;
  let position = 0;
  while (position < Math.min(before.length, after.length) && before[position] === after[position]) position++;
  return {before_length:before.length, after_length:after.length, first_difference:position,
    before_excerpt:before.slice(Math.max(0, position - 40), position + 80),
    after_excerpt:after.slice(Math.max(0, position - 40), position + 80)};
};
const imageKey = value => {
  try {
    let url = new URL(value, doc.baseURI);
    if (url.searchParams.has('fname')) url = new URL(url.searchParams.get('fname'), doc.baseURI);
    return url.origin + url.pathname;
  } catch (_) { return String(value || ''); }
};
const matchesImage = (image, url) => {
  const key = imageKey(url);
  return [image.currentSrc, image.src, image.getAttribute('data-src'),
    image.getAttribute('data-origin-url'), image.parentElement?.getAttribute('data-url')]
    .some(value => value && imageKey(value) === key);
};
const findImage = url => [...body.querySelectorAll('img')].filter(image => matchesImage(image, url));
const isGrid = node => node?.tagName === 'FIGURE' && (
  /image[-_]?(?:grid|collage)/i.test(String(node.className)) ||
  /^(?:imageGrid|imageCollage)$/i.test(node.getAttribute('data-ke-type') || '')
);
const gridFor = image => {
  let node = image?.parentElement;
  while (node && node !== body) { if (isGrid(node)) return node; node = node.parentElement; }
  return null;
};
const notifyChange = () => {
  editor?.nodeChanged?.(); editor?.setDirty?.(true); editor?.fire?.('change');
  body.dispatchEvent(new Event('input', {bubbles:true}));
};
"""

_PREPARE = "(run) => {" + _EDITOR + r"""
if (body.querySelector('[data-blog-helper-v2-upload]'))
  throw new Error('이 편집기에 V2 이미지를 이미 넣었습니다. 새 글쓰기 화면에서 다시 진행해 주세요.');
body.setAttribute('data-blog-helper-v2-editor', run);
const before = originalText();
const headings = [...body.querySelectorAll('h2,h3,h4,h5,h6')].filter(node => !node.closest('figure'));
for (let pair = 0; pair < 5; pair++) {
  const endIndex = Math.ceil((pair + 1) * headings.length / 5);
  const next = headings[endIndex] || null;
  const slot = doc.createElement('p');
  slot.setAttribute('data-blog-helper-v2-slot', `${run}-${pair}`);
  slot.innerHTML = '<br data-mce-bogus="1">';
  if (next) next.parentNode.insertBefore(slot, next);
  else body.appendChild(slot);
}
const thumbnail = doc.createElement('p');
thumbnail.setAttribute('data-blog-helper-v2-slot', `${run}-thumbnail`);
thumbnail.innerHTML = '<br data-mce-bogus="1">';
body.insertBefore(thumbnail, body.firstChild);
return {before, headings:headings.length};
}"""

_SELECT_SLOT = "(slotId) => {" + _EDITOR + r"""
const slot = body.querySelector(`[data-blog-helper-v2-slot="${slotId}"]`);
if (!slot) throw new Error('슬라이드를 넣을 본문 위치가 사라졌습니다.');
slot.scrollIntoView({block:'center'});
editor?.focus?.(); body.focus();
const range = doc.createRange(); range.selectNodeContents(slot); range.collapse(false);
const selection = doc.defaultView.getSelection(); selection.removeAllRanges(); selection.addRange(range);
editor?.selection?.setRng?.(range);
return true;
}"""

_MARK_UPLOAD = "({url, marker}) => {" + _EDITOR + r"""
const images = findImage(url);
if (images.length !== 1) return {count:images.length, ready:false};
const image = images[0];
image.setAttribute('data-blog-helper-v2-upload', marker);
const rect = image.getBoundingClientRect();
return {count:1, ready:!!image.complete && image.naturalWidth > 0 && rect.width > 10 && rect.height > 10};
}"""

_VERIFY_PAIR = "({urls, marker}) => {" + _EDITOR + r"""
const first = findImage(urls[0]), second = findImage(urls[1]);
if (first.length !== 1 || second.length !== 1) return false;
const grid = gridFor(first[0]);
if (!grid || grid !== gridFor(second[0])) return false;
const images = [...grid.querySelectorAll('img')];
if (images.length !== 2 || !matchesImage(images[0], urls[0]) || !matchesImage(images[1], urls[1])) return false;
grid.setAttribute('data-blog-helper-v2-pair', marker);
notifyChange();
return true;
}"""

_VERIFY_ALL = "({urls, thumbnail, before, run}) => {" + _EDITOR + r"""
const found = urls.map(url => findImage(url));
if (found.some(images => images.length !== 1)) return {ok:false, reason:'슬라이드 이미지가 누락되거나 중복되었습니다.'};
const ordered = [...body.querySelectorAll('img')].filter(image => urls.some(url => matchesImage(image, url)));
if (ordered.length !== 10 || ordered.some((image, i) => !matchesImage(image, urls[i])))
  return {ok:false, reason:'슬라이드 이미지 순서가 변경되었습니다.'};
const groups = new Set();
for (let index = 0; index < 10; index += 2) {
  const grid = gridFor(found[index][0]);
  if (!grid || grid !== gridFor(found[index + 1][0]) || grid.querySelectorAll('img').length !== 2)
    return {ok:false, reason:`${index / 2 + 1}번째 콜라주 구성을 확인하지 못했습니다.`};
  groups.add(grid);
}
if (groups.size !== 5) return {ok:false, reason:'콜라주가 5개가 아닙니다.'};
if (thumbnail) {
  const thumbs = findImage(thumbnail);
  if (thumbs.length !== 1 || gridFor(thumbs[0]) ||
      !(thumbs[0].compareDocumentPosition(found[0][0]) & Node.DOCUMENT_POSITION_FOLLOWING))
    return {ok:false, reason:'선택한 썸네일 위치를 확인하지 못했습니다.'};
}
const change = bodyChange(before);
if (change) return {ok:false, reason:'이미지 입력 중 원본 본문이 변경되었습니다.', body_change:change};
body.querySelectorAll('[data-blog-helper-v2-slot]').forEach(node => {
  if (node.getAttribute('data-blog-helper-v2-slot').startsWith(run) && !node.querySelector('img,figure') && !normalize(node.textContent)) node.remove();
});
notifyChange();
return {ok:true,slide_count:10,collage_count:groups.size,thumbnail:!!thumbnail};
}"""


def _wait(page, condition, cancel, timeout: float, message: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_cancel(cancel)
        result = condition()
        if result:
            return result
        page.wait_for_timeout(150)
    raise RuntimeError(message)


def wait_for_failed_editor(page) -> None:
    """Keep the failed native editor visible until the user closes it.

    No retry, rewrite, save or publication is performed here. The caller keeps
    ownership of the context/profile and reports the error before entering.
    """
    if page.is_closed():
        return
    page.bring_to_front()
    while not page.is_closed():
        try:
            page.wait_for_timeout(400)
        except Exception:
            # Closing the browser/context disconnects this page too.
            break


def _body_frame(page, run: str):
    selector = f'body[data-blog-helper-v2-editor="{run}"]'
    for frame in page.frames:
        if frame.locator(selector).count():
            return frame
    raise RuntimeError('티스토리 이미지가 있는 편집기 프레임을 찾지 못했습니다.')


def _mark_upload(page, url: str, marker: str, cancel) -> None:
    _wait(page, lambda: page.evaluate(_MARK_UPLOAD, {"url": url, "marker": marker}).get("ready"),
          cancel, 25, '파일은 업로드됐지만 본문에 새 이미지가 표시되지 않았습니다.')


def _settled_pair(first, second):
    """Require stable rendered geometry after asynchronous native attachment."""
    last, stable = None, 0

    def ready():
        nonlocal last, stable
        boxes = [image.bounding_box(timeout=1500) for image in (first, second)]
        if any(not box or box['width'] < 10 or box['height'] < 10 for box in boxes):
            stable = 0
            return False
        signature = tuple(round(box[key]) for box in boxes for key in ('x', 'y', 'width', 'height'))
        stable = stable + 1 if signature == last else 0
        last = signature
        return stable >= 2

    return ready


def _held_image_drag(page, first, second, fraction: float, cancel, target_y_fraction: float = .5) -> None:
    """Second upload → first upload using a held, stepped native mouse gesture.

    The hit-tested attachment surface (including a native selection overlay)
    receives the real pointer events. Never synthesize a DataTransfer or grid.
    """
    second.scroll_into_view_if_needed(timeout=5000)
    box = second.bounding_box(timeout=5000)
    if not box:
        raise RuntimeError('드래그할 두 번째 사진 위치를 확인하지 못했습니다.')
    sx, sy = box['x'] + box['width'] * .5, box['y'] + box['height'] * .5
    check_cancel(cancel)
    page.mouse.move(sx, sy)
    try:
        page.mouse.down()
        page.wait_for_timeout(180)
        check_cancel(cancel)
        # Cross the drag threshold first, before scrolling or approaching the
        # first picture. Some editors initialize their drop zones here.
        page.mouse.move(sx - 12, sy + 3, steps=6)
        page.wait_for_timeout(150)
        first.scroll_into_view_if_needed(timeout=5000)
        box = first.bounding_box(timeout=5000)
        if not box:
            raise RuntimeError('콜라주 대상인 첫 번째 사진 위치를 확인하지 못했습니다.')
        tx, ty = box['x'] + box['width'] * fraction, box['y'] + box['height'] * target_y_fraction
        check_cancel(cancel)
        page.mouse.move(tx, ty, steps=18)
        # A second move over the destination is needed for reliable dragover.
        # https://playwright.dev/python/docs/input#dragging-manually
        page.mouse.move(tx + 2, ty, steps=2)
        page.wait_for_timeout(650)
        check_cancel(cancel)
        page.mouse.move(tx, ty, steps=2)
    finally:
        # Never leave the physical mouse pressed after timeout/cancellation.
        page.mouse.up()


def _drag_pair(page, frame, run: str, index: int, urls: list[str], cancel,
               progress: Callable[[str], None] = lambda _message: None) -> None:
    marker = f'{run}-{index}'
    verified = lambda: page.evaluate(_VERIFY_PAIR, {"urls": urls, "marker": marker})
    errors = []
    # Always move upload 2 over upload 1, preserving their left-to-right order.
    # In the supplied video the source is dropped in the right half of IMG.
    # KEditor uses that half (and the middle vertical band) for an ordered merge;
    # a center/left drop can reverse the pair instead of preserving 1 → 2.
    for attempt, fraction in enumerate((0.9, 0.75), 1):
        check_cancel(cancel)
        if verified():
            return
        # Native editors can replace the IMG node after selection; reacquire
        # markers by hosted URL on every attempt instead of using stale nodes.
        _mark_upload(page, urls[0], f'{run}-slide-{index * 2}', cancel)
        _mark_upload(page, urls[1], f'{run}-slide-{index * 2 + 1}', cancel)
        first = frame.locator(f'img[data-blog-helper-v2-upload="{run}-slide-{index * 2}"]')
        second = frame.locator(f'img[data-blog-helper-v2-upload="{run}-slide-{index * 2 + 1}"]')
        try:
            _wait(page, _settled_pair(first, second), cancel, 6, '사진 첨부 후 배치가 안정되지 않았습니다.')
            progress(f'콜라주 {index + 1}/5 · 두 번째 사진을 첫 번째 사진의 오른쪽 영역으로 드래그합니다 · 시도 {attempt}/2')
            _held_image_drag(page, first, second, fraction, cancel)
            _wait(page, verified, cancel, 4,
                  f'{index + 1}번째 이미지 2장이 티스토리 콜라주로 묶이지 않았습니다.')
            progress(f'콜라주 {index + 1}/5 · 사진 2장이 하나의 콜라주로 묶인 것을 확인했습니다.')
            return
        except (Cancelled, MemoryError):
            raise
        except Exception as exc:
            # Keep the retry for pointer timeouts too, not just verification.
            errors.append(f'{type(exc).__name__}: {str(exc)[:180]}')
            progress(f'콜라주 {index + 1}/5 · 시도 {attempt} 미완료: {errors[-1]}')
    raise RuntimeError(f'{index + 1}번째 콜라주를 확인하지 못했습니다. 사진 2장 상태로 발행하지 않고 중단합니다. ' + ' / '.join(errors))


def insert_native_slide_pairs(
    page,
    slide_paths: list[str],
    thumbnail_path: str,
    upload_image: Callable[[str], str],
    progress: Callable[[str], None] = lambda _message: None,
    cancel=None,
    expected_size=SLIDE_SIZE,
    expected_sizes=None,
) -> dict:
    """Upload a separate thumbnail and ten slides as five native grids.

    Call only after title/body input has finished in the basic editor. ``upload_image``
    must select the local file through the existing uploader and return its hosted URL.
    All files are validated before touching the editor. Errors leave the current editor
    contents intact for inspection; retries require a fresh editor to avoid duplicates.
    """
    check_cancel(cancel)
    sizes = (validate_slide_sizes(expected_sizes) if expected_sizes is not None
             else [validate_slide_size(expected_size)] * 10)
    if len(slide_paths) != 10:
        raise ValueError('티스토리 V2 본문에는 슬라이드 이미지 10장이 필요합니다.')
    paths = [Path(value).expanduser().resolve() for value in slide_paths]
    if len(set(paths)) != 10:
        raise ValueError('같은 슬라이드 파일이 중복 선택되었습니다.')
    for path, size in zip(paths, sizes):
        with Image.open(path) as image:
            if image.format != 'PNG' or image.size != size:
                raise ValueError(f'{path.name}: 슬라이드는 {size[0]}×{size[1]} PNG여야 합니다.')
            image.verify()
    thumbnail = Path(thumbnail_path).expanduser().resolve() if thumbnail_path else None
    if thumbnail:
        with Image.open(thumbnail) as image:
            image.verify()
        if thumbnail in paths:
            raise ValueError('썸네일과 본문 슬라이드에는 서로 다른 파일이 필요합니다.')

    run = uuid.uuid4().hex
    prepared = page.evaluate(_PREPARE, run)
    frame = _body_frame(page, run)
    uploaded: list[str] = []
    thumbnail_url = ''

    def upload(path: Path, marker: str) -> str:
        check_cancel(cancel)
        url = str(upload_image(str(path)) or '').strip()
        if not url or url in uploaded or url == thumbnail_url:
            raise RuntimeError(f'{path.name}: 새 이미지 업로드 주소가 없거나 중복되었습니다.')
        _mark_upload(page, url, marker, cancel)
        return url

    if thumbnail:
        progress('선택한 썸네일을 본문 맨 앞에 넣고 있습니다...')
        page.evaluate(_SELECT_SLOT, f'{run}-thumbnail')
        thumbnail_url = upload(thumbnail, f'{run}-thumbnail')

    for index in range(5):
        check_cancel(cancel)
        progress(f'슬라이드 {index * 2 + 1}·{index * 2 + 2}번을 첨부하고 있습니다 ({index + 1}/5)...')
        page.evaluate(_SELECT_SLOT, f'{run}-{index}')
        pair = []
        for number in range(index * 2, index * 2 + 2):
            url = upload(paths[number], f'{run}-slide-{number}')
            uploaded.append(url)
            pair.append(url)
        progress(f'이미지 2장을 드래그하여 콜라주를 만들고 있습니다 ({index + 1}/5)...')
        _drag_pair(page, frame, run, index, pair, cancel, progress)

    check_cancel(cancel)
    result = page.evaluate(_VERIFY_ALL, {"urls": uploaded, "thumbnail": thumbnail_url,
                                       "before": prepared["before"], "run": run})
    if not result.get('ok'):
        if result.get('body_change'):
            progress('본문 변경 검사 상세: ' + json.dumps(result['body_change'], ensure_ascii=False))
        raise RuntimeError(result.get('reason') or '티스토리 V2 본문 이미지 검증에 실패했습니다.')
    progress('썸네일과 슬라이드 10장·콜라주 5개를 본문에 넣었습니다. 편집기에서 확인해 주세요.')
    return result
