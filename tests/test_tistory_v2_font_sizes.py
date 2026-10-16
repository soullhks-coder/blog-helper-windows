import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from tistory_v2 import (SLIDE_FONT_SIZES, V2Store, apply_slide_font_sizes, export_deck,
                        render_slide, slide_font_size_overrides, slide_font_sizes,
                        validate_slide_font_size)


class SlideFontSizeTests(unittest.TestCase):
    def setUp(self):
        self.slide = {"eyebrow": "소제목", "title": "제목", "summary": "설명", "bullets": ["항목"]}

    def test_default_sizes_keep_legacy_layout_and_photo_title_size(self):
        self.assertEqual(slide_font_sizes(self.slide), SLIDE_FONT_SIZES)
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "photo.png"
            Image.new("RGB", (100, 100), "white").save(photo)
            slide = {**self.slide, "image": {"path": str(photo)}}
            self.assertEqual(slide_font_sizes(slide, 1)["title"], 24)
            self.assertEqual(slide_font_sizes(slide, 3)["title"], 26)
            self.assertEqual(slide_font_sizes({**slide, "layout": "공통 스타일"})["title"], 26)

    def test_valid_range_and_invalid_input_are_bounded(self):
        for value in (6, 96, "18", " 30 "):
            self.assertEqual(validate_slide_font_size(value), int(value))
        for value in (None, [], {}, True, False, 0, 5, 97, -1, 20.5, "", "1e2", "-8", "999999", "NaN"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_slide_font_size(value)
        self.assertEqual(slide_font_size_overrides({"title": "32", "summary": 9999, "unknown": 18}), {"title": 32})

    def test_corrupt_saved_sizes_fall_back_without_changing_original_render(self):
        expected = render_slide(self.slide, show_numbers=False).tobytes()
        for bad in (None, [], "broken", {"title": -6}, {"summary": None, "bullets": 9000}):
            self.assertEqual(expected, render_slide({**self.slide, "font_sizes": bad}, show_numbers=False).tobytes())

    def test_each_role_uses_its_selected_size_and_changes_image(self):
        sizes = {"eyebrow": 12, "title": 30, "summary": 17, "bullets": 16}
        slide = {**self.slide, "font_sizes": sizes}
        recorded = {}
        original = ImageDraw.ImageDraw.text

        def capture(draw, xy, text, *args, **kwargs):
            recorded[text] = kwargs["font"].size
            return original(draw, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", capture):
            rendered = render_slide(slide, show_numbers=False)
        for key, text in (("eyebrow", "소제목"), ("title", "제목"), ("summary", "설명"), ("bullets", "항목")):
            self.assertEqual(recorded[text], sizes[key])
        for key in SLIDE_FONT_SIZES:
            other = copy.deepcopy(slide)
            other["font_sizes"][key] += 4
            self.assertNotEqual(rendered.tobytes(), render_slide(other, show_numbers=False).tobytes(), key)

    def test_output_resolution_scales_type_and_preserves_requested_metadata(self):
        slide = {**self.slide, "font_sizes": {"title": 30}}
        original_slide = copy.deepcopy(slide)
        recorded = []
        original = ImageDraw.ImageDraw.text

        def capture(draw, xy, text, *args, **kwargs):
            if text == "제목":
                recorded.append(kwargs["font"].size)
            return original(draw, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", capture):
            render_slide(slide, size=(1200, 600), show_numbers=False)
        self.assertEqual(recorded, [60])
        self.assertEqual(slide, original_slide)

    def test_bulk_apply_is_independent_and_only_changes_size_settings(self):
        slides = [{**self.slide, "layout": "공통 스타일", "text_vertical": "아래", "slide_size": [900, 600]},
                  {**self.slide, "color_theme": "민트", "image": {"path": "preserve"}}]
        before = copy.deepcopy(slides)
        sizes = {"eyebrow": 14, "title": 30, "summary": 16, "bullets": 16}
        apply_slide_font_sizes(slides, sizes)
        for i, slide in enumerate(slides):
            self.assertEqual({key: value for key, value in slide.items() if key != "font_sizes"}, before[i])
            self.assertEqual(slide["font_sizes"], sizes)
        slides[0]["font_sizes"]["title"] = 40
        self.assertEqual(slides[1]["font_sizes"]["title"], 30)
        with self.assertRaises(ValueError):
            apply_slide_font_sizes(slides, {"title": 10000})

    def test_defaults_persist_and_export_matches_preview_exactly(self):
        with tempfile.TemporaryDirectory() as directory:
            store = V2Store(Path(directory))
            old = store.new_deck("이전", "제목", "본문", 10, "스카이")
            store.save_deck(old)
            sizes = {"eyebrow": 12, "title": 30, "summary": 17, "bullets": 16}
            store.save_preferences({"font_size_defaults": sizes})
            deck = store.new_deck("새 글", "제목", "<h2>제목</h2><p>설명</p>", 10, "민트")
            self.assertTrue(all(slide["font_sizes"] == sizes for slide in deck["slides"]))
            deck["slides"][2]["font_sizes"]["title"] = 40
            paths = export_deck(store, deck)
            saved = V2Store(Path(directory)).load_deck(deck["id"])
            self.assertEqual(saved["slides"][2]["font_sizes"]["title"], 40)
            self.assertEqual(saved["slides"][1]["font_sizes"]["title"], 30)
            self.assertTrue(all("font_sizes" not in slide for slide in store.load_deck(old["id"])["slides"]))
            for index, path in enumerate(paths):
                with Image.open(path) as output:
                    expected = render_slide(saved["slides"][index], "민트", index + 1, 10)
                    self.assertEqual(output.tobytes(), expected.tobytes())

    def test_large_fonts_and_alignment_do_not_cross_photo_or_footer_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "photo.png"
            Image.new("RGB", (100, 100), "white").save(photo)
            original = ImageDraw.ImageDraw.text
            for dimensions in ((600, 300), (300, 150), (900, 600), (300, 900)):
                for layout in ("공통 스타일", "사진 왼쪽", "사진 오른쪽"):
                    for value in (6, 32, 96):
                        for alignment in ("위", "중앙", "아래", "기본"):
                            bounds = []
                            def capture(draw, xy, text, *args, **kwargs):
                                bounds.append(draw.textbbox(xy, text, font=kwargs["font"]))
                                return original(draw, xy, text, *args, **kwargs)
                            slide = {key: ("긴 문구입니다 " * 15) for key in ("eyebrow", "title", "summary")}
                            slide.update(bullets=["항목 문구입니다 " * 15] * 3,
                                         font_sizes={key: value for key in SLIDE_FONT_SIZES}, layout=layout,
                                         image={"path": str(photo)}, text_horizontal="오른쪽", text_vertical=alignment)
                            with patch.object(ImageDraw.ImageDraw, "text", capture):
                                render_slide(slide, size=dimensions, show_numbers=False)
                            with self.subTest(size=dimensions, layout=layout, value=value, alignment=alignment):
                                self.assertTrue(bounds)
                                self.assertTrue(all(0 <= left <= right <= dimensions[0] and
                                                    0 <= top <= bottom <= dimensions[1] for left, top, right, bottom in bounds), bounds)


if __name__ == "__main__":
    unittest.main()
