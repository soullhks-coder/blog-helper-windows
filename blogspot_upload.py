"""Bounded, pre-transfer Google Picker recovery; never retries a file upload."""
from __future__ import annotations

import math
import re
import time

from playwright.sync_api import Error as PlaywrightError


PICKER_SELECTOR = 'iframe[src*="docs.google.com/picker"]:visible'
PICKER_OPEN_ATTEMPTS = 3
PICKER_WAIT_SECONDS = 15


class BlogspotImageUploadError(RuntimeError):
    """An incomplete image operation: stop publication and preserve the editor."""


def open_image_picker(page, *, find_image_button, describe_state, log, check_cancel,
                      wait_seconds=PICKER_WAIT_SECONDS):
    """Return the one visible picker and its usable Browse button.

    No file chooser/set_files call is made here. Only this pre-transfer stage
    can be retried, so an uncertain upload can never send the same file twice.
    """
    def ready_picker():
        try:
            if not page.locator(PICKER_SELECTOR).count():
                return None
            frame = page.frame_locator(PICKER_SELECTOR).first
            browse = frame.get_by_role("button", name=re.compile(r"^(?:찾아보기|Browse)$", re.I)).first
            return (frame, browse) if browse.is_visible() and browse.is_enabled() else None
        except PlaywrightError:
            # Google can detach/recreate its frame while it is initializing.
            return None

    last_error = "선택기 또는 찾아보기 버튼이 준비되지 않았습니다."
    for attempt in range(1, PICKER_OPEN_ATTEMPTS + 1):
        check_cancel()
        if page.is_closed():
            raise BlogspotImageUploadError("이미지 선택기를 여는 중 Blogger Chrome이 닫혔습니다.")
        ready = ready_picker()
        if ready:
            log("Google 이미지 선택기 열림·찾아보기 버튼 준비 확인 (기존 창 재사용)")
            return ready
        try:
            if attempt > 1:
                # A late picker may have become ready since the previous poll.
                # Never dismiss a usable picker just to click the menu again.
                ready = ready_picker()
                if ready:
                    return ready
                page.keyboard.press("Escape")
                page.wait_for_timeout(200)
                check_cancel()
                if page.locator(PICKER_SELECTOR).count():
                    raise BlogspotImageUploadError(
                        "응답하지 않는 이미지 선택기가 닫히지 않아 중복 창을 열지 않고 중단했습니다.")

            if not page.locator(PICKER_SELECTOR).count():
                image_button = find_image_button(page)
                if image_button is None:
                    raise BlogspotImageUploadError("블로그스팟의 이미지 삽입 버튼을 찾지 못했습니다.")
                image_button.click(force=True)
                option = page.locator('[role="menuitem"]:visible').filter(
                    has_text=re.compile(r"컴퓨터에서 업로드|Upload from computer", re.I)).first
                option.wait_for(state="visible", timeout=5_000)
                check_cancel()
                option.focus()
                if attempt == 1:
                    option.press("Enter")
                else:
                    option.click(force=True)
                log(f"Google 이미지 선택기 열기 요청 {attempt}/{PICKER_OPEN_ATTEMPTS} · 실제 창 준비 대기")

            deadline = time.monotonic() + wait_seconds
            for _ in range(max(1, math.ceil(wait_seconds / .25)) + 1):
                check_cancel()
                if page.is_closed():
                    raise BlogspotImageUploadError("이미지 선택기를 여는 중 Blogger Chrome이 닫혔습니다.")
                ready = ready_picker()
                if ready:
                    log("Google 이미지 선택기 열림·찾아보기 버튼 준비 확인")
                    return ready
                remaining = round((deadline - time.monotonic()) * 1000)
                if remaining <= 0:
                    break
                page.wait_for_timeout(min(250, remaining))
        except PlaywrightError as exc:
            check_cancel()
            if page.is_closed():
                raise BlogspotImageUploadError("이미지 선택기를 여는 중 Blogger Chrome이 닫혔습니다.") from exc
            last_error = type(exc).__name__
        log(f"Google 이미지 선택기 준비 실패 {attempt}/{PICKER_OPEN_ATTEMPTS} · "
            f"{last_error} · {describe_state(page)}")
    raise BlogspotImageUploadError(
        f"Google 이미지 선택기를 {PICKER_OPEN_ATTEMPTS}회 확인했지만 준비되지 않았습니다. "
        "파일은 전송하지 않았으며 자동 발행을 중단했습니다.")
