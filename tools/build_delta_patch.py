from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import zipfile
from pathlib import Path

import bsdiff4


MAC_EXECUTABLE = "BlogHelper.app/Contents/MacOS/BlogHelper"
MAC_ASSET_PREFIX = "BlogHelper.app/Contents/Resources/assets/"
MAC_FONT_PREFIX = "BlogHelper.app/Contents/Resources/assets/fonts/"
MAC_REPLACEMENTS = (
    ("BlogHelper.app/Contents/Info.plist", "Contents/Info.plist"),
    ("BlogHelper.app/Contents/Resources/version.json", "Contents/Resources/version.json"),
    (
        "BlogHelper.app/Contents/Resources/assets/wordpress-logo.png",
        "Contents/Resources/assets/wordpress-logo.png",
    ),
    (
        "BlogHelper.app/Contents/Resources/assets/tistory-logo.png",
        "Contents/Resources/assets/tistory-logo.png",
    ),
    (
        "BlogHelper.app/Contents/Resources/assets/blogspot-logo.png",
        "Contents/Resources/assets/blogspot-logo.png",
    ),
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
            source_name = f"files/{target_name}"
            files[source_name] = content
            replacements.append(
                {
                    "target": target_name,
                    "source": source_name,
                    "target_sha256": sha256_bytes(content),
                }
            )
        # The executable diff cannot carry external resources. Include every
        # current app asset, not only fonts: new guide screenshots must arrive
        # on incremental installs too, and missing resources must self-repair.
        asset_names = sorted(name for name in current_zip.namelist()
                             if name.startswith(MAC_ASSET_PREFIX) and not name.endswith("/"))
        if not any(name.startswith(MAC_FONT_PREFIX) for name in asset_names):
            raise RuntimeError("Mac 업데이트에 포함할 글꼴 리소스를 찾지 못했습니다.")
        existing_targets = {item["target"] for item in replacements}
        for archive_name in asset_names:
            target_name = archive_name.removeprefix("BlogHelper.app/")
            if target_name in existing_targets:
                continue
            content = current_zip.read(archive_name)
            if (archive_name.startswith(MAC_FONT_PREFIX) and archive_name.lower().endswith((".ttf", ".otf"))
                    and content[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"ttcf")):
                raise RuntimeError(f"Mac 글꼴 리소스가 실제 글꼴 파일이 아닙니다: {archive_name}")
            source_name = f"files/{target_name}"
            files[source_name] = content
            replacements.append({"target": target_name, "source": source_name,
                                 "target_sha256": sha256_bytes(content)})

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
