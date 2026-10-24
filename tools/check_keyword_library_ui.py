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
            # Regression: the user's two entries were saved but hidden behind
            # a previous history tab and search. Registration reveals them.
            panel.history_tab.invoke()
            panel.search.insert(0, "일치하지 않는 검색어")
            panel.search_changed()
            panel.input.insert("1.0", "여행 키워드 000\n여행 키워드 001")
            panel.add_input()
            settle()
            assert panel.store.counts()["pending"] == 2
            assert [row["keyword"] for row in panel.rows] == ["여행 키워드 000", "여행 키워드 001"]
            assert not panel.history and not panel.search.get() and panel.offset == 0
            assert panel._search_job is None
            assert panel.input.get("1.0", "end-1c") == ""
            assert panel.pending_tab.cget("text") == "대기 키워드 2"
            assert panel.pending_tab.cget("fg_color") == panel.palette["accent"]
            screenshot("two-added")
            panel.input.insert("1.0", "\n".join(f"여행 키워드 {i:03d}" for i in range(2, 500)))
            panel.add_input()
            settle()
            assert panel.store.counts()["pending"] == 500
            assert len(panel.rows) == 40
            assert len(panel.list_frame.winfo_children()) == 40
            screenshot("pending")
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
            # Import from an empty/filtered history view must also reveal the
            # new rows even when the inbox spans many pages.
            panel.history_tab.invoke()
            panel.search.insert(0, "없는 키워드")
            panel.apply_search()
            imported = Path(directory) / "new.txt"
            imported.write_text("새 등록 하나\n새 등록 둘", encoding="utf-8")
            with patch.object(module.filedialog, "askopenfilename", return_value=str(imported)):
                panel.import_file()
            settle()
            assert not panel.history and not panel.search.get()
            assert panel.offset == 480
            assert {"새 등록 하나", "새 등록 둘"}.issubset({row["keyword"] for row in panel.rows})
            assert panel.list_frame._parent_canvas.yview()[0] == 0
            panel.search.insert(0, "없는 키워드")
            panel.apply_search()
            assert not panel.rows and panel.offset == 0
            panel.clear_search_button.invoke()
            assert panel.rows and not panel.search.get() and panel.offset == 0
            screenshot("registration-reset")
            # Folder creation/cancel, targeted paste/import, bulk reassignment,
            # grouped publication history and safe deletion of just a folder.
            with patch.object(module.simpledialog, "askstring", return_value=None):
                panel.create_group_button.invoke()
            assert panel.store.groups() == []
            with patch.object(module.simpledialog, "askstring", return_value="건강"):
                panel.create_group_button.invoke()
            health = panel.group_id
            assert health and panel.rows == [] and not panel.history
            assert panel.move_group_id == health
            with patch.object(module.simpledialog, "askstring", return_value="건강"), \
                 patch.object(module.messagebox, "showwarning") as warning:
                panel.create_group_button.invoke()
            warning.assert_called_once()
            assert len(panel.store.groups()) == 1 and panel.group_id == health
            panel.input.delete("1.0", "end")
            panel.input.insert("1.0", "건강 샘플 하나\n건강 샘플 둘")
            panel.add_input()
            assert len(panel.rows) == 2 and all(row["group_id"] == health for row in panel.rows)
            panel.select_all.select()
            panel.select_page()
            panel.change_move_group("미분류")
            panel.move_group_button.invoke()
            assert not panel.rows
            panel.change_group("미분류")
            panel.search.insert(0, "건강 샘플")
            panel.apply_search()
            assert len(panel.rows) == 2
            panel.select_all.select()
            panel.select_page()
            panel.change_move_group("건강")
            panel.move_group_button.invoke()
            assert not panel.rows
            with patch.object(module.simpledialog, "askstring", return_value="여행"):
                panel.create_group_button.invoke()
            travel = panel.group_id
            panel.change_group("전체 그룹")
            panel.search.insert(0, "여행 키워드 001")
            panel.apply_search()
            panel.selection[panel.rows[0]["id"]].set(True)
            panel.change_move_group("건강")
            panel.move_group_button.invoke()
            assert panel.rows[0]["group_id"] == health
            panel.change_group("건강")
            assert len(panel.rows) == 3 and not panel.search.get()
            imported.write_text("건강 파일 키워드", encoding="utf-8")
            with patch.object(module.filedialog, "askopenfilename", return_value=str(imported)):
                panel.import_file()
            assert len(panel.rows) == 4
            module.record_managed_keyword_publication("건강 샘플 하나", "tistory", "https://example.com/group", "그룹 테스트")
            panel.history_tab.invoke()
            assert len(panel.rows) == 1 and panel.rows[0]["group_name"] == "건강"
            panel.selection[panel.rows[0]["id"]].set(True)
            panel.change_move_group("여행")
            panel.move_group_button.invoke()
            assert not panel.rows
            panel.change_group("여행")
            assert panel.history and len(panel.rows) == 1
            with patch.object(module.simpledialog, "askstring", return_value="여행 · 축제"):
                panel.rename_group_button.invoke()
            assert panel.group_menu.get() == "여행 · 축제"
            assert panel.rows[0]["group_name"] == "여행 · 축제"
            app._switch_page("writing")
            app._switch_page("keywords")
            panel = app.keyword_library_panel
            settle()
            assert panel.group_id == travel and panel.history
            assert panel.move_group_menu.get() == "여행 · 축제"
            group_export = Path(directory) / "group.csv"
            with patch.object(module.filedialog, "asksaveasfilename", return_value=str(group_export)):
                panel.export_file()
            import csv
            with group_export.open(encoding="utf-8-sig") as stream:
                exported = list(csv.DictReader(stream))
            assert len(exported) == 1 and exported[0]["group_name"] == "여행 · 축제"
            screenshot("group-history")
            before_delete = panel.store.counts()
            with patch.object(module.messagebox, "askyesno", return_value=False):
                panel.delete_group_button.invoke()
            assert panel.group_id == travel
            with patch.object(module.messagebox, "askyesno", return_value=True):
                panel.delete_group_button.invoke()
            assert panel.group_id == 0 and panel.store.counts() == before_delete
            assert any(row["url"] == "https://example.com/group" for row in panel.rows)
            panel.change_group("건강")
            panel.pending_tab.invoke()
            assert len(panel.rows) == 3
            settle()
            screenshot("groups")
            if args.guide:
                from PIL import Image
                with Image.open(args.screenshots / f"keywords-{args.theme}-groups.png") as capture:
                    capture.save(root / "assets" / "writing-guide" / "keywords.library.png")
            # Narrow window: footer, navigation and all action controls stay visible.
            app.geometry("1050x850")
            settle()
            # Long folder names must not widen dropdowns or cover the controls.
            with patch.object(module.simpledialog, "askstring", return_value="그룹 이름이 아주 길어도 화면 밖으로 나가지 않는지 확인합니다 한글입니다"):
                panel.rename_group_button.invoke()
            settle()
            for widget in (panel.destination, panel.write_button, panel.next, panel.group_menu,
                           panel.create_group_button, panel.delete_group_button, panel.move_group_menu, panel.move_group_button):
                assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + app.winfo_width(), str(widget)
                assert widget.winfo_rootx() >= app.winfo_rootx(), str(widget)
                assert widget.winfo_rooty() + widget.winfo_height() <= app.winfo_rooty() + app.winfo_height(), str(widget)
            assert app.settings_nav_button.winfo_rooty() + app.settings_nav_button.winfo_height() < app.sidebar_version_label.winfo_rooty()
            assert panel.group_menu.winfo_rootx() + panel.group_menu.winfo_width() <= panel.create_group_button.winfo_rootx()
            with patch.object(module.simpledialog, "askstring", return_value="건강"):
                panel.rename_group_button.invoke()
            settle()
            screenshot("narrow")
            # Help uses the common dimmer and cleans up when navigating away.
            app._open_writing_help("keywords.library")
            settle()
            assert module.modal_dimmer(app).active
            app._switch_page("writing")
            settle()
            assert not module.modal_dimmer(app).active
            print(f"{sys.platform} {args.theme}: groups/create/rename/move/delete, grouped paste/import/history/export, two-entry registration, 500 keywords, pagination, both writing modes, narrow UI and help passed", flush=True)
        finally:
            app.destroy()


if __name__ == "__main__":
    main()
