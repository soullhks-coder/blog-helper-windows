import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class NaverBlogPromptManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = MAIN_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.methods = {
            node.name: node
            for node in ast.walk(cls.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _method_source(self, method_name: str) -> str:
        return ast.get_source_segment(self.source, self.methods[method_name]) or ""

    def test_naver_blog_page_no_longer_embeds_prompt_tab(self) -> None:
        source = self._method_source("_build_naver_blog_page")

        self.assertNotIn('("ai", "프롬프트")', source)
        self.assertNotIn('naver_blog_tab_frames["ai"]', source)
        self.assertNotIn("_build_naver_blog_ai_tab", source)

    def test_prompt_manager_hosts_naver_blog_prompts(self) -> None:
        source = self._method_source("_build_prompts_page")

        self.assertIn('("naver_blog", "N블로그")', source)
        self.assertNotIn("naver_blog_automation", source)
        self.assertLess(
            source.index('("tistory", "티스토리")'),
            source.index('("naver_blog", "N블로그")'),
        )
        self.assertLess(
            source.index('("naver_blog", "N블로그")'),
            source.index('("blogspot", "블로그스팟")'),
        )
        self.assertIn('text="추가"', source)
        self.assertIn('text="수정"', source)
        self.assertIn('text="삭제"', source)
        self.assertIn("self.naver_blog_title_prompt_box", source)
        self.assertIn("self.naver_blog_topic_prompt_box", source)

    def test_naver_blog_edit_button_opens_prompt_manager(self) -> None:
        source = self._method_source("_open_naver_blog_prompt_manager")

        self.assertIn('self._switch_page("prompts")', source)
        self.assertIn('self._switch_prompt_platform("naver_blog")', source)

    def test_naver_blog_writing_tab_uses_managed_prompt_sets(self) -> None:
        source = self._method_source("_build_naver_blog_writing_tab")

        self.assertIn("self._naver_blog_prompt_menu_values()", source)
        self.assertIn("self._on_naver_blog_prompt_selected", source)
        self.assertIn("self._refresh_naver_blog_prompt_menu()", source)

    def test_multiple_naver_blog_prompt_sets_are_preserved(self) -> None:
        settings = main.WordPressSettings()
        prompt_sets = main.PromptFileStore.default_prompt_sets(settings)
        prompt_sets.append(
            {
                "id": "naver-blog-entertainment",
                "platform": "naver_blog",
                "name": "연예이슈",
                "title_prompt": "연예 이슈 제목",
                "article_prompt": "연예 이슈 본문",
            }
        )

        normalized = main.PromptFileStore.normalize_prompt_sets(prompt_sets, settings)
        naver_blog_sets = [item for item in normalized if item["platform"] == "naver_blog"]

        self.assertEqual(len(naver_blog_sets), 2)
        self.assertEqual(naver_blog_sets[1]["name"], "연예이슈")

    def test_legacy_prompt_is_migrated_only_to_the_active_blog(self) -> None:
        profiles = main.normalize_naver_blog_profiles(
            [
                {"name": "블로그 1", "blog_id": "mine"},
                {"name": "블로그 2", "blog_id": "mom"},
                {"name": "블로그 3", "blog_id": "third"},
            ]
        )

        prompt_ids = main.normalize_naver_blog_profile_prompt_ids(
            {},
            profiles=profiles,
            active_profile="블로그 2",
            legacy_prompt_id="naver-blog-mom",
        )

        self.assertEqual(
            prompt_ids[main.NAVER_PLAYWRIGHT_PROFILE_BLOG],
            main.NAVER_BLOG_DEFAULT_PROMPT_ID,
        )
        self.assertEqual(
            prompt_ids[main.NAVER_PLAYWRIGHT_PROFILE_BLOG_2],
            "naver-blog-mom",
        )
        self.assertEqual(
            prompt_ids[main.NAVER_PLAYWRIGHT_PROFILE_BLOG_3],
            main.NAVER_BLOG_DEFAULT_PROMPT_ID,
        )

    def test_each_blogs_last_prompt_survives_settings_round_trip(self) -> None:
        prompt_ids = {
            main.NAVER_PLAYWRIGHT_PROFILE_BLOG: "naver-blog-mine",
            main.NAVER_PLAYWRIGHT_PROFILE_BLOG_2: "naver-blog-mom",
            main.NAVER_PLAYWRIGHT_PROFILE_BLOG_3: "naver-blog-third",
        }
        settings = main.WordPressSettings(
            naver_blog_profile_prompt_ids=prompt_ids,
        )

        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(
                    main.PromptFileStore,
                    "load_into",
                    side_effect=lambda value: value,
                ),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

        self.assertEqual(loaded.naver_blog_profile_prompt_ids, prompt_ids)

    def test_new_prompt_does_not_overwrite_selected_prompt(self) -> None:
        existing = [
            {
                "id": "naver-blog-existing",
                "platform": "naver_blog",
                "name": "기존 프롬프트",
                "title_prompt": "기존 제목 지침",
                "article_prompt": "기존 본문 지침",
            }
        ]

        prompt_sets, created = main.create_independent_prompt_set(
            existing,
            "naver_blog",
            "신규 프롬프트",
            "완전히 다른 제목 지침",
            "완전히 다른 본문 지침",
        )

        self.assertEqual(existing[0]["title_prompt"], "기존 제목 지침")
        self.assertEqual(prompt_sets[0]["article_prompt"], "기존 본문 지침")
        self.assertEqual(created["name"], "신규 프롬프트")
        self.assertEqual(created["title_prompt"], "완전히 다른 제목 지침")
        self.assertNotEqual(created["id"], existing[0]["id"])

    def test_duplicate_prompt_names_are_disambiguated_per_platform(self) -> None:
        existing = [
            {
                "id": "naver-blog-review",
                "platform": "naver_blog",
                "name": "리뷰",
                "title_prompt": "제목 1",
                "article_prompt": "본문 1",
            }
        ]

        prompt_sets, created = main.create_independent_prompt_set(
            existing,
            "naver_blog",
            "리뷰",
            "제목 2",
            "본문 2",
        )
        normalized = main.PromptFileStore.normalize_prompt_sets(
            prompt_sets
            + [
                {
                    "id": "naver-blog-review-3",
                    "platform": "naver_blog",
                    "name": "리뷰",
                    "title_prompt": "제목 3",
                    "article_prompt": "본문 3",
                }
            ],
            main.WordPressSettings(),
        )
        names = [item["name"] for item in normalized if item["platform"] == "naver_blog"]

        self.assertEqual(created["name"], "리뷰 (2)")
        self.assertEqual(names, ["리뷰", "리뷰 (2)", "리뷰 (3)"])

    def test_add_action_does_not_save_new_text_into_active_prompt_first(self) -> None:
        source = self._method_source("_add_prompt_set")

        self.assertNotIn("_save_active_prompt_set_to_memory", source)
        self.assertIn("create_independent_prompt_set", source)
        self.assertIn("PromptFileStore.save_prompt_sets", source)

    def test_separate_naver_blog_prompts_survive_file_round_trip(self) -> None:
        settings = main.WordPressSettings()
        original = main.PromptFileStore.default_prompt_sets(settings)
        original, created = main.create_independent_prompt_set(
            original,
            "naver_blog",
            "엄마 계정용",
            "엄마 계정 전용 제목",
            "엄마 계정 전용 본문",
        )

        with tempfile.TemporaryDirectory() as directory:
            prompt_dir = Path(directory)
            prompt_sets_file = prompt_dir / "prompt_sets.json"
            with (
                patch.object(main, "PROMPT_STORAGE_DIR", prompt_dir),
                patch.object(main, "PROMPT_SETS_FILE", prompt_sets_file),
                patch.object(main.PromptFileStore, "_ensure_desktop_shortcut"),
            ):
                main.PromptFileStore.save_prompt_sets(original)
                loaded = main.PromptFileStore.load_prompt_sets(settings)

        loaded_default = next(item for item in loaded if item["id"] == "naver-blog-default")
        loaded_created = next(item for item in loaded if item["id"] == created["id"])
        self.assertNotEqual(loaded_default["article_prompt"], loaded_created["article_prompt"])
        self.assertEqual(loaded_created["title_prompt"], "엄마 계정 전용 제목")
        self.assertEqual(loaded_created["article_prompt"], "엄마 계정 전용 본문")

    def test_naver_blog_prompt_files_round_trip_in_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prompt_dir = Path(directory)
            values = {
                "naver_blog_title_prompt": "연예 이슈 제목을 자연스럽게 작성해줘.",
                "naver_blog_topic_prompt": "사실을 바탕으로 읽기 쉬운 본문을 작성해줘.",
            }
            with (
                patch.object(main, "PROMPT_STORAGE_DIR", prompt_dir),
                patch.object(main.PromptFileStore, "_ensure_desktop_shortcut"),
            ):
                main.PromptFileStore.save_values(values)
                loaded = main.PromptFileStore.load_into(main.WordPressSettings())

            self.assertEqual(loaded.naver_blog_title_prompt, values["naver_blog_title_prompt"])
            self.assertEqual(loaded.naver_blog_topic_prompt, values["naver_blog_topic_prompt"])
            self.assertEqual(
                (prompt_dir / "naver_blog_title_prompt.txt").read_text(encoding="utf-8").strip(),
                values["naver_blog_title_prompt"],
            )


if __name__ == "__main__":
    unittest.main()
