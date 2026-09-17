import inspect
import queue
import unittest

import main


class NaverCreatorAdvisorKeywordTests(unittest.TestCase):
    def test_extracts_ranked_titles_and_urls_in_source_order(self) -> None:
        payload = {
            "data": {
                "mainInflowTrend": {
                    "contents": [
                        {
                            "contentTitle": "첫 번째 인기 글",
                            "contentUrl": "https://blog.naver.com/bluecrea/100",
                        },
                        {
                            "postTitle": "두 번째 인기 글",
                            "blogId": "bluecrea",
                            "logNo": "200",
                        },
                    ]
                }
            }
        }

        items = main.extract_creator_advisor_items_from_payload(payload)

        self.assertEqual(
            items,
            [
                {
                    "title": "첫 번째 인기 글",
                    "url": "https://blog.naver.com/bluecrea/100",
                },
                {
                    "title": "두 번째 인기 글",
                    "url": "https://blog.naver.com/bluecrea/200",
                },
            ],
        )

    def test_builds_top_ten_keyword_choices_with_reference_urls(self) -> None:
        items = [
            {
                "title": f"네이버 유입 콘텐츠 {index}",
                "url": f"https://blog.naver.com/bluecrea/{index}",
            }
            for index in range(1, 13)
        ]
        worker = main.NaverCreatorAdvisorKeywordWorker(queue.Queue())

        insights, reference_map = worker._build_payload(items)

        self.assertEqual(len(insights), 10)
        self.assertEqual(insights[0].keyword, "네이버 유입 콘텐츠 1")
        self.assertIn("1위", insights[0].reasons[0])
        self.assertIn(
            "https://blog.naver.com/bluecrea/1",
            reference_map["네이버 유입 콘텐츠 1"],
        )

    def test_writing_keyword_ui_uses_short_labels_and_search_icon(self) -> None:
        source = inspect.getsource(main.KeywordApp._build_writing_workflow)

        self.assertIn('text="다음"', source)
        self.assertIn('text="시그널"', source)
        self.assertIn('text="뉴닉"', source)
        self.assertIn('text="네이버"', source)
        self.assertIn('fg_color="#03C75A"', source)
        self.assertIn('text="검색"', source)
        self.assertIn('_bootstrap_sidebar_icon_image("search"', source)
        self.assertIn("command=self.load_naver_creator_keywords", source)

    def test_creator_advisor_uses_persistent_profile_and_login_flow(self) -> None:
        source = inspect.getsource(main.NaverCreatorAdvisorKeywordWorker)

        self.assertIn("launch_persistent_context", source)
        self.assertIn("NAVER_CREATOR_ADVISOR_CHROME_PROFILE_DIR", source)
        self.assertIn("NAVER_CREATOR_ADVISOR_URL", source)
        self.assertIn("네이버 메인에서 유입된 콘텐츠", source)
        self.assertIn("NID_AUT", source)


if __name__ == "__main__":
    unittest.main()
