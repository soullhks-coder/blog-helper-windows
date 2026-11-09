"""A bounded queue view; reservation data and cross-page selection stay intact."""
import math

PAGE_SIZE = 20


def page_window(items, page=0, page_size=PAGE_SIZE):
    def schedule(item):
        try:
            value = float(item.get("scheduled_at") or 0)
            return value if math.isfinite(value) else 0
        except (ValueError, TypeError):
            return 0
    ordered = sorted(items, key=schedule)
    pages = max(1, (len(ordered) + page_size - 1) // page_size)
    page = max(0, min(int(page), pages - 1))
    start = page * page_size
    return ordered[start:start + page_size], page, pages, start
