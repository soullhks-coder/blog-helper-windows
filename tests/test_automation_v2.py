"""V2 batch routing, immutable job options, offline generation and native handoff."""
import copy
import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

import main
import automation_v2 as v2
from tistory_v2 import V2Store
from tistory_v2_publish import export_edited_slide_deck


BODY = '<h2>마그네슘의 역할</h2><p>마그네슘은 여러 기능에 관여합니다.</p><p>주요 태그: 마그네슘, 건강</p>'
SUMMARIES = [{"source_sections": [1], "eyebrow": "핵심 정리", "title": "마그네슘 역할",
              "summary": "몸의 기능에 관여해요", "bullets": ["균형 있는 섭취"]} for _ in range(10)]


def settings():
    base = main.WordPressSettings()
    base.tistory_profiles = main.normalize_tistory_profiles([
        {"blog_url": "https://first.example", "write_url": "https://first.tistory.com/manage/newpost/"},
        {"blog_url": "https://second.example", "write_url": "https://second.tistory.com/manage/newpost/", "daily_publish_limit": 15},
    ])
    base.tistory_active_profile = "티스토리 1"
    base.target_platforms = ["wordpress"]
    base.gpt_api_key = "SECRET-NEVER-SAVE-IN-JOB"
    return base


def fake_app(base):
    prompts = [{"id": "t-prompt", "platform": "tistory", "name": "내 V2 프롬프트",
                "title_prompt": "{keyword} 제목 지침", "article_prompt": "{keyword} 본문 지침"}]
    return SimpleNamespace(wordpress_settings=base, _prompt_sets=lambda: prompts,
                           thumbnail_presets=main.normalize_thumbnail_presets([]),
                           _save_active_thumbnail_preset=Mock())


