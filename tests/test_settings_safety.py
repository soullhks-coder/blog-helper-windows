"""Saved user libraries survive scoped resets, stale writers and lost backups."""
from contextlib import ExitStack
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from settings_persistence import SettingsFile, SettingsReadError, merge_settings


def button_set(name, url='https://fixture.example/button'):
    return {'name': name, 'enabled': True, 'links': [
        {'button_text': name, 'url': url, 'width': '', 'full_width': True, 'position': '본문하단'}]}


class LibraryMergeSafetyTests(unittest.TestCase):
    def test_empty_whole_settings_save_cannot_clear_button_library(self):
        old = {'writing_link_sets': [button_set('가족 버튼')]}
        for autosave in (True, False):
            with self.subTest(autosave=autosave):
                merged = merge_settings(old, {'writing_link_sets': []}, autosave=autosave)
                self.assertEqual(merged, old)

    def test_stale_named_set_edit_keeps_sets_added_by_another_writer(self):
        first = button_set('첫 세트')
        second = button_set('나중에 추가한 세트')
        baseline = {'writing_link_sets': [first]}
        current = {'writing_link_sets': [first, second]}
        incoming = copy.deepcopy(baseline)
        incoming['writing_link_sets'][0]['enabled'] = False
        merged = merge_settings(current, incoming, baseline=baseline)
        self.assertEqual(merged['writing_link_sets'][1], second)
        # Existing library edits require the dedicated named-save transaction.
        self.assertTrue(merged['writing_link_sets'][0]['enabled'])

    def test_editing_one_thumbnail_does_not_replace_another_recovered_slot(self):
        baseline = {'thumbnail_presets': [{'font_size': 56}, {'font_size': 56}, {'font_size': 56}]}
        current = copy.deepcopy(baseline)
        current['thumbnail_presets'][2].update(background_image_path='managed.png', font_size=90)
        incoming = copy.deepcopy(baseline)
        incoming['thumbnail_presets'][0]['font_size'] = 80
        merged = merge_settings(current, incoming, baseline=baseline)
        self.assertEqual(merged['thumbnail_presets'][2], current['thumbnail_presets'][2])
        self.assertEqual(merged['thumbnail_presets'][0]['font_size'], 80)

    def test_untracked_default_templates_do_not_replace_saved_designs(self):
        previous = {'thumbnail_presets': [{'font_size': 90, 'background_image_path': 'kept.png'}]}
        merged = merge_settings(previous, {'thumbnail_presets': [{'font_size': 56, 'background_image_path': ''}]})
        self.assertEqual(merged, previous)


