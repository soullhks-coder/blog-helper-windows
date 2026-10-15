"""Bridge V2 to saved prompts, thumbnail designs and the existing Tistory login.

Only options/snapshot access Tk. Worker methods use detached values and report
events; editor input deliberately has no save/publication action.
"""
from __future__ import annotations

import copy
import json
import re
from html import escape
from pathlib import Path

from tistory_v2 import Cancelled, check_cancel, plain_text


class _ProgressQueue:
    def __init__(self, progress, cancel):
        self.progress, self.cancel = progress, cancel
        self.result = None
        self.error = ""

    def put(self, event):
        check_cancel(self.cancel)
        kind, payload = event
        if kind == "article_done":
            self.result = payload
        elif kind == "article_error":
            self.error = payload
        elif kind.endswith("progress"):
            self.progress(payload[1] if isinstance(payload, tuple) else str(payload))


class TistoryV2Workflow:
    def __init__(self, app, services):
        self.app, self.api = app, services

    def options(self):
        api, app = self.api, self.app
        settings = app.wordpress_settings
        prompts = [copy.deepcopy(item) for item in app._prompt_sets() if item.get("platform") == "tistory"]
        profiles = api.normalize_tistory_profiles(settings.tistory_profiles)
        labels = dict(api.home_blog_choices("tistory", settings))
        return {
            "prompts": [{"id": p["id"], "label": app._prompt_set_label(p)} for p in prompts],
            "profiles": [{"id": p["name"], "label": labels[p["name"]]} for p in profiles if p["name"] in labels],
            "active_profile": settings.tistory_active_profile,
            "thumbnail": api.normalize_thumbnail_preset_index(settings.thumbnail_default_preset),
            "thumbnails": [f"썸네일·카드{i + 1}" for i in range(api.THUMBNAIL_PRESET_COUNT)],
            "models": list(api.WRITING_MODEL_OPTIONS),
            "model": api.writing_model_label(settings.preferred_ai_provider),
        }

    def snapshot(self, selection, *, generation=False, editor=False, title=""):
        """Called on the UI thread; never persist secrets in the deck manifest."""
        api, app = self.api, self.app
        settings = copy.deepcopy(app.wordpress_settings)
        prompt = next((item for item in app._prompt_sets()
                       if item.get("id") == selection.get("prompt_id") and item.get("platform") == "tistory"), None)
        if generation and not prompt:
            raise ValueError("사용할 티스토리 글쓰기 프롬프트를 선택해 주세요.")
        if prompt:
            settings.title_prompt_template = str(prompt["title_prompt"])
            settings.article_prompt_template = str(prompt["article_prompt"])
        settings.preferred_ai_provider = api.normalize_writing_model(selection.get("model", settings.preferred_ai_provider))
        settings.inline_images_enabled = False
        if generation:
            error = api.writing_model_validation_error(settings)
            if error:
                raise ValueError(error)
        profile = next((p for p in api.normalize_tistory_profiles(settings.tistory_profiles)
                        if p["name"] == selection.get("profile")), None)
        thumbnail = api.normalize_thumbnail_preset_index(selection.get("thumbnail", 0))
        result = {"settings": settings, "profile": copy.deepcopy(profile), "selection": copy.deepcopy(selection)}
        if editor:
            if not profile:
                raise ValueError("환경설정에 등록한 티스토리 블로그를 선택해 주세요.")
            write_url = api.build_tistory_write_url(profile["blog_url"], profile["write_url"])
            if not write_url:
                raise ValueError("선택한 티스토리의 블로그 주소 또는 글쓰기 URL을 환경설정에서 입력해 주세요.")
            result.update(write_url=write_url, thumbnail=self._thumbnail_svg(thumbnail, title))
        return result

    def _thumbnail_svg(self, index, title):
        app = self.app
        app._save_active_thumbnail_preset()
        original_index = app.active_thumbnail_preset_index
        presets = copy.deepcopy(app.thumbnail_presets)
        preset = copy.deepcopy(presets[index])
        if preset.get("auto_title", True):
            preset["text"] = title
        try:
            app.active_thumbnail_preset_index = index
            app._apply_thumbnail_preset_to_ui(preset, refresh_preview=False, apply_cardnews=False)
            width = max(200, app._safe_int(app.thumbnail_width_entry.get(), 400))
            height = max(200, app._safe_int(app.thumbnail_height_entry.get(), 400))
            size = max(20, app._safe_int(app.thumbnail_font_size_entry.get(), 56))
            return {"svg": app._build_thumbnail_svg(width, height, size), "size": max(width, height), "index": index}
        finally:
            app.thumbnail_presets = presets
            app.active_thumbnail_preset_index = original_index
            app._apply_thumbnail_preset_to_ui(presets[original_index], refresh_preview=False, apply_cardnews=False)

    def generate(self, snapshot, keyword, reference, cancel, progress):
        check_cancel(cancel)
        events = _ProgressQueue(progress, cancel)
        self.api.ArticleGenerationWorker(snapshot["settings"], keyword, keyword, reference, events,
                                         tistory_v2_slides=True).run()
        check_cancel(cancel)
        if events.error or not events.result:
            raise RuntimeError(events.error or "글 생성 결과를 받지 못했습니다.")
        result = events.result
        if not plain_text(result.get("html", "")).strip():
            raise RuntimeError("생성된 본문이 비어 있습니다. 프롬프트와 AI 응답을 확인해 주세요.")
        return result

    def summarize(self, snapshot, title, body, cancel, progress):
        from tistory_v2_summary import request_summaries
        progress("최종 본문을 슬라이드용 짧은 문구로 요약하고 있습니다...")

        def generate(prompt):
            check_cancel(cancel)
            result = self.api.generate_text_with_writing_model(snapshot["settings"], prompt)
            check_cancel(cancel)
            return result

        return request_summaries(title, body, generate)

    def fill_editor(self, snapshot, deck, paths, directory, cancel, progress, notify):
        from playwright.sync_api import sync_playwright
        from tistory_v2_editor import insert_native_slide_pairs

        api = self.api
        if len(paths) != 10 or len(deck.get("slides", [])) != 10:
            raise ValueError("티스토리 입력에는 슬라이드 10장이 필요합니다. 글에서 슬라이드를 다시 만들어 주세요.")
        body = str(deck.get("body") or "").strip()
        if not plain_text(body):
            raise ValueError("티스토리에 입력할 본문이 없습니다.")
        title = str(deck.get("title") or deck.get("keyword") or "").strip()
        body, tags = api.extract_tistory_prompt_tags(api.normalize_generated_article_html(body))
        if not tags:
            _, tags = api.build_focus_keyword_and_tags(deck.get("keyword", title), title, body)
        # Keep the two source credits attached to the article as well as the PNGs.
        credits = []
        for slide in deck["slides"][:2]:
            asset = slide.get("image") or {}
            url = str(asset.get("source_page") or "")
            if url.startswith(("https://ko.wikipedia.org/", "https://namu.wiki/")):
                credits.append(f'<a href="{escape(url, quote=True)}">{escape(asset.get("source", "사진 출처"))}</a>')
        if credits:
            body += '<p>사진 출처: ' + " · ".join(dict.fromkeys(credits)) + '</p>'
        profile = snapshot["profile"]
        scope = profile["profile_scope"]
        spec = snapshot["thumbnail"]
        thumbnail_path = api.render_svg_to_png(spec["svg"], Path(directory) / "thumbnail.png", spec["size"], "티스토리 V2 썸네일")
        check_cancel(cancel)
        events = _ProgressQueue(progress, cancel)
        with sync_playwright() as playwright:
            context = api.launch_tistory_persistent_context(playwright, scope)
            page = None
            try:
                state = api.load_tistory_storage_state(scope)
                if state.get("cookies"):
                    context.add_cookies(state["cookies"])
                # Open a dedicated V2 tab rather than replacing an existing tab.
                page = context.new_page()
                page.set_default_timeout(20_000)
                page.set_default_navigation_timeout(60_000)
                def handle_dialog(dialog):
                    if re.search(r"이어서|작성하던|복원|이전.{0,20}작성|임시.{0,20}불러", dialog.message):
                        dialog.dismiss()
                    else:
                        dialog.accept()

                page.on("dialog", handle_dialog)
                progress("선택한 티스토리의 저장된 로그인으로 글쓰기 화면에 진입합니다...")
                page.goto(snapshot["write_url"], wait_until="domcontentloaded")
                page, _ = api.wait_for_tistory_editor(context, page, snapshot["write_url"], events,
                                                       check_cancel=lambda: check_cancel(cancel))
                api.save_tistory_storage_state(context, scope)
                check_cancel(cancel)
                script = api.build_tistory_editor_automation_script(
                    title, body, input_mode=profile["input_mode"],
                    automation_actions=["switch_html", "confirm_dialog", "set_title", "set_body", "switch_basic", "confirm_dialog", "set_rich_body"],
                    publish_after_input=False,
                )
                progress("완성된 제목과 본문을 티스토리에 입력합니다...")
                result = page.evaluate(script)
                result = json.loads(result) if isinstance(result, str) else result
                if not result.get("titleOk") or not (result.get("bodyOk") or result.get("richBodyOk")):
                    raise RuntimeError("티스토리 제목·본문 입력 결과를 확인하지 못했습니다.")
                used_urls = set()

                def upload(path):
                    check_cancel(cancel)
                    url = api.upload_tistory_image_file(page, str(path), events, excluded_urls=used_urls)
                    if url in used_urls:
                        raise RuntimeError("같은 이미지 업로드 주소가 중복되었습니다.")
                    used_urls.add(url)
                    return url

                from tistory_v2 import deck_slide_size, deck_slide_sizes
                result = insert_native_slide_pairs(page, [str(p) for p in paths], str(thumbnail_path), upload,
                                                   progress, cancel, expected_size=deck_slide_size(deck),
                                                   expected_sizes=deck_slide_sizes(deck))
                check_cancel(cancel)
                api.enter_tistory_tags_native(page, tags[:8], events)
                api.save_tistory_storage_state(context, scope)
                notify("editor_ready", "티스토리 입력 완료 · 썸네일 1장 + 슬라이드 10장 / 2장씩 콜라주 5개. Chrome에서 확인해 주세요.")
                api.append_runtime_log("TISTORY V2", f"편집기 입력 완료: {profile['name']} · 슬라이드 10장 · 콜라주 5개")
                self._wait_for_browser(page, cancel)
                return result
            except Cancelled:
                raise
            except Exception as exc:
                # Preserve the visible editor on a failed upload/drag for inspection.
                if page is not None and not page.is_closed():
                    notify("editor_attention", f"입력 확인 필요: {exc}\nChrome을 열어 두었습니다. 확인 후 브라우저를 닫아 주세요.")
                    self._wait_for_browser(page, cancel)
                raise
            finally:
                try:
                    api.save_tistory_storage_state(context, scope)
                finally:
                    context.close()

    @staticmethod
    def _wait_for_browser(page, cancel):
        while not page.is_closed():
            check_cancel(cancel)
            try:
                page.wait_for_timeout(400)
            except Exception:
                if page.is_closed():
                    break
                raise
