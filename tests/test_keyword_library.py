from concurrent.futures import ThreadPoolExecutor
import csv
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack, closing
import queue
import sqlite3

from keyword_library import KeywordLibrary, keyword_key, read_keyword_file
import main


class KeywordLibraryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "keyword-library.sqlite3"
        self.store = KeywordLibrary(self.path)

    def test_bulk_500_keywords_persist_and_search_paginate(self):
        result = self.store.add([f"키워드 {i}" for i in range(500)] + ["키워드 0", "", "  "])
        self.assertEqual(result, {"added": 500, "duplicate": 1, "used": 0, "first_added_id": 1})
        reopened = KeywordLibrary(self.path)
        rows, total = reopened.list(offset=480, limit=40)
        self.assertEqual(total, 500)
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0]["keyword"], "키워드 480")
        self.assertEqual(reopened.counts()["pending"], 500)
        self.assertEqual(reopened.list(search="워드 49")[1], 11)

    def test_registration_locates_first_added_or_readded_pending_page(self):
        initial = self.store.add([f"키워드 {i}" for i in range(85)])
        self.assertEqual(self.store.pending_page_offset(initial["first_added_id"]), 0)
        added = self.store.add(["키워드 0", "새 키워드 1", "새 키워드 2"])
        offset = self.store.pending_page_offset(added["first_added_id"])
        self.assertEqual(offset, 80)
        self.assertIn("새 키워드 1", [row["keyword"] for row in self.store.list(offset=offset)[0]])
        self.store.change_status([1], "removed")
        restored = self.store.add(["키워드 0"])
        self.assertEqual(restored["first_added_id"], 1)
        self.assertEqual(self.store.pending_page_offset(restored["first_added_id"]), 0)
        self.assertIsNone(self.store.add(["키워드 0"])["first_added_id"])
        self.assertEqual(self.store.pending_page_offset(None), 0)

    def test_normalization_preserves_meaningful_numbers_and_punctuation(self):
        self.assertEqual(keyword_key(" ＡＢＣ   여행\t추천 "), "abc 여행 추천")
        self.assertEqual(self.store.add(["2026년 운세", "1. 여행", "A, B", "100%_확인"])["added"], 4)
        self.assertEqual(self.store.list(search="%_")[1], 1)

    def test_legacy_database_migration_preserves_keywords_statuses_and_history(self):
        self.store.add(["기존 대기", "기존 사용", "기존 삭제"])
        self.store.record_use("기존 사용", platform="tistory", url="https://example.com/old", event_key="old")
        self.store.change_status([3], "removed")
        # Model a v1.3.2 database, which has no group tables at all.
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TABLE keyword_group_membership")
            db.execute("DROP TABLE keyword_groups")
        migrated = KeywordLibrary(self.path)
        self.assertEqual(migrated.groups(), [])
        self.assertEqual(migrated.counts(group_id=0), {"pending": 1, "used": 1, "history": 1})
        self.assertEqual(migrated.list()[0][0]["group_name"], "미분류")
        self.assertEqual(migrated.list(history=True)[0][0]["url"], "https://example.com/old")
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM keywords").fetchone()[0], 3)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_group_names_are_normalized_unique_and_validated(self):
        group = self.store.create_group("  ＡＢＣ   여행 ")
        self.assertEqual(self.store.groups(), [{"id": group, "name": "ABC 여행"}])
        for name in ("abc 여행", "", "  ", "미분류", "전체 그룹", "이" * 41):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.create_group(name)
        self.assertEqual(len(self.store.groups()), 1)

    def test_group_add_filter_counts_and_page_location(self):
        health = self.store.create_group("건강")
        travel = self.store.create_group("여행")
        self.store.add(["미분류 하나", "미분류 둘"])
        self.store.add([f"건강 {i}" for i in range(85)], group_id=health)
        added = self.store.add(["건강 마지막"], group_id=health)
        self.store.add(["여행 키워드"], group_id=travel)
        self.assertEqual(self.store.counts()["pending"], 89)
        self.assertEqual(self.store.counts(group_id=0)["pending"], 2)
        self.assertEqual(self.store.counts(group_id=health)["pending"], 86)
        self.assertEqual(self.store.list(group_id=travel)[0][0]["keyword"], "여행 키워드")
        self.assertEqual(self.store.list(group_id=health, search="건강 8")[1], 6)
        offset = self.store.pending_page_offset(added["first_added_id"], group_id=health)
        self.assertEqual(offset, 80)
        self.assertIn("건강 마지막", [row["keyword"] for row in self.store.list(group_id=health, offset=offset)[0]])
        self.store.record_use("건강 0", platform="wordpress", event_key="post1")
        self.assertEqual(self.store.counts(group_id=health), {"pending": 85, "used": 1, "history": 1})
        self.assertEqual(self.store.list(group_id=travel, history=True)[1], 0)

    def test_global_duplicates_do_not_silently_move_between_groups_or_reuse_used_keyword(self):
        first = self.store.create_group("건강")
        second = self.store.create_group("건강 둘")
        self.store.add(["마그네슘"], group_id=first)
        self.assertEqual(self.store.add(["마그네슘"], group_id=second)["duplicate"], 1)
        self.assertEqual(self.store.list(group_id=second)[1], 0)
        with patch.object(main, "KEYWORD_LIBRARY_FILE", self.path):
            main.record_managed_keyword_publication("마그네슘", "tistory", "https://example.com/group", "그룹 테스트")
        self.assertEqual(self.store.add(["마그네슘"], group_id=second)["used"], 1)
        self.assertEqual(self.store.list(history=True, group_id=first)[1], 1)
        self.assertEqual(self.store.list(history=True, group_id=second)[1], 0)

    def test_move_rename_delete_groups_preserves_keywords_and_all_usage(self):
        first = self.store.create_group("그룹 하나")
        second = self.store.create_group("그룹 둘")
        self.store.add(["대기", "사용"], group_id=first)
        self.store.record_use("사용", platform="tistory", event_key="post1", url="https://example.com/1")
        self.store.record_use("사용", platform="wordpress", event_key="post2", url="https://example.com/2")
        self.assertEqual(self.store.assign_group([1, 2, 2, 999], second), 2)
        self.assertEqual(self.store.assign_group([1, 2], second), 0)
        self.assertEqual(self.store.counts(group_id=first), {"pending": 0, "used": 0, "history": 0})
        self.assertEqual(self.store.counts(group_id=second), {"pending": 1, "used": 1, "history": 2})
        self.store.rename_group(second, "새 이름")
        self.assertEqual(self.store.list(history=True)[0][0]["group_name"], "새 이름")
        with self.assertRaises(ValueError):
            self.store.rename_group(second, "그룹 하나")
        self.store.delete_group(second)
        self.assertEqual(self.store.counts(group_id=0), {"pending": 1, "used": 1, "history": 2})
        self.assertEqual({row["url"] for row in self.store.list(history=True)[0]},
                         {"https://example.com/1", "https://example.com/2"})
        self.assertGreater(self.store.create_group("새 그룹"), second)

    def test_assign_ungrouped_and_readd_removed_to_selected_group(self):
        group = self.store.create_group("그룹")
        self.store.add(["재등록"], group_id=group)
        self.assertEqual(self.store.assign_group([1], 0), 1)
        self.assertEqual(self.store.list(group_id=0)[1], 1)
        self.store.change_status([1], "removed")
        self.store.add(["재등록"], group_id=group)
        self.assertEqual(self.store.list(group_id=group)[1], 1)
        self.assertEqual(KeywordLibrary(self.path).list()[0][0]["group_id"], group)

    def test_invalid_group_operations_are_atomic(self):
        self.store.add(["기존"])
        for operation in (lambda: self.store.add(["신규"], group_id=999),
                          lambda: self.store.assign_group([1], 999)):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(self.store.counts(group_id=0)["pending"], 1)
        self.assertEqual(self.store.list()[0][0]["keyword"], "기존")

    def test_group_export_includes_group_name_and_only_selected_group(self):
        group = self.store.create_group("그룹 ' %_ 이름")
        self.store.add(["다른 그룹"])
        self.store.add(["대기", "사용"], group_id=group)
        self.store.record_use("사용", platform="tistory", event_key="1")
        text = Path(self.directory.name) / "group.txt"
        self.store.export(text, group_id=group)
        self.assertEqual(read_keyword_file(text), ["대기"])
        path = text.with_suffix(".csv")
        self.store.export(path, group_id=group, history=True)
        with path.open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([(row["keyword"], row["group_name"]) for row in rows], [("사용", "그룹 ' %_ 이름")])

    def test_success_moves_to_history_with_title_url_platform_and_time(self):
        self.store.add(["마그네슘 효능"])
        self.assertTrue(self.store.record_use("마그네슘 효능", platform="tistory", url="https://example.com/1",
                                              title="마그네슘 설명", event_key="tistory:1"))
        self.assertEqual(self.store.list()[1], 0)
        history, total = self.store.list(history=True)
        self.assertEqual(total, 1)
        self.assertEqual(history[0]["platform"], "tistory")
        self.assertEqual(history[0]["title"], "마그네슘 설명")
        self.assertEqual(history[0]["url"], "https://example.com/1")
        self.assertTrue(history[0]["used_at"])
        self.assertEqual(self.store.add(["마그네슘 효능"])["used"], 1)

    def test_restore_keeps_history_and_ignores_delayed_duplicate_event(self):
        self.store.add(["여행"])
        self.store.record_use("여행", platform="wordpress", event_key="post1")
        history, _ = self.store.list(history=True)
        self.store.change_status([history[0]["item_id"]], "pending")
        self.assertFalse(self.store.record_use("여행", platform="wordpress", event_key="post1"))
        self.assertEqual(self.store.counts(), {"pending": 1, "used": 0, "history": 1})
        self.store.record_use("여행", platform="blogspot", event_key="post2")
        self.assertEqual(self.store.counts(), {"pending": 0, "used": 1, "history": 2})

    def test_removed_keywords_do_not_consume_and_can_be_added_again(self):
        self.store.add(["여행"])
        row = self.store.list()[0][0]
        self.store.change_status([row["id"]], "removed")
        self.assertFalse(self.store.record_use("여행", platform="manual", event_key="1"))
        self.assertEqual(self.store.add(["여행"])["added"], 1)
        self.assertEqual(self.store.counts()["pending"], 1)

    def test_unregistered_keyword_does_not_create_history(self):
        self.assertFalse(self.store.record_use("미등록", platform="wordpress", event_key="1"))
        self.assertEqual(self.store.counts()["history"], 0)

    def test_multiple_workers_same_publication_are_idempotent(self):
        self.store.add(["여행"])
        def consume(_):
            return KeywordLibrary(self.path).record_use("여행", platform="wordpress", event_key="same")
        with ThreadPoolExecutor(max_workers=8) as pool:
            result = list(pool.map(consume, range(20)))
        self.assertEqual(sum(result), 1)
        self.assertEqual(self.store.counts()["history"], 1)

    def test_invalid_import_is_atomic(self):
        with self.assertRaises(ValueError):
            self.store.add(["정상", "x" * 501])
        self.assertEqual(self.store.counts()["pending"], 0)

    def test_txt_utf8_bom_cp949_and_csv_column_import(self):
        for encoding in ("utf-8-sig", "cp949"):
            path = Path(self.directory.name) / "목록.txt"
            path.write_text("마그네슘 효능\n2026년 운세\n", encoding=encoding)
            self.assertEqual(read_keyword_file(path), ["마그네슘 효능", "2026년 운세"])
        path = Path(self.directory.name) / "목록.csv"
        path.write_text('분류,키워드,검색량\n건강,"비타민, 마그네슘",100\n건강,철분,200\n', encoding="utf-8-sig")
        self.assertEqual(read_keyword_file(path), ["비타민, 마그네슘", "철분"])

    def test_export_full_pending_and_history_roundtrip(self):
        self.store.add([f"키워드 {i}" for i in range(500)])
        self.store.record_use("키워드 0", platform="tistory", event_key="1")
        path = Path(self.directory.name) / "대기.txt"
        self.store.export(path)
        self.assertEqual(len(read_keyword_file(path)), 499)
        path = path.with_suffix(".csv")
        self.store.export(path, history=True)
        with path.open(encoding="utf-8-sig") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["platform"], "tistory")
        self.assertEqual(read_keyword_file(path), ["키워드 0"])

    def test_original_keyword_not_derived_seo_keyword_is_consumed(self):
        self.store.add(["마그네슘 효능", "마그네슘"])
        with patch.object(main, "KEYWORD_LIBRARY_FILE", self.path):
            main.record_managed_keyword_publication("마그네슘 효능", "wordpress", "https://example.com/1", "제목")
        self.assertEqual(self.store.list()[0][0]["keyword"], "마그네슘")

    def test_missing_or_broken_library_never_breaks_publication(self):
        path = Path(self.directory.name) / "missing.sqlite3"
        with patch.object(main, "KEYWORD_LIBRARY_FILE", path):
            main.record_managed_keyword_publication("여행", "wordpress", "https://example.com/1", "제목")
        self.assertFalse(path.exists())
        path.write_bytes(b"not sqlite")
        with patch.object(main, "KEYWORD_LIBRARY_FILE", path), patch.object(main, "append_runtime_log") as log:
            main.record_managed_keyword_publication("여행", "wordpress", "https://example.com/1", "제목")
            self.assertIn("이력 저장 실패", log.call_args.args[1])

    def test_worker_records_confirmed_platform_before_followup_work(self):
        source = inspect.getsource(main.PublishPipelineWorker.run)
        self.assertIn('wordpress_result.get("status") == "publish"', source)
        self.assertIn('blogspot_result.get("manual_completed")', source)
        self.assertLess(source.index('self.source_keyword, "wordpress"'), source.index('update_rank_math_metadata'))
        self.assertLess(source.index('self.source_keyword, "wordpress"'), source.index('run_blogspot_playwright_automation'))
        self.assertIn('"source_keyword": self.source_keyword', source)
        source = inspect.getsource(main.TistoryAutomationWorker.run)
        self.assertIn('done_payload["save_mode"] == TISTORY_SAVE_MODE_PUBLISH', source)
        self.assertIn('done_payload["published_url"] or done_payload["manual_completed"]', source)

    def test_loading_keyword_does_not_start_workers(self):
        source = inspect.getsource(main.KeywordApp._use_managed_keyword)
        for forbidden in ("start_direct_keyword_writing(", "_collect_reference_for_selected_keyword(",
                          "_run_publish_pipeline(", "_arm_writing_auto_progress("):
            self.assertNotIn(forbidden, source)
        self.assertIn("_writing_keyword_selection_locked", source)
        self.assertIn('self._switch_page(destination)', source)

    def test_pipeline_draft_failure_review_and_partial_success(self):
        cases = (
            ("wordpress", "publish", False, False, 1),
            ("wordpress", "draft", False, False, 0),
            ("wordpress", "publish", True, False, 0),
            ("blogspot", "publish", False, False, 1),
            ("blogspot", "draft", False, False, 0),
            ("blogspot", "manual", False, True, 1),
            ("blogspot", "publish", True, False, 0),
            # WordPress already succeeded: a subsequent Blogger failure must
            # not put that used keyword back in the pending inbox.
            ("both", "publish", True, False, 1),
        )
        for index, (platform, status, fail, manual, expected) in enumerate(cases):
            with self.subTest(platform=platform, status=status, fail=fail, manual=manual), ExitStack() as stack:
                keyword = f"원래 키워드 {index}"
                self.store.add([keyword])
                stack.enter_context(patch.object(main, "KEYWORD_LIBRARY_FILE", self.path))
                stack.enter_context(patch.object(main, "append_runtime_log"))
                stack.enter_context(patch.object(main.DailyPublishLimitStore, "ensure_can_publish"))
                wp = stack.enter_context(patch.object(main, "WordPressClient"))
                wp.return_value.publish_post.return_value = {"status": status, "link": f"https://example.com/wp/{index}", "post_id": index + 1}
                if fail and platform == "wordpress":
                    wp.return_value.publish_post.side_effect = RuntimeError("failed")
                blogger = stack.enter_context(patch.object(main, "run_blogspot_playwright_automation"))
                blogger.return_value = {"status": status, "manual_completed": manual, "link": f"https://example.com/blogger/{index}"}
                if fail and platform != "wordpress":
                    blogger.side_effect = RuntimeError("failed")
                settings = main.WordPressSettings(target_platforms=["wordpress", "blogspot"] if platform == "both" else [platform],
                    blog_url="https://example.com", username="test", app_password="test", cardnews_enabled=False,
                    post_mode="임시저장" if status == "draft" else "공개발행", threads_auto_publish=False)
                events = queue.Queue()
                worker = main.PublishPipelineWorker(settings, "제목", "<p>본문</p>", "", "별도 SEO 키워드", [], "", "", events,
                                                     source_keyword=keyword)
                worker.run()
                history, _ = self.store.list(history=True, search=keyword)
                self.assertEqual(len(history), expected)
                if expected:
                    self.assertEqual(history[0]["keyword"], keyword)
                elif not fail:
                    self.assertIn("publish_pipeline_done", [event[0] for event in events.queue])

    def test_tistory_consumes_only_confirmed_public_or_manual_success(self):
        for index, (mode, url, manual, success, expected) in enumerate((
            (main.TISTORY_SAVE_MODE_PUBLISH, "https://example.com/1", False, True, 1),
            (main.TISTORY_SAVE_MODE_PUBLISH, "", True, True, 1),
            (main.TISTORY_SAVE_MODE_DRAFT, "", False, True, 0),
            (main.TISTORY_SAVE_MODE_PUBLISH, "", False, True, 0),
            (main.TISTORY_SAVE_MODE_PUBLISH, "", False, False, 0),
        )):
            with self.subTest(mode=mode, url=url, manual=manual, success=success), ExitStack() as stack:
                keyword = f"티스토리 원래 키워드 {index}"
                self.store.add([keyword])
                stack.enter_context(patch.object(main, "KEYWORD_LIBRARY_FILE", self.path))
                stack.enter_context(patch.object(main, "GOOGLE_IMAGE_COLLAGE_ENABLED", False))
                stack.enter_context(patch.object(main, "append_runtime_log"))
                stack.enter_context(patch.object(main, "cleanup_tistory_automation_files"))
                for method in ("reserve_publish", "record_reserved_success", "cancel_reservation"):
                    stack.enter_context(patch.object(main.DailyPublishLimitStore, method, return_value=1))
                stack.enter_context(patch.object(main, "run_tistory_playwright_automation", return_value=(success,
                    {"message": "test", "published_url": url, "manual_completed": manual, "save_mode": mode})))
                main.TistoryAutomationWorker("제목", "<p>본문</p>", queue.Queue(), auto_publish=not manual,
                    save_mode=mode, public_blog_url="https://keyword-test.tistory.com", source_keyword=keyword).run()
                self.assertEqual(self.store.list(history=True, search=keyword)[1], expected)


if __name__ == "__main__":
    unittest.main()
