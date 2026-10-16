import copy
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

import main


class Entry:
    def __init__(self, value=""):
        self.value, self.state = value, "normal"

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def delete(self, *_):
        if self.state != "disabled":
            self.value = ""

    def insert(self, _position, value):
        if self.state != "disabled":
            self.value = value

    def cget(self, key):
        return self.state if key == "state" else self.value

    def configure(self, **values):
        if "state" in values:
            self.state = values["state"]
        if "text" in values:
            self.value = values["text"]


class KeywordUI:
    _remember_writing_keyword_inputs = main.KeywordApp._remember_writing_keyword_inputs
    _restore_writing_keyword_inputs = main.KeywordApp._restore_writing_keyword_inputs
    _selected_or_manual_keyword = main.KeywordApp._selected_or_manual_keyword

    def __init__(self, settings=None):
        self.wordpress_settings = settings or main.WordPressSettings()
        self._writing_keyword_inputs_ready = True
        self.writing_variant = "writing"
        self.topic_entry, self.manual_keyword_entry, self.selected_keyword_var = Entry(), Entry(), Entry()
        self.tistory_v2_wiki_keyword_entry = Entry()
        self.keyword_status_label = Entry("준비")
        self.writing_auto_progress_var = Entry(False)
        self._refresh_writing_auto_progress_ui = Mock()
        self.current_keyword = self.current_keyword_source = self.accepted_recommended_keyword = ""
        self.current_insights = []
        for name in ("daum_reference_map", "signal_reference_map", "newneek_reference_map",
                     "loword_reference_map", "naver_creator_reference_map", "collected_reference_map"):
            setattr(self, name, {})
        self._render_keyword_choices = Mock(side_effect=lambda _: setattr(self, "accepted_recommended_keyword", ""))
        self._clear_result_cards = Mock()

    def switch(self, variant):
        self._remember_writing_keyword_inputs()
        self.writing_variant = variant
        self._restore_writing_keyword_inputs(variant)


