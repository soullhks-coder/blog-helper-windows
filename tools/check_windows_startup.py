"""Measure actual frozen Windows startup and check offline automation cold/hot."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def run_probe(executable: Path, root: Path, *, full_app: bool) -> dict:
    directory = root / executable.stem
    directory.mkdir()
    report = directory / "report.json"
    environment = {k: v for k, v in os.environ.items() if not k.startswith("_PYI_") and k != "_MEIPASS2"}
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    environment["BLOG_HELPER_DISABLE_UPDATES"] = "1"
    environment["BLOG_HELPER_DATA_DIR"] = str(directory / "data")
    if full_app:
        environment["BLOG_HELPER_STARTUP_TEST_REPORT"] = str(report)
        ready_marker = report.with_suffix(".ui-ready")
    else:
        environment["BLOG_HELPER_RESTART_TEST_MARKER"] = str(report)
        environment["BLOG_HELPER_RESTART_TEST_WINDOW"] = "1"
        ready_marker = report
    started = time.perf_counter()
    process = subprocess.Popen([str(executable)], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = started + 90
        while not ready_marker.exists():
            if process.poll() is not None:
                raise RuntimeError(f"Program exited before startup probe: {executable}")
            if time.perf_counter() > deadline:
                raise TimeoutError(f"Windows startup timed out: {executable}")
            time.sleep(0.05)
        ready_seconds = time.perf_counter() - started
        process.wait(timeout=90)
        if process.returncode != 0:
            raise RuntimeError(f"Startup probe exit code {process.returncode}")
        result = json.loads(report.read_text()) if full_app else {}
        result["launch_to_ready_seconds"] = ready_seconds
        return result
    finally:
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=False, capture_output=True)
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        print("Frozen startup probe requires Windows.")
        return
    with tempfile.TemporaryDirectory(prefix="BlogHelper-startup-", ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        if args.baseline:
            baseline = run_probe(args.baseline.resolve(), root, full_app=False)
            print("Previous EXE extraction/imports/tiny-window:", json.dumps(baseline), flush=True)
        # Same tiny-window path as the existing release, so timings compare
        # extraction/imports rather than the old empty window with our full UI.
        small = run_probe(args.executable.resolve(), root, full_app=False)
        print("Optimized EXE extraction/imports/tiny-window:", json.dumps(small), flush=True)
        full_executable = root / "FullApp.exe"
        import shutil
        shutil.copy2(args.executable, full_executable)
        result = run_probe(full_executable, root, full_app=True)
        print("Optimized full UI and offline Chrome cold/hot:", json.dumps(result, ensure_ascii=False), flush=True)
        assert result["passed"], result
        assert not result["driver_extracted_at_startup"], result
        assert not result["cache_exists_at_ui_ready"], result
        metadata = result["metadata"]
        assert metadata["archive_bytes"] < metadata["unpacked_bytes"], metadata


if __name__ == "__main__":
    main()
