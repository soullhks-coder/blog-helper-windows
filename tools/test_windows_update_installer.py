from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_updater import launch_update_installer


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

        environment = dict(os.environ)
        if args.executable:
            environment["BLOG_HELPER_RESTART_TEST_MARKER"] = str(marker)
            environment["BLOG_HELPER_RESTART_TEST_WINDOW"] = "1"
            environment["BLOG_HELPER_DISABLE_UPDATES"] = "1"

        original_environment = dict(os.environ)
        os.environ.update(environment)
        try:
            launch_update_installer(
                source,
                root,
                target_executable=target,
                parent_process_id=2147483000,
                require_visible_window=bool(args.executable),
            )
        finally:
            os.environ.clear()
            os.environ.update(original_environment)

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if marker.exists() and log_path.exists() and "재실행 성공" in log_path.read_text(
                encoding="utf-8-sig"
            ):
                break
            time.sleep(0.2)

        if not marker.exists():
            log = log_path.read_text(encoding="utf-8-sig") if log_path.exists() else "로그 없음"
            helper_log_path = root / "update-helper-output.log"
            helper_log = (
                helper_log_path.read_text(encoding="utf-8-sig", errors="replace")
                if helper_log_path.exists()
                else "도우미 출력 로그 없음"
            )
            raise RuntimeError(
                f"업데이트 후 프로그램이 재실행되지 않았습니다.\n\n[설치 로그]\n{log}"
                f"\n\n[도우미 출력]\n{helper_log}"
            )
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
