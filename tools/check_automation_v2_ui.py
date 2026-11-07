"""Real offline UI fixture: keyword manager → V2 queue → ten slides → native handoff."""
from contextlib import ExitStack
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import Mock, patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--theme', choices=('light', 'dark'))
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    if not args.theme:
        for theme in ('light', 'dark'):
            command = [sys.executable, __file__, '--theme', theme]
            if args.screenshots:
                command += ['--screenshots', str(args.screenshots)]
            subprocess.run(command, check=True, timeout=120)
        return
    with tempfile.TemporaryDirectory(prefix='blog-helper-v2queue-ui-') as directory, ExitStack() as stack:
        os.environ['BLOG_HELPER_DATA_DIR'] = directory
        os.environ['BLOG_HELPER_DISABLE_UPDATES'] = '1'
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as api
        import automation_v2 as v2
        from tistory_v2 import V2Store
        from PIL import Image, ImageGrab
        settings = api.WordPressSettings(app_theme='화이트테마' if args.theme == 'light' else '블랙테마')
        settings.tistory_profiles = api.normalize_tistory_profiles([
            {'blog_url': 'https://first.example', 'write_url': 'https://first.tistory.com/manage/newpost/'},
            {'blog_url': 'https://second.example', 'write_url': 'https://second.tistory.com/manage/newpost/'},
        ])
        V2Store(Path(directory) / 'Tistory V2').save_preferences({
            'platform': 'tistory', 'profile': '티스토리 2', 'thumbnail': 2, 'review_publish': True,
            'show_numbers': False, 'show_photo_sources': False, 'slide_size': [600, 300], 'theme': '민트'})
        stack.enter_context(patch.object(api.AppStateStore, 'load', return_value=settings))
        for method in ('save', 'update_fields'):
            stack.enter_context(patch.object(api.AppStateStore, method))
        for method in ('_start_remote_agent_if_enabled', '_start_update_check', '_poll_queue',
                       '_automation_publish_scheduler_tick', '_load_version_catalog', '_save_ui_state',
                       '_save_ui_state_now', '_on_window_configure'):
            stack.enter_context(patch.object(api.KeywordApp, method))
        app = api.KeywordApp()
        app.geometry('1450x1050+30+50')
        errors = []
        app.report_callback_exception = lambda *error: errors.append(error)

        def settle(delay=100):
            app.update()
            app.after(delay, app.quit)
            app.mainloop()
            app.update_idletasks()
            assert not errors, errors

        def shot(name):
            if not args.screenshots:
                return
            app.lift()
            app.attributes('-topmost', True)
            settle(800)
            args.screenshots.mkdir(parents=True, exist_ok=True)
            path = args.screenshots / f'automation-v2-{args.theme}-{name}.png'
            if sys.platform == 'darwin':
                subprocess.run(['screencapture', '-x', '-R',
                    f'{app.winfo_rootx()},{app.winfo_rooty()},{app.winfo_width()},{app.winfo_height()}', str(path)], check=True)
            else:
                x, y = app.winfo_rootx(), app.winfo_rooty()
                ImageGrab.grab((x, y, x + app.winfo_width(), y + app.winfo_height())).save(path)

        try:
            app._switch_page('automation')
            settle()
            app.automation_target_platform_vars['tistory_v2'].set(True)
            app._on_automation_target_changed('tistory_v2')
            assert app._automation_target_platforms() == ['tistory_v2']
            shot('target')
            app._switch_page('keywords')
            settle()
            panel = app.keyword_library_panel
            panel.input.insert('1.0', '마그네슘 효능, 마그네슘\n칼슘 하루 권장량')
            panel.add_input()
            panel.destination.set('티스토리 V2')
            panel.destination_changed()
            panel.select_all.select()
            panel.select_page()
            with patch.object(v2.PrepareWorker, 'start') as start, \
                 patch.object(api.ArticleGenerationWorker, 'start') as article, \
                 patch.object(api.PublishPipelineWorker, 'start') as publish:
                panel.write_button.invoke()
                settle()
                start.assert_not_called()
                article.assert_not_called()
                publish.assert_not_called()
            assert app.current_page == 'automation'
            assert len(app.automation_queue) == 2
            first, second = app.automation_queue
            assert first['wiki_keyword'] == '마그네슘'
            assert second['wiki_keyword'] == ''
            assert first['target_platforms'] == ['tistory_v2']
            assert first['v2_config']['profile']['name'] == '티스토리 2'
            assert second['scheduled_at'] > first['scheduled_at']
            assert panel.store.counts()['pending'] == 2
            assert app.wordpress_settings.target_platforms == settings.target_platforms
            shot('queue')
            # A later checkbox change cannot retarget an existing V2 job.
            app.automation_target_platform_vars['wordpress'].set(True)
            app._on_automation_target_changed('wordpress')
            assert app._automation_target_platforms() == ['wordpress']
            assert first['target_platforms'] == ['tistory_v2']
            # Explicit direct writing still works while automation V2 is enabled.
            app.automation_target_platform_vars['tistory_v2'].set(True)
            app._on_automation_target_changed('tistory_v2')
            app._switch_page('keywords')
            settle()
            panel = app.keyword_library_panel
            panel.destination.set('티스토리 V2 직접 글쓰기')
            panel.selection[panel.rows[0]['id']].set(True)
            panel.write_button.invoke()
            settle()
            assert app.current_page == 'tistory_v2'
            assert app.topic_entry.get() == '마그네슘 효능'
            assert len(app.automation_queue) == 2
            # Begin one scheduled job with no paid model or authenticated browser.
            app._switch_page('automation')
            with patch.object(v2.PrepareWorker, 'start'), \
                 patch.object(api, 'writing_model_validation_error', return_value=''), \
                 patch.object(api.DailyPublishLimitStore, 'ensure_can_publish'):
                assert app._publish_next_automation_queue_item(item_id=first['id'])
            worker = app.pipeline_worker
            body = '<h2>마그네슘의 역할</h2><p>몸의 기능에 관여합니다.</p><p>주요 태그: 마그네슘, 건강</p>'
            summaries = [{'source_sections': [1], 'eyebrow': '핵심 정리', 'title': '마그네슘 역할',
                          'summary': '몸의 기능에 관여해요', 'bullets': ['균형 있게 섭취']} for _ in range(10)]
            photo = Path(directory) / 'sample.png'
            Image.new('RGB', (200, 100), 'red').save(photo)
            with patch.object(api.AutomationKeywordQueueWorker, '_collect_reference_text_for_keyword', return_value='참고 내용'), \
                 patch.object(api.ArticleGenerationWorker, '_generate_with_provider', side_effect=[('마그네슘 효능 안내', 'offline'),
                    (json.dumps({'article_html': body, 'slides': summaries}), 'offline')]), \
                 patch('tistory_v2.collect_wiki_images', return_value=([{'path': str(photo), 'source': '위키백과'}], [])), \
                 patch.object(api, 'require_google_chrome_executable', return_value=Path('/offline/chrome')):
                worker.run()
            prepared = None
            while not app.result_queue.empty():
                kind, payload = app.result_queue.get_nowait()
                if kind == 'automation_v2_article_ready':
                    app._handle_automation_v2_article_ready(payload)
                elif kind == 'automation_v2_prepared':
                    prepared = payload
                assert kind != 'automation_v2_error', payload
            assert prepared
            original_design = app._capture_thumbnail_preset_from_ui()
            with patch.object(api.PublishPipelineWorker, 'start'):
                app._handle_automation_v2_prepared(prepared)
            assert app._capture_thumbnail_preset_from_ui() == original_design
            assert app.pipeline_worker.tistory_v2
            assert app.pipeline_worker.settings.tistory_active_profile == '티스토리 2'
            assert not app.pipeline_worker.settings.tistory_auto_publish
            assert Path(first['thumbnail_path']).is_file()
            app.pipeline_worker.run()  # Tistory path prepares files only; native input is the next worker.
            done = None
            while not app.result_queue.empty():
                kind, payload = app.result_queue.get_nowait()
                if kind == 'publish_pipeline_done': done = payload
                assert kind != 'publish_pipeline_error', payload
            assert done, 'No prepared pipeline result'
            with patch.object(api, 'TistoryAutomationWorker') as native:
                app._handle_automation_publish_success(done)
                kwargs = native.call_args.kwargs
                assert len(kwargs['tistory_v2_slide_paths']) == 10
                assert kwargs['tistory_v2_slide_size'] == [600, 300]
                assert kwargs['tistory_v2_source_credits'] == ''
                assert kwargs['public_blog_url'] == 'https://second.example'
                assert not kwargs['auto_publish']
                assert kwargs['source_keyword'] == '마그네슘 효능'
                native.return_value.start.assert_called_once()
            assert panel.store.counts()['pending'] == 2
            # Failure pauses; changing registration time cannot accidentally resume it.
            app._handle_automation_publish_error('offline test failure')
            assert first['status'] == 'V2 확인 필요'
            before = first['status']
            app._reschedule_expired_automation_items()
            assert first['status'] == before
            shot('paused')
            app.geometry('1120x850')
            settle()
            shot('compact')
            print(f'PASS {args.theme}: multi-keyword/wiki queue, fixed account, direct mode, real article/slides, native handoff, pause')
        finally:
            app.destroy()


if __name__ == '__main__':
    main()
