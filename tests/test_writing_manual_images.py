import ast
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class WritingManualImageTests(unittest.TestCase):
    def test_manual_provider_and_paths_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "app_state.json"
            selected = root / "selected.HEIC"
            selected.write_bytes(b"image")
            settings = main.WordPressSettings(
                inline_images_enabled=True,
                inline_images_count=3,
                inline_images_provider=main.INLINE_IMAGES_PROVIDER_MANUAL,
                inline_images_manual_paths=[str(selected)],
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

        self.assertTrue(loaded.inline_images_enabled)
        self.assertEqual(
            loaded.inline_images_provider,
            main.INLINE_IMAGES_PROVIDER_MANUAL,
        )
        self.assertEqual(loaded.inline_images_manual_paths, [str(selected)])

    def test_usable_manual_images_allow_fewer_than_configured_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "one.jpg"
            second = root / "two.HEIC"
            first.write_bytes(b"one")
            second.write_bytes(b"two")

            paths = main.usable_inline_manual_image_paths(
                [str(first), str(second)],
                4,
            )

        self.assertEqual(paths, [str(first), str(second)])

    def test_article_worker_inserts_selected_images_without_imagen_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "one.jpg"
            second = root / "two.png"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            settings = main.WordPressSettings(
                inline_images_enabled=True,
                inline_images_count=4,
                inline_images_provider=main.INLINE_IMAGES_PROVIDER_MANUAL,
                inline_images_manual_paths=[str(first), str(second)],
                imagen_api_key="",
            )
            worker = main.ArticleGenerationWorker(
                settings,
                "주제",
                "키워드",
                "",
                queue.Queue(),
            )

            result = worker._attach_inline_images(
                "테스트 제목",
                "<p>첫 문단</p><h2>소제목</h2><p>둘째 문단</p>",
            )

        self.assertEqual(result.count("data-blog-helper-inline-image-path"), 2)
        self.assertIn(str(first), result)
        self.assertIn(str(second), result)

    def test_writing_ui_exposes_adaptive_multi_select_picker(self) -> None:
        source = MAIN_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        methods = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        build_source = ast.get_source_segment(
            source,
            methods["_build_writing_workflow"],
        ) or ""
        picker_source = ast.get_source_segment(
            source,
            methods["_choose_writing_manual_images"],
        ) or ""
        refresh_source = ast.get_source_segment(
            source,
            methods["_refresh_writing_manual_image_controls"],
        ) or ""
        thumbnail_source = ast.get_source_segment(
            source,
            methods["_render_writing_manual_image_thumbnails"],
        ) or ""

        self.assertIn("INLINE_IMAGES_PROVIDERS", build_source)
        self.assertIn("writing_manual_image_panel", build_source)
        self.assertIn("writing_manual_thumbnail_frame", build_source)
        self.assertIn("askopenfilenames", picker_source)
        self.assertIn("*.heic", picker_source)
        self.assertIn("merge_naver_blog_manual_image_paths", picker_source)
        self.assertIn("grid_remove", refresh_source)
        self.assertIn("_remove_writing_manual_image", thumbnail_source)


if __name__ == "__main__":
    unittest.main()
