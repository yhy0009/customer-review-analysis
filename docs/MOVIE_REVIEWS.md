# 제품·영화 리뷰 함께 사용하기

같은 DB에 제품과 영화 리뷰를 저장할 수 있습니다. 내부 `product_name` 필드는 두 도메인에서 **리뷰 대상 이름**을 뜻합니다. 영화 CSV의 `movie_title` 또는 `영화 제목` 열이 이 필드로 들어갑니다. 기존 제품 CSV와 API 필드명은 유지됩니다.

영화 리뷰에는 `review_domain=movie` 또는 `category=영화`를 넣으세요. `movie_title` 열을 사용하면 수집 단계에서 영화 유형이 자동 기록됩니다. 별점과 작성일은 선택 항목입니다. [예시 CSV](../data/sample_movie_reviews.csv)에는 가상 영화 두 편의 리뷰 4건이 있습니다.

```bash
python main.py import --file data/sample_movie_reviews.csv --policy skip
python main.py clean --policy skip
python main.py list --size 10
```

감정 분석은 리뷰마다 `product` 또는 `movie` 유형을 전달합니다. 영어 원문 전체나 리뷰 속 명령을 되풀이한 요약은 저장하지 않고 `null`로 둡니다. 감정 판정과 키워드는 유지합니다.

인사이트는 **한 도메인씩** 추출합니다. 영화는 제목이 있는 **한 작품씩** 추출해야 합니다. 제목이 없거나 여러 작품이 선택되면 AI 호출 전에 오류를 반환합니다. 제품과 영화를 함께 저장한 DB에서는 `--target`으로 대상을 지정하세요. 기존 `--product` 옵션도 계속 사용할 수 있습니다.

```bash
python main.py analyze --unanalyzed
python main.py extract --target "가상 영화 A"
```

`analyze`와 `extract`는 AI API를 호출합니다. 영화 인사이트는 서사·연출·연기 같은 작품 비평을 다루며, 제안은 해당 비평이 가리키는 작품 요소를 검토하는 수준으로 제한합니다. 서로 다른 영화의 평을 한 작품의 장단점으로 합치지 않습니다.

2026-09-27 기존 버전의 65건 평가는 변경 전 결과입니다. Cornell 영화 20건에는 제목이 없어 감정 분석 검토에는 사용할 수 있지만 작품별 인사이트 검토에는 사용할 수 없습니다. 사람 확정 라벨도 아직 없으므로 기존 수치를 새 버전의 성능으로 사용하지 마세요.

## 유형과 이름으로 정확히 선택하기

`list`, `stats`, `extract`, `dashboard`, `export`에서 `--domain product|movie`로 유형을 선택합니다. `--target`은 기본적으로 이름 부분 일치이며 `--exact-target`을 붙이면 대소문자를 무시한 전체 일치로 조회합니다. 예를 들어 `가상 영화 A`와 `가상 영화 A 2`를 구분할 수 있습니다. 웹 필터의 **리뷰 유형**, **이름 검색 → 전체 일치**도 동일하게 동작합니다. 다른 필터와 함께 통계·다운로드·인사이트 선택 범위에 적용됩니다.

```bash
python main.py list --domain movie --target "가상 영화 A" --exact-target
python main.py extract --domain movie --target "가상 영화 A" --exact-target
python main.py export --domain movie --target "가상 영화 A" --exact-target --format jsonl --output output/movie-a.jsonl
```

영화 제안은 전개·결말·서사·연기·대사·영상·음악 등 추출된 주제에 맞는 검토 항목입니다. 주제가 모호하거나 여러 요소가 섞이면 일반적인 작품 검토 문구를 사용합니다. 원인이나 각본 수정의 효과를 단정하지 않습니다.

## 개별 요약 상태

새 분석은 `summary_status`를 저장합니다. CLI `show`, 웹 리뷰 상세, CSV·JSONL·Excel에서 확인할 수 있습니다. 제외된 요약 원문은 별도로 저장하지 않습니다.

| 상태 | 의미 |
| --- | --- |
| `available` | 요약 제공됨 |
| `not_provided` | 모델이 요약을 제공하지 않음. 요약할 내용이 실제로 없다는 판정은 아님 |
| `filtered_length` | 160자 제한 초과로 제외 |
| `filtered_language` | 한국어 요약이 아니어서 제외 |
| `filtered_source_copy` | 원문 전체 반복으로 제외 |
| `filtered_instruction` | 알려진 명령문 반복으로 제외 |
| `legacy_unknown` | 이전 분석에 요약과 사유가 기록되지 않음 |

이 검사는 제한된 휴리스틱이며 모든 잘못된 요약을 탐지하지는 않습니다. 기존 분석은 자동으로 재호출하지 않습니다. 필요할 때 특정 리뷰에 `analyze --id ID --force`를 사용하면 새 프롬프트로 재분석하며 API 사용량이 발생합니다. 기존 인사이트는 프롬프트·필터·분석 정보가 달라지면 재생성이 필요합니다.

## 2026-09-27 수정 검증

통합 작업 트리에서 Python 570개 테스트(분석 기능 566개, 별도 사람 검토 도구 4개)와 JavaScript 14개 테스트가 통과했습니다. 마지막 분석 프롬프트 버전 변경 후 관련 Python 66개도 통과했습니다. SQLite v1/v2 이전·읽기 전용 호환, 유형/전체 일치 필터, 요약 제외 사유의 저장·조회·내보내기를 포함합니다.

`review-sentiment-v4-summary-status` / `review-insights-v9-domains`로 가상 영화의 영어 리뷰 2건을 실제 실행했습니다. 분석 2회·근거 추출 1회·인사이트 1회가 성공했고 구조 검사를 통과했습니다. 전개 비평에는 장면별 전개 속도 검토, 결말 비평에는 마지막 장면과 앞선 전개 검토를 제안했습니다. 두 요약은 한국어였고 `summary_status=available`로 저장됐습니다. 임시 라벨 2/2 일치는 소량 동작 확인이며 정확도 추정이 아닙니다. 사람 확정 라벨 검토는 아직 남아 있습니다.
