"""Check the real platform PNG exporter, offline, for every background style."""
import argparse
import os
from pathlib import Path
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont


def main():
    # GitHub's Windows runner defaults to cp1252 stdout. Keep Korean style
    # names diagnostic-only rather than letting their logging abort the test.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Optional contact sheet for visual QA")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="blog-helper-background-render-") as directory:
        os.environ["BLOG_HELPER_DATA_DIR"] = directory
        os.environ["BLOG_HELPER_DISABLE_UPDATES"] = "1"
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import main as app
        from design_backgrounds import BACKGROUND_STYLES, background_svg

        images = []
        for index, style in enumerate(BACKGROUND_STYLES):
            svg = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" viewBox="0 0 400 400">'
            svg += background_svg(400, 400, style, "#fffaf6", "#dd8bab")
            svg += '<rect x="14" y="14" width="372" height="372" rx="8" fill="none" stroke="#e3b4c7"/>'
            svg += '<text x="200" y="190" text-anchor="middle" font-family="sans-serif" font-size="23" font-weight="bold" fill="#25354b">Blog Helper</text>'
            svg += '<text x="200" y="224" text-anchor="middle" font-family="sans-serif" font-size="16" fill="#795064">Background style</text></svg>'
            path = Path(directory) / f"background-{index}.png"
            app.render_svg_to_png(svg, path, 400, "배경 스타일 테스트")
            with Image.open(path) as source:
                assert source.format == "PNG" and source.size == (400, 400), (style, source.size)
                image = source.convert("RGB")
                if style == "단색":
                    assert image.getpixel((80, 80)) == (255, 250, 246)
                elif style == "그라데이션":
                    top, bottom = image.getpixel((80, 30)), image.getpixel((80, 370))
                    assert sum(top) > sum(bottom), (style, top, bottom)
                else:
                    crop = image.crop((60, 40, 340, 140))
                    assert len(crop.getcolors(100_000)) > 1, style
                    crop.close()
                images.append(image)
            print(f"{sys.platform}: real PNG export {style} passed", flush=True)
        for style, width, height in (("메모지", 600, 300), ("도트", 400, 640)):
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">'
            svg += background_svg(width, height, style, '#fffaf6', '#dd8bab') + '</svg>'
            path = Path(directory) / f'ratio-{width}-{height}.png'
            app.render_svg_to_png(svg, path, max(width, height), '배경 비율 테스트')
            with Image.open(path) as image:
                assert image.size == (width, height), (style, image.size)
            print(f'{sys.platform}: real PNG export {style} {width}x{height} passed', flush=True)
        if args.output:
            sheet = Image.new("RGB", (4*260, 2*285), "#edf1f7")
            draw = ImageDraw.Draw(sheet)
            labels = ("Solid", "Gradient", "Grid paper", "Memo paper", "Dots", "Diagonal", "Stripes")
            for index, (image, label) in enumerate(zip(images, labels)):
                x, y = (index % 4)*260+10, (index // 4)*285+10
                with image.resize((240, 240), Image.Resampling.LANCZOS) as preview:
                    sheet.paste(preview, (x, y))
                draw.text((x+10, y+248), label, fill="#334155", font=ImageFont.load_default(size=16))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(args.output)
            sheet.close()
            print(args.output, flush=True)
        for image in images:
            image.close()


if __name__ == "__main__":
    main()
