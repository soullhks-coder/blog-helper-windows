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
        faulthandler.dump_traceback_later(45, repeat=True)
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
            assert int(app.home_refresh_button.cget("width")) == 215
            assert int(app.home_launch_status_label.grid_info()["row"]) == 1
            assert int(app.home_launch_status_label.grid_info()["columnspan"]) == 2
            assert resolved_color(app.home_adsense_title_label, "text_color") == "#ffffff"
            assert tuple(app.home_adsense_value_labels) == (
                "today", "yesterday", "last_7_days", "month_to_date", "balance",
            )
            for value_label in app.home_adsense_value_labels.values():
                assert resolved_color(value_label, "text_color") == "#ffffff"

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

            app._switch_page("writing")
            # Initialize the native window before scheduling the quit timer.
            # CTk's first Windows mainloop can pump events during setup and
            # consume that timer before Tk's actual event loop has started.
            app.update()
            settle()
            assert app._selected_writing_targets() == ["wordpress", "tistory", "blogspot"]
            assert not hasattr(app, "writing_auto_progress_status")
            for step in range(1, 5):
                assert app._bootstrap_sidebar_icon_image(f"{step}-circle", "#2563eb") is not None
                assert app._bootstrap_sidebar_icon_image(f"{step}-circle-fill", "#2563eb") is not None

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
                assert app.writing_step_rail.winfo_width() <= app.writing_page.winfo_width()
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
            for page_name, page_frame in app._page_frame_map().items():
                app._switch_page(page_name)
                settle(220)
                app._finish_theme_paint(force=True)
                settle(80)
                selected_nav = getattr(app, sidebar_button_names[page_name])
                assert resolved_color(selected_nav, "text_color") == app._theme_palette()["accent"]
                assert_white_button_contrast(page_frame)
            app._switch_page("automation")
            settle(220)
            assert tuple(app.automation_keyword_source_buttons) == (
                "daum", "signal", "newneek", "google", "naver",
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
            assert not errors, errors
            print(f"{sys.platform} {args.theme}: icons, targets, fixed accordion, data/export preservation, slides, responsive layout passed")
        finally:
            faulthandler.cancel_dump_traceback_later()
            app.destroy()


if __name__ == "__main__":
    main()
