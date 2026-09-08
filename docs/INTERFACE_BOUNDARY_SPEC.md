# 고객 리뷰 분석 프로젝트 경계 인터페이스 명세

> 문서 상태: Baseline v1.1 (코드 인터페이스 반영)
>
> 작성일: 2026-09-06
>
> 대상 브랜치: `dev`
>
> 목적: 팀원별 구현 영역이 독립적으로 개발·테스트·통합될 수 있도록 모듈 간 계약을 정의한다.

## 1. 문서 적용 규칙

- 이 문서의 모든 계약은 별도 표시가 없는 한 **확정 사항**이다.
- 현재 코드와 다른 계약은 각 기능 브랜치를 통합하기 전에 이 문서에 맞게 수정한다.
- 현재 남아 있는 팀 공통 경계의 미합의 사항은 없다.
- 공통 계약을 바꿀 때는 15절의 인터페이스 변경 규칙을 따른다.

이 프로젝트는 현재 하나의 Python 프로세스 안에서 모듈을 호출하고 SQLite 또는
JSONL 저장소를 공유하는 구조다. 따라서 이 문서의 "API"는 HTTP API가 아니라
Python 함수, 데이터 객체, 저장소 스키마, 파일 입출력 계약을 뜻한다.

## 2. 담당 영역과 경계

| 담당자 | 소유 영역 | 다른 영역에 제공할 인터페이스 |
|---|---|---|
| `yhy0009` | CLI, 설정·로깅, 조회·통계, 내보내기 | CLI 인자, 명령 핸들러 계약, 공통 설정, 조회·Export 결과 |
| `sayknow` | 수집, 정제, 시각화 | Raw/Clean Review, Import/Clean 결과, 차트 산출물 |
| `highslow1536` | AI 분석, 인사이트 추출, 리포트 | Analysis Result, Insight Result, 리포트 산출물 |
| `yhy0009` | 공통 저장소 `storage.py` | Raw/Clean/Analysis 읽기·쓰기 Repository API |

### 2.1 명령별 핸들러 소유권

| CLI 명령 | 핸들러 소유자 | 주로 호출하는 모듈 |
|---|---|---|
| `import` | `sayknow` | `collector`, `storage` |
| `clean` | `sayknow` | `cleaner`, `storage` |
| `analyze` | `highslow1536` | `analyzer`, `storage` |
| `extract` | `highslow1536` | `analyzer`, `storage` |
| `list` | `yhy0009` | `storage` |
| `show` | `yhy0009` | `storage` |
| `stats` | `yhy0009` | `storage` |
| `dashboard` | `sayknow` | `storage`, `visualizer`, `reporter` |
| `export` | `yhy0009` | `storage`, `exporter` |

`dashboard` 핸들러는 전체 실행을 조율하고, 리포트 본문 생성은
`highslow1536`의 `reporter` 인터페이스를 호출한다.

## 3. 전체 처리 흐름

```text
CSV/Excel
   │
   ▼
collector ── RawReview ──► storage(raw) ──► cleaner
                                              │
                                         CleanReview
                                              │
                                              ▼
                                       storage(clean) ──► analyzer
                                                            │
                                                      AnalysisResult
                                                            │
                                                            ▼
                                                    storage(analysis)
                                                            │
                              ┌─────────────────────────────┼──────────────────┐
                              ▼                             ▼                  ▼
                       query/exporter                 visualizer           reporter
                              │                             │                  │
                              ▼                             ▼                  ▼
                         CLI/파일                      PNG 차트           TXT/Markdown
```

## 4. 공통 데이터 계약

### 4.1 공통 원칙

