import queue
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
                "button_text": "#수원 국가유산 야행홈페이지 바로가기👆🏻",
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

    def test_transient_festival_link_is_used_in_article_but_not_saved(self) -> None:
        value = lambda text: SimpleNamespace(get=lambda: text)
        full_width = SimpleNamespace(get=lambda: True)
        app = SimpleNamespace(
            link_rows=[
                {
                    "button_entry": value("#축제홈페이지 바로가기👆🏻"),
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


if __name__ == "__main__":
    unittest.main()
