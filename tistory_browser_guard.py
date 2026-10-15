"""Reserve a Tistory Chrome profile across the app's independent workers.

Call ``acquire`` with the resolved profile directory before starting Chrome.
Register the returned callback on the context's ``close`` event and call it
again if browser launch or setup fails.  The callback is safe to repeat.
"""
from __future__ import annotations

import threading
from collections.abc import Callable


class TistoryProfileInUse(RuntimeError):
    """The requested Chrome profile is already owned by another app task."""


_guard = threading.Lock()
_owners: dict[str, object] = {}


def acquire(profile_key: object) -> Callable[..., None]:
    """Reserve one canonical profile key, returning its idempotent release.

    A resolved directory is preferable to a user-supplied profile name: legacy
    profile aliases can point at the same directory.  The caller already has
    that path when launching a persistent browser context.
    """
    key = str(profile_key or "").strip()
    if not key:
        raise ValueError("티스토리 브라우저 프로필을 확인할 수 없습니다.")
    owner = object()
    with _guard:
        if key in _owners:
            raise TistoryProfileInUse(
                "이 티스토리 계정의 전용 Chrome을 다른 작업에서 사용하고 있습니다. "
                "기존 티스토리 작업이나 V2 검토 창을 종료한 뒤 다시 시도해 주세요."
            )
        _owners[key] = owner

    def release(*_event_args: object) -> None:
        # Event handlers can fire after the explicit failure-cleanup path, or
        # after another worker has acquired the same profile. Only this exact
        # reservation is eligible for release.
        with _guard:
            if _owners.get(key) is owner:
                del _owners[key]

    return release
