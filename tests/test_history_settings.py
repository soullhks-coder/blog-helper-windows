import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
        self.assertEqual(BLOG_HELPER_HISTORY[0]["version"], f"v{main.APP_VERSION}")
        self.assertGreaterEqual(BLOG_HELPER_HISTORY[0]["date"], "2026-10-04")
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

        previous_post_results = search_history("이전 발행글")
        self.assertEqual(
            [entry["date"] for entry in previous_post_results],
            ["2026-09-27", "2026-09-26"],
        )

    def test_date_and_category_filters_can_be_combined(self) -> None:
        date_results = search_history(selected_date="2026-09-17")
        self.assertEqual(len(date_results), 1)
        self.assertIn("홈 대시보드", date_results[0]["title"])

        category = "프로그램 관리"
        self.assertIn(category, history_categories())
        combined = search_history("히스토리", "2026-09-25", category)
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["version"], "v1.1.173–v1.1.178")
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


class HistoryPaginationTests(unittest.TestCase):
    def make_app(self):
        app = SimpleNamespace(history_page=0, _history_render_job=None, history_scroll=Mock(),
            history_cards_frame=Mock(), history_search_entry=Mock(), history_date_menu=Mock(),
            history_category_menu=Mock(), history_result_label=Mock(), history_page_label=Mock(),
            history_prev_button=Mock(), history_next_button=Mock(), after_cancel=Mock(),
            _tag_mousewheel_descendants=Mock(), _theme_palette=lambda: {
                key: '#ffffff' for key in ('panel', 'border', 'text', 'muted', 'accent', 'selected', 'subtext', 'input')})
        app.history_cards_frame.winfo_children.return_value = [Mock(), Mock()]
        app.history_search_entry.get.return_value = ''
        app.history_date_menu.get.return_value = '전체 날짜'
        app.history_category_menu.get.return_value = '전체 분류'
        return app

    def render(self, app, **kwargs):
        with patch.object(main.ctk, 'CTkFrame'), patch.object(main.ctk, 'CTkFont'), \
             patch.object(main.ctk, 'CTkLabel') as labels:
            main.KeywordApp._render_history_cards(app, **kwargs)
        titles = {entry['title'] for entry in BLOG_HELPER_HISTORY}
        return [call.kwargs['text'] for call in labels.call_args_list if call.kwargs.get('text') in titles]

    def test_all_records_are_accessible_in_order_with_at_most_ten_retained_cards(self):
        app = self.make_app()
        displayed = []
        for page in range((len(BLOG_HELPER_HISTORY) + 9) // 10):
            app.history_page = page
            titles = self.render(app, reset_page=False)
            self.assertLessEqual(len(titles), 10)
            displayed.extend(titles)
        self.assertEqual(displayed, [entry['title'] for entry in search_history()])
        app.history_next_button.configure.assert_called_with(state='disabled')
        for old_card in app.history_cards_frame.winfo_children():
            self.assertEqual(old_card.destroy.call_count, (len(BLOG_HELPER_HISTORY) + 9) // 10)

    def test_query_filters_search_the_entire_catalog_and_reset_the_page(self):
        app = self.make_app()
        app.history_page = 8
        app.history_search_entry.get.return_value = '60px'
        self.assertEqual(self.render(app), [entry['title'] for entry in search_history('60px')])
        self.assertEqual(app.history_page, 0)
        app.history_prev_button.configure.assert_called_with(state='disabled')
        app.history_next_button.configure.assert_called_with(state='disabled')
        app.history_search_entry.get.return_value = '히스토리'
        app.history_date_menu.get.return_value = '2026-09-25'
        app.history_category_menu.get.return_value = '프로그램 관리'
        self.assertEqual(self.render(app), [entry['title'] for entry in search_history('히스토리', '2026-09-25', '프로그램 관리')])

    def test_page_edges_empty_results_and_pending_search_are_safe(self):
        app = self.make_app()
        app.history_page = -1
        self.assertEqual(self.render(app, reset_page=False), [entry['title'] for entry in search_history()[:10]])
        app.history_page = 1000
        self.render(app, reset_page=False)
        self.assertEqual(app.history_page, (len(BLOG_HELPER_HISTORY) - 1) // 10)
        app.history_search_entry.get.return_value = '없는 검색어'
        app._history_render_job = 'pending'
        self.assertEqual(self.render(app), [])
        app.after_cancel.assert_called_once_with('pending')
        self.assertIsNone(app._history_render_job)
        app.history_page_label.configure.assert_called_with(text='1 / 1')
        app.history_next_button.configure.assert_called_with(state='disabled')

    def test_navigation_preserves_filters_and_returns_to_the_top(self):
        app = self.make_app()
        app._render_history_cards = Mock()
        main.KeywordApp._move_history_page(app, 1)
        self.assertEqual(app.history_page, 1)
        app._render_history_cards.assert_called_once_with(reset_page=False)
        app.history_scroll._parent_canvas.yview_moveto.assert_called_once_with(0)


if __name__ == "__main__":
    unittest.main()
