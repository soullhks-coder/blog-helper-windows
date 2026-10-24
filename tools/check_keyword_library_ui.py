"""Offline keyword-library integration/visual checks; no real account or posts."""
from contextlib import ExitStack
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import Mock, patch


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", choices=("dark", "light"))
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--guide", action="store_true")
    args = parser.parse_args()
    if not args.theme:
        for theme in ("dark", "light"):
            command = [sys.executable, __file__, "--theme", theme]
            if args.screenshots:
                command += ["--screenshots", str(args.screenshots)]
            if args.guide and theme == "light":
                command += ["--guide"]
            subprocess.run(command, check=True, timeout=120)
        return
    with tempfile.TemporaryDirectory(prefix="blog-helper-keywords-ui-") as directory, ExitStack() as stack:
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root))
        import main as module
        settings = module.WordPressSettings(app_theme="화이트테마" if args.theme == "light" else "블랙테마",
                                            window_geometry="1450x1000", writing_auto_progress=True)
        stack.enter_context(patch.object(module.AppStateStore, "load", return_value=settings))
        for method in ("save", "update_fields"):
            stack.enter_context(patch.object(module.AppStateStore, method))
        for method in ("_start_remote_agent_if_enabled", "_start_update_check", "_poll_queue",
                       "_automation_publish_scheduler_tick", "_load_version_catalog", "_save_ui_state",
                       "_save_ui_state_now", "_on_window_configure"):
            stack.enter_context(patch.object(module.KeywordApp, method))
        app = module.KeywordApp()
        app.geometry("1450x1000+30+50")
        errors = []
        app.report_callback_exception = lambda *error: errors.append(error)
        def settle(delay=120):
            app.after(delay, app.quit)
            app.mainloop()
            app.update_idletasks()
            assert not errors, errors
        def screenshot(name):
            if not args.screenshots:
                return
            app.attributes("-topmost", True)
            app.lift()
            app.focus_force()
            # Let the native macOS mapping/Space transition finish too; Tk's
            # update_idletasks alone can capture a stale/animated backing store.
            settle(1000)
            args.screenshots.mkdir(parents=True, exist_ok=True)
            path = args.screenshots / f"keywords-{args.theme}-{name}.png"
            if sys.platform == "darwin":
                subprocess.run(["screencapture", "-x", "-R",
                    f"{app.winfo_rootx()},{app.winfo_rooty()},{app.winfo_width()},{app.winfo_height()}", str(path)], check=True)
            else:
                from PIL import ImageGrab
                x, y = app.winfo_rootx(), app.winfo_rooty()
                ImageGrab.grab((x, y, x + app.winfo_width(), y + app.winfo_height())).save(path)
        try:
            assert app.keyword_library_panel is None
            assert not module.KEYWORD_LIBRARY_FILE.exists()
            app._switch_page("keywords")
            settle()
            panel = app.keyword_library_panel
            assert panel is not None
            assert int(app.keywords_nav_button.grid_info()["row"]) == 3
            panel.input.insert("1.0", "\n".join(f"여행 키워드 {i:03d}" for i in range(500)))
            panel.add_input()
            settle()
            assert panel.store.counts()["pending"] == 500
            assert len(panel.rows) == 40
            assert len(panel.list_frame.winfo_children()) == 40
            screenshot("pending")
            if args.guide:
                from PIL import Image
                with Image.open(args.screenshots / f"keywords-{args.theme}-pending.png") as capture:
                    capture.save(root / "assets" / "writing-guide" / "keywords.library.png")
            panel.next.invoke()
            assert panel.rows[0]["keyword"] == "여행 키워드 040"
            panel.previous.invoke()
            panel.input.insert("1.0", "아직 등록하지 않은 임시 키워드")
            row = panel.rows[0]
            panel.selection[row["id"]].set(True)
            app.benchmark_mode_var.set(True)
            app._on_benchmark_mode_changed(save=False)
            # Loading into either writing mode must never start any worker,
            # even if auto-progress was enabled in the existing settings.
            with patch.object(app, "start_direct_keyword_writing") as start, \
                 patch.object(app, "_collect_reference_for_selected_keyword") as collect, \
                 patch.object(app, "_run_publish_pipeline") as publish:
                panel.write_button.invoke()
                settle()
                assert app.current_page == "writing"
                assert app.topic_entry.get() == row["keyword"]
                assert not app.benchmark_mode_var.get()
                assert not app.writing_auto_run_active
                start.assert_not_called()
                collect.assert_not_called()
                publish.assert_not_called()
            app._switch_page("keywords")
            panel = app.keyword_library_panel
            assert panel.input.get("1.0", "end-1c") == "아직 등록하지 않은 임시 키워드"
            panel.destination.set("티스토리 V2")
            panel.selection[panel.rows[0]["id"]].set(True)
            panel.write_button.invoke()
            settle()
            assert app.current_page == "tistory_v2"
            assert app.topic_entry.get() == row["keyword"]
            assert not app.writing_auto_run_active
            module.record_managed_keyword_publication(row["keyword"], "tistory", "https://example.com/test", "테스트 글")
            app._switch_page("keywords")
            panel = app.keyword_library_panel
            assert panel.store.counts()["pending"] == 499
            panel.history_tab.invoke()
            settle()
            assert len(panel.rows) == 1
            assert panel.summary.winfo_ismapped()
            assert panel.list_frame.winfo_ismapped()
            assert panel.rows[0]["url"] == "https://example.com/test"
            screenshot("history")
            panel.selection[panel.rows[0]["id"]].set(True)
            panel.restore_button.invoke()
            assert panel.store.counts()["pending"] == 500
            assert panel.store.counts()["history"] == 1
            panel.pending_tab.invoke()
            panel.search.insert(0, "키워드 499")
            panel.apply_search()
            assert len(panel.rows) == 1
            panel.search.delete(0, "end")
            panel.apply_search()
            panel.selection[panel.rows[0]["id"]].set(True)
            with patch.object(module.messagebox, "askyesno", return_value=True):
                panel.used_button.invoke()
            assert panel.store.counts()["pending"] == 499
            panel.history_tab.invoke()
            assert panel.rows[0]["platform"] == "manual"
            # Export is full-tab, not only the visible 40 rows or search result.
            export = Path(directory) / "export.txt"
            panel.pending_tab.invoke()
            with patch.object(module.filedialog, "asksaveasfilename", return_value=str(export)):
                panel.export_file()
            assert len(export.read_text(encoding="utf-8").splitlines()) == 499
            # Narrow window: footer, navigation and all action controls stay visible.
            app.geometry("1050x850")
            settle()
            for widget in (panel.destination, panel.write_button, panel.next):
                assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + app.winfo_width(), str(widget)
                assert widget.winfo_rooty() + widget.winfo_height() <= app.winfo_rooty() + app.winfo_height(), str(widget)
            assert app.settings_nav_button.winfo_rooty() + app.settings_nav_button.winfo_height() < app.sidebar_version_label.winfo_rooty()
            screenshot("narrow")
            # Help uses the common dimmer and cleans up when navigating away.
            app._open_writing_help("keywords.library")
            settle()
            assert module.modal_dimmer(app).active
            app._switch_page("writing")
            settle()
            assert not module.modal_dimmer(app).active
            print(f"{sys.platform} {args.theme}: 500 keywords, pagination, both writing modes, history/restore, manual use, export, narrow UI and help passed", flush=True)
        finally:
            app.destroy()


if __name__ == "__main__":
    main()
