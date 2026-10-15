"""Provider-independent V2 article/slide contracts; no model or file access."""
from __future__ import annotations

import copy
import json
import re

from tistory_v2 import article_sections, plain_text


SLIDE_SUMMARY_PROMPT = """[티스토리 V2 슬라이드 요약 지침]
블로그 본문은 선택한 글쓰기 프롬프트대로 상세하게 작성하고, 슬라이드 문구는 별도로 요약하세요.
슬라이드 이미지 10장에 들어갈 PPT 스타일의 짧고 명료한 문구를 만듭니다.
이미지 크기는 별도 설정하며, 기본 600×300에서도 읽기 좋도록 아래 길이 제한을 지킵니다.
- 본문 문장을 그대로 길게 복사하거나 단순 분할하지 말고, 핵심 의미를 압축해 새 문장으로 씁니다.
- 본문의 사실·수치·주의사항·조건은 유지합니다. 본문에 없는 사실이나 단정, 과장 표현을 추가하지 않습니다.
- 소제목 순서대로 핵심 내용을 10장에 분배합니다. 소제목이 적으면 다른 핵심 요점으로 나누고, 많으면 인접 소제목을 묶습니다.
- 각 장: eyebrow 16자 이내, title 20자 이내, summary 40자 이내, bullets 1~3개(각 22자 이내).
- summary와 bullets는 서로 같은 문장을 반복하지 않습니다. 문구에 HTML/마크다운을 넣지 않습니다.
- 페이지 번호(1/10, 1/1 등), '핵심 내용 1' 등의 순번, 표지 이미지 지시문, 사진 자리 표시, 제작 메모를 넣지 않습니다.
- source_sections는 각 장이 요약한 원문 소제목의 1부터 시작하는 번호 배열입니다. 모든 소제목을 순서대로 포함합니다.
- 슬라이드 문구와 JSON은 발행용 본문 HTML 안에 넣지 않습니다.
slides 형식: [{"source_sections":[1],"eyebrow":"핵심 정리","title":"짧은 제목","summary":"핵심 내용을 한 문장으로 요약","bullets":["짧은 핵심 요점"]}]
"""

ARTICLE_OUTPUT_PROMPT = SLIDE_SUMMARY_PROMPT + """
[최종 출력 형식 — 앞선 HTML만 반환 지시보다 이 형식을 우선합니다]
선택한 지침의 상세한 발행용 본문과 별도 슬라이드 요약을 같은 응답으로 반환하세요.
코드펜스나 설명 없이 JSON 객체 하나만 반환하세요:
{"article_html":"발행용 상세 HTML 본문", "slides":[위 형식의 슬라이드 요약 정확히 10개]}
article_html에는 슬라이드 JSON, 표지 이미지 지시문이나 제작 메모를 넣지 마세요.
"""


def json_object(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text).strip(), flags=re.I)
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("슬라이드 응답은 JSON 객체여야 합니다.")
    return result


def article_response(text: str) -> tuple[str, object]:
    """Never allow an unparsed article/slide envelope into the blog editor."""
    try:
        result = json_object(text)
    except (ValueError, TypeError):
        # Older models can ignore the JSON instruction. Preserve usable HTML,
        # then request only its summaries; never silently copy it onto slides.
        if str(text).lstrip().startswith(("<", "```html")):
            return text, None
        raise ValueError("V2 본문·슬라이드 응답 형식을 읽지 못했습니다. 글 작성을 다시 시도해 주세요.")
    body = result.get("article_html")
    if not isinstance(body, str) or not plain_text(body).strip():
        raise ValueError("V2 발행용 본문이 비어 있습니다.")
    return body, result.get("slides")


def validate_summaries(rows: object, title: str, body: str) -> list[dict]:
    if not isinstance(rows, list) or len(rows) != 10:
        raise ValueError("슬라이드 요약은 정확히 10장이어야 합니다.")
    sections = article_sections(body, title)
    clean, covered, previous = [], set(), 1
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("슬라이드 요약 항목 형식이 올바르지 않습니다.")
        indices = row.get("source_sections")
        if (not isinstance(indices, list) or not indices or
            any(type(i) is not int or not 1 <= i <= len(sections) for i in indices) or
            indices != sorted(set(indices)) or min(indices) < previous):
            raise ValueError("슬라이드와 원문 소제목의 연결·순서를 확인하지 못했습니다.")
        previous = min(indices)
        covered.update(indices)
        item = {"source_sections": list(indices)}
        for field, limit in (("eyebrow", 16), ("title", 20), ("summary", 40)):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
                raise ValueError(f"슬라이드 {field} 문구는 {limit}자 이내로 짧게 요약해야 합니다.")
            item[field] = value.strip()
        bullets = row.get("bullets")
        if (not isinstance(bullets, list) or not 1 <= len(bullets) <= 3 or
            any(not isinstance(b, str) or not b.strip() or len(b.strip()) > 22 for b in bullets)):
            raise ValueError("핵심 요점은 22자 이내의 짧은 문구 1~3개여야 합니다.")
        item["bullets"] = [b.strip() for b in bullets]
        text = " ".join([item["eyebrow"], item["title"], item["summary"], *item["bullets"]])
        if (re.search(r"<[^>]+>|\[(?:표지|사진|이미지|슬라이드)[^\]]*\]|페이지\s*\d+\s*/\s*\d+|핵심 내용\s*\d+", text)
            or any(re.fullmatch(r"\d+\s*/\s*\d+", item[key]) for key in ("eyebrow", "title"))):
            raise ValueError("슬라이드 요약에 제작 메모·HTML·페이지 번호가 포함됐습니다.")
        clean.append(item)
    if covered != set(range(1, len(sections) + 1)):
        raise ValueError("슬라이드 요약에서 누락된 원문 소제목이 있습니다.")
    return clean


def request_summaries(title: str, body: str, generate) -> list[dict]:
    """Bounded repair/legacy-article path; never regenerate the article itself."""
    headings = article_sections(body, title)
    prompt = (SLIDE_SUMMARY_PROMPT + "\n본문을 다시 작성하지 말고 {\"slides\":[10개 요약]} JSON 객체만 반환하세요.\n"
              + "[소제목 번호]\n" + "\n".join(f"{i}. {s['heading']}" for i, s in enumerate(headings, 1))
              + f"\n[완성된 원문 제목]\n{title}\n[완성된 원문 HTML]\n{body}")
    error = ""
    for _ in range(2):
        try:
            response = generate(prompt + ("\n[직전 응답 수정 요청]\n" + error if error else ""))
            if isinstance(response, tuple):
                response = response[0]
            return validate_summaries(json_object(response).get("slides"), title, body)
        except (ValueError, TypeError) as exc:
            error = str(exc)
    raise ValueError("슬라이드 요약 생성 실패: " + error)


def build_summary_slides(title: str, body: str, rows: object) -> list[dict]:
    rows = validate_summaries(rows, title, body)
    sections = article_sections(body, title)
    slides = []
    for index, row in enumerate(rows):
        selected = [copy.deepcopy(sections[i - 1]) for i in row["source_sections"]]
        slides.append({**{key: copy.deepcopy(row[key]) for key in ("eyebrow", "title", "summary", "bullets")},
                       "source_sections": selected, "source_text": "\n".join(s["text"] for s in selected),
                       "image": None, "layout": "사진 왼쪽" if index == 0 else "사진 오른쪽" if index == 1 else "공통 스타일"})
    return slides
