import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class NaverBlogQuoteClickDistanceTests(unittest.TestCase):
    def test_default_and_range_normalization(self) -> None:
        self.assertEqual(
            main.WordPressSettings().naver_blog_quote_click_distance_px,
            90,
        )
        self.assertEqual(main.normalize_naver_blog_quote_click_distance("350"), 350)
        self.assertEqual(main.normalize_naver_blog_quote_click_distance(""), 90)
        self.assertEqual(main.normalize_naver_blog_quote_click_distance(-1), 0)
        self.assertEqual(main.normalize_naver_blog_quote_click_distance(9999), 2000)

    def test_distance_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                naver_blog_quote_click_distance_px=425,
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

        self.assertEqual(loaded.naver_blog_quote_click_distance_px, 425)

    def test_worker_passes_distance_to_playwright(self) -> None:
        result_queue = queue.Queue()
        worker = main.NaverBlogBootstrapWorker(
            "https://blog.naver.com/example?Redirect=Write&",
            "example",
            result_queue,
            quote_click_distance_px=640,
        )
        with patch.object(
            main,
            "run_naver_blog_playwright_bootstrap",
            return_value=(True, {"message": "완료"}),
        ) as bootstrap:
            worker.run()

        self.assertEqual(
            bootstrap.call_args.kwargs["quote_click_distance_px"],
            640,
        )


if __name__ == "__main__":
    unittest.main()
