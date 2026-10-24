"""Offline all-menu guide QA and optional safe demo screenshot refresh."""
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

from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", choices=("light", "dark"))
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--only", nargs="+")
    args = parser.parse_args()
    if not args.theme:
        for theme in (("light",) if args.capture else ("light", "dark")):
            subprocess.run([sys.executable, __file__, "--theme", theme,
                            *(["--capture"] if args.capture else []),
                            *(["--inventory"] if args.inventory else []),
                            *(["--only", *args.only] if args.only else [])], check=True, timeout=120)
        return
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="blog-helper-menu-guides-") as directory, ExitStack() as stack:
        faulthandler.dump_traceback_later(100)
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(root))
        import main as module
        from menu_help import MENU_GUIDES, MENU_SECTIONS
        from writing_help import ALL_GUIDES
        settings = module.WordPressSettings(
            app_theme="화이트테마" if args.theme == "light" else "블랙테마",
            writing_auto_progress=False, window_geometry="1250x1000")
        stack.enter_context(patch.object(module.AppStateStore, "load", return_value=settings))
        for name in ("save", "update_fields"):
            stack.enter_context(patch.object(module.AppStateStore, name))
        for name in ("_start_remote_agent_if_enabled", "_start_update_check", "_poll_queue",
                     "_automation_publish_scheduler_tick", "_load_version_catalog", "_save_ui_state",
                     "_save_ui_state_now", "_on_window_configure", "_load_home_dashboard_keywords",
                     "_start_adsense_refresh", "_start_naver_kin_clipboard_monitor"):
            stack.enter_context(patch.object(module.KeywordApp, name, return_value=None))
        app = module.KeywordApp()
        errors = []
        app.report_callback_exception = lambda *error: errors.append(error)

        def settle(delay=80):
            app.after(delay, app.quit)
            app.mainloop()
            app.update_idletasks()
            assert not errors, errors

        scale = app._get_window_scaling()
        width = min(1250, int((app.winfo_screenwidth() - 60) / scale))
        height = min(1000, int((app.winfo_screenheight() - 100) / scale))

        def locate(topic):
            page = next((page for page, key, _headings in MENU_SECTIONS if key == topic), "tistory_v2")
            if app.current_page != page:
                app._switch_page(page)
            visible_root = app._page_frame_map()[page]
            if page == "settings":
                key = topic.split(".")[1]
                section = ("basic" if key in ("basic", "remote", "version") else
                           "theme" if key in ("theme", "font", "menu") else
                           "history" if key == "history" else "ai")
                if app.settings_section != section:
                    app._switch_settings_section(section)
                if section == "ai":
                    app._switch_settings_tab("wordpress" if key == "limit" else key)
                    visible_root = getattr(app, "wp_card" if key in ("wordpress", "limit") else key + "_card")
                else:
                    visible_root = getattr(app, section + "_scroll")
            elif page == "naver_blog":
                app._switch_naver_blog_tab("settings" if topic in ("naver.profiles", "naver.settings") else
                                           "rules" if topic == "naver.rules" else "writing")
            elif page == "naver_kin":
                app._switch_naver_kin_tab("writing")
            elif page == "public_data":
                app._show_public_data_source("공공 복지" if topic == "public.welfare" else "구석구석 축제")
                if topic == "public.api":
                    # Legacy API panel is built but deliberately hidden in the
                    # public UI. Capture only in this isolated fixture.
                    app.festival_panel.grid_remove()
                    for widget in app.public_data_api_widgets:
                        widget.grid()
            elif page == "prompts":
                platform = ("tistory_automation" if topic == "prompt.automation" else
                            "naver_kin_automation" if topic == "prompt.kin" else "wordpress")
                app._switch_prompt_platform(platform)
                if topic not in ("prompt.library", "prompt.files"):
                    visible_root = app.prompt_platform_frames[platform]
            elif page == "tistory_v2":
                app._open_writing_section("topic" if topic == "v2.wiki" else "publish")
            settle(100)
            if topic == "v2.publish":
                return app.writing_help_buttons["publish"]
            candidates = app.menu_help_buttons[topic]
            def descendant(button):
                node = button
                while node is not None:
                    if node is visible_root:
                        return True
                    node = node.master
                return False
            candidates = [button for button in candidates if descendant(button)]
            titles = next((titles for target, key, titles in MENU_SECTIONS if key == topic), ())
            candidates.sort(key=lambda button: titles.index(button._help_heading.cget("text"))
                            if button._help_heading.cget("text") in titles else -1)
            # A scrolled-off canvas child may be unmapped. Bring its section
            # into view before asserting map state, without invoking an action.
            if candidates:
                scroll_to(candidates[0])
            mapped = [button for button in candidates if button.winfo_ismapped()]
            assert mapped, (topic, "no visible help button")
            return mapped[0]

        def scroll_to(button):
            parents = []
            node = button._help_heading.master
            while node is not app and node is not None:
                if isinstance(node, module.ctk.CTkScrollableFrame):
                    parents.append(node)
                node = node.master
            for scroll in reversed(parents):
                canvas = scroll._parent_canvas
                y = button.winfo_rooty() - scroll.winfo_rooty()
                canvas.yview_moveto(max(0, (y - 16) / max(1, scroll.winfo_height())))
                settle()
            return parents

        def capture(topic, button):
            parents = scroll_to(button) if hasattr(button, "_help_heading") else []
            container = button._help_heading.master if hasattr(button, "_help_heading") else button.master
            while container.winfo_width() < 220 or container.winfo_height() < 80:
                if container.master is app.main_area:
                    break
                container = container.master
            left = max(container.winfo_rootx(), app.main_area.winfo_rootx() + 4)
            right = min(container.winfo_rootx() + container.winfo_width(),
                        app.winfo_rootx() + app.winfo_width() - 8)
            top = max(button.winfo_rooty() - 6, app.main_area.winfo_rooty() + 4)
            bottom = min(top + 240, app.winfo_rooty() + app.winfo_height() - 25,
                         *[p._parent_canvas.winfo_rooty() + p._parent_canvas.winfo_height() - 4 for p in parents])
            assert right - left > 150 and bottom - top > 30, (topic, left, top, right, bottom)
            destination = Path(directory) / f"{topic}.png"
            if sys.platform == "darwin":
                subprocess.run(["screencapture", "-x", "-R", f"{left},{top},{right-left},{bottom-top}", str(destination)], check=True)
            else:
                from PIL import ImageGrab
                ImageGrab.grab(bbox=(left, top, right, bottom)).save(destination)
            with Image.open(destination) as image:
                assert image.width >= right-left and image.height >= bottom-top
                assert image.width * (bottom-top) == image.height * (right-left), (topic, image.size)
                assert max(high for low, high in image.convert("RGB").getextrema()) >= 80, topic
            destination.replace(root / "assets" / "writing-guide" / f"{topic}.png")

        try:
            app.geometry(f"{width}x{height}+20+40")
            app.lift()
            app.focus_force()
            app.update()
            app._switch_page("tistory_v2")
            settle()
            samples = [module.KeywordInsight(keyword=text, score=90-i, reasons=["설명용 예시"],
                                             sources=["naver"], categories=["여행"],
                                             source_urls={"naver": "https://example.com"})
                       for i, text in enumerate(("제주도 여행 준비물", "제주 가족 여행", "렌터카 준비"))]
            for source in app.home_keyword_card_widgets:
                app.home_keyword_data[source] = samples
                app._render_home_keyword_source(source)
            app.naver_blog_reference_textbox.delete("1.0", "end")
            app.naver_blog_reference_textbox.insert("1.0", "[설명용 예시]\n제주 여행 전 예약 정보와 준비물을 확인합니다.")
            app.naver_blog_publish_name_entry.insert(0, "제주도 여행 준비물")
            for entry, value in ((app.remote_device_name_entry, "내 컴퓨터"),
                                 (app.remote_gateway_entry, "https://example.com"),
                                 (app.codex_cli_path_entry, "codex")):
                entry.configure(state="normal")
                entry.delete(0, "end")
                entry.insert(0, value)
            app.thumbnail_auto_title_var.set(False)
            app.thumbnail_prompt_preview.delete("1.0", "end")
            app.thumbnail_prompt_preview.insert("1.0", "제주 여행\n준비 체크")
            app.thumbnail_font_size_entry.delete(0, "end")
            app.thumbnail_font_size_entry.insert(0, "40")
            app.thumbnail_shadow_menu.set("없음")
            app.thumbnail_text_stroke_menu.set("없음")
            app.cardnews_specs = [{"heading": "출발 전 준비", "summary": "예약 정보와 신분증을 확인해요."},
                                 {"heading": "가방에 챙길 물건", "summary": "충전기와 개인 물품을 챙겨요."}]
            app.active_cardnews_slide_index = 0
            app._render_active_cardnews_slide()
            app._generate_thumbnail_preview()
            panel = app.tistory_v2_panel
            demo_deck = panel.store.new_deck("제주도 여행", "제주 여행 준비 체크",
                "<h2>출발 전 준비</h2><p>예약 정보와 신분증을 확인하세요.</p>"
                "<h2>가방에 챙길 물건</h2><p>충전기와 개인 물품을 챙기세요.</p>", 10, "스카이")
            panel.store.save_deck(demo_deck)
            panel._load_deck(demo_deck)
            # The keyword library is intentionally built on first navigation,
            # just like V2. Inventory it only after exercising that real route.
            previous_page = app.current_page
            app._switch_page("keywords")
            settle(100)
            app._switch_page(previous_page)
            missing = set(MENU_GUIDES) - set(app.menu_help_buttons) - {"v2.publish"}
            print("Guide inventory:", len(MENU_GUIDES), "new topics; missing:", sorted(missing), flush=True)
            assert not missing, missing
            if args.inventory:
                return
            if args.capture:
                app.attributes("-topmost", True)
            for topic in MENU_GUIDES:
                if args.only and topic not in args.only:
                    continue
                button = locate(topic)
                if args.capture:
                    app.geometry(f"{width}x{height}+20+40")
                    app.lift()
                    app.focus_force()
                    settle(180)
                    capture(topic, button)
                else:
                    if hasattr(button, "_help_heading"):
                        parents = scroll_to(button)
                        label = button._help_heading
                        gap = button.winfo_rootx() - label.winfo_rootx() - label.winfo_width()
                        assert 0 <= gap <= 3, (topic, gap)
                        assert button.winfo_width() <= round(21 * button._get_widget_scaling()) + 1
                        assert label.grid_info()["in"] is button.master
                    with Image.open(root / "assets" / "writing-guide" / f"{topic}.png") as image:
                        image.verify()
                    original = copy.deepcopy(asdict(app.wordpress_settings))
                    original_page = app.current_page
                    original_variant = app.writing_variant
                    # Isolate all writes: opening a manual must not save or
                    # start an AI/browser/publishing job, even on settings pages.
                    with patch.object(module.AppStateStore, "save") as save, \
                         patch.object(app, "_save_ui_state") as ui_save, \
                         patch.object(app, "_run_publish_pipeline") as publish:
                        button.invoke()
                        settle()
                        dialog = app.writing_help_dialog
                        assert dialog.topic == topic, (topic, dialog.topic)
                        assert dialog._source_image is not None, topic
                        assert dialog.heading.cget("text") == ALL_GUIDES[topic]["title"]
                        assert dialog.winfo_toplevel() is app
                        assert dialog.grab_current() is dialog
                        assert asdict(app.wordpress_settings) == original
                        assert (app.current_page, app.writing_variant) == (original_page, original_variant)
                        save.assert_not_called()
                        ui_save.assert_not_called()
                        publish.assert_not_called()
                    dialog.close()
                    assert not module.modal_dimmer(app).active, topic
                print(args.theme, topic, "OK", flush=True)
            # Decorate repeatedly without new widgets/duplicate icons.
            counts = {key: len(value) for key, value in app.menu_help_buttons.items()}
            app._install_menu_help()
            repeated = {key: len(value) for key, value in app.menu_help_buttons.items()}
            assert counts == repeated, {key: (counts.get(key), value) for key, value in repeated.items()
                                       if counts.get(key) != value}
            # Standard writing guides also stay available in V2, but publishing
            # instructions resolve to V2-specific review/collage instructions.
            app._switch_page("tistory_v2")
            app._open_writing_section("topic")
            settle()
            assert app.writing_help_buttons["topic"].winfo_ismapped()
            app._open_writing_help("publish")
            settle()
            assert app.writing_help_dialog.topic == "v2.publish"
            app._switch_page("settings")
            assert not module.modal_dimmer(app).active
            app.geometry("860x680+20+40")
            app._open_writing_help("settings.font")
            settle()
            dialog = app.writing_help_dialog
            assert dialog.winfo_width() <= app.main_area.winfo_width()
            assert dialog.winfo_height() <= app.main_area.winfo_height()
            dialog.close()
            print(f"{args.theme}: all-menu manuals, images, read-only behavior, V2 and modal cleanup passed", flush=True)
        finally:
            app.destroy()
            faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
