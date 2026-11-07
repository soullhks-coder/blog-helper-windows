"""Detached Tistory V2 queue jobs. No Tk or account mutations on worker threads."""
from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from keyword_library import clean_keyword, keyword_key
from tistory_v2 import (V2Store, assign_deck_themes, assign_slide_images,
                        apply_text_positions, apply_slide_font_sizes)
from writing_run import WritingCancelled, check_writing_cancelled


TARGET = "tistory_v2"
PAUSED = "V2 확인 필요"
EDITABLE = {"V2 대기 중", "대기 중", PAUSED}
PROFILE_FIELDS = ("blog_url", "write_url", "input_mode", "auto_publish", "upload_webp",
                  "save_mode", "reference_image_protection_mode", "ads_enabled", "ads_code",
                  "ads_slot_id", "ads_position", "ads_count", "daily_publish_limit", "profile_scope")


def targets(values):
    """V2 is one exclusive mode, never a second post to the same Tistory blog."""
    values = list(dict.fromkeys(v for v in (values or [])
                               if v in {"wordpress", "tistory", "blogspot", TARGET}))
    return [TARGET] if TARGET in values else values


def snapshot(app, api):
    """Capture only public job options. API credentials always stay in settings."""
    if getattr(app.wordpress_settings, "_state_read_error", ""):
        raise ValueError("설정 파일을 복구한 뒤 V2 대기열을 등록해 주세요.")
    values = V2Store(api.DATA_DIR / "Tistory V2").preferences()
    profiles = api.normalize_tistory_profiles(app.wordpress_settings.tistory_profiles)
    wanted = values.get("profile_selections", {}).get("tistory")
    wanted = wanted or (values.get("profile") if values.get("platform", "tistory") == "tistory" else None)
    wanted = wanted or app.wordpress_settings.tistory_active_profile
    profile = next((p for p in profiles if p["name"] == wanted), None)
    if not profile or not api.build_tistory_write_url(profile["blog_url"], profile["write_url"]):
        raise ValueError("티스토리 V2 메뉴에서 사용할 티스토리 블로그를 먼저 선택해 주세요.")
    identity = "tistory:" + profile["name"]
    remembered = values.get("blog_preferences", {}).get(identity,
                    values.get("blog_preferences", {}).get(profile["name"], {}))
    active_tistory = values.get("platform", "tistory") == "tistory"
    prompt_id = values.get("prompt_id") if active_tistory else remembered.get("prompt_id")
    prompts = [p for p in app._prompt_sets() if p.get("platform") == "tistory"]
    prompt = next((p for p in prompts if p.get("id") == prompt_id), None)
    if prompt is None and prompt_id is None and prompts:
        prompt = prompts[0]  # Same default as the V2 selector's first option.
    if not prompt:
        raise ValueError("티스토리 V2 메뉴에서 사용할 티스토리 글쓰기 프롬프트를 선택해 주세요.")
    index = api.normalize_thumbnail_preset_index(
        values.get("thumbnail", app.wordpress_settings.thumbnail_default_preset) if active_tistory
        else remembered.get("thumbnail", app.wordpress_settings.thumbnail_default_preset))
    app._save_active_thumbnail_preset()
    return {
        "profile": {"name": profile["name"], **{key: copy.deepcopy(profile[key]) for key in PROFILE_FIELDS}},
        "prompt": {key: str(prompt[key]) for key in ("id", "name", "title_prompt", "article_prompt")},
        "thumbnail_index": index,
        "thumbnail_preset": copy.deepcopy(app.thumbnail_presets[index]),
        "review_publish": bool(values.get("review_publish", False)),
        "slide_options": {key: copy.deepcopy(values[key]) for key in
                          ("theme", "show_numbers", "show_photo_sources", "slide_size",
                           "text_position_defaults", "font_size_defaults") if key in values},
    }


