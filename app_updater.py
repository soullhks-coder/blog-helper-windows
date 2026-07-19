from __future__ import annotations

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

import certifi

try:
    import bsdiff4
except ImportError:  # pragma: no cover - full update fallback
    bsdiff4 = None


DEFAULT_APP_VERSION = "1.0.3"
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


def delta_asset_name(current_version: str, latest_version: str) -> str:
    current = str(current_version or "").strip().lstrip("v")
    latest = str(latest_version or "").strip().lstrip("v")
    if os.name == "nt":
        platform_name = "Windows"
    elif sys.platform == "darwin":
        platform_name = "macOS"
    else:
        return ""
    return f"BlogHelper-{platform_name}-v{current}-to-v{latest}.patch.zip"


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
            full_asset = next(
                (
                    candidate
                    for candidate in release.get("assets", [])
                    if str(candidate.get("name") or "") == expected_name
                ),
                None,
            )
            if not full_asset:
                raise RuntimeError(f"{expected_name or '현재 운영체제'}용 업데이트 파일을 찾지 못했습니다.")

            patch_name = delta_asset_name(self.current_version, latest_version)
            patch_asset = next(
                (
                    candidate
                    for candidate in release.get("assets", [])
                    if str(candidate.get("name") or "") == patch_name
                ),
                None,
            )
            use_delta = bool(patch_asset and bsdiff4 is not None)
            asset = patch_asset if use_delta else full_asset

            def asset_payload(candidate: dict, update_kind: str) -> dict:
                return {
                    "version": latest_version,
                    "release_name": str(release.get("name") or f"v{latest_version}"),
                    "asset_name": str(candidate.get("name") or ""),
                    "download_url": str(candidate.get("browser_download_url") or ""),
                    "size": int(candidate.get("size") or 0),
                    "digest": str(candidate.get("digest") or ""),
                    "update_kind": update_kind,
                }

            selected_payload = asset_payload(asset, "delta" if use_delta else "full")
            if use_delta:
                selected_payload["full_asset"] = asset_payload(full_asset, "full")

            self.result_queue.put(
                (
                    "update_available",
                    selected_payload,
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
                            {
                                "downloaded": downloaded,
                                "total": total,
                                "version": self.payload["version"],
                                "update_kind": self.payload.get("update_kind", "full"),
                            },
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract_patch(archive_path: Path, destination: Path) -> None:
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            candidate = (destination / member.filename).resolve()
            if candidate != destination and destination not in candidate.parents:
                raise RuntimeError("업데이트 패치에 안전하지 않은 경로가 포함되어 있습니다.")
        archive.extractall(destination)


def _safe_child(base: Path, relative: str) -> Path:
    root = Path(base).resolve()
    candidate = (root / relative).resolve()
    if candidate == root or root not in candidate.parents:
        raise RuntimeError("업데이트 패치 경로 검증에 실패했습니다.")
    return candidate


def _verify_patch_source(path: Path, operation: dict) -> None:
    expected = str(operation.get("source_sha256") or "").lower()
    if not path.exists() or not expected or _sha256_file(path) != expected:
        raise RuntimeError("현재 프로그램 파일이 패치 기준 버전과 일치하지 않습니다.")


def _verify_patch_target(path: Path, operation: dict) -> None:
    expected = str(operation.get("target_sha256") or "").lower()
    if not path.exists() or not expected or _sha256_file(path) != expected:
        raise RuntimeError("빠른 업데이트 결과 파일 검증에 실패했습니다.")


def prepare_delta_update(
    patch_archive: Path,
    data_dir: Path,
    current_target: Path | None = None,
) -> Path:
    if bsdiff4 is None:
        raise RuntimeError("빠른 업데이트 구성요소를 사용할 수 없습니다.")

    patch_archive = Path(patch_archive).resolve()
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="delta-", dir=data_dir))
    try:
        _safe_extract_patch(patch_archive, work_dir)
        manifest_path = work_dir / "patch-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        from_version = str(manifest.get("from_version") or "").lstrip("v")
        to_version = str(manifest.get("to_version") or "").lstrip("v")
        if from_version != APP_VERSION or not is_newer_version(to_version, APP_VERSION):
            raise RuntimeError("이 빠른 업데이트는 현재 설치 버전에 적용할 수 없습니다.")

        operation = dict(manifest.get("binary_patch") or {})
        patch_file = _safe_child(work_dir, str(operation.get("patch_file") or ""))
        if not patch_file.is_file():
            raise RuntimeError("빠른 업데이트 패치 파일이 없습니다.")

        if os.name == "nt":
            if manifest.get("platform") != "windows":
                raise RuntimeError("Windows용 빠른 업데이트가 아닙니다.")
            current_executable = Path(current_target or sys.executable).resolve()
            _verify_patch_source(current_executable, operation)
            staged_executable = data_dir / f"BlogHelper-v{to_version}.exe"
            staged_executable.unlink(missing_ok=True)
            bsdiff4.file_patch(str(current_executable), str(staged_executable), str(patch_file))
            _verify_patch_target(staged_executable, operation)
            return staged_executable

        if sys.platform == "darwin":
            if manifest.get("platform") != "macos":
                raise RuntimeError("macOS용 빠른 업데이트가 아닙니다.")
            current_app = Path(current_target).resolve() if current_target else find_macos_app_root()
            if not current_app:
                raise RuntimeError("현재 실행 중인 BlogHelper.app 위치를 찾지 못했습니다.")
            staged_root = data_dir / f"BlogHelper-v{to_version}.stage"
            shutil.rmtree(staged_root, ignore_errors=True)
            staged_app = staged_root / "BlogHelper.app"
            shutil.copytree(current_app, staged_app, symlinks=True, copy_function=shutil.copy2)

            relative_target = str(operation.get("target") or "")
            current_binary = _safe_child(current_app, relative_target)
            staged_binary = _safe_child(staged_app, relative_target)
            _verify_patch_source(current_binary, operation)
            patched_binary = staged_binary.with_suffix(staged_binary.suffix + ".patched")
            bsdiff4.file_patch(str(current_binary), str(patched_binary), str(patch_file))
            _verify_patch_target(patched_binary, operation)
            patched_binary.chmod(staged_binary.stat().st_mode)
            patched_binary.replace(staged_binary)

            for replacement in manifest.get("replacements", []):
                replacement = dict(replacement)
                source = _safe_child(work_dir, str(replacement.get("source") or ""))
                target = _safe_child(staged_app, str(replacement.get("target") or ""))
                if not source.is_file():
                    raise RuntimeError("빠른 업데이트 교체 파일이 없습니다.")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target, follow_symlinks=False)
                _verify_patch_target(target, replacement)

            subprocess.run(
                ["/usr/bin/xattr", "-cr", str(staged_app)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(staged_app)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            return staged_app

        raise RuntimeError("현재 운영체제는 빠른 업데이트를 지원하지 않습니다.")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


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
$TargetDirectory = Split-Path -Parent $Target
$Backup = "$Target.update-backup"
$Pending = "$Target.update-new"

function Wait-ForParentExit {
    for ($i = 0; $i -lt 60; $i++) {
        if (-not (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 500
    }
    if (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) {
        Write-UpdateLog "정상 종료가 지연되어 기존 프로그램을 종료합니다."
        Stop-Process -Id $ParentProcessId -Force -ErrorAction SilentlyContinue
        for ($i = 0; $i -lt 20; $i++) {
            if (-not (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue)) { return }
            Start-Sleep -Milliseconds 250
        }
    }
}

function Move-WithRetry([string]$From, [string]$To) {
    $LastError = $null
    for ($i = 0; $i -lt 40; $i++) {
        try {
            Move-Item -LiteralPath $From -Destination $To -Force -ErrorAction Stop
            return
        } catch {
            $LastError = $_.Exception
            Start-Sleep -Milliseconds 500
        }
    }
    throw $LastError
}

function Start-BlogHelper {
    $Process = Start-Process `
        -FilePath $Target `
        -WorkingDirectory $TargetDirectory `
        -WindowStyle Normal `
        -PassThru `
        -ErrorAction Stop
    Start-Sleep -Seconds 4
    if ($Process.HasExited) {
        throw "새 프로그램이 실행 직후 종료되었습니다. 종료 코드: $($Process.ExitCode)"
    }
    Write-UpdateLog ("새 프로그램 재실행 성공 (PID: {0})" -f $Process.Id)
    return $Process
}

try {
    Write-UpdateLog "기존 프로그램 종료 대기"
    Wait-ForParentExit
    if (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) {
        throw "기존 프로그램을 종료하지 못했습니다."
    }

    New-Item -ItemType Directory -Force -Path $TargetDirectory | Out-Null
    Remove-Item -LiteralPath $Backup -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Pending -Force -ErrorAction SilentlyContinue

    Copy-Item -LiteralPath $Source -Destination $Pending -Force -ErrorAction Stop
    $SourceHash = (Get-FileHash -LiteralPath $Source -Algorithm SHA256).Hash
    $PendingHash = (Get-FileHash -LiteralPath $Pending -Algorithm SHA256).Hash
    if ($SourceHash -ne $PendingHash) {
        throw "새 프로그램 파일 검증에 실패했습니다."
    }

    if (Test-Path -LiteralPath $Target) {
        Move-WithRetry $Target $Backup
    }
    Move-WithRetry $Pending $Target
    Unblock-File -LiteralPath $Target -ErrorAction SilentlyContinue
    Write-UpdateLog "새 버전 설치 완료"
    $RestartedProcess = Start-BlogHelper

    Remove-Item -LiteralPath $Source -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Backup -Force -ErrorAction SilentlyContinue
} catch {
    Write-UpdateLog ("업데이트 실패: " + $_.Exception.Message)
    Remove-Item -LiteralPath $Pending -Force -ErrorAction SilentlyContinue
    if ($Backup -and (Test-Path -LiteralPath $Backup)) {
        Remove-Item -LiteralPath $Target -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $Backup -Destination $Target -Force -ErrorAction SilentlyContinue
        Write-UpdateLog "기존 버전 복구 완료"
    }
    if (Test-Path -LiteralPath $Target) {
        try {
            $RecoveredProcess = Start-BlogHelper
            Write-UpdateLog "기존 버전 재실행 완료"
        } catch {
            Write-UpdateLog ("기존 버전 재실행 실패: " + $_.Exception.Message)
        }
    }
}
''',
        encoding="utf-8-sig",
    )


def _write_macos_update_script(path: Path) -> None:
    path.write_text(
        r'''#!/bin/zsh
set -u
PARENT_PID="$1"
SOURCE="$2"
TARGET_APP="$3"
LOG_FILE="$4"
log_update() { print -r -- "[$(/bin/date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"; }

log_update "기존 프로그램 종료 대기"
for _ in {1..120}; do
    if ! /bin/kill -0 "$PARENT_PID" 2>/dev/null; then break; fi
    /bin/sleep 0.5
done

STAGE_DIR=""
BACKUP_APP="${TARGET_APP}.update-backup"
if [[ -d "$SOURCE" && "$SOURCE" == *.app ]]; then
    NEW_APP="$SOURCE"
else
    STAGE_DIR="$(/usr/bin/mktemp -d "${TMPDIR:-/tmp}/blog-helper-update.XXXXXX")"
    if /usr/bin/ditto -x -k "$SOURCE" "$STAGE_DIR"; then
        NEW_APP="$(/usr/bin/find "$STAGE_DIR" -maxdepth 3 -type d -name 'BlogHelper.app' -print -quit)"
    else
        NEW_APP=""
    fi
fi

if [[ -n "$NEW_APP" && -d "$NEW_APP" ]]; then
    /bin/rm -rf "$BACKUP_APP"
    if /bin/mv "$TARGET_APP" "$BACKUP_APP" && /bin/mv "$NEW_APP" "$TARGET_APP"; then
        /usr/bin/xattr -dr com.apple.quarantine "$TARGET_APP" 2>/dev/null || true
        log_update "새 버전 설치 완료"
        /usr/bin/open -n "$TARGET_APP"
        /bin/sleep 2
        /bin/rm -rf "$BACKUP_APP"
        [[ -n "$STAGE_DIR" ]] && /bin/rm -rf "$STAGE_DIR"
        [[ -f "$SOURCE" ]] && /bin/rm -f "$SOURCE"
        [[ -d "${SOURCE:h}" && "${SOURCE:h}" == *.stage ]] && /bin/rm -rf "${SOURCE:h}"
        exit 0
    fi
fi

log_update "업데이트 설치 실패, 기존 버전 복구"
if [[ -d "$BACKUP_APP" ]]; then
    /bin/rm -rf "$TARGET_APP"
    /bin/mv "$BACKUP_APP" "$TARGET_APP"
fi
[[ -d "$TARGET_APP" ]] && /usr/bin/open -n "$TARGET_APP"
[[ -n "$STAGE_DIR" ]] && /bin/rm -rf "$STAGE_DIR"
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