- 모듈 경계에서는 컬럼 위치에 의존하지 않고 필드 이름을 사용한다.
- 날짜의 외부 표현은 ISO 8601 `YYYY-MM-DD`로 통일한다.
- 감정 값은 `positive`, `neutral`, `negative` 소문자 세 값만 사용한다.
- 배치 함수는 일부 실패가 발생해도 처리 건수와 실패 사유를 반환한다.
- 저장소 내부 ID와 외부 원본 ID를 구분한다.
- 모듈 간 전달 타입은 `dataclass`로 통일한다.
- 공통 DTO와 enum은 `src/models.py`에 정의하고 `yhy0009`가 소유한다.
- `pandas.DataFrame`은 수집·집계 모듈 내부에서만 사용하며 공식 경계 밖으로 노출하지 않는다.
- Clean 이후 날짜 필드는 `datetime.date`, 생성·처리 시각은 UTC 기준
  timezone-aware `datetime.datetime`을 사용한다.
- 파일과 JSON으로 직렬화할 때 날짜·시각을 ISO 8601 문자열로 변환한다.

공통 enum은 Python 3.10 호환 `str, Enum`으로 구현하며 값은 다음으로 고정한다.

```python
Sentiment: positive | neutral | negative
DuplicatePolicy: skip | upsert
ProcessingStatus: RAW | REJECTED | CLEANED | ANALYZED | ANALYSIS_FAILED
```

### 4.2 RawReview

수집기가 생성하고 Raw 저장소가 소비한다.

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| `id` | `int \| None` | 아니요 | 저장소 조회 시 채워지는 내부 ID. 신규 수집 시 생략 |
| `source_review_id` | `object \| None` | 아니요 | 원본 데이터의 리뷰 ID |
| `product_name` | `object \| None` | 아니요 | 정제 전 영화명 또는 제품명 |
| `review_date` | `object \| None` | 아니요 | 정제 전 작성일 |
| `rating` | `object \| None` | 아니요 | 정제 전 원본 별점 |
| `review_text` | `object \| None` | 아니요 | 정제 전 원문 |
| `source_file` | `str \| None` | 아니요 | 유입 파일 추적용 |
| `raw_payload` | `dict[str, object]` | 예 | 표준 필드 외 원본 컬럼. 없으면 빈 dict |

공통 필드명은 `product_name`으로 확정한다. 수집기는 외부 컬럼 `movie_title`을
`product_name`으로 매핑하고, CLI의 `--product`는 `ReviewFilter.product_name`으로
변환한다. 저장소와 Export 컬럼도 `product_name`을 사용한다. RawReview는 결측값과
잘못된 날짜·별점을 그대로 담을 수 있고, 유효성 판정은 Cleaner가 담당한다.

### 4.3 CleanReview

정제기가 생성하고 저장소·AI 분석기가 소비한다.

| 필드 | 타입 | 필수 | 제약 |
|---|---|---:|---|
| `id` | `int` | 예 | 저장소 내부 고유 ID |
| `source_review_id` | `str \| None` | 아니요 | 원본 리뷰 ID |
| `product_name` | `str` | 예 | 앞뒤 공백 제거 후 빈 문자열 금지 |
| `review_date` | `date` | 예 | 리뷰 작성일 |
| `rating` | `int` | 예 | `1 <= rating <= 5` |
| `review_text` | `str` | 예 | 정규화 완료, 설정된 최소 길이 이상 |
| `cleaned_at` | `datetime` | 예 | UTC 기준 정제 시각 |

### 4.4 AnalysisResult

AI 분석기가 생성하고 저장소·조회·Export·시각화·리포트가 소비한다.

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| `review_id` | `int` | 예 | `CleanReview.id` 참조 |
| `sentiment` | `str` | 예 | `positive \| neutral \| negative` |
| `confidence` | `float` | 예 | `0.0 <= confidence <= 1.0` |
| `summary` | `str \| None` | 아니요 | 리뷰 단건 요약 |
| `keywords` | `list[str]` | 아니요 | 중복이 제거된 키워드 |
| `analyzed_at` | `datetime` | 예 | UTC 기준 분석 시각 |
| `provider` | `str` | 예 | 분석 제공자 |
| `model` | `str` | 예 | 실제 사용 모델 |
| `prompt_version` | `str \| None` | 아니요 | 결과 재현 및 마이그레이션용 |

### 4.5 InsightResult

