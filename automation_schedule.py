"""Rebase waiting jobs once per application launch without replaying uncertain work."""
from __future__ import annotations

import copy
import math
import time


def can_edit_schedule(item, active_id=""):
    return bool(item and str(item.get("id") or "") != str(active_id)
                and item.get("status") not in {"업로드 완료", "업로드 중", "V2 준비 중"}
                and not item.get("published_url") and not item.get("published_urls"))


def edited_schedule(item, expected, scheduled_at, *, active_id="", now=None):
    """Validate the exact job before replacing only its future reservation."""
    if not can_edit_schedule(item, active_id):
        raise ValueError("대기 중인 작업만 예약시간을 변경할 수 있습니다.")
    if item != expected:
        raise ValueError("대기열 정보가 변경되었습니다. 시간 수정창을 다시 열어 주세요.")
    try:
        timestamp = float(scheduled_at)
    except (ValueError, TypeError):
        raise ValueError("예약할 날짜와 시간을 선택해 주세요.") from None
    if not math.isfinite(timestamp) or timestamp <= (time.time() if now is None else now):
        raise ValueError("현재 시각 이후의 날짜와 시간을 선택해 주세요.")
    result = copy.deepcopy(item)
    result["scheduled_at"] = timestamp
    # Changing a paused/failed job's clock must never resume publication.
    return result


def restart_schedule(items, started_at, interval_minutes):
    started_at = float(started_at)
    interval = int(interval_minutes) * 60
    if not math.isfinite(started_at) or interval <= 0:
        raise ValueError("프로그램 시작 시각과 등록 간격을 확인해 주세요.")
    result = copy.deepcopy(items)
    waiting = []
    for index, item in enumerate(result):
        if item.get("status") in {"업로드 완료", "업로드 중", "V2 준비 중", "V2 확인 필요"}:
            continue
        if not item.get("v2_config") and not str(item.get("article_html") or "").strip():
            continue
        try:
            previous = float(item.get("scheduled_at") or 0)
        except (TypeError, ValueError):
            previous = 0
        waiting.append((previous if math.isfinite(previous) else 0, index, item))
    waiting.sort(key=lambda row: (row[0], row[1]))
    for order, (_previous, _index, item) in enumerate(waiting, 1):
        item["scheduled_at"] = started_at + interval * order
    return result, len(waiting)
