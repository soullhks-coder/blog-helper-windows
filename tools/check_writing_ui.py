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
from unittest.mock import patch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", choices=("dark", "light"))
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    if not args.theme:
        for theme in ("dark", "light"):
            command = [sys.executable, __file__, "--theme", theme]
            if args.screenshots:
                command.extend(["--screenshots", str(args.screenshots)])
            subprocess.run(command, check=True, timeout=150)
        return

    with tempfile.TemporaryDirectory(prefix="blog-helper-writing-ui-") as directory, ExitStack() as stack:
        faulthandler.dump_traceback_later(90, repeat=True)
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as app_module

        settings = app_module.WordPressSettings(
            app_theme="블랙테마" if args.theme == "dark" else "화이트테마",
            window_geometry="1500x1000",
            target_platforms=["wordpress", "tistory", "blogspot"],
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

        def screenshot(name):
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
            settle()
            destination = args.screenshots / f"writing-{args.theme}-{name}.png"
            if sys.platform == "darwin":
                area = f"{app.winfo_rootx()},{app.winfo_rooty()},{app.winfo_width()},{app.winfo_height()}"
                subprocess.run(["screencapture", "-x", "-R", area, str(destination)], check=True)
            else:
                from PIL import ImageGrab
                x, y = app.winfo_rootx(), app.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(destination)

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
                "naver_kin_nav_button": "naver_kin",
                "public_data_nav_button": "public_data",
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
                ).lstrip("#")
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
            assert app.home_adsense_card.winfo_ismapped()
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
                "naver_kin": "naver_kin_nav_button",
                "public_data": "public_data_nav_button",
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
            assert app._selected_writing_targets() == ["wordpress", "tistory", "blogspot"]
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
            app._clear_keyword_choices()
            settle()

            # Native switch/checkbox callbacks still use the original variables.
            app.writing_auto_progress_switch.toggle()
            assert app.writing_auto_progress_var.get()
            app.target_platform_vars["wordpress"].set(False)
            app._on_writing_target_changed()
            assert app._selected_writing_targets() == ["tistory", "blogspot"]
            app.target_platform_vars["wordpress"].set(True)
            app.writing_auto_progress_switch.toggle()

            # The selected prompt belongs to the publish target's history, not
            # to the prompt's own platform. Cross-platform choices must round-trip.
            app.target_platform_vars["wordpress"].set(True)
            app.target_platform_vars["tistory"].set(False)
            app.target_platform_vars["blogspot"].set(False)
            app._on_writing_target_changed("wordpress")
            app.writing_prompt_menu.set("티스토리 · 기본")
            app._on_writing_prompt_selected("티스토리 · 기본")
            assert app.wordpress_settings.writing_target_prompt_ids["wordpress"] == "tistory-default"

            app.target_platform_vars["wordpress"].set(False)
            app.target_platform_vars["tistory"].set(True)
            app._on_writing_target_changed("tistory")
            app.writing_prompt_menu.set("블로그스팟 · 기본")
            app._on_writing_prompt_selected("블로그스팟 · 기본")
            active_tistory = app_module.service_profile_by_name(
                app.wordpress_settings.tistory_profiles,
                app.wordpress_settings.tistory_active_profile,
            )
            assert active_tistory["last_prompt_id"] == "blogspot-default"

            app.target_platform_vars["wordpress"].set(True)
            app.target_platform_vars["tistory"].set(False)
            app._on_writing_target_changed("wordpress")
            assert app.writing_prompt_menu.get() == "티스토리 · 기본"
            app.target_platform_vars["wordpress"].set(False)
            app.target_platform_vars["tistory"].set(True)
            app._on_writing_target_changed("tistory")
            assert app.writing_prompt_menu.get() == "블로그스팟 · 기본"

            for variable in app.target_platform_vars.values():
                variable.set(True)
            app._on_writing_target_changed("blogspot")

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
            assert len(app.thumbnail_preset_buttons) == 3
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
            assert len(preset_settings.thumbnail_presets) == 3
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
            writing_canvas = app.writing_scroll._parent_canvas
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
                home_right = app.home_control_card.winfo_rootx() + app.home_control_card.winfo_width()
                for widget in (*app.home_target_groups.values(), app.home_prompt_frame):
                    assert widget.winfo_rootx() + widget.winfo_width() <= home_right + 2, (geometry, widget)
                app._switch_page("writing")
                settle()
                assert app.writing_step_rail.winfo_width() <= app.writing_page.winfo_width()
                selector_width = (
                    app.thumbnail_card_set_selector_host.winfo_width()
                    / app.thumbnail_card_set_selector_host._get_widget_scaling()
                )
                expected_default_row = 1 if selector_width < 760 else 0
                assert int(app.set_default_thumbnail_button.grid_info()["row"]) == expected_default_row
                app.writing_scroll._parent_canvas.yview_moveto(0)
                settle()
                canvas = app.writing_scroll._parent_canvas
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
                "naver_kin": "naver_kin_nav_button",
                "public_data": "public_data_nav_button",
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
            assert all(
                getattr(app, button_name).cget("hover") is False
                for button_name in sidebar_button_names.values()
            )
            screenshot("writing-complete-dialog")
            screenshot_inactive("writing-complete-dialog-inactive")
            app._close_writing_complete_dialog_and_reset()
            settle(120)
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
            app.geometry("860x680+30+35")
            settle(260)
            for button in app.prompt_action_buttons.values():
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
            screenshot("prompt-bundle-tools")
            assert not errors, errors
            print(f"{sys.platform} {args.theme}: icons, targets, fixed accordion, data/export preservation, slides, responsive layout passed")
        finally:
            faulthandler.cancel_dump_traceback_later()
            app.destroy()


if __name__ == "__main__":
    main()