여러 리뷰에 대한 `extract` 결과이며 리포터가 재사용할 수 있다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `filters` | `ReviewFilter` | 추출에 사용한 조건 |
| `review_count` | `int` | 실제 입력 리뷰 수 |
| `positive_keywords` | `list[KeywordCount]` | 긍정 키워드와 빈도 |
| `negative_keywords` | `list[KeywordCount]` | 부정 키워드와 빈도 |
| `issues` | `list[str]` | 주요 이슈 |
| `improvement_suggestions` | `list[str]` | 개선 제안 |
| `summary` | `str` | 전체 요약 |
| `generated_at` | `datetime` | UTC 기준 생성 시각 |

### 4.6 ReviewFilter

`list`, `stats`, `extract`, `dashboard`, `export`가 공유한다.

```python
@dataclass(slots=True)
class ReviewFilter:
    sentiment: Sentiment | None = None
    date_from: date | None = None
    date_to: date | None = None
    product_name: str | None = None
    rating: int | None = None
    rating_min: int | None = None
```

세부 규칙:

- `date_from`, `date_to`는 양 끝 날짜를 포함한다.
- `product_name`은 대소문자를 구분하지 않는 부분 일치를 사용한다.
- `rating`과 `rating_min`은 동시에 지정할 수 없으며 위반 시 `ValidationError`를 발생시킨다.

### 4.7 조회 및 배치 결과

`ReviewQuery`는 `ReviewFilter`와 다음 필드를 결합한다.

```python
@dataclass(frozen=True)
class ReviewQuery:
    filters: ReviewFilter
    page: int = 1
    size: int = 20
    sort: SortField = SortField.ID
    order: SortOrder = SortOrder.DESC
```

모든 `BatchOperationResult`는 다음 필드를 제공한다.

| 필드 | 타입 | 설명 |
|---|---|---|
| `processed` | `int` | 입력 또는 처리 대상으로 선택된 건수 |
| `succeeded` | `int` | 정상 완료 건수 |
| `skipped` | `int` | 중복 또는 기존 결과 때문에 건너뛴 건수 |
| `failed` | `int` | 실패 건수 |
| `rejected` | `int` | 정제 규칙에 의해 제외된 건수. 정제 외 배치는 0 |
| `errors` | `list[ItemError]` | 행 번호 또는 리뷰 ID, 오류 코드·메시지, 재시도 가능 여부 |

`CleanBatchResult`는 위 필드에 `reviews: list[CleanReview]`를,
`AnalysisBatchResult`는 `results: list[AnalysisResult]`를 추가한다. 모든 카운트는
`processed == succeeded + skipped + failed + rejected` 관계를 만족하고, 실패·제외
건마다 `ItemError` 하나 이상을 제공한다. 성공 결과 목록의 길이는 `succeeded`와 같다.

## 5. CLI ↔ 기능 모듈 계약

### 5.1 현재 확정된 계약

현재 [`src/cli.py`](../src/cli.py)는 다음 핸들러 형태를 사용한다.

```python
CommandHandler = Callable[[argparse.Namespace], Optional[int]]
```

- 핸들러 매핑 키는 CLI 명령 이름이다.
- 핸들러가 `None`을 반환하면 종료 코드 `0`으로 처리한다.
- 명시적인 정수를 반환하면 해당 값을 프로세스 종료 코드로 사용한다.
- 로드된 전체 설정은 `args.app_config`에 담겨 핸들러에 전달된다.
- 등록되지 않은 명령은 종료 코드 `2`를 반환한다.

### 5.2 핸들러 등록 경계

`src/handlers.py`는 의존성이 주입된 핸들러 매핑을 생성하고, `main.py`는 해당 매핑을
`src.cli.main()`에 전달한다.

```python
def build_handlers(services: ApplicationServices) -> dict[str, CommandHandler]:
    return {
        "import": _adapt(services.import_reviews, build_import_request),
        "clean": _adapt(services.clean_reviews, build_clean_request),
        "analyze": _adapt(services.analyze_reviews, build_analyze_request),
        "extract": _adapt(services.extract_insights, build_extract_request),
        "list": _adapt(services.list_reviews, build_list_request),
        "show": _adapt(services.show_review, build_show_request),
        "stats": _adapt(services.get_statistics, build_stats_request),
        "dashboard": _adapt(services.create_dashboard, build_dashboard_request),
        "export": _adapt(services.export_reviews, build_export_request),
    }
```

