"""Offline cancellation gates: never publish after a V2 stop/reset request."""
from __future__ import annotations

import queue
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import main
import tistory_v2_publish
from tistory_v2 import Cancelled
from tistory_v2_editor import wait_for_failed_editor
from writing_run import ScopedWritingQueue, WritingCancelled


BODY = "<h2>첫 소제목</h2><p>직접 작성한 본문입니다.</p>"
SLIDES = [f"/slides/slide-{index}.png" for index in range(10)]


class BrowserCancellationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.cancel = threading.Event()
        self.page, self.context = MagicMock(), MagicMock()
        self.page.url = "https://example.tistory.com/manage/newpost"
        self.page.is_closed.return_value = False
        self.page.evaluate.side_effect = lambda script, *args: (
            {"titleOk": True, "bodyOk": True} if script == "body-script" else None)
        self.context.pages = [self.page]
        self.stack.enter_context(patch("playwright.sync_api.sync_playwright", return_value=MagicMock()))
        self.launch = self.stack.enter_context(patch.object(main, "launch_tistory_persistent_context", return_value=self.context))
        self.stack.enter_context(patch.object(main, "load_tistory_storage_state", return_value={}))
        self.stack.enter_context(patch.object(main, "save_tistory_storage_state"))
        self.stack.enter_context(patch.object(main, "append_runtime_log"))
        self.login = self.stack.enter_context(patch.object(main, "wait_for_tistory_editor", return_value=(self.page, "")))
        self.stack.enter_context(patch.object(main, "collect_tistory_hosted_image_urls", return_value=[]))
        self.collage = self.stack.enter_context(patch("tistory_v2_editor.insert_native_slide_pairs", return_value={
            "ok": True, "slide_count": 10, "collage_count": 5}))
        self.attention = self.stack.enter_context(patch("tistory_v2_editor.wait_for_failed_editor"))
        self.tags = self.stack.enter_context(patch.object(main, "enter_tistory_tags_native"))
        self.complete = self.stack.enter_context(patch.object(main, "click_tistory_complete_native", return_value=True))
        self.draft = self.stack.enter_context(patch.object(main, "click_tistory_draft_save_native", return_value=True))
        self.publish = self.stack.enter_context(patch.object(main, "click_tistory_public_publish_native", return_value=True))

    def run_browser(self, **options):
        return main.run_tistory_playwright_automation(
            self.page.url, "body-script", queue.Queue(),
            tistory_v2_slide_paths=SLIDES, cancel_event=self.cancel, **options)

    def assert_no_write_after_cancel(self):
        self.draft.assert_not_called()
        self.publish.assert_not_called()
        self.attention.assert_not_called()
        self.context.close.assert_called_once()

    def test_cancel_before_start_does_not_launch_chrome(self):
        self.cancel.set()
        with self.assertRaises(WritingCancelled):
            self.run_browser(publish_after_input=True)
        self.launch.assert_not_called()

    def test_login_wait_receives_cancel_checkpoint_and_closes_on_stop(self):
        def login(*args, **kwargs):
            self.cancel.set()
            kwargs["check_cancel"]()
        self.login.side_effect = login
        with self.assertRaises(WritingCancelled):
            self.run_browser(publish_after_input=True)
        self.assert_no_write_after_cancel()
        self.page.evaluate.assert_not_called()

    def test_cancel_during_body_input_skips_images_tags_and_all_save_actions(self):
        def evaluate(script, *args):
            if script == "body-script":
                self.cancel.set()
                return {"titleOk": True, "bodyOk": True}
        self.page.evaluate.side_effect = evaluate
        with self.assertRaises(WritingCancelled):
            self.run_browser(publish_after_input=True)
        self.assert_no_write_after_cancel()
        self.collage.assert_not_called()
        self.tags.assert_not_called()

    def test_cancel_during_collage_skips_tags_and_draft(self):
        def collage(*args, **kwargs):
            self.assertIs(kwargs["cancel"], self.cancel)
            self.cancel.set()
            raise Cancelled("이미지 작업 중단")
        self.collage.side_effect = collage
        with self.assertRaises(WritingCancelled):
            self.run_browser(save_mode=main.TISTORY_SAVE_MODE_DRAFT)
        self.assert_no_write_after_cancel()
        self.tags.assert_not_called()

    def test_cancel_after_tags_prevents_opening_publish_sheet(self):
        self.tags.side_effect = lambda *args: self.cancel.set()
        with self.assertRaises(WritingCancelled):
            self.run_browser(publish_after_input=True)
        self.assert_no_write_after_cancel()
        self.complete.assert_not_called()

    def test_before_public_publish_is_followed_by_final_cancel_gate(self):
        with self.assertRaises(WritingCancelled):
            self.run_browser(publish_after_input=True, before_public_publish=self.cancel.set)
        self.complete.assert_called_once()
        self.assert_no_write_after_cancel()

    def test_captcha_monitor_stops_without_another_click(self):
        def clicked(*args, **kwargs):
            kwargs["on_public_publish_clicked"]()
            self.cancel.set()
            return True
        self.publish.side_effect = clicked
        marked = MagicMock()
        with self.assertRaises(WritingCancelled):
            self.run_browser(publish_after_input=True, on_public_publish_clicked=marked)
        marked.assert_called_once()
        self.publish.assert_called_once()
        self.attention.assert_not_called()
        self.context.close.assert_called_once()

    def test_failure_review_wait_is_cancellable_and_still_closes_context(self):
        self.collage.side_effect = RuntimeError("콜라주 검증 실패")
        def review(page, cancel):
            self.assertIs(cancel, self.cancel)
            cancel.set()
            raise Cancelled("리뷰 중단")
        self.attention.side_effect = review
        with self.assertRaises(WritingCancelled):
            self.run_browser(publish_after_input=True)
        self.context.close.assert_called_once()
        self.publish.assert_not_called()


