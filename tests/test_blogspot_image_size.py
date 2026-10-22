"""Image resize completion must reflect settled geometry, not one DOM change."""
import unittest
import tempfile
import queue
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from PIL import Image

import main
from blogspot_image_layout import SIZE_TIMEOUT_MS, verified_size, size_summary
from playwright.sync_api import Error as PlaywrightError


def snapshot(width=320, height=320, **values):
    return {"src": "fixture-image", "width": str(width), "height": str(height),
            "renderedWidth": width, "renderedHeight": height,
            "complete": True, "naturalWidth": 1024, "naturalHeight": 1024,
            "originalWidth": 1024, "originalHeight": 1024, **values}


class BlogspotImageSizeTests(unittest.TestCase):
    def wait(self, before, frames):
        page, image = Mock(), Mock()
        frames = list(frames)
        calls = []
        def observe():
            value = frames.pop(0) if len(frames) > 1 else frames[0]
            calls.append(value)
            return value
        with patch.object(main, "_blogspot_image_size_snapshot", side_effect=lambda _image: observe()), \
             patch.object(main, "append_runtime_log"):
            main._wait_for_blogspot_image_size(page, image, before)
        page.get_by_role.assert_not_called()
        image.click.assert_not_called()
        return page, calls

    def test_resize_after_ten_seconds_is_not_rejected_at_eight_seconds(self):
        page, calls = self.wait(snapshot(), [snapshot()] * 40 + [snapshot(640, 640)] * 4)
        self.assertGreaterEqual(len(calls), 44)
        self.assertGreaterEqual(sum(call.args[0] for call in page.wait_for_timeout.call_args_list), 10_750)

    def test_portrait_height_change_is_checked_when_width_stays_the_same(self):
        before = snapshot(320, 320, originalWidth=512, originalHeight=1024)
        result = snapshot(320, 640, originalWidth=512, originalHeight=1024)
        page, calls = self.wait(before, [result] * 4)
        self.assertEqual(len(calls), 4)

    def test_partial_growth_is_not_accepted_as_extra_large(self):
        page, calls = self.wait(snapshot(), [snapshot(400, 400)] * 4 + [snapshot(640, 640)] * 4)
        self.assertEqual(len(calls), 8)

    def test_dimensions_wait_for_actual_image_load_and_stability(self):
        frames = [snapshot(640, 640, complete=False)] * 6 + [snapshot(640, 640)] * 4
        page, calls = self.wait(snapshot(), frames)
        self.assertEqual(len(calls), 10)

    def test_width_and_height_must_finish_together(self):
        frames = [snapshot(640, 320)] * 4 + [snapshot(640, 640)] * 4
        page, calls = self.wait(snapshot(), frames)
        self.assertEqual(len(calls), 8)

    def test_small_original_does_not_require_upscaling_to_640(self):
        result = snapshot(320, 200, originalWidth=320, originalHeight=200,
                          naturalWidth=320, naturalHeight=200)
        page, calls = self.wait(result, [result] * 4)
        self.assertEqual(len(calls), 4)

    def test_css_resize_is_detected_without_attribute_changes(self):
        result = snapshot(320, 320, computedWidth="640px", computedHeight="640px")
        page, calls = self.wait(snapshot(), [result] * 4)
        self.assertEqual(len(calls), 4)

    def test_original_file_is_not_confused_with_small_download_preview(self):
        result = snapshot(320, 320, originalWidth="", originalHeight="", naturalWidth=320, naturalHeight=320)
        self.assertIsNone(verified_size({"originalWidth": 1024, "originalHeight": 1024}, result))

    def test_detached_or_unloaded_image_is_never_ready(self):
        for values in ({"connected": False}, {"naturalWidth": 0}, {"renderedHeight": 0}):
            self.assertIsNone(verified_size(snapshot(), snapshot(640, 640, **values)))

    def test_replaced_image_node_can_recover_without_clicking_other_controls(self):
        page, image = Mock(), Mock()
        page.is_closed.return_value = False
        with patch.object(main, "_blogspot_image_size_snapshot", side_effect=[PlaywrightError("detached")]
                          + [snapshot(640, 640)] * 4), patch.object(main, "append_runtime_log"):
            main._wait_for_blogspot_image_size(page, image, snapshot())
        self.assertEqual(page.wait_for_timeout.call_count, 4)
        page.get_by_role.assert_not_called()
        image.click.assert_not_called()

    def test_closed_browser_aborts_immediately(self):
        page = Mock()
        page.is_closed.return_value = True
        with patch.object(main, "_blogspot_image_size_snapshot", side_effect=PlaywrightError("closed")), \
             patch.object(main, "append_runtime_log"), self.assertRaisesRegex(RuntimeError, "브라우저가 닫혔"):
            main._wait_for_blogspot_image_size(page, Mock(), snapshot())
        page.wait_for_timeout.assert_not_called()

    def test_timeout_and_diagnostics_are_bounded_and_non_sensitive(self):
        self.assertEqual(SIZE_TIMEOUT_MS, 30_000)
        self.assertNotIn("secret", size_summary(snapshot(src="https://private.example/secret")))

    def test_uploaded_file_dimensions_reach_the_size_verifier(self):
        page = MagicMock()
        source = "https://blogger.googleusercontent.com/fixture.webp"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.webp"
            Image.new("RGB", (320, 200), "blue").save(path, "WEBP")
            with patch.object(main, "find_blogspot_image_insert_button", return_value=Mock()), \
                 patch("blogspot_upload.open_image_picker", return_value=(MagicMock(), MagicMock())), \
                 patch.object(main, "collect_blogspot_compose_image_sources", side_effect=[[], [source]]), \
                 patch.object(main, "configure_blogspot_inserted_image") as configure, \
                 patch.object(main, "describe_blogspot_upload_state", return_value="fixture"), \
                 patch.object(main, "append_runtime_log"):
                self.assertEqual(main._upload_blogspot_webp_files(page, [str(path)], queue.Queue()), 1)
                configure.assert_called_once_with(page, source, original_size=(320, 200))


if __name__ == "__main__":
    unittest.main()
