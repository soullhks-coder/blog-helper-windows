"""Confirm a Blogger size-menu action before waiting for image dimensions.

Only trusted pointer input is used; never dispatch synthetic DOM clicks or
rewrite an uploaded image's HTML/size attributes to simulate success.
"""
from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError


SIZE_LABELS = ("매우 크게", "아주 크게")
MENU_ROLES = ("menuitemradio", "menuitemcheckbox", "menuitem", "option", "radio", "button")


def _visible(locator):
    for index in range(locator.count()):
        item = locator.nth(index)
        if item.is_visible():
            return item
    return None


def find_size_choice(page, labels=SIZE_LABELS):
    for label in labels:
        for role in MENU_ROLES:
            item = _visible(page.get_by_role(role, name=label, exact=True))
            if item is not None:
                return item, label
        text = _visible(page.get_by_text(label, exact=True))
        if text is not None:
            # Older Closure/GWT menus expose a nested text label rather than
            # a semantic menu item. Resolve the clickable row, not that span.
            row = _visible(text.locator(
                'xpath=ancestor-or-self::*[@role="menuitemradio" or @role="menuitemcheckbox" or @role="menuitem" '
                'or @role="option" or @role="radio" or self::button '
                'or contains(concat(" ",normalize-space(@class)," ")," goog-menuitem ")][1]'
            ))
            return (row if row is not None else text), label
    return None, None


def _accepted(item):
    try:
        if not item.is_visible():
            return True  # Popup dismissed after its action.
        return any(item.get_attribute(name) == "true" for name in ("aria-checked", "aria-selected", "data-selected"))
    except PlaywrightError:
        # Re-resolve after a menu rerender; detachment alone is not proof.
        return False


def select_popup_size(page, *, labels=SIZE_LABELS, log=lambda _message: None):
    for attempt in range(2):
        for check in range(13):
            item, label = find_size_choice(page, labels)
            if item is not None:
                break
            if page.is_closed():
                raise RuntimeError("Blogger 이미지 크기 선택 중 브라우저가 닫혔습니다.")
            if check < 12:
                page.wait_for_timeout(250)
        if item is None:
            raise RuntimeError("Blogger 이미지 크기의 '매우 크게' 항목을 찾지 못했습니다.")
        try:
            item.hover(timeout=4_000)
            page.wait_for_timeout(180)
            box = item.bounding_box()
            if box is None:
                raise RuntimeError("Blogger 이미지 크기 메뉴의 클릭 위치를 확인하지 못했습니다.")
            # Click the row's leading gutter instead of a possibly overlaid
            # inner label. A held press also gives hover-driven menus time to
            # activate the item before mouseup dispatches its action.
            item.click(position={"x": min(12, box["width"] / 2),
                                 "y": min(12, box["height"] / 2)}, delay=120, timeout=4_000)
        except PlaywrightError:
            if page.is_closed():
                raise RuntimeError("Blogger 이미지 크기 선택 중 브라우저가 닫혔습니다.") from None
            if attempt == 0:
                log("이미지 크기 메뉴가 갱신되어 같은 항목을 다시 확인합니다.")
                continue
            raise RuntimeError("Blogger 이미지 크기 메뉴 클릭을 완료하지 못했습니다. 작성 중인 글은 유지합니다.") from None
        log(f"이미지 크기 항목 포인터 클릭: {label} · 클릭 후 1.5초 대기 시작")
        page.wait_for_timeout(1_500)
        if _accepted(item):
            log(f"이미지 크기 선택 접수 확인: {label} · 본문 크기 반영 확인으로 진행")
            return
        if attempt == 0:
            log("이미지 크기 메뉴가 열린 채 선택되지 않아 같은 항목만 1회 다시 누릅니다.")
    raise RuntimeError("Blogger 이미지 크기 메뉴에서 선택이 접수되지 않았습니다. "
                       "같은 항목을 두 번 눌렀지만 메뉴가 열린 채로 남아 다음 작업을 중단했습니다.")
