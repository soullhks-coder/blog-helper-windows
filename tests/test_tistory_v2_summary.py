import copy
import json
import queue
import unittest
from unittest.mock import patch

import main
from tistory_v2_summary import (ARTICLE_OUTPUT_PROMPT, article_response, build_summary_slides,
                               request_summaries, validate_summaries)


BODY = ''.join(f'<h2>소제목 {i}</h2><p>원본의 상세한 설명 {i}입니다. 길게 작성한 글은 그대로 보존합니다.</p>' for i in range(1, 11))


def summaries():
    return [{'source_sections': [i], 'eyebrow': '핵심 요약', 'title': f'간결한 제목 {i}',
             'summary': '핵심을 짧게 정리합니다.', 'bullets': ['주요 사항 확인', '조건과 주의점 확인']} for i in range(1, 11)]


class V2SummaryTests(unittest.TestCase):
    def test_failed_slide_summary_keeps_completed_article(self):
        response = json.dumps({'article_html': BODY, 'slides': summaries()[:9]})
        bad_summary = json.dumps({'slides': summaries()[:9]})
        events = queue.Queue()
        with patch.object(main, 'generate_text_with_writing_model', side_effect=[
            ('제목', '테스트 모델'), (response, '테스트 모델'), (bad_summary, '테스트 모델'), (bad_summary, '테스트 모델')]), \
             patch.object(main, 'append_runtime_log'):
            main.ArticleGenerationWorker(main.WordPressSettings(), '주제', '키워드', '', events, tistory_v2_slides=True).run()
        done = [payload for kind, payload in events.queue if kind == 'article_done'][0]
        self.assertEqual(done['html'], BODY)
        self.assertIsNone(done['slide_summaries'])
        self.assertTrue(done['slide_summary_warning'])
        self.assertFalse(any(kind == 'article_error' for kind, _ in events.queue))

    def test_article_and_summaries_are_separated_without_changing_body(self):
        raw = json.dumps({'article_html': BODY, 'slides': summaries()}, ensure_ascii=False)
        body, rows = article_response('```json\n' + raw + '\n```')
        self.assertEqual(body, BODY)
        slides = build_summary_slides('테스트 제목', body, rows)
        self.assertEqual(len(slides), 10)
        self.assertIn('상세한 설명', slides[0]['source_text'])
        self.assertNotIn('상세한 설명', slides[0]['summary'])
        self.assertEqual(slides[0]['source_sections'][0]['heading'], '소제목 1')
        self.assertTrue(all(s['image'] is None for s in slides))

    def test_invalid_or_verbose_plans_are_not_silently_copied_or_truncated(self):
        invalid = [summaries()[:9]]
        for field, value in [('title', '너무 긴 원본 문장' * 10), ('summary', '<p>본문</p>'),
                             ('eyebrow', '1/1'), ('summary', '[표지 이미지: 사진]'),
                             ('source_sections', [11]), ('bullets', ['너무 길게 복사한 원본 문장' * 5])]:
            rows = summaries()
            rows[0][field] = value
            invalid.append(rows)
        rows = summaries()
        rows[-1]['source_sections'] = [9]
        invalid.append(rows)
        for rows in invalid:
            with self.subTest(rows=rows[0] if rows else rows), self.assertRaises(ValueError):
                validate_summaries(rows, '제목', BODY)

    def test_legacy_html_is_preserved_but_unparsed_json_never_leaks(self):
        self.assertEqual(article_response(BODY), (BODY, None))
        with self.assertRaises(ValueError):
            article_response('{"article_html":broken')
        with self.assertRaises(ValueError):
            article_response(json.dumps({'article_html': '', 'slides': summaries()}))

    def test_summary_repair_is_bounded_and_does_not_rewrite_article(self):
        prompts = []
        def generate(prompt):
            prompts.append(prompt)
            return json.dumps({'slides': summaries()[:9] if len(prompts) == 1 else summaries()})
        rows = request_summaries('제목', BODY, generate)
        self.assertEqual(len(rows), 10)
        self.assertEqual(len(prompts), 2)
        self.assertIn(BODY, prompts[0])
        self.assertIn('본문을 다시 작성하지', prompts[0])
        self.assertIn('직전 응답 수정 요청', prompts[1])

    def test_v2_generation_uses_same_body_call_and_keeps_normal_prompts_unchanged(self):
        settings = main.WordPressSettings(title_prompt_template='내 제목 지침', article_prompt_template='내 상세 본문 지침')
        original = copy.deepcopy(settings)
        events = queue.Queue()
        response = json.dumps({'article_html': BODY, 'slides': summaries()}, ensure_ascii=False)
        with patch.object(main, 'generate_text_with_writing_model', side_effect=[('제목', '테스트 모델'), (response, '테스트 모델')]) as generate:
            main.ArticleGenerationWorker(settings, '주제', '키워드', '', events, tistory_v2_slides=True).run()
        done = [payload for kind, payload in events.queue if kind == 'article_done'][0]
        self.assertEqual(done['html'], BODY)
        self.assertEqual(len(done['slide_summaries']), 10)
        self.assertEqual(generate.call_count, 2)
        self.assertIn(ARTICLE_OUTPUT_PROMPT, generate.call_args_list[1].args[1])
        self.assertEqual(settings, original)
        normal_events = queue.Queue()
        with patch.object(main, 'generate_text_with_writing_model', side_effect=[('제목', '테스트 모델'), (BODY, '테스트 모델')]) as generate:
            main.ArticleGenerationWorker(settings, '주제', '키워드', '', normal_events).run()
        normal = [payload for kind, payload in normal_events.queue if kind == 'article_done'][0]
        self.assertNotIn('slide_summaries', normal)
        self.assertNotIn(ARTICLE_OUTPUT_PROMPT, generate.call_args_list[1].args[1])


if __name__ == '__main__':
    unittest.main()
