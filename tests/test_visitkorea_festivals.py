import queue
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import main


class VisitKoreaFestivalTests(unittest.TestCase):
    DETAIL_URL = (
        "https://korean.visitkorea.or.kr/kfes/detail/fstvlDetail.do?"
        "fstvlCntntsId=61868dbd-e352-418c-a6f3-9cd0684c5cf7&cntntsNm=수원국가유산야행"
    )

    def test_public_data_select_all_only_enables_selectable_rows(self) -> None:
        class FakeBooleanVar:
            def __init__(self, value: bool = False) -> None:
                self.value = value

            def set(self, value: bool) -> None:
                self.value = bool(value)

            def get(self) -> bool:
                return self.value

        variables = {
            "festival-visible-1": FakeBooleanVar(),
            "festival-published": FakeBooleanVar(True),
            "festival-visible-2": FakeBooleanVar(),
        }

        selected_count = main.KeywordApp._set_public_data_selection_vars(
            variables,
            {"festival-visible-1", "festival-visible-2"},
            True,
        )

        self.assertEqual(selected_count, 2)
        self.assertTrue(variables["festival-visible-1"].get())
        self.assertTrue(variables["festival-visible-2"].get())
        self.assertFalse(variables["festival-published"].get())

        cleared_count = main.KeywordApp._set_public_data_selection_vars(
            variables,
            {"festival-visible-1", "festival-visible-2"},
            False,
        )
        self.assertEqual(cleared_count, 0)
        self.assertFalse(any(variable.get() for variable in variables.values()))

    def test_extracts_stable_festival_id_from_detail_url(self) -> None:
        self.assertEqual(
            main.visitkorea_festival_id(self.DETAIL_URL),
            "61868dbd-e352-418c-a6f3-9cd0684c5cf7",
        )

    def test_publish_history_is_persistent_and_does_not_overwrite_first_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = main.FestivalStateStore(Path(directory) / "festival-state.json")
            state = store.load()
            event = {
                "festival_id": "festival-123",
                "title": "테스트 축제",
                "detail_url": self.DETAIL_URL,
            }
            first = store.mark_published(
                state,
                event,
                ["https://blog.example.com/first"],
                published_at="2026-08-16 12:00:00",
            )
            second = store.mark_published(
                state,
                event,
                ["https://blog.example.com/duplicate"],
                published_at="2026-08-17 12:00:00",
            )

            restored = store.load()
            self.assertEqual(first, second)
            self.assertEqual(restored["published"]["festival-123"]["published_at"], "2026-08-16 12:00:00")
            self.assertEqual(
                restored["published"]["festival-123"]["published_urls"],
                ["https://blog.example.com/first"],
            )

    def test_publish_history_does_not_clear_a_different_active_festival(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = main.FestivalStateStore(Path(directory) / "festival-state.json")
            state = store.load()
            state["active"] = {
                "festival_id": "festival-next",
                "title": "다음 축제",
            }

            store.mark_published(
                state,
                {
                    "festival_id": "festival-finished",
                    "title": "완료 축제",
                    "detail_url": self.DETAIL_URL,
                },
                ["https://blog.example.com/finished"],
            )

            restored = store.load()
            self.assertEqual(restored["active"]["festival_id"], "festival-next")

    def test_new_fetch_save_preserves_history_already_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = main.FestivalStateStore(Path(directory) / "festival-state.json")
            current = store.load()
            store.mark_published(
                current,
                {"festival_id": "festival-saved", "title": "저장 축제", "detail_url": self.DETAIL_URL},
                ["https://blog.example.com/saved"],
            )
            stale_fetch_state = store.empty_state()
            stale_fetch_state["events"] = [{"festival_id": "festival-new", "title": "신규 축제"}]

            store.save(stale_fetch_state)

            restored = store.load()
            self.assertIn("festival-saved", restored["published"])
            self.assertEqual(restored["events"][0]["festival_id"], "festival-new")

    def test_publish_history_is_removed_only_by_explicit_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = main.FestivalStateStore(Path(directory) / "festival-state.json")
            state = store.load()
            state["events"] = [{"festival_id": "festival-123", "published": True}]
            store.mark_published(
                state,
                {"festival_id": "festival-123", "title": "테스트 축제", "detail_url": self.DETAIL_URL},
                ["https://blog.example.com/published"],
            )

            store.clear_published(state)

            restored = store.load()
            self.assertEqual(restored["published"], {})
            self.assertFalse(restored["events"][0]["published"])

    def test_reference_text_contains_official_fields_and_portal_section(self) -> None:
        worker = main.VisitKoreaFestivalDetailWorker({}, queue.Queue())
        reference = worker._build_reference_text(
            {
                "title": "수원 국가유산 야행",
                "schedule": "2026.08.14 ~ 2026.08.16",
                "address": "경기도 수원시 수원천로392번길",
                "price": "입장료 무료",
                "organizer": "수원특례시/수원문화재단",
                "phone": "031-290-3562",
                "official_url": "https://www.swcf.or.kr/",
                "detail_url": self.DETAIL_URL,
                "poster_url": "https://kfescdn.visitkorea.or.kr/poster.jpg",
                "description": "공식 축제 소개입니다.",
            },
            "네이버와 구글에서 확인한 참고자료",
        )

        self.assertIn("축제 일정: 2026.08.14 ~ 2026.08.16", reference)
        self.assertIn("장소/위치: 경기도 수원시", reference)
        self.assertIn("공식 포스터 이미지:", reference)
        self.assertIn("[여러 포털 교차검색 참고자료]", reference)

    def test_builds_transient_full_width_middle_link_for_official_homepage(self) -> None:
        link = main.build_festival_official_link(
            {
                "title": "수원 국가유산 야행",
                "official_url": "https://www.swcf.or.kr/",
            }
        )

        self.assertEqual(
            link,
            {
                "button_text": "수원 국가유산 야행홈페이지 바로가기👆🏻",
                "url": "https://www.swcf.or.kr/",
                "width": "",
                "full_width": True,
                "position": "본문중간",
                "transient": True,
                "source": "festival",
            },
        )

    def test_skips_festival_link_when_official_homepage_is_missing(self) -> None:
        self.assertIsNone(
            main.build_festival_official_link(
                {
                    "title": "홈페이지 없는 축제",
                    "official_url": "",
                }
            )
        )

    def test_builds_festival_link_for_each_selected_article_position(self) -> None:
        event = {
            "title": "수원 국가유산 야행",
            "official_url": "https://www.swcf.or.kr/",
        }

        positions = [
            main.build_festival_official_link(event, position=position)["position"]
            for position in main.LINK_POSITION_OPTIONS
        ]

        self.assertEqual(positions, ["본문상단", "본문중간", "본문하단"])

    def test_festival_link_position_settings_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = main.FestivalStateStore(Path(directory) / "festival-state.json")
            state = store.load()
            state["link_positions"] = ["본문상단", "본문하단"]
            store.save(state)

            restored = store.load()

            self.assertEqual(restored["link_positions"], ["본문상단", "본문하단"])

    def test_multiple_festival_selections_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = main.FestivalStateStore(Path(directory) / "festival-state.json")
            state = store.load()
            state["selected_ids"] = ["festival-1", "festival-2"]
            store.save(state)

            restored = store.load()

            self.assertEqual(restored["selected_ids"], ["festival-1", "festival-2"])

    def test_festival_automation_payload_keeps_detail_and_selected_links(self) -> None:
        event = {
            "festival_id": "festival-1",
            "title": "수원 국가유산 야행",
            "detail_url": self.DETAIL_URL,
            "official_url": "https://www.swcf.or.kr/",
            "reference_text": "축제 공식 상세정보",
            "area": "경기",
        }

        payload = main.build_public_data_automation_payload(
            event,
            "festival",
            ["본문상단", "본문하단"],
        )

        self.assertEqual(payload["source_name"], "구석구석 축제")
        self.assertEqual(payload["festival"]["festival_id"], "festival-1")
        self.assertEqual(
            [link["position"] for link in payload["writing_links"]],
            ["본문상단", "본문하단"],
        )

    def test_transient_festival_link_is_used_in_article_but_not_saved(self) -> None:
        value = lambda text: SimpleNamespace(get=lambda: text)
        full_width = SimpleNamespace(get=lambda: True)
        app = SimpleNamespace(
            link_rows=[
                {
                    "button_entry": value("축제홈페이지 바로가기👆🏻"),
                    "url_entry": value("https://festival.example.com"),
                    "width_entry": value(""),
                    "full_width_var": full_width,
                    "position_menu": value("본문중간"),
                    "transient": True,
                    "source": "festival",
                }
            ]
        )

        article_links = main.KeywordApp._current_writing_links(app)
        saved_links = main.KeywordApp._current_writing_links(app, include_transient=False)

        self.assertEqual(len(article_links), 1)
        self.assertEqual(saved_links, [])

    def test_applying_festival_does_not_override_saved_thumbnail_design(self) -> None:
        method_source = inspect.getsource(main.KeywordApp._apply_visitkorea_festival_to_writing)

        self.assertNotIn("self.thumbnail_background_image_path =", method_source)
        self.assertNotIn("self.thumbnail_background_mode_menu.set", method_source)
        self.assertNotIn("self.thumbnail_image_position_menu.set", method_source)
        self.assertNotIn("self.thumbnail_image_scale_var.set", method_source)
        self.assertNotIn("self.thumbnail_image_opacity_var.set", method_source)

    def test_applying_festival_starts_auto_progress_after_collected_reference(self) -> None:
        method_source = inspect.getsource(main.KeywordApp._apply_visitkorea_festival_to_writing)

        self.assertIn("self._start_auto_progress_with_collected_reference", method_source)
        self.assertIn("축제 공식정보와 포털 참고자료", method_source)

    def test_applying_festival_adds_links_for_selected_positions(self) -> None:
        method_source = inspect.getsource(main.KeywordApp._apply_visitkorea_festival_to_writing)

        self.assertIn("for position in self._selected_festival_link_positions()", method_source)
        self.assertIn("build_festival_official_link(event, position=position)", method_source)

    def test_detail_apply_button_is_next_to_published_history_filter(self) -> None:
        method_source = inspect.getsource(main.KeywordApp._build_visitkorea_festival_panel)

        button_index = method_source.index("self.festival_apply_button = ctk.CTkButton")
        lower_help_row_index = method_source.index("apply_row = ctk.CTkFrame")
        self.assertLess(button_index, lower_help_row_index)
        self.assertIn(
            "self.festival_apply_button.grid(row=0, column=2, sticky=\"e\")",
            method_source,
        )


if __name__ == "__main__":
    unittest.main()
