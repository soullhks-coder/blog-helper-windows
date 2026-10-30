"""Review publishes prepare the current representative but never publish it."""
from contextlib import ExitStack
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import main


class ReviewThumbnailTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'current-liver.png'
        self.path.write_bytes(b'current-liver-thumbnail')
        self.page = Mock()
        self.events = queue.Queue()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.complete = self.stack.enter_context(patch.object(main, 'click_tistory_complete_native', return_value=True))
        self.attach = self.stack.enter_context(patch.object(main, 'attach_tistory_representative_image_file', return_value=True))
        self.publish = self.stack.enter_context(patch.object(main, 'click_tistory_public_publish_native'))
        self.stack.enter_context(patch.object(main, 'append_runtime_log'))

    def test_prepares_current_file_with_format_and_cancel_options_without_publication(self):
        cancel = threading.Event()
        main.prepare_tistory_publish_thumbnail(self.page, str(self.path), self.events,
            upload_webp=True, cancel_event=cancel)
        self.complete.assert_called_once_with(self.page, self.events, cancel_event=cancel)
        self.attach.assert_called_once_with(self.page, str(self.path.resolve()), self.events, upload_webp=True)
        self.publish.assert_not_called()

    def test_missing_current_file_never_opens_panel_or_reuses_an_old_image(self):
        self.path.unlink()
        with self.assertRaisesRegex(RuntimeError, '대표이미지 파일'):
            main.prepare_tistory_publish_thumbnail(self.page, str(self.path), self.events)
        self.complete.assert_not_called()
        self.attach.assert_not_called()
        self.publish.assert_not_called()

    def test_failure_to_open_panel_prevents_attachment_and_publication(self):
        self.complete.return_value = False
        with self.assertRaisesRegex(RuntimeError, '발행 설정창'):
            main.prepare_tistory_publish_thumbnail(self.page, str(self.path), self.events)
        self.attach.assert_not_called()
        self.publish.assert_not_called()

    def test_unverified_representative_is_not_reported_ready(self):
        self.attach.return_value = False
        with self.assertRaisesRegex(RuntimeError, '대표이미지 교체'):
            main.prepare_tistory_publish_thumbnail(self.page, str(self.path), self.events)
        self.publish.assert_not_called()

    def test_source_change_during_attachment_prevents_ready_result(self):
        self.attach.side_effect = lambda *args, **kwargs: self.path.write_bytes(b'another-article') or True
        with self.assertRaisesRegex(RuntimeError, '원본 파일이 바뀌었습니다'):
            main.prepare_tistory_publish_thumbnail(self.page, str(self.path), self.events)
        self.publish.assert_not_called()

    def test_cancel_prevents_all_browser_actions(self):
        cancel = threading.Event()
        cancel.set()
        from writing_run import WritingCancelled
        with self.assertRaises(WritingCancelled):
            main.prepare_tistory_publish_thumbnail(self.page, str(self.path), self.events, cancel_event=cancel)
        self.complete.assert_not_called()
        self.attach.assert_not_called()
        self.publish.assert_not_called()

    def test_worker_retains_representative_for_manual_review_but_not_draft_only(self):
        for review in (True, False):
            with self.subTest(review=review), ExitStack() as stack:
                stack.enter_context(patch.object(main, 'GOOGLE_IMAGE_COLLAGE_ENABLED', False))
                stack.enter_context(patch.object(main, 'DAILY_PUBLISH_COUNTS_FILE', self.root / f'counts-{review}.json'))
                stack.enter_context(patch.object(main, 'cleanup_tistory_automation_files'))
                run = stack.enter_context(patch.object(main, 'run_tistory_playwright_automation', return_value=(True, {
                    'save_mode': main.TISTORY_SAVE_MODE_PUBLISH if review else main.TISTORY_SAVE_MODE_DRAFT,
                    'manual_completed': review})))
                worker = main.TistoryAutomationWorker(
                    'Current liver article', '<p>Body</p>', queue.Queue(), thumbnail_path=str(self.path),
                    write_url='https://fixture.tistory.com/manage/newpost',
                    public_blog_url='https://fixture.tistory.com', upload_webp=False,
                    auto_publish=not review, save_mode=main.TISTORY_SAVE_MODE_DRAFT,
                    publish_after_input=False)
                worker.run()
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs['representative_image_path'], str(self.path) if review else '')
                self.assertFalse(run.call_args.kwargs['publish_after_input'])


if __name__ == '__main__':
    unittest.main()
