from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import certifi


DEFAULT_APP_VERSION = "1.0.0"
DEFAULT_UPDATE_REPOSITORY = "soullhks-coder/blog-helper-releases"
SCRIPT_DIR = Path(__file__).resolve().parent


def load_release_config() -> tuple[str, str]:
    version_file = SCRIPT_DIR / "version.json"
    try:
        payload = json.loads(version_file.read_text(encoding="utf-8"))
        version = str(payload.get("version") or DEFAULT_APP_VERSION).strip()
        repository = str(payload.get("update_repository") or DEFAULT_UPDATE_REPOSITORY).strip()
        return version, repository
    except (OSError, ValueError, TypeError):
        return DEFAULT_APP_VERSION, DEFAULT_UPDATE_REPOSITORY


APP_VERSION, UPDATE_REPOSITORY = load_release_config()


def version_key(value: str) -> tuple[int, ...]:
    cleaned = str(value or "").strip().lower().lstrip("v")
    core = cleaned.split("-", 1)[0]
    numbers: list[int] = []
    for part in core.split("."):
        digits = "".join(character for character in part if character.isdigit())
        numbers.append(int(digits or 0))
    return tuple((numbers + [0, 0, 0])[:3])


def is_newer_version(latest: str, current: str) -> bool:
    return version_key(latest) > version_key(current)


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def platform_asset_name() -> str:
    if os.name == "nt":
        return "BlogHelper.exe"
    if sys.platform == "darwin":
        return "BlogHelper-macOS.zip"
    return ""


class UpdateCheckWorker(threading.Thread):
    def __init__(
        self,
        result_queue,
        repository: str = UPDATE_REPOSITORY,
        current_version: str = APP_VERSION,
        api_url: str = "",
    ) -> None:
        super().__init__(daemon=True)
        self.result_queue = result_queue
        self.repository = repository
        self.current_version = current_version
        self.api_url = api_url or f"https://api.github.com/repos/{repository}/releases/latest"
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def run(self) -> None:
        try:
            request = Request(
                self.api_url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": f"BlogHelper/{self.current_version}",
                },
            )
            with urlopen(request, timeout=18, context=self.ssl_context) as response:
                release = json.loads(response.read().decode("utf-8"))

            latest_version = str(release.get("tag_name") or "").strip().lstrip("v")
            if not latest_version or not is_newer_version(latest_version, self.current_version):
                self.result_queue.put(("update_none", latest_version or self.current_version))
                return

            expected_name = platform_asset_name()
            asset = next(
                (
                    candidate
                    for candidate in release.get("assets", [])
                    if str(candidate.get("name") or "") == expected_name
                ),
                None,
            )
            if not asset:
                raise RuntimeError(f"{expected_name or '현재 운영체제'}용 업데이트 파일을 찾지 못했습니다.")

            self.result_queue.put(
                (
                    "update_available",
                    {
                        "version": latest_version,
                        "release_name": str(release.get("name") or f"v{latest_version}"),
                        "asset_name": expected_name,
                        "download_url": str(asset.get("browser_download_url") or ""),
                        "size": int(asset.get("size") or 0),
                        "digest": str(asset.get("digest") or ""),
                    },
                )
            )
        except Exception as exc:  # pragma: no cover - network/runtime handling
            self.result_queue.put(("update_check_error", str(exc)))


class UpdateDownloadWorker(threading.Thread):
    def __init__(self, result_queue, payload: dict, download_dir: Path) -> None:
        super().__init__(daemon=True)
        self.result_queue = result_queue
        self.payload = dict(payload)
        self.download_dir = Path(download_dir)
        self.ssl_context = ssl.create_default_context(cafile=certifi.where())

    def run(self) -> None:
        temp_path: Path | None = None
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
            asset_name = Path(str(self.payload["asset_name"])).name
            final_path = self.download_dir / f"v{self.payload['version']}-{asset_name}"
            temp_path = final_path.with_suffix(final_path.suffix + ".download")
            request = Request(
                str(self.payload["download_url"]),
                headers={"User-Agent": f"BlogHelper/{APP_VERSION}"},
            )
            downloaded = 0
            sha256 = hashlib.sha256()
            with urlopen(request, timeout=60, context=self.ssl_context) as response, temp_path.open("wb") as output:
                total = int(response.headers.get("Content-Length") or self.payload.get("size") or 0)
                while True:
                    chunk = response.read(1024 * 512)
                    if not chunk:
                        break
                    output.write(chunk)
                    sha256.update(chunk)
                    downloaded += len(chunk)
                    self.result_queue.put(
                        (
                            "update_progress",
                            {"downloaded": downloaded, "total": total, "version": self.payload["version"]},
                        )
                    )

            expected_digest = str(self.payload.get("digest") or "").strip().lower()
            actual_digest = sha256.hexdigest().lower()
            if expected_digest.startswith("sha256:") and actual_digest != expected_digest.split(":", 1)[1]:
                raise RuntimeError("다운로드 파일 검증에 실패했습니다. 안전을 위해 업데이트를 중단했습니다.")
            if not downloaded:
                raise RuntimeError("업데이트 파일이 비어 있습니다.")

            temp_path.replace(final_path)
            result = dict(self.payload)
            result.update({"local_path": str(final_path), "downloaded": downloaded, "sha256": actual_digest})
            self.result_queue.put(("update_downloaded", result))
        except Exception as exc:  # pragma: no cover - network/runtime handling
            if temp_path:
                temp_path.unlink(missing_ok=True)
            self.result_queue.put(("update_download_error", str(exc)))


