import ast
import base64
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
from pillow_heif import from_pillow


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class NaverBlogManualImageTests(unittest.TestCase):
    def test_default_mode_is_automatic(self) -> None:
        settings = main.WordPressSettings()

        self.assertEqual(
            settings.naver_blog_image_mode,
            main.NAVER_BLOG_IMAGE_MODE_AUTO,
        )
        self.assertEqual(settings.naver_blog_manual_image_paths, [])
        self.assertEqual(
            main.normalize_naver_blog_image_mode("이미지 수동"),
            main.NAVER_BLOG_IMAGE_MODE_MANUAL,
        )

    def test_manual_mode_and_paths_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "app_state.json"
            selected = root / "selected.jpg"
            selected.write_bytes(b"image")
            settings = main.WordPressSettings(
                naver_blog_image_mode=main.NAVER_BLOG_IMAGE_MODE_MANUAL,
                naver_blog_manual_image_paths=[str(selected)],
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(
                    main.PromptFileStore,
                    "load_into",
                    side_effect=lambda value: value,
                ),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

        self.assertEqual(
            loaded.naver_blog_image_mode,
            main.NAVER_BLOG_IMAGE_MODE_MANUAL,
        )
        self.assertEqual(loaded.naver_blog_manual_image_paths, [str(selected)])

    def test_manual_images_below_limit_are_copied_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root / "source"
            work_dir = root / "work"
            source_dir.mkdir()
            work_dir.mkdir()
            first = source_dir / "first.jpg"
            second = source_dir / "second.png"
            first.write_bytes(b"first-image")
            second.write_bytes(b"second-image")

            copied = main.prepare_naver_blog_manual_image_files(
                [str(first), str(second)],
                5,
                work_dir,
                queue.Queue(),
            )

            self.assertEqual(len(copied), 2)
            self.assertEqual(Path(copied[0]).read_bytes(), b"first-image")
            self.assertEqual(Path(copied[1]).read_bytes(), b"second-image")

    def test_heic_image_is_preserved_for_naver_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "iPhone photo.HEIC"
            source.write_bytes(b"original-heic-image")

            copied = main.prepare_naver_blog_manual_image_files(
                [str(source)],
                3,
                root / "work",
                queue.Queue(),
            )

            self.assertEqual(len(copied), 1)
            self.assertEqual(Path(copied[0]).suffix, ".heic")
            self.assertEqual(Path(copied[0]).read_bytes(), b"original-heic-image")

    def test_heic_image_can_be_opened_for_thumbnail_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "preview.HEIC"
            original = main.Image.new("RGB", (12, 8), (32, 96, 160))
            from_pillow(original).save(source)

            self.assertTrue(main._ensure_heif_opener_registered())

            with main.Image.open(source) as preview:
                preview.load()

            self.assertEqual(preview.size, (12, 8))

    def test_manual_mode_allows_zero_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            copied = main.prepare_naver_blog_manual_image_files(
                [],
                3,
                Path(directory),
                queue.Queue(),
            )

        self.assertEqual(copied, [])

    def test_manual_images_over_setting_limit_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = []
            for index in range(3):
                image = root / f"image-{index}.jpg"
                image.write_bytes(b"image")
                images.append(str(image))

            with self.assertRaisesRegex(RuntimeError, "2장"):
                main.prepare_naver_blog_manual_image_files(
                    images,
                    2,
                    root / "work",
                    queue.Queue(),
                )

    def test_images_can_be_added_in_multiple_batches(self) -> None:
        first_batch = ["/images/one.jpg", "/images/two.jpg"]
        second_batch = ["/images/three.jpg", "/images/four.jpg"]

        merged = main.merge_naver_blog_manual_image_paths(
            first_batch,
            second_batch,
            5,
        )

        self.assertEqual(merged, [*first_batch, *second_batch])

    def test_duplicate_images_are_ignored_when_adding_another_batch(self) -> None:
        merged = main.merge_naver_blog_manual_image_paths(
            ["/images/one.jpg", "/images/two.jpg"],
            ["/images/two.jpg", "/images/three.jpg"],
            4,
        )

        self.assertEqual(
            merged,
            ["/images/one.jpg", "/images/two.jpg", "/images/three.jpg"],
        )

    def test_additional_batch_cannot_exceed_remaining_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "4장"):
            main.merge_naver_blog_manual_image_paths(
                ["/images/one.jpg", "/images/two.jpg"],
                ["/images/three.jpg", "/images/four.jpg", "/images/five.jpg"],
                4,
            )

    def test_manual_workflow_does_not_run_automatic_image_collector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "selected.jpg"
            source.write_bytes(b"selected-image")
            collector = Mock()
            collector._collect_reference_text_for_keyword.return_value = "최신 참고내용"

            with (
                patch.object(
                    main,
                    "AutomationKeywordQueueWorker",
                    return_value=collector,
                ),
                patch.object(
                    main,
                    "generate_naver_blog_article",
                    return_value=("제목", "<p>본문</p>", "본문", "테스트 모델"),
                ),
                patch.object(main, "collect_naver_blog_image_files") as auto_collect,
            ):
                payload = main.build_naver_blog_workflow_payload(
                    main.WordPressSettings(),
                    "테스트 주제",
                    4,
                    str(root / "work"),
                    queue.Queue(),
                    image_mode=main.NAVER_BLOG_IMAGE_MODE_MANUAL,
                    manual_image_paths=[str(source)],
                )

            auto_collect.assert_not_called()
            self.assertEqual(payload["image_mode"], main.NAVER_BLOG_IMAGE_MODE_MANUAL)
            self.assertEqual(payload["image_count"], 1)
            self.assertEqual(Path(payload["image_paths"][0]).read_bytes(), b"selected-image")

    def test_writing_tab_exposes_manual_picker_and_setting_limit(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        build_source = ast.get_source_segment(
            source,
            methods["_build_naver_blog_writing_tab"],
        ) or ""
        picker_source = ast.get_source_segment(
            source,
            methods["_choose_naver_blog_manual_images"],
        ) or ""
        thumbnail_source = ast.get_source_segment(
            source,
            methods["_render_naver_blog_manual_image_thumbnails"],
        ) or ""
        thumbnail_loader_source = ast.get_source_segment(
            source,
            methods["_create_naver_blog_manual_thumbnail"],
        ) or ""
        remove_icon_source = ast.get_source_segment(
            source,
            methods["_load_naver_blog_remove_icon"],
        ) or ""

        self.assertIn('values=["이미지 자동", "이미지 수동"]', build_source)
        self.assertIn("naver_blog_manual_thumbnail_frame", build_source)
        self.assertIn("naver_blog_manual_image_button", build_source)
        self.assertIn("askopenfilenames", picker_source)
        self.assertIn("*.heic", picker_source)
        self.assertIn("*.HEIC", picker_source)
        self.assertIn("HEIC", picker_source)
        self.assertIn("_naver_blog_manual_image_limit", picker_source)
        self.assertIn("merge_naver_blog_manual_image_paths", picker_source)
        self.assertIn('"이미지 수 초과"', picker_source)
        self.assertIn("_create_naver_blog_manual_thumbnail", thumbnail_source)
        self.assertIn('size: int = 40', thumbnail_loader_source)
        self.assertIn("remove_button_kwargs", thumbnail_source)
        self.assertIn('remove_button_kwargs["image"]', thumbnail_source)
        self.assertIn("_remove_naver_blog_manual_image", thumbnail_source)
        self.assertIn("NAVER_BLOG_REMOVE_ICON_PNG_BASE64", remove_icon_source)
        self.assertIn("size=(20, 20)", remove_icon_source)

    def test_attached_remove_icon_is_embedded_as_png(self) -> None:
        icon_bytes = base64.b64decode(main.NAVER_BLOG_REMOVE_ICON_PNG_BASE64)

        self.assertTrue(icon_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(icon_bytes), 1000)


if __name__ == "__main__":
    unittest.main()