class WritingKeywordIsolationTests(unittest.TestCase):
    def test_topic_manual_and_selected_values_are_independent_during_quick_switches(self):
        app = KeywordUI()
        app.topic_entry.set("일반 주제")
        app.manual_keyword_entry.set("일반 직접 키워드")
        app.switch("tistory_v2")
        self.assertEqual((app.topic_entry.get(), app.manual_keyword_entry.get()), ("", ""))
        app.topic_entry.set("V2 주제")
        app.selected_keyword_var.set("V2 추천 키워드")
        app.switch("writing")
        self.assertEqual((app.topic_entry.get(), app._selected_or_manual_keyword()), ("일반 주제", "일반 직접 키워드"))
        app.switch("tistory_v2")
        self.assertEqual((app.topic_entry.get(), app.selected_keyword_var.get()), ("V2 주제", "V2 추천 키워드"))
        self.assertEqual(app.manual_keyword_entry.get(), "")

    def test_source_results_and_accepted_radio_are_restored_without_sharing_mutable_maps(self):
        app = KeywordUI()
        app.current_keyword, app.current_keyword_source = "일반 검색", "analysis"
        app.current_insights = [{"keyword": "일반 추천"}]
        app.selected_keyword_var.set("일반 추천")
        app.accepted_recommended_keyword = "일반 추천"
        app.collected_reference_map = {"일반 추천": "일반 자료"}
        app.keyword_status_label.value = "일반 검색 완료"
        app.switch("tistory_v2")
        self.assertEqual(app.current_insights, [])
        self.assertEqual(app.current_keyword_source, "")
        app.collected_reference_map["V2 추천"] = "V2 자료"
        app.current_keyword_source = "daum"
        app.switch("writing")
        self.assertEqual(app.current_keyword_source, "analysis")
        self.assertEqual(app.current_insights, [{"keyword": "일반 추천"}])
        self.assertEqual(app.accepted_recommended_keyword, "일반 추천")
        self.assertEqual(app.keyword_status_label.value, "일반 검색 완료")
        self.assertEqual(app.collected_reference_map, {"일반 추천": "일반 자료"})
        app.collected_reference_map.clear()
        self.assertEqual(app._writing_keyword_contexts["writing"]["maps"]["collected_reference_map"], {"일반 추천": "일반 자료"})

    def test_restart_restores_each_menu_from_its_own_fields(self):
        settings = main.WordPressSettings(writing_topic="일반 주제", writing_selected_keyword="일반 선택",
                                          tistory_v2_topic="V2 주제", tistory_v2_selected_keyword="V2 선택")
        for variant, expected in (("writing", ("일반 주제", "일반 선택")), ("tistory_v2", ("V2 주제", "V2 선택"))):
            app = KeywordUI(copy.deepcopy(settings))
            app._restore_writing_keyword_inputs(variant)
            self.assertEqual((app.topic_entry.get(), app._selected_or_manual_keyword()), expected)

    def test_old_normal_values_do_not_seed_new_v2_fields(self):
        app = KeywordUI(main.WordPressSettings(writing_topic="이전 주제", writing_selected_keyword="이전 선택"))
        app._restore_writing_keyword_inputs("tistory_v2")
        self.assertEqual((app.topic_entry.get(), app._selected_or_manual_keyword()), ("", ""))
        self.assertEqual(app.wordpress_settings.writing_topic, "이전 주제")

    def test_explicit_clearing_is_saved_and_does_not_fall_back_to_other_menu(self):
        app = KeywordUI(main.WordPressSettings(writing_topic="일반", tistory_v2_topic="V2"))
        app._restore_writing_keyword_inputs("writing")
        app.switch("tistory_v2")
        app.topic_entry.set("")
        app.switch("writing")
        self.assertEqual(app.topic_entry.get(), "일반")
        self.assertEqual(app.wordpress_settings.tistory_v2_topic, "")
        app.switch("tistory_v2")
        self.assertEqual(app.topic_entry.get(), "")

    def test_disabled_benchmark_topic_is_replaced_and_stays_disabled(self):
        app = KeywordUI(main.WordPressSettings(tistory_v2_topic="V2 저장 주제"))
        app.topic_entry.set("일반 주제")
        app.topic_entry.state = "disabled"
        app.switch("tistory_v2")
        self.assertEqual(app.topic_entry.get(), "V2 저장 주제")
        self.assertEqual(app.topic_entry.state, "disabled")

    def test_initial_widget_construction_cannot_overwrite_saved_values(self):
        app = KeywordUI(main.WordPressSettings(writing_topic="일반 저장", tistory_v2_topic="V2 저장"))
        app._writing_keyword_inputs_ready = False
        app._remember_writing_keyword_inputs()
        self.assertEqual((app.wordpress_settings.writing_topic, app.wordpress_settings.tistory_v2_topic), ("일반 저장", "V2 저장"))

    def test_settings_round_trip_keeps_both_menus_and_explicit_empty_v2(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main, "STATE_FILE", Path(directory) / "app_state.json"), \
             patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value), \
             patch.object(main.KeychainStore, "load_secret", return_value=""):
            settings = main.WordPressSettings(writing_topic="일반", writing_selected_keyword="일반 선택",
                                              tistory_v2_topic="V2", tistory_v2_selected_keyword="V2 선택")
            main.AppStateStore.save(settings, save_secrets=False)
            restored = main.AppStateStore.load()
            self.assertEqual((restored.writing_topic, restored.tistory_v2_topic), ("일반", "V2"))
            self.assertEqual((restored.writing_selected_keyword, restored.tistory_v2_selected_keyword), ("일반 선택", "V2 선택"))
            restored.tistory_v2_topic = restored.tistory_v2_selected_keyword = ""
            main.AppStateStore.save(restored, save_secrets=False)
            self.assertEqual(main.AppStateStore.load().tistory_v2_topic, "")
            self.assertEqual(main.AppStateStore.load().writing_topic, "일반")

    def test_blocked_switch_does_not_save_or_restore_another_context(self):
        app = KeywordUI()
        app.result_queue = queue.Queue()
        app._sidebar_activity_map = Mock(return_value={"writing": True})
        with patch.object(app, "_remember_writing_keyword_inputs") as remember, \
             patch.object(app, "_restore_writing_keyword_inputs") as restore, \
             patch.object(main.messagebox, "showinfo"):
            self.assertFalse(main.KeywordApp._set_writing_variant(app, "tistory_v2"))
        remember.assert_not_called()
        restore.assert_not_called()
        self.assertEqual(app.writing_variant, "writing")

    def test_auto_progress_is_independent_in_all_four_on_off_combinations(self):
        for normal in (False, True):
            for v2 in (False, True):
                with self.subTest(normal=normal, v2=v2):
                    app = KeywordUI(main.WordPressSettings(writing_auto_progress=normal, tistory_v2_auto_progress=v2))
                    app._restore_writing_keyword_inputs("writing")
                    self.assertEqual(app.writing_auto_progress_var.get(), normal)
                    for variant, expected in (("tistory_v2", v2), ("writing", normal), ("tistory_v2", v2)):
                        app.switch(variant)
                        self.assertEqual(app.writing_auto_progress_var.get(), expected)
                    self.assertEqual(app.wordpress_settings.writing_auto_progress, normal)
                    self.assertEqual(app.wordpress_settings.tistory_v2_auto_progress, v2)

    def test_auto_progress_edits_only_update_current_menu_and_false_is_preserved(self):
        app = KeywordUI(main.WordPressSettings(writing_auto_progress=True, tistory_v2_auto_progress=True))
        app._restore_writing_keyword_inputs("writing")
        app.switch("tistory_v2")
        app.writing_auto_progress_var.set(False)
        app.switch("writing")
        self.assertTrue(app.writing_auto_progress_var.get())
        self.assertFalse(app.wordpress_settings.tistory_v2_auto_progress)
        app.writing_auto_progress_var.set(False)
        app.switch("tistory_v2")
        self.assertFalse(app.writing_auto_progress_var.get())
        self.assertFalse(app.wordpress_settings.writing_auto_progress)
        app.writing_auto_progress_var.set(True)
        app.switch("writing")
        self.assertFalse(app.writing_auto_progress_var.get())
        self.assertTrue(app.wordpress_settings.tistory_v2_auto_progress)

    def test_auto_progress_round_trip_and_restart_keep_both_modes(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main, "STATE_FILE", Path(directory) / "app_state.json"), \
             patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value), \
             patch.object(main.KeychainStore, "load_secret", return_value=""):
            for normal, v2 in ((True, False), (False, True), (False, False), (True, True)):
                main.AppStateStore.save(main.WordPressSettings(writing_auto_progress=normal,
                                                               tistory_v2_auto_progress=v2), save_secrets=False)
                settings = main.AppStateStore.load()
                self.assertEqual((settings.writing_auto_progress, settings.tistory_v2_auto_progress), (normal, v2))
                app = KeywordUI(settings)
                app._restore_writing_keyword_inputs("writing")
                self.assertEqual(app.writing_auto_progress_var.get(), normal)
                app.switch("tistory_v2")
                self.assertEqual(app.writing_auto_progress_var.get(), v2)

    def test_old_normal_on_does_not_seed_v2_and_restoring_on_never_starts_work(self):
        settings = main.WordPressSettings(writing_auto_progress=True)
        self.assertFalse(settings.tistory_v2_auto_progress)
        app = KeywordUI(settings)
        app._arm_writing_auto_progress = Mock()
        app._stop_writing_auto_progress = Mock()
        app._restore_writing_keyword_inputs("writing")
        self.assertTrue(app.writing_auto_progress_var.get())
        app.switch("tistory_v2")
        self.assertFalse(app.writing_auto_progress_var.get())
        app._arm_writing_auto_progress.assert_not_called()
        app._stop_writing_auto_progress.assert_not_called()


if __name__ == "__main__":
    unittest.main()
