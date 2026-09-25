import inspect
import unittest

import main
from history_data import (
    BLOG_HELPER_HISTORY,
    history_categories,
    history_dates,
    search_history,
)


class HistoryDataTests(unittest.TestCase):
    def test_history_covers_the_project_from_first_release_to_current_work(self) -> None:
        self.assertGreaterEqual(len(BLOG_HELPER_HISTORY), 40)
        self.assertEqual(BLOG_HELPER_HISTORY[0]["date"], "2026-09-25")
        self.assertEqual(BLOG_HELPER_HISTORY[-1]["date"], "2026-07-19")
        self.assertEqual(history_dates(), tuple(sorted(history_dates(), reverse=True)))
        self.assertEqual(len(history_dates()), len(set(history_dates())))

        for entry in BLOG_HELPER_HISTORY:
            with self.subTest(date=entry["date"], title=entry["title"]):
                self.assertTrue(str(entry["version"]).startswith("v"))
                self.assertTrue(str(entry["category"]).strip())
                self.assertTrue(str(entry["title"]).strip())
                self.assertTrue(str(entry["request"]).strip())
                self.assertGreater(len(tuple(entry["changes"])), 0)

    def test_search_finds_visible_text_across_requests_and_changes(self) -> None:
        blogspot_results = search_history("블로그스팟")
        self.assertGreater(len(blogspot_results), 0)
        self.assertTrue(
            any("Blogger" in " ".join(result["changes"]) for result in blogspot_results)
        )

        crop_results = search_history("60px")
        self.assertEqual([entry["date"] for entry in crop_results], ["2026-09-20"])

        version_results = search_history("v1.1.170")
        self.assertEqual([entry["date"] for entry in version_results], ["2026-09-22"])

    def test_date_and_category_filters_can_be_combined(self) -> None:
        date_results = search_history(selected_date="2026-09-17")
        self.assertEqual(len(date_results), 1)
        self.assertIn("홈 대시보드", date_results[0]["title"])

        category = "프로그램 관리"
        self.assertIn(category, history_categories())
        combined = search_history("히스토리", "2026-09-25", category)
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["version"], "v1.1.173–v1.1.177")
        self.assertEqual(search_history("없는 검색어", "2026-09-25", category), [])


class HistorySettingsSourceTests(unittest.TestCase):
    def test_settings_header_exposes_history_to_the_right_of_service_connections(self) -> None:
        source = inspect.getsource(main.KeywordApp._build_settings_page)
        service_position = source.index('text="서비스 연동"')
        history_position = source.index('text="히스토리"')
        self.assertLess(service_position, history_position)
        self.assertIn('self._switch_settings_section("history")', source)
        self.assertIn("self._build_history_settings_page()", source)

    def test_history_page_provides_search_date_category_and_reset_controls(self) -> None:
        source = inspect.getsource(main.KeywordApp._build_history_settings_page)
        for attribute in (
            "history_search_entry",
            "history_date_menu",
            "history_category_menu",
            "history_clear_button",
            "history_cards_frame",
        ):
            self.assertIn(attribute, source)

        switch_source = inspect.getsource(main.KeywordApp._switch_settings_section)
        self.assertIn('"history": self.settings_history_section_button', switch_source)
        self.assertIn("self.history_scroll.grid", switch_source)
        self.assertIn("self._render_history_cards()", switch_source)


if __name__ == "__main__":
    unittest.main()
