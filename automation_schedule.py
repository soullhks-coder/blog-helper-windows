"""Rebase waiting jobs once per application launch without replaying uncertain work."""
from __future__ import annotations

import copy
import math


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
