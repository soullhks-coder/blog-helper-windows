"""Launch-relative reservations for normal and V2 queues, without publication."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from automation_schedule import restart_schedule, edited_schedule


class RestartScheduleTests(unittest.TestCase):
    def test_manual_rebase_starts_in_five_minutes_then_uses_interval_and_preserves_content(self):
        jobs = self.jobs()
        before = copy.deepcopy(jobs)
        for minutes in (3, 10, 120, 300):
            with self.subTest(minutes=minutes):
                result, count = restart_schedule(jobs, 1000000, minutes, first_delay_minutes=5)
                self.assertEqual(count, 2)
                self.assertEqual(result[1]['scheduled_at'], 1000300)
                self.assertEqual(result[0]['scheduled_at'], 1000300 + minutes * 60)
                self.assertEqual(result[2:], before[2:])
                for old, new in zip(jobs, result):
                    self.assertEqual({k:v for k,v in new.items() if k != 'scheduled_at'},
                                     {k:v for k,v in old.items() if k != 'scheduled_at'})
        again, _ = restart_schedule(result, 2000000, 120, first_delay_minutes=5)
        self.assertEqual(again[1]['scheduled_at'], 2000300)
        self.assertEqual(jobs, before)

    def test_manual_rebase_excludes_active_editing_and_partially_published_jobs(self):
        jobs = self.jobs()
        jobs += [{'id':'partial', 'status':'대기 중', 'article_html':'partial article', 'scheduled_at':10,
                  'published_urls':{'wordpress':'https://example.com'}}]
        result, count = restart_schedule(jobs, 1000000, 120, first_delay_minutes=5, exclude_ids={'first'})
        self.assertEqual(count, 1)
        self.assertEqual(result[0]['scheduled_at'], 1000300)
        self.assertEqual(result[1:], jobs[1:])

    def test_rebase_button_saves_atomically_and_preserves_queue_on_failure_or_read_only(self):
        app = self.app()
        app.active_automation_upload_item_id = ''
        app._format_automation_schedule_time = str
        with patch.object(main.time, 'time', return_value=1000000), patch.object(main, 'append_runtime_log'):
            main.KeywordApp._reschedule_automation_queue_now(app)
        self.assertEqual(app.automation_queue[1]['scheduled_at'], 1000300)
        self.assertEqual(app.automation_queue[0]['scheduled_at'], 1007500)
        self.assertEqual(app._automation_queue_page, 0)
        self.assertFalse(app._automation_startup_schedule_error)
        committed = copy.deepcopy(app.automation_queue)
        app._save_automation_queue.side_effect = OSError('disk full')
        with patch.object(main.messagebox, 'showerror'), patch.object(main.time, 'time', return_value=2000000):
            main.KeywordApp._reschedule_automation_queue_now(app)
            self.assertEqual(app.automation_queue, committed)
            app.wordpress_settings._state_read_error = 'corrupt'
            app._save_automation_queue.reset_mock()
            main.KeywordApp._reschedule_automation_queue_now(app)
            app._save_automation_queue.assert_not_called()
        self.assertEqual(app.automation_queue, committed)

    def test_manual_schedule_changes_only_time_including_paused_jobs(self):
        for item in (self.jobs()[1], self.jobs()[2]):
            before = copy.deepcopy(item)
            result = edited_schedule(item, before, 2000000, now=1000000)
            self.assertEqual(result, {**before, 'scheduled_at': 2000000})
            self.assertEqual(item, before)

    def test_manual_schedule_rejects_past_invalid_stale_running_and_published_jobs(self):
        item = self.jobs()[1]
        for value in (1000000, 999999, float('nan'), float('inf'), None, 'invalid'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                edited_schedule(item, copy.deepcopy(item), value, now=1000000)
        for patch_value in ({'status': '업로드 중'}, {'status': 'V2 준비 중'}, {'status': '업로드 완료'},
                            {'published_urls': {'tistory': 'https://example.com'}}):
            changed = {**item, **patch_value}
            with self.assertRaises(ValueError):
                edited_schedule(changed, copy.deepcopy(changed), 2000000, now=1000000)
        with self.assertRaises(ValueError):
            edited_schedule(item, {**item, 'keyword': 'stale'}, 2000000, now=1000000)
        with self.assertRaises(ValueError):
            edited_schedule(item, copy.deepcopy(item), 2000000, now=1000000, active_id=item['id'])

    def test_manual_save_failure_rolls_back_and_read_only_state_blocks_saving(self):
        app = self.app()
        item = app.automation_queue[1]
        before = copy.deepcopy(item)
        app._find_automation_queue_item = lambda _id: item
        app.active_automation_upload_item_id = ''
        app._save_automation_queue.side_effect = OSError('disk full')
        with patch('automation_schedule.time.time', return_value=1000000), self.assertRaisesRegex(OSError, 'disk full'):
            main.KeywordApp._save_automation_queue_schedule(app, item['id'], before, 2000000)
        self.assertEqual(item, before)
        self.assertEqual(app.wordpress_settings.automation_queue, app.automation_queue)
        app.wordpress_settings._state_read_error = 'corrupt'
        app._save_automation_queue.reset_mock()
        with self.assertRaisesRegex(ValueError, '복구'):
            main.KeywordApp._save_automation_queue_schedule(app, item['id'], before, 2000000)
        app._save_automation_queue.assert_not_called()

    def test_calendar_month_navigation_handles_new_year_and_leap_year(self):
        from datetime import date
        from automation_schedule_editor import AutomationScheduleEditor
        editor = SimpleNamespace(month=date(2027, 12, 1), render_calendar=Mock())
        AutomationScheduleEditor.move_month(editor, 1)
        self.assertEqual(editor.month, date(2028, 1, 1))
        AutomationScheduleEditor.move_month(editor, 1)
        editor.select_day = lambda day: AutomationScheduleEditor.select_day(editor, day)
        editor.select_day(29)
        self.assertEqual(editor.selected_date, date(2028, 2, 29))
        AutomationScheduleEditor.move_month(editor, -2)
        self.assertEqual(editor.month, date(2027, 12, 1))

    def test_open_calendar_holds_overdue_normal_job_without_automatic_rescheduling(self):
        app = self.app()
        app.automation_queue = [app.automation_queue[1]]
        app._automation_keyword_editing_ids = {'first'}
        app._next_automation_schedule_timestamp = Mock(side_effect=AssertionError('Held job must stay unchanged'))
        with patch.object(main.time, 'time', return_value=1000000):
            self.assertFalse(main.KeywordApp._reschedule_expired_automation_items(app))
        self.assertEqual(app.automation_queue[0]['scheduled_at'], 1)
        app._save_automation_queue.assert_not_called()

    def jobs(self):
        return [
            {'id':'later', 'keyword':'나중', 'status':'V2 대기 중', 'v2_config':{'profile': 'fixed'}, 'scheduled_at':9000000},
            {'id':'first', 'keyword':'먼저', 'status':'대기 중', 'article_html':'<p>완성 본문</p>', 'scheduled_at':1},
            {'id':'paused', 'keyword':'확인', 'status':'V2 확인 필요', 'v2_config':{'profile': 'fixed'}, 'scheduled_at':2},
            {'id':'uploading', 'status':'업로드 중', 'article_html':'uncertain', 'scheduled_at':3},
            {'id':'preparing', 'status':'V2 준비 중', 'v2_config':{'profile': 'fixed'}, 'scheduled_at':4},
            {'id':'done', 'status':'업로드 완료', 'article_html':'done', 'scheduled_at':5},
            {'id':'empty', 'status':'대기 중', 'article_html':'', 'scheduled_at':6},
        ]

    def test_120_to_300_minutes_rebase_expired_and_future_jobs_and_preserve_saved_order(self):
        jobs = self.jobs()
        original = copy.deepcopy(jobs)
        for minutes in (3, 60, 120, 180, 240, 300):
            with self.subTest(minutes=minutes):
                result, count = restart_schedule(jobs, 1000000, minutes)
                self.assertEqual(count, 2)
                self.assertEqual(result[1]['scheduled_at'], 1000000 + minutes * 60)
                self.assertEqual(result[0]['scheduled_at'], 1000000 + minutes * 120)
                self.assertEqual([item['id'] for item in result], [item['id'] for item in jobs])
                for index in (0, 1):
                    expected = {**jobs[index], 'scheduled_at': result[index]['scheduled_at']}
                    self.assertEqual(result[index], expected)
                self.assertEqual(result[2:], jobs[2:])
        self.assertEqual(jobs, original)

    def test_second_launch_uses_the_new_launch_time_with_the_same_spacing(self):
        first, _ = restart_schedule(self.jobs(), 1000000, 120)
        second, _ = restart_schedule(first, 1100000, 120)
        self.assertEqual(second[1]['scheduled_at'], 1100000 + 7200)
        self.assertEqual(second[0]['scheduled_at'], 1100000 + 14400)

    def test_old_paused_reservations_do_not_push_new_jobs_or_retry_away_from_launch_slots(self):
        app = self.app()
        for item in app.automation_queue:
            if item['id'] in {'paused', 'uploading', 'preparing'}:
                item['scheduled_at'] = 1900000000
        with patch.object(main, 'append_runtime_log'):
            main.KeywordApp._reset_automation_schedule_for_startup(app)
        with patch.object(main.time, 'time', return_value=1000000):
            self.assertEqual(main.KeywordApp._next_automation_schedule_timestamp(app), 1000000 + 21600)
            app._next_automation_schedule_timestamp = lambda: main.KeywordApp._next_automation_schedule_timestamp(app)
            app._find_automation_queue_item = lambda _id: next(row for row in app.automation_queue if row['id'] == _id)
            main.KeywordApp._retry_automation_v2_item(app, 'paused')
        paused = app._find_automation_queue_item('paused')
        self.assertEqual(paused['status'], 'V2 대기 중')
        self.assertEqual(paused['scheduled_at'], 1000000 + 21600)

    def test_empty_invalid_equal_and_missing_timestamps_have_stable_order(self):
        items = [{'id':str(i), 'status':'대기 중', 'v2_config':{'fixed':True}, 'scheduled_at':stamp}
                 for i, stamp in enumerate((None, 'bad', float('nan'), 0))]
        result, count = restart_schedule(items, 1000000, 10)
        self.assertEqual(count, 4)
        self.assertEqual([row['scheduled_at'] for row in result], [1000600, 1001200, 1001800, 1002400])
        self.assertEqual(restart_schedule([], 1000000, 10), ([], 0))
        for anchor, interval in ((float('inf'), 10), (1000000, 0), (1000000, -10)):
            with self.assertRaises(ValueError):
                restart_schedule(items, anchor, interval)

    def app(self):
        jobs = self.jobs()
        return SimpleNamespace(automation_queue=jobs, wordpress_settings=SimpleNamespace(automation_queue=copy.deepcopy(jobs)),
            _automation_started_at=1000000, _automation_startup_schedule_applied=False,
            _automation_startup_schedule_error='', _automation_publish_interval_minutes=lambda:120,
            _save_automation_queue=Mock(), _refresh_automation_queue=Mock(), automation_status_label=Mock())

    def test_startup_is_applied_once_and_refresh_does_not_shift_reservations_again(self):
        app = self.app()
        with patch.object(main, 'append_runtime_log'):
            main.KeywordApp._reset_automation_schedule_for_startup(app)
            first = copy.deepcopy(app.automation_queue)
            app._automation_started_at = 2000000
            main.KeywordApp._reset_automation_schedule_for_startup(app)
        self.assertEqual(app.automation_queue, first)
        self.assertTrue(app._automation_startup_schedule_applied)
        self.assertFalse(app._automation_startup_schedule_error)
        app._save_automation_queue.assert_called_once()

    def test_failed_save_preserves_original_queue_and_blocks_scheduled_and_remote_publication(self):
        app = self.app()
        original = copy.deepcopy(app.automation_queue)
        app._save_automation_queue.side_effect = OSError('disk full')
        with patch.object(main, 'append_runtime_log'):
            main.KeywordApp._reset_automation_schedule_for_startup(app)
        self.assertEqual(app.automation_queue, original)
        self.assertEqual(app.wordpress_settings.automation_queue, original)
        self.assertIn('disk full', app._automation_startup_schedule_error)
        self.assertFalse(app._automation_startup_schedule_applied)
        app._schedule_next_automation_publish = Mock()
        app._stop_automation_publish_countdown = Mock()
        main.KeywordApp._automation_publish_scheduler_tick(app)
        self.assertFalse(main.KeywordApp._publish_next_automation_queue_item(app, item_id='first'))
        self.assertEqual(app._schedule_next_automation_publish.call_count, 2)


if __name__ == '__main__':
    unittest.main()