class AutomationV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = settings()
        self.app = fake_app(self.base)
        self.api = SimpleNamespace(**{key: getattr(main, key) for key in
            ("normalize_tistory_profiles", "build_tistory_write_url", "normalize_thumbnail_preset_index",
             "TISTORY_SAVE_MODE_PUBLISH", "AutomationKeywordQueueWorker", "ArticleGenerationWorker",
             "generate_text_with_writing_model")}, DATA_DIR=self.root,
             require_google_chrome_executable=lambda: Path('/offline/chrome'))
        self.preferences = {"platform": "tistory", "profile": "티스토리 2", "prompt_id": "t-prompt", "thumbnail": 2,
                            "review_publish": True, "theme": "민트", "show_numbers": False,
                            "show_photo_sources": False, "slide_size": [800, 400],
                            "text_position_defaults": {"text_horizontal": "중앙", "text_vertical": "아래"}}
        V2Store(self.root / 'Tistory V2').save_preferences(self.preferences)
        self.config = v2.snapshot(self.app, self.api)

    def test_v2_mode_is_exclusive_and_unbuilt_automation_page_reads_saved_selection(self):
        self.assertEqual(v2.targets(['tistory', 'wordpress', 'tistory_v2', 'tistory_v2']), ['tistory_v2'])
        self.base.automation_target_platforms = ['tistory_v2']
        self.app.automation_target_platform_vars = {}
        self.assertEqual(main.KeywordApp._automation_target_platforms(self.app), ['tistory_v2'])
        self.base.automation_target_platforms = []
        self.assertEqual(main.KeywordApp._automation_target_platforms(self.app), [])

    def test_checkbox_changes_preserve_normal_multi_target_but_exclude_v2(self):
        class Var:
            def __init__(self, value): self.value = value
            def get(self): return self.value
            def set(self, value): self.value = value
        self.app.automation_target_platform_vars = {k: Var(True) for k in ['wordpress', 'tistory', 'blogspot', 'tistory_v2']}
        self.app._save_automation_settings = Mock()
        main.KeywordApp._on_automation_target_changed(self.app, 'tistory_v2')
        self.assertEqual(main.KeywordApp._automation_target_platforms(self.app), ['tistory_v2'])
        self.app.automation_target_platform_vars['blogspot'].set(True)
        main.KeywordApp._on_automation_target_changed(self.app, 'blogspot')
        self.assertEqual(main.KeywordApp._automation_target_platforms(self.app), ['blogspot'])

    def test_capture_account_prompt_design_preferences_without_secrets(self):
        self.assertEqual(self.config['profile']['name'], '티스토리 2')
        self.assertEqual(self.config['prompt']['id'], 't-prompt')
        self.assertEqual(self.config['thumbnail_index'], 2)
        self.assertFalse(self.config['slide_options']['show_numbers'])
        self.assertNotIn('SECRET', json.dumps(self.config))
        self.assertNotIn('profile_path', self.config['profile'])
        self.app.thumbnail_presets[2]['text'] = 'changed after enqueue'
        self.assertNotEqual(self.config['thumbnail_preset']['text'], 'changed after enqueue')

    def test_queue_roundtrip_keeps_v2_jobs_and_settings_failure_rolls_back_memory(self):
        items = v2.make_items([{'keyword': '마그네슘', 'wiki_keyword': 'Mg'}], self.config, [])[0]
        self.base.automation_queue = items
        self.base.automation_target_platforms = ['tistory_v2']
        self.base.automation_v2_profile = '티스토리 2'
        with patch.object(main, 'STATE_FILE', self.root / 'state.json'):
            main.AppStateStore.save(self.base, save_secrets=False)
            saved = json.loads((self.root / 'state.json').read_text(encoding='utf-8'))
            with patch.object(main.KeychainStore, 'load_secret', return_value=''), \
                 patch.object(main.PromptFileStore, 'load_into', side_effect=lambda value: value):
                restored = main.AppStateStore.load()
        self.assertEqual(restored.automation_v2_profile, '티스토리 2')
        self.assertEqual(saved['automation_queue'], items)
        self.assertEqual(saved['automation_target_platforms'], ['tistory_v2'])
        self.app.automation_queue = []
        self.app._assign_automation_schedule = lambda item: item.update(scheduled_at=123)
        self.app._save_automation_queue = Mock(side_effect=OSError('disk full'))
        with self.assertRaisesRegex(OSError, 'disk full'):
            main.KeywordApp._enqueue_managed_v2_keywords(self.app, [{'keyword': '마그네슘'}], config=self.config)
        self.assertEqual(self.app.automation_queue, [])
        self.assertEqual(self.base.automation_queue, [])

    def test_explicit_automation_blog_uses_its_own_v2_design_without_changing_manual_choice(self):
        self.base.automation_v2_profile = '티스토리 1'
        alternate = {"id": "first-prompt", "platform": "tistory", "name": "첫 블로그용",
                     "title_prompt": "첫 제목", "article_prompt": "첫 본문"}
        self.app._prompt_sets = lambda: [alternate, {**alternate, 'id': 't-prompt'}]
        preferences = {**self.preferences, 'blog_preferences': {
            'tistory:티스토리 1': {'prompt_id': 'first-prompt', 'thumbnail': 4}}}
        store = V2Store(self.root / 'Tistory V2')
        store.save_preferences(preferences)
        original = store.preferences()
        before = copy.deepcopy(self.base)
        config = v2.snapshot(self.app, self.api)
        self.assertEqual(config['profile']['name'], '티스토리 1')
        self.assertEqual(config['prompt']['id'], 'first-prompt')
        self.assertEqual(config['thumbnail_index'], 4)
        queued = v2.make_items([{'keyword': '첫 블로그 글'}], config, [])[0][0]
        self.base.automation_v2_profile = '티스토리 2'
        self.assertEqual(v2.snapshot(self.app, self.api)['profile']['name'], '티스토리 2')
        self.assertEqual(v2.execution_settings(queued['v2_config'], self.base, self.api).tistory_blog_url,
                         'https://first.example')
        self.base.automation_v2_profile = before.automation_v2_profile
        self.assertEqual(self.base, before)
        self.assertEqual(store.preferences(), original)

    def test_explicit_blog_without_saved_design_uses_defaults_and_missing_blog_never_falls_back(self):
        self.base.automation_v2_profile = '티스토리 1'
        first_prompt = {"id": "default-prompt", "platform": "tistory", "name": "기본",
                        "title_prompt": "제목", "article_prompt": "본문"}
        self.app._prompt_sets = lambda: [first_prompt, {**first_prompt, 'id': 't-prompt'}]
        config = v2.snapshot(self.app, self.api)
        self.assertEqual(config['prompt']['id'], 'default-prompt')
        self.assertEqual(config['thumbnail_index'], self.base.thumbnail_default_preset)
        self.base.automation_v2_profile = '티스토리 3'  # Empty slot, despite a valid manual V2 choice.
        with self.assertRaisesRegex(ValueError, 'V2 발행 블로그'):
            v2.snapshot(self.app, self.api)

    def test_selector_save_failure_and_read_only_settings_revert_choice(self):
        from automation_v2_selector import AutomationV2BlogSelector
        selector = SimpleNamespace(app=self.app, api=self.api, choices={'첫 블로그': '티스토리 1'}, refresh=Mock())
        selector.api.AppStateStore = SimpleNamespace(save=Mock(side_effect=OSError('disk full')))
        self.base.automation_v2_profile = '티스토리 2'
        with patch('automation_v2_selector.messagebox.showerror') as error:
            AutomationV2BlogSelector.changed(selector, '첫 블로그')
            error.assert_called_once()
        self.assertEqual(self.base.automation_v2_profile, '티스토리 2')
        self.base._state_read_error = 'corrupt'
        selector.api.AppStateStore.save.reset_mock()
        with patch('automation_v2_selector.messagebox.showerror'):
            AutomationV2BlogSelector.changed(selector, '첫 블로그')
        selector.api.AppStateStore.save.assert_not_called()
        self.assertEqual(self.base.automation_v2_profile, '티스토리 2')

    def test_blogspot_header_cannot_redirect_tistory_queue_account_or_prompt(self):
        prefs = {**self.preferences, 'platform': 'blogspot', 'profile': '블로그스팟 1', 'prompt_id': 'blogspot-prompt',
                 'profile_selections': {'tistory': '티스토리 2'},
                 'blog_preferences': {'tistory:티스토리 2': {'prompt_id': 't-prompt', 'thumbnail': 3}}}
        V2Store(self.root / 'Tistory V2').save_preferences(prefs)
        config = v2.snapshot(self.app, self.api)
        self.assertEqual(config['profile']['name'], '티스토리 2')
        self.assertEqual(config['prompt']['id'], 't-prompt')
        self.assertEqual(config['thumbnail_index'], 3)

    def test_account_or_prompt_missing_fails_before_queue_mutation(self):
        prefs = {**self.preferences, 'prompt_id': 'deleted'}
        V2Store(self.root / 'Tistory V2').save_preferences(prefs)
        with self.assertRaisesRegex(ValueError, '프롬프트'):
            v2.snapshot(self.app, self.api)
        prefs.update(prompt_id='t-prompt', profile='티스토리 3')
        V2Store(self.root / 'Tistory V2').save_preferences(prefs)
        with self.assertRaisesRegex(ValueError, '블로그'):
            v2.snapshot(self.app, self.api)

    def test_batch_optional_wiki_scoped_dedupe_500_and_detached_records(self):
        rows = [{'keyword': f'건강 {i}', 'wiki_keyword': '마그네슘' if i % 2 == 0 else ''} for i in range(500)]
        items, skipped = v2.make_items(rows + [rows[0]], self.config, [])
        self.assertEqual((len(items), skipped), (500, 1))
        self.assertEqual(items[0]['wiki_keyword'], '마그네슘')
        self.assertEqual(items[1]['wiki_keyword'], '')
        self.assertFalse(items[0]['article_html'])
        items[0]['v2_config']['prompt']['name'] = 'edited item'
        self.assertNotEqual(items[1]['v2_config']['prompt']['name'], 'edited item')
        more, skipped = v2.make_items(rows[:1], self.config, items)
        self.assertEqual((len(more), skipped), (0, 1))
        changed = copy.deepcopy(self.config)
        changed['profile']['profile_scope'] = 'another-blog'
        more, skipped = v2.make_items(rows[:1], changed, items)
        self.assertEqual((len(more), skipped), (0, 1))  # Same blog registered in another slot.
        changed['profile']['write_url'] = 'https://another.tistory.com/manage/newpost/'
        more, skipped = v2.make_items(rows[:1], changed, items)
        self.assertEqual((len(more), skipped), (1, 0))
        more, _ = v2.make_items(rows[:1], self.config, [{'keyword': rows[0]['keyword'], 'target_platforms': ['tistory']}])
        self.assertEqual(len(more), 1)

    def test_execution_uses_job_account_and_review_setting_without_changing_base(self):
        before = copy.deepcopy(self.base)
        result = v2.execution_settings(self.config, self.base, self.api)
        self.assertEqual(result.target_platforms, ['tistory'])
        self.assertEqual(result.tistory_active_profile, '티스토리 2')
        self.assertEqual(result.tistory_blog_url, 'https://second.example')
        self.assertFalse(result.tistory_auto_publish)
        self.assertEqual(result.tistory_save_mode, main.TISTORY_SAVE_MODE_PUBLISH)
        self.assertFalse(result.inline_images_enabled)
        self.assertFalse(result.cardnews_enabled)
        self.assertEqual(self.base, before)
        self.base.tistory_profiles[1]['daily_publish_limit'] = 5
        self.assertEqual(v2.execution_settings(self.config, self.base, self.api).tistory_daily_publish_limit, 5)
        self.base.tistory_profiles[1]['write_url'] = 'https://changed.tistory.com/manage/newpost/'
        with self.assertRaisesRegex(ValueError, '변경'):
            v2.execution_settings(self.config, self.base, self.api)

    def test_review_off_retains_automatic_or_draft_intent_and_read_only_settings_are_blocked(self):
        config = copy.deepcopy(self.config)
        config['review_publish'] = False
        for automatic in (False, True):
            for mode in (main.TISTORY_SAVE_MODE_DRAFT, main.TISTORY_SAVE_MODE_PUBLISH):
                config['profile'].update(auto_publish=automatic, save_mode=mode)
                result = v2.execution_settings(config, self.base, self.api)
                self.assertEqual(result.tistory_auto_publish, automatic)
                self.assertEqual(result.tistory_save_mode, mode)
        self.base._state_read_error = 'corrupt settings'
        with self.assertRaisesRegex(ValueError, '복구'):
            v2.snapshot(self.app, self.api)

    def worker(self, row=None):
        item = v2.make_items([row or {'keyword': '마그네슘 효능', 'wiki_keyword': '마그네슘'}], self.config, [])[0][0]
        result_queue = queue.Queue()
        result = v2.PrepareWorker(item, v2.execution_settings(self.config, self.base, self.api), self.api, result_queue)
        return result, result_queue

    def test_real_article_summary_photo_deck_export_through_pipeline_contract(self):
        worker, events = self.worker()
        photo = self.root / 'sample.png'
        Image.new('RGB', (200, 100), 'red').save(photo)
        photos = [{'path': str(photo), 'source': '위키백과', 'source_page': 'https://ko.wikipedia.org/wiki/마그네슘'}] * 2
        with patch.object(main.AutomationKeywordQueueWorker, '_collect_reference_text_for_keyword', return_value='수집된 참고 자료') as collect, \
             patch.object(main.ArticleGenerationWorker, '_generate_with_provider', side_effect=[('마그네슘 효능 안내', 'offline'),
                 (json.dumps({'article_html': BODY, 'slides': SUMMARIES}), 'offline')]) as generate, \
             patch('tistory_v2.collect_wiki_images', return_value=(photos, [])) as images:
            worker.run()
        results = list(events.queue)
        self.assertFalse([p for kind, p in results if kind == 'automation_v2_error'], results)
        prepared = [p for kind, p in results if kind == 'automation_v2_prepared'][0]
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(images.call_args.args[0], '마그네슘')
        self.assertIn('article_html', generate.call_args.args[0])
        self.assertEqual([k for k, _ in results if k.endswith(('article_ready', 'prepared'))],
                         ['automation_v2_article_ready', 'automation_v2_prepared'])
        deck = prepared['v2_deck']
        self.assertEqual(deck['text_mode'], 'summary')
        self.assertEqual(len(deck['slides']), 10)
        self.assertTrue(all(s['image'] for s in deck['slides'][:2]))
        self.assertTrue(all(s['image'] is None for s in deck['slides'][2:]))
        self.assertEqual(deck['slides'][0]['text_horizontal'], '중앙')
        self.assertFalse(deck['show_numbers'])
        self.assertFalse(deck['show_photo_sources'])
        exported = export_edited_slide_deck(deck, prepared['title'], prepared['article_html'], prepared['keyword'],
            self.root / 'Tistory V2', wiki_keyword=prepared['wiki_keyword'])
        self.assertEqual(len(exported['paths']), 10)
        self.assertEqual(exported['source_credits_html'], '')
        for path in exported['paths']:
            with Image.open(path) as image:
                self.assertEqual(image.size, (800, 400))

    def test_retry_with_article_never_regenerates_full_article_or_recollects_reference(self):
        worker, events = self.worker({'keyword': '칼슘', 'article_html': BODY, 'title': '칼슘 안내'})
        with patch.object(main.AutomationKeywordQueueWorker, '_collect_reference_text_for_keyword') as collect, \
             patch.object(main.ArticleGenerationWorker, 'run') as article, \
             patch.object(self.api, 'generate_text_with_writing_model', return_value=(json.dumps({'slides': SUMMARIES}), 'offline')) as summarize, \
             patch('tistory_v2.collect_wiki_images', return_value=([], [])) as images:
            worker.run()
        collect.assert_not_called()
        article.assert_not_called()
        summarize.assert_called_once()
        self.assertEqual(images.call_args.args[0], '칼슘')
        self.assertEqual(list(events.queue)[-1][0], 'automation_v2_prepared')

    def test_invalid_summary_stops_and_preserves_completed_article_for_retry(self):
        worker, events = self.worker()
        with patch.object(main.AutomationKeywordQueueWorker, '_collect_reference_text_for_keyword', return_value='참고'), \
             patch.object(main.ArticleGenerationWorker, '_generate_with_provider', side_effect=[('제목', 'offline'),
                 ('<p>완성된 상세 본문</p>', 'offline'), RuntimeError('summary unavailable')]), \
             patch.object(self.api, 'generate_text_with_writing_model', side_effect=RuntimeError('summary unavailable')), \
             patch('tistory_v2.collect_wiki_images') as images:
            worker.run()
        kinds = [kind for kind, _ in list(events.queue)]
        self.assertIn('automation_v2_article_ready', kinds)
        self.assertEqual(kinds[-1], 'automation_v2_error')
        images.assert_not_called()

    def test_cancel_prevents_generation_and_upload(self):
        worker, events = self.worker()
        worker.cancel()
        with patch.object(main.ArticleGenerationWorker, 'run') as article:
            worker.run()
        article.assert_not_called()
        self.assertEqual(list(events.queue)[-1][0], 'automation_v2_error')

    def test_failed_jobs_are_not_rescheduled_or_automatically_retried(self):
        items = v2.make_items([{'keyword': '대기'}, {'keyword': '실패'}], self.config, [])[0]
        items[0].update(scheduled_at=1, status='V2 대기 중')
        items[1].update(scheduled_at=1, status=v2.PAUSED)
        app = SimpleNamespace(automation_queue=items)
        self.assertFalse(main.KeywordApp._reschedule_expired_automation_items(app))
        self.assertIs(main.KeywordApp._next_publishable_automation_item_for_time(app, due_only=True), items[0])
        self.assertEqual(items[1]['status'], v2.PAUSED)

    def test_keyword_only_queue_routes_to_v2_without_normal_publish_adapter(self):
        item = v2.make_items([{'keyword': '마그네슘'}], self.config, [])[0][0]
        app = SimpleNamespace(active_automation_upload_item_id='', pipeline_worker=None, tistory_automation_worker=None,
            _writing_keyword_selection_locked=lambda: False,
            _stop_automation_publish_countdown=Mock(), _worker_is_running=lambda worker: False,
            _find_automation_queue_item=lambda _id: item, _start_automation_v2_item=Mock(return_value=True),
            _read_wordpress_settings=Mock(side_effect=AssertionError('V2 handoff must occur first')))
        self.assertTrue(main.KeywordApp._publish_next_automation_queue_item(app, item_id=item['id']))
        app._start_automation_v2_item.assert_called_once_with(item)

    def test_native_handoff_gap_cannot_accept_an_unrelated_writing_keyword(self):
        app = SimpleNamespace(active_automation_upload_item_id='v2-pending-events')
        self.assertTrue(main.KeywordApp._writing_keyword_selection_locked(app))

    def test_waiting_keyword_edit_invalidates_old_content_but_preserves_schedule_and_options(self):
        item = v2.make_items([{'keyword': '마그네슘', 'wiki_keyword': 'Mg'}], self.config, [])[0][0]
        item.update(title='완성 제목', article_html=BODY, reference_text='old facts', source_urls={'old': 'https://example.com'},
                    thumbnail_path='old-thumb.png', slide_summaries=SUMMARIES, v2_deck={'id': 'old'},
                    provider='offline', scheduled_at=12345)
        before = copy.deepcopy(item)
        result = v2.edited_keywords(item, before, '  칼슘  효능 ', '', [item])
        self.assertEqual(result['keyword'], '칼슘 효능')
        self.assertEqual(result['wiki_keyword'], '')
        self.assertEqual(result['title'], '칼슘 효능')
        for key in ('article_html', 'reference_text', 'thumbnail_path'):
            self.assertEqual(result[key], '')
        for key in ('slide_summaries', 'v2_deck', 'provider'):
            self.assertNotIn(key, result)
        self.assertEqual(result['source_urls'], {})
        for key in ('id', 'scheduled_at', 'status', 'v2_config', 'target_platforms'):
            self.assertEqual(result[key], before[key])
        self.assertEqual(item, before)

    def test_wiki_only_edit_keeps_article_summaries_and_pause_boundary(self):
        item = v2.make_items([{'keyword': '마그네슘', 'wiki_keyword': 'Mg'}], self.config, [])[0][0]
        item.update(status=v2.PAUSED, article_html=BODY, reference_text='수집 내용', slide_summaries=SUMMARIES,
                    v2_deck={'id': 'old-photos'}, thumbnail_path='current.png')
        result = v2.edited_keywords(item, copy.deepcopy(item), '마그네슘', '  마그네슘  ', [item])
        self.assertEqual(result['wiki_keyword'], '마그네슘')
        for key in ('article_html', 'slide_summaries', 'reference_text', 'thumbnail_path', 'status'):
            self.assertEqual(result[key], item[key])
        self.assertNotIn('v2_deck', result)
        self.assertEqual(v2.edited_keywords(item, copy.deepcopy(item), '마그네슘', 'Mg', [item]), item)

    def test_edit_rejects_blank_long_duplicate_stale_running_or_published_jobs_atomically(self):
        items = v2.make_items([{'keyword': '마그네슘'}, {'keyword': '칼슘'}], self.config, [])[0]
        item, before = items[0], copy.deepcopy(items[0])
        for keyword, wiki in [('', ''), ('x' * 501, ''), ('마그네슘', 'x' * 501), ('칼슘', '')]:
            with self.subTest(keyword=keyword[:20], wiki=wiki[:20]), self.assertRaises(ValueError):
                v2.edited_keywords(item, before, keyword, wiki, items)
            self.assertEqual(item, before)
        with self.assertRaisesRegex(ValueError, '변경'):
            v2.edited_keywords(item, {**before, 'wiki_keyword': 'stale'}, 'new', '', items)
        for patch in ({'status': 'V2 준비 중'}, {'status': '업로드 중'}, {'published_url': 'https://example.com/post'}):
            changed = {**item, **patch}
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                v2.edited_keywords(changed, copy.deepcopy(changed), 'new', '', [changed])
        with self.assertRaises(ValueError):
            v2.edited_keywords(item, before, 'new', '', items, active_id=item['id'])
        with self.assertRaises(ValueError):
            v2.edited_keywords(None, before, 'new', '', items)

    def test_edit_scheduler_hold_blocks_both_due_and_explicit_start_and_releases_on_close(self):
        item = v2.make_items([{'keyword': '편집 중'}], self.config, [])[0][0]
        item['scheduled_at'] = 1
        app = SimpleNamespace(automation_queue=[item], _automation_keyword_editing_ids={item['id']},
            active_automation_upload_item_id='', pipeline_worker=None, tistory_automation_worker=None,
            _writing_keyword_selection_locked=lambda: False, _worker_is_running=lambda worker: False,
            _stop_automation_publish_countdown=Mock(), _schedule_next_automation_publish=Mock(),
            _find_automation_queue_item=lambda _id: item, _start_automation_v2_item=Mock())
        self.assertIsNone(main.KeywordApp._next_publishable_automation_item_for_time(app, due_only=True))
        self.assertFalse(main.KeywordApp._publish_next_automation_queue_item(app, item_id=item['id']))
        app._start_automation_v2_item.assert_not_called()
        app._automation_keyword_editing_ids.clear()
        self.assertIs(main.KeywordApp._next_publishable_automation_item_for_time(app, due_only=True), item)

    def test_edit_save_failure_rolls_back_original_job_and_cached_settings(self):
        item = v2.make_items([{'keyword': '원본', 'wiki_keyword': '원본 사진'}], self.config, [])[0][0]
        before = copy.deepcopy(item)
        app = SimpleNamespace(automation_queue=[item], wordpress_settings=self.base, active_automation_upload_item_id='',
            _find_automation_queue_item=lambda _id: item,
            _save_automation_queue=Mock(side_effect=OSError('disk full')))
        with self.assertRaisesRegex(OSError, 'disk full'):
            main.KeywordApp._save_automation_queue_keywords(app, item['id'], before, '새 키워드', '')
        self.assertEqual(item, before)
        self.assertEqual(self.base.automation_queue, [before])

    def test_edited_job_is_saved_with_optional_query_and_other_job_intact(self):
        items = v2.make_items([{'keyword': '원본', 'wiki_keyword': '원본 사진'}, {'keyword': '다른 작업'}], self.config, [])[0]
        items[0].update(article_html=BODY, scheduled_at=23456)
        self.base.automation_queue = items
        before_other = copy.deepcopy(items[1])
        app = SimpleNamespace(automation_queue=items, wordpress_settings=self.base, active_automation_upload_item_id='',
            _find_automation_queue_item=lambda _id: items[0], _refresh_automation_queue=Mock(), automation_status_label=Mock())
        def save():
            self.base.automation_queue = list(items)
            main.AppStateStore.save(self.base, save_secrets=False)
        app._save_automation_queue = save
        with patch.object(main, 'STATE_FILE', self.root / 'state.json'), patch.object(main, 'append_runtime_log'):
            save()
            main.KeywordApp._save_automation_queue_keywords(app, items[0]['id'], copy.deepcopy(items[0]), '새로운 키워드', '')
            saved = json.loads((self.root / 'state.json').read_text(encoding='utf-8'))['automation_queue']
        self.assertEqual(saved[0]['keyword'], '새로운 키워드')
        self.assertEqual(saved[0]['wiki_keyword'], '')
        self.assertEqual(saved[0]['article_html'], '')
        self.assertEqual(saved[0]['scheduled_at'], 23456)
        self.assertEqual(saved[1], before_other)


if __name__ == '__main__':
    unittest.main()
