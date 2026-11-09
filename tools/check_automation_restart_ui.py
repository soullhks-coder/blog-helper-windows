"""Real isolated app launches: restore interval, rebase once and preserve paused jobs."""
from contextlib import ExitStack
import argparse
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path)
    parser.add_argument('--phase', type=int, choices=(1, 2))
    parser.add_argument('--theme', choices=('light', 'dark'))
    args = parser.parse_args()
    if args.data is None:
        for theme in ('light', 'dark'):
            with tempfile.TemporaryDirectory(prefix='blog-helper-restart-ui-') as directory:
                for phase in (1, 2):
                    subprocess.run([sys.executable, __file__, '--data', directory, '--theme', theme,
                                    '--phase', str(phase)], check=True, timeout=120)
        return
    os.environ['BLOG_HELPER_DATA_DIR'] = str(args.data)
    os.environ['BLOG_HELPER_DISABLE_UPDATES'] = '1'
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if args.phase == 1:
        (args.data / 'app_state.json').write_text('{}', encoding='utf-8')
    import main as api
    with ExitStack() as stack:
        for name in ('load_secret', 'save_secret', 'delete_secret'):
            stack.enter_context(patch.object(api.KeychainStore, name, return_value=''))
        for name in ('_start_remote_agent_if_enabled', '_start_update_check', '_start_update_probe', '_poll_queue',
                     '_automation_publish_scheduler_tick', '_load_version_catalog', '_on_window_configure',
                     '_load_home_dashboard_keywords', '_start_adsense_refresh', '_start_naver_kin_clipboard_monitor',
                     '_shared_tistory_usage_tick', '_refresh_shared_tistory_usage_async'):
            stack.enter_context(patch.object(api.KeywordApp, name))
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline only')))
        stack.enter_context(patch.object(api.threading.Thread, 'start', side_effect=AssertionError('No publication workers')))
        stack.enter_context(patch.object(api.messagebox, 'showerror', side_effect=AssertionError('Unexpected dialog')))
        config = {'profile': {'blog_url': 'https://fixture.example', 'write_url': ''},
                  'prompt': {'name': 'Fixture'}, 'thumbnail_index': 0}
        if args.phase == 1:
            settings = api.WordPressSettings(app_theme='화이트테마' if args.theme == 'light' else '블랙테마',
                automation_v2_profile='티스토리 1', tistory_profiles=api.normalize_tistory_profiles([
                    {'blog_url': 'https://first.example', 'write_url': 'https://first.tistory.com/manage/newpost/'},
                    {'blog_url': 'https://second.example', 'write_url': 'https://second.tistory.com/manage/newpost/'},
                ]), automation_publish_interval_minutes=120, automation_queue=[
                    {'id':'v2-later', 'keyword':'두 번째', 'v2_config':config, 'status':'V2 대기 중', 'scheduled_at':1900000000},
                    {'id':'normal-first', 'keyword':'첫 번째', 'article_html':'<p>Fixture article</p>', 'status':'대기 중', 'scheduled_at':1},
                    {'id':'paused', 'keyword':'확인 필요', 'v2_config':config, 'status':'V2 확인 필요', 'scheduled_at':1900000200},
                    {'id':'interrupted', 'keyword':'종료 시 중단', 'v2_config':config, 'status':'업로드 중', 'scheduled_at':1900000300},
                ])
            api.AppStateStore.save(settings, save_secrets=False)
        anchor = 1700000000 if args.phase == 1 else 1700100000
        with patch.object(api.time, 'time', return_value=anchor):
            app = api.KeywordApp()
        app.withdraw()
        errors = []
        app.report_callback_exception = lambda *error: errors.append(error)
        try:
            app.update()
            app.after(120, app.quit)
            app.mainloop()
            assert not errors, errors
            assert app._automation_started_at == anchor
            assert app._automation_startup_schedule_applied and not app._automation_startup_schedule_error
            minutes = 120 if args.phase == 1 else 300
            items = {item['id']: item for item in app.automation_queue}
            assert items['normal-first']['scheduled_at'] == anchor + minutes * 60
            assert items['v2-later']['scheduled_at'] == anchor + minutes * 120
            assert items['paused']['status'] == 'V2 확인 필요' and items['paused']['scheduled_at'] == 1900000200
            assert items['interrupted']['status'] == 'V2 확인 필요' and items['interrupted']['scheduled_at'] == 1900000300
            with patch.object(api.time, 'time', return_value=anchor):
                assert app._next_automation_schedule_timestamp() == anchor + minutes * 180
            assert app.automation_publish_interval_menu.get() == f'{minutes}분'
            selector = app.automation_v2_blog_selector
            assert selector.menu.get() == ('first.example' if args.phase == 1 else 'second.example')
            assert app.wordpress_settings.tistory_active_profile == '티스토리 1'
            options = app.automation_publish_interval_menu.cget('values')
            assert all(f'{value}분' in options for value in (120, 180, 240, 300))
            before = copy.deepcopy(app.automation_queue)
            if args.phase == 1:
                selector.menu.set('second.example')
                selector.menu.cget('command')('second.example')
                app.automation_publish_interval_menu.set('300분')
                app._save_automation_settings()
                # These edits use the real settings store; launch 2 must read
                # the saved review mode, then rebase the edited reservation.
                with patch.object(api.time, 'time', return_value=anchor):
                    app._save_automation_queue_schedule('normal-first', copy.deepcopy(items['normal-first']), anchor + 600)
                app._set_automation_queue_review('v2-later', copy.deepcopy(items['v2-later']), True)
                with patch.object(api.time, 'time', return_value=anchor):
                    app._reschedule_automation_queue_now()
                items = {item['id']:item for item in app.automation_queue}
                assert items['normal-first']['scheduled_at'] == anchor + 300
                assert items['v2-later']['scheduled_at'] == anchor + 300 + 300*60
                before = copy.deepcopy(app.automation_queue)
            assert items['v2-later']['v2_config']['review_publish']
            assert not items['paused']['v2_config'].get('review_publish')
            app._switch_page('automation')
            app._switch_page('home')
            app._switch_page('automation')
            app._reset_automation_schedule_for_startup()
            assert app.automation_queue == before
            saved = json.loads((args.data / 'app_state.json').read_text(encoding='utf-8'))
            assert saved['automation_queue'] == before
            assert saved['automation_publish_interval_minutes'] == 300
            assert saved['automation_v2_profile'] == '티스토리 2'
            assert next(row for row in saved['automation_queue'] if row['id'] == 'v2-later')['v2_config']['review_publish']
            print(f'PASS {args.theme} launch {args.phase}: saved +5min manual reset, job review/calendar/V2 blog, {minutes}min launch rebase, paused jobs protected, no reset on navigation')
        finally:
            app.destroy()


if __name__ == '__main__':
    main()
