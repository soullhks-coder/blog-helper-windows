from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import main
import tistory_v2_publish as publish
from tistory_v2 import V2Store, deck_wiki_keyword
from test_writing_keyword_isolation import Entry, KeywordUI


BODY = '<h2>마그네슘 효능</h2><p>최종 편집한 글입니다.</p>'


class WikiKeywordTests(unittest.TestCase):
    def test_separate_query_reaches_collector_without_changing_article_identity(self):
        for override, expected in (('마그네슘', '마그네슘'), ('  미네랄  ', '미네랄'), ('', '마그네슘 효능')):
            with self.subTest(query=override), tempfile.TemporaryDirectory() as directory, \
                 patch.object(publish, 'collect_wiki_images', return_value=([], [])) as collect:
                result = publish.prepare_slide_deck('원래 제목', BODY, '마그네슘 효능', Path(directory),
                                                    '/fake/chrome', wiki_keyword=override)
                self.assertEqual(collect.call_args.args[0], expected)
                self.assertEqual(result['deck']['keyword'], '마그네슘 효능')
                self.assertEqual(result['deck']['title'], '원래 제목')
                self.assertEqual(result['article_html'], BODY)
                self.assertEqual(result['deck']['wiki_keyword'], expected)

    def test_old_deck_falls_back_and_changed_query_blocks_stale_photo_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            deck = V2Store(Path(directory)).new_deck('마그네슘 효능', '제목', BODY, 10, '스카이')
            deck.pop('wiki_keyword')
            self.assertEqual(deck_wiki_keyword(deck), '마그네슘 효능')
            self.assertTrue(publish.deck_matches_article(deck, '제목', BODY, '마그네슘 효능', wiki_keyword=''))
            self.assertFalse(publish.deck_matches_article(deck, '제목', BODY, '마그네슘 효능', wiki_keyword='마그네슘'))
            deck['wiki_keyword'] = '마그네슘'
            self.assertTrue(publish.deck_matches_article(deck, '제목', BODY, '마그네슘 효능', wiki_keyword='마그네슘'))
            with patch.object(publish, 'export_deck') as export, self.assertRaisesRegex(ValueError, '사진 검색어'):
                publish.export_edited_slide_deck(deck, '제목', BODY, '마그네슘 효능', Path(directory), wiki_keyword='다른 검색어')
            export.assert_not_called()

    def test_photo_query_does_not_invalidate_cached_article_slide_summary(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(main, 'DATA_DIR', Path(directory)):
            rows = [{'title': '이미 요약한 문구'}] * 10
            V2Store(Path(directory) / 'Tistory V2').save_preferences({'article_summary_plan': {
                'title': '제목', 'article_html': BODY, 'keyword': '마그네슘 효능', 'slides': rows}})
            app = SimpleNamespace(article_title_entry=Entry('제목'), generated_article_title='',
                                  _selected_or_manual_keyword=lambda: '마그네슘 효능', current_keyword='마그네슘 효능',
                                  _get_current_article_html=lambda: BODY, tistory_v2_wiki_keyword_entry=Entry('마그네슘'))
            for query in ('마그네슘', '미네랄', ''):
                app.tistory_v2_wiki_keyword_entry.set(query)
                source = main.KeywordApp._v2_article_source(app)
                self.assertEqual(source['wiki_keyword'], query)
                self.assertEqual(source['keyword'], '마그네슘 효능')
                self.assertEqual(source['slide_summaries'], rows)

    def test_query_is_saved_in_v2_only_not_overwritten_by_normal_menu(self):
        app = KeywordUI(main.WordPressSettings(tistory_v2_wiki_keyword='이전 위키 단어'))
        app._restore_writing_keyword_inputs('writing')
        app.switch('tistory_v2')
        self.assertEqual(app.tistory_v2_wiki_keyword_entry.get(), '이전 위키 단어')
        app.tistory_v2_wiki_keyword_entry.set('새 위키 단어')
        app.switch('writing')
        self.assertEqual(app.wordpress_settings.tistory_v2_wiki_keyword, '새 위키 단어')
        app.tistory_v2_wiki_keyword_entry.set('일반 메뉴에서 숨겨진 입력')
        app.switch('tistory_v2')
        self.assertEqual(app.tistory_v2_wiki_keyword_entry.get(), '새 위키 단어')
        app.tistory_v2_wiki_keyword_entry.set('')
        app.switch('writing')
        app.switch('tistory_v2')
        self.assertEqual(app.tistory_v2_wiki_keyword_entry.get(), '')

    def test_query_round_trips_without_copying_legacy_normal_topic(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main, 'STATE_FILE', Path(directory) / 'app_state.json'), \
             patch.object(main.PromptFileStore, 'load_into', side_effect=lambda value: value), \
             patch.object(main.KeychainStore, 'load_secret', return_value=''):
            settings = main.WordPressSettings(writing_topic='일반 주제')
            main.AppStateStore.save(settings, save_secrets=False)
            self.assertEqual(main.AppStateStore.load().tistory_v2_wiki_keyword, '')
            settings.tistory_v2_wiki_keyword = '마그네슘'
            main.AppStateStore.save(settings, save_secrets=False)
            restored = main.AppStateStore.load()
            self.assertEqual(restored.tistory_v2_wiki_keyword, '마그네슘')
            self.assertEqual(restored.writing_topic, '일반 주제')


if __name__ == '__main__':
    unittest.main()
