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

기존 CLI는 Python 모듈과 SQLite 또는 JSONL 저장소를 공유한다. 기존 절의 "API"는
Python 함수·데이터 객체·저장소·파일 계약을 뜻한다. JS 웹 대시보드는 별도의 로컬 HTTP
어댑터를 사용하며, 해당 계약은 25절과 웹 대시보드 안내에 정의한다.

## 2. 담당 영역과 경계

| 담당자 | 소유 영역 | 다른 영역에 제공할 인터페이스 |
|---|---|---|
| `yhy0009` | CLI, 설정·로깅, 조회·통계, 내보내기 | CLI 인자, 명령 핸들러 계약, 공통 설정, 조회·Export 결과 |
| `sayknow` | 수집, 정제, Python 시각화 자료 | Raw/Clean Review, Import/Clean 결과, 차트 산출물 |
| `highslow1536` | AI 분석, 인사이트 추출, 리포트, JS 웹 대시보드 | Analysis Result, Insight Result, 리포트 산출물, 웹 화면·조회 HTTP 어댑터 |
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
`highslow1536`의 `reporter` 인터페이스를 호출한다. 이 CLI 소유권과 별도로
JS 웹 화면 및 연결 서버는 `highslow1536`이 담당한다. 시각화 차트 자체는
`sayknow`의 `DashboardVisualizer` 산출물을 그대로 사용한다.

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

`src/handlers.py`는 의존성이 주입된 핸들러를 생성한다. `src.cli.main()`에 매핑을
명시하면 해당 매핑을 사용하고, 생략하면 `src.runtime.build_default_handlers()`로
현재 연결된 `analyze/extract/list/show/stats/export`의 기본 매핑을 구성한다.
빈 매핑 `{}`도 명시적 주입으로 취급한다.

