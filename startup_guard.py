from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from pathlib import Path


ERROR_ALREADY_EXISTS = 183
_WINDOWS_MESSAGE_FLAGS = 0x00000040 | 0x00010000 | 0x00040000


def windows_single_instance_mutex_name(executable_path: str | os.PathLike[str]) -> str:
    """Return a stable, per-executable mutex name for the packaged Windows app."""
    normalized_path = os.path.normcase(str(Path(executable_path).resolve()))
    path_digest = hashlib.sha256(
        normalized_path.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:20]
    return rf"Local\BlogHelper-{path_digest}"


def acquire_windows_single_instance(
    *,
    os_name: str | None = None,
    frozen: bool | None = None,
    executable_path: str | os.PathLike[str] | None = None,
    kernel32=None,
    user32=None,
) -> int | None:
    """Acquire the packaged app mutex or immediately notify a duplicate launch."""
    current_os_name = os.name if os_name is None else os_name
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    if current_os_name != "nt" or not is_frozen:
        return None

    try:
        if kernel32 is None:
            kernel32 = ctypes.windll.kernel32
        if user32 is None:
            user32 = ctypes.windll.user32

        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        create_mutex.restype = ctypes.c_void_p
        get_last_error = kernel32.GetLastError
        get_last_error.argtypes = []
        get_last_error.restype = ctypes.c_ulong
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_bool
        message_box = user32.MessageBoxW
        message_box.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint,
        ]
        message_box.restype = ctypes.c_int
        handle = create_mutex(
            None,
            False,
            windows_single_instance_mutex_name(executable_path or sys.executable),
        )
        if not handle:
            return None

        if int(get_last_error()) != ERROR_ALREADY_EXISTS:
            return int(handle)

        try:
            close_handle(handle)
        finally:
            message_box(
                None,
                "Blog Helper가 이미 실행 중입니다.\n\n작업 표시줄에서 열린 프로그램을 확인해 주세요.",
                "이미 프로그램이 실행 중입니다.",
                _WINDOWS_MESSAGE_FLAGS,
            )
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        # A Windows API failure must not prevent the primary app from opening.
        return None


def release_windows_single_instance(handle: int | None, *, kernel32=None) -> None:
    if not handle:
        return
    try:
        if kernel32 is None:
            kernel32 = ctypes.windll.kernel32
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_bool
        close_handle(handle)
    except Exception:
        pass
