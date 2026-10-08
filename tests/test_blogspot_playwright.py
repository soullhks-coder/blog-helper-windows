from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main


class BlogspotPlaywrightTests(unittest.TestCase):
    def test_each_compose_image_waits_after_selecting_extra_large_size(self) -> None:
        for available_label in ("매우 크게", "아주 크게"):
            with self.subTest(label=available_label):
                events = []

                def locator_for(name, *, visible=True):
                    candidate = Mock()
                    candidate.is_visible.return_value = True
                    candidate.click.side_effect = lambda **_kwargs: events.append(("click", name))
                    locator = Mock()
                    locator.count.return_value = int(visible)
                    locator.nth.return_value = candidate
                    return locator

                page = Mock()
                page.get_by_role.side_effect = lambda _role, name, exact: locator_for(name)
                page.get_by_text.side_effect = lambda name, exact: locator_for(
                    name, visible=name == available_label
                )
                page.wait_for_timeout.side_effect = lambda timeout: events.append(("wait", timeout))
                image = Mock()
                image.click.side_effect = lambda: events.append(("click", "image"))
                image.evaluate.side_effect = [
                    {"width": "320"}, {"width": "640"},
                    {"width": "320"}, {"width": "640"},
                ]

                with (
                    patch.object(main, "find_blogspot_compose_image", return_value=image),
                    patch.object(main, "_find_blogspot_image_edit_dialog", return_value=None),
                    patch.object(main, "append_runtime_log"),
                ):
                    for image_source in ("first-image", "second-image"):
                        main.configure_blogspot_inserted_image(page, image_source)
                        events.append(("next", image_source))
                        self.assertEqual(
                            events[-3:],
                            [("click", available_label), ("wait", 1_500), ("next", image_source)],
                        )
                self.assertEqual(events.count(("wait", 1_500)), 2)

    def test_image_edit_dialog_waits_after_size_click_before_update(self) -> None:
        events = []
        choice = Mock()
        choice.get_attribute.return_value = "false"
        choice.evaluate.return_value = False
        choice.click.side_effect = lambda: events.append("size-click")
        update = Mock()
        update.click.side_effect = lambda: events.append("update-click")

        def locator_for(candidate):
            candidate.is_visible.return_value = True
            locator = Mock()
            locator.count.return_value = 1
            locator.nth.return_value = candidate
            return locator

        dialog = Mock()
        dialog.get_by_role.side_effect = lambda role, **_kwargs: locator_for(
            choice if role == "radio" else update
        )
        dialog.wait_for.side_effect = lambda **_kwargs: events.append("dialog-hidden")
        page = Mock()
        page.wait_for_timeout.side_effect = lambda delay: events.append(("wait", delay))
        image = Mock()
        image.evaluate.return_value = {"width": "640"}
        with patch.object(main, "append_runtime_log"):
            main._apply_blogspot_image_size_dialog(page, dialog, image, {"width": "320"})
        self.assertEqual(events, ["size-click", ("wait", 1_500), "update-click", "dialog-hidden"])
        choice.click.assert_called_once_with()
        update.click.assert_called_once_with()

    def test_slow_size_change_keeps_waiting_without_clicking_another_control(self) -> None:
        page = Mock()
        image = Mock()
        image.evaluate.side_effect = [{"width": "320"}] * 5 + [{"width": "640"}]
        with patch.object(main, "append_runtime_log"):
            main._wait_for_blogspot_image_size(page, image, {"width": "320"})
        self.assertEqual([call.args[0] for call in page.wait_for_timeout.call_args_list], [250] * 5)
        page.get_by_role.assert_not_called()
        image.click.assert_not_called()

    def test_open_image_dialog_is_applied_before_any_alignment_click(self) -> None:
        events = []
        page = Mock()
        image = Mock()
        image.evaluate.return_value = {"width": "320"}
        dialog = Mock()
        with (
            patch.object(main, "find_blogspot_compose_image", return_value=image),
            patch.object(main, "_find_blogspot_image_edit_dialog", return_value=dialog),
            patch.object(main, "_apply_blogspot_image_size_dialog", side_effect=lambda *_args: events.append("size-and-wait")),
            patch.object(main, "_center_blogspot_inserted_image", side_effect=lambda *_args: events.append("center")),
        ):
            main.configure_blogspot_inserted_image(page, "source")
        self.assertEqual(events, ["size-and-wait", "center"])

    def test_unapplied_size_change_does_not_silently_continue(self) -> None:
        page = Mock()
        image = Mock()
        image.evaluate.return_value = {"width": "320"}
        with patch.object(main, "append_runtime_log"):
            with self.assertRaisesRegex(RuntimeError, "반영되지 않아 다음 작업"):
                main._wait_for_blogspot_image_size(page, image, {"width": "320"})
        self.assertEqual(page.wait_for_timeout.call_count, 32)

    def test_new_image_url_alone_does_not_count_as_finished_resize(self) -> None:
        page = Mock()
        image = Mock()
        image.evaluate.side_effect = [
            {"src": "new-url", "width": "320", "height": "240"},
            {"src": "new-url", "width": "640"},
        ]
        with patch.object(main, "append_runtime_log"):
            main._wait_for_blogspot_image_size(page, image, {"src": "old-url", "width": "320"})
        page.wait_for_timeout.assert_called_once_with(250)

    def test_already_extra_large_image_is_allowed_to_continue(self) -> None:
        for width in ("640", "800"):
            with self.subTest(width=width):
                page = Mock()
                image = Mock()
                image.evaluate.return_value = {"width": width}
                with patch.object(main, "append_runtime_log"):
                    main._wait_for_blogspot_image_size(page, image, {"width": width})
                page.wait_for_timeout.assert_not_called()

    def test_legacy_layout_waits_before_selecting_alignment(self) -> None:
        for already_selected in (False, True):
            with self.subTest(already_selected=already_selected):
                events = []

                def locator_for(name):
                    candidate = Mock()
                    candidate.is_visible.return_value = True
                    candidate.get_attribute.return_value = (
                        "true" if name == "아주 크게" and already_selected else "false"
                    )
                    candidate.click.side_effect = lambda: events.append(("click", name))
                    locator = Mock()
                    locator.count.return_value = 1
                    locator.nth.return_value = candidate
                    return locator

                page = Mock()
                page.get_by_role.side_effect = lambda _role, name, exact: locator_for(name)
                page.wait_for_timeout.side_effect = lambda timeout: events.append(("wait", timeout))
                with patch.object(main, "append_runtime_log"):
                    main._select_blogspot_layout_choice(page, "아주 크게")
                    main._select_blogspot_layout_choice(page, "가운데")
                    events.append(("next", "confirm"))

                expected = [("wait", 1_500), ("click", "가운데"), ("next", "confirm")]
                if not already_selected:
                    expected.insert(0, ("click", "아주 크게"))
                self.assertEqual(events, expected)

    def test_image_button_is_revealed_from_the_responsive_toolbar(self) -> None:
        class CandidateStub:
            def __init__(self, page, *, aria_haspopup=None) -> None:
                self.page = page
                self.aria_haspopup = aria_haspopup
                self.clicked = False

            def is_visible(self) -> bool:
                return True

            def get_attribute(self, name: str):
                if name == "aria-haspopup":
                    return self.aria_haspopup
                if name == "aria-disabled":
                    return "false"
                return None

            def click(self, force: bool = False) -> None:
                self.clicked = True
                if self.aria_haspopup is None:
                    self.page.expanded = True

        class LocatorStub:
            def __init__(self, candidates) -> None:
                self.candidates = candidates

            def count(self) -> int:
                return len(self.candidates)

            def nth(self, index: int):
                return self.candidates[index]

        class PageStub:
            def __init__(self) -> None:
                self.expanded = False
                self.header_more = CandidateStub(self, aria_haspopup="true")
                self.toolbar_more = CandidateStub(self)
                self.image_button = CandidateStub(self)

            def get_by_role(self, _role: str, name=None):
                pattern = getattr(name, "pattern", "")
                if "Insert image" in pattern:
                    return LocatorStub([self.image_button] if self.expanded else [])
                return LocatorStub([self.header_more, self.toolbar_more])

            def wait_for_timeout(self, _timeout: int) -> None:
                pass

        page = PageStub()
        with patch.object(main, "append_runtime_log"):
            button = main.find_blogspot_image_insert_button(page)

        self.assertIs(button, page.image_button)
        self.assertFalse(page.header_more.clicked)
        self.assertTrue(page.toolbar_more.clicked)

    def test_writing_targets_include_blogspot_after_tistory(self) -> None:
        source = inspect.getsource(main.KeywordApp._build_writing_page)
        wordpress = source.index('("wordpress", "워드프레스")')
        tistory = source.index('("tistory", "티스토리")')
        blogspot = source.index('("blogspot", "블로그스팟")')
        self.assertLess(wordpress, tistory)
        self.assertLess(tistory, blogspot)

    def test_blogspot_settings_are_playwright_only(self) -> None:
        source = inspect.getsource(main.KeywordApp._build_blogspot_card)
        self.assertIn("Playwright 전용 Chrome", source)
        self.assertIn("로그인/프로필 확인", source)
        self.assertIn("blogspot_save_mode_var", source)
        self.assertNotIn("Client Secret", source)
        self.assertNotIn("Google 인증", source)

    def test_publish_pipeline_uses_playwright_instead_of_blogger_api(self) -> None:
        source = inspect.getsource(main.PublishPipelineWorker.run)
        self.assertIn("run_blogspot_playwright_automation", source)
        blogspot_block = source[source.index("blogspot_result = None") : source.index("threads_result = None")]
        self.assertNotIn("BlogspotClient", blogspot_block)
        self.assertNotIn("blogspot_access_token", blogspot_block)

    def test_blogspot_profile_and_publish_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                blogspot_blog_id="123456",
                blogspot_blog_url="https://example.blogspot.com/",
                blogspot_blog_name="테스트 블로그",
                blogspot_save_mode=main.TISTORY_SAVE_MODE_DRAFT,
                blogspot_reference_image_protection_mode=True,
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()
        self.assertEqual(loaded.blogspot_blog_id, "123456")
        self.assertEqual(loaded.blogspot_blog_url, "https://example.blogspot.com/")
        self.assertEqual(loaded.blogspot_blog_name, "테스트 블로그")
        self.assertEqual(loaded.blogspot_save_mode, main.TISTORY_SAVE_MODE_DRAFT)
        self.assertTrue(loaded.blogspot_reference_image_protection_mode)

    def test_local_images_are_removed_from_html_and_queued_for_native_upload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "card.png"
            thumbnail_path = Path(directory) / "thumb.png"
            image_path.write_bytes(b"image")
            thumbnail_path.write_bytes(b"thumbnail")
            html = (
                f'<p>본문</p><figure><img src="{image_path}"></figure>'
                f'<figure class="blog-helper-inline-image" '
                f'data-blog-helper-inline-image-path="{image_path}">참고 이미지</figure>'
                '<img src="data:image/png;base64,AAAA">'
                '<img src="https://example.com/remote.jpg">'
            )
            cleaned, images = main.prepare_blogspot_html_and_images(
                html,
                str(thumbnail_path),
            )
        self.assertNotIn("data:image", cleaned)
        self.assertNotIn(str(image_path), cleaned)
        self.assertIn("https://example.com/remote.jpg", cleaned)
        self.assertEqual(images, [str(thumbnail_path), str(image_path)])

    def test_image_slots_are_distributed_and_removed_after_upload(self) -> None:
        html = "".join(
            [
                "<h2>첫째</h2><p>첫 문단</p>",
                "<h2>둘째</h2><p>둘째 문단</p>",
                "<h2>셋째</h2><p>셋째 문단</p>",
                "<h2>넷째</h2><p>넷째 문단</p>",
            ]
        )
        slotted, markers = main.insert_blogspot_image_slot_markers(html, 3)
        self.assertEqual(len(markers), 3)
        self.assertEqual(slotted.count(main.BLOGSPOT_IMAGE_SLOT_PREFIX), 3)
        self.assertLess(slotted.index(markers[0]), slotted.index(markers[1]))
        self.assertLess(slotted.index(markers[1]), slotted.index(markers[2]))
        cleaned = slotted
        for marker in markers:
            cleaned = main.remove_blogspot_image_slot_marker(cleaned, marker)
        self.assertNotIn(main.BLOGSPOT_IMAGE_SLOT_PREFIX, cleaned)

    def test_thumbnail_slot_is_pinned_above_article_body(self) -> None:
        html = "".join(
            [
                "<h2>첫째</h2><p>첫 문단</p>",
                "<h2>둘째</h2><p>둘째 문단</p>",
                "<h2>셋째</h2><p>셋째 문단</p>",
            ]
        )
        slotted, markers = main.insert_blogspot_image_slot_markers(
            html,
            3,
            first_image_at_top=True,
        )
        self.assertTrue(slotted.startswith(f"<p>{markers[0]}</p>\n<h2>"))
        self.assertLess(slotted.index(markers[0]), slotted.index("<h2>"))
        self.assertGreater(slotted.index(markers[1]), slotted.index("<h2>"))
        self.assertGreater(slotted.index(markers[2]), slotted.index(markers[1]))

    def test_prompt_tag_footer_is_moved_to_blogspot_labels(self) -> None:
        html = (
            "<h2>본문 제목</h2><p>본문 내용입니다.</p>"
            "<p><strong>주요 태그:</strong> 블로그스팟, 자동 포스팅, AI 글쓰기</p>"
        )
        cleaned, labels = main.extract_blogspot_prompt_labels(html)
        self.assertEqual(labels, ["블로그스팟", "자동 포스팅", "AI 글쓰기"])
        self.assertNotIn("주요 태그", cleaned)
        self.assertIn("본문 내용입니다.", cleaned)

    def test_unlabeled_comma_tag_footer_is_supported(self) -> None:
        html = (
            "<h2>본문 제목</h2><p>본문 내용입니다.</p>"
            "<p>#블로그스팟, #자동포스팅, #Blogger</p>"
        )
        cleaned, labels = main.extract_blogspot_prompt_labels(html)
        self.assertEqual(labels, ["블로그스팟", "자동포스팅", "Blogger"])
        self.assertNotIn("#블로그스팟", cleaned)

    def test_normal_final_sentence_is_not_used_as_blogspot_labels(self) -> None:
        html = "<h2>마무리</h2><p>가격, 후기, 신청 방법을 차례로 확인해 보세요.</p>"
        cleaned, labels = main.extract_blogspot_prompt_labels(html)
        self.assertEqual(labels, [])
        self.assertEqual(cleaned, html)

    def test_blogger_editor_uses_open_html_option_and_codemirror(self) -> None:
        source = inspect.getsource(main.run_blogspot_playwright_automation)
        html_switch_source = inspect.getsource(main.open_blogspot_html_editor)
        view_switch_source = inspect.getsource(main._open_blogspot_view_option)
        html_fill_source = inspect.getsource(main.fill_blogspot_html_editor)
        upload_source = inspect.getsource(main.upload_blogspot_images)
        image_button_source = inspect.getsource(
            main.find_blogspot_image_insert_button
        )
        current_layout_source = inspect.getsource(
            main.configure_blogspot_inserted_image
        )
        publish_confirm_source = inspect.getsource(
            main.confirm_blogspot_publish_dialog
        )
        label_input_source = inspect.getsource(main.fill_blogspot_labels)
        self.assertIn("fill_blogspot_html_editor", source)
        self.assertIn('[role="listbox"][aria-label="보기 전환"]:visible', view_switch_source)
        self.assertIn('[role="option"][data-value="{option_value}"]:visible', view_switch_source)
        self.assertIn('option.first.press("Enter")', view_switch_source)
        self.assertIn('page.keyboard.press("Escape")', view_switch_source)
        self.assertIn("switcher.click(force=True)", view_switch_source)
        self.assertIn('page.locator(".CodeMirror:visible")', html_switch_source)
        self.assertNotIn('get_by_text("HTML 보기", exact=True)', html_switch_source)
        self.assertIn("element.CodeMirror.setValue", html_fill_source)
        self.assertIn("element.CodeMirror.getValue", html_fill_source)
        self.assertIn('textarea[aria-label*="라벨을 구분"]:visible', label_input_source)
        self.assertIn("extract_blogspot_prompt_labels(article_html)", source)
        self.assertIn("fill_blogspot_labels", source)
        self.assertIn("field.press_sequentially", label_input_source)
        self.assertIn('field.press(",")', label_input_source)
        self.assertIn("field.input_value()", label_input_source)
        self.assertNotIn("field.fill", label_input_source)
        self.assertNotIn('str(tag or "").strip() for tag in tag_names', source)
        self.assertIn('[role="menuitem"]:visible', upload_source)
        self.assertIn("find_blogspot_image_insert_button", upload_source)
        self.assertIn('name=re.compile(r"^(?:옵션 더보기|More options)$"', image_button_source)
        self.assertIn('candidate.get_attribute("aria-haspopup") == "true"', image_button_source)
        self.assertIn("candidate.click(force=True)", image_button_source)
        self.assertIn('upload_option.press("Enter")', upload_source)
        self.assertNotIn('get_by_text("컴퓨터에서 업로드"', upload_source)
        self.assertIn('iframe[src*="docs.google.com/picker"]:visible', upload_source)
        self.assertIn("expect_file_chooser", upload_source)
        self.assertIn("set_files(valid_paths)", upload_source)
        self.assertIn("page.wait_for_timeout(2_500)", upload_source)
        self.assertIn('re.compile(r"^\\s*레이아웃 선택\\s*$")', upload_source)
        self.assertIn("last_picker_action_at", upload_source)
        self.assertIn("page.wait_for_timeout(2_000)", upload_source)
        self.assertIn("collect_blogspot_compose_image_sources", upload_source)
        self.assertIn("configure_blogspot_inserted_image", upload_source)
        self.assertIn('"blogger.googleusercontent.com"', upload_source)
        self.assertIn('name="가운데 정렬"', inspect.getsource(main._center_blogspot_inserted_image))
        self.assertIn("_center_blogspot_inserted_image", current_layout_source)
        self.assertIn('(\"매우 크게\", \"아주 크게\")', current_layout_source)
        self.assertIn('_select_blogspot_layout_choice(page, "아주 크게")', upload_source)
        self.assertIn('_select_blogspot_layout_choice(page, "가운데")', upload_source)
        self.assertIn('get_by_role("button", name="확인", exact=True)', upload_source)
        self.assertIn("return len(valid_paths)", upload_source)
        self.assertIn("글을\\s*게시하시겠습니까", publish_confirm_source)
        self.assertIn('name="확인", exact=True', publish_confirm_source)
        self.assertIn("confirm_button.click(force=True)", publish_confirm_source)
        self.assertIn("확인창 닫힘 확인", publish_confirm_source)
        self.assertIn("confirm_blogspot_publish_dialog(page)", source)

    def test_blogger_toggles_compose_mode_for_each_body_image(self) -> None:
        source = inspect.getsource(main.run_blogspot_playwright_automation)
        compose_source = inspect.getsource(main.open_blogspot_compose_editor)
        focus_source = inspect.getsource(main.focus_blogspot_image_slot)
        pipeline_source = inspect.getsource(main.PublishPipelineWorker.run)
        settings_source = inspect.getsource(main.KeywordApp._build_blogspot_card)
        self.assertIn("insert_blogspot_image_slot_markers", source)
        self.assertIn("first_image_at_top=thumbnail_at_top", source)
        self.assertIn("focus_blogspot_image_slot", source)
        self.assertIn("remove_blogspot_image_slot_marker", source)
        self.assertIn('_open_blogspot_view_option(page, "compose")', compose_source)
        self.assertIn("range.collapse(true)", focus_source)
        self.assertIn("collect_tistory_reference_image_files", source)
        self.assertIn("reference_image_protection_mode", source)
        self.assertIn("blogspot_reference_image_protection_mode", pipeline_source)
        self.assertIn("저작권 보호 모드", settings_source)


if __name__ == "__main__":
    unittest.main()
