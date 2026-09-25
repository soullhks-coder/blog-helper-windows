import inspect
import unittest
from types import SimpleNamespace

import main


class WorkerStub:
    def __init__(self, running: bool) -> None:
        self.running = running

    def is_alive(self) -> bool:
        return self.running


def activity_app(**overrides):
    values = {
        "_worker_is_running": main.KeywordApp._worker_is_running,
        "writing_auto_run_active": False,
        "active_automation_upload_item_id": "",
        "naver_kin_direct_mode": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class SidebarActivityShimmerTests(unittest.TestCase):
    def test_three_supported_menus_can_be_active_at_the_same_time(self) -> None:
        app = activity_app(
            article_worker=WorkerStub(True),
            naver_blog_worker=WorkerStub(True),
            naver_kin_automation_worker=WorkerStub(True),
        )

        self.assertEqual(
            main.KeywordApp._sidebar_activity_map(app),
            {"writing": True, "naver_blog": True, "naver_kin": True},
        )

    def test_stopped_workers_clear_each_indicator_independently(self) -> None:
        app = activity_app(
            article_worker=WorkerStub(False),
            naver_blog_worker=WorkerStub(True),
            naver_kin_worker=WorkerStub(False),
        )

        self.assertEqual(
            main.KeywordApp._sidebar_activity_map(app),
            {"writing": False, "naver_blog": True, "naver_kin": False},
        )

    def test_automation_queue_pipeline_does_not_mark_manual_writing_menu(self) -> None:
        app = activity_app(
            active_automation_upload_item_id="queue-item-1",
            pipeline_worker=WorkerStub(True),
        )

        self.assertFalse(main.KeywordApp._sidebar_activity_map(app)["writing"])

    def test_direct_kin_flow_stays_active_between_worker_transitions(self) -> None:
        app = activity_app(naver_kin_direct_mode=True)

        self.assertTrue(main.KeywordApp._sidebar_activity_map(app)["naver_kin"])

    def test_shimmer_uses_indeterminate_progress_without_changing_menu_text(self) -> None:
        build_source = inspect.getsource(main.KeywordApp._build_sidebar_activity_shimmers)
        refresh_source = inspect.getsource(main.KeywordApp._refresh_sidebar_activity_shimmers)
        labels_source = inspect.getsource(main.KeywordApp._apply_sidebar_menu_labels)

        self.assertIn('mode="indeterminate"', build_source)
        self.assertIn("shimmer.start()", refresh_source)
        self.assertIn("shimmer.stop()", refresh_source)
        self.assertNotIn("진행 중", labels_source)
        self.assertIn("text=normalized[page_name]", labels_source)


if __name__ == "__main__":
    unittest.main()
