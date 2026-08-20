import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class TistoryNativeImageTests(unittest.TestCase):
    def test_published_entry_url_is_read_from_tistory_publish_sheet(self) -> None:
        class FakePage:
            url = "https://soullhka.tistory.com/manage/newpost/"

            def evaluate(self, _script: str) -> list[str]:
                return [
                    "URL https://soullhka.tistory.com/entry/혹세무민-뜻과-유래",
                ]

        self.assertEqual(
            main.find_tistory_published_entry_url(FakePage()),
            "https://soullhka.tistory.com/entry/%ED%98%B9%EC%84%B8%EB%AC%B4%EB%AF%BC-%EB%9C%BB%EA%B3%BC-%EC%9C%A0%EB%9E%98",
        )

    def test_relative_tistory_entry_url_uses_blog_origin(self) -> None:
        self.assertEqual(
            main.normalize_tistory_entry_url(
                "/entry/테스트-글",
                "https://example.tistory.com/manage/newpost/",
            ),
            "https://example.tistory.com/entry/%ED%85%8C%EC%8A%A4%ED%8A%B8-%EA%B8%80",
        )

    def test_default_tistory_entry_is_rewritten_to_configured_custom_domain(self) -> None:
        self.assertEqual(
            main.normalize_tistory_entry_url(
                "https://soullhka.tistory.com/entry/인공눈물-안전-사용법",
                "https://info.soullhk.kr/",
            ),
            "https://info.soullhk.kr/entry/%EC%9D%B8%EA%B3%B5%EB%88%88%EB%AC%BC-%EC%95%88%EC%A0%84-%EC%82%AC%EC%9A%A9%EB%B2%95",
        )

    def test_publish_sheet_action_labels_are_never_treated_as_entry_slug(self) -> None:
        broken_action_slug = "https://soullhka.tistory.com/entry/ªË¡¶√Îº“∞¯∞≥"

        self.assertEqual(
            main.normalize_tistory_entry_url(
                broken_action_slug,
                "https://info.soullhk.kr/",
            ),
            "",
        )

    def test_rss_match_returns_only_the_actual_published_entry_url(self) -> None:
        rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item>
            <title>김민재 주장 완장 착용, 바이에른 뮌헨 제주SK전 선발 출격 소식</title>
            <link>https://info.soullhk.kr/entry/actual-entry-42</link>
          </item>
        </channel></rss>"""

        self.assertEqual(
            main.find_tistory_entry_url_in_rss(
                rss_xml,
                "https://info.soullhk.kr/",
                "김민재 주장 완장 착용,  바이에른 뮌헨 제주SK전 선발 출격 소식",
            ),
            "https://info.soullhk.kr/entry/actual-entry-42",
        )

    def test_rss_mismatch_never_guesses_an_entry_url(self) -> None:
        rss_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel>
          <item>
            <title>다른 글</title>
            <link>https://info.soullhk.kr/entry/other-entry</link>
          </item>
        </channel></rss>"""

        self.assertEqual(
            main.find_tistory_entry_url_in_rss(
                rss_xml,
                "https://info.soullhk.kr/",
                "맨시티 포든 방한 소식",
            ),
            "",
        )

    def test_representative_image_replaces_old_preview_through_thumb_box_input(self) -> None:
        class FakeInput:
            selected_path = ""

            def set_input_files(self, value) -> None:
                if value:
                    self.selected_path = str(value)

            def evaluate(self, _script: str) -> str:
                return Path(self.selected_path).name

        class FakeLocator:
            def __init__(self, candidate: FakeInput) -> None:
                self.candidate = candidate

            def count(self) -> int:
                return 1

            def nth(self, _index: int) -> FakeInput:
                return self.candidate

        class FakePage:
            def __init__(self) -> None:
                self.input = FakeInput()
                self.selector = ""

            def locator(self, selector: str) -> FakeLocator:
                self.selector = selector
                return FakeLocator(self.input)

            def wait_for_timeout(self, _milliseconds: int) -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "현재_글_썸네일.png"
            image_path.write_bytes(b"current-thumbnail")
            page = FakePage()
            events: queue.Queue = queue.Queue()

            with (
                patch.object(main, "wait_for_tistory_publish_panel", return_value=True),
                patch.object(
                    main,
                    "collect_tistory_representative_preview_signatures",
                    side_effect=[["old-preview"], ["new-preview"]],
                ),
                patch.object(main, "remove_tistory_existing_representative_image", return_value=True) as remove,
            ):
                attached = main.attach_tistory_representative_image_file(
                    page,
                    str(image_path),
                    events,
                )

            self.assertTrue(attached)
            remove.assert_called_once_with(page)
            self.assertIn(".box_thumb", page.selector)
            self.assertEqual(Path(page.input.selected_path), image_path.resolve())

    def test_thumbnail_filename_uses_title_and_underscores(self) -> None:
        self.assertEqual(
            main.build_thumbnail_filename("심규덕 변호사 핵심 정보"),
            "심규덕_변호사_핵심_정보.png",
        )
        self.assertEqual(
            main.build_thumbnail_filename('제목: 테스트/확인?'),
            "제목_테스트확인.png",
        )

    def test_fresh_upload_url_never_reuses_previous_image(self) -> None:
        old_url = "https://blog.kakaocdn.net/dna/old/image/img.png?x=1"
        new_url = "https://blog.kakaocdn.net/dna/new/image/img.png?x=2"

        self.assertEqual(
            main.choose_fresh_tistory_image_url({old_url}, [old_url]),
            "",
        )
        self.assertEqual(
            main.choose_fresh_tistory_image_url({old_url}, [old_url, new_url]),
            new_url,
        )

    def test_attachment_response_json_returns_uploaded_url(self) -> None:
        uploaded_url = "https://blog.kakaocdn.net/dna/new-key/image/img.png?credential=test"
        response = '{"name":"card.png","url":"' + uploaded_url + '","size":123}'

        self.assertEqual(main.extract_tistory_attachment_url(response), uploaded_url)
        self.assertEqual(main.extract_tistory_attachment_url({"url": uploaded_url}), uploaded_url)

    def test_local_cardnews_placeholder_becomes_native_upload_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "body-cardnews-test.png"
            image_path.write_bytes(b"test-image")
            source = "<h2>핵심 내용</h2>" + main.build_cardnews_image_figure(image_path, "테스트", 1)

            prepared, native_files = main.prepare_tistory_native_attachment_html(source, "테스트")

            self.assertEqual(list(native_files.values()), [str(image_path)])
            self.assertIn("__BLOG_HELPER_TISTORY_NATIVE_IMAGE_1__", prepared)
            self.assertNotIn(str(image_path), prepared)
            self.assertNotIn("data:image", prepared)
            self.assertIn("blog-helper-cardnews-image", prepared)

    def test_reference_image_query_combines_title_and_meaningful_tags(self) -> None:
        query = main.build_tistory_reference_image_query(
            "일본 지진 건물 붕괴 소식",
            ["#일본 지진", "건물 붕괴", "여진", "여진", "추가 태그"],
        )

        self.assertEqual(
            query,
            "일본 지진 건물 붕괴 소식 일본 지진 건물 붕괴 여진",
        )

    def test_reference_image_protection_mode_defaults_to_disabled(self) -> None:
        settings = main.WordPressSettings()

        self.assertFalse(settings.tistory_reference_image_protection_mode)
        self.assertIn(
            main.TISTORY_PROTECTION_OFF_MIGRATION,
            settings.applied_settings_migrations,
        )

    def test_tistory_input_mode_defaults_to_fast_and_normalizes_unknown_values(self) -> None:
        self.assertEqual(
            main.WordPressSettings().tistory_input_mode,
            main.TEXT_INPUT_MODE_FAST,
        )

    def test_tistory_save_mode_defaults_to_public_and_normalizes_unknown_values(self) -> None:
        self.assertEqual(
            main.WordPressSettings().tistory_save_mode,
            main.TISTORY_SAVE_MODE_PUBLISH,
        )
        self.assertEqual(
            main.normalize_tistory_save_mode("알 수 없는 저장 방식"),
            main.TISTORY_SAVE_MODE_PUBLISH,
        )
        self.assertEqual(
            main.normalize_text_input_mode("알 수 없는 모드"),
            main.TEXT_INPUT_MODE_FAST,
        )

    def test_direct_typing_script_types_title_and_body_sequentially(self) -> None:
        script = main.build_tistory_editor_automation_script(
            "직접 타이핑 제목",
            "<p>직접 타이핑 본문</p>",
            input_mode=main.TEXT_INPUT_MODE_TYPING,
        )

        self.assertIn('const inputMode = "직접 타이핑"', script)
        self.assertIn("const typeNativeValue = async", script)
        self.assertIn("await typeNativeValue(target, title, '제목', 28)", script)
        self.assertIn("await typeNativeValue(tistoryHtmlEditor, composedHtml, '본문', 12)", script)
        self.assertIn("if (directTyping) return true", script)

    def test_tistory_adsense_script_is_inserted_in_article_middle_once(self) -> None:
        article = "<p>첫 문단</p><p>둘째 문단</p><p>셋째 문단</p><p>마지막 문단</p>"

        inserted = main.insert_tistory_adsense_script(article)
        inserted_again = main.insert_tistory_adsense_script(inserted)

        self.assertIn(main.TISTORY_ADSENSE_MIDDLE_MARKER, inserted)
        self.assertIn("ca-pub-7920445775975888", inserted)
        self.assertLess(inserted.index("ca-pub-7920445775975888"), inserted.index("셋째 문단"))
        self.assertEqual(inserted_again.count(main.TISTORY_ADSENSE_MIDDLE_MARKER), 1)
        self.assertEqual(inserted_again.count("ca-pub-7920445775975888"), 1)

    def test_tistory_ads_are_inserted_above_distinct_random_images(self) -> None:
        article = "".join(
            f'<figure id="image-{index}"><img src="image-{index}.png"></figure>'
            for index in range(1, 4)
        )

        inserted, inserted_count = main.insert_tistory_ads_near_images(
            article,
            "<script>custom-ad</script>",
            main.TISTORY_AD_POSITION_ABOVE,
            2,
            randomizer=main.random.Random(7),
        )

        self.assertEqual(inserted_count, 2)
        self.assertEqual(inserted.count(main.TISTORY_ADSENSE_IMAGE_BLOCK_START), 2)
        self.assertEqual(inserted.count("<script>custom-ad</script>"), 2)
        self.assertEqual(
            len(
                main.re.findall(
                    rf"{main.TISTORY_ADSENSE_IMAGE_BLOCK_END}\s*-->\s*<figure",
                    inserted,
                    flags=main.re.S,
                )
            ),
            2,
        )

    def test_tistory_loader_only_code_builds_visible_ad_unit_with_slot(self) -> None:
        article = '<figure><img src="one.png"></figure>'

        inserted, inserted_count = main.insert_tistory_ads_near_images(
            article,
            main.DEFAULT_TISTORY_AD_CODE,
            main.TISTORY_AD_POSITION_ABOVE,
            1,
            randomizer=main.random.Random(1),
            ad_slot_id="5295351254",
        )

        self.assertEqual(inserted_count, 1)
        self.assertIn('class="adsbygoogle"', inserted)
        self.assertIn('data-ad-client="ca-pub-7920445775975888"', inserted)
        self.assertIn('data-ad-slot="5295351254"', inserted)
        self.assertIn("(adsbygoogle = window.adsbygoogle || []).push({});", inserted)

    def test_tistory_complete_ad_unit_is_not_duplicated(self) -> None:
        article = '<figure><img src="one.png"></figure>'
        complete_code = (
            main.DEFAULT_TISTORY_AD_CODE
            + '\n<ins class="adsbygoogle" data-ad-client="ca-pub-1" data-ad-slot="123"></ins>'
            + '\n<script>(adsbygoogle = window.adsbygoogle || []).push({});</script>'
        )

        inserted, _ = main.insert_tistory_ads_near_images(
            article,
            complete_code,
            main.TISTORY_AD_POSITION_ABOVE,
            1,
            ad_slot_id="999",
        )

        self.assertEqual(inserted.count('class="adsbygoogle"'), 1)
        self.assertNotIn('data-ad-slot="999"', inserted)

    def test_tistory_ads_are_inserted_below_images_and_clamped_to_image_count(self) -> None:
        article = (
            '<figure id="one"><img src="one.png"></figure>'
            '<figure id="two"><img src="two.png"></figure>'
        )

        inserted, inserted_count = main.insert_tistory_ads_near_images(
            article,
            "<script>custom-ad</script>",
            main.TISTORY_AD_POSITION_BELOW,
            8,
            randomizer=main.random.Random(3),
        )

        self.assertEqual(inserted_count, 2)
        self.assertEqual(inserted.count(main.TISTORY_ADSENSE_IMAGE_BLOCK_START), 2)
        self.assertEqual(
            len(
                main.re.findall(
                    rf"</figure>\s*<!--\s*{main.TISTORY_ADSENSE_IMAGE_BLOCK_START}",
                    inserted,
                    flags=main.re.S,
                )
            ),
            2,
        )

    def test_tistory_image_ad_insertion_is_idempotent_and_skips_when_no_image(self) -> None:
        article = '<figure><img src="one.png"></figure>'
        inserted, first_count = main.insert_tistory_ads_near_images(
            article,
            "<script>custom-ad</script>",
            main.TISTORY_AD_POSITION_ABOVE,
            1,
            randomizer=main.random.Random(1),
        )
        inserted_again, second_count = main.insert_tistory_ads_near_images(
            inserted,
            "<script>custom-ad</script>",
            main.TISTORY_AD_POSITION_BELOW,
            1,
            randomizer=main.random.Random(2),
        )
        no_image_html, no_image_count = main.insert_tistory_ads_near_images(
            "<p>이미지 없는 본문</p>",
            "<script>custom-ad</script>",
            main.TISTORY_AD_POSITION_ABOVE,
            2,
        )

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 1)
        self.assertEqual(inserted_again.count(main.TISTORY_ADSENSE_IMAGE_BLOCK_START), 1)
        self.assertEqual(inserted_again.count("<script>custom-ad</script>"), 1)
        self.assertEqual(no_image_count, 0)
        self.assertEqual(no_image_html, "<p>이미지 없는 본문</p>")

    def test_web_image_search_removes_license_filter_only_when_protection_is_off(self) -> None:
        collector = main.GoogleImageCollageCollector()

        protected_url = collector._google_image_search_url("일본 지진", licensed_only=True)
        unrestricted_url = collector._google_image_search_url("일본 지진", licensed_only=False)

        self.assertIn("tbs=il%3Acl", protected_url)
        self.assertNotIn("tbs=il%3Acl", unrestricted_url)
        self.assertIn("tbm=isch", unrestricted_url)

    def test_naver_news_image_parser_keeps_article_image_and_source_page(self) -> None:
        collector = main.GoogleImageCollageCollector()
        html = """
        <a href="https://news.example.com/article/123" target="_blank" data-heatmap-target=".img">
          <div>
            <img width="104"
                 alt="일본 강진으로 쇼핑몰 &lt;mark&gt;붕괴&lt;/mark&gt;의 이미지"
                 src="https://search.pstatic.net/common/?src=https%3A%2F%2Fimgnews.pstatic.net%2Fimage%2Forigin%2F001%2F2026%2F07%2F28%2Fexample.jpg&amp;type=fface200_200"/>
          </div>
        </a>
        <img width="24" alt="언론사 프로필 이미지"
             src="https://search.pstatic.net/common/?src=https%3A%2F%2Fmimgnews.pstatic.net%2Flogo.png"/>
        """

        candidates = collector._extract_naver_news_image_candidates(html)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_url"], "https://news.example.com/article/123")
        self.assertEqual(
            candidates[0]["image_url"],
            "https://imgnews.pstatic.net/image/origin/001/2026/07/28/example.jpg",
        )
        self.assertEqual(candidates[0]["title"], "일본 강진으로 쇼핑몰 붕괴")

    def test_web_image_candidates_prefer_specific_event_over_news_roundup(self) -> None:
        collector = main.GoogleImageCollageCollector()
        candidates = [
            {"title": "오늘의 뉴스 종합 일본 강진 관련 주요 소식", "source_url": "roundup"},
            {"title": "건물 회복력과 도시 안전 세미나", "source_url": "generic"},
            {
                "title": "일본 혼슈 규모 7.2 지진 현장",
                "source_url": "https://news.kbs.co.kr/news/pc/view/view.do?ncd=1",
                "label": "specific",
            },
            {"title": "구마모토 지진으로 공장 굴뚝 붕괴", "source_url": "alias"},
        ]

        ranked = collector._rank_web_image_candidates(
            "일본 지진 건물 붕괴 뉴스 사진",
            candidates,
        )

        self.assertEqual(
            [item.get("label") or item["source_url"] for item in ranked[:2]],
            ["specific", "alias"],
        )

    def test_web_search_queries_retry_with_shorter_topic_phrases(self) -> None:
        collector = main.GoogleImageCollageCollector()

        queries = collector._web_search_queries(
            "일본 구마모토 강진 대형 쇼핑몰 건물 붕괴 피해 최신 뉴스 사진"
        )

        self.assertGreaterEqual(len(queries), 3)
        self.assertEqual(queries[0], "일본 구마모토 강진 대형 쇼핑몰 건물 붕괴 피해")
        self.assertTrue(any(len(query.split()) <= 5 for query in queries[1:]))
        self.assertTrue(any("강진" in query or "붕괴" in query for query in queries))

    def test_reference_image_protection_mode_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                tistory_reference_image_protection_mode=False,
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

            self.assertFalse(loaded.tistory_reference_image_protection_mode)

    def test_v1_1_23_migration_turns_existing_protection_mode_off_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            state_file.write_text(
                json.dumps(
                    {"tistory_reference_image_protection_mode": True},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                loaded = main.AppStateStore.load()

            migrated_payload = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertFalse(loaded.tistory_reference_image_protection_mode)
            self.assertFalse(migrated_payload["tistory_reference_image_protection_mode"])
            self.assertIn(
                main.TISTORY_PROTECTION_OFF_MIGRATION,
                migrated_payload["applied_settings_migrations"],
            )

    def test_v1_1_23_migration_does_not_override_later_user_choice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            state_file.write_text(
                json.dumps(
                    {
                        "tistory_reference_image_protection_mode": True,
                        "applied_settings_migrations": [
                            main.TISTORY_PROTECTION_OFF_MIGRATION
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                loaded = main.AppStateStore.load()

            self.assertTrue(loaded.tistory_reference_image_protection_mode)

    def test_tistory_input_mode_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                tistory_input_mode=main.TEXT_INPUT_MODE_TYPING,
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

            self.assertEqual(loaded.tistory_input_mode, main.TEXT_INPUT_MODE_TYPING)

    def test_tistory_save_mode_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                tistory_save_mode=main.TISTORY_SAVE_MODE_DRAFT,
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

            self.assertEqual(
                loaded.tistory_save_mode,
                main.TISTORY_SAVE_MODE_DRAFT,
            )

    def test_tistory_ad_settings_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "app_state.json"
            settings = main.WordPressSettings(
                tistory_ads_enabled=True,
                tistory_ads_code="<script>saved-ad-code</script>",
                tistory_ads_slot_id="9876543210",
                tistory_ads_position=main.TISTORY_AD_POSITION_BELOW,
                tistory_ads_count=4,
            )
            with (
                patch.object(main, "STATE_FILE", state_file),
                patch.object(main.PromptFileStore, "load_into", side_effect=lambda value: value),
                patch.object(main.KeychainStore, "load_secret", return_value=""),
            ):
                main.AppStateStore.save(settings, save_secrets=False)
                loaded = main.AppStateStore.load()

            self.assertTrue(loaded.tistory_ads_enabled)
            self.assertEqual(loaded.tistory_ads_code, "<script>saved-ad-code</script>")
            self.assertEqual(loaded.tistory_ads_slot_id, "9876543210")
            self.assertEqual(loaded.tistory_ads_position, main.TISTORY_AD_POSITION_BELOW)
            self.assertEqual(loaded.tistory_ads_count, 4)

    def test_reference_image_is_inserted_in_article_middle(self) -> None:
        article = "".join(f"<p>본문 {index}</p>" for index in range(1, 7))
        marker = "<figure class='reference'>참고 이미지</figure>"

        prepared = main.insert_reference_images_in_article_middle(article, [marker])

        self.assertGreater(prepared.index(marker), prepared.index("<p>본문 2</p>"))
        self.assertLess(prepared.index(marker), prepared.index("<p>본문 6</p>"))

    def test_reference_image_does_not_nest_inside_existing_cardnews(self) -> None:
        cardnews = (
            "<figure class='blog-helper-cardnews-image'>"
            "<p>카드뉴스 이미지 자리</p><figcaption>카드뉴스</figcaption>"
            "</figure>"
        )
        article = f"<p>도입부</p>{cardnews}<p>핵심 내용</p><p>마무리</p>"
        marker = "<figure class='reference'>참고 이미지</figure>"

        prepared = main.insert_reference_images_in_article_middle(article, [marker])
        cardnews_start = prepared.index("<figure class='blog-helper-cardnews-image'>")
        cardnews_end = prepared.index("</figure>", cardnews_start)
        marker_index = prepared.index(marker)

        self.assertFalse(cardnews_start < marker_index < cardnews_end)

    def test_reference_placeholder_becomes_native_upload_with_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "reference-capture-test.png"
            image_path.write_bytes(b"reference-image")
            image = {
                "path": str(image_path),
                "source_url": "https://commons.wikimedia.org/wiki/File:Earthquake.jpg",
                "source": "Wikimedia Commons",
                "license": "CC BY-SA 4.0",
                "creator": "Example Creator",
            }
            source = (
                "<p>도입부</p>"
                + main.build_tistory_reference_image_figure(image, "일본 지진", 1)
                + "<p>마무리</p>"
            )

            prepared, native_files = main.prepare_tistory_native_attachment_html(source, "일본 지진")

            self.assertEqual(list(native_files.values()), [str(image_path)])
            self.assertIn("__BLOG_HELPER_TISTORY_NATIVE_IMAGE_1__", prepared)
            self.assertIn("blog-helper-reference-image", prepared)
            self.assertIn("Wikimedia Commons", prepared)
            self.assertIn("CC BY-SA 4.0", prepared)
            self.assertIn("Example Creator", prepared)
            self.assertIn("target='_blank'", prepared)
            self.assertNotIn(str(image_path), prepared)
            self.assertNotIn("data:image", prepared)

    def test_reference_placeholder_omits_attribution_when_protection_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "reference-capture-unprotected.png"
            image_path.write_bytes(b"reference-image")
            image = {
                "path": str(image_path),
                "source_url": "https://news.example.com/article/123",
                "source": "뉴스 이미지",
                "license": "저작권 보호 모드 OFF",
                "creator": "Example Creator",
            }
            source = main.build_tistory_reference_image_figure(
                image,
                "일본 지진",
                1,
                show_source_attribution=False,
            )

            prepared, native_files = main.prepare_tistory_native_attachment_html(source, "일본 지진")

            self.assertEqual(list(native_files.values()), [str(image_path)])
            self.assertIn("__BLOG_HELPER_TISTORY_NATIVE_IMAGE_1__", prepared)
            self.assertNotIn("<figcaption>", prepared)
            self.assertNotIn("출처:", prepared)
            self.assertNotIn("뉴스 이미지", prepared)
            self.assertNotIn("저작권 보호 모드 OFF", prepared)
            self.assertNotIn("Example Creator", prepared)
            self.assertNotIn("news.example.com", prepared)

    def test_unprotected_reference_image_crops_configured_footer_from_bottom(self) -> None:
        if main.Image is None:
            self.skipTest("Pillow is not available")
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "reference-crop.png"
            image = main.Image.new("RGB", (640, 480), "white")
            image.save(image_path, format="PNG")

            self.assertTrue(
                main._crop_reference_image_bottom(
                    image_path,
                    main.TISTORY_UNPROTECTED_IMAGE_BOTTOM_CROP_PX,
                )
            )
            with main.Image.open(image_path) as cropped:
                self.assertEqual(cropped.size, (640, 420))

    def test_reference_collector_saves_only_captured_image_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)

            def fake_capture(
                _data_url: str,
                destination: Path,
                crop_bottom_px: int = 0,
            ) -> bool:
                self.assertEqual(crop_bottom_px, 0)
                destination.write_bytes(b"captured-image-region")
                return True

            with (
                patch.object(main, "GENERATED_UPLOAD_DIR", output_dir),
                patch.object(
                    main.GoogleImageCollageCollector,
                    "collect_licensed",
                    return_value=[
                        {
                            "data_url": "data:image/png;base64,AA==",
                            "source_url": "https://openverse.org/image/example",
                            "source": "Openverse",
                            "license": "CC0",
                            "creator": "Creator",
                        }
                    ],
                ),
                patch.object(main, "capture_reference_image_region", side_effect=fake_capture),
            ):
                captures = main.collect_tistory_reference_image_files(
                    "일본 지진 건물 붕괴",
                    ["일본 지진"],
                    1,
                )

            self.assertEqual(len(captures), 1)
            capture_path = Path(captures[0]["path"])
            self.assertTrue(capture_path.exists())
            self.assertTrue(capture_path.name.startswith(main.TISTORY_REFERENCE_IMAGE_PREFIX))
            self.assertNotIn("data_url", captures[0])
            self.assertEqual(captures[0]["license"], "CC0")

    def test_reference_collector_uses_general_web_search_when_protection_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)

            def fake_capture(
                _data_url: str,
                destination: Path,
                crop_bottom_px: int = 0,
            ) -> bool:
                self.assertEqual(
                    crop_bottom_px,
                    main.TISTORY_UNPROTECTED_IMAGE_BOTTOM_CROP_PX,
                )
                destination.write_bytes(b"captured-web-image")
                return True

            with (
                patch.object(main, "GENERATED_UPLOAD_DIR", output_dir),
                patch.object(
                    main.GoogleImageCollageCollector,
                    "collect_web",
                    return_value=[
                        {
                            "data_url": "data:image/png;base64,AA==",
                            "source_url": "https://www.google.com/search?tbm=isch&q=test",
                            "source": "일반 웹 이미지 검색",
                            "license": "저작권 보호 모드 OFF",
                        }
                    ],
                ) as collect_web,
                patch.object(main.GoogleImageCollageCollector, "collect_licensed") as collect_licensed,
                patch.object(main, "capture_reference_image_region", side_effect=fake_capture),
            ):
                captures = main.collect_tistory_reference_image_files(
                    "일본 지진 건물 붕괴",
                    ["일본 지진"],
                    2,
                    protection_mode=False,
                )

            collect_web.assert_called_once()
            self.assertIn("뉴스 사진", collect_web.call_args.args[0])
            self.assertGreaterEqual(collect_web.call_args.args[1], 10)
            collect_licensed.assert_not_called()
            self.assertEqual(len(captures), 1)
            self.assertEqual(captures[0]["license"], "저작권 보호 모드 OFF")

    def test_unprotected_reference_capture_retries_after_first_candidate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            attempted_data_urls: list[str] = []

            def fake_capture(
                data_url: str,
                destination: Path,
                crop_bottom_px: int = 0,
            ) -> bool:
                self.assertEqual(
                    crop_bottom_px,
                    main.TISTORY_UNPROTECTED_IMAGE_BOTTOM_CROP_PX,
                )
                attempted_data_urls.append(data_url)
                if data_url.endswith("FIRST"):
                    return False
                destination.write_bytes(b"captured-second-web-image")
                return True

            candidates = [
                {
                    "data_url": "data:image/png;base64,FIRST",
                    "image_url": "https://images.example.com/first.png",
                    "source_url": "https://news.example.com/first",
                    "source": "뉴스 이미지",
                    "license": "저작권 보호 모드 OFF",
                },
                {
                    "data_url": "data:image/png;base64,SECOND",
                    "image_url": "https://images.example.com/second.png",
                    "source_url": "https://news.example.com/second",
                    "source": "뉴스 이미지",
                    "license": "저작권 보호 모드 OFF",
                },
            ]
            with (
                patch.object(main, "GENERATED_UPLOAD_DIR", output_dir),
                patch.object(
                    main.GoogleImageCollageCollector,
                    "collect_web",
                    return_value=candidates,
                ),
                patch.object(main, "capture_reference_image_region", side_effect=fake_capture),
            ):
                captures = main.collect_tistory_reference_image_files(
                    "일본 지진 건물 붕괴",
                    ["일본 지진"],
                    2,
                    protection_mode=False,
                )

            self.assertEqual(len(captures), 1)
            self.assertEqual(
                attempted_data_urls,
                ["data:image/png;base64,FIRST", "data:image/png;base64,SECOND"],
            )
            self.assertEqual(
                captures[0]["source_url"],
                "https://news.example.com/second",
            )

    def test_unprotected_reference_capture_uses_browser_when_downloaded_candidates_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            downloaded_candidate = {
                "data_url": "data:image/png;base64,BROKEN",
                "image_url": "https://images.example.com/broken.png",
                "source_url": "https://news.example.com/broken",
                "source": "뉴스 이미지",
                "license": "저작권 보호 모드 OFF",
            }
            browser_candidate = {
                "data_url": "data:image/png;base64,BROWSER",
                "image_url": "https://images.example.com/browser.png",
                "source_url": "https://search.example.com/images",
                "source": "Chrome 이미지 검색 캡처",
                "license": "저작권 보호 모드 OFF",
            }

            def fake_capture(
                data_url: str,
                destination: Path,
                crop_bottom_px: int = 0,
            ) -> bool:
                self.assertEqual(
                    crop_bottom_px,
                    main.TISTORY_UNPROTECTED_IMAGE_BOTTOM_CROP_PX,
                )
                if not data_url.endswith("BROWSER"):
                    return False
                destination.write_bytes(b"captured-browser-image")
                return True

            with (
                patch.object(main, "GENERATED_UPLOAD_DIR", output_dir),
                patch.object(
                    main.GoogleImageCollageCollector,
                    "collect_web",
                    return_value=[downloaded_candidate],
                ),
                patch.object(
                    main.GoogleImageCollageCollector,
                    "_collect_browser_image_elements",
                    return_value=[browser_candidate],
                ) as browser_search,
                patch.object(main, "capture_reference_image_region", side_effect=fake_capture),
            ):
                captures = main.collect_tistory_reference_image_files(
                    "일본 지진 건물 붕괴",
                    ["일본 지진"],
                    2,
                    protection_mode=False,
                )

            browser_search.assert_called_once()
            self.assertEqual(len(captures), 1)
            self.assertEqual(captures[0]["source"], "Chrome 이미지 검색 캡처")

    def test_tistory_worker_uploads_reference_capture_as_native_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "reference-capture-worker.png"
            image_path.write_bytes(b"reference-image")
            reference_image = {
                "path": str(image_path),
                "source_url": "https://commons.wikimedia.org/wiki/File:Earthquake.jpg",
                "source": "Wikimedia Commons",
                "license": "CC BY-SA 4.0",
                "creator": "Example Creator",
            }
            events: queue.Queue = queue.Queue()

            with (
                patch.object(
                    main,
                    "collect_tistory_reference_image_files",
                    return_value=[reference_image],
                ) as collect_reference_images,
                patch.object(
                    main,
                    "run_tistory_playwright_automation",
                    return_value=(True, "완료"),
                ) as run_automation,
                patch.object(main, "cleanup_generated_upload_images", return_value=1),
                patch.object(main, "cleanup_tistory_automation_files"),
            ):
                worker = main.TistoryAutomationWorker(
                    "일본 지진 피해 정리",
                    "<p>도입부</p><p>핵심 내용</p><p>마무리</p>",
                    events,
                    tag_names=["일본 지진", "건물 붕괴"],
                    publish_after_input=True,
                    write_url="https://example.tistory.com/manage/newpost",
                    public_blog_url="https://info.example.com/",
                    reference_image_protection_mode=False,
                    input_mode=main.TEXT_INPUT_MODE_TYPING,
                )
                worker.run()

            self.assertFalse(collect_reference_images.call_args.kwargs["protection_mode"])
            call = run_automation.call_args
            script = call.args[1]
            native_files = call.kwargs["native_image_files"]
            self.assertEqual(call.kwargs["expected_title"], "일본 지진 피해 정리")
            self.assertEqual(call.kwargs["public_blog_url"], "https://info.example.com/")
            self.assertIn(str(image_path), native_files.values())
            self.assertNotIn("Wikimedia Commons", script)
            self.assertNotIn("CC BY-SA 4.0", script)
            self.assertNotIn("출처:", script)
            self.assertIn("const collageImages = []", script)
            self.assertIn('const inputMode = "직접 타이핑"', script)
            self.assertIn(main.TISTORY_ADSENSE_MIDDLE_MARKER, script)
            self.assertIn("ca-pub-7920445775975888", script)
            event_types = [events.get_nowait()[0] for _ in range(events.qsize())]
            self.assertIn("tistory_automation_done", event_types)

    def test_thumbnail_body_uses_uploaded_content_url(self) -> None:
        script = main.build_tistory_editor_automation_script(
            "테스트 제목",
            "<p>본문</p>",
            thumbnail_data_url="data:image/png;base64,AA==",
            thumbnail_content_url="__BLOG_HELPER_TISTORY_NATIVE_THUMBNAIL__",
        )

        self.assertIn('const thumbnailContentUrl = "__BLOG_HELPER_TISTORY_NATIVE_THUMBNAIL__"', script)
        self.assertIn("const thumbnailHtml = thumbnailContentUrl", script)
        self.assertNotIn("const thumbnailHtml = thumbnailDataUrl", script)

    def test_publish_script_leaves_representative_image_to_playwright_file_chooser(self) -> None:
        script = main.build_tistory_editor_automation_script(
            "테스트 제목",
            "<p>본문</p>",
            thumbnail_data_url="data:image/png;base64,AA==",
            thumbnail_content_url="https://blog.kakaocdn.net/current-thumbnail.png",
            automation_actions=[
                "set_title",
                "set_body",
                "click_complete",
                "attach_representative_image",
                "click_public_publish",
            ],
            publish_after_input=True,
        )

        actions_start = script.index("const automationActions = ")
        actions_end = script.index(";", actions_start)
        actions_line = script[actions_start:actions_end]
        self.assertIn("click_complete", actions_line)
        self.assertNotIn("attach_representative_image", actions_line)
        self.assertNotIn("click_public_publish", actions_line)

    def test_draft_script_never_opens_publish_panel(self) -> None:
        script = main.build_tistory_editor_automation_script(
            "임시저장 제목",
            "<p>본문</p>",
            automation_actions=[
                "set_title",
                "set_body",
                "set_tags",
                "click_complete",
                "attach_representative_image",
                "set_publish_now",
                "click_public_publish",
            ],
            publish_after_input=False,
            save_mode=main.TISTORY_SAVE_MODE_DRAFT,
        )

        actions_start = script.index("const automationActions = ")
        actions_end = script.index(";", actions_start)
        actions_line = script[actions_start:actions_end]
        self.assertIn("set_tags", actions_line)
        self.assertNotIn("click_complete", actions_line)
        self.assertNotIn("attach_representative_image", actions_line)
        self.assertNotIn("set_publish_now", actions_line)
        self.assertNotIn("click_public_publish", actions_line)

    def test_draft_save_click_uses_bottom_button_and_waits_for_confirmation(self) -> None:
        class FakeMouse:
            def __init__(self) -> None:
                self.clicks = 0

            def move(self, _x: float, _y: float) -> None:
                pass

            def down(self) -> None:
                pass

            def up(self) -> None:
                self.clicks += 1

        class FakePage:
            def __init__(self) -> None:
                self.mouse = FakeMouse()
                self.confirmation_checks = 0

            def evaluate(self, script: str) -> dict | bool:
                if "작성 중인 글이 저장되었습니다." in script:
                    self.confirmation_checks += 1
                    return self.confirmation_checks >= 2
                return {
                    "text": "임시저장 2",
                    "x": 800,
                    "y": 780,
                    "left": 740,
                    "top": 760,
                    "width": 120,
                    "height": 40,
                }

            def wait_for_timeout(self, _milliseconds: int) -> None:
                pass

        page = FakePage()
        events: queue.Queue = queue.Queue()
        self.assertTrue(main.click_tistory_draft_save_native(page, events))
        self.assertEqual(page.mouse.clicks, 1)
        self.assertGreaterEqual(page.confirmation_checks, 2)
        messages = [events.get_nowait()[1] for _ in range(events.qsize())]
        self.assertTrue(any("작성 중인 글이 저장되었습니다." in message for message in messages))

    def test_draft_save_is_not_successful_without_confirmation_message(self) -> None:
        class FakeMouse:
            def move(self, _x: float, _y: float) -> None:
                pass

            def down(self) -> None:
                pass

            def up(self) -> None:
                pass

        class FakePage:
            mouse = FakeMouse()

            def evaluate(self, script: str) -> dict | bool:
                if "작성 중인 글이 저장되었습니다." in script:
                    return False
                return {
                    "text": "임시저장 1",
                    "x": 800,
                    "y": 780,
                    "left": 740,
                    "top": 760,
                    "width": 120,
                    "height": 40,
                }

            def wait_for_timeout(self, _milliseconds: int) -> None:
                pass

        with patch.object(main.time, "time", side_effect=[0, 0, 13]):
            self.assertFalse(main.click_tistory_draft_save_native(FakePage()))

    def test_captcha_is_detected_inside_cross_origin_frame(self) -> None:
        class FakeFrame:
            def __init__(self, url: str, detected: bool = False) -> None:
                self.url = url
                self.detected = detected

            def evaluate(self, _script: str) -> bool:
                return self.detected

        main_frame = FakeFrame("https://soullhka.tistory.com/manage/newpost")
        captcha_frame = FakeFrame("https://captcha.kakao.com/dkaptcha/challenge")

        class FakePage:
            def __init__(self) -> None:
                self.main_frame = main_frame
                self.frames = [main_frame, captcha_frame]

        self.assertTrue(main.is_tistory_captcha_visible(FakePage()))

    def test_tistory_editor_page_is_kept_open_for_manual_publish(self) -> None:
        class FakePage:
            url = "https://soullhka.tistory.com/manage/newpost/?type=post"

        self.assertTrue(main.is_tistory_editor_page_open(FakePage()))

    def test_public_publish_is_clicked_once_without_followup_confirm_click(self) -> None:
        class FakeMouse:
            def __init__(self) -> None:
                self.down_count = 0
                self.up_count = 0
                self.click_count = 0

            def move(self, _x: float, _y: float) -> None:
                pass

            def down(self) -> None:
                self.down_count += 1

            def up(self) -> None:
                self.up_count += 1

            def click(self, _x: float, _y: float) -> None:
                self.click_count += 1

        class FakePage:
            def __init__(self) -> None:
                self.mouse = FakeMouse()

            def wait_for_timeout(self, _milliseconds: int) -> None:
                pass

        page = FakePage()
        rect = {
            "text": "공개 발행",
            "x": 900,
            "y": 800,
            "left": 820,
            "top": 780,
            "width": 160,
            "height": 40,
        }
        with (
            patch.object(main, "click_tistory_publish_now_native", return_value=True),
            patch.object(main, "find_tistory_public_publish_button_rect", return_value=rect),
        ):
            self.assertTrue(main.click_tistory_public_publish_native(page))

        self.assertEqual(page.mouse.down_count, 1)
        self.assertEqual(page.mouse.up_count, 1)
        self.assertEqual(page.mouse.click_count, 0)


if __name__ == "__main__":
    unittest.main()
