"""Offline regression tests for V2 through the ORIGINAL publishing pipeline."""
import queue
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

import main
import tistory_v2_publish as prepare
from tistory_v2 import Cancelled, V2Store, render_slide


BODY = "".join(f"<h2>검토한 소제목 {i}</h2><p>직접 수정한 최종 내용 {i}</p>" for i in range(10))


class SlidePreparationTests(unittest.TestCase):
    def test_custom_size_snapshot_is_exported_and_wrong_size_still_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = V2Store(root)
            deck = store.new_deck("서울", "제목", BODY, 10, "민트")
            deck["slide_size"] = [900, 600]
            deck["slides"][0]["slide_size"] = [600, 900]
            result = prepare.export_edited_slide_deck(deck, "제목", BODY, "서울", root)
            self.assertEqual(result["slide_size"], [900, 600])
            self.assertEqual(result["slide_sizes"], [[600, 900]] + [[900, 600]] * 9)
            for index, path in enumerate(result["paths"]):
                with Image.open(path) as image:
                    self.assertEqual(image.size, (600, 900) if index == 0 else (900, 600))
            Image.new("RGB", (600, 300), "white").save(result["paths"][-1])
            with patch.object(prepare, "export_deck", return_value=result["paths"]), \
                 self.assertRaisesRegex(ValueError, "900×600"):
                prepare.export_edited_slide_deck(deck, "제목", BODY, "서울", root)

    def test_edited_deck_export_preserves_text_photo_theme_without_regeneration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = V2Store(root)
            deck = store.new_deck("서울", "제목", BODY, 10, "스카이")
            photo = root / "chosen.png"
            Image.new("RGB", (200, 100), "red").save(photo)
            deck["slides"][0].update(title="직접 고친 문구", summary="수정한 설명", bullets=["내가 넣은 항목"],
                                      image={"path": str(photo), "source": "내 이미지"}, layout="사진 왼쪽",
                                      text_horizontal="오른쪽", text_vertical="아래")
            with patch.object(prepare, "collect_wiki_images") as collect, \
                 patch.object(V2Store, "new_deck") as create:
                result = prepare.export_edited_slide_deck(deck, "제목", BODY, "서울", root)
            collect.assert_not_called()
            create.assert_not_called()
            self.assertEqual(result["deck"], deck)
            self.assertEqual(len(result["paths"]), 10)
            with Image.open(result["paths"][0]) as saved:
                expected = render_slide(deck["slides"][0], deck["theme"], 1, 10)
                self.assertEqual(saved.convert("RGB").tobytes(), expected.convert("RGB").tobytes())

    def test_stale_source_never_silently_overwrites_edits(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deck = V2Store(root).new_deck("서울", "제목", BODY, 10, "스카이")
            for title, body, keyword in (("다른 제목", BODY, "서울"), ("제목", BODY + "추가", "서울"), ("제목", BODY, "부산")):
                with patch.object(prepare, "export_deck") as export:
                    with self.assertRaisesRegex(ValueError, "현재 글"):
                        prepare.export_edited_slide_deck(deck, title, body, keyword, root)
                    export.assert_not_called()

    def test_final_edited_body_ten_pngs_first_two_photos_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            photo = root / "photo.png"
            Image.new("RGB", (100, 100), "red").save(photo)
            assets = [{"path": str(photo), "source": "위키백과", "source_page": "https://ko.wikipedia.org/wiki/서울"},
                      {"path": str(photo), "source": "나무위키", "source_page": "https://namu.wiki/w/서울"}]
            with patch.object(prepare, "collect_wiki_images", return_value=(assets, [])) as collect:
                result = prepare.prepare_slide_deck("수정한 제목", BODY, "서울", root / "decks", "/fake/chrome")
            self.assertEqual(collect.call_args.args[0], "서울")
            self.assertEqual(result["article_html"], BODY)
            self.assertEqual(len(result["paths"]), 10)
            for path in result["paths"]:
                with Image.open(path) as image:
                    self.assertEqual(image.size, (600, 300))
                    self.assertEqual(image.format, "PNG")
            slides = result["deck"]["slides"]
            self.assertTrue(all(s["image"] for s in slides[:2]))
            self.assertTrue(all(s["image"] is None for s in slides[2:]))
            self.assertIn("https://ko.wikipedia.org/wiki/서울", result["source_credits_html"])
            self.assertEqual([s["title"] for s in slides], [f"검토한 소제목 {i}" for i in range(10)])

    def test_wiki_failure_uses_common_style_but_render_failure_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(prepare, "collect_wiki_images", side_effect=RuntimeError("사진 없음")):
                result = prepare.prepare_slide_deck("제목", BODY, "서울", Path(temporary), "/fake/chrome")
            self.assertEqual(len(result["paths"]), 10)
            self.assertTrue(result["warnings"])
            self.assertTrue(all(s["image"] is None for s in result["deck"]["slides"]))
            with patch.object(prepare, "collect_wiki_images", return_value=([], [])), \
                 patch.object(prepare, "export_deck", side_effect=OSError("디스크 오류")):
                with self.assertRaisesRegex(OSError, "디스크"):
                    prepare.prepare_slide_deck("제목", BODY, "서울", Path(temporary), "/fake/chrome")

    def test_only_generated_figures_removed_not_authored_content(self):
        authored = '<figure><img src="https://example.com/authored.png"><figcaption>직접 넣은 사진</figcaption></figure>'
        generated = '<figure class="blog-helper-cardnews-image"><img src="old.png"></figure>'
        self.assertEqual(prepare._without_generated_figures(BODY + authored + generated), BODY + authored)

    def test_cancel_is_not_treated_as_missing_photo(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(prepare, "collect_wiki_images", side_effect=Cancelled("취소")):
            with self.assertRaises(Cancelled):
                prepare.prepare_slide_deck("제목", BODY, "서울", Path(temporary), "/fake/chrome")


class OriginalPipelineTests(unittest.TestCase):
    def test_publish_worker_uses_editor_snapshot_not_new_generated_slides(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            deck = V2Store(root / "Tistory V2").new_deck("서울", "제목", BODY, 10, "스카이")
            deck["slides"][0]["title"] = "편집해서 확정한 제목"
            from tistory_v2 import assign_deck_themes
            assign_deck_themes(deck, "랜덤")
            colors = [slide["color_theme"] for slide in deck["slides"]]
            deck["slide_size"] = [900, 600]
            deck["slides"][1]["slide_size"] = [600, 900]
            settings = main.WordPressSettings(target_platforms=["tistory"],
                tistory_blog_url="https://example.tistory.com", tistory_auto_publish=False, threads_auto_publish=False)
            stack.enter_context(patch.object(main, "DATA_DIR", root))
            stack.enter_context(patch.object(main, "TISTORY_DRAFT_FILE", root / "draft.html"))
            regenerate = stack.enter_context(patch.object(prepare, "prepare_slide_deck", side_effect=AssertionError("재생성 금지")))
            events = queue.Queue()
            worker = main.PublishPipelineWorker(settings, "제목", BODY, "", "서울", [], "slug", "설명", events,
                                                tistory_v2=True, tistory_v2_keyword="서울", tistory_v2_deck=deck)
            deck["slides"][0]["title"] = "업로드 요청 후 바꾼 값"
            worker.run()
            regenerate.assert_not_called()
            payload = next(p for kind, p in events.queue if kind == "publish_pipeline_done")["tistory"]
            self.assertEqual(len(payload["tistory_v2_slide_paths"]), 10)
            self.assertEqual(payload["tistory_v2_slide_size"], [900, 600])
            self.assertEqual(payload["tistory_v2_slide_sizes"], [[900, 600], [600, 900]] + [[900, 600]] * 8)
            self.assertEqual(worker.tistory_v2_deck["slides"][0]["title"], "편집해서 확정한 제목")
            saved = V2Store(root / "Tistory V2").load_deck(deck["id"])
            self.assertEqual(saved["slides"][0]["title"], "편집해서 확정한 제목")
            self.assertEqual(saved["slide_size"], [900, 600])
            self.assertEqual(saved["theme"], "랜덤")
            self.assertEqual([slide["color_theme"] for slide in saved["slides"]], colors)

    def test_v2_payload_keeps_selected_profile_publish_settings_and_credits(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            paths = [root / f"slide-{i}.png" for i in range(10)]
            settings = main.WordPressSettings(target_platforms=["tistory"], tistory_blog_url="https://second.tistory.com",
                tistory_profiles=[{}, {"blog_url": "https://second.tistory.com"}], tistory_active_profile="티스토리 2",
                tistory_auto_publish=False, tistory_daily_publish_limit=15, threads_auto_publish=False)
            stack.enter_context(patch.object(main, "DATA_DIR", root))
            stack.enter_context(patch.object(main, "TISTORY_DRAFT_FILE", root / "draft.html"))
            stack.enter_context(patch.object(main, "require_google_chrome_executable", return_value=Path("/fake/chrome")))
            prepare_mock = stack.enter_context(patch.object(prepare, "prepare_slide_deck", return_value={
                "paths": paths, "article_html": BODY, "source_credits_html": "<p>사진 출처</p>"}))
            events = queue.Queue()
            worker = main.PublishPipelineWorker(settings, "제목", BODY, "", "서울", [], "slug", "설명", events,
                                                tistory_v2=True, tistory_v2_keyword="서울")
            worker.run()
            payload = next(p for kind, p in events.queue if kind == "publish_pipeline_done")["tistory"]
            self.assertEqual(payload["html"], BODY)
            self.assertEqual(payload["tistory_v2_slide_paths"], list(map(str, paths)))
            self.assertEqual(payload["tistory_v2_source_credits"], "<p>사진 출처</p>")
            self.assertEqual(payload["profile_scope"], main.TISTORY_PROFILE_SCOPES[1])
            self.assertFalse(payload["auto_publish"])
            self.assertEqual(payload["daily_publish_limit"], 15)
            prepare_mock.assert_called_once()
            events = queue.Queue()
            main.PublishPipelineWorker(settings, "제목", BODY, "", "서울", [], "slug", "설명", events).run()
            prepare_mock.assert_called_once()  # Original mode never generates V2 slides.

    def test_worker_skips_legacy_photos_but_keeps_quota_and_tag_logic(self):
        events = queue.Queue()
        paths = [f"/slides/{i}.png" for i in range(10)]
        with ExitStack() as stack:
            collect = stack.enter_context(patch.object(main, "collect_tistory_reference_image_files"))
            stack.enter_context(patch.object(main, "extract_tistory_prompt_tags", return_value=(BODY, ["서울", "여행"])))
            stack.enter_context(patch.object(main, "prepare_tistory_native_attachment_html", side_effect=lambda body, _: (body, {})))
            build = stack.enter_context(patch.object(main, "build_tistory_editor_automation_script", return_value="script"))
            run = stack.enter_context(patch.object(main, "run_tistory_playwright_automation", return_value=(True, {
                "published_url": "https://example.tistory.com/1", "save_mode": main.TISTORY_SAVE_MODE_PUBLISH})))
            reserve = stack.enter_context(patch.object(main.DailyPublishLimitStore, "reserve_publish", return_value="reserved"))
            record = stack.enter_context(patch.object(main.DailyPublishLimitStore, "record_reserved_success", return_value=1))
            stack.enter_context(patch.object(main.DailyPublishLimitStore, "cancel_reservation"))
            for name in ("append_runtime_log", "cleanup_tistory_automation_files", "cleanup_generated_upload_images"):
                stack.enter_context(patch.object(main, name))
            main.TistoryAutomationWorker("제목", BODY, events, publish_after_input=True,
                write_url="https://example.tistory.com/manage/newpost", public_blog_url="https://example.tistory.com",
                daily_publish_limit=15, tistory_v2_slide_paths=paths, tistory_v2_source_credits="<p>출처</p>").run()
            collect.assert_not_called()
            reserve.assert_called_once_with("tistory", "https://example.tistory.com", 15)
            record.assert_called_once_with("tistory", "https://example.tistory.com", "reserved")
            self.assertTrue(build.call_args.args[1].endswith("<p>출처</p>"))
            self.assertEqual(run.call_args.kwargs["tag_names"], ["서울", "여행"])
            self.assertEqual(run.call_args.kwargs["tistory_v2_slide_paths"], paths)

    def _editor_run(self, *, collage_ok=True, ads=False):
        order = []
        page, context = MagicMock(), MagicMock()
        page.url = "https://example.tistory.com/manage/newpost"
        page.is_closed.return_value = False
        context.pages = [page]
        page.evaluate.side_effect = lambda script, *args: (
            order.append("body") or {"titleOk": True, "bodyOk": True}
            if script == "body-script" else 2 if args else None)
        with ExitStack() as stack:
            stack.enter_context(patch("playwright.sync_api.sync_playwright", return_value=MagicMock()))
            stack.enter_context(patch.object(main, "launch_tistory_persistent_context", return_value=context))
            stack.enter_context(patch.object(main, "load_tistory_storage_state", return_value={}))
            stack.enter_context(patch.object(main, "save_tistory_storage_state"))
            attention_wait = stack.enter_context(patch("tistory_v2_editor.wait_for_failed_editor"))
            stack.enter_context(patch.object(main, "append_runtime_log"))
            stack.enter_context(patch.object(main, "wait_for_tistory_editor", return_value=(page, "")))
            stack.enter_context(patch.object(main, "collect_tistory_hosted_image_urls", return_value=[]))
            def collage(*args, **kwargs):
                order.append("collage")
                self.assertEqual(len(args[1]), 10)
                self.assertEqual(args[2], "")  # Thumbnail already added by original body input.
                self.assertEqual(kwargs["expected_size"], [900, 600])
                self.assertEqual(kwargs["expected_sizes"], [[900, 600], [600, 900]] * 5)
                return {"ok": collage_ok, "slide_count": 10, "collage_count": 5 if collage_ok else 4}
            stack.enter_context(patch("tistory_v2_editor.insert_native_slide_pairs", side_effect=collage))
            stack.enter_context(patch.object(main, "enter_tistory_tags_native", side_effect=lambda *args: order.append("tags")))
            draft = stack.enter_context(patch.object(main, "click_tistory_draft_save_native", side_effect=lambda *args: order.append("draft") or True))
            publish = stack.enter_context(patch.object(main, "click_tistory_public_publish_native"))
            def run():
                return main.run_tistory_playwright_automation(page.url, "body-script", queue.Queue(),
                    save_mode=main.TISTORY_SAVE_MODE_DRAFT, tistory_v2_slide_paths=[f"/{i}.png" for i in range(10)],
                    tistory_v2_slide_size=[900, 600],
                    tistory_v2_slide_sizes=[[900, 600], [600, 900]] * 5,
                    tistory_v2_ads={"code": '<ins class="adsbygoogle"></ins>', "count": 2} if ads else None)
            if collage_ok:
                success, _ = run()
                self.assertTrue(success)
                self.assertEqual(order, ["body", "collage", "tags", "draft"])
                attention_wait.assert_not_called()
            else:
                with self.assertRaisesRegex(RuntimeError, "콜라주"):
                    run()
                self.assertEqual(order, ["body", "collage"])
                draft.assert_not_called()
                attention_wait.assert_called_once_with(page)
            publish.assert_not_called()
            context.close.assert_called_once()

    def test_native_collages_finish_before_tags_and_draft(self):
        self._editor_run()

    def test_incomplete_collages_prevent_all_saving(self):
        self._editor_run(collage_ok=False)

    def test_ads_keep_native_groups_before_draft(self):
        self._editor_run(ads=True)


if __name__ == "__main__":
    unittest.main()
