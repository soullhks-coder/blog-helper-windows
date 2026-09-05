"""Offline Tk smoke checks for the writing UI, run on macOS and Windows CI."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
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
            subprocess.run(command, check=True)
        return

    with tempfile.TemporaryDirectory(prefix="blog-helper-writing-ui-") as directory, ExitStack() as stack:
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as app_module

        settings = app_module.WordPressSettings(
            app_theme="블랙테마" if args.theme == "dark" else "화이트테마",
            window_geometry="1500x1000",
            target_platforms=["wordpress", "tistory"],
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
        app = app_module.KeywordApp()
        errors = []
        app.report_callback_exception = lambda *error: errors.append(error)

        def settle():
            app.after(160, app.quit)
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

        try:
            app.geometry("1500x1000+30+35")
            app._switch_page("writing")
            settle()
            assert app._selected_writing_targets() == ["wordpress", "tistory"]
            assert not hasattr(app, "writing_auto_progress_status")
            for step in range(1, 5):
                assert app._bootstrap_sidebar_icon_image(f"{step}-circle", "#2563eb") is not None
                assert app._bootstrap_sidebar_icon_image(f"{step}-circle-fill", "#2563eb") is not None

            # Native switch/checkbox callbacks still use the original variables.
            app.writing_auto_progress_switch.toggle()
            assert app.writing_auto_progress_var.get()
            app.target_platform_vars["wordpress"].set(False)
            app._on_writing_target_changed()
            assert app._selected_writing_targets() == ["tistory"]
            app.target_platform_vars["wordpress"].set(True)
            app.writing_auto_progress_switch.toggle()

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

            palette = app._theme_palette()
            app._finish_theme_paint(force=True)
            settle()
            for widget in (app.thumbnail_prompt_preview, app.cardnews_heading_entry):
                assert widget.cget("fg_color") == palette["input"]
                assert widget.cget("text_color") == palette["text"]
            for widget in (app.save_thumbnail_button, app.generate_cardnews_button):
                assert widget.cget("text_color") == "#ffffff"
                assert widget.cget("fg_color") == "#2563eb"

            # Both compact laptop and wider desktop layouts retain all controls.
            for geometry in ("1500x1000+30+35", "1100x900+30+35", "860x680+30+35"):
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
            assert not errors, errors
            print(f"{sys.platform} {args.theme}: icons, targets, fixed accordion, data/export preservation, slides, responsive layout passed")
        finally:
            app.destroy()


if __name__ == "__main__":
    main()
