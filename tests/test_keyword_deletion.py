"""Deletion is scoped to a confirmed snapshot; audit and undo are atomic."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from keyword_library import KeywordLibrary


class KeywordDeletionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'keywords.sqlite3'
        self.store = KeywordLibrary(self.path)
        self.mbti = self.store.create_group('MBTI')
        self.health = self.store.create_group('건강')
        self.store.add([f'INTP 궁합 {i:02d}' for i in range(45)], group_id=self.mbti)
        self.store.add(['건강 하나', '건강 둘', '건강 셋'], group_id=self.health)
        self.rows = self.store.list(group_id=self.mbti)[0]

    def statuses(self):
        with closing(sqlite3.connect(self.path)) as db:
            return dict(db.execute('SELECT id,status FROM keywords'))

    def test_nonadjacent_selection_changes_only_confirmed_ids_and_records_exact_batch(self):
        selected = [self.rows[0], self.rows[4]]
        batch = self.store.remove_selected(selected, group_id=self.mbti)
        self.assertEqual(batch['count'], 2)
        self.assertEqual({key for key, value in self.statuses().items() if value == 'removed'}, {1, 5})
        self.assertEqual(self.store.counts(group_id=self.health)['pending'], 3)
        audit = KeywordLibrary(self.path).last_removal()
        self.assertEqual([row['item_id'] for row in audit['rows']], [1, 5])
        self.assertEqual(audit['scope_group_id'], self.mbti)

    def test_other_group_in_snapshot_rejects_entire_batch(self):
        healthy = self.store.list(group_id=self.health)[0][0]
        with self.assertRaises(ValueError):
            self.store.remove_selected([self.rows[0], healthy], group_id=self.mbti)
        self.assertEqual(set(self.statuses().values()), {'pending'})
        self.assertIsNone(self.store.last_removal())

    def test_search_not_matching_selected_keyword_rejects_entire_batch(self):
        healthy = self.store.list(group_id=self.health)[0][0]
        with self.assertRaises(ValueError):
            self.store.remove_selected([self.rows[0], healthy], search='INTP')
        self.assertEqual(set(self.statuses().values()), {'pending'})

    def test_group_change_during_confirmation_cannot_delete_a_health_keyword(self):
        self.store.assign_group([self.rows[0]['item_id']], self.health)
        with self.assertRaises(ValueError):
            self.store.remove_selected(self.rows[:3], group_id=self.mbti)
        self.assertEqual(set(self.statuses().values()), {'pending'})
        self.assertEqual(self.store.counts(group_id=self.health)['pending'], 4)

    def test_all_groups_still_rejects_moved_snapshot_member(self):
        self.store.assign_group([self.rows[0]['item_id']], self.health)
        with self.assertRaises(ValueError):
            self.store.remove_selected(self.rows[:2])
        self.assertEqual(set(self.statuses().values()), {'pending'})

    def test_publication_during_confirmation_preserves_usage_and_unselected_rows(self):
        self.store.record_use(self.rows[0]['keyword'], platform='tistory', event_key='published')
        with self.assertRaises(ValueError):
            self.store.remove_selected(self.rows[:3], group_id=self.mbti)
        self.assertEqual(self.store.counts(), {'pending': 47, 'used': 1, 'history': 1})
        self.assertIsNone(self.store.last_removal())

    def test_readded_identity_invalidates_old_confirmation(self):
        self.store.change_status([1], 'removed')
        with patch.object(self.store, '_now', return_value='2030-01-01T00:00:00+00:00'):
            self.store.add([self.rows[0]['keyword']], group_id=self.mbti)
        with self.assertRaises(ValueError):
            self.store.remove_selected(self.rows[:2], group_id=self.mbti)
        self.assertEqual(set(self.statuses().values()), {'pending'})

    def test_empty_selection_does_not_create_a_batch_or_expand_scope(self):
        self.assertEqual(self.store.remove_selected([], group_id=self.mbti), {'id': None, 'count': 0})
        self.assertEqual(set(self.statuses().values()), {'pending'})
        self.assertIsNone(self.store.last_removal())

    def test_storage_failure_rolls_back_all_updates_and_audit(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TRIGGER reject_second BEFORE UPDATE OF status ON keywords "
                       "WHEN NEW.id=2 BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
            db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.remove_selected(self.rows[:3], group_id=self.mbti)
        self.assertEqual(set(self.statuses().values()), {'pending'})
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM keyword_removals').fetchone()[0], 0)

    def test_undo_restores_only_selected_batch_preserving_health_and_usage(self):
        batch = self.store.remove_selected(self.rows[:3], group_id=self.mbti)
        self.store.record_use('건강 하나', platform='tistory', event_key='health-published')
        self.assertEqual(KeywordLibrary(self.path).restore_removal(batch['id']), 3)
        self.assertEqual(self.store.counts(group_id=self.mbti)['pending'], 45)
        self.assertEqual(self.store.counts(group_id=self.health), {'pending': 2, 'used': 1, 'history': 1})
        self.assertEqual(self.store.restore_removal(batch['id']), 0)
        self.assertIsNone(self.store.last_removal())

    def test_undo_confirmed_batch_never_undoes_a_newer_deletion(self):
        first = self.store.remove_selected(self.rows[:1], group_id=self.mbti)
        second = self.store.remove_selected(self.rows[1:2], group_id=self.mbti)
        self.assertEqual(self.store.restore_removal(first['id']), 1)
        self.assertEqual(self.statuses()[2], 'removed')
        self.assertEqual(self.store.last_removal()['id'], second['id'])

    def test_old_undo_never_overwrites_republication_or_a_newer_deletion(self):
        first = self.store.remove_selected(self.rows[:2], group_id=self.mbti)
        with patch.object(self.store, '_now', return_value='2030-01-01T00:00:00+00:00'):
            self.store.add([row['keyword'] for row in self.rows[:2]], group_id=self.mbti)
        self.store.record_use(self.rows[0]['keyword'], platform='tistory', event_key='new-post')
        current = self.store.list(search=self.rows[1]['keyword'])[0]
        second = self.store.remove_selected(current, group_id=self.mbti)
        self.assertEqual(self.store.restore_removal(first['id']), 0)
        self.assertEqual((self.statuses()[1], self.statuses()[2]), ('used', 'removed'))
        self.assertEqual(self.store.restore_removal(second['id']), 1)

    def test_legacy_removed_keywords_are_never_restored_as_an_unrelated_batch(self):
        self.store.change_status([45], 'removed')
        batch = self.store.remove_selected(self.rows[:1], group_id=self.mbti)
        self.store.restore_removal(batch['id'])
        self.assertEqual(self.statuses()[45], 'removed')


if __name__ == '__main__':
    unittest.main()