class PhysicalPublishCancellationTests(unittest.TestCase):
    def test_stop_during_final_button_wait_does_not_mouse_down(self):
        cancel, page, marked = threading.Event(), MagicMock(), MagicMock()
        page.wait_for_timeout.side_effect = lambda duration: cancel.set() if duration == 120 else None
        with patch.object(main, "click_tistory_publish_now_native", return_value=True), \
             patch.object(main, "find_tistory_public_publish_button_rect", return_value={"x": 10, "y": 20}), \
             self.assertRaises(WritingCancelled):
            main.click_tistory_public_publish_native(page, cancel_event=cancel, on_public_publish_clicked=marked)
        page.mouse.down.assert_not_called()
        marked.assert_not_called()

    def test_click_boundary_is_marked_before_mouse_down_can_fail(self):
        page, order = MagicMock(), []
        page.mouse.down.side_effect = lambda: order.append("down")
        with patch.object(main, "click_tistory_publish_now_native", return_value=True), \
             patch.object(main, "find_tistory_public_publish_button_rect", return_value={"x": 10, "y": 20}):
            self.assertTrue(main.click_tistory_public_publish_native(
                page, cancel_event=threading.Event(), on_public_publish_clicked=lambda: order.append("marked")))
        self.assertEqual(order, ["marked", "down"])

    def test_stop_during_draft_button_wait_does_not_mouse_down(self):
        cancel, page = threading.Event(), MagicMock()
        page.wait_for_timeout.side_effect = lambda duration: cancel.set() if duration == 100 else None
        with patch.object(main, "find_tistory_draft_save_button_rect", return_value={"x": 10, "y": 20}), \
             self.assertRaises(WritingCancelled):
            main.click_tistory_draft_save_native(page, cancel_event=cancel)
        page.mouse.down.assert_not_called()

    def test_failed_browser_review_breaks_on_cancel(self):
        cancel, page = threading.Event(), MagicMock()
        page.is_closed.return_value = False
        page.wait_for_timeout.side_effect = lambda duration: cancel.set()
        with self.assertRaises(Cancelled):
            wait_for_failed_editor(page, cancel)
        page.wait_for_timeout.assert_called_once_with(400)

    def test_manual_wait_cancellation_clears_only_its_store_scope(self):
        cancel, events, errors = threading.Event(), queue.Queue(), []
        def run():
            try:
                main.wait_for_manual_publish_completion("tistory", "stop-scope", MagicMock(), events, cancel)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=run)
        worker.start()
        self.assertEqual(events.get(timeout=1)[0], "manual_publish_waiting")
        cancel.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(errors[0], WritingCancelled)
        self.assertNotIn("stop-scope", main.ManualPublishCompletionStore.waiting_scopes("tistory"))
        self.assertTrue(events.empty())


class WorkerCancellationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ("append_runtime_log", "cleanup_generated_upload_images", "cleanup_tistory_automation_files"):
            setattr(self, name, self.stack.enter_context(patch.object(main, name)))
        self.stack.enter_context(patch.object(main, "GOOGLE_IMAGE_COLLAGE_ENABLED", False))
        self.stack.enter_context(patch.object(main, "extract_tistory_prompt_tags", return_value=(BODY, ["테스트"])))
        self.prepare = self.stack.enter_context(patch.object(main, "prepare_tistory_native_attachment_html", return_value=(BODY, {})))
        self.stack.enter_context(patch.object(main, "build_tistory_editor_automation_script", return_value="body-script"))
        self.reserve = self.stack.enter_context(patch.object(main.DailyPublishLimitStore, "reserve_publish", return_value="reserved"))
        self.release = self.stack.enter_context(patch.object(main.DailyPublishLimitStore, "cancel_reservation"))
        self.record = self.stack.enter_context(patch.object(main.DailyPublishLimitStore, "record_reserved_success", return_value=1))
        self.record_uncertain = self.stack.enter_context(patch.object(main.DailyPublishLimitStore, "record_success", return_value=1))
        self.browser = self.stack.enter_context(patch.object(main, "run_tistory_playwright_automation"))
        self.cancel, self.events = threading.Event(), queue.Queue()

    def worker(self, **options):
        return main.TistoryAutomationWorker(
            "제목", BODY, self.events, publish_after_input=True,
            write_url="https://example.tistory.com/manage/newpost", public_blog_url="https://example.tistory.com",
            tistory_v2_slide_paths=SLIDES, cancel_event=self.cancel, **options)

    def assert_no_final_events(self):
        self.assertFalse(any(kind in {"tistory_automation_done", "tistory_automation_error"} for kind, _ in self.events.queue))

    def test_pre_cancelled_worker_never_reserves_or_opens_browser(self):
        worker = self.worker()
        worker.cancel()
        worker.run()
        self.reserve.assert_not_called()
        self.browser.assert_not_called()
        self.cleanup_tistory_automation_files.assert_not_called()
        self.assert_no_final_events()

    def test_queue_scope_event_is_inferred_by_both_workers(self):
        scoped = ScopedWritingQueue(queue.Queue())
        worker = main.TistoryAutomationWorker("제목", BODY, scoped)
        pipeline = main.PublishPipelineWorker(main.WordPressSettings(), "제목", BODY, "", "키워드", [], "slug", "설명", scoped)
        self.assertIs(worker.cancel_event, scoped.cancel_event)
        self.assertIs(pipeline.cancel_event, scoped.cancel_event)
        worker.cancel()
        self.assertTrue(scoped.cancel_event.is_set())

    def test_stop_after_preparation_releases_unused_quota_without_browser(self):
        self.prepare.side_effect = lambda *args: (self.cancel.set() or (BODY, {}))
        self.worker().run()
        self.browser.assert_not_called()
        self.release.assert_any_call("reserved")
        self.record.assert_not_called()
        self.assert_no_final_events()

    def test_stop_before_public_click_returns_shared_reservation(self):
        shared = MagicMock()
        shared.reserve.return_value = {"reservationId": "shared-slot", "date": "2026-10-16", "published": 1, "remaining": 13}
        self.browser.side_effect = lambda *args, **kwargs: (self.cancel.set() or (True, {}))
        with patch.object(main, "is_shared_tistory_blog", return_value=True), \
             patch.object(main, "SharedTistoryPublishLimitClient", return_value=shared), \
             patch.object(main.RemoteAgentConfigStore, "load", return_value=MagicMock()), \
             patch.object(main.DailyPublishLimitStore, "count", return_value=1):
            self.worker().run()
        shared.cancel.assert_called_once_with("shared-slot", "2026-10-16")
        shared.commit.assert_not_called()
        self.assert_no_final_events()

    def test_stop_after_public_click_keeps_shared_reservation(self):
        shared = MagicMock()
        shared.reserve.return_value = {"reservationId": "shared-slot", "date": "2026-10-16", "published": 1, "remaining": 13}
        def publish(*args, **kwargs):
            kwargs["on_public_publish_clicked"]()
            self.cancel.set()
            raise WritingCancelled("중단")
        self.browser.side_effect = publish
        with patch.object(main, "is_shared_tistory_blog", return_value=True), \
             patch.object(main, "SharedTistoryPublishLimitClient", return_value=shared), \
             patch.object(main.RemoteAgentConfigStore, "load", return_value=MagicMock()), \
             patch.object(main.DailyPublishLimitStore, "count", return_value=1):
            worker = self.worker()
            worker.run()
        self.assertTrue(worker.public_publish_clicked)
        shared.cancel.assert_not_called()
        shared.commit.assert_not_called()
        self.assert_no_final_events()

    def test_stop_after_public_click_consumes_uncertain_local_slot(self):
        def publish(*args, **kwargs):
            self.assertIs(kwargs["cancel_event"], self.cancel)
            kwargs["on_public_publish_clicked"]()
            self.cancel.set()
            return True, {"published_url": "https://example.tistory.com/1"}
        self.browser.side_effect = publish
        self.worker().run()
        self.record.assert_not_called()
        self.record_uncertain.assert_called_once_with("tistory", "https://example.tistory.com")
        self.release.assert_any_call("reserved")
        self.assert_no_final_events()

    def test_uncertain_local_slot_is_not_returned_if_persisting_count_fails(self):
        def publish(*args, **kwargs):
            kwargs["on_public_publish_clicked"]()
            self.cancel.set()
            raise WritingCancelled("중단")
        self.browser.side_effect = publish
        self.record_uncertain.side_effect = OSError("디스크 오류")
        self.worker().run()
        self.record_uncertain.assert_called_once()
        self.assertNotIn(unittest.mock.call("reserved"), self.release.call_args_list)
        self.assert_no_final_events()

    def test_cancelled_pipeline_does_not_hydrate_save_or_emit_done(self):
        worker = main.PublishPipelineWorker(main.WordPressSettings(target_platforms=["tistory"]),
            "제목", BODY, "", "키워드", [], "slug", "설명", self.events,
            tistory_v2=True, cancel_event=self.cancel)
        worker.cancel()
        with patch.object(main, "hydrate_inline_image_placeholders") as hydrate:
            worker.run()
        hydrate.assert_not_called()
        self.assertTrue(self.events.empty())

    def test_cancel_during_pipeline_export_never_writes_draft_or_done(self):
        def export(*args, **kwargs):
            self.assertIs(kwargs["cancel_event"], self.cancel)
            self.cancel.set()
            return {"paths": list(map(Path, SLIDES)), "article_html": BODY}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main, "TISTORY_DRAFT_FILE", Path(directory) / "draft.html"), \
             patch.object(tistory_v2_publish, "export_edited_slide_deck", side_effect=export):
            worker = main.PublishPipelineWorker(main.WordPressSettings(target_platforms=["tistory"], tistory_blog_url="https://example.tistory.com"),
                "제목", BODY, "", "키워드", [], "slug", "설명", self.events,
                tistory_v2=True, tistory_v2_deck={"slides": []}, cancel_event=self.cancel)
            worker.run()
            self.assertFalse(main.TISTORY_DRAFT_FILE.exists())
        self.assertFalse(any(kind in {"publish_pipeline_done", "publish_pipeline_error"} for kind, _ in self.events.queue))


if __name__ == "__main__":
    unittest.main()
