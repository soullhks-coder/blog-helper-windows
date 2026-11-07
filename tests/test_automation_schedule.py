"""Launch-relative reservations for normal and V2 queues, without publication."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from automation_schedule import restart_schedule


class RestartScheduleTests(unittest.TestCase):
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