def execution_settings(config, base, api):
    profile = config["profile"]
    current = next((p for p in api.normalize_tistory_profiles(base.tistory_profiles)
                    if p["name"] == profile["name"]), None)
    # Never silently use a different account if a slot is re-registered later.
    if (not current or current["profile_scope"] != profile["profile_scope"] or
        api.build_tistory_write_url(current["blog_url"], current["write_url"]) !=
            api.build_tistory_write_url(profile["blog_url"], profile["write_url"])):
        raise ValueError("대기열에 지정한 티스토리 계정이 변경되었거나 삭제되었습니다. 해당 작업을 삭제하고 다시 넣어 주세요.")
    settings = copy.deepcopy(base)
    settings.target_platforms = ["tistory"]
    settings.inline_images_enabled = False
    settings.cardnews_enabled = False
    settings.tistory_active_profile = profile["name"]
    for key in PROFILE_FIELDS:
        if key != "profile_scope":
            setattr(settings, "tistory_" + key, copy.deepcopy(profile[key]))
    # Honor tightened limits even for already queued jobs.
    settings.tistory_daily_publish_limit = current["daily_publish_limit"]
    prompt = config["prompt"]
    settings.selected_prompt_id = prompt["id"]
    settings.title_prompt_template = prompt["title_prompt"]
    settings.article_prompt_template = prompt["article_prompt"]
    settings.writing_prompt_active_target = "tistory"
    if config.get("review_publish"):
        settings.tistory_auto_publish = False
        settings.tistory_save_mode = api.TISTORY_SAVE_MODE_PUBLISH
    return settings


def _account(config):
    profile = config.get("profile", {})
    return (urlsplit(profile.get("write_url") or profile.get("blog_url") or "").hostname
            or profile.get("profile_scope", "")).casefold()


def make_items(rows, config, existing):
    """Dedupe per mode/account; never consume the keyword on queue registration."""
    account = _account(config)
    seen = {(_account(item["v2_config"]),
             keyword_key(item.get("keyword", ""))) for item in existing if item.get("v2_config")}
    items, skipped = [], 0
    for row in rows:
        keyword = str(row.get("keyword") or "").strip()
        key = (account, keyword_key(keyword))
        if not keyword or key in seen:
            skipped += 1
            continue
        seen.add(key)
        items.append({"id": "v2-" + uuid.uuid4().hex, "keyword": keyword,
                      "wiki_keyword": str(row.get("wiki_keyword") or "").strip(),
                      "title": str(row.get("title") or keyword), "topic": "키워드 관리",
                      "source_name": str(row.get("source_name") or "키워드 관리"),
                      "article_html": str(row.get("article_html") or ""),
                      "reference_text": str(row.get("reference_text") or ""),
                      "source_urls": copy.deepcopy(row.get("source_urls") or {}),
                      "status": "V2 대기 중", "target_platforms": [TARGET],
                      "v2_config": copy.deepcopy(config)})
    return items, skipped


def can_edit(item, active_id=""):
    return bool(item and item.get("v2_config") and item.get("status") in EDITABLE
                and str(item.get("id") or "") != str(active_id)
                and not item.get("published_url") and not item.get("published_urls"))


def edited_keywords(item, expected, keyword, wiki_keyword, existing, *, active_id=""):
    """Validate before changing anything; leave manager rows and other jobs alone."""
    if not can_edit(item, active_id):
        raise ValueError("대기 중인 V2 작업만 수정할 수 있습니다. 실행 중이거나 발행된 작업은 변경하지 않습니다.")
    if item != expected:
        raise ValueError("대기열 정보가 변경되었습니다. 수정창을 닫고 최신 항목에서 다시 열어 주세요.")
    keyword, wiki_keyword = clean_keyword(keyword), clean_keyword(wiki_keyword)
    if not keyword:
        raise ValueError("글쓰기 키워드를 입력해 주세요.")
    if len(keyword) > 500 or len(wiki_keyword) > 500:
        raise ValueError("키워드와 위키 검색어는 각각 500자 이내로 입력해 주세요.")
    account = _account(item["v2_config"])
    if any(other.get("id") != item["id"] and other.get("v2_config")
           and _account(other["v2_config"]) == account
           and keyword_key(other.get("keyword", "")) == keyword_key(keyword) for other in existing):
        raise ValueError("이 블로그의 V2 대기열에 같은 키워드가 이미 있습니다.")
    article_changed = keyword != item.get("keyword", "")
    wiki_changed = wiki_keyword != item.get("wiki_keyword", "")
    result = copy.deepcopy(item)
    if not article_changed and not wiki_changed:
        return result
    result.update(keyword=keyword, wiki_keyword=wiki_keyword,
                  keyword_edit_revision=int(item.get("keyword_edit_revision", 0)) + 1,
                  keywords_edited_at=datetime.now().isoformat(timespec="seconds"))
    # Keep the reservation, publication settings and failed/review boundary.
    # No files are deleted: old saved decks may still be open in the editor.
    for key in ("v2_deck", "cardnews_image_paths", "cardnews_paths", "slide_paths"):
        result.pop(key, None)
    if article_changed:
        result.update(title=keyword, article_html="", reference_text="", source_urls={}, thumbnail_path="")
        for key in ("slide_summaries", "provider", "cleanup_paths", "generation_error"):
            result.pop(key, None)
    return result


