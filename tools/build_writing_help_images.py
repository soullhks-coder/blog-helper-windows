"""Refresh bundled guide screenshots from a sandboxed, offline demo window.

No real account/profile, AI request, browser upload or publication is used.
Run on a desktop with screen capture permission after writing UI changes.
"""
from contextlib import ExitStack
import faulthandler
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch


def main():
    faulthandler.dump_traceback_later(45)
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="blog-helper-guide-demo-") as directory, ExitStack() as stack:
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(root))
        import main as app_module
        settings = app_module.WordPressSettings(app_theme="화이트테마", writing_auto_progress=False,
                                                 window_geometry="1300x1100", preferred_ai_provider="codex")
        stack.enter_context(patch.object(app_module.AppStateStore, "load", return_value=settings))
        for name in ("save", "update_fields"):
            stack.enter_context(patch.object(app_module.AppStateStore, name))
        for name in ("_start_remote_agent_if_enabled", "_start_update_check", "_poll_queue",
                     "_automation_publish_scheduler_tick", "_load_version_catalog", "_save_ui_state",
                     "_save_ui_state_now", "_on_window_configure"):
            stack.enter_context(patch.object(app_module.KeywordApp, name, return_value=None))
        print("Constructing isolated guide demo", flush=True)
        app = app_module.KeywordApp()
        print("Guide demo constructed", flush=True)
        output = root / "assets" / "writing-guide"
        output.mkdir(parents=True, exist_ok=True)
        errors = []
        app.report_callback_exception = lambda *error: errors.append(error)

        def settle():
            app.after(200, app.quit)
            app.mainloop()
            app.update_idletasks()
            assert not errors, errors

        def shot(topic, widgets):
            widgets = list(widgets)
            canvas = app.writing_scroll._parent_canvas
            canvas.yview_moveto(0)
            settle()
            top = min(widget.winfo_rooty() for widget in widgets)
            content_top = top - app.writing_scroll.winfo_rooty()
            total = max(1, app.writing_scroll.winfo_height())
            canvas.yview_moveto(max(0, (content_top - 20) / total))
            settle()
            left = min(widget.winfo_rootx() for widget in widgets)
            top = min(widget.winfo_rooty() for widget in widgets)
            right = max(widget.winfo_rootx() + widget.winfo_width() for widget in widgets)
            bottom = max(widget.winfo_rooty() + widget.winfo_height() for widget in widgets)
            assert top >= canvas.winfo_rooty(), (topic, top, canvas.winfo_rooty())
            assert bottom <= canvas.winfo_rooty() + canvas.winfo_height(), (topic, bottom, canvas.winfo_height())
            destination = output / f"{topic}.png"
            if sys.platform == "darwin":
                subprocess.run(["screencapture", "-x", "-R", f"{left},{top},{right-left},{bottom-top}", str(destination)], check=True)
            else:
                from PIL import ImageGrab
                ImageGrab.grab(bbox=(left, top, right, bottom)).save(destination)
            print(f"Guide screenshot: {topic}", flush=True)

        try:
            app._switch_page("writing")
            app.attributes("-topmost", True)
            app.lift()
            app.focus_force()
            app.update()
            app._open_writing_section("topic")
            app.topic_entry.delete(0, "end")
            app.topic_entry.insert(0, "제주도 여행 준비물")
            shot("topic", [app.writing_section_content_headers["topic"], app.topic_entry.master])
            app.benchmark_mode_var.set(True)
            app._on_benchmark_mode_changed(save=False)
            app.benchmark_url_entry.insert(0, "https://example.com/travel-guide")
            shot("benchmark", [app.benchmark_title_group.master])
            app.benchmark_mode_var.set(False)
            app._on_benchmark_mode_changed(save=False)
            for text, url, position in (("신청하러 가기", "https://example.com/apply", "본문상단"),
                                        ("자세히 알아보기", "https://example.com/details", "본문하단")):
                app._add_link_row({"button_text": text, "url": url, "position": position, "full_width": True})
            with patch.object(app_module.simpledialog, "askstring", return_value="자주 쓰는 안내 버튼"):
                app.save_link_set_button.invoke()
            shot("links", [app.link_card])
            app._open_writing_section("keyword")
            app._render_keyword_choices([app_module.KeywordInsight(
                keyword=text, score=90-i, reasons=["설명용 예시"], sources=["naver"], categories=["여행"],
                source_urls={"naver": "https://example.com"})
                for i, text in enumerate(("제주도 여행 준비물", "제주도 가족 여행", "제주도 렌터카 준비"))])
            app.selected_keyword_var.set("제주도 여행 준비물")
            shot("keyword", [app.writing_section_content_headers["keyword"], app.keyword_choice_frame])
            app.manual_keyword_entry.insert(0, "제주도 여행 준비물 체크리스트")
            shot("manual", [app.writing_help_buttons["manual"].master, app.manual_keyword_entry])
            app.reference_textbox.insert("1.0", "[설명용 참고자료 예시]\n여행 일정과 날씨에 맞춰 준비물을 정리합니다.\n신분증, 예약 확인서, 충전기 등 필요한 항목을 확인하세요.\n정확한 안내는 이용할 시설의 공식 홈페이지에서 확인해 주세요.")
            shot("reference", [app.writing_help_buttons["reference"].master.master, app.reference_textbox])
            app.inline_images_enabled_var.set(True)
            app._on_inline_images_enabled_changed()
            app._on_inline_images_provider_changed(app_module.INLINE_IMAGES_PROVIDER_MANUAL)
            shot("images", [app.writing_inline_image_card])
            app._open_writing_section("article")
            title = "제주도 여행 준비물, 출발 전 확인할 체크리스트"
            app.article_title_entry.insert(0, title)
            app.article_editor.delete("1.0", "end")
            app.article_editor.insert("1.0", "<h2>출발 전 준비</h2>\n<p>예약 정보와 신분증을 확인하세요.</p>\n<h2>가방에 챙길 물건</h2>\n<p>충전기와 개인 물품을 정리해 보세요.</p>")
            shot("article", [app.article_title_entry, app.article_editor])
            app._open_writing_section("publish")
            app.thumbnail_auto_title_var.set(False)
            app.thumbnail_prompt_preview.delete("1.0", "end")
            app.thumbnail_prompt_preview.insert("1.0", "제주 여행\n준비 체크")
            app.cardnews_specs = [{"heading": "출발 전 준비", "summary": "예약 정보와 신분증을 확인하세요."},
                                  {"heading": "가방에 챙길 물건", "summary": "충전기와 개인 물품을 정리해요."}]
            app.active_cardnews_slide_index = 0
            app._render_active_cardnews_slide()
            app._generate_thumbnail_preview()
            shot("thumbnail", [app.writing_design_preview_panels[0]])
            shot("cardnews", [app.writing_design_preview_panels[1]])
            shot("publish", [app.publish_pipeline_button, app.queue_post_button,
                             app.tistory_retry_button, app.open_published_post_button])
        finally:
            app.destroy()
            faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
