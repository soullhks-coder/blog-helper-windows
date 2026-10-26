import copy
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from PIL import Image, ImageDraw

import main
import tistory_v2_publish as publish
from tistory_v2 import V2Store, assign_slide_images, export_deck, render_slide
from tistory_v2_workflow import TistoryV2Workflow


BODY = '<h2>소제목</h2><p>본문을 유지합니다.</p>'
OLD_CREDIT = '<p class="blog-helper-tistory-v2-credits">사진 출처: 이전 자동 출처</p>'
AUTHORED_CREDIT = '<p>직접 입력한 출처: <a href="https://example.com">참고 문헌</a></p>'


class PhotoSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        photo = self.directory / 'photo.png'
        Image.new('RGB', (100, 100), '#ff0000').save(photo)
        self.assets = [
            {'path': str(photo), 'source': '위키백과', 'title': '서울', 'source_page': 'https://ko.wikipedia.org/wiki/서울'},
            {'path': str(photo), 'source': '나무위키', 'title': '서울', 'source_page': 'https://namu.wiki/w/서울'},
        ]
        self.store = V2Store(self.directory / 'decks')
        self.deck = self.store.new_deck('서울', '제목', BODY, 10, '스카이')
        self.deck['images'] = copy.deepcopy(self.assets)
        assign_slide_images(self.deck['slides'], self.assets)

    def test_number_and_photo_source_visibility_are_independent(self):
        original = ImageDraw.ImageDraw.text
        for numbers in (True, False):
            for sources in (True, False):
                texts = []
                def capture(draw, xy, text, *args, **kwargs):
                    texts.append(text)
                    return original(draw, xy, text, *args, **kwargs)
                with self.subTest(numbers=numbers, sources=sources), patch.object(ImageDraw.ImageDraw, 'text', capture):
                    rendered = render_slide(self.deck['slides'][0], index=1, total=10,
                                             show_numbers=numbers, show_photo_sources=sources)
                    self.assertEqual(any(text.startswith('사진:') for text in texts), sources)
                    self.assertEqual('1 / 10' in texts, numbers)
                    self.assertEqual(rendered.getpixel((105, 150)), (255, 0, 0))
        self.assertEqual(self.deck['slides'][0]['image'], self.assets[0])

    def test_default_and_legacy_decks_keep_source_display_enabled(self):
        self.assertTrue(self.deck['show_photo_sources'])
        slide = self.deck['slides'][0]
        self.assertEqual(render_slide(slide).tobytes(), render_slide(slide, show_photo_sources=True).tobytes())
        self.deck.pop('show_photo_sources')
        self.assertIn('위키백과', publish.source_credits_html(self.deck))

    def test_reexport_removes_image_footer_and_html_credits_but_retains_metadata(self):
        paths = export_deck(self.store, self.deck)
        with Image.open(paths[0]) as image:
            before = image.tobytes()
        self.deck['body'] += OLD_CREDIT + AUTHORED_CREDIT
        self.deck['show_photo_sources'] = False
        paths = export_deck(self.store, self.deck)
        with Image.open(paths[0]) as image:
            self.assertNotEqual(image.tobytes(), before)
            self.assertEqual(image.tobytes(), render_slide(self.deck['slides'][0], total=10, show_photo_sources=False).tobytes())
        preview = (self.store.deck_directory(self.deck) / 'article.html').read_text(encoding='utf-8')
        self.assertNotIn('사진 출처', preview)
        self.assertNotIn('이전 자동 출처', preview)
        self.assertNotIn('ko.wikipedia.org', preview)
        self.assertNotIn('namu.wiki', preview)
        self.assertIn(AUTHORED_CREDIT, preview)
        saved = self.store.load_deck(self.deck['id'])
        self.assertFalse(saved['show_photo_sources'])
        self.assertEqual(saved['images'], self.assets)
        self.assertEqual(saved['slides'][0]['image'], self.assets[0])
        self.deck['show_photo_sources'] = True
        export_deck(self.store, self.deck)
        restored = (self.store.deck_directory(self.deck) / 'article.html').read_text(encoding='utf-8')
        self.assertIn('사진 출처', restored)
        self.assertIn('namu.wiki', restored)

    def test_false_preference_applies_to_new_decks_and_automatic_preparation(self):
        self.store.save_preferences({'show_photo_sources': False})
        with patch.object(publish, 'collect_wiki_images', return_value=(self.assets, [])):
            result = publish.prepare_slide_deck('제목', BODY, '서울', self.store.directory, '/fake/chrome')
        self.assertFalse(result['deck']['show_photo_sources'])
        self.assertEqual(result['source_credits_html'], '')
        with Image.open(result['paths'][0]) as image:
            self.assertEqual(image.tobytes(), render_slide(result['deck']['slides'][0], total=10, show_photo_sources=False).tobytes())
        self.assertFalse(V2Store(self.store.directory).new_deck('다음', '제목', BODY, 10, '민트')['show_photo_sources'])

    def test_edited_publish_snapshot_omits_stale_generated_credits_only(self):
        self.deck['body'] = BODY + OLD_CREDIT + AUTHORED_CREDIT
        self.deck['show_photo_sources'] = False
        result = publish.export_edited_slide_deck(self.deck, '제목', BODY + AUTHORED_CREDIT, '서울', self.store.directory)
        self.assertEqual(result['source_credits_html'], '')
        self.assertEqual(result['article_html'], BODY + AUTHORED_CREDIT)
        self.assertEqual(self.deck['body'], BODY + OLD_CREDIT + AUTHORED_CREDIT)

    def test_general_writing_insert_does_not_emit_source_metadata_when_disabled(self):
        app = SimpleNamespace(
            _writing_keyword_selection_locked=lambda: False, _worker_is_running=lambda _: False,
            article_editor=MagicMock(), _switch_page=Mock(), _open_writing_section=Mock(), article_progress_label=Mock())
        app.article_editor.get.return_value = BODY
        for enabled in (False, True):
            self.deck['show_photo_sources'] = enabled
            main.KeywordApp._insert_tistory_v2_images(app, self.deck, [self.directory / 'slide.png'])
            body = app.article_editor.insert.call_args.args[1]
            self.assertEqual('ko.wikipedia.org' in body, enabled)
            self.assertIn("data-blog-helper-show-source='" + ('1' if enabled else '0') + "'", body)

    def test_standalone_editor_and_original_pipeline_share_credit_preference(self):
        page, context = MagicMock(), MagicMock()
        page.is_closed.return_value = False
        page.evaluate.return_value = {'titleOk': True, 'bodyOk': True}
        context.new_page.return_value = page
        api = SimpleNamespace(
            normalize_generated_article_html=lambda body: body,
            extract_tistory_prompt_tags=lambda body: (body, ['서울']),
            render_svg_to_png=Mock(return_value=self.directory / 'thumbnail.png'),
            launch_tistory_persistent_context=Mock(return_value=context), load_tistory_storage_state=Mock(return_value={}),
            restore_tistory_session_cookies=Mock(),
            save_tistory_storage_state=Mock(), wait_for_tistory_editor=Mock(return_value=(page, '')),
            build_tistory_editor_automation_script=Mock(return_value='script'), enter_tistory_tags_native=Mock(), append_runtime_log=Mock())
        workflow = TistoryV2Workflow(None, api)
        snapshot = {'profile': {'profile_scope': 'test', 'name': 'test', 'input_mode': 'HTML'},
                    'write_url': 'https://example.tistory.com/manage/newpost', 'thumbnail': {'svg': '<svg/>', 'size': 400}}
        self.deck['body'] = BODY + OLD_CREDIT + AUTHORED_CREDIT
        for enabled in (False, True):
            self.deck['show_photo_sources'] = enabled
            with patch('playwright.sync_api.sync_playwright', return_value=MagicMock()), \
                 patch('tistory_v2_editor.insert_native_slide_pairs', return_value={'collage_count': 5}), \
                 patch.object(workflow, '_wait_for_browser'):
                workflow.fill_editor(snapshot, self.deck, [self.directory / f'{i}.png' for i in range(10)],
                                     self.directory, threading.Event(), lambda _: None, Mock())
            body = api.build_tistory_editor_automation_script.call_args.args[1]
            self.assertEqual('사진 출처' in body, enabled)
            self.assertNotIn('이전 자동 출처', body)
            self.assertIn(AUTHORED_CREDIT, body)
            self.assertEqual(body, BODY + AUTHORED_CREDIT + publish.source_credits_html(self.deck))


if __name__ == '__main__':
    unittest.main()
