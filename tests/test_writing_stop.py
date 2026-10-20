"""Foreground stop gates; no real AI requests, browser or publication."""
from contextlib import ExitStack
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import main
from writing_run import ScopedWritingQueue, WritingCancelled


class WritingStopTests(unittest.TestCase):
    def setUp(self):
        self.scope = ScopedWritingQueue(queue.Queue())
        self.settings = main.WordPressSettings()
        self.log = patch.object(main, 'append_runtime_log')
        self.log.start()
        self.addCleanup(self.log.stop)

    def test_cancelled_article_starts_no_request(self):
        self.scope.cancel()
        worker = main.ArticleGenerationWorker(self.settings, 'topic', 'keyword', '', self.scope)
        with patch.object(main, 'generate_text_with_writing_model') as generate:
            worker.run()
        generate.assert_not_called()
        self.assertTrue(self.scope.target.empty())

    def test_stop_during_title_request_never_starts_the_body_request(self):
        worker = main.ArticleGenerationWorker(self.settings, 'topic', 'keyword', '', self.scope)
        def title(*args, **kwargs):
            self.scope.cancel()
            return 'title', 'fake'
        with patch.object(main, 'generate_text_with_writing_model', side_effect=title) as generate:
            worker.run()
        self.assertEqual(generate.call_count, 1)
        self.assertFalse(any(event[1][1][0] == 'article_done' for event in self.scope.target.queue))

    def test_stop_during_thumbnail_prompt_skips_paid_image_generation(self):
        worker = main.ThumbnailAIWorker(self.settings, 'title', 'keyword', '<p>body</p>', self.scope)
        with patch.object(worker, '_run_codex_cli', side_effect=lambda prompt: self.scope.cancel() or 'prompt'), \
             patch.object(main, 'ImagenClient') as imagen:
            worker.run()
        imagen.assert_not_called()

    def test_cancelled_wordpress_client_never_sends_another_http_request(self):
        client = main.WordPressClient(main.WordPressSettings(blog_url='https://example.com'))
        client.cancel_event = self.scope.cancel_event
        self.scope.cancel()
        with patch.object(main, 'urlopen') as request:
            for action in (lambda: client._request_json('/posts', 'POST', {}),
                           lambda: client._request_form('/metadata', body={}),
                           lambda: client.upload_media('/missing/not-read.png', 'title')):
                with self.assertRaises(WritingCancelled):
                    action()
        request.assert_not_called()

    def test_stop_during_wordpress_post_keeps_result_accounting_but_skips_metadata_and_next_platform(self):
        settings = main.WordPressSettings(blog_url='https://example.com', username='demo', app_password='fake',
                                         target_platforms=['wordpress', 'blogspot'])
        worker = main.PublishPipelineWorker(settings, 'title', '<p>body</p>', '', 'keyword', [], '', '', self.scope)
        with patch.object(main, 'WordPressClient') as factory, \
             patch.object(main.DailyPublishLimitStore, 'ensure_can_publish'), \
             patch.object(main, 'run_blogspot_playwright_automation') as blogger:
            client = factory.return_value
            client.publish_post.side_effect = lambda **kwargs: self.scope.cancel() or {'post_id': 42}
            worker.run()
        client.publish_post.assert_called_once()
        client.update_rank_math_metadata.assert_not_called()
        blogger.assert_not_called()

    def test_stop_after_wordpress_tags_prevents_public_post(self):
        settings = main.WordPressSettings(blog_url='https://example.com', username='demo', app_password='fake',
                                         target_platforms=['wordpress'])
        worker = main.PublishPipelineWorker(settings, 'title', '<p>body</p>', '', 'keyword', [], '', '', self.scope)
        with patch.object(main, 'WordPressClient') as factory, \
             patch.object(main.DailyPublishLimitStore, 'ensure_can_publish'):
            client = factory.return_value
            client.ensure_tag_ids.side_effect = lambda tags: self.scope.cancel() or []
            worker.run()
        client.publish_post.assert_not_called()

    def test_responsive_actions_keep_view_and_stop_adjacent(self):
        for width, variant in ((1100, False), (500, False), (1100, True), (500, True)):
            app = SimpleNamespace(_is_tistory_v2_writing=lambda: variant)
            for name in ('publish_pipeline_button', 'queue_post_button', 'tistory_retry_button',
                         'open_published_post_button', 'writing_stop_button'):
                setattr(app, name, Mock())
            row = SimpleNamespace(_action_columns=None, _get_widget_scaling=lambda: 1, grid_columnconfigure=Mock())
            main.KeywordApp._layout_writing_publish_actions(app, row, width)
            if variant:
                app.writing_stop_button.grid_remove.assert_called_once()
            else:
                view = app.open_published_post_button.grid.call_args.kwargs
                stop = app.writing_stop_button.grid.call_args.kwargs
                self.assertEqual(view['row'], stop['row'])
                self.assertEqual(view['column'] + 1, stop['column'])


class BloggerStopTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.scope = ScopedWritingQueue(queue.Queue())
        self.page, self.context = MagicMock(), MagicMock()
        self.context.pages = [self.page]
        self.page.url = 'https://www.blogger.com/blog/posts/123'
        self.publish, self.title = MagicMock(), MagicMock()
        self.page.get_by_role.side_effect = lambda role, **kwargs: (
            self.publish if kwargs.get('name') == '게시' else self.title if role == 'textbox' else MagicMock())
        self.stack.enter_context(patch('playwright.sync_api.sync_playwright', return_value=MagicMock()))
        self.launch = self.stack.enter_context(patch.object(main, 'launch_blogspot_persistent_context', return_value=self.context))
        self.stack.enter_context(patch.object(main, 'load_blogspot_storage_state', return_value={}))
        self.stack.enter_context(patch.object(main, 'save_blogspot_storage_state'))
        self.stack.enter_context(patch.object(main, 'append_runtime_log'))
        self.stack.enter_context(patch.object(main, 'GOOGLE_IMAGE_COLLAGE_ENABLED', False))
        self.stack.enter_context(patch.object(main, 'is_blogspot_dashboard_ready', return_value=True))
        self.stack.enter_context(patch.object(main, 'prepare_blogspot_html_and_images', return_value=('<p>body</p>', [])))
        self.stack.enter_context(patch.object(main, 'fill_blogspot_html_editor'))
        editor = MagicMock()
        editor.evaluate.return_value = '<p>body</p>'
        self.stack.enter_context(patch.object(main, 'open_blogspot_html_editor', return_value=editor))
        self.tags = self.stack.enter_context(patch.object(main, 'fill_blogspot_labels', return_value=1))
        self.real_confirm = main.confirm_blogspot_publish_dialog
        self.confirm = self.stack.enter_context(patch.object(main, 'confirm_blogspot_publish_dialog'))
        self.reserve = self.stack.enter_context(patch.object(main.DailyPublishLimitStore, 'reserve_publish', return_value='slot'))
        self.record = self.stack.enter_context(patch.object(main.DailyPublishLimitStore, 'record_reserved_success', return_value=3))
        self.release = self.stack.enter_context(patch.object(main.DailyPublishLimitStore, 'cancel_reservation'))
        self.stack.enter_context(patch.object(main, 'cleanup_generated_upload_images'))

    def run_browser(self):
        return main.run_blogspot_playwright_automation('title', '<p>body</p>', [], self.scope, blog_id='123')

    def test_pre_cancelled_run_never_reserves_or_launches(self):
        self.scope.cancel()
        with self.assertRaises(WritingCancelled):
            self.run_browser()
        self.reserve.assert_not_called()
        self.launch.assert_not_called()

    def test_stop_during_editor_wait_prevents_input(self):
        self.title.wait_for.side_effect = lambda **kwargs: self.scope.cancel()
        with self.assertRaises(WritingCancelled):
            self.run_browser()
        self.title.fill.assert_not_called()
        self.confirm.assert_not_called()
        self.context.close.assert_called_once()
        self.release.assert_called_once_with('slot')

    def test_stop_after_labels_prevents_publish_and_releases_unused_slot(self):
        self.tags.side_effect = lambda *args: self.scope.cancel() or 1
        with self.assertRaises(WritingCancelled):
            self.run_browser()
        self.publish.first.click.assert_not_called()
        self.confirm.assert_not_called()
        self.record.assert_not_called()
        self.release.assert_called_once_with('slot')
        self.context.close.assert_called_once()

    def test_stop_during_publish_button_wait_cannot_click_when_button_appears(self):
        self.publish.first.wait_for.side_effect = lambda **kwargs: self.scope.cancel()
        with self.assertRaises(WritingCancelled):
            self.run_browser()
        self.publish.first.click.assert_not_called()
        self.confirm.assert_not_called()

    def test_already_sent_public_request_consumes_slot_after_stop(self):
        def sent(*args, **kwargs):
            kwargs['on_public_publish_clicked']()
            self.scope.cancel()
        self.confirm.side_effect = sent
        with self.assertRaises(WritingCancelled):
            self.run_browser()
        self.record.assert_called_once_with('blogspot', '123', 'slot')
        self.release.assert_called_once_with('')
        self.context.close.assert_called_once()

    def test_final_confirmation_wait_is_cancellable(self):
        self.page.wait_for_timeout.side_effect = lambda delay: self.scope.cancel()
        with patch.object(main, '_first_visible_blogspot_locator', return_value=None), \
             self.assertRaises(WritingCancelled):
            self.real_confirm(self.page, cancel_event=self.scope.cancel_event)

    def test_stop_after_first_image_never_uploads_second_image(self):
        with patch.object(main, 'prepare_blogspot_html_and_images', return_value=('<p>body</p>', ['/fake/a.png', '/fake/b.png'])), \
             patch.object(main, 'insert_blogspot_image_slot_markers', return_value=('<p>body</p>', ['a', 'b'])), \
             patch.object(main, 'focus_blogspot_image_slot'), \
             patch.object(main, 'upload_blogspot_images', side_effect=lambda *args: self.scope.cancel() or 1) as upload, \
             self.assertRaises(WritingCancelled):
            self.run_browser()
        self.assertEqual(upload.call_count, 1)
        self.confirm.assert_not_called()
        self.context.close.assert_called_once()

    def test_manual_wait_inherits_foreground_cancellation(self):
        from threading import Thread
        errors = []
        def wait():
            try:
                main.wait_for_manual_publish_completion('blogspot', 'stop-test-scope', self.page, self.scope)
            except Exception as exc:
                errors.append(exc)
        thread = Thread(target=wait)
        thread.start()
        self.assertEqual(self.scope.target.get(timeout=1)[1][1][0], 'manual_publish_waiting')
        self.scope.cancel()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], WritingCancelled)
        self.assertNotIn('stop-test-scope', main.ManualPublishCompletionStore.waiting_scopes('blogspot'))


if __name__ == '__main__':
    unittest.main()
