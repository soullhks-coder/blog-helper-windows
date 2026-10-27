"""Small locked JSON transactions with atomic writes and last-good recovery."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time


class SettingsReadError(OSError):
    pass


_locks: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()


@contextmanager
def _thread_lock(lock, timeout):
    if not lock.acquire(timeout=timeout):
        raise SettingsReadError("다른 설정 저장이 진행 중입니다. 잠시 후 다시 시도해 주세요.")
    try:
        yield
    finally:
        lock.release()


@contextmanager
def file_lock(path: Path, timeout: float = 5):
    path = Path(path)
    key = str(path.resolve())
    with _registry_lock:
        lock = _locks.setdefault(key, threading.RLock())
    deadline = time.monotonic() + timeout
    with _thread_lock(lock, timeout):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(path.suffix + ".lock").open("a+b") as handle:
            if os.name == "nt":
                import msvcrt
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
            else:
                import fcntl
            while True:
                try:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise SettingsReadError("다른 설정 저장이 진행 중입니다. 잠시 후 다시 시도해 주세요.") from None
                    time.sleep(.025)
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write(path: Path, content: str | bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content.encode("utf-8") if isinstance(content, str) else content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SettingsFile:
    DAILY_HISTORY_LIMIT = 90
    REVISION_HISTORY_LIMIT = 100

    def __init__(self, path):
        self.path = Path(path)
        self.backup = self.path.with_suffix(self.path.suffix + ".bak")
        self.history_dir = self.path.parent / 'Settings Backups' / f'{self.path.stem}-history'

    def history_paths(self):
        # Only our own immutable snapshots, never user-created recovery folders.
        return sorted([*self.history_dir.glob('*-revision-*.json'),
                       *self.history_dir.glob('*-daily.json')], reverse=True)

    def _checkpoint(self, payload, *, revision):
        if not payload or not critical_settings(payload):
            return
        self.history_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = datetime.now(timezone.utc)
        daily = self.history_dir / f'{now:%Y%m%d}T000000.000000Z-daily.json'
        if daily.exists() and not revision:
            return
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        if not daily.exists():
            atomic_write(daily, content)
        if revision:
            digest = hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]
            revisions = sorted(self.history_dir.glob('*-revision-*.json'), reverse=True)
            if not revisions or not revisions[0].name.endswith(f'-{digest}.json'):
                atomic_write(self.history_dir / f'{now:%Y%m%dT%H%M%S.%fZ}-revision-{digest}.json', content)
        for pattern, keep in (('*-daily.json', self.DAILY_HISTORY_LIMIT),
                              ('*-revision-*.json', self.REVISION_HISTORY_LIMIT)):
            for old in sorted(self.history_dir.glob(pattern), reverse=True)[keep:]:
                old.unlink()

    @staticmethod
    def _decode(path):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("설정은 JSON 객체여야 합니다.")
        return value

    def _read(self):
        for path in (self.path, self.backup):
            if not path.exists():
                continue
            try:
                return self._decode(path), path != self.path
            except (OSError, ValueError):
                continue
        history = self.history_paths()
        for path in history:
            try:
                payload = self._decode(path)
                if not critical_settings(payload):
                    continue
                if '-revision-' in path.name:
                    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')).hexdigest()[:16]
                    if not path.name.endswith(f'-{digest}.json'):
                        continue
                return payload, True
            except (OSError, ValueError):
                continue
        if self.path.exists() or self.backup.exists() or history:
            raise SettingsReadError("설정 파일과 백업을 읽지 못했습니다. 기존 파일을 보호하기 위해 덮어쓰지 않습니다.")
        return {}, False

    def read(self):
        with file_lock(self.path):
            payload, _recovered = self._read()
            return payload

    def update(self, transform, *, on_commit=None):
        with file_lock(self.path):
            previous, recovered = self._read()
            payload = transform(copy.deepcopy(previous))
            if not isinstance(payload, dict):
                raise ValueError("설정 저장 결과는 JSON 객체여야 합니다.")
            critical_changed = critical_settings(previous) != critical_settings(payload)
            # A failed safety backup blocks the destructive write. The first
            # copy of each day survives normal autosaves and .bak rotation.
            self._checkpoint(previous, revision=critical_changed)
            if payload != previous or recovered:
                if recovered and self.path.exists():
                    atomic_write(self.path.with_suffix(self.path.suffix + ".corrupt"),
                                 self.path.read_bytes())
                if self.path.exists() and not recovered:
                    atomic_write(self.backup, json.dumps(previous, ensure_ascii=False, indent=2))
                atomic_write(self.path, json.dumps(payload, ensure_ascii=False, indent=2))
            self._checkpoint(payload, revision=critical_changed)
            if on_commit is not None:
                on_commit(payload, previous)
            return payload, previous


PROFILE_FIELDS = ("tistory_profiles", "blogspot_profiles", "naver_blog_profiles", "naver_kin_profiles")
PROFILE_IDENTITIES = ("blog_url", "write_url", "kakao_account", "blog_id", "blog_name", "nickname")


def critical_settings(payload):
    """Fingerprint reusable user data, excluding per-article preview changes."""
    keys = (*PROFILE_FIELDS, 'blog_url', 'username', 'writing_link_sets', 'prompt_sets')
    result = {key: copy.deepcopy(payload[key]) for key in keys if key in payload}
    if 'thumbnail_presets' in payload:
        result['thumbnail_presets'] = [
            {k: copy.deepcopy(v) for k, v in row.items() if k not in ('text', 'saved_path')}
            for row in _profiles(payload['thumbnail_presets'])]
    if isinstance(payload.get('blog_writing_preferences'), dict):
        result['blog_writing_preferences'] = {
            key: {k: copy.deepcopy(v) for k, v in row.items() if k.startswith('writing_link')}
            for key, row in payload['blog_writing_preferences'].items() if isinstance(row, dict)}
    return result


def _profiles(value):
    return [p for p in value if isinstance(p, dict)] if isinstance(value, list) else []


def _profile_key(profile):
    return profile.get("profile_scope") or profile.get("name")


def _merge_changed(previous, incoming, baseline):
    """Apply only edits made since this settings object's snapshot was loaded."""
    if incoming == baseline:
        return copy.deepcopy(previous)
    if all(isinstance(item, dict) for item in (previous, incoming, baseline)):
        merged = copy.deepcopy(previous)
        for key, value in incoming.items():
            if key in baseline and key in previous:
                merged[key] = _merge_changed(previous[key], value, baseline[key])
            else:
                merged[key] = copy.deepcopy(value)
        for key in baseline.keys() - incoming.keys():
            merged.pop(key, None)
        return merged
    return copy.deepcopy(incoming)


