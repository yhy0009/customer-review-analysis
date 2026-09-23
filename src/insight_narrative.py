"""Keep request metadata out of the generated, user-facing narrative."""

import re
import unicodedata


NARRATIVE_RULES = """
JSON 키는 지정된 구조에만 사용한다. summary와 배열 항목의 문자열에는 입력·출력 필드명이나
`필드명: 값` 같은 메타데이터를 쓰지 않는다. praise_label, issue_candidates,
suggestion_candidates, summary_scope 같은 내부 이름을 사용자에게 노출하지 않는다.
장점은 필드의 문자열 값만 사용해 자연스러운 한국어 문장으로 연결한다.
예를 들어 praise_label 값이 '음질 좋음'이면 summary에는 '음질 좋음이라는 장점도 언급됩니다.'
처럼 쓴다. 이 예시의 장점은 실제 입력에 있을 때만 사용한다."""

# Match distinctive schema names, including escaped Markdown, case and common
# separator variations. Ordinary prose mentioning "label" or "summary" is valid.
_INTERNAL_NAMES = re.compile(
    r"(?<![a-z0-9_])(?:praise[_\s-]*(?:label|lable)|issue[_\s-]*candidates|"
    r"suggestion[_\s-]*candidates|summary[_\s-]*scope|total[_\s-]*complaint[_\s-]*topics|"
    r"positive[_\s-]*keywords|negative[_\s-]*keywords|improvement[_\s-]*suggestions|"
    r"review[_\s-]*count|topic[_\s-]*number|product[_\s-]*name)(?![a-z0-9_])",
    re.IGNORECASE,
)
_KEY_VALUE = re.compile(
    r"(?<![a-z0-9_])(?:summary|issues|label|complaints|praises|topics|quote)"
    r"[\s\"'`*]*:", re.IGNORECASE,
)


def validate_narrative(text: str) -> None:
    """Reject leaked schema names; never alter the AI's meaning by replacing text."""
    inspected = unicodedata.normalize("NFKC", text).replace("\\_", "_")
    if _INTERNAL_NAMES.search(inspected) or _KEY_VALUE.search(inspected):
        raise ValueError("Internal field name in insight narrative")
