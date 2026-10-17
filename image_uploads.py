"""Upload-only WebP encoding; never overwrite an authored/source image."""
from __future__ import annotations

from contextlib import contextmanager
import io
from pathlib import Path
import tempfile


def encode_webp_upload(source: Path | str) -> bytes:
    """Keep dimensions, alpha, orientation and animation without added loss.

    Pillow/HEIF support is loaded on demand, not during Windows app startup.
    Detect the real format: changing a filename extension is not conversion.
    """
    source = Path(source).expanduser()
    try:
        from PIL import Image, ImageOps, features

        if not features.check("webp"):
            raise RuntimeError("현재 이미지 라이브러리가 WebP 인코딩을 지원하지 않습니다.")
        if source.suffix.lower() in {".heic", ".heif"}:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        with Image.open(source) as original:
            if original.format == "WEBP":
                original.load()
                return source.read_bytes()
            output = io.BytesIO()
            frames = []
            durations = []
            animated = bool(getattr(original, "is_animated", False))
            start = 1 if animated and original.info.get("default_image") else 0
            frame_count = getattr(original, "n_frames", 1) if animated else 1
            metadata = {}
            # A CMYK/LAB source profile cannot describe converted RGB pixels.
            if original.mode in {"RGB", "RGBA", "P"} and original.info.get("icc_profile"):
                metadata["icc_profile"] = original.info["icc_profile"]
            loop = original.info.get("loop", 1)
            try:
                for index in range(start, frame_count):
                    original.seek(index)
                    transposed = ImageOps.exif_transpose(original)
                    try:
                        frames.append(transposed.convert("RGBA"))
                    finally:
                        transposed.close()
                    durations.append(original.info.get("duration", 0))
                frames[0].save(
                    output, format="WEBP", lossless=True, quality=100, method=6,
                    exact=True, save_all=animated, append_images=frames[1:],
                    duration=durations, loop=loop, **metadata,
                )
            finally:
                for frame in frames:
                    frame.close()
            data = output.getvalue()
            if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
                raise RuntimeError("WebP 변환 결과의 파일 형식을 확인하지 못했습니다.")
            return data
    except Exception as exc:
        raise RuntimeError(f"이미지 WebP 변환 실패 ({source.name}): {exc}") from exc


@contextmanager
def webp_upload_files(sources: list[str | Path]):
    """Keep isolated, verified WebP files alive until the uploader finishes.

    Per-file directories preserve readable names without collisions between
    different folders/formats with the same stem. Only staging copies are
    removed, including when conversion or browser upload raises an error.
    """
    with tempfile.TemporaryDirectory(prefix="blog-helper-webp-", ignore_cleanup_errors=True) as directory:
        paths = []
        for index, source in enumerate(sources):
            source = Path(source).expanduser()
            data = encode_webp_upload(source)
            parent = Path(directory) / str(index)
            parent.mkdir()
            staged = parent / source.with_suffix(".webp").name
            staged.write_bytes(data)
            paths.append(str(staged))
        yield paths
