import queue
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from writing_run import ScopedWritingQueue, WritingCancelled, active_writing_event, check_writing_cancelled


class WritingRunTests(unittest.TestCase):
    def test_current_event_is_unwrapped(self):
        target = queue.Queue()
        scope = ScopedWritingQueue(target)
        expected = ("publish_pipeline_done", {"tistory": {"title": "테스트"}})
        scope.put(expected)
        self.assertEqual(active_writing_event(target.get_nowait(), scope), expected)

    def test_cancel_drops_future_events_and_fences_already_queued_result(self):
        target = queue.Queue()
        scope = ScopedWritingQueue(target)
        scope.put(("publish_pipeline_done", {}))
        pending = target.get_nowait()
        scope.cancel()
        scope.put(("tistory_automation_done", {}))
        scope.put_nowait(("publish_pipeline_error", "late"))
        self.assertTrue(target.empty())
        self.assertIsNone(active_writing_event(pending, scope))
        with self.assertRaises(WritingCancelled):
            check_writing_cancelled(scope.cancel_event)

    def test_old_run_cannot_affect_new_run_with_same_keyword(self):
        target = queue.Queue()
        old, new = ScopedWritingQueue(target), ScopedWritingQueue(target)
        old.put(("article_done", {"keyword": "같은 키워드"}))
        new.put(("article_done", {"keyword": "같은 키워드"}))
        self.assertIsNone(active_writing_event(target.get_nowait(), new))
        self.assertEqual(active_writing_event(target.get_nowait(), new)[0], "article_done")

    def test_unrelated_events_pass_through_after_stop(self):
        target = queue.Queue()
        scope = ScopedWritingQueue(target)
        scope.cancel()
        for event in (("update_progress", 0.5), ("naver_kin_done", {}), ("automation_publish_done", {})):
            self.assertIs(active_writing_event(event, scope), event)

    def test_optional_cancellation_does_not_change_legacy_runs(self):
        check_writing_cancelled()
        check_writing_cancelled(threading.Event())

    def test_scheduled_v2_callback_cannot_restart_after_reset(self):
        import main
        app = SimpleNamespace(_writing_run_queue=None, result_queue=queue.Queue())
        app._is_tistory_v2_writing = lambda: True
        app._writing_result_queue = lambda: main.KeywordApp._writing_result_queue(app)
        pending = []
        app.after = lambda delay, callback: pending.append(callback)
        action = Mock()
        main.KeywordApp._after_writing(app, 450, action)
        app._writing_run_queue.cancel()
        app._writing_run_queue = None
        app._writing_result_queue()
        pending[0]()
        action.assert_not_called()

    def test_non_v2_uses_original_queue_and_timer(self):
        import main
        app = SimpleNamespace(_writing_run_queue=None, result_queue=queue.Queue())
        app._is_tistory_v2_writing = lambda: False
        self.assertIs(main.KeywordApp._writing_result_queue(app), app.result_queue)
        app.after = Mock(return_value="job")
        action = Mock()
        self.assertEqual(main.KeywordApp._after_writing(app, 450, action, "argument"), "job")
        app.after.assert_called_once_with(450, action, "argument")


if __name__ == "__main__":
    unittest.main()
