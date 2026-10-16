"""Offline Tk smoke checks for the writing UI, run on macOS and Windows CI."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import faulthandler
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import Mock, patch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", choices=("dark", "light"))
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--windows-dimmer", action="store_true",
                        help="Exercise Windows-only dim refresh logic on this host (not native Windows rendering).")
    parser.add_argument("--preview-v2-seconds", type=int, default=0,
                        help="Keep the isolated V2 editor visible for manual visual inspection.")
    args = parser.parse_args()
    if not args.theme:
        for theme in ("dark", "light"):
            command = [sys.executable, __file__, "--theme", theme]
            if args.screenshots:
                command.extend(["--screenshots", str(args.screenshots)])
            if args.windows_dimmer:
                command.append("--windows-dimmer")
            subprocess.run(command, check=True, timeout=150)
        return

    with tempfile.TemporaryDirectory(prefix="blog-helper-writing-ui-") as directory, ExitStack() as stack:
        # Expanded full-app UI checks can exceed 90s on Windows CI runners.
        # Keep diagnostics before the unchanged enforcing 150s parent timeout.
        faulthandler.dump_traceback_later(120, repeat=True)
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as app_module
        if args.windows_dimmer:
            import modal_backdrop
            real_dimmer = modal_backdrop.ModalDimmer
            stack.enter_context(patch.object(
                modal_backdrop, "ModalDimmer",
                lambda root: real_dimmer(root, platform_name="win32"),
            ))
        poll_result_queue = app_module.KeywordApp._poll_queue

        settings = app_module.WordPressSettings(
            app_theme="블랙테마" if args.theme == "dark" else "화이트테마",
            window_geometry="1500x1000",
            target_platforms=["wordpress", "tistory", "blogspot"],
            tistory_profiles=[{"blog_url": "https://v2-first.tistory.com"}, {"blog_url": "https://v2-second.tistory.com"}],
            writing_topic="일반 저장 주제", writing_selected_keyword="일반 저장 선택",
            tistory_v2_topic="V2 저장 주제", tistory_v2_selected_keyword="V2 저장 선택",
        )
        stack.enter_context(patch.object(app_module.AppStateStore, "load", return_value=settings))
        for method in ("save", "update_fields"):
            stack.enter_context(patch.object(app_module.AppStateStore, method))
        for method in (
            "_start_remote_agent_if_enabled", "_start_update_check", "_poll_queue",
            "_automation_publish_scheduler_tick", "_load_version_catalog",
            "_save_ui_state", "_save_ui_state_now", "_on_window_configure",
        ):
            stack.enter_context(patch.object(app_module.KeywordApp, method, return_value=None))
        print(f"{sys.platform} {args.theme}: constructing app", flush=True)
        app = app_module.KeywordApp()
        print(f"{sys.platform} {args.theme}: app constructed", flush=True)
        assert app._file_drop_available
        errors = []
        app.report_callback_exception = lambda *error: errors.append(error)

        def settle(delay_ms=160):
            app.after(delay_ms, app.quit)
            app.mainloop()
            app.update_idletasks()
            assert not errors, errors

        def screenshot(name, window=None):
            if not args.screenshots:
                return
            args.screenshots.mkdir(parents=True, exist_ok=True)
            modal = getattr(app, "writing_complete_dialog", None)
            if modal is not None and modal.winfo_exists():
                app.attributes("-topmost", True)
                app.lift()
                modal.lift()
            else:
                app.attributes("-topmost", True)
                app.lift()
            target = window if window is not None else app
            if window is not None:
                window.attributes("-topmost", True)
                window.lift()
            settle()
            destination = args.screenshots / f"writing-{args.theme}-{name}.png"
            if sys.platform == "darwin":
                area = f"{target.winfo_rootx()},{target.winfo_rooty()},{target.winfo_width()},{target.winfo_height()}"
                subprocess.run(["screencapture", "-x", "-R", area, str(destination)], check=True)
            else:
                from PIL import ImageGrab
                x, y = target.winfo_rootx(), target.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + target.winfo_width(), y + target.winfo_height())).save(destination)

        def screenshot_inactive(name):
            """Capture the macOS app after another process owns focus."""
            if not args.screenshots or sys.platform != "darwin":
                return
            args.screenshots.mkdir(parents=True, exist_ok=True)
            app.update()
            subprocess.run(
                ["osascript", "-e", 'tell application "Finder" to activate'],
                check=True,
            )
            settle(350)
            destination = args.screenshots / f"writing-{args.theme}-{name}.png"
            area = f"{app.winfo_rootx()},{app.winfo_rooty()},{app.winfo_width()},{app.winfo_height()}"
            subprocess.run(["screencapture", "-x", "-R", area, str(destination)], check=True)
            from PIL import Image as PILImage

            captured = PILImage.open(destination).convert("RGB")
            palette = app._theme_palette()
            button_pages = {
                "home_nav_button": "home",
                "writing_nav_button": "writing",
                "automation_nav_button": "automation",
                "naver_blog_nav_button": "naver_blog",
                "tistory_v2_nav_button": "tistory_v2",
                "naver_kin_nav_button": "naver_kin",
                "public_data_nav_button": "public_data",
                "thumbnail_nav_button": "thumbnails",
                "prompt_nav_button": "prompts",
                "settings_nav_button": "settings",
            }
            for button_name, page_name in button_pages.items():
                button = getattr(app, button_name)
                sample_x = button.winfo_rootx() - app.winfo_rootx() + 2
                sample_y = button.winfo_rooty() - app.winfo_rooty() + (button.winfo_height() // 2)
                actual = captured.getpixel((sample_x, sample_y))
                expected_color = (
                    palette["selected"]
                    if page_name == app.current_page
                    else palette["sidebar"]
                )
                if app_module.modal_dimmer(app).active:
                    expected_color = app_module.modal_dimmer(app).root.winfo_rgb(expected_color)
                    expected_color = "#" + "".join(f"{round(c / 257 * 0.45):02x}" for c in expected_color)
                expected_color = expected_color.lstrip("#")
                expected_rgb = tuple(
                    int(expected_color[index:index + 2], 16)
                    for index in (0, 2, 4)
                )
                assert all(abs(actual[channel] - expected_rgb[channel]) <= 3 for channel in range(3)), (
                    button_name,
                    actual,
                    expected_rgb,
                )
            app.lift()
            app.focus_force()
            settle(120)

        def resolved_color(widget, option):
            value = widget.cget(option)
            try:
                return str(widget._apply_appearance_mode(value)).lower()
            except Exception:
                if isinstance(value, (tuple, list)):
                    value = value[0] if args.theme == "light" else value[-1]
                return str(value).lower()

        def assert_segmented_contrast(control):
            assert isinstance(control, app_module.ContrastSegmentedButton)
            assert control.cget("border_width") == 0
            assert control.get() in control._buttons_dict
            for value, button in control._buttons_dict.items():
                assert button.cget("border_width") == 0
                if value == control.get():
                    assert resolved_color(button, "text_color") == "#ffffff"
                elif args.theme == "light":
                    assert resolved_color(button, "text_color") == "#1f2937"

        def assert_white_button_contrast(root):
            if args.theme != "light":
                return
            pending = [root]
            while pending:
                widget = pending.pop()
                if isinstance(widget, app_module.ContrastSegmentedButton):
                    assert_segmented_contrast(widget)
                    continue
                pending.extend(widget.winfo_children())
                if not isinstance(widget, app_module.ctk.CTkButton):
                    continue
                fill = resolved_color(widget, "fg_color")
                if app._is_dark_button_fill(fill):
                    assert resolved_color(widget, "text_color") == "#ffffff", (
                        widget.cget("text"),
                        fill,
                        resolved_color(widget, "text_color"),
                    )

        try:
            app.geometry("1500x1000+30+35")
            app._switch_page("home")
            app.update()
            settle(900)
            assert app.tistory_v2_panel is None  # no separate workflow/startup work
            original_article = app.article_editor.get("1.0", "end")
            original_target = app.wordpress_settings.home_target_platform
            original_default = app.default_thumbnail_preset_index
            shared_topic_entry = app.topic_entry
            assert app.topic_entry.get() == "일반 저장 주제"
            app.topic_entry.delete(0, "end")
            app.topic_entry.insert(0, "일반 최근 입력")
            app.manual_keyword_entry.delete(0, "end")
            app.manual_keyword_entry.insert(0, "일반 직접 입력")
            app.current_keyword_source = "analysis"
            app.current_insights = [app_module.KeywordInsight("일반 추천", 80, [], [], [], {})]
            app._switch_page("tistory_v2")
            settle(250)
            assert app.topic_entry.get() == "V2 저장 주제"
            assert app._selected_or_manual_keyword() == "V2 저장 선택"
            assert not app.current_insights and not app.current_keyword_source
            app.topic_entry.delete(0, "end")
            app.topic_entry.insert(0, "V2 최근 입력")
            app.manual_keyword_entry.delete(0, "end")
            app.manual_keyword_entry.insert(0, "V2 직접 입력")
            app.current_keyword_source = "daum"
            app.current_insights = [app_module.KeywordInsight("V2 추천", 80, [], [], [], {})]
            saved_inputs = app._read_wordpress_settings(include_prompts=False)
            assert (saved_inputs.writing_topic, saved_inputs.tistory_v2_topic) == ("일반 최근 입력", "V2 최근 입력")
            assert (saved_inputs.writing_selected_keyword, saved_inputs.tistory_v2_selected_keyword) == ("일반 직접 입력", "V2 직접 입력")
            app._switch_page("writing")
            assert app.topic_entry.get() == "일반 최근 입력"
            assert app.manual_keyword_entry.get() == "일반 직접 입력"
            assert app.current_keyword_source == "analysis"
            assert [item.keyword for item in app.current_insights] == ["일반 추천"]
            app._switch_page("home")
            app._switch_page("tistory_v2")
            assert app.topic_entry.get() == "V2 최근 입력"
            assert app.manual_keyword_entry.get() == "V2 직접 입력"
            assert app.current_keyword_source == "daum"
            assert [item.keyword for item in app.current_insights] == ["V2 추천"]
            app.topic_entry.delete(0, "end")
            app.manual_keyword_entry.delete(0, "end")
            app.selected_keyword_var.set("")
            assert app._read_wordpress_settings(include_prompts=False).tistory_v2_topic == ""
            print(f"{sys.platform} {args.theme}: normal/V2 keyword inputs, selection, source and persistence are isolated", flush=True)
            settle(160)
            assert app.current_page == "tistory_v2"
            assert app._current_page_frame() is app.writing_page
            assert app.writing_page.winfo_ismapped()
            assert app.topic_entry is shared_topic_entry
            assert tuple(app.writing_step_headers) == ("topic", "keyword", "article", "publish")
            assert app.writing_page_title.cget("text") == "티스토리 V2"
            assert app._current_primary_scroll_frame() is app.writing_scroll
            assert int(app.tistory_v2_nav_button.grid_info()["row"]) == int(app.naver_blog_nav_button.grid_info()["row"]) + 1
            assert app_module.SIDEBAR_MENU_DEFAULT_ICONS["tistory_v2"] == "type"
            controls = app.tistory_v2_controls
            assert len(controls.choices["profile"]) == 2
            assert len(controls.choices["thumbnail"]) == 5
            assert controls.menus["prompt_id"].get().startswith("티스토리 ·")
            assert not app.writing_target_row.winfo_ismapped()
            controls.menus["profile"].set("v2-second.tistory.com")
            controls.changed("profile")
            controls.select_thumbnail(3)
            execution = controls.execution_settings(app.wordpress_settings)
            assert execution.target_platforms == ["tistory"]
            assert execution.tistory_active_profile == "티스토리 2"
            assert execution.tistory_blog_url == "https://v2-second.tistory.com"
            assert execution.inline_images_enabled is False
            assert app.wordpress_settings.home_target_platform == original_target
            assert app.default_thumbnail_preset_index == original_default

            # Same generation worker receives the actual collected references.
            with patch.object(app_module, "ArticleGenerationWorker") as worker, \
                 patch.object(app_module, "writing_model_validation_error", return_value=""):
                worker.return_value.is_alive.return_value = False
                app._start_article_generation("여행", "서울", "실제로 수집한 참고내용")
                args_used = worker.call_args.args
                assert args_used[1:4] == ("여행", "서울", "실제로 수집한 참고내용")
                assert args_used[0].title_prompt_template == execution.title_prompt_template
                assert not args_used[0].inline_images_enabled
                worker.return_value.start.assert_called_once()
            app.article_worker = None
            app.article_progress_bar.stop()

            edited_body = "".join(f"<h2>수정한 소제목 {i}</h2><p>검토한 본문 {i}</p>" for i in range(10))
            app.article_title_entry.delete(0, "end")
            app.article_title_entry.insert(0, "검토한 서울 여행")
            app.article_editor.delete("1.0", "end")
            app.article_editor.insert("1.0", edited_body)
            from tistory_v2 import V2Store
            from tistory_v2_summary import validate_summaries
            cached_summaries = [{"source_sections": [i + 1], "eyebrow": "핵심 요약", "title": f"간단한 요점 {i}",
                                 "summary": "본문의 핵심만 짧게 정리합니다.", "bullets": ["주요 사항 확인"]}
                                for i in range(10)]
            validate_summaries(cached_summaries, "검토한 서울 여행", edited_body)
            store = V2Store(app_module.DATA_DIR / "Tistory V2")
            store.save_preferences({**store.preferences(), "article_summary_plan": {
                **{key: value for key, value in app._v2_article_source().items() if key != "slide_summaries"},
                "slides": cached_summaries,
            }})
            panel = app.tistory_v2_panel
            assert panel.embedded
            assert not isinstance(panel.scroll, app_module.ctk.CTkScrollableFrame)
            assert not panel.connection_card.winfo_ismapped()
            panel.mode.set("수동 모드")
            assert panel.prepare_article(confirm=False)
            for _ in range(200):
                settle(50)
                if not panel.busy:
                    break
            assert not panel.busy and len(panel.deck["slides"]) == 10
            assert panel.deck["text_mode"] == "summary"
            assert panel.deck["slides"][0]["summary"] == "본문의 핵심만 짧게 정리합니다."
            panel.show_numbers.set(False)
            panel._number_display_changed()
            assert not panel.store.preferences()["show_numbers"]
            assert not panel.deck["show_numbers"]
            panel._load_deck(panel.store.load_deck(panel.deck["id"]))
            assert not panel.show_numbers.get()
            for entry, value in zip(panel.size_entries, (900, 600)):
                entry.delete(0, "end")
                entry.insert(0, str(value))
            assert panel._apply_image_size()
            assert panel.store.preferences()["slide_size"] == [900, 600]
            assert panel.deck["slide_size"] == [900, 600]
            panel._load_deck(panel.store.load_deck(panel.deck["id"]))
            assert [entry.get() for entry in panel.size_entries] == ["900", "600"]
            panel.size_entries[0].delete(0, "end")
            panel.size_entries[0].insert(0, "-1")
            with patch("tistory_v2_ui.messagebox.showerror") as invalid_size:
                assert not panel._apply_image_size()
                invalid_size.assert_called_once()
            assert panel.deck["slide_size"] == [900, 600]
            panel.size_entries[0].delete(0, "end")
            panel.size_entries[0].insert(0, "900")
            # Legacy work remains 600×300 even when the last preference is larger.
            legacy_deck = dict(panel.deck)
            legacy_deck.pop("slide_size")
            panel._load_deck(legacy_deck)
            assert panel._output_size == (600, 300)
            for entry, value in zip(panel.size_entries, (900, 600)):
                entry.delete(0, "end")
                entry.insert(0, str(value))
            assert panel._apply_image_size()
            original_deck_id = panel.deck["id"]
            # Editing the article invalidates its cached summary plan. Summarize
            # only the changed final body, never regenerate the full article.
            changed_body = edited_body + '<p>수정된 추가 조건을 보존합니다.</p>'
            app.article_editor.insert('end', '<p>수정된 추가 조건을 보존합니다.</p>')
            assert app._v2_article_source()['slide_summaries'] is None
            with patch.object(app_module, 'writing_model_validation_error', return_value=''), \
                 patch.object(panel.workflow, 'summarize', return_value=cached_summaries) as summarize, \
                 patch.object(panel.workflow, 'generate') as generate:
                assert panel.prepare_article(confirm=False)
                for _ in range(200):
                    settle(50)
                    if not panel.busy:
                        break
                assert not panel.busy
                summarize.assert_called_once()
                assert changed_body in summarize.call_args.args[2]
                generate.assert_not_called()
            app.article_editor.delete('1.0', 'end')
            app.article_editor.insert('1.0', edited_body)
            # Cached summary plans are reused; the body is never regenerated.
            import tistory_v2_ui as slide_ui
            from PIL import Image
            photo_path = Path(directory) / "v2-chosen-photo.png"
            Image.new("RGB", (200, 100), "#bfd6ff").save(photo_path)
            asset = {"path": str(photo_path), "source": "테스트 사진", "title": "직접 선택한 사진"}
            panel.mode.set("자동 모드")
            with patch.object(slide_ui, "collect_wiki_images", return_value=([asset, dict(asset)], [])) as collect, \
                 patch.object(panel, "chrome_path_provider", return_value=Path("/fake/chrome")):
                assert panel.prepare_article(confirm=False)
                for _ in range(200):
                    settle(50)
                    if not panel.busy:
                        break
                collect.assert_called_once()
            assert not panel.busy and panel.deck["id"] != original_deck_id
            assert panel.deck["slide_size"] == [900, 600]
            with Image.open(panel.store.deck_directory(panel.deck) / "slide-01.png") as exported:
                assert exported.size == (900, 600)
            assert panel.store.load_deck(original_deck_id)["body"] == panel.deck["body"]
            assert all(slide["image"] is not None for slide in panel.deck["slides"][:2])
            assert all(slide["image"] is None for slide in panel.deck["slides"][2:])
            panel._choose_photo("사진 없음")
            assert panel.deck["slides"][0]["image"] is None
            panel._choose_photo(next(iter(panel._image_labels)))
            panel.layout_menu.set("사진 왼쪽")
            panel.text_position_menus["text_horizontal"].set("오른쪽")
            panel.text_position_menus["text_vertical"].set("아래")
            panel._text_position_changed()
            panel._move(2)
            assert panel.text_position_menus["text_horizontal"].get() == "왼쪽"
            assert panel.text_position_menus["text_vertical"].get() == "기본"
            assert all(menu.cget("state") == "normal" for menu in panel.text_position_menus.values())
            panel.text_position_menus["text_horizontal"].set("중앙")
            panel.text_position_menus["text_vertical"].set("위")
            panel._text_position_changed()
            panel._move(-2)
            assert panel.text_position_menus["text_horizontal"].get() == "오른쪽"
            assert panel.text_position_menus["text_vertical"].get() == "아래"
            panel.slide_title_box.delete("1.0", "end")
            panel.slide_title_box.insert("1.0", "일괄 적용 전 입력한 문구")
            panel._edited()
            panel.text_position_menus["text_horizontal"].set("중앙")
            panel.text_position_menus["text_vertical"].set("중앙")
            assert panel.text_position_all_button.invoke()
            assert panel._edit_job is None
            assert panel.deck["slides"][0]["title"] == "일괄 적용 전 입력한 문구"
            saved_bulk = panel.store.load_deck(panel.deck["id"])
            assert all(slide["text_horizontal"] == "중앙" and slide["text_vertical"] == "중앙" for slide in saved_bulk["slides"])
            assert all(slide["image"] is not None for slide in saved_bulk["slides"][:2])
            assert all(slide["image"] is None for slide in saved_bulk["slides"][2:])
            with patch.object(panel, "worker", object()):
                assert not panel._apply_text_position_to_all()
            with patch.object(panel, "_publishing", return_value=True):
                assert not panel._apply_text_position_to_all()
            # Keep individual override behavior after applying all ten at once.
            panel._move(2)
            panel.text_position_menus["text_horizontal"].set("왼쪽")
            panel.text_position_menus["text_vertical"].set("위")
            panel._text_position_changed()
            panel._move(-2)
            assert panel.text_position_menus["text_horizontal"].get() == "중앙"
            assert panel.text_position_menus["text_vertical"].get() == "중앙"
            # Font controls update the actual preview without a Save/Apply click.
            from PIL import ImageTk
            before_preview = ImageTk.getimage(panel._preview_image).tobytes()
            font_sizes = {"eyebrow": 12, "title": 30, "summary": 16, "bullets": 15}
            for key, value in font_sizes.items():
                panel.font_size_vars[key].set(str(value))
            settle(140)
            assert panel._font_preview_job is None
            assert ImageTk.getimage(panel._preview_image).tobytes() != before_preview
            assert panel.deck["slides"][0]["font_sizes"] == font_sizes
            settle(400)
            assert panel.store.load_deck(panel.deck["id"])["slides"][0]["font_sizes"] == font_sizes
            for invalid in ("", "0", "9999"):
                panel.font_size_vars["title"].set(invalid)
                assert panel.deck["slides"][0]["font_sizes"]["title"] == 30
                panel._finish_font_size_input("title")
                assert panel.font_size_vars["title"].get() == "30"
            panel.font_size_sliders["summary"].cget("command")(18)
            assert panel.font_size_vars["summary"].get() == "18"
            font_sizes["summary"] = 18
            panel._move(1)
            assert panel.font_size_vars["title"].get() == "24"  # untouched photo slide
            panel.font_size_vars["title"].set("34")
            panel._move(-1)
            assert panel.font_size_vars["title"].get() == "30"
            font_content = app_module.copy.deepcopy(panel.deck["slides"])
            assert panel.font_size_all_button.invoke()
            assert all(slide["font_sizes"] == font_sizes for slide in panel.deck["slides"])
            for old_slide, slide in zip(font_content, panel.deck["slides"]):
                assert {key: value for key, value in old_slide.items() if key != "font_sizes"} == {
                    key: value for key, value in slide.items() if key != "font_sizes"}
            panel.font_size_reset_button.invoke()
            assert "font_sizes" not in panel.deck["slides"][0]
            assert panel.font_size_vars["title"].get() == "24"
            assert panel.deck["slides"][1]["font_sizes"] == font_sizes
            for key, value in font_sizes.items():
                panel.font_size_vars[key].set(str(value))
            with patch.object(panel, "worker", object()):
                assert not panel._apply_font_sizes_to_all()
            with patch.object(panel, "_publishing", return_value=True):
                assert not panel._apply_font_sizes_to_all()
            print(f"{sys.platform} {args.theme}: V2 live font preview, per-slide sizes, bulk apply and persistence passed", flush=True)
            app._open_writing_section("publish")
            settle(200)
            assert panel.winfo_ismapped() and panel.preview.winfo_ismapped()
            # Editing then immediately uploading must flush the 350ms debounce.
            panel.slide_title_box.delete("1.0", "end")
            panel.slide_title_box.insert("1.0", "편집한 슬라이드 제목 유지")
            panel._edited()
            # Upload also commits valid pending dimension inputs without an Apply click.
            for entry, value in zip(panel.size_entries, (1200, 600)):
                entry.delete(0, "end")
                entry.insert(0, str(value))
            saved_deck_id = panel.deck["id"]
            controls.select_thumbnail(4)
            assert panel.store.preferences()["deck_id"] == saved_deck_id
            panel._save_preferences()
            assert panel.store.preferences()["thumbnail"] == 4
            with patch.object(app_module, "PublishPipelineWorker") as publisher, \
                 patch.object(controls, "export_thumbnail", return_value=Path(directory) / "thumbnail.png"):
                publisher.return_value.is_alive.return_value = False
                app._run_publish_pipeline()
                options = publisher.call_args.kwargs
                assert options["tistory_v2"] is True
                assert edited_body in options["article_html"]
                assert options["settings"].target_platforms == ["tistory"]
                assert options["settings"].tistory_active_profile == "티스토리 2"
                assert options["tistory_v2_deck"]["id"] == saved_deck_id
                assert options["tistory_v2_deck"]["slide_size"] == [1200, 600]
                assert options["tistory_v2_deck"]["slides"][0]["title"] == "편집한 슬라이드 제목 유지"
                assert options["tistory_v2_deck"]["slides"][0]["image"]["path"] == str(photo_path)
                assert options["tistory_v2_deck"]["slides"][0]["layout"] == "사진 왼쪽"
                assert options["tistory_v2_deck"]["slides"][0]["font_sizes"] == font_sizes
                assert options["tistory_v2_deck"]["slides"][0]["text_horizontal"] == "중앙"
                assert options["tistory_v2_deck"]["slides"][0]["text_vertical"] == "중앙"
                assert options["tistory_v2_deck"]["slides"][2]["text_horizontal"] == "왼쪽"
                assert options["tistory_v2_deck"]["slides"][2]["text_vertical"] == "위"
                assert panel._edit_job is None
                publisher.return_value.start.assert_called_once()
            app.pipeline_worker = None
            app.publish_progress_bar.stop()
            app.writing_auto_run_active = False
            app.direct_keyword_start_keyword = ""
            app.article_editor.insert("end", "본문이 바뀜")
            with patch.object(app_module, "PublishPipelineWorker") as publisher, \
                 patch.object(app_module.messagebox, "showwarning") as warning:
                app._run_publish_pipeline()
                publisher.assert_not_called()
                warning.assert_called_once()
            app.article_editor.delete("1.0", "end")
            app.article_editor.insert("1.0", edited_body)
            with patch.object(panel, "worker", object()):
                app.writing_auto_progress_var.set(True)
                app.writing_auto_run_active = True
                app._auto_publish_current_article()
                assert app.writing_v2_publish_pending
                assert app.writing_auto_run_active  # waiting is not a failed upload
            app._v2_slide_editor_finished(False)
            assert not app.writing_v2_publish_pending
            assert not app.writing_auto_run_active
            app.writing_auto_progress_var.set(False)
            panel._load_deck(panel.store.load_deck(saved_deck_id))
            assert panel.slide_title_box.get("1.0", "end").strip() == "편집한 슬라이드 제목 유지"
            assert panel.text_position_menus["text_horizontal"].get() == "중앙"
            assert panel.text_position_menus["text_vertical"].get() == "중앙"
            assert controls.selection()["thumbnail"] == 4
            # Loading an older deck must not replace the last actively selected
            # defaults, even across panel/application reconstruction.
            expected_defaults = {"text_horizontal": "왼쪽", "text_vertical": "위"}
            assert panel.store.preferences()["text_position_defaults"] == expected_defaults
            host = app_module.ctk.CTkFrame(app)
            restarted_panel = slide_ui.TistoryV2Panel(
                host, app, panel.store.directory, panel.chrome_path_provider, panel.apply_images,
                workflow=panel.workflow, article_provider=app._v2_article_source)
            assert restarted_panel._text_position_defaults == expected_defaults
            assert restarted_panel._font_size_defaults == font_sizes
            assert {key: int(value.get()) for key, value in restarted_panel.font_size_vars.items()} == font_sizes
            assert restarted_panel.text_position_menus["text_horizontal"].get() == "중앙"
            assert restarted_panel.text_position_menus["text_vertical"].get() == "중앙"
            restarted_panel.destroy()
            host.destroy()
            assert panel.store.preferences()["text_position_defaults"] == expected_defaults
            panel.text_position_menus["text_horizontal"].set("오른쪽")
            panel.text_position_menus["text_vertical"].set("아래")
            panel._text_position_changed()
            previous_deck_path = panel.store.deck_directory(panel.deck) / "deck.json"
            previous_bytes = previous_deck_path.read_bytes()
            panel.mode.set("수동 모드")
            assert panel.prepare_article(confirm=False)
            for _ in range(200):
                settle(50)
                if not panel.busy:
                    break
            assert not panel.busy
            assert panel.deck["id"] != saved_deck_id
            assert all(slide["font_sizes"] == font_sizes for slide in panel.deck["slides"])
            assert all(slide["text_horizontal"] == "오른쪽" and slide["text_vertical"] == "아래" for slide in panel.deck["slides"])
            assert previous_deck_path.read_bytes() == previous_bytes
            # Reset uses this deck's initial defaults, not another task's style.
            panel.text_position_menus["text_horizontal"].set("왼쪽")
            panel._text_position_changed()
            panel._reset_slide()
            assert panel.text_position_menus["text_horizontal"].get() == "오른쪽"
            assert panel.text_position_menus["text_vertical"].get() == "아래"
            panel._load_deck(panel.store.load_deck(saved_deck_id))
            assert panel.store.preferences()["text_position_defaults"] == {"text_horizontal": "왼쪽", "text_vertical": "아래"}
            assert "랜덤" in panel.theme_menu.cget("values")
            panel.theme.set("랜덤")
            panel._theme_changed()
            # Per-slide sizes coexist with the shared default and random colors.
            for entry, value in zip(panel.size_entries, (900, 600)):
                entry.delete(0, "end")
                entry.insert(0, str(value))
            assert panel._apply_image_size()
            for entry, value in zip(panel.slide_size_entries, (900, 900)):
                entry.delete(0, "end")
                entry.insert(0, str(value))
            assert panel.slide_size_apply_button.invoke()
            assert panel.deck["slides"][0]["slide_size"] == [900, 900]
            assert panel.deck["slide_size"] == [900, 600]
            panel._move(1)
            assert [entry.get() for entry in panel.slide_size_entries] == ["900", "600"]
            for entry, value in zip(panel.slide_size_entries, (600, 900)):
                entry.delete(0, "end")
                entry.insert(0, str(value))
            assert panel._apply_slide_image_size()
            panel._move(-1)
            assert [entry.get() for entry in panel.slide_size_entries] == ["900", "900"]
            panel.slide_size_entries[0].delete(0, "end")
            panel.slide_size_entries[0].insert(0, "0")
            with patch("tistory_v2_ui.messagebox.showerror") as invalid:
                assert not panel._apply_slide_image_size()
                invalid.assert_called_once()
            assert panel.deck["slides"][0]["slide_size"] == [900, 900]
            panel.slide_size_entries[0].delete(0, "end")
            panel.slide_size_entries[0].insert(0, "900")
            panel._start("export")
            for _ in range(200):
                settle(50)
                if not panel.busy:
                    break
            assert not panel.busy
            expected_sizes = [(900, 900), (600, 900)] + [(900, 600)] * 8
            for index, expected in enumerate(expected_sizes):
                with Image.open(panel.store.deck_directory(panel.deck) / f"slide-{index+1:02d}.png") as image:
                    assert image.size == expected
            assert panel.store.preferences()["slide_size"] == [900, 600]
            mixed = panel.snapshot_for_publish()
            assert slide_ui.deck_slide_sizes(mixed) == expected_sizes
            host = app_module.ctk.CTkFrame(app)
            restarted_panel = slide_ui.TistoryV2Panel(
                host, app, panel.store.directory, panel.chrome_path_provider, panel.apply_images,
                workflow=panel.workflow, article_provider=app._v2_article_source)
            assert slide_ui.deck_slide_sizes(restarted_panel.deck) == expected_sizes
            assert [entry.get() for entry in restarted_panel.slide_size_entries] == ["900", "900"]
            restarted_panel.destroy()
            host.destroy()
            # A still-pending individual edit also reaches the upload snapshot.
            for entry, value in zip(panel.slide_size_entries, (800, 400)):
                entry.delete(0, "end")
                entry.insert(0, str(value))
            assert panel.snapshot_for_publish()["slides"][0]["slide_size"] == [800, 400]
            panel._use_common_slide_size()
            assert "slide_size" not in panel.deck["slides"][0]
            assert panel.deck["slides"][1]["slide_size"] == [600, 900]
            assert panel._apply_image_size()  # Explicit common Apply resets all ten.
            assert all("slide_size" not in slide for slide in panel.deck["slides"])
            assert slide_ui.deck_slide_sizes(panel.deck) == [(900, 600)] * 10
            colors = [slide["color_theme"] for slide in panel.deck["slides"]]
            assert len(set(colors)) > 1
            assert panel.store.preferences()["theme"] == "랜덤"
            assert colors[0] in panel.page_label.cget("text")
            with patch("tistory_v2.random.choice", side_effect=AssertionError("기존 장의 랜덤 색상 변경 금지")):
                panel._draw_preview()
                panel._move(1)
                panel._move(-1)
                panel._reset_slide()
                panel._load_deck(panel.store.load_deck(panel.deck["id"]))
                assert [slide["color_theme"] for slide in panel.deck["slides"]] == colors
                assert [slide["color_theme"] for slide in panel.snapshot_for_publish()["slides"]] == colors
            host = app_module.ctk.CTkFrame(app)
            restarted_panel = slide_ui.TistoryV2Panel(
                host, app, panel.store.directory, panel.chrome_path_provider, panel.apply_images,
                workflow=panel.workflow, article_provider=app._v2_article_source)
            assert restarted_panel.theme.get() == "랜덤"
            assert [slide["color_theme"] for slide in restarted_panel.deck["slides"]] == colors
            restarted_panel.destroy()
            host.destroy()
            assert panel.prepare_article(confirm=False)
            for _ in range(200):
                settle(50)
                if not panel.busy:
                    break
            assert not panel.busy and panel.deck["theme"] == "랜덤"
            assert all(slide["color_theme"] in slide_ui.THEME_CHOICES for slide in panel.deck["slides"])
            assert len({slide["color_theme"] for slide in panel.deck["slides"]}) > 1
            # A fixed selection must restore a single consistent theme.
            panel.theme.set("민트")
            panel._theme_changed()
            assert panel.deck["theme"] == "민트"
            assert all("color_theme" not in slide for slide in panel.deck["slides"])
            panel.theme.set("랜덤")
            panel._theme_changed()
            if args.preview_v2_seconds:
                app.geometry("1450x1000+25+25")
                settle(250)
                canvas = app.writing_scroll._parent_canvas
                y = panel.winfo_rooty() - app.writing_scroll.winfo_rooty()
                canvas.yview_moveto(max(0, y / max(1, app.writing_scroll.winfo_height())))
                print("Isolated V2 editor preview ready", flush=True)
                settle(min(180, args.preview_v2_seconds) * 1000)
                return
            with patch.object(panel, "worker", object()), patch.object(app_module.messagebox, "showinfo"):
                assert app._sidebar_activity_map()["tistory_v2"]
                assert app._writing_keyword_selection_locked()
                assert not app._set_writing_variant("writing")

            # Changing menus must not change an in-flight run's image mode.
            with patch.object(app, "_sidebar_activity_map", return_value={"tistory_v2": True}), \
                 patch.object(app_module.messagebox, "showinfo"):
                app._switch_page("writing")
                assert app.current_page == "tistory_v2"
            app.result_queue.put(("publish_done", {}))
            with patch.object(app_module.messagebox, "showinfo"):
                assert not app._set_writing_variant("writing")
            with app.result_queue.mutex:
                app.result_queue.queue.remove(("publish_done", {}))

            for geometry in ("1500x1000+30+35", "860x600+30+35"):
                app.geometry(geometry)
                settle(250)
                assert app.writing_page.winfo_ismapped()
                assert int(panel.font_size_panel.grid_info()["row"]) < int(panel.photo_menu.grid_info()["row"])
                for entry in panel.font_size_entries.values():
                    header = entry.master
                    right = max(child.winfo_x() + child.winfo_width() for child in header.winfo_children())
                    assert right <= header.winfo_width() + 1, (geometry, right, header.winfo_width())
                assert app.settings_nav_button.winfo_rooty() + app.settings_nav_button.winfo_height() < app.sidebar_version_label.winfo_rooty()
            with patch.object(app_module.messagebox, "showinfo"):
                app._switch_page("writing")
            settle(160)
            assert app.current_page == "writing", (app._sidebar_activity_map(), [event[0] for event in app.result_queue.queue])
            assert app.writing_page.winfo_ismapped()
            assert app.writing_page_title.cget("text") == "블로그글쓰기"
            assert app.writing_target_row.winfo_ismapped()
            assert app.topic_entry is shared_topic_entry
            assert edited_body in app.article_editor.get("1.0", "end")
            assert app.wordpress_settings.home_target_platform == original_target
            assert app.default_thumbnail_preset_index == original_default
            assert not app.writing_v2_stop_row.winfo_ismapped()
            app._switch_page("tistory_v2")
            app._open_writing_section("publish")
            settle(160)
            assert app.writing_v2_stop_row.winfo_ismapped()
            assert app.writing_v2_stop_button.cget("text") == "발행 중단"
            assert int(app.writing_v2_stop_row.grid_info()["row"]) > int(app.publish_progress_bar.grid_info()["row"])
            saved_id = panel.deck["id"]
            selection_before_stop = controls.selection()
            design_before_stop = app_module.copy.deepcopy(app.thumbnail_presets)
            old_scope = app._writing_result_queue()
            old_panel_events = panel.events
            old_panel_cancel = panel.cancel
            simulated_worker = Mock()
            simulated_worker.result_queue = old_scope
            simulated_worker.is_alive.return_value = True
            app.pipeline_worker = simulated_worker
            panel.worker = Mock()
            panel.worker.is_alive.return_value = True
            app.writing_auto_run_active = True
            app.writing_v2_publish_pending = True
            app.writing_v2_pending_auto = True
            app._tistory_v2_attention_reported = True
            app.reference_textbox.insert("1.0", "중단할 참고자료")
            app.topic_entry.delete(0, "end")
            app.topic_entry.insert(0, "중단할 키워드")
            stale_callback = Mock()
            app._after_writing(80, stale_callback)
            old_scope.put(("publish_pipeline_done", {"tistory": {"write_url": "https://never-publish.tistory.com/manage/newpost"}}))
            old_scope.put(("article_done", {"title": "이전 작업", "html": "이전 본문"}))
            old_panel_events.put(("error", "중단된 슬라이드 작업의 늦은 오류"))
            unrelated_dialog = Mock()
            app.manual_publish_dialog = unrelated_dialog
            app.manual_publish_dialog_platform = "blogspot"
            app.manual_publish_dialog_profile_scope = "other-profile"
            with patch.object(app, "_handle_publish_pipeline_success") as publish_done, \
                 patch.object(app_module.messagebox, "showerror") as popup, \
                 patch.object(app, "_handle_remote_agent_status") as unrelated_status:
                app.writing_v2_stop_button.invoke()
                app.result_queue.put(("remote_agent_status", {"status": "ready", "message": "unrelated preserved"}))
                poll_result_queue(app)
                settle(220)
                publish_done.assert_not_called()
                popup.assert_not_called()
                stale_callback.assert_not_called()
                unrelated_status.assert_called_once_with("ready", "unrelated preserved")
            simulated_worker.cancel.assert_called_once()
            assert old_scope.cancel_event.is_set() and old_panel_cancel.is_set()
            assert app._writing_run_queue is None
            assert app.pipeline_worker is None
            assert not app.writing_auto_run_active and not app.writing_v2_publish_pending
            assert not app._tistory_v2_attention_reported
            assert app.current_page == "tistory_v2" and app.active_writing_section == "topic"
            assert app.writing_progress_stage == 1 and app.writing_progress_fraction == 0
            assert not app.writing_completed_sections
            assert not app.topic_entry.get() and not app.manual_keyword_entry.get()
            assert not app.reference_textbox.get("1.0", "end").strip()
            assert not app.article_title_entry.get() and not app.article_editor.get("1.0", "end").strip()
            assert panel.deck is None and panel.events is not old_panel_events
            assert not panel.busy and not panel.store.preferences()["deck_id"]
            assert saved_id in [deck["id"] for deck in panel.store.list_decks()]
            assert controls.selection() == selection_before_stop
            assert app.thumbnail_presets == design_before_stop
            assert panel.store.preferences()["theme"] == "랜덤"
            assert app.manual_publish_dialog is unrelated_dialog
            unrelated_dialog.destroy.assert_not_called()
            app.manual_publish_dialog = None
            app.manual_publish_dialog_platform = app.manual_publish_dialog_profile_scope = ""
            app._stopped_writing_workers = []
            new_scope = app._writing_result_queue()
            assert new_scope is not old_scope and not new_scope.cancel_event.is_set()
            app.benchmark_mode_var.set(True)
            app._on_benchmark_mode_changed(save=False)
            app.benchmark_button.configure(state="disabled", text="가져오는 중...")
            app._stop_tistory_v2_publishing()
            assert app.benchmark_button.cget("state") == "normal"
            assert app.benchmark_button.cget("text") == "벤치마킹"
            app.benchmark_mode_var.set(False)
            app._on_benchmark_mode_changed(save=False)
            app._switch_page("writing")
            assert not app.writing_v2_stop_row.winfo_ismapped()
            print(f"{sys.platform} {args.theme}: V2 stop/reset fences late work, returns to stage1, preserves saved designs and unrelated jobs", flush=True)
            app.article_editor.delete("1.0", "end")
            app.article_editor.insert("1.0", original_article.strip())
            app.geometry("1500x1000+30+35")
            app._switch_page("home")
            settle(320)
            app.home_scroll._parent_canvas.yview_moveto(0)
            settle(200)
            print(f"{sys.platform} {args.theme}: V2 shared four-stage flow, prompt/profile/thumbnail isolation, upload routing and guards passed", flush=True)
            screenshot("home")
            assert tuple(app._home_platform_logo_cache) == (
                "wordpress", "tistory", "blogspot",
            )
            for platform, logo_label in app.home_publish_logo_labels.items():
                assert logo_label.cget("image") is app._home_platform_logo_cache[platform]
                assert app._home_platform_logo_cache[platform].cget("size") == (42, 42)
            assert tuple(app.home_publish_remaining_labels) == (
                "wordpress", "tistory", "blogspot",
            )
            assert app.home_adsense_card.winfo_ismapped(), (
                app.current_page, app.home_page.winfo_ismapped(),
                app.home_scroll._parent_canvas.yview(),
                app.home_scroll._parent_canvas.bbox("all"),
                app.home_adsense_card.winfo_y(),
            )
            assert int(app.home_adsense_card.grid_info()["row"]) == 0
            assert int(app.home_control_card.grid_info()["row"]) == 1
            assert int(app.home_keyword_cards_frame.grid_info()["row"]) == 2
            assert app.home_publish_summary_frame.master is app.home_page
            assert int(app.home_publish_summary_frame.grid_info()["row"]) == 2
            fixed_summary_y = app.home_publish_summary_frame.winfo_rooty()
            app.home_scroll._parent_canvas.yview_moveto(1)
            settle()
            assert app.home_publish_summary_frame.winfo_rooty() == fixed_summary_y
            app.home_scroll._parent_canvas.yview_moveto(0)
            settle()
            fixed_wheel_widget = app.home_publish_count_labels["wordpress"]
            assert app_module.MOUSE_WHEEL_ROUTER_BINDTAG in fixed_wheel_widget.bindtags()
            home_canvas = app.home_scroll._parent_canvas
            home_canvas.yview_moveto(1)
            settle()
            home_before_wheel = home_canvas.yview()[0]
            if home_before_wheel > 0:
                wheel_event = type(
                    "WheelEvent",
                    (),
                    {"widget": fixed_wheel_widget, "delta": 4, "num": 0},
                )()
                assert app._route_mousewheel(wheel_event) == "break"
                settle()
                assert home_canvas.yview()[0] < home_before_wheel
            home_canvas.yview_moveto(0)
            settle()
            assert app.home_refresh_button.master is app.home_control_card
            assert int(app.home_refresh_button.grid_info()["row"]) == 1
            assert int(app.home_refresh_button.grid_info()["column"]) == 2
            assert int(app.home_refresh_button.cget("width")) == 165
            assert int(app.home_launch_status_label.grid_info()["row"]) == 1
            assert int(app.home_launch_status_label.grid_info()["columnspan"]) == 2
            assert tuple(app.home_blog_menus) == ("wordpress", "tistory", "blogspot")
            assert tuple(app._home_platform_compact_logo_cache) == (
                "wordpress", "tistory", "blogspot",
            )
            for logo in app._home_platform_compact_logo_cache.values():
                assert logo.cget("size") == (20, 20)
            assert int(app.home_prompt_menu.cget("height")) == 27
            assert int(app.home_thumbnail_menu.cget("height")) == 27
            assert all(int(menu.cget("height")) == 27 for menu in app.home_blog_menus.values())
            app._on_home_thumbnail_selected("썸네일·카드2")
            assert app.default_thumbnail_preset_index == 1
            assert app.active_thumbnail_preset_index == 1
            app._on_home_thumbnail_selected("썸네일·카드1")
            assert app.default_thumbnail_preset_index == 0
            assert app.active_thumbnail_preset_index == 0
            assert resolved_color(app.home_adsense_title_label, "text_color") == "#ffffff"
            assert tuple(app.home_adsense_value_labels) == (
                "today", "yesterday", "last_7_days", "month_to_date", "balance",
            )
            for value_label in app.home_adsense_value_labels.values():
                assert resolved_color(value_label, "text_color") == "#ffffff"

            class RunningWorker:
                @staticmethod
                def is_alive():
                    return True

            app.article_worker = RunningWorker()
            app.naver_blog_worker = RunningWorker()
            app.naver_kin_automation_worker = RunningWorker()
            settle(320)
            for page_name in ("writing", "naver_blog", "naver_kin"):
                assert app._sidebar_activity_states[page_name]
                assert app._sidebar_activity_bars[page_name].winfo_ismapped()
                assert app._sidebar_activity_bars[page_name].master is app.sidebar_frame
            assert app.writing_nav_button.cget("text") == "블로그글쓰기"
            assert app.naver_blog_nav_button.cget("text") == "N블로그자동화"
            assert app.naver_kin_nav_button.cget("text") == "N지식인자동화"
            screenshot("concurrent-activity-shimmers")
            app.article_worker = None
            app.naver_blog_worker = None
            app.naver_kin_automation_worker = None
            settle(320)
            assert not any(app._sidebar_activity_states.values())
            assert all(
                not bar.winfo_ismapped()
                for bar in app._sidebar_activity_bars.values()
            )
            sidebar_palette = app._theme_palette()
            for page_name, button_name in {
                "home": "home_nav_button",
                "writing": "writing_nav_button",
                "automation": "automation_nav_button",
                "naver_blog": "naver_blog_nav_button",
                "tistory_v2": "tistory_v2_nav_button",
                "naver_kin": "naver_kin_nav_button",
                "public_data": "public_data_nav_button",
                "thumbnails": "thumbnail_nav_button",
                "prompts": "prompt_nav_button",
                "settings": "settings_nav_button",
            }.items():
                sidebar_button = getattr(app, button_name)
                expected_fill = (
                    sidebar_palette["selected"]
                    if page_name == app.current_page
                    else sidebar_palette["sidebar"]
                )
                assert resolved_color(sidebar_button, "bg_color") == sidebar_palette["sidebar"]
                assert resolved_color(sidebar_button, "fg_color") == expected_fill
                assert resolved_color(sidebar_button._canvas, "bg") == sidebar_palette["sidebar"]
            screenshot("activity-shimmers-stopped")

            app._switch_page("settings")
            app._switch_settings_section("theme")
            settle()
            assert app.font_menu.cget("values") == list(app_module.FONT_OPTIONS)
            assert app.font_menu.get() == app_module.SYSTEM_FONT
            assert int(app.font_menu.master.grid_info()["row"]) == 1
            assert int(app.sidebar_menu_settings_status_label.master.grid_info()["row"]) == 2
            assert app._read_wordpress_settings().app_font == app_module.SYSTEM_FONT
            screenshot("font-settings")
            with patch.object(app, "_restart_after_theme_change") as restart:
                app.font_menu.set("나눔스퀘어 네오")
                app._on_font_changed("나눔스퀘어 네오")
                assert app.wordpress_settings.app_font == "나눔스퀘어 네오"
                assert app.font_menu.cget("state") == "disabled"
                assert app.theme_menu.cget("state") == "disabled"
                settle(500)
                restart.assert_called_once()
            app._theme_restart_pending = False
            app.wordpress_settings.app_font = app_module.SYSTEM_FONT
            app.font_menu.set(app_module.SYSTEM_FONT)
            app.font_menu.configure(state="normal")
            app.theme_menu.configure(state="normal")
            print(f"{sys.platform} {args.theme}: font settings placement, persistence and restart callback passed", flush=True)
            app._switch_settings_section("ai")
            app._switch_settings_tab("adsense")
            app.update()
            settle()
            screenshot("adsense-settings")
            assert app.adsense_connect_button.cget("text") == "Google 계정 연결 · 로그인"
            assert tuple(app.adsense_settings_value_labels) == (
                "today", "yesterday", "last_7_days", "month_to_date", "balance",
            )
            for value_label in app.adsense_settings_value_labels.values():
                assert resolved_color(value_label, "text_color") == "#ffffff"

            app._switch_settings_section("history")
            settle(900)
            screenshot("settings-history")
            assert app.settings_history_section_button.cget("text") == "히스토리"
            assert app.history_scroll.winfo_ismapped()
            assert not app.settings_ai_tabs_header.winfo_ismapped()
            assert app.history_rendered
            assert len(app.history_cards_frame.winfo_children()) == len(
                app_module.BLOG_HELPER_HISTORY
            )
            app.history_search_entry.insert(0, "60px")
            app._render_history_cards()
            settle(120)
            assert len(app.history_cards_frame.winfo_children()) == 1
            assert "검색 결과 1개" in app.history_result_label.cget("text")
            app._clear_history_filters()
            settle(120)
            assert len(app.history_cards_frame.winfo_children()) == len(
                app_module.BLOG_HELPER_HISTORY
            )

            app._switch_page("writing")
            # Initialize the native window before scheduling the quit timer.
            # CTk's first Windows mainloop can pump events during setup and
            # consume that timer before Tk's actual event loop has started.
            app.update()
            settle()
            assert app._selected_writing_targets() == ["wordpress"]
            assert tuple(app.writing_blog_menus) == ("wordpress", "tistory", "blogspot")
            assert int(app.writing_thumbnail_menu.cget("height")) == 27
            assert int(app.writing_header_prompt_menu.cget("height")) == 27
            app._open_writing_section("topic")
            assert app.link_card.master is app.writing_section_bodies["topic"]
            assert int(app.link_card.grid_info()["row"]) == 3
            app._add_link_row()
            settle()
            empty_row = app.link_rows[-1]
            for entry_key in ("button_entry", "url_entry", "width_entry"):
                entry = empty_row[entry_key]
                assert entry.get() == ""
                assert entry._placeholder_text_active, entry_key
                assert entry._entry.get() == entry.cget("placeholder_text"), entry_key
            screenshot("body-link-placeholders")
            app._remove_link_row(empty_row)
            app._add_link_row({"button_text": "WP 링크", "url": "https://example.com/wp", "position": "본문상단"})
            app._on_writing_target_changed("tistory")
            assert app._current_writing_links() == []
            app._add_link_row({"button_text": "티스토리 링크", "url": "https://example.com/tistory", "position": "본문중간"})
            app._on_writing_target_changed("wordpress")
            assert app._current_writing_links()[0]["button_text"] == "WP 링크"
            app._on_writing_target_changed("tistory")
            assert app._current_writing_links()[0]["button_text"] == "티스토리 링크"
            app._remove_link_row(app.link_rows[0])
            app._on_writing_target_changed("wordpress")
            app._remove_link_row(app.link_rows[0])
            app._on_writing_target_changed("tistory")
            assert app._current_writing_links() == []
            app._on_writing_target_changed("wordpress")
            screenshot("direct-topic-input")
            app._on_inline_images_provider_changed(
                app_module.INLINE_IMAGES_PROVIDER_MANUAL
            )
            app._open_writing_section("keyword")
            settle()
            assert app.writing_manual_image_drop_zone.winfo_ismapped()
            assert app.writing_manual_image_drop_zone._blog_helper_drop_enabled
            assert app.writing_manual_image_drop_zone.dnd_bind("<<Drop>>")
            assert not hasattr(app, "writing_auto_progress_status")
            for step in range(1, 5):
                assert app._bootstrap_sidebar_icon_image(f"{step}-circle", "#2563eb") is not None
                assert app._bootstrap_sidebar_icon_image(f"{step}-circle-fill", "#2563eb") is not None

            app._render_keyword_choices([
                app_module.KeywordInsight(
                    keyword=f"추천 키워드 {index}",
                    score=100 - index,
                    reasons=["화면 표시 확인"],
                    sources=["naver"],
                    categories=["테스트"],
                    source_urls={"naver": f"https://example.com/{index}"},
                )
                for index in range(1, 11)
            ])
            app._open_writing_section("keyword")
            settle()
            keyword_rows = app.keyword_choice_frame.winfo_children()
            assert len(keyword_rows) == 10
            assert app.keyword_refresh_button.winfo_ismapped()
            assert app.keyword_refresh_button.cget("text") == "새로고침"
            assert (
                app.keyword_refresh_button.winfo_rootx()
                > app.writing_section_content_title_labels["keyword"].winfo_rootx()
            )
            keyword_viewport = app.keyword_choice_frame._parent_canvas
            assert (
                keyword_rows[-1].winfo_rooty() + keyword_rows[-1].winfo_height()
                <= keyword_viewport.winfo_rooty() + keyword_viewport.winfo_height() + 2
            )
            screenshot("ten-recommended-keywords")
            # CTkScrollableFrame.cget/configure delegate to its outer CTkFrame,
            # not the native inner Frame visible in the gaps between rows.
            # Check the actual painted surfaces while a completion popup is up.
            keyword_backgrounds = [
                (app.keyword_choice_frame,
                 app_module.tk.Frame.cget(app.keyword_choice_frame, "background")),
                (keyword_viewport, keyword_viewport.cget("background")),
                *[(row._canvas, row._canvas.cget("background")) for row in keyword_rows],
            ]
            app._show_reference_collection_complete_dialog("추천 키워드 1", 14)
            settle(650)
            from modal_backdrop import dim_color
            for surface, original in keyword_backgrounds:
                actual = app_module.tk.Frame.cget(surface, "background")
                assert actual == dim_color(surface, original), (
                    "undimmed keyword list background", surface, actual, original
                )
            assert resolved_color(app.reference_collection_dialog, "fg_color") == app._theme_palette()["shell"]
            screenshot("reference-dialog-uniform-dim")
            app._close_reference_collection_dialog()
            settle(120)
            for surface, original in keyword_backgrounds:
                assert app_module.tk.Frame.cget(surface, "background") == original
            app._clear_keyword_choices()
            settle()

            # The header chooses exactly one publishing target.
            app.writing_auto_progress_switch.toggle()
            assert app.writing_auto_progress_var.get()
            app._on_writing_target_changed("tistory")
            assert app._selected_writing_targets() == ["tistory"]
            assert app.home_target_platform_var.get() == "tistory"
            app._on_writing_target_changed("wordpress")
            app.writing_auto_progress_switch.toggle()

            # The selected prompt belongs to the publish target's history, not
            # to the prompt's own platform. Cross-platform choices must round-trip.
            app._on_writing_target_changed("wordpress")
            app.writing_prompt_menu.set("티스토리 · 기본")
            app._on_writing_prompt_selected("티스토리 · 기본")
            assert app.wordpress_settings.writing_target_prompt_ids["wordpress"] == "tistory-default"
            app._on_home_thumbnail_selected("썸네일·카드2")

            app._on_writing_target_changed("tistory")
            app.writing_prompt_menu.set("블로그스팟 · 기본")
            app._on_writing_prompt_selected("블로그스팟 · 기본")
            app._on_home_thumbnail_selected("썸네일·카드3")
            active_tistory = app_module.service_profile_by_name(
                app.wordpress_settings.tistory_profiles,
                app.wordpress_settings.tistory_active_profile,
            )
            assert active_tistory["last_prompt_id"] == "blogspot-default"

            app._on_writing_target_changed("wordpress")
            assert app.writing_prompt_menu.get() == "티스토리 · 기본"
            assert app.home_prompt_menu.get() == "티스토리 · 기본"
            assert app.writing_thumbnail_menu.get() == "썸네일·카드2"
            app._on_writing_target_changed("tistory")
            assert app.writing_prompt_menu.get() == "블로그스팟 · 기본"
            assert app.home_prompt_menu.get() == "블로그스팟 · 기본"
            assert app.writing_thumbnail_menu.get() == "썸네일·카드3"

            app.wordpress_settings.tistory_active_profile = "티스토리 2"
            app._on_writing_target_changed("tistory")
            app._on_home_thumbnail_selected("썸네일·카드1")
            app._on_home_prompt_selected("워드프레스 · 기본")
            assert app.wordpress_settings.blog_writing_preferences[
                "tistory:tistory_2"
            ] == {"thumbnail_preset": 0, "prompt_id": "wordpress-default", "writing_links": []}
            app.wordpress_settings.tistory_active_profile = "티스토리 1"
            app._on_writing_target_changed("tistory")
            assert app.writing_thumbnail_menu.get() == "썸네일·카드3"
            assert app.writing_prompt_menu.get() == "블로그스팟 · 기본"

            app._on_writing_target_changed("blogspot")
            app._on_home_thumbnail_selected("썸네일·카드1")

            app.article_title_entry.delete(0, "end")
            app.article_title_entry.insert(0, "오늘의 여행 이야기")
            app.article_editor.delete("1.0", "end")
            article = "<h2>가볍게 떠나는 여행</h2><p>아침의 풍경과 오늘의 기록입니다.</p>"
            app.article_editor.insert("1.0", article)
            app.thumbnail_auto_title_var.set(False)
            app.thumbnail_prompt_preview.delete("1.0", "end")
            app.thumbnail_prompt_preview.insert("1.0", "오늘의 여행\n소중한 순간")
            app.cardnews_border_color_menu.set("빨간색")
            app._save_active_thumbnail_preset()
            assert len(app.thumbnail_preset_buttons) == 5
            assert app.default_thumbnail_preset_index == 0
            assert int(app.writing_section_content_title_labels["publish"].grid_info()["row"]) == 0
            assert int(app.thumbnail_card_set_selector_host.grid_info()["row"]) == 1
            assert app.thumbnail_preset_buttons[0].cget("text").startswith("썸네일·카드1")
            app._switch_thumbnail_preset(1)
            app.thumbnail_auto_title_var.set(False)
            app.thumbnail_prompt_preview.delete("1.0", "end")
            app.thumbnail_prompt_preview.insert("1.0", "두 번째 블로그 전용")
            app.thumbnail_border_color_menu.set("파란색")
            app.cardnews_border_color_menu.set("민트색")
            app._on_thumbnail_control_changed()
            app._on_cardnews_control_changed()
            app._set_default_thumbnail_preset()
            assert app.default_thumbnail_preset_index == 1
            assert app.set_default_thumbnail_button.cget("state") == "disabled"
            app._switch_thumbnail_preset(0)
            assert app.thumbnail_prompt_preview.get("1.0", "end").strip() == "오늘의 여행\n소중한 순간"
            assert app.thumbnail_border_color_menu.get() != "파란색"
            assert app.cardnews_border_color_menu.get() == "빨간색"
            preset_settings = app._read_wordpress_settings(include_prompts=False)
            assert preset_settings.thumbnail_default_preset == 1
            assert preset_settings.thumbnail_active_preset == 0
            assert len(preset_settings.thumbnail_presets) == 5
            assert preset_settings.thumbnail_text == "두 번째 블로그 전용"
            assert preset_settings.thumbnail_border_color == "파란색"
            assert preset_settings.cardnews_border_color == "민트색"
            assert preset_settings.thumbnail_presets[1]["cardnews_style"]["border_color"] == "민트색"
            exported_default = {}

            def capture_default_thumbnail(destination):
                exported_default["text"] = app.thumbnail_prompt_preview.get("1.0", "end").strip()
                exported_default["border"] = app.thumbnail_border_color_menu.get()
                return destination

            with patch.object(app, "_export_thumbnail_png", side_effect=capture_default_thumbnail):
                expected_path = Path(directory) / "default-thumbnail.png"
                assert app._export_default_thumbnail_png(expected_path) == expected_path
            assert exported_default == {
                "text": "두 번째 블로그 전용",
                "border": "파란색",
            }
            assert app.active_thumbnail_preset_index == 0
            assert app.thumbnail_prompt_preview.get("1.0", "end").strip() == "오늘의 여행\n소중한 순간"
            assert app.cardnews_border_color_menu.get() == "빨간색"
            app._activate_default_thumbnail_card_set_for_publish()
            assert app.active_thumbnail_preset_index == 1
            assert app.thumbnail_prompt_preview.get("1.0", "end").strip() == "두 번째 블로그 전용"
            assert app.cardnews_border_color_menu.get() == "민트색"
            app._switch_thumbnail_preset(0)
            automation_card_set = {}

            def capture_automation_cardnews(destination, **_kwargs):
                automation_card_set["border"] = app.cardnews_border_color_menu.get()
                return destination

            automation_item = {
                "id": "card-set-check",
                "title": "세트 자동화 확인",
                "article_html": "<h2>핵심 정보</h2><p>카드뉴스 세트를 확인하는 본문입니다.</p>",
            }
            automation_settings = app_module.WordPressSettings(
                inline_images_enabled=True,
                inline_images_provider="카드뉴스 생성",
                inline_images_count=1,
            )
            with patch.object(
                app,
                "_export_body_cardnews_png",
                side_effect=capture_automation_cardnews,
            ):
                assert app._apply_cardnews_to_automation_queue_item(
                    automation_item,
                    automation_settings,
                )
            assert automation_card_set["border"] == "민트색"
            assert app.active_thumbnail_preset_index == 0
            assert app.cardnews_border_color_menu.get() == "빨간색"
            app.cardnews_specs = [
                {"heading": "가볍게 떠나는 여행", "summary": "나를 위한 하루를 기록해 보세요."},
                {"heading": "천천히 즐기는 풍경", "summary": "작은 순간에서 새로운 영감을 만나요."},
            ]
            app._render_active_cardnews_slide()
            app._generate_thumbnail_preview()
            thumbnail_svg = app._build_thumbnail_svg(400, 400, 56)
            cardnews_svg = app._build_body_cardnews_svg(1024, 1024, "제목", "요약", 1, 2)
            screenshot("top")

            for key in app_module.WRITING_STAGE_LABELS:
                print(f"{sys.platform} {args.theme}: accordion {key}", flush=True)
                if key != app.active_writing_section:
                    app.writing_section_toggle_buttons[key].invoke()
                settle()
                assert app.active_writing_section == key
                assert app.writing_section_cards[key].winfo_ismapped()
                assert sum(card.winfo_ismapped() for card in app.writing_section_cards.values()) == 1
                rail_y = app.writing_step_rail.winfo_rooty()
                app.writing_scroll._parent_canvas.yview_moveto(1)
                settle()
                assert app.writing_step_rail.winfo_rooty() == rail_y
                assert all(button.winfo_ismapped() for button in app.writing_section_toggle_buttons.values())
                app.writing_section_toggle_buttons[key].invoke()
                settle()
                assert app.active_writing_section == ""
                assert not any(card.winfo_ismapped() for card in app.writing_section_cards.values())
                app.writing_section_toggle_buttons[key].invoke()

            assert app.article_editor.get("1.0", "end").strip() == article
            assert app._build_thumbnail_svg(400, 400, 56) == thumbnail_svg
            assert app._build_body_cardnews_svg(1024, 1024, "제목", "요약", 1, 2) == cardnews_svg
            app.cardnews_next_button.invoke()
            assert app.active_cardnews_slide_index == 1
            app.cardnews_prev_button.invoke()
            assert app.active_cardnews_slide_index == 0
            app._set_writing_section_completed("keyword")
            assert "✓" in app.writing_section_title_labels["keyword"].cget("text")
            app._set_writing_progress(3, "글 확인 중", 0.5)
            assert app.writing_fixed_progress_bar.get() == 0.625

            # A textbox at its own upper edge must hand an upward wheel gesture
            # to the enclosing page instead of swallowing it intermittently.
            app._open_writing_section("publish")
            settle()
            app._switch_page("thumbnails")
            settle()
            writing_canvas = app.thumbnails_scroll._parent_canvas
            wheel_textbox = app.thumbnail_prompt_preview._textbox
            assert app_module.MOUSE_WHEEL_ROUTER_BINDTAG in wheel_textbox.bindtags()
            wheel_textbox.yview_moveto(0)
            writing_canvas.yview_moveto(1)
            settle()
            writing_before_wheel = writing_canvas.yview()[0]
            wheel_event = type(
                "WheelEvent",
                (),
                {"widget": wheel_textbox, "delta": 4, "num": 0},
            )()
            assert app._route_mousewheel(wheel_event) == "break"
            settle()
            assert writing_canvas.yview()[0] < writing_before_wheel

            app._switch_page("writing")
            palette = app._theme_palette()
            app._finish_theme_paint(force=True)
            settle()
            for widget in (app.thumbnail_prompt_preview, app.cardnews_heading_entry):
                assert widget.cget("fg_color") == palette["input"]
                assert widget.cget("text_color") == palette["text"]
            for widget in (app.save_thumbnail_button, app.generate_cardnews_button):
                assert widget.cget("text_color") == "#ffffff"
                assert widget.cget("fg_color") == "#2563eb"
            assert_segmented_contrast(app.tistory_input_mode_selector)
            assert_segmented_contrast(app.tistory_save_mode_selector)
            assert_segmented_contrast(app.blogspot_save_mode_selector)
            original_input_mode = app.tistory_input_mode_selector.get()
            alternate_input_mode = next(
                value
                for value in app.tistory_input_mode_selector._buttons_dict
                if value != original_input_mode
            )
            app.tistory_input_mode_selector._buttons_dict[alternate_input_mode].invoke()
            assert app.tistory_input_mode_var.get() == alternate_input_mode
            assert_segmented_contrast(app.tistory_input_mode_selector)
            app.tistory_input_mode_selector._buttons_dict[original_input_mode].invoke()

            # Both compact laptop and wider desktop layouts retain all controls.
            for geometry in ("1500x1000+30+35", "1100x900+30+35", "860x680+30+35"):
                print(f"{sys.platform} {args.theme}: layout {geometry}", flush=True)
                app.geometry(geometry)
                settle()
                app._switch_page("home")
                settle()
                assert int(app.home_prompt_frame.grid_info()["row"]) == 0
                assert all(int(group.grid_info()["row"]) == 0 for group in app.home_target_groups.values())
                assert (
                    app.home_target_row.winfo_rootx() + app.home_target_row.winfo_width()
                    <= app.home_prompt_frame.winfo_rootx() + 2
                ), geometry
                home_canvas = app.home_control_canvas
                if not home_canvas.winfo_ismapped():
                    settle(120)
                needs_scroll = app.home_control_top.winfo_reqwidth() > home_canvas.winfo_width() + 2
                assert bool(app.home_control_scrollbar.winfo_ismapped()) == needs_scroll, (
                    geometry,
                    app.home_control_top.winfo_reqwidth(),
                    home_canvas.winfo_width(),
                    bool(app.home_control_scrollbar.winfo_ismapped()),
                )
                if needs_scroll:
                    assert home_canvas.xview()[1] - home_canvas.xview()[0] < 1
                    home_canvas.xview_moveto(1)
                    settle()
                    assert app.home_prompt_menu.winfo_rootx() + app.home_prompt_menu.winfo_width() <= (
                        home_canvas.winfo_rootx() + home_canvas.winfo_width() + 2
                    )
                    home_canvas.xview_moveto(0)
                    settle()
                screenshot("home-" + geometry.split("+", 1)[0])
                app._switch_page("writing")
                settle()
                screenshot("writing-" + geometry.split("+", 1)[0])
                assert app.topic_entry.winfo_rooty() == app.direct_keyword_start_button.winfo_rooty()
                assert app.topic_entry.winfo_rootx() + app.topic_entry.winfo_width() < (
                    app.direct_keyword_start_button.winfo_rootx()
                )
                writing_canvas = app.writing_control_canvas
                writing_needs_scroll = (
                    app.writing_control_row.winfo_reqwidth()
                    > writing_canvas.winfo_width() + 2
                )
                assert bool(app.writing_control_scrollbar.winfo_ismapped()) == writing_needs_scroll
                assert app.writing_control_scrollbar.cget("height") == app.home_control_scrollbar.cget("height")
                if not writing_needs_scroll:
                    assert abs(
                        writing_canvas.winfo_rootx() + writing_canvas.winfo_width()
                        - app.writing_header_prompt_menu.winfo_rootx()
                        - app.writing_header_prompt_menu.winfo_width()
                    ) <= 16, geometry
                if writing_needs_scroll:
                    writing_canvas.xview_moveto(1)
                    settle()
                    assert (
                        app.writing_header_prompt_menu.winfo_rootx()
                        + app.writing_header_prompt_menu.winfo_width()
                        <= writing_canvas.winfo_rootx() + writing_canvas.winfo_width() + 2
                    )
                    writing_canvas.xview_moveto(0)
                    settle()
                assert app.writing_step_rail.winfo_width() <= app.writing_page.winfo_width()
                selector_width = (
                    app.thumbnail_card_set_selector_host.winfo_width()
                    / app.thumbnail_card_set_selector_host._get_widget_scaling()
                )
                selector_columns = 5 if selector_width >= 1000 else 3 if selector_width >= 620 else 2
                expected_default_row = (5 + selector_columns - 1) // selector_columns if selector_columns < 5 else 0
                assert int(app.set_default_thumbnail_button.grid_info()["row"]) == expected_default_row
                app.writing_scroll._parent_canvas.yview_moveto(0)
                settle()
                canvas = app.writing_scroll._parent_canvas
                app._switch_page("thumbnails")
                settle()
                canvas = app.thumbnails_scroll._parent_canvas
                for workspace in (app.thumbnail_design_workspace, app.cardnews_design_workspace):
                    assert workspace.winfo_rootx() + workspace.winfo_width() <= canvas.winfo_rootx() + canvas.winfo_width() + 2
                    assert workspace._design_columns == (2 if workspace.winfo_width() / workspace._get_widget_scaling() >= 960 else 1)
                    pending = list(workspace.winfo_children())
                    while pending:
                        widget = pending.pop()
                        pending.extend(widget.winfo_children())
                        if isinstance(widget, (app_module.ctk.CTkButton, app_module.ctk.CTkEntry, app_module.ctk.CTkOptionMenu)):
                            assert widget.winfo_rootx() + widget.winfo_width() <= workspace.winfo_rootx() + workspace.winfo_width() + 2, (geometry, widget, widget.winfo_width())
                screenshot(geometry.split("+", 1)[0])
                app._switch_page("writing")
                settle()
                canvas = app.writing_scroll._parent_canvas
                for button in (app.publish_pipeline_button, app.queue_post_button,
                               app.tistory_retry_button, app.open_published_post_button):
                    assert button.winfo_rootx() + button.winfo_width() <= canvas.winfo_rootx() + canvas.winfo_width() + 2
            app.geometry("1500x1000+30+35")
            settle()
            app.writing_scroll._parent_canvas.yview_moveto(0)
            settle()
            screenshot("thumbnail")
            canvas = app.writing_scroll._parent_canvas
            total_height = canvas.bbox("all")[3]
            target_y = app.cardnews_design_workspace.winfo_y() + app.writing_section_bodies["publish"].winfo_y()
            canvas.yview_moveto(target_y / total_height)
            settle()
            screenshot("cardnews")
            app._reset_writing_accordion_state()
            settle()
            assert app.active_writing_section == "topic"
            assert not app.writing_completed_sections
            assert app.writing_section_cards["topic"].winfo_ismapped()

            # Naver Knowledge iN keeps direct URL collection and progress visible
            # in both themes without exercising a real account or network.
            app._switch_page("naver_kin")
            settle(700)
            visible_texts = []
            pending = [app.naver_kin_page]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                try:
                    visible_texts.append(str(widget.cget("text")))
                except Exception:
                    pass
            visible_copy = "\n".join(visible_texts)
            assert "네이버 지식인 최신 질문을 확인하고" not in visible_copy
            assert "자동화 흐름" not in visible_copy
            assert app.naver_kin_start_button.cget("text") == "질문 목록 수집"
            assert app.naver_kin_direct_collect_button.cget("text") == "수집"
            assert_segmented_contrast(app.naver_kin_automation_mode_control)
            assert set(app.naver_kin_tab_buttons) == {"writing", "settings"}
            writing_profile_radios = [
                widget
                for widget in app.naver_kin_writing_profile_frame.winfo_children()
                if isinstance(widget, app_module.ctk.CTkRadioButton)
            ]
            assert len(writing_profile_radios) == 3
            screenshot("naver-kin-writing")
            app._switch_naver_kin_tab("settings")
            settle()
            settings_profile_cards = app.naver_kin_profile_cards_frame.winfo_children()
            assert len(settings_profile_cards) == 3
            assert len({
                app_module.naver_kin_profile_scope(profile, index)
                for index, profile in enumerate(app._current_naver_kin_profiles())
            }) == 3
            screenshot("naver-kin-settings")
            app._switch_naver_kin_tab("writing")
            settle()
            screenshot("naver-kin-writing-return")
            assert list(app.naver_kin_collect_count_menu.cget("values")) == [
                f"{count}개" for count in range(1, 11)
            ]
            assert list(app.naver_kin_collect_interval_menu.cget("values")) == [
                "5분", "10분", "15분", "20분", "30분",
                "1시간", "2시간", "4시간", "6시간", "12시간", "24시간",
            ]
            assert (
                app.naver_kin_collect_count_menu.winfo_rootx()
                < app.naver_kin_collect_interval_menu.winfo_rootx()
            )
            assert app.naver_kin_fixed_progress_panel.winfo_ismapped()
            reference_native_textbox = app.naver_kin_reference_textbox._textbox
            assert reference_native_textbox.bindtags()[0] == app_module.TEXT_EDITING_SHORTCUT_BINDTAG
            reference_shortcut_text = "한글 모드 전체 선택 확인"
            reference_native_textbox.delete("1.0", "end")
            reference_native_textbox.insert("1.0", reference_shortcut_text)
            reference_native_textbox.focus_force()
            settle(40)
            if sys.platform == "darwin":
                app._handle_text_editing_shortcut(
                    type(
                        "MacKoreanCommandAEvent",
                        (),
                        {
                            "widget": reference_native_textbox,
                            "keysym": "??",
                            "keycode": 97,
                        },
                    )()
                )
            else:
                reference_native_textbox.event_generate(
                    "<Control-KeyPress>",
                    keycode=65,
                    when="now",
                )
            settle(40)
            assert reference_native_textbox.get("sel.first", "sel.last").rstrip("\n") == reference_shortcut_text
            reference_native_textbox.tag_remove("sel", "1.0", "end")
            reference_native_textbox.delete("1.0", "end")
            assert all(
                not frame.winfo_ismapped()
                for name, frame in app._page_frame_map().items()
                if name != "naver_kin"
            )
            app.naver_kin_scroll._parent_canvas.yview_moveto(0)
            settle()
            fixed_y = app.naver_kin_fixed_progress_panel.winfo_rooty()
            app.naver_kin_scroll._parent_canvas.yview_moveto(1)
            settle()
            assert app.naver_kin_fixed_progress_panel.winfo_rooty() == fixed_y, (
                fixed_y,
                app.naver_kin_fixed_progress_panel.winfo_rooty(),
            )
            app._set_naver_kin_progress("워드프레스 글 발행을 준비하고 있습니다...", 0.42)
            assert app.naver_kin_fixed_progress_bar.get() == 0.42
            assert app.naver_kin_fixed_progress_percent.cget("text") == "42%"
            app._set_naver_kin_progress("N지식인 답변 등록 완료", state="complete")
            assert app.naver_kin_fixed_progress_bar.get() == 1.0
            assert app.naver_kin_fixed_progress_badge.cget("text") == "완료"
            print(f"{sys.platform} {args.theme}: Naver Knowledge iN tabs, three profiles, direct URL and fixed progress UI passed")

            # NBlog exposes six independent slots in matching 3 x 2 grids on
            # both the writing and settings tabs.
            app._switch_page("naver_blog")
            app._switch_naver_blog_tab("writing")
            settle(700)
            writing_radios = [
                widget
                for widget in app.naver_blog_writing_profile_frame.winfo_children()
                if isinstance(widget, app_module.ctk.CTkRadioButton)
            ]
            assert len(writing_radios) == 6
            assert {
                (int(widget.grid_info()["row"]), int(widget.grid_info()["column"]))
                for widget in writing_radios
            } == {(1, 0), (1, 1), (1, 2), (2, 0), (2, 1), (2, 2)}
            assert_segmented_contrast(app.naver_blog_image_mode_control)
            assert_segmented_contrast(app.naver_blog_automation_mode_control)
            app.naver_blog_image_mode_var.set("이미지 수동")
            app._on_naver_blog_image_mode_changed("이미지 수동")
            settle()
            assert app.naver_blog_manual_image_drop_zone.grid_info()
            assert app.naver_blog_manual_image_drop_zone._blog_helper_drop_enabled
            assert app.naver_blog_manual_image_drop_zone.dnd_bind("<<Drop>>")
            screenshot("naver-blog-writing")

            app._switch_naver_blog_tab("settings")
            settle(700)
            profile_cards = app.naver_profile_cards_frame.winfo_children()
            assert len(profile_cards) == 6
            assert {
                (int(card.grid_info()["row"]), int(card.grid_info()["column"]))
                for card in profile_cards
            } == {(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)}
            print(f"{sys.platform} {args.theme}: NBlog six-profile 3 x 2 layouts passed")

            # Every page uses the same white-theme contrast rule: selected or
            # otherwise dark buttons have white labels, and segmented controls
            # never reintroduce native outline seams.
            sidebar_button_names = {
                "home": "home_nav_button",
                "writing": "writing_nav_button",
                "automation": "automation_nav_button",
                "naver_blog": "naver_blog_nav_button",
                "tistory_v2": "tistory_v2_nav_button",
                "naver_kin": "naver_kin_nav_button",
                "public_data": "public_data_nav_button",
                "thumbnails": "thumbnail_nav_button",
                "prompts": "prompt_nav_button",
                "settings": "settings_nav_button",
            }
            assert all(
                getattr(app, button_name).cget("hover") is False
                for button_name in sidebar_button_names.values()
            )
            sidebar_palette = app._theme_palette()
            for page_name, button_name in sidebar_button_names.items():
                sidebar_button = getattr(app, button_name)
                expected_fill = (
                    sidebar_palette["selected"]
                    if page_name == app.current_page
                    else sidebar_palette["sidebar"]
                )
                assert resolved_color(sidebar_button, "bg_color") == sidebar_palette["sidebar"]
                assert resolved_color(sidebar_button, "fg_color") == expected_fill
                assert resolved_color(sidebar_button._canvas, "bg") == sidebar_palette["sidebar"]
            app._switch_page("writing")
            app.geometry("970x680+30+35")
            settle(260)
            app.article_worker = RunningWorker()
            settle(320)
            assert app._sidebar_activity_bars["writing"].winfo_ismapped()
            app.article_worker = None
            settle(320)
            assert not app._sidebar_activity_bars["writing"].winfo_ismapped()
            app.writing_completion_platforms = ["tistory"]
            app._show_writing_complete_dialog()
            settle(220)
            assert app.writing_complete_dialog.winfo_ismapped()
            assert app.grab_current() is app.writing_complete_dialog
            assert app.writing_complete_dialog.master is app.main_area
            assert app_module.modal_dimmer(app).active
            assert resolved_color(app.sidebar_frame, "fg_color") != app._theme_palette()["sidebar"]
            assert resolved_color(app.writing_complete_dialog, "fg_color") == app._theme_palette()["shell"]
            if sys.platform == "win32" or args.windows_dimmer:
                dimmer = app_module.modal_dimmer(app)
                assert dimmer._windows
                with patch.object(app.sidebar_frame, "configure", wraps=app.sidebar_frame.configure) as writes:
                    settle(1300)
                    for _ in range(3):
                        dimmer.refresh()
                    writes.assert_not_called()
                print(f"{sys.platform} {args.theme}: Windows completion scrim remains dim without periodic repaint", flush=True)
            assert all(
                getattr(app, button_name).cget("hover") is False
                for button_name in sidebar_button_names.values()
            )
            screenshot("writing-complete-dialog")
            screenshot_inactive("writing-complete-dialog-inactive")
            app._close_writing_complete_dialog_and_reset()
            settle(120)
            assert not app_module.modal_dimmer(app).active
            assert resolved_color(app.sidebar_frame, "fg_color") == app._theme_palette()["sidebar"]
            # Concurrent completion popups share one scrim and restore the
            # remaining modal grab when the top popup is dismissed.
            app._show_reference_collection_complete_dialog("검증 키워드", 2)
            app._show_naver_kin_complete_dialog("")
            settle(220)
            assert app_module.modal_dimmer(app).active
            app._close_naver_kin_complete_dialog()
            settle(120)
            assert app.grab_current() is app.reference_collection_dialog
            assert app_module.modal_dimmer(app).active
            app._close_reference_collection_dialog()
            settle(120)
            assert not app_module.modal_dimmer(app).active
            app._show_manual_publish_completion_dialog("tistory", "tistory_1")
            settle(120)
            assert app_module.modal_dimmer(app).active
            app._close_manual_publish_dialog()
            settle(120)
            assert not app_module.modal_dimmer(app).active
            app.geometry("1500x1000+30+35")
            settle(220)
            app._switch_page("thumbnails")
            settle(220)
            assert int(app.thumbnail_nav_button.grid_info()["row"]) < int(app.prompt_nav_button.grid_info()["row"])
            assert app.thumbnail_manager_selector_host.master is app.thumbnail_manager_card
            assert app.thumbnail_design_workspace.master is app.thumbnail_manager_editor_host
            app._switch_thumbnail_preset(4)
            app.thumbnail_auto_title_var.set(False)
            app.thumbnail_prompt_preview.delete("1.0", "end")
            app.thumbnail_prompt_preview.insert("1.0", "다섯 번째 세트 전용")
            app.cardnews_signature_entry.delete(0, "end")
            app.cardnews_signature_entry.insert(0, "5번 카드")
            app._save_thumbnail_manager_settings()
            assert app.thumbnail_presets[4]["text"] == "다섯 번째 세트 전용"
            assert app.thumbnail_presets[4]["cardnews_style"]["signature"] == "5번 카드"
            app._set_default_thumbnail_preset()
            assert app.home_thumbnail_menu.get() == "썸네일·카드5"
            assert app.writing_thumbnail_menu.get() == "썸네일·카드5"
            for geometry in ("1500x1000+30+35", "860x600+30+35"):
                app.geometry(geometry)
                app.thumbnails_scroll._parent_canvas.yview_moveto(0)
                settle(220)
                assert app.thumbnail_prompt_preview.winfo_ismapped()
                canvas = app.thumbnail_preview_canvas
                viewport = app.thumbnails_scroll._parent_canvas
                offset = max(0, canvas.winfo_rooty() - viewport.winfo_rooty() - 30)
                viewport.yview_moveto(offset / max(1, viewport.bbox("all")[3]))
                settle(120)
                visible = app.winfo_containing(canvas.winfo_rootx() + 10, canvas.winfo_rooty() + 10)
                assert visible is canvas, (geometry, visible, canvas)
                assert app.settings_nav_button.winfo_rooty() + app.settings_nav_button.winfo_height() < app.sidebar_version_label.winfo_rooty()
                for button in app.thumbnail_manager_preset_buttons.values():
                    assert button.winfo_rootx() + button.winfo_width() <= app.thumbnails_scroll._parent_canvas.winfo_rootx() + app.thumbnails_scroll._parent_canvas.winfo_width() + 2
            app._switch_page("writing")
            app._open_writing_section("publish")
            settle(220)
            assert app.thumbnail_prompt_preview.get("1.0", "end").strip() == "다섯 번째 세트 전용"
            assert app.writing_thumbnail_preview_canvas.winfo_ismapped()
            app._switch_thumbnail_preset(0)
            app.geometry("1500x1000+30+35")
            settle(220)
            for page_name, page_frame in app._page_frame_map().items():
                app._switch_page(page_name)
                settle(220)
                app._finish_theme_paint(force=True)
                settle(80)
                selected_nav = getattr(app, sidebar_button_names[page_name])
                assert resolved_color(selected_nav, "text_color") == app._theme_palette()["accent"]
                for candidate_page, button_name in sidebar_button_names.items():
                    sidebar_button = getattr(app, button_name)
                    expected_fill = (
                        app._theme_palette()["selected"]
                        if candidate_page == page_name
                        else app._theme_palette()["sidebar"]
                    )
                    assert resolved_color(sidebar_button, "bg_color") == app._theme_palette()["sidebar"]
                    assert resolved_color(sidebar_button, "fg_color") == expected_fill
                assert_white_button_contrast(page_frame)
            app._switch_page("automation")
            settle(220)
            assert tuple(app.automation_keyword_source_buttons) == (
                "daum", "signal", "newneek", "loword", "naver",
            )
            source_button_positions = [
                button.winfo_rootx()
                for button in app.automation_keyword_source_buttons.values()
            ]
            assert source_button_positions == sorted(source_button_positions)
            assert all(button.winfo_ismapped() for button in app.automation_keyword_source_buttons.values())
            screenshot("automation")
            app._switch_page("public_data")
            settle(220)
            assert_segmented_contrast(app.public_data_source_switch)
            public_selected = app.public_data_source_switch._buttons_dict["구석구석 축제"]
            public_unselected = app.public_data_source_switch._buttons_dict["공공 복지"]
            expected_public_colors = (
                {
                    "selected": "#2563eb",
                    "selected_hover": "#1d4ed8",
                    "unselected": "#e8eff9",
                    "unselected_hover": "#d4e2f5",
                }
                if args.theme == "light"
                else {
                    "selected": "#3468e8",
                    "selected_hover": "#2d5cd0",
                    "unselected": "#1d2635",
                    "unselected_hover": "#27364b",
                }
            )
            assert resolved_color(public_selected, "fg_color") == expected_public_colors["selected"]
            assert resolved_color(public_selected, "hover_color") == expected_public_colors["selected_hover"]
            assert resolved_color(public_unselected, "fg_color") == expected_public_colors["unselected"]
            assert resolved_color(public_unselected, "hover_color") == expected_public_colors["unselected_hover"]
            app.public_data_source_switch._buttons_dict["공공 복지"].invoke()
            assert app.public_data_source_switch.get() == "공공 복지"
            assert_segmented_contrast(app.public_data_source_switch)
            app.public_data_source_switch._buttons_dict["구석구석 축제"].invoke()
            screenshot("public-data")

            app._switch_page("prompts")
            app.geometry("1200x900+30+35")
            settle(200)
            assert app._prompt_tab_columns == 6
            screenshot("prompt-manager-wide")
            assert (
                app.prompt_tool_row.winfo_rooty()
                >= app.prompt_article_boxes["wordpress"].winfo_rooty()
                + app.prompt_article_boxes["wordpress"].winfo_height()
            )
            app.prompts_scroll._parent_canvas.yview_moveto(1.0)
            settle(140)
            screenshot("prompt-manager-bottom")
            app.prompts_scroll._parent_canvas.yview_moveto(0.0)
            app.geometry("860x680+30+35")
            settle(260)
            assert app._prompt_tab_columns == 3
            for button in (*app.prompt_tab_buttons.values(), *app.prompt_action_buttons.values()):
                assert button.winfo_ismapped()
                assert (
                    button.winfo_rootx() + button.winfo_width()
                    <= app.prompts_scroll.winfo_rootx() + app.prompts_scroll.winfo_width() + 2
                )
            name_entry = app.prompt_name_entries["wordpress"]
            title_box = app.prompt_title_boxes["wordpress"]
            article_box = app.prompt_article_boxes["wordpress"]
            name_entry.delete(0, "end")
            name_entry.insert(0, "축제전용")
            title_box.delete("1.0", "end")
            title_box.insert("1.0", "축제 제목 지침")
            article_box.delete("1.0", "end")
            article_box.insert("1.0", "축제 본문 지침\n둘째 줄")
            app.prompt_action_buttons["copy"].invoke()
            copied = app_module.parse_prompt_bundle(app.clipboard_get())
            assert copied["name"] == "축제전용"
            assert copied["title_prompt"] == "축제 제목 지침"
            assert copied["article_prompt"] == "축제 본문 지침\n둘째 줄"
            article_box.delete("1.0", "end")
            app.prompt_action_buttons["paste"].invoke()
            assert article_box.get("1.0", "end-1c") == "축제 본문 지침\n둘째 줄"
            export_path = Path(directory) / "축제전용.txt"
            with patch.object(
                app_module.filedialog,
                "asksaveasfilename",
                return_value=str(export_path),
            ):
                app.prompt_action_buttons["export"].invoke()
            assert export_path.read_text(encoding="utf-8") == app.clipboard_get()
            article_box.delete("1.0", "end")
            with patch.object(
                app_module.filedialog,
                "askopenfilename",
                return_value=str(export_path),
            ):
                app.prompt_action_buttons["import"].invoke()
            assert article_box.get("1.0", "end-1c") == "축제 본문 지침\n둘째 줄"
            plain_path = Path(directory) / "외부에서_수정한_본문.txt"
            plain_path.write_text("외부 TXT로 편집한 본문", encoding="utf-8")
            app._prompt_last_focused_box = article_box
            with (
                patch.object(app_module.filedialog, "askopenfilename", return_value=str(plain_path)),
                patch.object(app_module.messagebox, "askyesno", return_value=True),
            ):
                app.prompt_action_buttons["import"].invoke()
            assert article_box.get("1.0", "end-1c") == "외부 TXT로 편집한 본문"
            app._create_new_prompt_set()
            created_id = app.active_prompt_set_ids["wordpress"]
            assert created_id != "wordpress-default"
            app.prompt_name_entries["wordpress"].delete(0, "end")
            app.prompt_name_entries["wordpress"].insert(0, "검색 가능한 새 항목")
            app.prompt_title_boxes["wordpress"].delete("1.0", "end")
            app.prompt_title_boxes["wordpress"].insert("1.0", "독립된 제목 지침")
            app._save_prompt_settings()
            app._select_prompt_set_from_library("wordpress", "wordpress-default")
            app._select_prompt_set_from_library("wordpress", created_id)
            assert app.prompt_title_boxes["wordpress"].get("1.0", "end-1c") == "독립된 제목 지침"
            app.prompt_library_search_entry.insert(0, "검색 가능한")
            app._refresh_prompt_library()
            assert "1개 표시" in app.prompt_library_count_label.cget("text")
            with patch.object(app_module.messagebox, "askyesno", return_value=True):
                app._delete_prompt_set("wordpress")
            assert app._prompt_set_by_id(created_id) is None
            app._switch_prompt_platform("tistory_automation")
            settle(80)
            assert not app.prompt_library_panel.winfo_ismapped()
            app._switch_prompt_platform("naver_kin_automation")
            settle(80)
            assert not app.prompt_library_panel.winfo_ismapped()
            app._switch_prompt_platform("wordpress")
            settle(80)
            assert app.prompt_library_panel.winfo_ismapped()
            title_box = app.prompt_title_boxes["wordpress"]
            app._prompt_last_focused_box = title_box
            with (
                patch.object(app_module.filedialog, "askopenfilename", return_value=str(plain_path)),
                patch.object(app_module.messagebox, "askyesno", return_value=True),
            ):
                app.prompt_action_buttons["import"].invoke()
            assert title_box.get("1.0", "end-1c") == "외부 TXT로 편집한 본문"
            screenshot("prompt-bundle-tools")

            original_title = title_box.get("1.0", "end-1c")
            original_article = article_box.get("1.0", "end-1c")
            app.prompt_expand_buttons[("wordpress", "title")].invoke()
            settle(140)
            assert app.prompt_expanded_box.get("1.0", "end-1c") == original_title
            app.prompt_expanded_box.delete("1.0", "end")
            app.prompt_expanded_box.insert("1.0", "취소할 편집 내용")
            app._close_expanded_prompt_editor()
            assert title_box.get("1.0", "end-1c") == original_title
            app.prompt_expand_buttons[("wordpress", "article")].invoke()
            settle(140)
            assert app.prompt_expanded_box.get("1.0", "end-1c") == original_article
            expanded_article = "큰 편집창에서 수정한 본문\n\n" + "축제 일정과 방문 정보를 구분해서 설명하세요.\n" * 80
            app.prompt_expanded_box.delete("1.0", "end")
            app.prompt_expanded_box.insert("1.0", expanded_article)
            settle(100)
            assert article_box.get("1.0", "end-1c") == original_article
            assert app.prompt_expanded_count_label.cget("text") == f"{len(expanded_article):,}자"
            screenshot("prompt-expanded-editor", window=app.prompt_expanded_dialog)
            app.prompt_expanded_dialog.geometry("600x440")
            settle(160)
            assert app.prompt_expanded_box.winfo_height() >= 100
            for widget in app.prompt_expanded_dialog.winfo_children()[-1].winfo_children():
                if isinstance(widget, app_module.ctk.CTkButton):
                    assert widget.winfo_rootx() + widget.winfo_width() <= (
                        app.prompt_expanded_dialog.winfo_rootx() + app.prompt_expanded_dialog.winfo_width()
                    )
            screenshot("prompt-expanded-editor-small", window=app.prompt_expanded_dialog)
            app._apply_expanded_prompt_editor()
            assert app.prompt_expanded_dialog is None
            assert article_box.get("1.0", "end-1c") == expanded_article
            assert title_box.get("1.0", "end-1c") == original_title
            app.prompt_expand_buttons[("wordpress", "title")].invoke()
            settle(140)
            app.prompt_expanded_box.delete("1.0", "end")
            app.prompt_expanded_box.insert("1.0", "확대 편집창에서 저장한 제목")
            app._apply_expanded_prompt_editor(save=True)
            settle(550)
            saved_prompt = app._prompt_set_by_id(app.active_prompt_set_ids["wordpress"])
            assert saved_prompt["title_prompt"] == "확대 편집창에서 저장한 제목"
            assert saved_prompt["article_prompt"] == expanded_article.strip()
            assert "저장 완료" in app.prompt_feedback_label.cget("text")

            # The new topic shortcut uses the existing Playwright-first
            # reference worker, then writes without requiring a keyword choice.
            app._switch_page("writing")
            app.writing_auto_progress_var.set(False)
            while not app.result_queue.empty():
                app.result_queue.get_nowait()
            app.topic_entry.delete(0, "end")
            app.topic_entry.insert(0, "청년도약계좌")
            reference_worker = Mock()
            reference_worker.is_alive.return_value = False
            reference_worker.start.side_effect = lambda: app.result_queue.put((
                "reference_collect_done",
                {"keyword": "청년도약계좌", "reference_text": "확인한 기사 내용", "count": 1},
            ))
            with (
                patch.object(app_module, "ReferenceCollectionWorker", return_value=reference_worker) as worker_class,
                patch.object(app, "_start_article_generation") as article_generation,
            ):
                app.direct_keyword_start_button.invoke()
                assert app.direct_keyword_start_keyword == "청년도약계좌"
                assert app.direct_keyword_start_button.cget("state") == "disabled"
                assert app.current_keyword_source == "direct"
                assert app.selected_keyword_var.get() == "청년도약계좌"
                assert app.manual_keyword_entry.get() == "청년도약계좌"
                assert worker_class.call_args.kwargs["source_url"].startswith("https://www.google.com/search?q=")
                poll_result_queue(app)
                settle(350)
                article_generation.assert_called_once_with(
                    "청년도약계좌", "청년도약계좌", "확인한 기사 내용"
                )
                assert not app.writing_auto_run_active
                assert app.direct_keyword_start_button.cget("state") == "normal"

            app.writing_auto_progress_var.set(True)
            auto_worker = Mock()
            auto_worker.is_alive.return_value = False
            auto_worker.start.side_effect = lambda: app.result_queue.put((
                "reference_collect_done",
                {"keyword": "청년도약계좌", "reference_text": "자동진행 참고자료", "count": 1},
            ))
            with (
                patch.object(app_module, "ReferenceCollectionWorker", return_value=auto_worker),
                patch.object(app, "_start_article_generation") as article_generation,
            ):
                app.direct_keyword_start_button.invoke()
                poll_result_queue(app)
                settle(350)
                article_generation.assert_called_once_with(
                    "청년도약계좌", "청년도약계좌", "자동진행 참고자료"
                )
                assert app.writing_auto_run_active
                assert app.writing_auto_stage == "article"
            app._stop_writing_auto_progress()
            app.writing_auto_progress_var.set(False)

            failed_worker = Mock()
            failed_worker.is_alive.return_value = False
            failed_worker.start.side_effect = lambda: app.result_queue.put((
                "reference_collect_error", "브라우저 수집 실패"
            ))
            with (
                patch.object(app_module, "ReferenceCollectionWorker", return_value=failed_worker),
                patch.object(app_module.messagebox, "showerror") as show_error,
            ):
                app.direct_keyword_start_button.invoke()
                poll_result_queue(app)
                show_error.assert_called_once()
                assert app.direct_keyword_start_keyword == ""
                assert app.direct_keyword_start_button.cget("state") == "normal"
            assert not errors, errors
            print(f"{sys.platform} {args.theme}: icons, targets, fixed accordion, data/export preservation, slides, responsive layout passed")
        finally:
            faulthandler.cancel_dump_traceback_later()
            app.destroy()


if __name__ == "__main__":
    main()
