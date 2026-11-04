"""Optional photo keywords survive bulk import, editing and legacy upgrades."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from keyword_library import KeywordLibrary, parse_keyword_entries, read_keyword_entries, read_keyword_file


class KeywordWikiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / 'keywords.sqlite3'
        self.store = KeywordLibrary(self.path)

    def row(self, keyword):
        return next(row for row in self.store.list(limit=1000)[0] if row['keyword'] == keyword)

    def legacy(self):
        group = self.store.create_group('건강')
        self.store.add(['기존 대기', '기존 사용', '기존 삭제'], group_id=group)
        self.store.record_use('기존 사용', platform='tistory', event_key='old-post')
        self.store.change_status([3], 'removed')
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('ALTER TABLE keywords DROP COLUMN wiki_keyword')
            db.commit()
            before = db.execute('SELECT * FROM keywords ORDER BY id').fetchall()
        return group, before

    def test_comma_pairs_optional_queries_and_quoted_commas(self):
        entries = parse_keyword_entries('마그네슘 효능, 마그네슘\n철분 부족\n"비타민, 미네랄","영양소, 무기질"\n')
        self.assertEqual(entries, [{'keyword': '마그네슘 효능', 'wiki_keyword': '마그네슘'},
            {'keyword': '철분 부족'}, {'keyword': '비타민, 미네랄', 'wiki_keyword': '영양소, 무기질'}])
        self.store.add_entries(entries)
        self.assertEqual(self.row('철분 부족')['wiki_keyword'], '')
        self.assertEqual(self.row('비타민, 미네랄')['wiki_keyword'], '영양소, 무기질')

    def test_500_optional_pairs_persist_without_changing_article_identity(self):
        text = '\n'.join(f'2026 키워드 {i}, 위키 {i}' if i % 2 else f'2026 키워드 {i}' for i in range(500))
        result = self.store.add_entries(parse_keyword_entries(text))
        self.assertEqual(result['added'], 500)
        rows, total = KeywordLibrary(self.path).list(limit=500)
        self.assertEqual(total, 500)
        for i, row in enumerate(rows):
            self.assertEqual(row['keyword'], f'2026 키워드 {i}')
            self.assertEqual(row['wiki_keyword'], f'위키 {i}' if i % 2 else '')

    def test_invalid_csv_or_empty_article_never_partially_registers(self):
        for text in ('정상, 검색어\n잘못, 셋, 칸', '정상, 검색어\n"닫히지 않은 문구', ',검색어'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.store.add_entries(parse_keyword_entries(text))
        self.assertEqual(self.store.counts()['pending'], 0)
        with self.assertRaises(ValueError):
            self.store.add_entries([{'keyword': '정상'}, {'keyword': '', 'wiki_keyword': '검색어'}])

    def test_validation_before_bulk_write_includes_photo_query_length(self):
        with self.assertRaises(ValueError):
            self.store.add_entries([{'keyword': '정상'}, {'keyword': '잘못', 'wiki_keyword': '가' * 501}])
        self.assertEqual(self.store.counts()['pending'], 0)

    def test_utf8_and_cp949_files_and_named_columns(self):
        for encoding in ('utf-8-sig', 'cp949'):
            path = self.root / 'pairs.txt'
            path.write_text('마그네슘 효능,마그네슘\n철분 부족\n', encoding=encoding)
            self.assertEqual(read_keyword_entries(path)[0]['wiki_keyword'], '마그네슘')
            self.assertEqual(read_keyword_file(path), ['마그네슘 효능', '철분 부족'])
        path = self.root / 'pairs.csv'
        path.write_text('분류,키워드,위키 검색어,검색량\n건강,마그네슘 효능,마그네슘,100\n', encoding='utf-8-sig')
        self.assertEqual(read_keyword_entries(path), [{'keyword': '마그네슘 효능', 'wiki_keyword': '마그네슘'}])
        for header in ('위키키워드', '사진 검색어', 'wiki_keyword'):
            self.assertEqual(parse_keyword_entries(f'키워드,{header}\n마그네슘 효능,마그네슘'),
                             [{'keyword': '마그네슘 효능', 'wiki_keyword': '마그네슘'}])

    def test_legacy_csv_group_column_is_not_mistaken_for_wiki_query(self):
        path = self.root / 'legacy.csv'
        path.write_text('keyword,group_name,added_at\n"비타민, 미네랄",건강,2026-01-01\n', encoding='utf-8')
        self.assertEqual(read_keyword_entries(path), [{'keyword': '비타민, 미네랄'}])
        self.store.add(['A, B'])  # The existing string API keeps commas literal.
        self.assertEqual(self.row('A, B')['wiki_keyword'], '')

    def test_csv_and_txt_export_roundtrip_optional_pairs_and_commas(self):
        entries = parse_keyword_entries('"비타민, 미네랄","영양소, 무기질"\n철분 부족\n')
        self.store.add_entries(entries)
        for suffix in ('.txt', '.csv'):
            path = self.root / ('export' + suffix)
            self.store.export(path)
            restored = KeywordLibrary(self.root / ('new' + suffix + '.sqlite3'))
            restored.add_entries(read_keyword_entries(path))
            self.assertEqual([(r['keyword'], r['wiki_keyword']) for r in restored.list()[0]],
                             [('비타민, 미네랄', '영양소, 무기질'), ('철분 부족', '')])

    def test_existing_query_updates_only_in_selected_scope_and_blank_preserves_it(self):
        first, second = self.store.create_group('건강'), self.store.create_group('다른 그룹')
        self.store.add(['기존'], group_id=first)
        entry = [{'keyword': '기존', 'wiki_keyword': '검색어'}]
        self.assertEqual(self.store.add_entries(entry, group_id=second)['other_group'], 1)
        self.assertEqual(self.row('기존')['wiki_keyword'], '')
        result = self.store.add_entries(entry, group_id=first)
        self.assertEqual((result['added'], result['wiki_updated'], result['first_added_id']), (0, 1, 1))
        self.store.add_entries([{'keyword': '기존', 'wiki_keyword': ''}], group_id=first)
        self.assertEqual(self.row('기존')['wiki_keyword'], '검색어')
        self.store.record_use('기존', platform='tistory', event_key='post')
        result = self.store.add_entries([{'keyword': '기존', 'wiki_keyword': '바꾸면 안 됨'}])
        self.assertEqual(result['used'], 1)
        self.assertEqual(self.store.list(history=True)[0][0]['wiki_keyword'], '검색어')

    def test_single_row_edit_clear_and_stale_editor_rejection(self):
        self.store.add(['기존'])
        old = self.row('기존')
        self.assertTrue(self.store.update_wiki_keyword(old, ' 새 검색어 '))
        with self.assertRaises(ValueError):
            self.store.update_wiki_keyword(old, '오래된 편집')
        self.assertEqual(self.row('기존')['wiki_keyword'], '새 검색어')
        self.store.update_wiki_keyword(self.row('기존'), '')
        self.assertEqual(self.row('기존')['wiki_keyword'], '')

    def test_row_edit_cannot_cross_a_group_change_or_publication(self):
        self.store.add(['기존'])
        row = self.row('기존')
        group = self.store.create_group('그룹')
        self.store.assign_group([1], group)
        with self.assertRaises(ValueError):
            self.store.update_wiki_keyword(row, '오래된 그룹 편집')
        row = self.row('기존')
        self.store.record_use('기존', platform='tistory', event_key='post')
        with self.assertRaises(ValueError):
            self.store.update_wiki_keyword(row, '발행 후 편집')

    def test_changed_query_invalidates_old_delete_confirmation_and_survives_undo(self):
        self.store.add_entries([{'keyword': '마그네슘 효능', 'wiki_keyword': '미네랄'}])
        old = self.row('마그네슘 효능')
        self.store.update_wiki_keyword(old, '마그네슘')
        with self.assertRaises(ValueError):
            self.store.remove_selected([old])
        batch = self.store.remove_selected([self.row('마그네슘 효능')])
        self.store.restore_removal(batch['id'])
        self.assertEqual(self.row('마그네슘 효능')['wiki_keyword'], '마그네슘')

    def test_legacy_upgrade_backs_up_before_alter_and_preserves_ids_groups_and_usage(self):
        group, before = self.legacy()
        migrated = KeywordLibrary(self.path)
        self.assertEqual(migrated.counts(group_id=group), {'pending': 1, 'used': 1, 'history': 1})
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT id,key,keyword,added_at,status FROM keywords ORDER BY id').fetchall(), before)
            self.assertEqual(db.execute("SELECT count(*) FROM keywords WHERE wiki_keyword='' ").fetchone()[0], 3)
        backups = list((self.root / 'Keyword Backups').glob('before-wiki-column-*.sqlite3'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertNotIn('wiki_keyword', [row[1] for row in db.execute('PRAGMA table_info(keywords)')])
            self.assertEqual(db.execute('SELECT * FROM keywords ORDER BY id').fetchall(), before)
        if os.name != 'nt':
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        KeywordLibrary(self.path)
        self.assertEqual(len(list((self.root / 'Keyword Backups').glob('*.sqlite3'))), 1)

    def test_backup_failure_leaves_legacy_schema_and_data_untouched(self):
        _, before = self.legacy()
        with patch.object(KeywordLibrary, '_backup_before_wiki_migration', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                KeywordLibrary(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT * FROM keywords ORDER BY id').fetchall(), before)
            self.assertNotIn('wiki_keyword', [row[1] for row in db.execute('PRAGMA table_info(keywords)')])

    def test_concurrent_legacy_upgrades_never_duplicate_column_or_lose_rows(self):
        _, before = self.legacy()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: KeywordLibrary(self.path), range(4)))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT id,key,keyword,added_at,status FROM keywords ORDER BY id').fetchall(), before)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')


if __name__ == '__main__':
    unittest.main()
