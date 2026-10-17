"""Real image encoding and upload-boundary checks without remote publishing."""
import io
import base64
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageOps, features
import image_uploads
import main
from tistory_v2_writing import TistoryV2WritingControls
from types import SimpleNamespace


class WebPEncodingTests(unittest.TestCase):
    def test_source_formats_dimensions_alpha_and_pixels_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            for fmt, mode, suffix, color in (("PNG", "RGBA", ".png", (23, 62, 97, 90)),
                                              ("JPEG", "RGB", ".jpg", (51, 83, 22)),
                                              ("BMP", "RGB", ".bmp", (70, 90, 150)),
                                              ("PNG", "L", ".png", 128),
                                              ("PNG", "P", ".png", 3),
                                              ("JPEG", "CMYK", ".jpg", (12, 74, 98, 4))):
                with self.subTest(fmt=fmt, mode=mode):
                    source = Path(directory) / (mode + suffix)
                    Image.new(mode, (31, 19), color).save(source, format=fmt)
                    before = source.read_bytes()
                    with Image.open(source) as image:
                        expected = image.convert("RGBA").tobytes()
                    result = image_uploads.encode_webp_upload(source)
                    self.assertEqual(result[:4], b"RIFF")
                    self.assertEqual(result[8:12], b"WEBP")
                    with Image.open(io.BytesIO(result)) as image:
                        self.assertEqual(image.format, "WEBP")
                        self.assertEqual(image.size, (31, 19))
                        self.assertEqual(image.convert("RGBA").tobytes(), expected)
                    self.assertEqual(source.read_bytes(), before)

    def test_exif_orientation_is_applied_not_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "휴대폰.jpg"
            exif = Image.Exif()
            exif[274] = 6
            Image.new("RGB", (30, 20), "red").save(source, exif=exif)
            with Image.open(source) as image:
                expected = ImageOps.exif_transpose(image).convert("RGBA")
            with Image.open(io.BytesIO(image_uploads.encode_webp_upload(source))) as image:
                self.assertEqual(image.size, (20, 30))
                self.assertEqual(image.convert("RGBA").tobytes(), expected.tobytes())

    def test_animated_gif_keeps_frames_timing_and_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "움직임.gif"
            frames = [Image.new("RGB", (20, 14), color) for color in ("red", "blue")]
            frames[0].save(source, save_all=True, append_images=frames[1:], duration=[100, 250], loop=2)
            with Image.open(io.BytesIO(image_uploads.encode_webp_upload(source))) as image:
                self.assertEqual(image.n_frames, 2)
                self.assertEqual(image.info["loop"], 2)
                durations = []
                for index, color in enumerate(((255, 0, 0), (0, 0, 255))):
                    image.seek(index)
                    image.load()
                    self.assertEqual(image.convert("RGB").getpixel((0, 0)), color)
                    durations.append(image.info["duration"])
                self.assertEqual(durations, [100, 250])

    def test_apng_default_preview_is_not_inserted_as_an_animation_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "animation.png"
            frames = [Image.new("RGBA", (20, 14), color) for color in ("green", "red", "blue")]
            frames[0].save(source, save_all=True, append_images=frames[1:], default_image=True,
                           duration=[80, 160], loop=3)
            with Image.open(io.BytesIO(image_uploads.encode_webp_upload(source))) as image:
                self.assertEqual(image.n_frames, 2)
                self.assertEqual(image.info["loop"], 3)
                image.seek(0)
                self.assertEqual(image.convert("RGB").getpixel((0, 0)), (255, 0, 0))

    def test_existing_webp_is_not_recompressed_and_mislabeled_png_is_really_encoded(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "existing.webp"
            Image.new("RGBA", (20, 14), (12, 78, 53, 0)).save(source, format="WEBP", lossless=True)
            before = source.read_bytes()
            self.assertEqual(image_uploads.encode_webp_upload(source), before)
            Image.new("RGB", (20, 14), "blue").save(source, format="PNG")
            with Image.open(io.BytesIO(image_uploads.encode_webp_upload(source))) as result:
                self.assertEqual(result.format, "WEBP")
            self.assertTrue(source.read_bytes().startswith(b"\x89PNG"))

    def test_encoder_unavailable_or_invalid_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "invalid.png"
            source.write_bytes(b"not an image")
            with self.assertRaisesRegex(RuntimeError, "WebP 변환 실패"):
                image_uploads.encode_webp_upload(source)
            with patch.object(features, "check", return_value=False), \
                 self.assertRaisesRegex(RuntimeError, "WebP 인코딩을 지원하지"):
                image_uploads.encode_webp_upload(source)

    def test_heic_is_decoded_only_when_needed(self):
        try:
            from pillow_heif import register_heif_opener
        except ImportError:
            self.skipTest("HEIF plugin not installed")
        register_heif_opener()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "휴대폰.heic"
            Image.new("RGB", (32, 24), "red").save(source, format="HEIF")
            before = source.read_bytes()
            with Image.open(io.BytesIO(image_uploads.encode_webp_upload(source))) as result:
                self.assertEqual(result.format, "WEBP")
                self.assertEqual(result.size, (32, 24))
            self.assertEqual(source.read_bytes(), before)


class UploadBoundaryTests(unittest.TestCase):
    def test_v2_uses_selected_blog_webp_preference_without_mutating_other_blog(self):
        controls = object.__new__(TistoryV2WritingControls)
        controls.api = main
        controls.app = SimpleNamespace(_prompt_sets=lambda: [{
            "id": "tistory", "platform": "tistory", "title_prompt": "제목", "article_prompt": "본문"}])
        controls.selection = lambda: {"profile": "티스토리 2", "prompt_id": "tistory"}
        controls.review_publish_var = SimpleNamespace(get=lambda: False)
        for enabled in (False, True):
            base = main.WordPressSettings(tistory_upload_webp=not enabled, tistory_profiles=[
                {"upload_webp": not enabled}, {"upload_webp": enabled}])
            execution = controls.execution_settings(base)
            self.assertIs(execution.tistory_upload_webp, enabled)
            self.assertIs(base.tistory_upload_webp, not enabled)
            self.assertIs(base.tistory_profiles[0]["upload_webp"], not enabled)
            self.assertEqual(base.tistory_active_profile, "티스토리 1")

    def test_normal_and_v2_workers_forward_setting_and_matching_thumbnail_data(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "thumbnail.png"
            Image.new("RGBA", (32, 24), (20, 40, 60, 80)).save(source)
            for enabled in (False, True):
                for v2 in (False, True):
                    with self.subTest(enabled=enabled, v2=v2), \
                         patch.object(main, "GOOGLE_IMAGE_COLLAGE_ENABLED", False), \
                         patch.object(main, "append_runtime_log"), \
                         patch.object(main, "cleanup_tistory_automation_files"), \
                         patch.object(main, "build_tistory_editor_automation_script", return_value="fixture") as script, \
                         patch.object(main, "run_tistory_playwright_automation", return_value=(False, "fixture")) as browser:
                        worker = main.TistoryAutomationWorker(
                            "테스트 글", "<p>테스트 본문</p>", queue.Queue(),
                            thumbnail_path=str(source), ads_enabled=False, upload_webp=enabled,
                            tistory_v2_slide_paths=[f"/fixture/{index}.png" for index in range(10)] if v2 else None,
                        )
                        worker.run()
                        browser.assert_called_once()
                        self.assertIs(browser.call_args.kwargs["upload_webp"], enabled)
                        self.assertEqual(browser.call_args.kwargs["native_image_files"], {
                            "__BLOG_HELPER_TISTORY_NATIVE_THUMBNAIL__": str(source)})
                        options = script.call_args.kwargs
                        self.assertEqual(options["thumbnail_file_name"], "thumbnail.webp" if enabled else "thumbnail.png")
                        mime, encoded = options["thumbnail_data_url"].split(",", 1)
                        self.assertEqual(mime, "data:image/webp;base64" if enabled else "data:image/png;base64")
                        with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
                            self.assertEqual(image.format, "WEBP" if enabled else "PNG")

    def test_standard_pipeline_carries_setting_in_immutable_upload_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            for enabled in (False, True):
                settings = main.WordPressSettings(
                    target_platforms=["tistory"], tistory_upload_webp=enabled,
                    tistory_blog_url="https://fixture.tistory.com", threads_auto_publish=False,
                )
                events = queue.Queue()
                with patch.object(main, "TISTORY_DRAFT_FILE", Path(directory) / "draft.html"):
                    main.PublishPipelineWorker(
                        settings, "테스트 글", "<p>테스트 본문</p>", "", "테스트", [], "slug", "", events,
                    ).run()
                self.assertFalse(any(kind == "publish_pipeline_error" for kind, _ in events.queue))
                payload = next(value["tistory"] for kind, value in events.queue if kind == "publish_pipeline_done")
                settings.tistory_upload_webp = not enabled
                self.assertIs(payload["upload_webp"], enabled)

    def test_blogger_stages_each_file_without_stem_collisions_and_cleans_on_error(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = [Path(directory) / "사진.png", Path(directory) / "사진.jpg"]
            for source, color in zip(sources, ("red", "blue")):
                Image.new("RGB", (32, 24), color).save(source)
            before = [source.read_bytes() for source in sources]
            observed = []
            def upload(_page, paths, _events):
                self.assertEqual(len(set(paths)), 2)
                for path, color in zip(paths, ((255, 0, 0), (0, 0, 254))):
                    observed.append(Path(path))
                    self.assertEqual(Path(path).suffix, ".webp")
                    with Image.open(path) as image:
                        self.assertEqual(image.format, "WEBP")
                        self.assertEqual(image.convert("RGB").getpixel((0, 0)), color)
                raise RuntimeError("picker failure")
            with patch.object(main, "_upload_blogspot_webp_files", side_effect=upload), \
                 self.assertRaisesRegex(RuntimeError, "picker failure"):
                main.upload_blogspot_images(object(), list(map(str, sources)), queue.Queue())
            self.assertTrue(all(not path.exists() for path in observed))
            self.assertEqual([source.read_bytes() for source in sources], before)

    def test_tistory_native_and_representative_uploads_follow_toggle(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "thumbnail.png"
            Image.new("RGB", (32, 24), "red").save(source)
            before = source.read_bytes()
            for enabled in (False, True):
                seen = []
                def upload(_page, path, *_args):
                    seen.append(Path(path))
                    with Image.open(path) as image:
                        self.assertEqual(image.format, "WEBP" if enabled else "PNG")
                    return "uploaded"
                with patch.object(main, "_upload_tistory_image_file", side_effect=upload):
                    self.assertEqual(main.upload_tistory_image_file(object(), str(source), queue.Queue(),
                                                                    upload_webp=enabled), "uploaded")
                with patch.object(main, "_attach_tistory_representative_image_file", side_effect=upload):
                    main.attach_tistory_representative_image_file(object(), str(source), queue.Queue(), upload_webp=enabled)
                self.assertEqual(source.read_bytes(), before)
                if enabled:
                    self.assertTrue(all(not path.exists() for path in seen))
                else:
                    self.assertEqual(seen, [source, source])

    def test_invalid_blogger_or_wordpress_source_never_reaches_uploader(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.png"
            source.write_bytes(b"bad image")
            with patch.object(main, "_upload_blogspot_webp_files") as upload, self.assertRaises(RuntimeError):
                main.upload_blogspot_images(object(), [str(source)], queue.Queue())
            upload.assert_not_called()
            client = main.WordPressClient(main.WordPressSettings(blog_url="https://example.com"))
            with patch.object(main, "urlopen") as post, self.assertRaises(RuntimeError):
                client.upload_media(source, "제목")
            post.assert_not_called()

    def test_tistory_setting_defaults_on_and_round_trips_per_profile(self):
        self.assertTrue(main.WordPressSettings().tistory_upload_webp)
        profiles = main.normalize_tistory_profiles([])
        self.assertTrue(all(profile["upload_webp"] for profile in profiles))
        profiles[1]["upload_webp"] = False
        settings = main.WordPressSettings(tistory_profiles=profiles, tistory_active_profile="티스토리 2",
                                          tistory_upload_webp=False)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main, "STATE_FILE", Path(directory) / "state.json"), \
             patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value), \
             patch.object(main.KeychainStore, "load_secret", return_value=""):
            main.AppStateStore.save(settings, save_secrets=False)
            restored = main.AppStateStore.load()
        self.assertFalse(restored.tistory_upload_webp)
        self.assertFalse(restored.tistory_profiles[1]["upload_webp"])
        self.assertTrue(restored.tistory_profiles[0]["upload_webp"])
        self.assertTrue(restored.tistory_profiles[2]["upload_webp"])


if __name__ == "__main__":
    unittest.main()
