"""Cancellation and event fencing for one foreground writing run.

Queues from other menus remain untouched. Cancelled runs never deliver late
progress/done/error events to a reset or newly started writing screen.
"""
from __future__ import annotations

import threading


class WritingCancelled(RuntimeError):
    """The user stopped this writing run; not a publication failure."""


def check_writing_cancelled(cancel_event=None):
    if cancel_event is not None and cancel_event.is_set():
        raise WritingCancelled("발행을 중단했습니다.")


class ScopedWritingQueue:
    def __init__(self, target):
        self.target = target
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def put(self, event, block=True, timeout=None):
        if not self.cancel_event.is_set():
            self.target.put(("writing_run_event", (self, event)), block=block, timeout=timeout)

    def put_nowait(self, event):
        self.put(event, block=False)


def active_writing_event(event, active_queue):
    """Return raw/unrelated events as-is, or the current run's inner event."""
    kind, payload = event
    if kind != "writing_run_event":
        return event
    sender, inner = payload
    if sender is active_queue and not sender.cancel_event.is_set():
        return inner
    return None
