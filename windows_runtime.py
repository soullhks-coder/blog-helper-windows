"""Keep the large Playwright driver compressed until Windows automation needs it.

The normal one-file bootloader need only copy one compressed archive, rather
than extract Node and hundreds of driver files on every application launch.
The verified driver cache is shared across app updates with the same driver.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import threading
import time
import zipfile


ARCHIVE_NAME = "playwright-driver.zip"
METADATA_NAME = "playwright-driver.json"
_DRIVER_LOCK = threading.Lock()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_driver_archive(driver_dir: Path, destination: Path) -> tuple[Path, Path]:
    """Called by the Windows spec; preserve all driver files and licenses."""
    driver_dir = Path(driver_dir)
    for required in ("node.exe", "package/cli.js"):
        if not (driver_dir / required).is_file():
            raise RuntimeError(f"Windows Playwright driver is missing {required}")
    destination.mkdir(parents=True, exist_ok=True)
    archive_path = destination / ARCHIVE_NAME
    unpacked_bytes = 0
    file_count = 0
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source in sorted(driver_dir.rglob("*")):
            if source.is_file():
                # pip installation timestamps vary on every build. Stable ZIP
                # metadata lets unchanged drivers reuse the same update cache.
                item = zipfile.ZipInfo(source.relative_to(driver_dir).as_posix(), (2020, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.external_attr = (stat.S_IFREG | 0o644) << 16
                with source.open("rb") as incoming, archive.open(item, "w") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                unpacked_bytes += source.stat().st_size
                file_count += 1
    metadata = {
        "format": 1,
        "sha256": _sha256_file(archive_path),
        "node_size": (driver_dir / "node.exe").stat().st_size,
        "cli_size": (driver_dir / "package/cli.js").stat().st_size,
        "unpacked_bytes": unpacked_bytes,
        "archive_bytes": archive_path.stat().st_size,
        "file_count": file_count,
    }
    metadata_path = destination / METADATA_NAME
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    print(f"Windows startup driver: {unpacked_bytes:,} bytes / {file_count} files -> "
          f"{archive_path.stat().st_size:,} compressed bytes; unpack only on first automation")
    return archive_path, metadata_path


def _cache_ready(directory: Path, metadata: dict) -> bool:
    try:
        marker = json.loads((directory / "ready.json").read_text(encoding="utf-8"))
        return (
            marker.get("sha256") == metadata["sha256"]
            and (directory / "node.exe").stat().st_size == metadata["node_size"]
            and (directory / "package/cli.js").stat().st_size == metadata["cli_size"]
        )
    except (OSError, ValueError, KeyError):
        return False


def prepare_driver_cache(bundle_dir: Path, cache_root: Path) -> Path:
    metadata = json.loads((bundle_dir / METADATA_NAME).read_text(encoding="utf-8"))
    digest = str(metadata.get("sha256") or "")
    if metadata.get("format") != 1 or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise RuntimeError("브라우저 자동화 모듈 정보가 올바르지 않습니다.")
    target = cache_root / digest
    with _DRIVER_LOCK:
        if _cache_ready(target, metadata):
            return target
        archive_path = bundle_dir / ARCHIVE_NAME
        if _sha256_file(archive_path) != digest:
            raise RuntimeError("브라우저 자동화 모듈 검증에 실패했습니다. 프로그램을 다시 업데이트해 주세요.")
        cache_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="preparing-", dir=cache_root))
        try:
            with zipfile.ZipFile(archive_path) as archive:
                for item in archive.infolist():
                    relative = PurePosixPath(item.filename)
                    if (
                        relative.is_absolute() or ".." in relative.parts
                        or "\\" in item.filename or ":" in item.filename
                        or stat.S_ISLNK(item.external_attr >> 16)
                    ):
                        raise RuntimeError("브라우저 자동화 모듈에 안전하지 않은 파일 경로가 있습니다.")
                archive.extractall(staging)
            (staging / "ready.json").write_text(json.dumps(metadata), encoding="utf-8")
            if not _cache_ready(staging, metadata):
                raise RuntimeError("브라우저 자동화 모듈을 준비하지 못했습니다.")
            # Another separately installed copy can finish preparing the same
            # cache while this process extracts. Never overwrite a valid cache.
            if _cache_ready(target, metadata):
                return target
            if target.exists():
                # Preserve an incomplete/corrupted old cache for diagnosis.
                backup = cache_root / (digest + "-incomplete-" + staging.name)
                try:
                    target.rename(backup)
                except OSError:
                    if _cache_ready(target, metadata):
                        return target
                    raise
            try:
                staging.rename(target)
            except OSError:
                if not _cache_ready(target, metadata):
                    raise
            return target
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def install_windows_playwright_runtime(bundle_dir: Path, cache_root: Path) -> bool:
    """Redirect the pinned Playwright transport; no extraction until connect."""
    if not (bundle_dir / METADATA_NAME).is_file():
        return False  # Source runs and the macOS bundle use the standard driver.
    from playwright._impl import _transport

    def compute_cached_driver_executable() -> tuple[str, str]:
        directory = prepare_driver_cache(bundle_dir, cache_root)
        return str(directory / "node.exe"), str(directory / "package/cli.js")

    # Playwright 1.60 imports the resolver into _transport. Keeping the change
    # here covers sync/async APIs and every worker without changing browser code.
    _transport.compute_driver_executable = compute_cached_driver_executable
    return True


def start_frozen_startup_probe(app, report_path: Path, app_started: float) -> None:
    """Release-only offline smoke: full UI, first Chrome launch, cache reuse."""
    import sys

    bundle_dir = Path(sys._MEIPASS)
    cache_root = Path(os.environ["BLOG_HELPER_DATA_DIR"]) / "runtime" / "playwright"
    report = {
        "ui_seconds": time.perf_counter() - app_started,
        "driver_extracted_at_startup": (bundle_dir / "playwright/driver/node.exe").exists(),
        "cache_exists_at_ui_ready": cache_root.exists(),
    }
    ui_marker = report_path.with_suffix(".ui-ready")
    app.update_idletasks()
    ui_marker.write_text(json.dumps(report), encoding="utf-8")
    completed = threading.Event()

    def check_driver():
        try:
            from playwright.sync_api import sync_playwright

            cache_directory = None
            for label in ("first_automation", "cached_automation"):
                started = time.perf_counter()
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(channel="chrome", headless=True)
                    page = browser.new_page()
                    page.route("**/*", lambda route: route.abort())
                    page.set_content("<html><body>BlogHelper offline startup test</body></html>")
                    assert page.locator("body").inner_text() == "BlogHelper offline startup test"
                    browser.close()
                report[label + "_seconds"] = time.perf_counter() - started
                prepared = list(cache_root.glob("*/ready.json"))
                assert len(prepared) == 1, "Driver cache was not prepared exactly once"
                if cache_directory is None:
                    cache_directory = prepared[0]
                    ready_mtime = cache_directory.stat().st_mtime_ns
                else:
                    assert cache_directory == prepared[0]
                    assert cache_directory.stat().st_mtime_ns == ready_mtime, "Hot cache was extracted again"
            report["metadata"] = json.loads((bundle_dir / METADATA_NAME).read_text(encoding="utf-8"))
            report["passed"] = True
        except Exception as exc:
            report["error"] = repr(exc)
            report["passed"] = False
        finally:
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            completed.set()

    def poll_probe():
        if completed.is_set():
            app.destroy()
        else:
            app.after(100, poll_probe)

    threading.Thread(target=check_driver, daemon=True).start()
    app.after(100, poll_probe)
