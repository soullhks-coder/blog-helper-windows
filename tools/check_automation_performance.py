"""Offline 500-job UI regression: bounded widgets, bulk time reset and selection."""
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
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--theme', choices=('light', 'dark'))
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    if not args.theme:
        for theme in ('light', 'dark'):
            command = [sys.executable, __file__, '--theme', theme]
            if args.screenshots: command += ['--screenshots', str(args.screenshots)]
            subprocess.run(command, check=True, timeout=180)
        return
    with tempfile.TemporaryDirectory(prefix='blog-helper-queue500-') as directory, ExitStack() as stack:
        os.environ['BLOG_HELPER_DATA_DIR'] = directory
        os.environ['BLOG_HELPER_DISABLE_UPDATES'] = '1'
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as api
        from automation_queue_view import PAGE_SIZE
        from PIL import ImageGrab
        settings = api.WordPressSettings(app_theme='화이트테마' if args.theme == 'light' else '블랙테마',
                                         automation_publish_interval_minutes=120)
        config = {'profile': {'blog_url':'https://fixture.example', 'write_url':''},
                  'prompt': {'name':'Fixture'}, 'thumbnail_index':0, 'review_publish':True}
        anchor = time.time()
        settings.automation_queue = [dict(id=f'job-{i}', title=f'대기 작업 {i + 1}', keyword=f'키워드 {i + 1}',
            article_html='<p>원본 본문</p>', v2_config=copy.deepcopy(config), status='V2 대기 중',
            scheduled_at=anchor + 600 + i*600, target_platforms=['tistory_v2']) for i in range(500)]
        stack.enter_context(patch.object(api.AppStateStore, 'load', return_value=settings))
        for name in ('save', 'update_fields'): stack.enter_context(patch.object(api.AppStateStore, name))
        for name in ('_start_remote_agent_if_enabled', '_start_update_check', '_start_update_probe', '_poll_queue',
                     '_automation_publish_scheduler_tick', '_load_version_catalog', '_save_ui_state', '_save_ui_state_now',
                     '_on_window_configure', '_load_home_dashboard_keywords', '_start_adsense_refresh',
                     '_start_naver_kin_clipboard_monitor', '_shared_tistory_usage_tick', '_refresh_shared_tistory_usage_async'):
            stack.enter_context(patch.object(api.KeywordApp, name))
        stack.enter_context(patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline fixture')))
        stack.enter_context(patch.object(api.threading.Thread, 'start', side_effect=AssertionError('No publication')))
        start = time.perf_counter()
        app = api.KeywordApp()
        startup = time.perf_counter() - start
        app.geometry('1450x1050+30+50')
        errors = []
        app.report_callback_exception = lambda *error: errors.append(error)
        def settle(delay=100):
            app.update()
            app.after(delay, app.quit)
            app.mainloop()
            assert not errors, errors
        def shot():
            if not args.screenshots: return
            args.screenshots.mkdir(parents=True, exist_ok=True)
            path = args.screenshots / f'queue500-{args.theme}.png'
            app.lift()
            app.attributes('-topmost', True)
            settle(800)
            if sys.platform == 'darwin':
                subprocess.run(['screencapture','-x','-R',
                    f'{app.winfo_rootx()},{app.winfo_rooty()},{app.winfo_width()},{app.winfo_height()}',str(path)], check=True)
            else:
                x,y=app.winfo_rootx(),app.winfo_rooty()
                ImageGrab.grab((x,y,x+app.winfo_width(),y+app.winfo_height())).save(path)
        try:
            assert len(app.automation_queue) == 500
            assert not app.automation_list.winfo_children(), 'Hidden startup rendered all queued cards'
            start = time.perf_counter()
            app._switch_page('automation')
            settle()
            first_page = time.perf_counter() - start
            assert len(app.automation_list.winfo_children()) == PAGE_SIZE
            assert set(app.automation_schedule_buttons) == {f'job-{i}' for i in range(20)}
            widgets = list(app.automation_list.winfo_children())
            for _ in range(10): app._refresh_automation_queue()
            assert app.automation_list.winfo_children() == widgets
            app.automation_selection_vars['job-0'].set(True)
            app.automation_queue_next_button.invoke()
            settle()
            assert app.automation_selection_vars['job-0'].get()
            assert 'job-20' in app.automation_schedule_buttons
            app.automation_selection_vars['job-20'].set(True)
            app.automation_queue_page_menu.set('25 / 25')
            app.automation_queue_page_menu.cget('command')('25 / 25')
            settle()
            assert set(app.automation_schedule_buttons) == {f'job-{i}' for i in range(480,500)}
            before = copy.deepcopy(app.automation_queue)
            original = before[0]['scheduled_at']
            app.active_automation_upload_item_id = 'job-0'
            app.automation_queue[1]['status'] = 'V2 확인 필요'
            before[1]['status'] = 'V2 확인 필요'
            app._automation_keyword_editing_ids = {'job-2'}
            reset_at = time.time()
            with patch.object(api.time, 'time', return_value=reset_at):
                app.automation_reschedule_button.invoke()
            settle()
            assert app._automation_queue_page == 0
            assert app.automation_queue[:3] == before[:3]
            for offset, item in enumerate(app.automation_queue[3:]):
                assert item['scheduled_at'] == reset_at + 300 + offset * 120 * 60
                assert {k:v for k,v in item.items() if k!='scheduled_at'} == {
                    k:v for k,v in before[offset+3].items() if k!='scheduled_at'}
            assert original == app.automation_queue[0]['scheduled_at']
            app.active_automation_upload_item_id = ''
            app._automation_keyword_editing_ids.clear()
            app.automation_select_all_var.set(True)
            app._toggle_all_automation_queue_selection()
            assert len(app._selected_automation_queue_ids()) == 500
            app.automation_select_all_var.set(False)
            app._toggle_all_automation_queue_selection()
            app.automation_selection_vars['job-0'].set(True)
            app.automation_selection_vars['job-20'].set(True)
            with patch.object(api.messagebox, 'askyesno', return_value=True):
                app._delete_selected_automation_queue_items()
            settle()
            assert len(app.automation_queue) == 498
            assert {row['id'] for row in app.automation_queue} == {f'job-{i}' for i in range(500)} - {'job-0','job-20'}
            shot()
            print(f'PASS {sys.platform} {args.theme}: 500 jobs, hidden startup 0 cards, {PAGE_SIZE} visible, selection across pages, 5min reset/120min spacing, active/paused/edited protected, startup={startup:.3f}s first_page={first_page:.3f}s', flush=True)
        finally:
            app.destroy()


if __name__ == '__main__': main()
