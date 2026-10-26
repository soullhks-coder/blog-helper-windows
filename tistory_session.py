"""Local Tistory cookie recovery without replacing a newer browser session."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
import time


def _cookie_key(cookie):
    return (str(cookie.get("domain") or "").lower().lstrip("."),
            str(cookie.get("path") or "/"), str(cookie.get("name") or ""))


def _valid(cookie, now):
    if not isinstance(cookie, dict):
        return False
    if not all(isinstance(cookie.get(key), str) for key in ("name", "value", "domain", "path")):
        return False
    if not cookie["name"] or not cookie["domain"]:
        return False
    try:
        expires = float(cookie.get("expires", -1))
    except (TypeError, ValueError):
        return False
    return math.isfinite(expires) and (expires < 0 or expires > now)


def missing_cookies(saved, current, *, now=None):
    """Only recover valid missing cookies; live browser values always win."""
    now = time.time() if now is None else now
    current_keys = {_cookie_key(cookie) for cookie in current
                    if _valid(cookie, now)}
    result = []
    for cookie in saved:
        if not _valid(cookie, now):
            continue
        key = _cookie_key(cookie)
        if key in current_keys:
            continue
        current_keys.add(key)
        result.append(dict(cookie))
    return result


def _read(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("cookies"), list):
            return payload
    except (OSError, ValueError, TypeError):
        pass
    return None


def load_snapshot(path):
    """Return a complete snapshot, falling back only on missing/broken JSON."""
    path = Path(path)
    payload = _read(path)
    if payload is not None:
        return payload, False
    backup = _read(path.with_name(path.name + ".backup"))
    return (backup, True) if backup is not None else ({"cookies": [], "origins": []}, False)


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def save_snapshot(path, state, *, authenticated, is_auth_cookie):
    """Save only an editor-verified session, preserving its previous good copy."""
    if not authenticated:
        return False
    cookies = state.get("cookies", [])
    if not isinstance(cookies, list) or not any(
            _valid(cookie, time.time()) and cookie.get("value") and is_auth_cookie(cookie)
            for cookie in cookies):
        return False
    path = Path(path)
    data = json.dumps(state, ensure_ascii=False, indent=2)
    previous = _read(path)
    if previous == state:
        return False  # Success and finally paths commonly see identical state.
    if previous is not None and any(
            isinstance(cookie, dict) and cookie.get("value") and is_auth_cookie(cookie)
            for cookie in previous["cookies"]):
        _atomic_write(path.with_name(path.name + ".backup"),
                      json.dumps(previous, ensure_ascii=False, indent=2))
    _atomic_write(path, data)
    return True
