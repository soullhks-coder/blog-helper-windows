"""No live blogs: pre-transfer picker retries and local recovery ownership."""
import json
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from playwright.sync_api import TimeoutError as PlaywrightTimeout

import main
import blogspot_upload as upload
from blogspot_recovery import save_failed_editor, wait_for_failed_editor
from writing_run import WritingCancelled


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.state = SimpleNamespace(visible=False, ready=False, polls=0, activations=0)
        self.page, self.option, self.browse, self.frame = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        self.page.is_closed.return_value = False
        self.frame.get_by_role.return_value.first = self.browse
        self.page.frame_locator.return_value.first = self.frame
        self.pickers = Mock()
        self.pickers.count.side_effect = lambda: int(self.state.visible)
        self.menu = MagicMock()
        self.menu.filter.return_value.first = self.option
        self.page.locator.side_effect = lambda selector: self.pickers if selector == upload.PICKER_SELECTOR else self.menu
        self.browse.is_visible.side_effect = lambda: self.state.visible and self.state.ready
        self.browse.is_enabled.return_value = True
        self.option.press.side_effect = lambda *_args: self.activate()
        self.option.click.side_effect = lambda **_kwargs: self.activate()
        self.page.keyboard.press.side_effect = lambda *_args: setattr(self.state, 'visible', False)
        self.page.wait_for_timeout.side_effect = lambda _delay: setattr(self.state, 'polls', self.state.polls + 1)
        self.find, self.describe, self.log, self.cancel = Mock(return_value=Mock()), Mock(return_value='fixture'), Mock(), Mock()

    def activate(self):
        self.state.activations += 1

    def run_picker(self):
        return upload.open_image_picker(self.page, find_image_button=self.find, describe_state=self.describe,
            log=self.log, check_cancel=self.cancel, wait_seconds=.25)

    def open_ready(self):
        self.state.visible = self.state.ready = True

    def test_complete_is_logged_only_after_picker_and_enabled_browse_are_ready(self):
        def activate():
            self.assertFalse(any('준비 확인' in c.args[0] for c in self.log.call_args_list))
            self.open_ready()
        self.option.press.side_effect = lambda *_args: activate()
        self.assertEqual(self.run_picker(), (self.frame, self.browse))
        self.assertIn('준비 확인', self.log.call_args.args[0])
        self.page.expect_file_chooser.assert_not_called()

    def test_swallowed_enter_reopens_menu_and_uses_pointer_once(self):
        self.option.click.side_effect = lambda **_kwargs: self.open_ready()
        self.assertEqual(self.run_picker(), (self.frame, self.browse))
        self.option.press.assert_called_once_with('Enter')
        self.option.click.assert_called_once_with(force=True)
        self.assertEqual(self.find.call_count, 2)

    def test_three_failed_opens_are_bounded_and_never_select_files(self):
        with self.assertRaisesRegex(upload.BlogspotImageUploadError, '3회'):
            self.run_picker()
        self.assertEqual(self.state.activations, 3)
        self.assertLess(self.state.polls, 12)
        self.page.expect_file_chooser.assert_not_called()
        self.assertFalse(any('준비 확인' in c.args[0] for c in self.log.call_args_list))

    def test_existing_ready_picker_is_reused_without_opening_a_second_one(self):
        self.open_ready()
        self.run_picker()
        self.find.assert_not_called()
        self.page.keyboard.press.assert_not_called()

    def test_late_existing_picker_is_waited_for_without_menu_activation(self):
        self.state.visible = True
        self.page.wait_for_timeout.side_effect = lambda _delay: self.open_ready()
        self.run_picker()
        self.find.assert_not_called()
        self.page.keyboard.press.assert_not_called()

    def test_stuck_picker_is_not_overlaid_with_another_picker(self):
        self.state.visible = True
        self.page.keyboard.press.side_effect = None
        with self.assertRaisesRegex(upload.BlogspotImageUploadError, '중복 창'):
            self.run_picker()
        self.find.assert_not_called()

    def test_closed_browser_is_not_retried(self):
        self.page.is_closed.return_value = True
        with self.assertRaisesRegex(upload.BlogspotImageUploadError, '닫혔'):
            self.run_picker()
        self.find.assert_not_called()

    def test_stop_during_readiness_wait_cannot_reactivate_upload_menu(self):
        def stop(_delay):
            self.cancel.side_effect = WritingCancelled('stop')
        self.page.wait_for_timeout.side_effect = stop
        with self.assertRaises(WritingCancelled):
            self.run_picker()
        self.assertEqual(self.state.activations, 1)

    def test_disabled_browse_is_not_mistaken_for_a_ready_picker(self):
        self.open_ready()
        self.browse.is_enabled.return_value = False
        self.page.wait_for_timeout.side_effect = lambda _delay: setattr(self.browse.is_enabled, 'return_value', True)
        self.run_picker()
        self.find.assert_not_called()

    def test_transient_menu_timeout_can_recover(self):
        self.option.wait_for.side_effect = [PlaywrightTimeout('late menu'), None]
        self.option.click.side_effect = lambda **_kwargs: self.open_ready()
        self.run_picker()
        self.assertEqual(self.find.call_count, 2)

    def test_replaced_picker_frame_during_readiness_check_can_recover(self):
        checks = 0
        def visible():
            nonlocal checks
            checks += 1
            if checks == 1:
                raise PlaywrightTimeout('frame replaced')
            return self.state.visible
        self.pickers.count.side_effect = visible
        self.option.press.side_effect = lambda *_args: self.open_ready()
        self.run_picker()
        self.option.press.assert_called_once()

    def test_error_after_file_transfer_never_reopens_or_retransmits(self):
        page, frame, browse = MagicMock(), Mock(), Mock()
        page.expect_file_chooser.return_value.__enter__.return_value.value.set_files.side_effect = PlaywrightTimeout('uncertain transfer')
        source = Path(main.SCRIPT_DIR) / 'assets' / 'blogspot-logo.png'
        with patch.object(upload, 'open_image_picker', return_value=(frame, browse)) as opening, \
             patch.object(main, 'collect_blogspot_compose_image_sources', return_value=[]), \
             patch.object(main, 'append_runtime_log'), \
             patch.object(main, 'webp_upload_files') as copies:
            copies.return_value.__enter__.return_value = [str(source)]
            with self.assertRaises(upload.BlogspotImageUploadError):
                main.upload_blogspot_images(page, [str(source)], queue.Queue())
            opening.assert_called_once()
            page.expect_file_chooser.return_value.__enter__.return_value.value.set_files.assert_called_once()

    def test_stop_after_file_chooser_opens_prevents_the_file_transfer(self):
        from writing_run import ScopedWritingQueue
        events = ScopedWritingQueue(queue.Queue())
        page = MagicMock()
        page.expect_file_chooser.return_value.__exit__.side_effect = lambda *_args: events.cancel()
        source = Path(main.SCRIPT_DIR) / 'assets' / 'blogspot-logo.png'
        with patch.object(upload, 'open_image_picker', return_value=(Mock(), Mock())) as opening, \
             patch.object(main, 'collect_blogspot_compose_image_sources', return_value=[]), \
             patch.object(main, 'append_runtime_log'), patch.object(main, 'webp_upload_files') as copies:
            copies.return_value.__enter__.return_value = [str(source)]
            with self.assertRaises(WritingCancelled):
                main.upload_blogspot_images(page, [str(source)], events)
            opening.assert_called_once()
            page.expect_file_chooser.return_value.__enter__.return_value.value.set_files.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def test_source_and_live_html_and_all_images_are_preserved_without_changing_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.webp'
            source.write_bytes(b'fixture')
            page = MagicMock()
            page.locator.return_value.count.return_value = 1
            page.locator.return_value.first.evaluate.return_value = '<p>현재 편집 내용</p>'
            folder = save_failed_editor(page, root / 'recovery', title='표고버섯', article_html='<p>원문</p>',
                image_paths=[str(source)], blog_id='123', profile_scope='first', completed_images=2)
            payload = json.loads((folder / 'recovery.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['article_html'], '<p>원문</p>')
            self.assertEqual(payload['editor_html'], '<p>현재 편집 내용</p>')
            self.assertEqual(payload['status'], 'incomplete_not_auto_published')
            self.assertEqual(payload['completed_images'], 2)
            self.assertEqual((folder / payload['images'][0]).read_bytes(), source.read_bytes())
            page.get_by_role.assert_not_called()

    def test_disconnected_editor_still_preserves_source_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            page = Mock()
            page.locator.side_effect = PlaywrightTimeout('closed')
            folder = save_failed_editor(page, directory, title='제목', article_html='<p>원문</p>', image_paths=[],
                blog_id='123', profile_scope='first', completed_images=0)
            payload = json.loads((folder / 'recovery.json').read_text(encoding='utf-8'))
            self.assertEqual(payload['editor_html'], '')
            self.assertNotIn('cookies', payload)
            self.assertNotIn('storage_state', payload)

    def test_failed_browser_remains_owned_until_user_closes_it_without_any_editor_write(self):
        page, cancel = Mock(), Mock()
        page.is_closed.side_effect = [False, False, True]
        wait_for_failed_editor(page, check_cancel=cancel)
        page.bring_to_front.assert_called_once()
        page.wait_for_timeout.assert_called_once_with(400)
        page.close.assert_not_called()
        page.get_by_role.assert_not_called()
        page.evaluate.assert_not_called()

    def test_stop_while_inspecting_failed_editor_is_propagated(self):
        page = Mock()
        page.is_closed.return_value = False
        with self.assertRaises(WritingCancelled):
            wait_for_failed_editor(page, check_cancel=Mock(side_effect=WritingCancelled('stop')))
        page.bring_to_front.assert_not_called()

    def test_attention_keeps_upload_blocked_and_does_not_free_the_live_worker(self):
        worker = Mock()
        app = SimpleNamespace(_stop_writing_auto_progress=Mock(), publish_progress_bar=Mock(),
            publish_pipeline_button=Mock(), publish_status_label=Mock(), _set_writing_progress=Mock(),
            active_automation_upload_item_id='', pipeline_worker=worker)
        with patch.object(main.messagebox, 'showwarning') as warning:
            main.KeywordApp._handle_blogspot_editor_attention(app, {'error': 'fixture', 'recovery_path': '/tmp/recovery',
                'completed_images': 2, 'total_images': 5})
        self.assertIs(app.pipeline_worker, worker)
        self.assertTrue(app._blogspot_attention_reported)
        self.assertEqual(app.publish_pipeline_button.configure.call_args.kwargs['state'], 'disabled')
        self.assertIn('/tmp/recovery', warning.call_args.args[1])

    def test_background_queue_attention_does_not_leave_foreground_upload_button_disabled(self):
        app = SimpleNamespace(_stop_writing_auto_progress=Mock(), publish_progress_bar=Mock(),
            publish_pipeline_button=Mock(), publish_status_label=Mock(), _set_writing_progress=Mock(),
            active_automation_upload_item_id='background-item', automation_status_label=Mock())
        with patch.object(main.messagebox, 'showwarning'):
            main.KeywordApp._handle_blogspot_editor_attention(app, {'error': 'fixture', 'recovery_path': '',
                'completed_images': 2, 'total_images': 5})
        app.publish_pipeline_button.configure.assert_not_called()
        app._set_writing_progress.assert_not_called()
        self.assertIn('2/5', app.automation_status_label.configure.call_args.kwargs['text'])


if __name__ == '__main__':
    unittest.main()
