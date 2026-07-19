from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_updater import _write_windows_update_script


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--executable",
        type=Path,
        help="Test updater restart with the real PyInstaller BlogHelper.exe.",
    )
    return parser.parse_args()


def main() -> None:
    if os.name != "nt":
        print("Windows updater integration test skipped on this platform.")
        return

    args = parse_args()
    # Windows can retain the launched process working directory for a few
    # seconds after exit. That delayed cleanup must not mask a passed restart.
    with tempfile.TemporaryDirectory(
        prefix="Blog Helper updater test ",
        ignore_cleanup_errors=True,
    ) as temp_dir:
        root = Path(temp_dir)
        marker = root / "restart-success.txt"
        log_path = root / "update-install.log"
        script_path = root / "apply-update.ps1"

        if args.executable:
            executable = args.executable.resolve()
            if not executable.is_file():
                raise FileNotFoundError(executable)
            source = root / "새 버전 BlogHelper.exe"
            target = root / "BlogHelper.exe"
            shutil.copy2(executable, source)
            shutil.copy2(executable, target)
            timeout = 90
        else:
            source = root / "새 버전.cmd"
            target = root / "Blog Helper.cmd"
            target.write_text("@echo off\r\nexit /b 99\r\n", encoding="utf-8")
            source.write_text(
                f'@echo off\r\n> "{marker}" echo restarted\r\nping 127.0.0.1 -n 8 > nul\r\n',
                encoding="utf-8",
            )
            timeout = 20

        expected_hash = sha256_file(source)
        _write_windows_update_script(script_path)

        environment = dict(os.environ)
        if args.executable:
            environment["BLOG_HELPER_RESTART_TEST_MARKER"] = str(marker)
            environment["BLOG_HELPER_DISABLE_UPDATES"] = "1"

        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                "-ParentProcessId",
                "2147483000",
                "-Source",
                str(source),
                "-Target",
                str(target),
                "-LogFile",
                str(log_path),
            ],
            check=True,
            timeout=timeout,
            env=environment,
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.2)

        if not marker.exists():
            log = log_path.read_text(encoding="utf-8-sig") if log_path.exists() else "로그 없음"
            raise RuntimeError(f"업데이트 후 프로그램이 재실행되지 않았습니다.\n{log}")
        if sha256_file(target) != expected_hash:
            raise RuntimeError("업데이트 후 대상 프로그램의 파일 검증에 실패했습니다.")
        if "새 프로그램 재실행 성공" not in log_path.read_text(encoding="utf-8-sig"):
            raise RuntimeError("업데이트 로그에서 재실행 성공을 확인하지 못했습니다.")
        test_kind = "actual BlogHelper.exe" if args.executable else "lightweight command"
        print(f"Windows updater replacement and restart test passed: {test_kind}.")
        # The test command stays alive briefly so Start-BlogHelper can verify it.
        # Let it release its working directory before TemporaryDirectory cleanup.
        time.sleep(10 if args.executable else 5)


if __name__ == "__main__":
    main()
