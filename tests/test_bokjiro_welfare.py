import inspect
import queue
import tempfile
import unittest
from pathlib import Path

import main


class BokjiroWelfareTests(unittest.TestCase):
    def test_publish_history_survives_a_new_collection_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = main.WelfareStateStore(Path(directory) / "welfare-state.json")
            state = store.load()
            store.mark_published(
                state,
                {
                    "welfare_id": "WLF-100",
                    "title": "청년 지원정책",
                    "detail_url": "https://www.bokjiro.go.kr/policy/WLF-100",
                },
                ["https://blog.example.com/welfare"],
            )
            fetched = store.empty_state()
            fetched["events"] = [{"welfare_id": "WLF-200", "title": "새 정책"}]

            store.save(fetched)

            restored = store.load()
            self.assertIn("WLF-100", restored["published"])
            self.assertEqual(restored["events"][0]["welfare_id"], "WLF-200")

    def test_explicit_reset_clears_welfare_publish_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = main.WelfareStateStore(Path(directory) / "welfare-state.json")
            state = store.load()
            state["events"] = [{"welfare_id": "WLF-100", "published": True}]
            store.mark_published(
                state,
                {"welfare_id": "WLF-100", "title": "청년 지원정책"},
                ["https://blog.example.com/welfare"],
            )

            store.clear_published(state)

            restored = store.load()
            self.assertEqual(restored["published"], {})
            self.assertFalse(restored["events"][0]["published"])

    def test_list_worker_normalizes_bokjiro_row_and_filters_keywords(self) -> None:
        worker = main.BokjiroWelfareListWorker(
            filters={
                "provider": "중앙부처",
                "lifecycle": "청년",
                "household": "전체",
                "interest": "일자리",
                "keyword": "청년",
                "excluded_keyword": "",
            },
            published_history={},
            result_queue=queue.Queue(),
        )
        rows = [
            {
                "WLFARE_INFO_ID": "WLF-100",
                "WLFARE_INFO_NM": "청년 취업 지원",
                "WLFARE_INFO_OUTL_CN": "구직 청년의 취업 준비를 지원합니다.",
                "BIZ_CHR_INST_NM": "고용노동부",
                "WLFARE_GDNC_TRGT_KCD": "01",
                "RETURN_STR": "BKJR_LFTM_CYC_CD:청년;FMLY_CIRC_CD:전체;INTRS_THEMA_CD:일자리;",
            },
            {
                "WLFARE_INFO_ID": "WLF-200",
                "WLFARE_INFO_NM": "노년 건강 지원",
                "RETURN_STR": "BKJR_LFTM_CYC_CD:노년;INTRS_THEMA_CD:신체건강;",
            },
        ]

        events = worker._normalize_rows(rows)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["welfare_id"], "WLF-100")
        self.assertEqual(events[0]["target_kind"], "01")
        self.assertIn("/moveTWAT52011M.do?", events[0]["detail_url"])
        self.assertIn("wlfareInfoId=WLF-100", events[0]["detail_url"])

    def test_private_policy_uses_the_private_bokjiro_detail_page(self) -> None:
        worker = main.BokjiroWelfareListWorker(
            filters={"provider": "민간"},
            published_history={},
            result_queue=queue.Queue(),
        )

        events = worker._normalize_rows(
            [
                {
                    "WLFARE_INFO_ID": "WLF-PRIVATE",
                    "WLFARE_INFO_NM": "민간 아동 지원",
                    "WLFARE_GDNC_TRGT_KCD": "03",
                }
            ]
        )

        self.assertEqual(events[0]["target_kind"], "03")
        self.assertIn("/moveTWAT52015M.do?", events[0]["detail_url"])

    def test_reference_text_contains_official_policy_sections(self) -> None:
        worker = main.BokjiroWelfareDetailWorker({}, queue.Queue())
        reference = worker._build_reference_text(
            {
                "title": "청년 지원정책",
                "summary": "청년의 자립을 지원합니다.",
                "institution": "보건복지부",
                "eligibility": "만 19세 이상 청년",
                "benefits": "월 지원금 지급",
                "application_method": "복지로 온라인 신청",
                "detail_url": "https://www.bokjiro.go.kr/policy/WLF-100",
            },
            "포털 교차검색 자료",
        )

        self.assertIn("지원대상: 만 19세 이상 청년", reference)
        self.assertIn("서비스 내용: 월 지원금 지급", reference)
        self.assertIn("신청방법: 복지로 온라인 신청", reference)
        self.assertIn("[여러 포털 교차검색 참고자료]", reference)

    def test_detail_sections_ignore_selected_tab_and_survey_text(self) -> None:
        worker = main.BokjiroWelfareDetailWorker({}, queue.Queue())

        sections = worker._extract_sections(
            "추가정보\n서비스 내용 선택됨\n"
            "서비스 내용\n매월 50만원을 지급합니다.\n"
            "현재 페이지의 메뉴가 명확하게 구분되어 있습니까?\n1점\n2점"
        )

        self.assertNotIn("additional_info", sections)
        self.assertEqual(sections["benefits"], "매월 50만원을 지급합니다.")

    def test_welfare_writing_flow_does_not_override_thumbnail_design(self) -> None:
        method_source = inspect.getsource(main.KeywordApp._apply_bokjiro_welfare_to_writing)

        self.assertNotIn("self.thumbnail_background_image_path =", method_source)
        self.assertNotIn("self.thumbnail_background_mode_menu.set", method_source)
        self.assertIn("self._start_auto_progress_with_collected_reference", method_source)

    def test_welfare_link_positions_and_multiple_selections_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = main.WelfareStateStore(Path(directory) / "welfare-state.json")
            state = store.load()
            state["link_positions"] = ["본문상단", "본문중간", "본문하단"]
            state["selected_ids"] = ["WLF-100", "WLF-200"]
            store.save(state)

            restored = store.load()

            self.assertEqual(restored["link_positions"], main.LINK_POSITION_OPTIONS)
            self.assertEqual(restored["selected_ids"], ["WLF-100", "WLF-200"])

    def test_welfare_automation_payload_adds_links_to_all_selected_positions(self) -> None:
        event = {
            "welfare_id": "WLF-100",
            "title": "청년 월세 지원",
            "detail_url": "https://www.bokjiro.go.kr/policy/WLF-100",
            "application_url": "https://www.bokjiro.go.kr/apply/WLF-100",
            "reference_text": "지원대상과 신청방법을 포함한 공식 상세정보",
            "interest": "주거",
        }

        payload = main.build_public_data_automation_payload(
            event,
            "welfare",
            main.LINK_POSITION_OPTIONS,
        )

        self.assertEqual(payload["source_name"], "공공 복지")
        self.assertEqual(payload["welfare"]["welfare_id"], "WLF-100")
        self.assertEqual(
            [link["position"] for link in payload["writing_links"]],
            ["본문상단", "본문중간", "본문하단"],
        )
        self.assertTrue(all(link["full_width"] for link in payload["writing_links"]))

    def test_welfare_writing_flow_applies_every_selected_link_position(self) -> None:
        method_source = inspect.getsource(main.KeywordApp._apply_bokjiro_welfare_to_writing)

        self.assertIn("for position in self._selected_welfare_link_positions()", method_source)
        self.assertIn("build_welfare_official_link(event, position=position)", method_source)


if __name__ == "__main__":
    unittest.main()
