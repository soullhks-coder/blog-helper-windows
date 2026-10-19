"""Native contextual-guide checks with isolated demo settings and no uploads."""
import argparse
from contextlib import ExitStack
import copy
from dataclasses import asdict
import faulthandler
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", choices=("light", "dark"))
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    if not args.theme:
        for theme in ("light", "dark"):
            command = [sys.executable, __file__, "--theme", theme]
            if args.screenshots:
                command.extend(["--screenshots", str(args.screenshots)])
            subprocess.run(command, check=True, timeout=100)
        return
    with tempfile.TemporaryDirectory(prefix="blog-helper-guide-ui-") as directory, ExitStack() as stack:
        faulthandler.dump_traceback_later(75)
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as app_module
        from writing_help import WRITING_GUIDES, WritingHelpDialog
        first_maps = {}
        first_visible = {}
        activate = WritingHelpDialog._activate

        def relative_position(dialog):
            return (dialog.winfo_rootx() - dialog.master.winfo_rootx(),
                    dialog.winfo_rooty() - dialog.master.winfo_rooty())

        def activate_prepositioned(dialog):
            assert not dialog.winfo_ismapped()
            positions = first_maps.setdefault(dialog, [])
            app_module.tk.Misc.bind(dialog, "<Map>", lambda event: positions.append(relative_position(dialog))
                        if event.widget is dialog else None, add="+")
            activate(dialog)
            if dialog._shown:
                first_visible[dialog] = relative_position(dialog)
                assert dialog.winfo_toplevel() is app

        stack.enter_context(patch.object(WritingHelpDialog, "_activate", activate_prepositioned))

        settings = app_module.WordPressSettings(
            app_theme="화이트테마" if args.theme == "light" else "블랙테마",
            writing_auto_progress=False, window_geometry="1250x1000")
        stack.enter_context(patch.object(app_module.AppStateStore, "load", return_value=settings))
        for name in ("save", "update_fields"):
            stack.enter_context(patch.object(app_module.AppStateStore, name))
        for name in ("_start_remote_agent_if_enabled", "_start_update_check", "_poll_queue",
                     "_automation_publish_scheduler_tick", "_load_version_catalog", "_save_ui_state",
                     "_save_ui_state_now", "_on_window_configure"):
            stack.enter_context(patch.object(app_module.KeywordApp, name, return_value=None))
        print(f"{sys.platform} {args.theme}: constructing guide test", flush=True)
        app = app_module.KeywordApp()
        errors = []
        app.report_callback_exception = lambda *error: errors.append(error)

        def settle():
            app.after(220, app.quit)
            app.mainloop()
            app.update_idletasks()
            assert not errors, errors

        def snapshot():
            return (copy.deepcopy(asdict(app.wordpress_settings)), app.topic_entry.get(),
                    app.article_editor.get("1.0", "end"), app.active_writing_section,
                    app.writing_auto_progress_var.get(), app.benchmark_mode_var.get())

        def surfaces(root=None):
            result = {}
            pending = [root or app]
            while pending:
                widget = pending.pop()
                if isinstance(widget, app_module.ctk.CTkToplevel):
                    continue
                pending.extend(widget.winfo_children())
                for option in ("fg_color", "bg_color", "border_color", "text_color", "background", "foreground"):
                    try:
                        value = widget.cget(option)
                        result[(widget, option)] = tuple(value) if isinstance(value, list) else value
                    except (app_module.tk.TclError, ValueError):
                        pass
            return result

        try:
            app._switch_page("writing")
            app._open_writing_section("topic")
            app.topic_entry.insert(0, "작성 중인 주제는 유지")
            app.article_editor.insert("1.0", "<p>작성 중인 본문</p>")
            app.lift()
            app.focus_force()
            app.update()
            app.geometry("1250x1000+30+35")
            settle()
            assert set(app.writing_help_buttons) == set(WRITING_GUIDES)
            for key, label in (("topic", app.writing_section_content_title_labels["topic"]),
                               ("benchmark", app.benchmark_checkbox), ("links", app.link_title_label)):
                button = app.writing_help_buttons[key]
                assert button.winfo_ismapped()
                assert button.master is label.master
                gap = button.winfo_rootx() - label.winfo_rootx() - label.winfo_width()
                assert 0 <= gap <= 3, (key, gap)
                assert button.cget("image") is not None
                assert button.cget("width") == 20 and button.cget("height") == 20
                scale = button._get_widget_scaling()
                assert button.winfo_width() <= round(21 * scale) + 1, (key, button.winfo_width())
                assert button._image_label.winfo_rootx() - button.winfo_rootx() <= round(2 * scale) + 1
                assert button.cget("text_color") in ("#9ca3af", "#aab2bf")
                assert int(button._canvas.cget("takefocus")) == 1
            original = snapshot()
            original_icons = {key: button.cget("image") for key, button in app.writing_help_buttons.items()}
            original_surfaces = surfaces()
            first_window = None
            for key in WRITING_GUIDES:
                app.writing_help_buttons[key].invoke()
                settle()
                dialog = app.writing_help_dialog
                if first_window is None:
                    first_window = dialog
                assert dialog is first_window
                assert dialog.topic == key and dialog.heading.cget("text") == WRITING_GUIDES[key]["title"]
                assert dialog._source_image is not None and dialog._photo is not None, key
                assert dialog.grab_current() is dialog
                assert first_maps[dialog]
                current_position = relative_position(dialog)
                assert all(abs(x - current_position[0]) <= 2 and abs(y - current_position[1]) <= 2
                           for x, y in first_maps[dialog]), (first_maps[dialog], current_position)
                assert first_visible[dialog] == current_position, (first_visible[dialog], current_position)
                assert app_module.modal_dimmer(app).active
                assert snapshot() == original, key
                assert len(dialog._text_labels) == 10
                dialog.scroll._parent_canvas.yview_moveto(1)
                settle()
                assert dialog.scroll._parent_canvas.yview()[1] >= 0.99
                if args.screenshots and key in ("topic", "benchmark", "links"):
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    dialog.scroll._parent_canvas.yview_moveto(0)
                    dialog.lift()
                    settle()
                    destination = args.screenshots / f"guide-{args.theme}-{key}.png"
                    if sys.platform == "darwin":
                        area = f"{dialog.winfo_rootx()},{dialog.winfo_rooty()},{dialog.winfo_width()},{dialog.winfo_height()}"
                        subprocess.run(["screencapture", "-x", "-R", area, str(destination)], check=True)
                    else:
                        from PIL import ImageGrab
                        x, y = dialog.winfo_rootx(), dialog.winfo_rooty()
                        ImageGrab.grab(bbox=(x, y, x + dialog.winfo_width(), y + dialog.winfo_height())).save(destination)
            dialog.configure(width=440, height=560)
            settle()
            assert dialog._image_size[0] <= dialog.scroll.winfo_width() / dialog.scroll._get_widget_scaling()
            assert dialog._image_size[1] <= 280
            # A synthetic key is only delivered when this native window owns
            # keyboard focus (in particular after macOS screenshots/resizes).
            dialog.focus_force()
            settle()
            dialog.event_generate("<Escape>")
            settle()
            assert not dialog.winfo_exists()
            assert not app_module.modal_dimmer(app).active
            assert snapshot() == original
            assert all(button.cget("image") is original_icons[key] for key, button in app.writing_help_buttons.items())
            assert surfaces() == original_surfaces
            # A missing resource should show a useful text fallback, not crash.
            fallback = WritingHelpDialog(app, "links", resource_dir=Path(directory), palette=app._theme_palette())
            settle()
            assert fallback._source_image is None
            assert "아래 설명" in fallback.image_label.cget("text")
            fallback.close()
            settle()
            assert not app_module.modal_dimmer(app).active
            # Reopening and immediate close must not leave pending guide timers.
            app.writing_help_buttons["topic"].invoke()
            app.writing_help_dialog.close()
            assert not app_module.modal_dimmer(app).active
            settle()
            assert not app_module.modal_dimmer(app).active
            assert snapshot() == original
            # Navigation must finish the guide's dim release before touching
            # any new page, including a Destroy whose idle callback is pending.
            for destination in ("settings", "thumbnails", "prompts"):
                app._switch_page(destination)
                settle()
                # Compare equivalent, already-initialized navigation paths:
                # returning to writing refreshes thumbnail preset colors even
                # without a guide, and first-visit theme painting is lazy.
                app._switch_page("writing")
                app._switch_page(destination)
                settle()
                expected_page = surfaces(app._current_page_frame())
                app._switch_page("writing")
                app._open_writing_help("links")
                settle()
                old_dialog = app.writing_help_dialog
                if destination == "prompts":
                    old_dialog.destroy()
                app._switch_page(destination)
                assert not old_dialog.winfo_exists()
                dimmer = app_module.modal_dimmer(app)
                assert not dimmer.active and not dimmer._windows_saved and not dimmer._pending_closes
                settle()
                root_fill = app.cget("fg_color")
                if isinstance(root_fill, list):
                    root_fill = tuple(root_fill)
                assert root_fill == original_surfaces[(app, "fg_color")], (root_fill, original_surfaces[(app, "fg_color")])
                assert app.sidebar_frame.cget("fg_color") == app._theme_palette()["sidebar"]
                actual_page = surfaces(app._current_page_frame())
                assert actual_page == expected_page, (destination, [
                    (str(widget), option, expected, actual_page.get((widget, option)))
                    for (widget, option), expected in expected_page.items()
                    if actual_page.get((widget, option)) != expected][:20])
            app._switch_page("writing")
            app._open_writing_help("topic")
            settle()
            app.writing_help_dialog.place_forget()
            settle()
            assert not app.writing_help_dialog.winfo_exists()
            assert not app_module.modal_dimmer(app).active
            app._open_writing_help("topic")
            settle()
            hidden_dialog = app.writing_help_dialog
            hidden_dialog.place_forget()
            app._open_writing_help("links")
            settle()
            assert not hidden_dialog.winfo_exists()
            assert app.writing_help_dialog.topic == "links"
            assert app_module.modal_dimmer(app)._depth == 1
            app.writing_help_dialog.close()
            # Closing immediately before opening another guide must not let
            # the old idle callback release the new guide's dim layer.
            app._open_writing_help("topic")
            settle()
            app.writing_help_dialog.close()
            assert not app_module.modal_dimmer(app).active
            app._open_writing_help("links")
            settle()
            assert app_module.modal_dimmer(app)._depth == 1
            app.writing_help_dialog.close()
            assert not app_module.modal_dimmer(app).active
            settle()
            app._open_writing_help("topic")
            settle()
            app.geometry("860x680+30+35")
            settle()
            dialog = app.writing_help_dialog
            assert dialog.winfo_width() <= dialog.master.winfo_width()
            assert dialog.winfo_height() <= dialog.master.winfo_height()
            assert dialog.winfo_rootx() >= dialog.master.winfo_rootx()
            assert dialog.winfo_rooty() >= dialog.master.winfo_rooty()
            # Escape also works after a click focuses a native child label,
            # not only when the popup frame itself owns keyboard focus.
            dialog.heading._label.focus_force()
            settle()
            dialog.heading._label.event_generate("<Escape>")
            settle()
            assert not dialog.winfo_exists() and not app_module.modal_dimmer(app).active
            print(f"{sys.platform} {args.theme}: 11 illustrated guides, compact icons, stable first presentation, read-only state, scrolling, reuse, Escape, fallback, immediate/navigation/hidden-window dim restoration passed", flush=True)
        finally:
            app.destroy()
            faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