class ScopedWordPressResetTests(unittest.TestCase):
    def test_reset_backup_failure_leaves_ui_model_and_credentials_unchanged(self):
        import main
        original = main.WordPressSettings(username='fixture-wp', app_password='fixture-pass')
        app = SimpleNamespace(wordpress_settings=original)
        with patch.object(main.AppStateStore, 'save', side_effect=OSError('fixture backup failure')), \
             patch.object(main.messagebox, 'showerror') as error, \
             patch.object(main.KeychainStore, 'delete_secret') as delete:
            main.KeywordApp._reset_wordpress_settings(app)
        self.assertIs(app.wordpress_settings, original)
        self.assertEqual(original.app_password, 'fixture-pass')
        error.assert_called_once()
        delete.assert_not_called()

    def test_wordpress_reset_preserves_every_unrelated_setting(self):
        import main
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            path = Path(folder) / 'app_state.json'
            stack.enter_context(patch.object(main, 'STATE_FILE', path))
            stack.enter_context(patch.object(main.KeychainStore, 'save_secret', return_value=True))
            deleted = stack.enter_context(patch.object(main.KeychainStore, 'delete_secret'))
            settings = main.WordPressSettings(
                blog_url='https://wp.fixture.example', username='fixture-wp', app_password='fixture-pass',
                writing_link_sets=[button_set('가족 버튼')],
                thumbnail_presets=main.normalize_thumbnail_presets(None),
                tistory_profiles=main.normalize_tistory_profiles([{'blog_url': 'https://fixture.tistory.com'}]),
                app_title='Keep title', gpt_api_key='fixture-ai-secret',
            )
            settings.thumbnail_presets[2]['background_image_path'] = 'keep-design.png'
            main.AppStateStore.save(settings)
            before = asdict(settings)
            app = SimpleNamespace(wordpress_settings=settings)
            for name in ('blog_url_entry', 'username_entry', 'password_entry', 'category_menu',
                         'post_mode_menu', 'webmaster_naver_var', 'webmaster_daum_var',
                         'webmaster_google_var', 'wordpress_daily_publish_limit_entry',
                         '_refresh_category_checkboxes', '_refresh_daily_publish_limit_statuses',
                         '_set_wp_status', '_update_quick_status'):
                setattr(app, name, Mock())
            main.KeywordApp._reset_wordpress_settings(app)
            after = asdict(app.wordpress_settings)
            allowed = {'blog_url', 'username', 'app_password', 'category_name', 'category_id',
                       'category_names', 'category_ids', 'categories', 'post_mode',
                       'wordpress_daily_publish_limit', 'webmaster_submit_naver',
                       'webmaster_submit_daum', 'webmaster_submit_google', 'webmaster_completion_message'}
            self.assertEqual({k:v for k,v in before.items() if k not in allowed},
                             {k:v for k,v in after.items() if k not in allowed})
            saved = SettingsFile(path).read()
            self.assertEqual(saved['writing_link_sets'], before['writing_link_sets'])
            self.assertEqual(saved['thumbnail_presets'], before['thumbnail_presets'])
            self.assertEqual(saved['tistory_profiles'], before['tistory_profiles'])
            self.assertEqual(saved['gpt_api_key_fallback'], 'fixture-ai-secret')
            self.assertEqual(saved['blog_url'], '')
            self.assertEqual(saved['app_password_fallback'], '')
            deleted.assert_called_once_with(main.KEYCHAIN_WP_PREFIX + 'fixture-wp')


class SettingsHistorySafetyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'app_state.json'
        self.store = SettingsFile(self.path)
        self.good = {'writing_link_sets': [button_set('가족 버튼')], 'writing_topic': 'first topic'}

    def test_first_successful_save_is_archived_and_autosaves_do_not_rotate_it_away(self):
        self.store.update(lambda _: copy.deepcopy(self.good))
        count = len(self.store.history_paths())
        for index in range(30):
            self.store.update(lambda current: {**current, 'writing_topic': str(index)})
        self.assertEqual(len(self.store.history_paths()), count)
        self.assertTrue(any(json.loads(p.read_text(encoding='utf-8')) == self.good
                            for p in self.store.history_paths()))

    def test_newest_history_restores_data_if_primary_and_rotating_backup_are_broken(self):
        self.store.update(lambda _: copy.deepcopy(self.good))
        latest = {**self.good, 'writing_link_sets': [button_set('새 세트')]}
        self.store.update(lambda _: latest)
        self.path.write_bytes(b'{broken-primary')
        self.store.backup.write_bytes(b'{broken-backup')
        self.assertEqual(self.store.read(), latest)
        self.store.update(lambda value: value)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), latest)
        self.assertEqual(self.path.with_suffix('.json.corrupt').read_bytes(), b'{broken-primary')

    def test_invalid_newest_history_is_skipped(self):
        self.store.update(lambda _: copy.deepcopy(self.good))
        for name, content in (('99990101T000000Z-revision-deadbeef.json', '{}'),
                              ('99990102T000000Z-daily.json', 'broken')):
            (self.store.history_dir / name).write_text(content, encoding='utf-8')
        self.path.unlink()
        self.assertEqual(self.store.read(), self.good)

    def test_backup_failure_blocks_change_before_original_settings_are_replaced(self):
        self.store.update(lambda _: copy.deepcopy(self.good))
        original = self.path.read_bytes()
        with patch.object(self.store, '_checkpoint', side_effect=OSError('fixture backup unavailable')):
            with self.assertRaises(OSError):
                self.store.update(lambda _: {'writing_link_sets': []})
        self.assertEqual(self.path.read_bytes(), original)

    def test_daily_and_recent_revision_retention_are_independent(self):
        with patch.object(self.store, 'DAILY_HISTORY_LIMIT', 2), \
             patch.object(self.store, 'REVISION_HISTORY_LIMIT', 3):
            for day in range(1, 6):
                with patch('settings_persistence.datetime') as clock:
                    clock.now.return_value = datetime(2026, 10, day, 12, tzinfo=timezone.utc)
                    self.store.update(lambda _: {'writing_link_sets': [button_set(f'날짜 {day}')]})
        self.assertEqual(len(list(self.store.history_dir.glob('*-daily.json'))), 2)
        self.assertEqual(len(list(self.store.history_dir.glob('*-revision-*.json'))), 3)

    def test_missing_primary_does_not_trigger_ancient_legacy_migration_over_history(self):
        import main
        self.store.update(lambda _: copy.deepcopy(self.good))
        self.path.unlink()
        legacy = self.path.parent / 'legacy'
        legacy.mkdir()
        (legacy / 'app_state.json').write_text('{"old":true}', encoding='utf-8')
        with patch.object(main, 'STATE_FILE', self.path), patch.object(main, 'SCRIPT_DIR', legacy):
            main.migrate_legacy_app_state()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.store.read(), self.good)