실제 코드의 Protocol 이름은 `ApplicationServices`이며 각 메서드는 아래 결과를 반환한다.

| 메서드 | 요청 | 반환 |
|---|---|---|
| `import_reviews` | `ImportRequest` | `BatchOperationResult` |
| `clean_reviews` | `CleanRequest` | `CleanBatchResult` |
| `analyze_reviews` | `AnalyzeRequest` | `AnalysisBatchResult` |
| `extract_insights` | `ExtractRequest` | `InsightResult` |
| `list_reviews` | `ListRequest` | `Page[ReviewDetail]` |
| `show_review` | `ShowRequest` | `ReviewDetail \| None` |
| `get_statistics` | `StatsRequest` | `ReviewStatistics` |
| `create_dashboard` | `DashboardRequest` | `DashboardResult` |
| `export_reviews` | `ExportRequest` | `ExportResult` |

구현체가 아직 없으므로 `main.py`의 기본 실행은 미연결 오류를 유지한다. 실제 서비스가
준비되면 `src.handlers.build_handlers()`에 구현체를 주입해 CLI에 연결한다.

### 5.3 CLI 명령 인자

| 명령 | 전달 인자 |
|---|---|
| `import` | `file`, `policy` |
| `clean` | `policy`, `min_length` |
| `analyze` | `analyze_all`, `review_id`, `unanalyzed`, `limit`, `force` |
| `extract` | `sentiment`, `date_from`, `date_to`, `product`, `limit` |
| `list` | `sentiment`, `date_from`, `date_to`, `rating`, `product`, `page`, `size`, `sort`, `order` |
| `show` | `review_id` |
| `stats` | `sentiment`, `date_from`, `date_to`, `product` |
| `dashboard` | `date_from`, `date_to`, `product`, `output`, `report_format`, `force` |
| `export` | `format`, `output`, `sentiment`, `date_from`, `date_to`, `rating_min`, `product`, `force` |

각 핸들러는 `argparse.Namespace`를 명령별 Request dataclass로 변환한 뒤 서비스
함수를 호출한다. 기능 모듈은 `argparse.Namespace`를 직접 참조하지 않는다.

## 6. 수집기 ↔ 저장소 계약

수집기의 공식 공개 함수는 다음 형태로 확정한다.

```python
def load_reviews(
    path: Path | str,
    column_overrides: Mapping[str, str] | None = None,
) -> list[RawReview]:
    ...
```

수집기 기능 브랜치의 현재 DataFrame 반환값은 통합 전에 `list[RawReview]`로 변환한다.

- DataFrame은 수집기 내부 구현에만 사용한다.
- 추가 원본 컬럼은 `RawReview.raw_payload: dict[str, object]`에 보존한다.
- 외부 컬럼 `review_id`는 `source_review_id`로 매핑한다. 값이 없으면 `None`으로
  반환하고 저장소가 내부 ID를 생성한다. 값 정규화와 중복 키 생성은 저장소가 담당한다.
- 빈 파일·미지원 확장자·컬럼 추론 실패는 `InputFileError`를 발생시킨다.
- `load_reviews`는 읽기와 표준화만 담당하고, 저장은 `import` 핸들러가 수행한다.

저장은 다음 경계를 사용한다.

```python
save_raw_reviews(
    reviews: Sequence[RawReview],
    policy: DuplicatePolicy,
) -> BatchOperationResult
```

## 7. 정제기 ↔ 저장소 ↔ 분석기 계약

### 7.1 정제 함수

```python
clean_reviews(
    reviews: Sequence[RawReview],
    options: CleaningOptions,
) -> CleanBatchResult
```

`CleanBatchResult`는 다음을 포함한다.

