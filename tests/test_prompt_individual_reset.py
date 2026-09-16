from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main


class PromptIndividualResetTests(unittest.TestCase):
    def test_restores_only_selected_prompt_set(self) -> None:
        settings = main.WordPressSettings()
        prompt_sets = main.PromptFileStore.default_prompt_sets(settings)
        for item in prompt_sets:
            item["title_prompt"] = f"{item['platform']} 사용자 제목"
            item["article_prompt"] = f"{item['platform']} 사용자 본문"
        prompt_sets, selected = main.create_independent_prompt_set(
            prompt_sets,
            "tistory",
            "엄마 계정용",
            "선택 항목 사용자 제목",
            "선택 항목 사용자 본문",
        )
        before = {item["id"]: dict(item) for item in prompt_sets}

        restored_sets, restored = main.restore_single_prompt_set_defaults(
            prompt_sets,
            "tistory",
            str(selected["id"]),
        )

        self.assertIsNotNone(restored)
        self.assertEqual(restored["name"], "엄마 계정용")
        self.assertEqual(restored["title_prompt"], main.DEFAULT_TISTORY_TITLE_PROMPT)
        self.assertEqual(restored["article_prompt"], main.DEFAULT_TISTORY_ARTICLE_PROMPT)
        for item in restored_sets:
            if item["id"] == selected["id"]:
                continue
            self.assertEqual(item, before[item["id"]])

    def test_unknown_selection_changes_nothing(self) -> None:
        settings = main.WordPressSettings()
        prompt_sets = main.PromptFileStore.default_prompt_sets(settings)

        restored_sets, restored = main.restore_single_prompt_set_defaults(
            prompt_sets,
            "wordpress",
            "missing-id",
        )

        self.assertIsNone(restored)
        self.assertEqual(restored_sets, prompt_sets)

    def test_ui_reset_preserves_other_prompts_and_profile_connections(self) -> None:
        settings = main.WordPressSettings()
        settings.prompt_sets = main.PromptFileStore.default_prompt_sets(settings)
        settings.prompt_sets[0]["title_prompt"] = "보존할 워드프레스 제목"
        settings.prompt_sets[0]["article_prompt"] = "보존할 워드프레스 본문"
        settings.prompt_sets, selected = main.create_independent_prompt_set(
            settings.prompt_sets,
            "tistory",
            "선택한 티스토리",
            "사용자 티스토리 제목",
            "사용자 티스토리 본문",
        )
        settings.selected_prompt_id = "wordpress-default"
        settings.naver_blog_profile_prompt_ids = {
            "profile-1": "naver-blog-default",
            "profile-2": "naver-blog-mom",
        }
        original_profile_connections = dict(settings.naver_blog_profile_prompt_ids)
        app = SimpleNamespace(
            active_prompt_platform="tistory",
            wordpress_settings=settings,
            active_prompt_set_ids={"tistory": selected["id"]},
            prompt_feedback_label=Mock(),
            _save_active_prompt_set_to_memory=Mock(),
            _load_prompt_set_into_boxes=Mock(),
            _refresh_prompt_set_menus=Mock(),
            _apply_naver_blog_prompt_set=Mock(),
            _update_quick_status=Mock(),
        )
        app._prompt_sets = lambda: app.wordpress_settings.prompt_sets

        with (
            patch.object(main.PromptFileStore, "save_values") as save_values,
            patch.object(main.PromptFileStore, "save_prompt_sets") as save_sets,
            patch.object(main.AppStateStore, "save") as save_state,
        ):
            main.KeywordApp._reset_prompt_settings(app)

        restored = next(
            item
            for item in app.wordpress_settings.prompt_sets
            if item["id"] == selected["id"]
        )
        wordpress = next(
            item
            for item in app.wordpress_settings.prompt_sets
            if item["id"] == "wordpress-default"
        )
        self.assertEqual(restored["title_prompt"], main.DEFAULT_TISTORY_TITLE_PROMPT)
        self.assertEqual(restored["article_prompt"], main.DEFAULT_TISTORY_ARTICLE_PROMPT)
        self.assertEqual(wordpress["title_prompt"], "보존할 워드프레스 제목")
        self.assertEqual(wordpress["article_prompt"], "보존할 워드프레스 본문")
        self.assertEqual(settings.selected_prompt_id, "wordpress-default")
        self.assertEqual(settings.naver_blog_profile_prompt_ids, original_profile_connections)
        save_values.assert_called_once()
        save_sets.assert_called_once()
        save_state.assert_called_once_with(settings, save_secrets=False)

    def test_ui_reset_uses_active_tab_without_rebuilding_all_sets(self) -> None:
        source = inspect.getsource(main.KeywordApp._reset_prompt_settings)

        self.assertIn("active_prompt_platform", source)
        self.assertIn("restore_single_prompt_set_defaults", source)
        self.assertNotIn("PromptFileStore.default_prompt_sets", source)
        self.assertNotIn("NAVER_BLOG_DEFAULT_PROFILE_PROMPT_IDS", source)


if __name__ == "__main__":
    unittest.main()