class NamedSetTransactionTests(unittest.TestCase):
    def setUp(self):
        import main
        self.main = main
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'app_state.json'
        self.patch = patch.object(main, 'STATE_FILE', self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_stale_app_adds_one_set_without_replacing_other_library_or_blog_preferences(self):
        first, second = button_set('첫 세트'), button_set('두 번째 세트')
        self.main.AppStateStore.save_writing_link_set(first, expected=None, identity='wordpress')
        self.main.AppStateStore.update_fields(blog_writing_preferences={
            'tistory:tistory_2': {'prompt_id': 'keep-prompt', 'thumbnail_preset': 2}})
        saved = self.main.AppStateStore.save_writing_link_set(second, expected=None, identity='blogspot:blogspot_1')
        self.assertEqual(saved['writing_link_sets'], [first, second])
        self.assertEqual(saved['blog_writing_preferences']['tistory:tistory_2']['prompt_id'], 'keep-prompt')
        self.assertEqual(saved['blog_writing_preferences']['blogspot:blogspot_1']['writing_link_set_name'], second['name'])

    def test_changed_same_name_requires_fresh_confirmation_and_leaves_disk_unchanged(self):
        first = button_set('가족 버튼')
        changed = button_set('가족 버튼', 'https://fixture.example/new')
        self.main.AppStateStore.save_writing_link_set(first, expected=None, identity=None)
        saved = self.main.AppStateStore.save_writing_link_set(changed, expected=first, identity=None)
        before = self.path.read_bytes()
        with self.assertRaises(SettingsReadError):
            self.main.AppStateStore.save_writing_link_set(first, expected=first, identity=None)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(saved['writing_link_sets'], [changed])

    def test_general_partial_save_cannot_clear_or_edit_named_library(self):
        first = button_set('가족 버튼')
        self.main.AppStateStore.save_writing_link_set(first, expected=None, identity=None)
        for value in ([], [button_set('가족 버튼', 'https://stale.example')]):
            self.main.AppStateStore.update_fields(writing_link_sets=value)
            self.assertEqual(SettingsFile(self.path).read()['writing_link_sets'], [first])

    def test_read_only_state_cannot_claim_named_set_was_saved(self):
        with patch.object(self.main.AppStateStore, '_read_only_paths', {str(self.path.resolve())}):
            with self.assertRaises(SettingsReadError):
                self.main.AppStateStore.save_writing_link_set(button_set('A'), expected=None, identity=None)
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