- 성공한 `CleanReview` 목록
- 입력 건수
- 성공 건수
- 제외 건수
- 실패 건수
- 리뷰별 제외·실패 사유

정제 기준과 저장 정책을 분리한다.

- `cleaner`: 정규화와 유효성 판정
- `storage`: 중복 탐지, `skip`/`upsert`, 트랜잭션

### 7.2 분석 함수

```python
analyze_review(
    review: CleanReview,
    options: AnalysisOptions,
) -> AnalysisResult

analyze_reviews(
    reviews: Sequence[CleanReview],
    options: AnalysisOptions,
    *,
    force: bool = False,
) -> AnalysisBatchResult
```

처리 규칙:

- `analyze_review`가 단건 분석을 담당하고 `analyze_reviews`가 배치 반복·재시도를 조율한다.
- AI 응답 JSON 파싱 실패는 `AIProviderError`로 처리한다.
- 설정된 횟수만큼 재시도한 뒤 실패하면 리뷰 상태를 `ANALYSIS_FAILED`로 저장한다.
- `force=False`일 때 기존 분석 결과를 건너뛰는 판단은 `analyze_reviews`가 담당한다.
- 리뷰별 최신 분석 결과 하나만 유지한다. 재분석 시 기존 결과를 덮어쓰되 모델과
  `prompt_version`을 함께 기록한다.

## 8. 공통 저장소 API

`yhy0009`가 `storage.py`와 저장소 스키마를 소유한다. SQLite를 기본·기준
백엔드로 사용하며, JSONL 백엔드도 동일한 상위 계약과 contract test를 통과해야 한다.

```python
class ReviewRepository(Protocol):
    def save_raw_reviews(
        self,
        reviews: Sequence[RawReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult: ...

    def fetch_raw_reviews(self, *, status: str | None = None) -> list[RawReview]: ...

    def save_clean_reviews(
        self,
        reviews: Sequence[CleanReview],
        policy: DuplicatePolicy,
    ) -> BatchOperationResult: ...

    def fetch_clean_reviews(
        self,
        filters: ReviewFilter | None = None,
        *,
        limit: int | None = None,
    ) -> list[CleanReview]: ...

    def fetch_unanalyzed_reviews(self, *, limit: int | None = None) -> list[CleanReview]: ...

    def save_analysis(self, result: AnalysisResult) -> None: ...

    def mark_analysis_failed(self, review_id: int, error_message: str) -> None: ...

    def get_review(self, review_id: int) -> ReviewDetail | None: ...

    def list_reviews(self, query: ReviewQuery) -> Page[ReviewDetail]: ...

    def get_statistics(self, filters: ReviewFilter | None = None) -> ReviewStatistics: ...
```

`src/services.py`에는 위 Repository 외에도 `ReviewCollector`, `ReviewCleaner`,
`ReviewAnalyzer`, `InsightExtractor`, `ReviewVisualizer`, `ReportGenerator`,
`ReviewExporter` Protocol을 제공한다. 각 담당 모듈은 해당 Protocol을 구조적으로
구현하고 CLI 계층은 구체 클래스가 아닌 `ApplicationServices`에만 의존한다.

### 8.1 중복 정책

```python
DuplicatePolicy = Literal["skip", "upsert"]
```

중복 키는 다음 순서로 만든다.

1. 원본 ID가 있으면 정규화한 `source_review_id`를 사용한다.
2. 원본 ID가 없으면 정규화한 `product_name`, `review_date`, `rating`, `review_text`를
   이어 붙인 값의 SHA-256 해시를 사용한다.

저장 규칙:

- `skip`은 기존 데이터를 변경하지 않고 `skipped` 건수만 증가시킨다.
- `upsert`는 기존 레코드를 갱신한다.
- Raw Review가 upsert되면 연결된 기존 Clean Review와 Analysis Result를 삭제하고
  해당 Raw Review를 다시 정제 대상으로 만든다.
- AI 입력에 영향을 주는 Clean 필드가 변경되면 기존 Analysis Result를 삭제하고
  상태를 `CLEANED`로 되돌린다.
