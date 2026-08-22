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
