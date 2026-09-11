import ast
import queue
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"


class _EntryStub:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def delete(self, _start, _end) -> None:
        self.value = ""

    def insert(self, _index, value: str) -> None:
        self.value = value


class _LabelStub:
    def __init__(self) -> None:
        self.options = {}

    def configure(self, **kwargs) -> None:
        self.options.update(kwargs)


class NaverKinAutomationTests(unittest.TestCase):
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

    def test_question_title_does_not_include_list_metadata_or_body(self) -> None:
        raw = "청년도약계좌 가입 조건\n신청하려는데 소득 조건이 궁금합니다.\n답변 2 새 창"

        self.assertEqual(
            main.clean_naver_kin_question_title(raw),
            "청년도약계좌 가입 조건",
        )

    def test_browser_update_notice_is_not_used_as_question_title(self) -> None:
        self.assertEqual(
            main.clean_naver_kin_question_title("권장 브라우저 업데이트 안내"),
            "",
        )
        self.assertEqual(
            main.clean_naver_kin_question_title("크롬 브라우저 업데이트 후 오류 해결 방법"),
            "크롬 브라우저 업데이트 후 오류 해결 방법",
        )

    def test_question_body_removes_repeated_title(self) -> None:
        body = main.clean_naver_kin_question_body(
            "청년도약계좌 가입 조건: 신청하려는데 소득 조건과 준비 서류가 궁금합니다.",
            "청년도약계좌 가입 조건",
        )

        self.assertEqual(body, "신청하려는데 소득 조건과 준비 서류가 궁금합니다.")

    def test_only_detail_questions_with_body_are_automation_ready(self) -> None:
        ready = {
            "title": "청년도약계좌 가입 조건",
            "question_text": "신청하려는데 소득 조건과 준비 서류가 무엇인지 자세히 궁금합니다.",
            "url": "https://kin.naver.com/qna/detail.naver?d1id=1&docId=123",
        }
        title_only = {**ready, "question_text": ""}

        self.assertTrue(main.naver_kin_question_ready(ready))
        self.assertFalse(main.naver_kin_question_ready(title_only))

    def test_collect_count_is_normalized_to_one_through_ten(self) -> None:
        self.assertEqual(main.normalize_naver_kin_collect_count("1개"), 1)
        self.assertEqual(main.normalize_naver_kin_collect_count("6개"), 6)
        self.assertEqual(main.normalize_naver_kin_collect_count(20), 10)
        self.assertEqual(main.normalize_naver_kin_collect_count(0), 1)
        self.assertEqual(main.normalize_naver_kin_collect_count("잘못된 값"), 10)

    def test_single_question_url_validation_accepts_detail_only(self) -> None:
        normalized = main.normalize_naver_kin_question_url(
            "kin.naver.com/qna/detail.naver?d1id=1&docId=123"
        )

        self.assertEqual(
            normalized,
            "https://kin.naver.com/qna/detail.naver?d1id=1&docId=123",
        )
        with self.assertRaisesRegex(ValueError, "질문 상세 URL"):
            main.normalize_naver_kin_question_url(
                "https://kin.naver.com/qna/questionList.naver"
            )
        with self.assertRaisesRegex(ValueError, "kin.naver.com"):
            main.normalize_naver_kin_question_url(
                "https://example.com/qna/detail.naver?docId=123"
            )

    def test_clipboard_text_extracts_only_naver_kin_detail_url(self) -> None:
        self.assertEqual(
            main.extract_naver_kin_question_url(
                "확인할 링크: [https://kin.naver.com/qna/detail.naver?d1id=8&docId=123](https://kin.naver.com)"
            ),
            "https://kin.naver.com/qna/detail.naver?d1id=8&docId=123",
        )
        self.assertEqual(
            main.extract_naver_kin_question_url(
                "https://kin.naver.com/qna/questionList.naver"
            ),
            "",
        )
        self.assertEqual(
            main.extract_naver_kin_question_url("https://example.com/qna/detail.naver?docId=123"),
            "",
        )

    def test_clipboard_monitor_fills_and_saves_detected_question_url(self) -> None:
        question_url = "https://kin.naver.com/qna/detail.naver?d1id=8&docId=456"
        scheduled = []
        app = type("ClipboardAppStub", (), {})()
        app._naver_kin_clipboard_job = None
        app._last_naver_kin_clipboard_url = ""
        app._app_closing = False
        app.current_page = "naver_kin"
        app.naver_kin_automation_running = False
        app.naver_kin_worker = None
        app.naver_kin_direct_worker = None
        app.naver_kin_automation_worker = None
        app.naver_kin_direct_url_entry = _EntryStub("old value")
        app.naver_kin_clipboard_status_label = _LabelStub()
        app.naver_kin_status_label = _LabelStub()
        app.wordpress_settings = main.WordPressSettings()
        app.clipboard_get = lambda: f"복사한 질문 {question_url}"
        app._start_naver_kin_clipboard_monitor = (
            lambda delay_ms=120: scheduled.append(delay_ms)
        )

        with patch.object(main.AppStateStore, "update_fields") as update_fields:
            main.KeywordApp._monitor_naver_kin_clipboard(app)

        self.assertEqual(app.naver_kin_direct_url_entry.get(), question_url)
        self.assertEqual(
            app.wordpress_settings.naver_kin_direct_question_url,
            question_url,
        )
        update_fields.assert_called_once_with(
            naver_kin_direct_question_url=question_url,
        )
        self.assertIn(
            "자동으로 가져왔습니다",
            app.naver_kin_clipboard_status_label.options["text"],
        )
        self.assertEqual(scheduled, [800])

    def test_single_question_page_extracts_title_and_body(self) -> None:
        class FakeLocator:
            def __init__(self, text: str = "", attribute: str = "") -> None:
                self.text = text
                self.attribute = attribute

            @property
            def first(self):
                return self

            def count(self):
                return 1 if self.text or self.attribute else 0

            def nth(self, _index):
                return self

            def is_visible(self, timeout=0):
                return True

            def inner_text(self, timeout=0):
                return self.text

            def get_attribute(self, _name):
                return self.attribute

        class FakePage:
            url = "https://kin.naver.com/qna/detail.naver?docId=123"

            def locator(self, selector):
                if selector == ".questionDetail .c-heading__title":
                    return FakeLocator("청년도약계좌 가입 조건")
                if selector == ".questionDetail":
                    return FakeLocator(
                        "청년도약계좌 가입 조건: 신청하려는데 소득 조건과 필요한 준비 서류가 무엇인지 궁금합니다."
                    )
                return FakeLocator()

            def title(self):
                return "네이버 지식iN"

            def evaluate(self, _script):
                return ""

        question = main.extract_naver_kin_question_from_page(
            FakePage(),
            FakePage.url,
        )

        self.assertEqual(question["title"], "청년도약계좌 가입 조건")
        self.assertEqual(
            question["question_text"],
            "신청하려는데 소득 조건과 필요한 준비 서류가 무엇인지 궁금합니다.",
        )
        self.assertTrue(main.naver_kin_question_ready(question))

    def test_naver_kin_uses_explicit_wordpress_prompt(self) -> None:
        settings = main.WordPressSettings(
            prompt_sets=[
                {
                    "id": "wordpress-default",
                    "platform": "wordpress",
                    "name": "기본",
                    "title_prompt": "기본 제목",
                    "article_prompt": "기본 본문",
                },
                {
                    "id": "wordpress-kin",
                    "platform": "wordpress",
                    "name": "지식인 답변형",
                    "title_prompt": "지식인 제목",
                    "article_prompt": "지식인 본문",
                },
                {
                    "id": "tistory-news",
                    "platform": "tistory",
                    "name": "뉴스",
                    "title_prompt": "티스토리 제목",
                    "article_prompt": "티스토리 본문",
                },
            ],
            selected_prompt_id="tistory-news",
            naver_kin_wordpress_prompt_id="wordpress-kin",
        )

        selected = main.resolve_naver_kin_wordpress_prompt_set(settings)

        self.assertEqual(selected["id"], "wordpress-kin")
        self.assertEqual(selected["article_prompt"], "지식인 본문")

    def test_legacy_default_answer_prompt_is_migrated_without_touching_custom_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            prompt_dir = Path(directory)
            prompt_path = prompt_dir / "naver_kin_answer_prompt.txt"
            prompt_path.write_text(main.LEGACY_NAVER_KIN_ANSWER_PROMPT, encoding="utf-8")
            with (
                patch.object(main, "PROMPT_STORAGE_DIR", prompt_dir),
                patch.object(main.PromptFileStore, "_ensure_desktop_shortcut"),
            ):
                settings = main.PromptFileStore.load_into(main.WordPressSettings())

            self.assertEqual(
                settings.naver_kin_answer_prompt,
                main.DEFAULT_NAVER_KIN_ANSWER_PROMPT,
            )
            self.assertEqual(
                prompt_path.read_text(encoding="utf-8").strip(),
                main.DEFAULT_NAVER_KIN_ANSWER_PROMPT,
            )

    def test_naver_kin_schedule_and_wordpress_prompt_selection_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                naver_kin_profiles=[
                    {"name": "지식인 1", "nickname": "내 계정"},
                    {"name": "지식인 2", "nickname": "엄마 계정"},
                    {"name": "지식인 3", "nickname": "업무 계정"},
                ],
                naver_kin_active_profile="지식인 2",
                naver_kin_collect_count=4,
                naver_kin_collect_interval_minutes=120,
                naver_kin_answer_interval_minutes=10,
                naver_kin_wordpress_prompt_id="wordpress-kin",
                naver_kin_next_action="collect",
                naver_kin_next_run_at=12345.0,
                naver_kin_direct_question_url="https://kin.naver.com/qna/detail.naver?docId=456",
                naver_kin_reference_text="질문자가 직접 알려 준 참고 답변",
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

            self.assertEqual(loaded.naver_kin_collect_count, 4)
            self.assertEqual(loaded.naver_kin_collect_interval_minutes, 120)
            self.assertEqual(loaded.naver_kin_answer_interval_minutes, 10)
            self.assertEqual(loaded.naver_kin_wordpress_prompt_id, "wordpress-kin")
            self.assertEqual(loaded.naver_kin_active_profile, "지식인 2")
            self.assertEqual(
                [profile["nickname"] for profile in loaded.naver_kin_profiles],
                ["내 계정", "엄마 계정", "업무 계정"],
            )
            self.assertEqual(loaded.naver_kin_next_action, "collect")
            self.assertEqual(loaded.naver_kin_next_run_at, 12345.0)
            self.assertEqual(
                loaded.naver_kin_direct_question_url,
                "https://kin.naver.com/qna/detail.naver?docId=456",
            )
            self.assertEqual(
                loaded.naver_kin_reference_text,
                "질문자가 직접 알려 준 참고 답변",
            )

    def test_collector_opens_detail_pages_and_extracts_body(self) -> None:
        source = self._method_source("run_naver_kin_playwright_bootstrap")

        self.assertIn("detail_page.goto", source)
        self.assertIn("extract_question_body(detail_page)", source)
        self.assertIn("naver_kin_question_ready(question)", source)
        self.assertIn('sort_mode == "최신순"', source)
        self.assertIn("collection_limit = normalize_naver_kin_collect_count(collect_count)", source)
        self.assertIn("len(questions) >= collection_limit", source)
        self.assertLess(
            source.index('".questionTitle"'),
            source.index('for selector in ("main h1", "#content h1", "h1")'),
        )

    def test_bootstrap_worker_forwards_selected_collect_count(self) -> None:
        result_queue = queue.Queue()
        payload = {"questions": []}
        with patch.object(
            main,
            "run_naver_kin_playwright_bootstrap",
            return_value=(True, payload),
        ) as bootstrap:
            worker = main.NaverKinBootstrapWorker(
                main.NAVER_KIN_QUESTION_LIST_URL,
                "최신순",
                result_queue,
                6,
            )
            worker.run()

        bootstrap.assert_called_once_with(
            main.NAVER_KIN_QUESTION_LIST_URL,
            "최신순",
            result_queue,
            6,
            main.NAVER_PLAYWRIGHT_PROFILE_KIN,
        )
        self.assertEqual(result_queue.get_nowait(), ("naver_kin_done", payload))

    def test_answer_editor_supports_naver_input_buffer_without_clipboard_paste(self) -> None:
        source = self._method_source("run_naver_kin_answer_playwright")

        self.assertIn("#smartEditorArea .se-text-paragraph", source)
        self.assertIn("/^input_buffer/i", source)
        self.assertIn("target_page.keyboard.insert_text(line)", source)
        self.assertIn("editor_contains_inserted_text(frame)", source)
        self.assertIn("navigator.clipboard.writeText(url)", source)
        self.assertIn("button.se-oglink-toolbar-button", source)
        self.assertIn("button.se-popup-button-confirm", source)
        self.assertIn("success_hold_completed = True", source)
        self.assertNotIn("paste_with_system_clipboard", source)
        self.assertNotIn('keyboard.press("Meta+V")', source)

    def test_answer_builder_applies_saved_template_and_reference_text(self) -> None:
        settings = main.WordPressSettings(
            naver_kin_answer_template="더 자세한 내용은 여기에서 확인해 보세요.\n{url}",
        )
        captured_prompt = ""

        def fake_generate(_settings, prompt):
            nonlocal captured_prompt
            captured_prompt = prompt
            return "술맛은 온도와 안주, 그날의 입안 상태에 따라 다르게 느껴질 수 있어요.", "test"

        with patch.object(main, "generate_text_with_writing_model", side_effect=fake_generate):
            answer = main.build_naver_kin_answer_text(
                settings,
                "왜 술맛이 매번 달라지는 거죠?",
                "참이슬과 청하가 어떤 날은 쓰고 어떤 날은 달게 느껴져요.",
                "술맛이 달라지는 이유",
                "https://example.com/answer",
                "온도와 음식, 컨디션에 따른 맛 지각 차이를 정리했습니다.",
                "차갑게 마시면 쓴맛이 덜 느껴질 수 있습니다.",
            )

        self.assertIn("차갑게 마시면 쓴맛이 덜 느껴질 수 있습니다.", captured_prompt)
        self.assertIn("더 자세한 내용은 여기에서 확인해 보세요.", captured_prompt)
        self.assertTrue(answer.endswith("더 자세한 내용은 여기에서 확인해 보세요."))
        self.assertNotIn("https://example.com/answer", answer)

    def test_collect_schedule_runs_fresh_playwright_collection(self) -> None:
        source = self._method_source("_run_naver_kin_automation_once")

        self.assertIn('scheduled_action == "collect"', source)
        self.assertIn("self._start_naver_kin_bootstrap()", source)

    def test_scheduler_skips_title_only_questions(self) -> None:
        source = self._method_source("_next_naver_kin_question_for_automation")

        self.assertIn("naver_kin_question_ready(question)", source)

    def test_worker_applies_wordpress_prompt_and_answer_prompt_separately(self) -> None:
        source = self._method_source("run")
        worker_source = self.source[
            self.source.index("class NaverKinAutomationWorker"):
            self.source.index("class ThumbnailAIWorker")
        ]

        self.assertIn("resolve_naver_kin_wordpress_prompt_set", worker_source)
        self.assertIn("wordpress_title_prompt_template", worker_source)
        self.assertIn("wordpress_article_prompt_template", worker_source)
        self.assertIn("naver_kin_reference_text", worker_source)
        self.assertIn("DailyPublishLimitStore.reserve_publish", worker_source)
        self.assertIn("DailyPublishLimitStore.record_reserved_success", worker_source)
        self.assertIn("DailyPublishLimitStore.cancel_reservation", worker_source)
        self.assertIn('"naver_kin"', worker_source)
        self.assertIn("NAVER_KIN_DAILY_ANSWER_LIMIT", worker_source)
        self.assertIn('"naver_kin_answer_count"', worker_source)
        self.assertIn("build_naver_kin_answer_text", worker_source)
        self.assertTrue(source)

    def test_single_url_worker_collects_then_emits_question(self) -> None:
        result_queue = queue.Queue()
        question = {
            "title": "청년도약계좌 가입 조건",
            "question_text": "신청하려는데 소득 조건과 필요한 준비 서류가 무엇인지 궁금합니다.",
            "url": "https://kin.naver.com/qna/detail.naver?docId=123",
        }
        with patch.object(
            main,
            "run_naver_kin_single_question_playwright",
            return_value=(True, question),
        ):
            worker = main.NaverKinQuestionCollectorWorker(question["url"], result_queue)
            worker.run()

        self.assertEqual(result_queue.get_nowait(), ("naver_kin_direct_collected", question))

    def test_naver_kin_page_has_direct_url_flow_and_fixed_progress(self) -> None:
        source = self._method_source("_build_naver_kin_page")
        direct_handler = self._method_source("_handle_naver_kin_direct_collected")

        self.assertNotIn("네이버 지식인 최신 질문을 확인하고", source)
        self.assertNotIn('text="자동화 흐름"', source)
        self.assertNotIn("1. 지식인 질문 목록", source)
        self.assertIn('text="질문 목록 수집"', source)
        self.assertIn('text="수집건수"', source)
        self.assertIn("naver_kin_collect_count_menu", source)
        self.assertIn('values=[f"{count}개" for count in range(1, 11)]', source)
        self.assertIn(
            'values=["5분", "10분", "15분", "20분", "30분", "1시간", "2시간", "4시간", "6시간", "12시간", "24시간"]',
            source,
        )
        self.assertIn('text="지식인 URL"', source)
        self.assertIn('text="참고 자료"', source)
        self.assertIn("naver_kin_reference_textbox", source)
        self.assertIn("naver_kin_direct_collect_button", source)
        self.assertIn("naver_kin_clipboard_status_label", source)
        self.assertIn("지식인 상세 URL을 복사하면", source)
        self.assertIn("naver_kin_fixed_progress_bar", source)
        self.assertIn('(("writing", "글작성"), ("settings", "설정"))', source)
        self.assertIn("_build_naver_kin_settings_tab", source)
        self.assertIn("_start_naver_kin_question_worker", direct_handler)

    def test_three_naver_kin_profiles_use_distinct_browser_storage(self) -> None:
        profiles = main.normalize_naver_kin_profiles(
            [{"nickname": "나"}, {"nickname": "엄마"}, {"nickname": "업무"}]
        )

        self.assertEqual(len(profiles), 3)
        self.assertEqual(
            [profile["profile_scope"] for profile in profiles],
            list(main.NAVER_KIN_PROFILE_SCOPES),
        )
        self.assertEqual(len({profile["profile_path"] for profile in profiles}), 3)
        self.assertEqual(profiles[0]["profile_path"], str(main.NAVER_KIN_CHROME_PROFILE_DIR))
        self.assertEqual(
            main.naver_kin_profile_scope_for_name(profiles, "지식인 2"),
            main.NAVER_PLAYWRIGHT_PROFILE_KIN_2,
        )

    def test_saved_naver_login_marks_profile_as_registered(self) -> None:
        with patch.object(
            main,
            "load_naver_blog_storage_state",
            return_value={
                "cookies": [
                    {
                        "name": "NID_AUT",
                        "domain": ".naver.com",
                        "expires": time.time() + 3600,
                    }
                ]
            },
        ):
            self.assertTrue(
                main.has_saved_naver_login(main.NAVER_PLAYWRIGHT_PROFILE_KIN)
            )

        with patch.object(
            main,
            "load_naver_blog_storage_state",
            return_value={"cookies": [{"name": "NNB", "domain": ".naver.com"}]},
        ):
            self.assertFalse(
                main.has_saved_naver_login(main.NAVER_PLAYWRIGHT_PROFILE_KIN)
            )

    def test_naver_kin_nickname_is_read_from_signed_in_gnb(self) -> None:
        page = SimpleNamespace(evaluate=lambda _script: "정여사님")

        self.assertEqual(main.extract_naver_kin_nickname(page), "정여사")

    def test_profile_worker_forwards_detected_nickname(self) -> None:
        result_queue = queue.Queue()
        with patch.object(
            main,
            "run_naver_kin_profile_playwright",
            return_value=(
                True,
                {"message": "프로필 저장 완료", "nickname": "정여사"},
            ),
        ):
            worker = main.NaverKinProfileWorker(
                main.NAVER_PLAYWRIGHT_PROFILE_KIN_2,
                result_queue,
            )
            worker.run()

        event, payload = result_queue.get_nowait()
        self.assertEqual(event, "naver_kin_profile_done")
        self.assertEqual(payload["nickname"], "정여사")
        self.assertEqual(
            payload["profile_scope"], main.NAVER_PLAYWRIGHT_PROFILE_KIN_2
        )

    def test_kin_profile_ui_uses_saved_login_for_registered_label(self) -> None:
        writing_source = self._method_source(
            "_refresh_naver_kin_writing_profile_choices"
        )
        cards_source = self._method_source("_refresh_naver_kin_profile_cards")
        done_source = self._method_source("_handle_naver_kin_profile_done")

        self.assertIn("has_saved_naver_login(profile_scope)", writing_source)
        self.assertIn('"등록됨"', writing_source)
        self.assertIn("네이버 로그인 등록됨", cards_source)
        self.assertIn("_refresh_naver_kin_writing_profile_choices()", done_source)
        self.assertIn("_refresh_naver_kin_profile_cards()", done_source)
        self.assertIn('profile["nickname"] = nickname', done_source)
        self.assertIn("AppStateStore.save", done_source)

    def test_profile_scope_is_forwarded_to_collect_and_answer_workers(self) -> None:
        result_queue = queue.Queue()
        with patch.object(
            main,
            "run_naver_kin_single_question_playwright",
            return_value=(True, {"title": "질문"}),
        ) as collector:
            worker = main.NaverKinQuestionCollectorWorker(
                "https://kin.naver.com/qna/detail.naver?docId=123",
                result_queue,
                main.NAVER_PLAYWRIGHT_PROFILE_KIN_3,
            )
            worker.run()

        collector.assert_called_once_with(
            "https://kin.naver.com/qna/detail.naver?docId=123",
            result_queue,
            main.NAVER_PLAYWRIGHT_PROFILE_KIN_3,
        )
        worker = main.NaverKinAutomationWorker(
            main.WordPressSettings(),
            {},
            result_queue,
            main.NAVER_PLAYWRIGHT_PROFILE_KIN_2,
        )
        self.assertEqual(worker.profile_scope, main.NAVER_PLAYWRIGHT_PROFILE_KIN_2)

    def test_profile_login_window_stays_open_until_user_closes_it(self) -> None:
        source = self._method_source("run_naver_kin_profile_playwright")

        self.assertIn("로그인 확인 완료 · 프로필을 저장했습니다.", source)
        self.assertIn("Chrome 창을 직접 닫아주세요.", source)
        self.assertIn("while True:", source)
        self.assertIn("if not open_pages:\n                    break", source)
        self.assertIn("extract_naver_kin_nickname(page)", source)
        self.assertIn("candidate_nickname", source)
        self.assertLess(
            source.index("save_naver_blog_storage_state(context, scope)"),
            source.index("# Do not close a successfully authenticated profile immediately."),
        )

    def test_completion_uses_styled_dialog_and_opens_answer_url(self) -> None:
        handler_source = self._method_source("_handle_naver_kin_automation_done")
        dialog_source = self._method_source("_show_naver_kin_complete_dialog")
        open_source = self._method_source("_open_naver_kin_completed_answer")

        self.assertIn(
            "if was_direct:\n            self._show_naver_kin_complete_dialog(question_url)",
            handler_source,
        )
        self.assertNotIn("messagebox.showinfo", handler_source)
        self.assertIn('text="지식인 답변 등록이 완료되었습니다."', dialog_source)
        self.assertIn('text="보러가기"', dialog_source)
        self.assertIn('text="확인"', dialog_source)
        self.assertIn("format_daily_publish_usage", dialog_source)
        self.assertIn("format_naver_kin_answer_usage", dialog_source)
        self.assertIn("self._open_source_url(question_url)", open_source)

    def test_interval_automation_completion_does_not_open_modal_dialog(self) -> None:
        events = []
        app = SimpleNamespace(
            naver_kin_questions=[],
            naver_kin_automation_worker=object(),
            naver_kin_direct_mode=False,
            naver_kin_automation_running=True,
            naver_kin_next_run_at=0,
            _refresh_daily_publish_limit_statuses=lambda: None,
            _persist_naver_kin_schedule_state=lambda: None,
            _render_naver_kin_questions=lambda _questions: None,
            _append_naver_kin_run_log=lambda _message: None,
            _set_naver_kin_progress=lambda *_args, **_kwargs: None,
            _update_quick_status=lambda *_args: None,
            _naver_kin_daily_answer_count=lambda: 1,
            _stop_naver_kin_automation_for_daily_limit=lambda count: events.append(("stop", count)),
            _show_naver_kin_complete_dialog=lambda url: events.append(("dialog", url)),
            _next_naver_kin_question_for_automation=lambda: None,
            _naver_kin_collect_interval_minutes=lambda: 30,
            _schedule_next_naver_kin_automation=lambda **kwargs: events.append(("schedule", kwargs)),
            _update_naver_kin_next_run_label=lambda: None,
        )

        main.KeywordApp._handle_naver_kin_automation_done(
            app,
            {"question_url": "https://kin.naver.com/qna/detail.naver?docId=123"},
        )

        self.assertFalse(any(event[0] == "dialog" for event in events))
        self.assertTrue(any(event[0] == "schedule" for event in events))

    def test_direct_url_completion_still_opens_modal_dialog(self) -> None:
        dialogs = []
        app = SimpleNamespace(
            naver_kin_questions=[],
            naver_kin_automation_worker=object(),
            naver_kin_direct_mode=True,
            naver_kin_automation_running=False,
            naver_kin_next_run_at=0,
            _refresh_daily_publish_limit_statuses=lambda: None,
            _set_naver_kin_direct_button_state=lambda _running: None,
            _persist_naver_kin_schedule_state=lambda: None,
            _render_naver_kin_questions=lambda _questions: None,
            _append_naver_kin_run_log=lambda _message: None,
            _set_naver_kin_progress=lambda *_args, **_kwargs: None,
            _update_quick_status=lambda *_args: None,
            _naver_kin_daily_answer_count=lambda: 1,
            _stop_naver_kin_automation_for_daily_limit=lambda count: None,
            _show_naver_kin_complete_dialog=lambda url: dialogs.append(url),
            _update_naver_kin_next_run_label=lambda: None,
        )
        question_url = "https://kin.naver.com/qna/detail.naver?docId=456"

        main.KeywordApp._handle_naver_kin_automation_done(
            app,
            {"question_url": question_url},
        )

        self.assertEqual(dialogs, [question_url])

    def test_interval_automation_stops_without_dialog_at_thirty_answers(self) -> None:
        events = []
        app = SimpleNamespace(
            naver_kin_questions=[],
            naver_kin_automation_worker=object(),
            naver_kin_direct_mode=False,
            naver_kin_automation_running=True,
            naver_kin_next_run_at=0,
            _refresh_daily_publish_limit_statuses=lambda: None,
            _persist_naver_kin_schedule_state=lambda: None,
            _render_naver_kin_questions=lambda _questions: None,
            _append_naver_kin_run_log=lambda _message: None,
            _set_naver_kin_progress=lambda *_args, **_kwargs: None,
            _update_quick_status=lambda *_args: None,
            _naver_kin_daily_answer_count=lambda: 30,
            _stop_naver_kin_automation_for_daily_limit=lambda count: events.append(("stop", count)),
            _show_naver_kin_complete_dialog=lambda url: events.append(("dialog", url)),
            _next_naver_kin_question_for_automation=lambda: {},
            _naver_kin_answer_interval_minutes=lambda: 5,
            _schedule_next_naver_kin_automation=lambda **kwargs: events.append(("schedule", kwargs)),
            _update_naver_kin_next_run_label=lambda: None,
        )

        main.KeywordApp._handle_naver_kin_automation_done(
            app,
            {
                "question_url": "https://kin.naver.com/qna/detail.naver?docId=789",
                "naver_kin_answer_count": 30,
            },
        )

        self.assertIn(("stop", 30), events)
        self.assertFalse(any(event[0] == "dialog" for event in events))
        self.assertFalse(any(event[0] == "schedule" for event in events))

    def test_scheduled_run_does_not_start_when_daily_limit_is_already_full(self) -> None:
        stopped = []
        app = SimpleNamespace(
            _naver_kin_automation_job=object(),
            naver_kin_automation_running=True,
            _naver_kin_daily_answer_count=lambda: 30,
            _stop_naver_kin_automation_for_daily_limit=lambda count: stopped.append(count),
        )

        main.KeywordApp._run_naver_kin_automation_once(app)

        self.assertEqual(stopped, [30])

    def test_direct_thirtieth_answer_still_opens_completion_dialog(self) -> None:
        events = []
        app = SimpleNamespace(
            naver_kin_questions=[],
            naver_kin_automation_worker=object(),
            naver_kin_direct_mode=True,
            naver_kin_automation_running=False,
            naver_kin_next_run_at=0,
            _refresh_daily_publish_limit_statuses=lambda: None,
            _set_naver_kin_direct_button_state=lambda _running: None,
            _persist_naver_kin_schedule_state=lambda: None,
            _render_naver_kin_questions=lambda _questions: None,
            _append_naver_kin_run_log=lambda _message: None,
            _set_naver_kin_progress=lambda *_args, **_kwargs: None,
            _update_quick_status=lambda *_args: None,
            _naver_kin_daily_answer_count=lambda: 30,
            _stop_naver_kin_automation_for_daily_limit=lambda count: events.append(("stop", count)),
            _show_naver_kin_complete_dialog=lambda url: events.append(("dialog", url)),
            _update_naver_kin_next_run_label=lambda: None,
        )
        question_url = "https://kin.naver.com/qna/detail.naver?docId=999"

        main.KeywordApp._handle_naver_kin_automation_done(
            app,
            {
                "question_url": question_url,
                "naver_kin_answer_count": 30,
            },
        )

        self.assertIn(("stop", 30), events)
        self.assertIn(("dialog", question_url), events)

    def test_daily_limit_stop_path_does_not_open_a_messagebox(self) -> None:
        source = self._method_source("_stop_naver_kin_automation_for_daily_limit")

        self.assertNotIn("messagebox", source)
        self.assertIn("naver_kin_automation_running = False", source)
        self.assertIn("naver_kin_next_run_at = 0", source)

    def test_today_answer_history_backfills_daily_counter(self) -> None:
        app = SimpleNamespace(
            naver_kin_questions=[
                {
                    "url": "https://kin.naver.com/qna/detail.naver?docId=1",
                    "answered_at": "2026-09-07 09:00:00",
                },
                {
                    "url": "https://kin.naver.com/qna/detail.naver?docId=2",
                    "answered_at": "2026-09-07 10:00:00",
                },
                {
                    "url": "https://kin.naver.com/qna/detail.naver?docId=3",
                    "answered_at": "2026-09-06 10:00:00",
                },
            ]
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(
                main,
                "DAILY_PUBLISH_COUNTS_FILE",
                Path(directory) / "daily-publish-counts.json",
            ),
            patch.object(time, "strftime", return_value="2026-09-07"),
        ):
            count = main.KeywordApp._naver_kin_daily_answer_count(app)

        self.assertEqual(count, 2)

    def test_daily_answer_counts_are_isolated_per_kin_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch.object(
            main,
            "DAILY_PUBLISH_COUNTS_FILE",
            Path(directory) / "daily-publish-counts.json",
        ):
            profile_one_account = main.naver_kin_daily_answer_account(
                main.NAVER_PLAYWRIGHT_PROFILE_KIN
            )
            main.DailyPublishLimitStore.record_success("naver_kin", profile_one_account)
            app = SimpleNamespace(
                naver_kin_questions=[],
                _selected_naver_kin_profile_scope=lambda: main.NAVER_PLAYWRIGHT_PROFILE_KIN,
            )
            self.assertEqual(main.KeywordApp._naver_kin_daily_answer_count(app), 1)

            app._selected_naver_kin_profile_scope = (
                lambda: main.NAVER_PLAYWRIGHT_PROFILE_KIN_2
            )
            self.assertEqual(main.KeywordApp._naver_kin_daily_answer_count(app), 0)


if __name__ == "__main__":
    unittest.main()