- 데이터 한 건의 유효성·중복 문제는 행 단위 savepoint로 격리하고 성공 건은 커밋한다.
- 연결 실패나 스키마 오류 같은 저장소 인프라 문제는 배치 전체를 롤백하고
  `StorageError`를 발생시킨다.

### 8.2 저장 구조

- Raw, Clean, Analysis는 각각 `raw_reviews`, `clean_reviews`, `analysis_results`
  논리 영역으로 분리한다.
- `analysis_results.review_id`에는 UNIQUE 제약을 두어 최신 결과 하나만 유지한다.
- `raw_reviews`에는 중복 판정용 `dedupe_key`를 저장하고 UNIQUE 제약을 둔다.
- 생성·수정 시각은 UTC로 저장하고 직렬화 시 `Z`가 포함된 ISO 8601을 사용한다.
- 스키마 생성과 마이그레이션은 `storage.py` 소유자가 관리한다.

## 9. 분석 결과 ↔ 조회·Export·시각화·리포트 계약

### 9.1 조회 결과

`ReviewDetail`은 Clean Review와 최신 Analysis Result를 결합한 읽기 전용 객체다.
분석되지 않은 리뷰는 분석 필드를 `None`으로 반환한다.

페이지 결과는 다음 형식을 사용한다.

```python
from typing import Generic, TypeVar

T = TypeVar("T")

@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    page: int
    size: int
    total_items: int
    total_pages: int
```

### 9.2 통계 결과

`stats`, `dashboard`, `reporter`가 같은 집계 계약을 공유한다.

```text
total_reviews
analyzed_reviews
unanalyzed_reviews
failed_reviews
average_rating
sentiment_counts
sentiment_ratios
daily_sentiment_counts
rating_sentiment_matrix
top_positive_keywords
top_negative_keywords
```

집계 계산은 `storage.py`의 `get_statistics()`에서 한 번만 구현한다. 시각화와
리포터는 계산된 `ReviewStatistics`를 소비하며 자체적으로 같은 집계를 반복하지 않는다.

### 9.3 산출물 결과

```python
@dataclass(frozen=True)
class OutputArtifact:
    kind: Literal["chart", "report", "export"]
    path: Path
    format: str
```

- `visualizer`는 `list[OutputArtifact]`를 반환한다.
- `reporter`는 생성한 리포트의 `OutputArtifact`를 반환한다.
- `exporter`는 생성 파일의 `OutputArtifact`와 내보낸 행 수를 반환한다.
- 모든 모듈은 파일 경로를 `print`하지 않고 반환하며, 사용자 출력은 CLI가 담당한다.

## 10. 설정 인터페이스

현재 설정 구조는 [`src/config.py`](../src/config.py)의 `DEFAULT_CONFIG`를 기준으로 한다.

| 설정 섹션 | 주 소비자 |
|---|---|
| `paths` | CLI, visualizer, reporter, exporter |
| `storage` | storage 및 저장소 생성 코드 |
| `cleaning` | cleaner, import/clean 핸들러 |
| `ai` | analyzer |
| `visualization` | visualizer |
| `logging` | 공통 로깅 초기화 |

우선순위는 다음과 같이 유지한다.

```text
CLI 명시 옵션 > 환경변수 > config.json > 기본값
```

적용 규칙:

- API 키는 로그, 예외 메시지, 반환 객체에 포함하지 않는다.
- 기능 모듈에는 전체 설정 dict보다 필요한 섹션만 전달한다.
- 상대 경로 기준점은 현재 작업 디렉터리가 아니라 프로젝트 루트로 통일한다.
- 모듈은 환경변수를 직접 읽지 않고, 설정 모듈이 해석한 값을 받는다.

## 11. 처리 상태와 오류 계약

### 11.1 상태 모델

```text
RAW ──► CLEANED ──► ANALYZED
 │          │
 └─► REJECTED
            └─────► ANALYSIS_FAILED ──► 재시도 성공 시 ANALYZED
```