def _merge_named_library(previous, incoming, *, explicit=False):
    """A whole-settings save cannot delete reusable named sets."""
    result = copy.deepcopy(_profiles(previous))
    positions = {p.get('name'): i for i, p in enumerate(result)}
    for row in _profiles(incoming):
        name = row.get('name')
        if not name:
            continue
        if name not in positions:
            positions[name] = len(result)
            result.append(copy.deepcopy(row))
        elif explicit:
            result[positions[name]] = copy.deepcopy(row)
        # Existing entries are edited only by the explicit named transaction,
        # not a full application settings object (even one with a baseline).
    return result


def _merge_design_slots(previous, incoming, baseline=None):
    result = copy.deepcopy(previous) if isinstance(previous, list) else []
    before = baseline if isinstance(baseline, list) else []
    for index, row in enumerate(incoming if isinstance(incoming, list) else []):
        if not isinstance(row, dict):
            continue
        if index >= len(result):
            result.extend({} for _ in range(index - len(result)))
            result.append(copy.deepcopy(row))
        elif index < len(before) and isinstance(before[index], dict):
            result[index] = _merge_changed(result[index], row, before[index])
    return result


def merge_settings(previous, incoming, *, baseline=None, preserve_empty=(), autosave=False,
                   protect_profiles=True, explicit_named_updates=False):
    result = copy.deepcopy(previous)
    for key, value in incoming.items():
        if key == 'writing_link_sets':
            result[key] = _merge_named_library(previous.get(key), value,
                explicit=explicit_named_updates)
        elif key == 'thumbnail_presets':
            result[key] = _merge_design_slots(previous.get(key), value,
                baseline.get(key) if baseline else None)
        elif baseline is not None and key in baseline and key in previous:
            if key in PROFILE_FIELDS and all(isinstance(item, list) for item in (value, baseline[key], previous[key])):
                # Service slots have stable profile_scope identifiers. Merge
                # per slot so a concurrent connection to another slot survives.
                before = {_profile_key(p): p for p in _profiles(baseline[key])}
                current = {_profile_key(p): p for p in _profiles(previous[key])}
                if value == baseline[key]:
                    result[key] = copy.deepcopy(previous[key])
                else:
                    result[key] = [_merge_changed(current.get(_profile_key(p), {}), p,
                        before.get(_profile_key(p), {})) for p in _profiles(value)]
            else:
                result[key] = _merge_changed(previous[key], value, baseline[key])
        else:
            result[key] = copy.deepcopy(value)
        if autosave and key in preserve_empty and not value and previous.get(key):
            result[key] = copy.deepcopy(previous[key])
    if (autosave or baseline is None) and protect_profiles:
        for key in PROFILE_FIELDS:
            old = {_profile_key(p): p for p in _profiles(previous.get(key))}
            for profile in _profiles(result.get(key)):
                saved = old.get(_profile_key(profile), {})
                for name in PROFILE_IDENTITIES:
                    if not profile.get(name) and saved.get(name):
                        profile[name] = saved[name]
    return result
