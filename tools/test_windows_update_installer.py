from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app_updater import _write_windows_update_script


def main() -> None:
    if os.name != "nt":
        print("Windows updater integration test skipped on this platform.")
        return

    with tempfile.TemporaryDirectory(prefix="Blog Helper updater test ") as temp_dir:
        root = Path(temp_dir)
        source = root / "새 버전.cmd"
        target = root / "Blog Helper.cmd"
        marker = root / "restart-success.txt"
        log_path = root / "update-install.log"
        script_path = root / "apply-update.ps1"

        target.write_text("@echo off\r\nexit /b 99\r\n", encoding="utf-8")
        source.write_text(
            f'@echo off\r\n> "{marker}" echo restarted\r\nping 127.0.0.1 -n 8 > nul\r\n',
            encoding="utf-8",
        )
        _write_windows_update_script(script_path)

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
            timeout=30,
        )

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.2)

        if not marker.exists():
            log = log_path.read_text(encoding="utf-8-sig") if log_path.exists() else "로그 없음"
            raise RuntimeError(f"업데이트 후 프로그램이 재실행되지 않았습니다.\n{log}")
        if "새 프로그램 재실행 성공" not in log_path.read_text(encoding="utf-8-sig"):
            raise RuntimeError("업데이트 로그에서 재실행 성공을 확인하지 못했습니다.")
        print("Windows updater replacement and restart test passed.")


if __name__ == "__main__":
    main()