`REJECTED`는 입력 데이터가 정제 조건을 통과하지 못한 경우이고,
`ANALYSIS_FAILED`는 재시도 가능한 AI 처리 실패를 뜻한다.

### 11.2 공통 예외 계층

```python
class AppError(Exception): ...
class ConfigError(AppError): ...
class InputFileError(AppError): ...
class ValidationError(AppError): ...
class StorageError(AppError): ...
class AIProviderError(AppError): ...
class OutputError(AppError): ...
```

현재 `ConfigError`는 `ValueError`를 직접 상속하므로 `AppError`를 상속하도록 변경한다.

### 11.3 CLI 종료 코드

| 코드 | 의미 |
|---:|---|
| `0` | 정상 완료 |
| `1` | 명령 실행 실패 또는 일부 처리 실패 |
| `2` | CLI 사용법, 설정 또는 핸들러 연결 오류 |
| `3` | 입력 파일 또는 저장소 오류 |
| `4` | AI 제공자 오류 |

일부 실패가 있어도 성공 건을 저장하는 경우, 종료 코드 `1`과 함께
`processed/succeeded/skipped/failed` 건수를 출력한다.

## 12. 로깅 계약

- 모든 모듈은 `src.config.get_logger("모듈명")`을 사용한다.
- 정상 처리 요약은 `INFO`, 재시도·제외는 `WARNING`, 실패는 `ERROR`를 사용한다.
- API 키, 전체 리뷰 원문, 개인정보 가능 필드는 로그에 남기지 않는다.
- 배치 로그에는 가능하면 내부 `review_id`를 포함한다.
- 사용자를 위한 메시지는 CLI가 출력하고, 진단 정보는 logger로 기록한다.

## 13. 파일 출력 계약

- TXT·Markdown·JSONL은 UTF-8을 사용하고 CSV는 Excel 한글 호환을 위해
  UTF-8 BOM이 포함된 `utf-8-sig`를 사용한다.
- 출력 디렉터리가 없으면 생성한다.
- 기존 파일은 기본적으로 덮어쓰지 않는다.
- `dashboard`와 `export`에 `--force`를 추가하고 지정된 경우에만 덮어쓴다.
- 자동 파일명 날짜·시간은 UTC의 `YYYYMMDD_HHMMSS` 형식을 사용한다.
- JSONL에서 날짜는 ISO 8601 문자열, 값 없음은 JSON `null`로 표현한다.
- 생성 함수는 파일을 만든 뒤 `Path.resolve()`가 적용된 절대 경로를 반환한다.

## 14. 계약 테스트

각 모듈의 내부 구현보다 경계 계약을 먼저 테스트한다.

| 테스트 | 보장 내용 | 담당 |
|---|---|---|
| CLI contract test | 명령·인자·기본값·핸들러 호출 | `yhy0009` |
| Collector contract test | 입력 파일 → RawReview 필드·타입 | `sayknow` |
| Cleaner contract test | RawReview → CleanResult 및 제외 사유 | `sayknow` |
| Repository contract test | SQLite/JSONL의 동일 동작 | `yhy0009` |
| Analyzer contract test | CleanReview → AnalysisResult | `highslow1536` |
| Consumer contract test | 분석 결과를 조회·차트·리포트가 수용 | 공동 |
| Output contract test | 파일명·인코딩·필수 컬럼·산출물 경로 | `yhy0009`, `sayknow`, `highslow1536` |

공통 fixture는 다음 최소 사례를 포함한다.

- 정상 리뷰
- 원본 ID가 없는 리뷰
- 중복 리뷰
- 빈 본문과 최소 길이 미달 리뷰
- 잘못된 날짜와 별점
- 한글·영문·이모지가 섞인 리뷰
- AI 분석 완료·미완료·실패 리뷰

## 15. 인터페이스 변경 규칙