```python
def build_handlers(services: ApplicationServices) -> dict[str, CommandHandler]:
    return {
        "import": build_import_handler(services.import_reviews),
        "clean": build_clean_handler(services.clean_reviews),
        "analyze": build_analyze_handler(services.analyze_reviews),
        "extract": build_extract_handler(services.extract_insights),
        "list": build_list_handler(services.list_reviews),
        "show": build_show_handler(services.show_review),
        "stats": build_stats_handler(services.get_statistics),
        "dashboard": build_dashboard_handler(services.create_dashboard),
        "export": build_export_handler(services.export_reviews),
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

`QueryService`는 목록·상세·통계를 구현한다. 기본 조회 핸들러는 인자 검증 후 설정에서
SQLite를 열고 서비스 실행 뒤 연결을 닫는다. `ExportService`도 기본 `export` 명령에 연결됐다.
기존 `AnalysisService`·`InsightService`도 기본 `analyze`·`extract`에 연결됐다.
`ImportService`는 수집기와 원본 저장소를 연결하며 기본 `import` 명령에 등록됐다.
`CleanService`도 기본 `clean` 명령에 등록됐다. `dashboard`는 미연결 오류를 유지한다.
전체 서비스 구현체가 준비되면 `build_handlers()`를 이용해 9개 명령을 함께 주입할 수 있다.

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

수집기는 DataFrame을 내부에서 `list[RawReview]`로 변환해 반환한다.

- DataFrame은 수집기 내부 구현에만 사용한다.
- 추가 원본 컬럼은 `RawReview.raw_payload: dict[str, object]`에 보존한다.
- 외부 컬럼 `review_id`는 `source_review_id`로 매핑한다. 값이 없으면 `None`으로
  반환하고 저장소가 내부 ID를 생성한다. 값 정규화와 중복 키 생성은 저장소가 담당한다.
- 빈 파일·미지원 확장자·컬럼 추론 실패는 `InputFileError`를 발생시킨다.
- `load_reviews`는 읽기와 표준화만 담당하고, `ImportService`가 원본 저장을 요청한다.
  핸들러는 요청 변환·결과 출력·종료 코드 처리를 담당한다.

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

`CleanService`는 모든 저장 원본(REJECTED 포함)을 ID 순서로 처리한다. `skip`일 때 이미
Clean이 있는 ID는 정제 전에 건너뛰고, `upsert`일 때는 전체를 다시 검증한다.
원본 한 건마다 정제 후 성공 리뷰를 저장하거나 `mark_cleaning_rejected()`로 제외 상태를 기록한다.
실제 저장 성공만 `succeeded`와 `reviews`에 포함하고, 저장 건너뛰기·행 오류·정제 제외를
합산한다. 모든 `ItemError.item_ref`는 원본 내부 ID다. 예기치 않은 정제 실패는 `failed`로
집계하며 기존 상태를 유지한다. DB 장애는 중단·전파하고 앞선 항목의 커밋은 유지한다.

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

    def mark_cleaning_rejected(self, review_id: int) -> None: ...

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
- 정제 제외 저장은 Raw를 보존하고 상태를 `REJECTED`로 바꾸며 기존 Clean·Analysis를
  같은 트랜잭션에서 삭제한다. 기존 스키마 v1을 유지하며 제외 사유는 서비스 결과에 포함한다.
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
`clean` 재실행은 REJECTED도 다시 검사한다. 기준 완화 또는 원본 수정 뒤 통과하면 같은 ID로
CLEANED가 된다. `clean --policy upsert`에서 기존 정제 리뷰가 제외되면 REJECTED가 되고
기존 정제·분석 결과가 삭제된다. `skip`은 기존 Clean 리뷰를 재검증하지 않는다.

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


## 17. SQLite 저장소 통합 구현 현황

`src/storage.py`는 공통 `ReviewRepository` Protocol을 정의하고,
`src/sqlite_repository.py`의 `SQLiteReviewRepository(database_path)`가 Raw/Clean/Analysis
저장·조회 및 통계를 구현한다. 기존 `src.storage.SQLiteReviewRepository`와
`src.sqlite_repository.SqliteReviewRepository`는 동일한 구현을 가리키는 호환 이름이다.
JSONL 저장소는 후속 작업이다. 기본 CLI의 list/show/stats와 export는 각각 조회·내보내기 서비스에,
analyze/extract는 기존 AI 서비스에 연결돼 있다.

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


### 17.1 통합 시 ID·트랜잭션 규칙

- PR #5의 스키마 v1과 Raw JSON 직렬화 형식을 유지한다. `CleanReview.id`는
  반드시 저장소에서 조회한 `RawReview.id`이며, Clean 저장 시 재발급하지 않는다.
  원본이 없는 ID는 행 단위 실패다. 중복 판단은 해당 원본 ID를 기준으로 한다.
- Raw와 Clean 배치는 모두 바깥 `BEGIN IMMEDIATE`와 행별 savepoint를 사용한다.
  행의 잘못된 값은 다른 성공 행을 막지 않고, DB 장애는 앞선 갱신과 분석 삭제까지 롤백한다.
- Clean upsert에서 AI 입력 필드가 바뀐 경우에만 기존 분석을 무효화한다.
- 분석 저장과 상태 변경은 하나의 트랜잭션이다. 최근 분석 실패를 기록하면 이전 결과를
  제거해 미분석 재시도 대상으로 만들며, 재시도 성공 시 실패 상태를 해제한다.
  실패 메시지에는 원문·인증 정보가 포함될 수 있어 고정된 실패 사유만 저장한다.
- 통계의 `unanalyzed_reviews`는 실패를 제외한 미분석 수이며 `failed_reviews`는 별도 집계한다.
  따라서 총수는 분석 완료·미분석·실패 수의 합이다.
- 통계의 별점×감정 행렬은 별점 1~5와 모든 감정의 0건 구간을 포함한다. 분석된 날짜별
  집계에도 모든 감정을 포함한다. 평균 별점은 표시 전 반올림하지 않으며, 상위 키워드는
  건수 내림차순·동률 시 키워드 오름차순으로 정렬한다. 한 리뷰의 같은 키워드는 한 번 센다.
- 제품명 검색은 Unicode casefold 기반 부분 일치이며 `%`, `_`도 일반 문자로 검색한다.
- 이전 별도 구현의 버전 없는 DB는 v1과 호환되지 않는다. 자동 덮어쓰기나 암묵적
  마이그레이션을 하지 않고 초기화 오류를 반환한다. 기존 데이터는 보존되며 별도 변환이 필요하다.

기존 import 경로, 구형 DB 확인과 변환 시 지켜야 할 사항은
[SQLite 저장소 안내](SQLITE_STORAGE.md)를 참고한다.


### 17.2 수집·정제 연결

수집기의 `load_reviews()`는 ID 없는 `RawReview`를 반환한다. 호출자는 먼저
`save_raw_reviews()`로 저장하고 `fetch_raw_reviews(status=ProcessingStatus.RAW)`로
다시 조회한 객체를 Cleaner에 전달한다. Cleaner는 해당 내부 ID를 그대로 유지한다.
ID 없는 입력은 임의 번호를 붙이지 않고 배치의 실패 행으로 반환한다.
Collector는 파일 없음·잘못된 형식 모두 `src.errors.InputFileError`를 사용하므로
CLI의 공통 오류 처리(종료 코드 3)에 연결된다. 두 모듈은 공통 logger를 사용한다.

## 18. AI 분석 구현 현황

`src/analyzer.py`의 `analyze_review(review, options)`는 7.2절의 단건 계약을 구현한다.
`src/ai_provider.py`는 OpenAI 호출을 분리하며, 기본 모델은 `gpt-5-mini`다.
`AnalysisOptions`에 선택 필드 `base_url: str | None = None`과
`reasoning_effort: str | None = "minimal"`을 추가한다.
기존 호출의 호환성과 Protocol 메서드 시그니처는 유지한다. 설정 모듈은
`AI_BASE_URL`을 `ai.base_url`에 반영한다. `openai`는 공식 주소를 사용하고,
`openai-compatible`은 명시한 서버 주소를 사용하며 자동으로 다른 서버를 호출하지 않는다.
공식 어댑터는 JSON Schema와 추론 옵션을 전송한다. COPA 호환 어댑터는 고급 옵션
거절에 대응해 model/messages만 보내고 스키마를 시스템 프롬프트에 포함한다.
두 경로 모두 동일한 로컬 응답 검증을 적용한다.

단건 요청은 재시도·DB 저장 없이 `AnalysisResult`를 반환한다. API 오류·불완전 응답·
응답 검증 실패는 `AIProviderError`로 변환한다. API 응답의 모델 이름과 실제 사용한
프롬프트 버전을 기록하고, 오류 메시지와 모듈 로그에 원문·키를 포함하지 않는다.

`BatchReviewAnalyzer(repository, provider=None)`는 7.2절의 단건·배치 메서드를 모두
제공한다. `force=False`이면 기존 결과를 건너뛰고, 최초 호출 후 최대 `max_retries`회
재시도한다. 대기 간격은 1초에서 시작하여 두 배씩 증가하고 16초로 제한한다.
인증·잘못된 요청·할당량 소진·거부·출력 중단처럼 같은 요청으로 회복할 수 없는 오류는
재시도하지 않는다. 실패 상태와 고정된 오류 메시지를 저장한 뒤 다른 리뷰를 계속 처리한다.
성공 건은 즉시 저장하고, 저장소·설정 오류는 배치를 중단하여 기존 성공 커밋을 보존한다.

한 배치의 반복 ID는 추가 호출 없이 skipped로 집계한다. 저장된 정제 리뷰가 없거나
제품명·별점·본문이 현재 저장소 값과 다르면 상태를 변경하지 않고 행 오류로 집계한다.
`AnalysisBatchResult.results`는 이번 배치에서 저장까지 성공한 결과만 포함한다.

`AnalysisService`는 전체·미분석·단건 대상을 조회하여 배치 분석기로 전달한다.
`build_analyze_handler()`는 분석 서비스만 CLI에 주입할 수 있으며 처리 건수 요약과
공통 종료 코드를 제공한다. `main.py`의 기본 analyze/extract/list/show/stats/export는 연결됐다.
전체 `ApplicationServices` 구성은 후속 작업이다. fake 저장소 배치 테스트와 함께 실제 SQLite에 분석 서비스를
연결하여 저장·실패·재시도·강제 재분석·저장소 오류 중단과 통계를 검증한다.

실행 예시, 테스트 방법, llama-server 확장 경계는 [AI 분석 안내](AI_ANALYSIS.md)를 따른다.


## 19. 조회 CLI 구현 현황

`QueryService`는 Repository의 `list_reviews`, `get_review`, `get_statistics`를 호출한다.
`src/query_output.py`는 조회 결과를 순수 문자열로 변환하며 핸들러가 stdout으로 출력한다.
기존 요청·결과 모델과 공통 Repository 계약은 변경하지 않는다.

- `show`는 Clean 본문과 최신 분석 결과를 표시한다. 분석 데이터가 없으면 미분석/실패를
  추측하지 않고 `분석 결과 없음`을 표시한다. ID 없음은 stderr 안내와 종료 코드 1이다.
- 빈 목록·범위 밖 페이지·빈 통계는 정상 조회(종료 코드 0)다.
- 통계의 분석 완료율은 전체 Clean 기준, 감정 비율은 분석 완료 수 기준이다.
- 상대 설정 파일, `.env`, 로그 파일 경로는 기존 DB·입출력 경로와 같이 프로젝트 루트 기준이다.
- 요청 인자 검증 뒤 DB를 열고 항상 닫는다. 미연결 명령은 DB를 열지 않는다.
- 조회를 실행하기 위해 AI SDK 또는 API 키를 준비할 필요는 없다.

실행 예시와 명령별 종료 코드는 [조회 CLI 안내](QUERY_CLI.md)를 참고한다.


## 20. 내보내기 구현 현황

`src/export_service.py`의 `ExportService`는 공통 `list_reviews()`를 재사용해 필터에
맞는 전체 `ReviewDetail`을 ID 오름차순으로 읽는다. `FileReviewExporter`는 기존
`ReviewExporter` Protocol을 구현해 CSV·JSONL·Excel 파일과 `ExportResult`를 반환한다.

- 컬럼 순서: `id`, `source_review_id`, `product_name`, `review_date`, `rating`,
  `review_text`, `cleaned_at`, `sentiment`, `confidence`, `summary`, `keywords`,
  `analyzed_at`, `provider`, `model`, `prompt_version`.
- 키워드는 JSONL에서 배열, CSV·Excel에서 JSON 배열 문자열이다. 분석이 없으면
  분석 필드는 모두 결측값이며 JSONL에서는 `null`로 표현한다.
- CSV는 수식처럼 해석될 수 있는 문자열에 작은따옴표를 붙이고 Excel은 문자열 셀로
  저장한다. 파일 생성 실패나 Excel 셀·행 제한 초과 시 불완전한 결과로 기존 파일을 바꾸지 않는다.
- `SQLiteReviewRepository.read_snapshot()`은 CLI 내보내기에 사용되는 추가 컨텍스트다.
  기존 공통 Repository Protocol은 유지하며 다중 페이지 조회를 하나의 읽기 트랜잭션으로 묶는다.
- 필터 조회가 0건이어도 파일을 생성한다. CSV/Excel은 헤더를 포함하고 JSONL은 빈 파일이다.
- 내보내기는 임시 파일 작성 후 최종 경로로 게시한다. 기본은 덮어쓰기 금지이며
  `--force`가 있으면 성공한 파일만 교체한다. 사용 중인 DB 및 관련 journal 파일은 보호한다.

상세 포맷·파일명·종료 코드와 실행 예시는 [내보내기 안내](EXPORT.md)를 따른다.

## 21. 인사이트 추출 구현 현황

`src/insight_extractor.py`의 `AIInsightExtractor`는 기존 `InsightExtractor` Protocol을
구현하고, `src/insight_service.py`의 `InsightService`는 `ExtractRequest`를 받아
`InsightResult`를 반환한다. 공통 모델·Protocol은 유지하며 기본 CLI 연결은 23절을 따른다.

- 조건에 맞는 분석 완료 리뷰를 ID 오름차순으로 선택한 뒤 `limit`을 적용한다.
  `review_count`는 실제 선택 수다. 중립도 포함하며 미분석·실패는 제외한다.
- 긍·부정 키워드는 해당 선택 집합의 저장된 키워드를 리뷰당 한 번씩 세어 상위 10개를
  반환한다. SQLite 통계와 동일한 빈도·동률 정렬 규칙을 사용한다.
- AI는 내부적으로 리뷰별 근거를 먼저 추출하고 이슈·전체 요약을 작성한다. 최종 이슈의
  근거 label 누락을 검사하고, 개선 제안은 해당 불편 label에 근거한 점검·절차 개선 후보에서
  선택한다. 반환 DTO는 그대로이며 근거·요약 각 단계에 제한된 재시도를 수행한다.
  개별 리뷰의 분석·상태는 쓰지 않는다. 정상 요청은 2회이며 공통 AI timeout 기본은 90초다.
- SQLite 연결 시 `snapshot=repository.read_snapshot`을 주입한다. 서비스는 대상 조회를
  스냅샷 안에서 마친 후 트랜잭션을 종료하고 AI를 호출한다.
- 빈 대상은 API를 호출하지 않는다. 기본 24,000자 사용자 JSON 한도를 넘으면 내용을
  자르지 않고 필터나 `limit`을 줄이도록 안내한다. 대량 입력 분할 요약은 후속 작업이다.

상세 규칙과 서비스 연결·검증 방법은 [인사이트 추출 안내](INSIGHT_EXTRACTION.md)를 따른다.

## 22. 종합 리포트 구현 현황

`src/reporter.py`의 `FileReportGenerator`는 기존 `ReportGenerator` Protocol을 구현한다.
`ReviewStatistics`와 선택적인 `InsightResult`를 받아 TXT·Markdown 리포트를 생성하고
`OutputArtifact`를 반환한다. 저장소 조회·AI 호출·차트 생성·CLI 출력은 호출자가 조율한다.

- 처리 건수, 정제 리뷰 기준 완료율·실패율, 평균 별점, 감정 비율, 날짜·별점별 집계와
  전달된 순서의 TOP N 키워드를 표시한다. 원본 리뷰를 재집계하지 않는다.
- 인사이트의 자체 조건·실제 대상 수·시각을 별도 표시한다. 통계 DTO에 조회 조건이 없으므로
  두 결과가 같은 모집단이라는 가정을 하지 않는다. 인사이트 없이도 리포트를 생성한다.
- 자동 파일명은 `report_YYYYMMDD_HHMMSS.txt|md`(UTC), 인코딩은 UTF-8이며 반환 경로는
  절대경로다. `force=True`일 때만 기존 파일을 교체하고 실패 시 기존 내용을 보존한다.
- Markdown에서는 외부 텍스트의 마크업을 이스케이프한다. 통계와 인사이트 입력은 변경하지 않는다.

상세 출력 규칙과 dashboard 조율자 연결 예시는 [종합 리포트 안내](REPORT_GENERATION.md)를 따른다.

## 23. 분석·인사이트 기본 CLI 연결

`src/runtime.py`는 공통 AI 설정에서 `AnalysisOptions`를 구성하고 기존 분석·추출 서비스를
기본 명령에 연결한다. AI 코어와 공유 Repository·Request·Result 계약은 유지한다.

- `analyze`: `AnalysisService`와 `BatchReviewAnalyzer`를 조합한다. 기존 선택·건너뛰기·
  재시도·저장 동작과 `processed/succeeded/skipped/failed` 출력을 사용한다.
- `extract`: `InsightService`와 `AIInsightExtractor`를 조합하며
  `snapshot=repository.read_snapshot`을 주입한다. AI 호출 전에 읽기 트랜잭션을 종료한다.
- `build_extract_handler()`가 `format_insight_result()`의 한국어 문자열을 출력한다.
  실제 대상 수·필터·UTC 생성 시각·키워드별 리뷰 수·요약·이슈·개선안을 표시한다.
  대상 0건에서는 기존 선택 필드에 내용이 남아 있어도 AI 요약으로 표시하지 않는다.
- AI 명령을 실행할 때만 SDK를 import한다. SDK 누락은 설정 오류(2)이며 조회·CSV/JSONL
  내보내기와 도움말은 AI SDK가 없어도 동작한다. 명시적 핸들러·빈 매핑 주입도 유지한다.
- 각 명령은 요청 검증 후 연결을 열고 성공·실패 모두 연결을 닫는다. 분석 배치의 행 실패는
  1, 설정·인자 오류는 2, 저장소 오류는 3, 추출 AI 오류는 4로 매핑한다.
- 공통 설정의 지원 필드만 AI 옵션에 전달하며 각 모듈의 내장 프롬프트 버전을 사용한다.

기본 실행 사용법과 통합 테스트는 [AI CLI 실행 안내](AI_CLI.md)를 따른다.


## 24. 수집·정제·대시보드 공통 CLI 연결 경계

세 명령에 공통 결과 출력과 선택적 등록 경계를 제공한다. import·clean은 구체 서비스까지 연결됐다.
기존 Request·Result·ApplicationServices 계약은 유지하며, Repository에는 정제 제외 상태 기록 메서드를 추가한다.

- `build_import_handler`, `build_clean_handler`, `build_dashboard_handler`는 요청 생성·
  공통 오류 처리와 함께 결과를 stdout에 표시한다. `build_handlers(services)`도 이를 사용한다.
- `format_batch_result()`는 processed/succeeded/skipped/failed/rejected와 ItemError 상세를
  표시한다. failed 또는 rejected가 있으면 기존 배치 계약에 따라 종료 코드 1이다.
- `format_dashboard_result()`는 반환된 산출물의 종류·포맷·경로를 표시한다.
  빈 목록은 파일이 생성되지 않았음을 표시하며 정상 반환(0)으로 처리한다.
- `build_default_handlers()`는 기본 import·clean 생성 함수를 사용한다. 해당 factory를 전달하면
  교체하고, `dashboard_factory`를 전달하면 해당 명령을 추가한다.
  명시적인 `None`은 해당 파이프라인 명령을 등록하지 않는다. 생성 함수는
  `(repository, config)`를 받아 기존 요청을 소비하는 서비스 메서드를 반환한다.
- 요청 검증 후 명령별 SQLite 연결을 열고 생성 함수를 호출한다. 서비스는 연결을 빌려 쓰며
  런타임이 성공·오류·사용자 중단 모두 연결을 닫는다. 전체 명령의 트랜잭션을 추가하지 않는다.
- 생성 함수와 서비스를 실행할 때 발생한 ImportError는 의존성 안내와 코드 2로 처리한다.
  기본 매핑 생성만으로 구체 기능 모듈이나 선택적 SDK를 import하지 않는다.

인자 없는 기본 실행은 import·clean을 포함한 8개 명령을 등록한다. `ImportService`는 수집기의
원본 목록과 요청의 중복 정책을 `save_raw_reviews()`에 전달하고 배치 결과를 그대로 반환한다.
날짜·평점·본문 유효성 검사는 정제 단계에 맡긴다. `dashboard`의 기본 연결은 후속 단계다.
자세한 연결 규약과 검증 방법은
[수집·정제·대시보드 CLI 연결 안내](CLI_PIPELINE_INTEGRATION.md)를 따른다.


## 19. 인사이트 근거 선택 필드 (v5)

AI·리포트 확장으로 기존 `InsightResult` 생성자의 필수 인자는 유지하며 다음 선택 필드를 추가한다.

- `evidence_groups: list[InsightEvidenceGroup] = []`
- `summary_scope: str = "all_evidence"` (`all_evidence` 또는 `top_complaints`)
- `InsightEvidenceGroup(product_name, kind, label, citations)`: kind는 complaints/praises.
  `review_count` 속성은 인용의 서로 다른 review_id 수다.
- `InsightCitation(review_id, label, quote)`: 원래 label·원문 인용 및 내부 리뷰 ID.

분할 추출의 모든 근거를 보존하며 API에는 내부 리뷰 ID를 전송하지 않는다. 주요 이슈는 기존
최대 3개/항목당 80자, 전체 요약 160자 계약을 유지한다. 대량 입력의 주요 이슈는 전체 근거
목록을 참조하며 선택 범위를 명시한다. reporter와 extract 출력 어댑터는 전체 근거를 표시한다.
기존 DTO 소비자는 기본값으로 호환되며 Repository/DB/Request/CLI 인자 계약은 바꾸지 않는다.
정렬·제한·동의어 병합은 [인사이트 추출 안내](INSIGHT_EXTRACTION.md)를 따른다.


## 25. JS 웹 대시보드 조회 경계

- `web/`는 빌드 없는 JS 모듈 기반 화면, `src/dashboard_server.py`는 로컬 HTTP 어댑터다.
- `SQLiteReviewRepository(path, read_only=True)`는 기존 DB만 조회한다. 스키마 생성·이행을
  수행하지 않으며 쓰기 SQL을 거부한다. 기본 생성자와 기존 import 경로는 유지한다.
- `GET /api/snapshot`은 같은 `read_snapshot()` 안에서 통계·리뷰 페이지·인사이트 근거를
  읽고 `schema_version=1`, `snapshot_id`를 반환한다. JS는 별도로 통계를 재계산하지 않는다.
- 차트, TXT/MD 리포트, 리뷰 상세는 이 ID의 보관된 데이터만 사용한다. 보관 한도는 16개,
  수명은 15분이다. 만료 또는 한도 초과로 삭제된 결과는 HTTP 410이며 새 조회가 필요하다.
- 인사이트 파일에는 선택 한도·리뷰 ID·원문과 분석의 SHA-256·필터를 저장한다.
  필터나 원문·분석이 달라진 결과는 표시하지 않는다. AI 호출은 명시적 준비 스크립트로 분리한다.
- 차트는 기존 `DashboardVisualizer`, 리포트는 기존 `FileReportGenerator`를 호출한다.
  DB 스키마, 기존 CLI 명령, 담당자의 시각화 구현을 변경하지 않는다.
- 서버는 127.0.0.1에만 바인딩한다. 브라우저에는 API 키·원본 파일 경로·Raw payload를 보내지 않는다.

세부 JSON 필드와 실행·오류 계약은 [웹 대시보드 안내](WEB_DASHBOARD.md)를 따른다.

### 25.1 조건별 인사이트 생성 확장

- `--enable-insights`로 명시적으로 활성화한 서버에 한해 화면에서 생성할 수 있다.
  리뷰 DB는 계속 읽기 전용이며 결과는 별도 조건별 캐시 파일에 저장한다.
- snapshot v1에 선택적 `generation` 필드를 추가한다. 기존 소비자와 고정 인사이트 파일은
  유지한다. 생성에는 이미 조회한 snapshot ID를 전달하고 서버에서 원문 변경을 재확인한다.
- `POST /api/insight-jobs`, `GET /api/insight-jobs/{id}`가 생성·상태 조회를 제공한다.
  한 서버의 동시 작업은 1개이며 같은 요청은 합치고, 실패 후 사용자 재시도만 허용한다.
- 캐시는 DB 경로·필터·선택 한도별로 분리하며 원문·분석 해시와 인용 검증을 유지한다.
  생성 완료 전후의 화면·리포트는 각 snapshot의 데이터를 사용한다.
- 조회 요청에서는 AI 설정이나 자격 증명을 로드하지 않는다. 생성 요청만 기존
  `AIInsightExtractor`를 사용하며 CLI 명령과 Python 차트 생성기 계약을 바꾸지 않는다.

### 25.2 인사이트 생성 정보와 캐시 호환성

- 생성 활성 서버는 시작 시 옵션과 내장 프롬프트 버전을 함께 고정한다. 설정 변경은
  재시작 이후 적용하며 실제 AI 호출은 생성 버튼 요청에서만 수행한다.
- 파일 envelope v2에 `generation_profile`을 추가한다. 공급자·요청 모델·내장 프롬프트 버전·
  추론 설정·서버 주소 지문을 비교해 다르면 `config_mismatch`로 제외한다. API 키는 저장·비교하지 않는다.
- v1 파일은 조회 전용으로 계속 표시할 수 있다. 생성 활성 모드에서는 출처 설정이 없는
  결과의 재사용을 막고 명시적인 재생성을 요구한다. 새 파일은 검증 후 같은 조건의 파일을 교체한다.
- HTTP snapshot v1에 `insight_provenance`, `generation.profile`을 선택적으로 추가한다.
  공개 정보는 provider/model/prompt_version/reasoning_effort이며 서버 지문·URL·키는 보내지 않는다.
- 모델 정보는 요청 설정이며 실제 응답 모델을 의미하지 않는다. 공통 `InsightResult` DTO,
  Repository, 기존 리포트 형식은 변경하지 않는다.