class PrepareWorker(threading.Thread):
    def __init__(self, item, settings, api, result_queue):
        super().__init__(daemon=True, name="AutomationTistoryV2")
        self.item, self.settings = copy.deepcopy(item), settings
        self.api, self.result_queue = api, result_queue
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        from tistory_v2 import Cancelled, collect_wiki_images
        from tistory_v2_summary import build_summary_slides, validate_summaries
        from tistory_v2_workflow import TistoryV2Workflow
        api, item, cancel = self.api, self.item, self.cancel_event

        def report(message):
            check_writing_cancelled(cancel)
            self.result_queue.put(("automation_v2_progress", {"id": item["id"], "message": str(message)}))

        try:
            check_writing_cancelled(cancel)
            workflow = TistoryV2Workflow(None, api)
            if not item.get("article_html", "").strip():
                report(f"'{item['keyword']}' 참고자료를 수집합니다...")
                helper = api.AutomationKeywordQueueWorker(self.settings, [], _ReferenceProgress(report))
                item["reference_text"] = helper._collect_reference_text_for_keyword(item)
                check_writing_cancelled(cancel)
                result = workflow.generate({"settings": self.settings}, item["keyword"],
                                           item["reference_text"], cancel, report)
                item.update(title=result["title"], article_html=result["html"],
                            provider=result.get("provider", ""), slide_summaries=result.get("slide_summaries"))
                # Save the completed article before photos/rendering. A retry
                # must not charge for an already successful article again.
                self.result_queue.put(("automation_v2_article_ready", copy.deepcopy(item)))
            title, body = item["title"], item["article_html"]
            try:
                summaries = validate_summaries(item.get("slide_summaries"), title, body)
            except ValueError:
                summaries = workflow.summarize({"settings": self.settings}, title, body, cancel, report)
            check_writing_cancelled(cancel)
            store = V2Store(api.DATA_DIR / "Tistory V2")
            options = item["v2_config"].get("slide_options", {})
            deck = store.new_deck(item["keyword"], title, body, 10, options.get("theme", "스카이"), preferences=options)
            deck["wiki_keyword"] = item["wiki_keyword"] or item["keyword"]
            deck["show_numbers"] = bool(options.get("show_numbers", True))
            deck["slides"] = build_summary_slides(title, body, summaries)
            deck["summary_slides"], deck["text_mode"] = summaries, "summary"
            positions = deck["text_position_defaults"]
            apply_text_positions(deck["slides"], positions["text_horizontal"], positions["text_vertical"])
            apply_slide_font_sizes(deck["slides"], deck["font_size_defaults"])
            assign_deck_themes(deck)
            try:
                deck["images"], warnings = collect_wiki_images(
                    deck["wiki_keyword"], store.deck_directory(deck) / "images",
                    api.require_google_chrome_executable(), cancel=cancel, progress=report)
                for warning in warnings:
                    report(warning)
            except (WritingCancelled, Cancelled, MemoryError):
                raise
            except Exception as exc:
                deck["images"] = []
                report(f"위키 사진 수집 제한: {str(exc)[:180]} · 공통 스타일로 계속합니다.")
            check_writing_cancelled(cancel)
            assign_slide_images(deck["slides"], deck["images"][:2])
            store.save_deck(deck)
            self.result_queue.put(("automation_v2_prepared", {**item, "slide_summaries": summaries, "v2_deck": deck}))
        except (WritingCancelled, Cancelled):
            self.result_queue.put(("automation_v2_error", {"id": item["id"], "message": "V2 작업을 중단했습니다."}))
        except Exception as exc:
            self.result_queue.put(("automation_v2_error", {"id": item["id"], "message": str(exc)}))


class _ReferenceProgress:
    def __init__(self, report):
        self.report = report

    def put(self, event):
        self.report(str(event[1]))