- 공통 모델, Repository API, CLI 인자를 변경하는 PR에는 영향받는 담당자를 reviewer로 지정한다.
- 필드 삭제·이름 변경은 같은 PR에서 생산자와 모든 소비자를 함께 수정한다.
- 새 선택 필드는 하위 호환으로 추가할 수 있지만 새 필수 필드는 인터페이스 변경 PR에서 승인받는다.
- 인터페이스 변경 시 계약 테스트와 이 문서를 함께 갱신한다.

## 16. 경계별 완료 조건

각 기능은 다음 조건을 만족해야 통합 가능한 것으로 본다.

1. 공개 함수의 입력·반환 타입과 예외가 문서화되어 있다.
2. 정상·빈 입력·잘못된 입력·부분 실패 테스트가 있다.
3. 다른 담당자 모듈 없이 mock 또는 fake로 단독 테스트할 수 있다.
4. 공통 데이터 필드와 enum 값을 임의로 변경하지 않는다.
5. 로그에 비밀값이나 전체 리뷰 원문을 남기지 않는다.
6. 생산자와 소비자의 contract test가 모두 통과한다.


## 17. SQLite Raw 저장소 구현 현황

첫 구현은 `SQLiteReviewRepository(database_path)`의 스키마 초기화, 연결 종료,
`save_raw_reviews()`, `fetch_raw_reviews()`를 제공한다. 아직 전체 `ReviewRepository`
구현체는 아니며 Clean/Analysis 저장·조회, 통계, JSONL, CLI 연결은 후속 작업이다.

- 상대 DB 경로는 프로젝트 루트 기준이다. 상위 디렉터리를 생성하며 메모리 DB는 허용하지 않는다.
- `with SQLiteReviewRepository(path) as repository:`로 사용하면 종료 시 연결을 닫는다.
- 스키마 버전은 `PRAGMA user_version = 1`이며 Raw/Clean/Analysis 테이블을 생성한다.
  기존 미등록 스키마나 지원하지 않는 버전은 자동 변경하지 않고 `StorageError`를 반환한다.
- `RawReview.id`는 하위 호환 선택 필드다. 저장 시 입력 ID는 무시하고 조회 시 내부 ID를 채운다.
  후속 Cleaner는 이 ID를 `CleanReview.id`에 전달한다. 원본 ID와 내부 ID는 별개다.
- Raw 필드와 원본 추가 컬럼은 JSON으로 보관한다. 잘못된 별점·날짜·빈 본문은
  저장하고 정제 여부는 Cleaner가 판단한다. 날짜 객체는 ISO 문자열, timezone-aware
  시각은 UTC `Z` 문자열, float NaN은 JSON null로 저장한다.
  JSON으로 표현할 수 없는 객체·무한대·문자열이 아닌 dict 키는 행 단위 실패로 처리한다.
- 중복 비교는 NFKC Unicode 정규화와 연속 공백 축약을 적용하되 대소문자는 유지한다.
  숫자형 원본 ID `1`과 `1.0`은 같고 문자열 `"001"`은 별개다. 공백뿐인 ID와 NaN은
  ID 없음으로 취급한다. ID가 없으면 제품·날짜·별점·본문을 JSON 배열로 묶어 SHA-256을
  계산한다. ISO 날짜와 숫자형 별점을 정규화하므로 별점 `5`, `5.0`, `"5"`는 같다.
  파일명과 추가 원본 컬럼은 중복 키에 포함하지 않는다. 저장하는 원문 자체는 정규화하지 않는다.
- `skip`은 원본·타임스탬프·하위 데이터를 보존한다. `upsert`는 내부 ID와 생성 시각을
  보존하고 수정 시각을 갱신하며, 연결된 Clean/Analysis를 삭제하고 Raw 상태를 복원한다.
- 조회는 내부 ID 오름차순이며 상태 필터는 `ProcessingStatus` enum을 받는다.
- 배치는 명시적 트랜잭션과 행별 savepoint를 사용한다. 행 오류는 1부터 시작하는
  입력 행 번호를 `ItemError.item_ref`로 반환한다. 저장소 장애는 배치 전체를 롤백한다.
  로그와 오류 메시지에는 원문·원본 ID·원본 추가 컬럼을 포함하지 않는다.
