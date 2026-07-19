from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import bsdiff4


MAC_EXECUTABLE = "BlogHelper.app/Contents/MacOS/BlogHelper"
MAC_REPLACEMENTS = (
    ("BlogHelper.app/Contents/Info.plist", "Contents/Info.plist"),
    ("BlogHelper.app/Contents/Resources/version.json", "Contents/Resources/version.json"),
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_patch_archive(output: Path, manifest: dict, files: dict[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(
            "patch-manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        for name, content in files.items():
            archive.writestr(name, content)


def build_windows_patch(args: argparse.Namespace) -> None:
    previous = Path(args.previous)
    current = Path(args.current)
    with tempfile.TemporaryDirectory() as temp_dir:
        patch_path = Path(temp_dir) / "BlogHelper.exe.bsdiff"
        bsdiff4.file_diff(str(previous), str(current), str(patch_path))
        manifest = {
            "format": 1,
            "platform": "windows",
            "from_version": args.from_version,
            "to_version": args.to_version,
            "binary_patch": {
                "target": "BlogHelper.exe",
                "patch_file": "patches/BlogHelper.exe.bsdiff",
                "source_sha256": sha256_file(previous),
                "target_sha256": sha256_file(current),
            },
            "replacements": [],
        }
        write_patch_archive(
            Path(args.output),
            manifest,
            {"patches/BlogHelper.exe.bsdiff": patch_path.read_bytes()},
        )


def build_macos_patch(args: argparse.Namespace) -> None:
    with zipfile.ZipFile(args.previous) as previous_zip, zipfile.ZipFile(args.current) as current_zip:
        previous_binary = previous_zip.read(MAC_EXECUTABLE)
        current_binary = current_zip.read(MAC_EXECUTABLE)
        files = {"patches/BlogHelper.bsdiff": bsdiff4.diff(previous_binary, current_binary)}
        replacements = []
        for archive_name, target_name in MAC_REPLACEMENTS:
            content = current_zip.read(archive_name)
            source_name = f"files/{Path(target_name).name}"
            files[source_name] = content
            replacements.append(
                {
                    "target": target_name,
                    "source": source_name,
                    "target_sha256": sha256_bytes(content),
                }
            )

    manifest = {
        "format": 1,
        "platform": "macos",
        "from_version": args.from_version,
        "to_version": args.to_version,
        "binary_patch": {
            "target": "Contents/MacOS/BlogHelper",
            "patch_file": "patches/BlogHelper.bsdiff",
            "source_sha256": sha256_bytes(previous_binary),
            "target_sha256": sha256_bytes(current_binary),
        },
        "replacements": replacements,
    }
    write_patch_archive(Path(args.output), manifest, files)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a verified Blog Helper delta patch.")
    parser.add_argument("--platform", choices=("windows", "macos"), required=True)
    parser.add_argument("--previous", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.platform == "windows":
        build_windows_patch(args)
    else:
        build_macos_patch(args)


if __name__ == "__main__":
    main()