def find_macos_app_root(executable: Path | None = None) -> Path | None:
    current = Path(executable or sys.executable).resolve()
    for parent in (current, *current.parents):
        if parent.suffix.lower() == ".app":
            return parent
    return None


def _write_windows_update_script(path: Path) -> None:
    path.write_text(
        r'''param(
    [int]$ParentProcessId,
    [string]$Source,
    [string]$Target,
    [string]$LogFile
)
$ErrorActionPreference = "Stop"
function Write-UpdateLog([string]$Message) {
    Add-Content -Path $LogFile -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message) -Encoding UTF8
}
try {
    Write-UpdateLog "기존 프로그램 종료 대기"
    for ($i = 0; $i -lt 120; $i++) {
        if (-not (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    $Backup = "$Target.update-backup"
    Remove-Item -LiteralPath $Backup -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $Target) { Copy-Item -LiteralPath $Target -Destination $Backup -Force }
    Copy-Item -LiteralPath $Source -Destination $Target -Force
    Write-UpdateLog "새 버전 설치 완료"
    Start-Process -FilePath $Target
    Start-Sleep -Seconds 2
    Remove-Item -LiteralPath $Source -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Backup -Force -ErrorAction SilentlyContinue
} catch {
    Write-UpdateLog ("업데이트 실패: " + $_.Exception.Message)
    if ($Backup -and (Test-Path -LiteralPath $Backup)) {
        Copy-Item -LiteralPath $Backup -Destination $Target -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $Target) { Start-Process -FilePath $Target }
}
''',
        encoding="utf-8-sig",
    )


def _write_macos_update_script(path: Path) -> None:
    path.write_text(
        r'''#!/bin/zsh
set -u
PARENT_PID="$1"
ARCHIVE="$2"
TARGET_APP="$3"
LOG_FILE="$4"
log_update() { print -r -- "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"; }

log_update "기존 프로그램 종료 대기"
for _ in {1..120}; do
    if ! /bin/kill -0 "$PARENT_PID" 2>/dev/null; then break; fi
    /bin/sleep 0.5
done

STAGE_DIR="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/blog-helper-update.XXXXXX")"
BACKUP_APP="${TARGET_APP}.update-backup"
if /usr/bin/ditto -x -k "$ARCHIVE" "$STAGE_DIR"; then
    NEW_APP="$(/usr/bin/find "$STAGE_DIR" -maxdepth 3 -type d -name 'BlogHelper.app' -print -quit)"
else
    NEW_APP=""
fi

if [[ -n "$NEW_APP" && -d "$NEW_APP" ]]; then
    /bin/rm -rf "$BACKUP_APP"
    if /bin/mv "$TARGET_APP" "$BACKUP_APP" && /bin/mv "$NEW_APP" "$TARGET_APP"; then
        /usr/bin/xattr -dr com.apple.quarantine "$TARGET_APP" 2>/dev/null || true
        log_update "새 버전 설치 완료"
        /usr/bin/open -n "$TARGET_APP"
        /bin/sleep 2
        /bin/rm -rf "$BACKUP_APP" "$STAGE_DIR" "$ARCHIVE"
        exit 0
    fi
fi

log_update "업데이트 설치 실패, 기존 버전 복구"
if [[ -d "$BACKUP_APP" ]]; then
    /bin/rm -rf "$TARGET_APP"
    /bin/mv "$BACKUP_APP" "$TARGET_APP"
fi
[[ -d "$TARGET_APP" ]] && /usr/bin/open -n "$TARGET_APP"
/bin/rm -rf "$STAGE_DIR"
exit 1
''',
        encoding="utf-8",
    )
    path.chmod(0o700)


def launch_update_installer(downloaded_file: Path, data_dir: Path) -> None:
    downloaded_file = Path(downloaded_file).resolve()
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "update-install.log"

    if os.name == "nt":
        target = Path(sys.executable).resolve()
        script_path = data_dir / "apply-update.ps1"
        _write_windows_update_script(script_path)
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script_path),
            "-ParentProcessId",
            str(os.getpid()),
            "-Source",
            str(downloaded_file),
            "-Target",
            str(target),
            "-LogFile",
            str(log_path),
        ]
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(command, close_fds=True, creationflags=creation_flags)
        return

    if sys.platform == "darwin":
        target_app = find_macos_app_root()
        if not target_app:
            raise RuntimeError("현재 실행 중인 BlogHelper.app 위치를 찾지 못했습니다.")
        script_path = data_dir / "apply-update.zsh"
        _write_macos_update_script(script_path)
        subprocess.Popen(
            [str(script_path), str(os.getpid()), str(downloaded_file), str(target_app), str(log_path)],
            close_fds=True,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return

    raise RuntimeError("현재 운영체제는 자동 업데이트를 지원하지 않습니다.")


def cleanup_old_downloads(download_dir: Path, keep: Path | None = None) -> None:
    directory = Path(download_dir)
    if not directory.exists():
        return
    keep_resolved = keep.resolve() if keep else None
    for candidate in directory.iterdir():
        try:
            if keep_resolved and candidate.resolve() == keep_resolved:
                continue
            if candidate.is_file() and candidate.name.startswith("v"):
                candidate.unlink(missing_ok=True)
        except OSError:
            pass
