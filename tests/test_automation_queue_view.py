"""Large queue pagination, refresh coalescing and paint reentrancy safeguards."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import main
from automation_queue_view import PAGE_SIZE, page_window


class QueueViewTests(unittest.TestCase):
    def test_500_jobs_are_shown_in_reservation_order_in_bounded_pages_without_mutation(self):
        items = [{'id':str(i), 'scheduled_at':i, 'v2_config':{'review_publish':bool(i % 2)}} for i in reversed(range(500))]
        before = copy.deepcopy(items)
        visited = []
        for page in range(25):
            rows, actual, pages, start = page_window(items, page)
            self.assertEqual((actual, pages, start), (page, 25, page * PAGE_SIZE))
            self.assertEqual(len(rows), PAGE_SIZE)
            visited.extend(row['id'] for row in rows)
        self.assertEqual(visited, [str(i) for i in range(500)])
        self.assertEqual(items, before)
        self.assertEqual(page_window(items[:1], 24)[1:3], (0, 1))
        self.assertEqual(page_window([]), ([], 0, 1, 0))

    def test_burst_requests_share_one_scheduled_redraw(self):
        app = SimpleNamespace(_automation_queue_refresh_job=None, after=Mock(return_value='refresh-id'),
                              _run_scheduled_automation_queue_refresh=Mock())
        for _ in range(24):
            main.KeywordApp._schedule_automation_queue_refresh(app)
        app.after.assert_called_once_with(60, app._run_scheduled_automation_queue_refresh)
        app._refresh_automation_queue = Mock()
        main.KeywordApp._run_scheduled_automation_queue_refresh(app)
        self.assertIsNone(app._automation_queue_refresh_job)
        app._refresh_automation_queue.assert_called_once()

    def test_worker_event_refresh_is_deferred_before_any_expensive_widget_work(self):
        app = SimpleNamespace(automation_list=object(), _in_result_queue_poll=True,
                              _schedule_automation_queue_refresh=Mock())
        main.KeywordApp._refresh_automation_queue(app)
        app._schedule_automation_queue_refresh.assert_called_once()

    def test_nested_theme_paint_does_not_reenter_tree_walk_and_releases_guard(self):
        class Widget:
            def __init__(self): self.values = {}
            def cget(self, name): return self.values.get(name)
            def configure(self, **values): self.values.update(values)
        app = SimpleNamespace(shell_frame=Widget(), sidebar_frame=Widget(), sidebar_title=Widget(),
            sidebar_version_label=Widget(), wordpress_settings=SimpleNamespace(app_theme='화이트테마', app_title='Fixture'),
            _normalize_app_theme=lambda _value:'화이트테마', _defer_while_text_composing=lambda *args:False,
            _theme_paint_last_run=0, _theme_paint_refresh_job=None, _theme_palette=lambda:{
                'shell':'white','sidebar':'white','accent':'blue','muted':'gray'},
            configure=lambda **values:None, cget=lambda _name:None, _current_page_frame=lambda:object(),
            _refresh_sidebar_navigation_styles=Mock(), _apply_white_contrast_overrides=Mock(),
            _refresh_writing_section_completion_styles=Mock())
        calls = []
        def paint(widget, palette):
            calls.append(widget)
            main.KeywordApp._finish_theme_paint(app, force=True)
        app._retint_widget_tree = paint
        app._finish_theme_paint = lambda **kwargs: main.KeywordApp._finish_theme_paint(app, **kwargs)
        main.KeywordApp._finish_theme_paint(app, force=True)
        self.assertEqual(len(calls), 2)
        self.assertFalse(app._theme_paint_in_progress)

    def test_event_time_budget_yields_without_losing_or_reordering_payloads(self):
        import queue
        from unittest.mock import patch
        events = queue.Queue()
        for i in range(10): events.put(('automation_v2_article_ready', {'id':i}))
        handled = []
        app = SimpleNamespace(result_queue=events, _handle_automation_v2_article_ready=handled.append,
                              after=Mock(), _poll_queue=Mock())
        with patch.object(main.time, 'monotonic', side_effect=[1000, 1000.020]):
            main.KeywordApp._poll_queue(app)
        self.assertEqual(handled, [{'id':0}])
        self.assertEqual(events.qsize(), 9)
        self.assertFalse(app._in_result_queue_poll)
        self.assertEqual(app.after.call_args.args[0], 60)


if __name__ == '__main__': unittest.main()
